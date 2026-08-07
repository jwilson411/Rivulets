"""Per-installation capability tags (issue #10's "which peer is good at
what" primitive).

Same storage shape as identity.py's node key: a small file inside
`sync_dir`, per-installation, never synced/shared directly — what gets
shared is a live gossipsub *announcement* of this file's contents (see
SyncEngine.publish_capabilities in engine.py), not the file itself. Tags
are free-form strings a user assigns to their own machine (e.g. "gpu"),
not a fixed vocabulary.
"""

import json
from pathlib import Path

_CAPABILITIES_FILENAME = "capabilities.json"


def load_capabilities(sync_dir: Path) -> list[str]:
    path = sync_dir / _CAPABILITIES_FILENAME
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def save_capabilities(sync_dir: Path, tags: list[str]) -> None:
    sync_dir.mkdir(parents=True, exist_ok=True)
    path = sync_dir / _CAPABILITIES_FILENAME
    path.write_text(json.dumps(tags), encoding="utf-8")
