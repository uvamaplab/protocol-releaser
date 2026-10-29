#!/usr/bin/env python3
import glob
import json
import os
import re
import sys

VARIABLE = re.compile(r"\$\{[^}]*\}")
PROTOCOL = "protocol.json"


def refs(node):
    if isinstance(node, dict):
        kind = next((value for key, value in node.items() if key.lower() == "type"), None)
        path = next((value for key, value in node.items() if key.lower() == "path"), None)
        if isinstance(kind, str) and kind.lower() == "ref" and isinstance(path, str):
            yield path
        for value in node.values():
            yield from refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from refs(value)


def normalize(path):
    return path.replace("\\", "/").strip("./").lower()


def resolve(src, path):
    path = normalize(path)
    if "://" in path:
        return None, None
    if VARIABLE.search(path):
        pattern = VARIABLE.sub("*", path)
        return sorted(f for f in glob.glob(os.path.join(src, pattern)) if os.path.isfile(f)), pattern
    full = os.path.join(src, path)
    return ([full] if os.path.isfile(full) else []), None


def bundle(src):
    files = {}
    warnings = []
    pending = [PROTOCOL]
    while pending:
        rel = pending.pop(0)
        if rel in files:
            continue
        full = os.path.join(src, rel)
        if not os.path.isfile(full):
            sys.exit(f"{src} has no {rel}")
        with open(full, encoding="utf-8-sig") as handle:
            text = handle.read()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            sys.exit(f"{rel} is not valid JSON: {error}")
        files[rel] = text
        for path in refs(parsed):
            found, pattern = resolve(src, path)
            if found is None:
                continue
            if not found:
                if pattern:
                    warnings.append(f'{rel}: no file matches "{path}"')
                else:
                    sys.exit(f'{rel} references "{path}", which is not in {src}')
            pending.extend(os.path.relpath(f, src).replace(os.sep, "/") for f in found)
    return dict(sorted(files.items())), warnings


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: bundle.py <src directory> <output file>")
    src, out = os.path.normpath(sys.argv[1]), sys.argv[2]
    files, warnings = bundle(src)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(files, handle, ensure_ascii=False, indent=2)
    for warning in warnings:
        print(f"::warning::{warning}")
    print(f"{out}: {len(files)} files, {os.path.getsize(out)} bytes")
    for rel in files:
        print(f"  {rel}")


if __name__ == "__main__":
    main()
