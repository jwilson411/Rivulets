"""Per-installation node identity (sync/identity.py). The happy path (no
existing key -> generate + persist; existing valid key -> reload) is
exercised for real by test_sync.py's SyncEngine tests, which call
load_or_create_node_key indirectly via engine.start(). This file covers
the corrupt-key guard, which those never hit."""

from pathlib import Path

import pytest

from rivulets.sync.identity import load_or_create_node_key


def test_load_or_create_node_key_creates_and_reloads_a_stable_identity(tmp_path: Path) -> None:
    first = load_or_create_node_key(tmp_path)
    second = load_or_create_node_key(tmp_path)
    assert first.public_key.serialize() == second.public_key.serialize()


def test_load_or_create_node_key_rejects_corrupt_seed(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "node_key").write_bytes(b"too-short")

    with pytest.raises(RuntimeError, match="Corrupt node key"):
        load_or_create_node_key(tmp_path)
