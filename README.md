# protocol-releaser
A GitHub action to prepare and release a protocol for Digital Trails.

## Releasing

Protocols run this action as `uses: uvamaplab/protocol-releaser@latest`. The `latest` tag follows
the release that the Releases page marks as latest: publishing a release moves it forward, and
marking an older release as latest moves it back, both by the "Point latest at the latest release"
workflow. Commits to `main` move nothing until a release is published from them: tag the commit
(`v1.9.1` and so on) and publish a GitHub release for the tag.

GitHub runs a release event's workflow as of that release's commit, so the tag follows releases
from the first one that carries the workflow; to point `latest` at an older release, run the
workflow from the Actions tab.

## bundledprotocol.json

Every release also carries `bundledprotocol.json`: one JSON object mapping a path to that file's
text, holding `protocol.json` and every file its `{"type": "ref", "path": ...}` objects name,
followed recursively (`bundle.py`). Paths with a scheme (`flow://...`) are not files and are
skipped; a path with a variable (`protocols/${global:group}/surveys.json`) includes every file
the pattern can name, so the bundle covers every arm. The trails cloud (azure-iac's
`ProtocolReleasesSync`) reads this one small file per release, instead of the repo or the zip, for
what the server needs from a protocol, such as its `agent` section. A `protocol.json` that is not
valid JSON, or a reference to a file the protocol does not have, fails the release here, where the
publisher sees it; a variable pattern matching no file is a warning.

## Upload order

`protocol.zip` is uploaded last, in its own step, after `manifest.json`, `bundledprotocol.json`,
`media.zip` and `text.zip`. Keep it that way. The trails cloud (azure-iac's `ProtocolReleasesSync`) offers a
release to the app as soon as it sees `protocol.zip`, and takes the app version the release needs
from the `manifest.json` already attached. A `manifest.json` that arrived after the zip would leave
the release offered to apps too old to run it until the next check.
