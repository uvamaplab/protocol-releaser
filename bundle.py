#!/usr/bin/env python3
import glob
import itertools
import json
import os
import re
import sys

VARIABLE = re.compile(r"\$\{([^}]*)\}")
PROTOCOL = "protocol.json"
MISSING = ""

# The variable language, shared with the app (digital-trails CLAUDE.md, "Protocol variables")
# and the server (azure-iac ParticipantProtocols): ${namespace:name,default}. These are the
# namespaces a placeholder may read from; anything else never resolves, so it fails the release.
NAMESPACES = {"global", "role", "historical"}


def is_ref(node):
    if not isinstance(node, dict):
        return False
    kind = next((value for key, value in node.items() if key.lower() == "type"), None)
    path = next((value for key, value in node.items() if key.lower() == "path"), None)
    return isinstance(kind, str) and kind.lower() == "ref" and isinstance(path, str)


def ref_path(node):
    return next(value for key, value in node.items() if key.lower() == "path")


def refs(node):
    if isinstance(node, dict):
        if is_ref(node):
            yield ref_path(node)
        for value in node.values():
            yield from refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from refs(value)


def normalize(path):
    return path.replace("\\", "/").strip("./").lower()


def parse_token(token):
    name, _, default = token.partition(",")
    return name.strip(), (default.strip() if _ else None)


def namespace_of(name):
    """The namespace of a variable name: "global:arm" gives "global", a bare "role" gives "role"."""
    return name.partition(":")[0].strip().lower()


def declared_variables(protocol):
    """
    protocol.json's optional "variables" object, {"arm": {"default": ..., "values": [...]}},
    keyed as the placeholders name them ("global:arm"). Declaring is optional: an undeclared
    variable's values still come from the files its ref paths match.
    """
    declared = {}
    section = protocol.get("variables") if isinstance(protocol, dict) else None
    if not isinstance(section, dict):
        return declared
    for name, spec in section.items():
        if not isinstance(spec, dict):
            continue
        values = spec.get("values")
        default = spec.get("default")
        declared[f"global:{name}"] = {
            "values": [str(value) for value in values] if isinstance(values, list) else [],
            "default": None if default is None or isinstance(default, (dict, list)) else str(default),
        }
    return declared


def strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from strings(value)


def lint_placeholders(rel, parsed, declared):
    """
    Every ${...} in the file, checked against the language: an unknown namespace (including the
    retired variant:) or a nested placeholder is an error, since the app would leave it as
    written; a global the protocol declares nothing about is a warning once it declares any.
    """
    errors, warnings = [], []
    for text in strings(parsed):
        if "${" in text and not VARIABLE.search(text):
            warnings.append(f'{rel}: "{text}" opens a placeholder it never closes')
        for token in VARIABLE.findall(text):
            if "${" in token:
                errors.append(f'{rel}: "${{{token}}}" nests one placeholder in another, which the language has no meaning for')
                continue
            name, _ = parse_token(token)
            namespace = namespace_of(name)
            if namespace not in NAMESPACES:
                errors.append(f'{rel}: "${{{token}}}" reads from "{namespace}", which is not a variable namespace ({", ".join(sorted(NAMESPACES))})')
            elif namespace == "global" and name.partition(":")[2].strip() == "":
                errors.append(f'{rel}: "${{{token}}}" names no variable')
            elif namespace == "global" and declared and name.lower() not in {key.lower() for key in declared}:
                warnings.append(f'{rel}: "${{{token}}}" is not declared in {PROTOCOL}\'s "variables"')
    return errors, warnings


def is_file_path(path):
    return "://" not in path


def resolve_path(path, values):
    def replace(match):
        name, default = parse_token(match.group(1))
        value = values.get(name, MISSING)
        if value == MISSING and default is not None:
            value = default
        if value == MISSING:
            raise KeyError(name)
        return value

    return normalize(VARIABLE.sub(replace, path))


def matches(src, path):
    path = normalize(path)
    tokens = [parse_token(token) for token in VARIABLE.findall(path)]
    pattern = VARIABLE.sub("*", path)
    regex = re.compile("^" + re.escape(pattern).replace(r"\*", "([^/]+)") + "$")
    found = []
    for full in sorted(glob.glob(os.path.join(src, pattern))):
        if not os.path.isfile(full):
            continue
        rel = os.path.relpath(full, src).replace(os.sep, "/")
        matched = regex.match(rel)
        found.append((rel, dict(zip((name for name, _ in tokens), matched.groups())) if matched else {}))
    return found, tokens, pattern != path


def read(src, rel):
    full = os.path.join(src, rel)
    if not os.path.isfile(full):
        sys.exit(f"{src} has no {rel}")
    with open(full, encoding="utf-8-sig") as handle:
        text = handle.read()
    try:
        return text, json.loads(text)
    except json.JSONDecodeError as error:
        sys.exit(f"{rel} is not valid JSON: {error}")


def collect(src):
    files = {}
    values = {}
    warnings = []
    errors = []
    declared = None
    pending = [PROTOCOL]
    while pending:
        rel = pending.pop(0)
        if rel in files:
            continue
        text, parsed = read(src, rel)
        files[rel] = text
        if declared is None:
            declared = declared_variables(parsed)
            for name, spec in declared.items():
                values.setdefault(name, set()).update(spec["values"])
                if spec["default"] is not None:
                    values[name].add(spec["default"])
        for path in refs(parsed):
            if not is_file_path(path):
                continue
            found, tokens, patterned = matches(src, path)
            for name, default in tokens:
                values.setdefault(name, set())
                if default is not None:
                    values[name].add(default)
            if not found:
                if patterned:
                    warnings.append(f'{rel}: no file matches "{path}"')
                else:
                    sys.exit(f'{rel} references "{path}", which is not in {src}')
            for file, matched in found:
                pending.append(file)
                for name, value in matched.items():
                    values[name].add(value)
    for rel in sorted(every_json(src)):
        if rel in files:
            parsed = json.loads(files[rel])
        else:
            # A flow or home file the refs never reach is linted but not required to parse here:
            # the app reads it on its own terms, and its JSON was never this script's to refuse.
            with open(os.path.join(src, rel), encoding="utf-8-sig") as handle:
                try:
                    parsed = json.load(handle)
                except json.JSONDecodeError as error:
                    warnings.append(f"{rel} is not valid JSON: {error}")
                    continue
        file_errors, file_warnings = lint_placeholders(rel, parsed, declared or {})
        errors.extend(file_errors)
        warnings.extend(file_warnings)
    if errors:
        sys.exit("\n".join(errors))
    return files, values, warnings


def every_json(src):
    """Every .json under src, as paths relative to it: flows and home files too, not only what the refs reach."""
    for root, _, names in os.walk(src):
        for name in names:
            if name.lower().endswith(".json"):
                yield os.path.relpath(os.path.join(root, name), src).replace(os.sep, "/")


def dereference(node, files, values):
    if isinstance(node, dict):
        if is_ref(node):
            path = resolve_path(ref_path(node), values)
            if path not in files:
                raise FileNotFoundError(path)
            node = json.loads(files[path])
            return dereference(node, files, values)
        return {key: dereference(value, files, values) for key, value in node.items()}
    if isinstance(node, list):
        return [dereference(value, files, values) for value in node]
    return node


def resolve_all(files, values):
    names = sorted(values)
    resolved = {}
    choices = [sorted(values[name]) + [MISSING] for name in names]
    for combination in itertools.product(*choices):
        chosen = dict(zip(names, combination))
        try:
            protocol = dereference(json.loads(files[PROTOCOL]), files, chosen)
        except (KeyError, FileNotFoundError):
            continue
        # Keys are lower case, as the server builds them from a participant's values: the files
        # that give a variable its values are lower-cased by the action, roles are not.
        resolved["|".join(value.lower() for value in combination)] = json.dumps(protocol, ensure_ascii=False)
    return names, resolved


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: bundle.py <src directory> <output file>")
    src, out = os.path.normpath(sys.argv[1]), sys.argv[2]
    files, values, warnings = collect(src)
    names, resolved = resolve_all(files, values)
    if not resolved:
        sys.exit(f"{PROTOCOL} cannot be resolved for any combination of {names}")
    bundle = {"files": dict(sorted(files.items())), "variables": names, "resolved": resolved}
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(bundle, handle, ensure_ascii=False, indent=2)
    for warning in warnings:
        print(f"::warning::{warning}")
    print(f"{out}: {len(files)} files, {len(resolved)} resolved over {names}, {os.path.getsize(out)} bytes")
    for rel in files:
        print(f"  {rel}")
    for key in resolved:
        print(f"  resolved[{key!r}]")


if __name__ == "__main__":
    main()
