"""rivulets/validation.py -- the shared path-segment validators #239 pulled
out so sync/apply.py's peer-payload path reuses exactly what the HTTP
create paths (api/tools.py, api/files.py) already enforced."""

import pytest

from rivulets.config import get_settings
from rivulets.validation import CONTENT_HASH_RE, TOOL_NAME_RE, local_path_for_content_hash


def test_tool_name_re_accepts_valid_identifiers() -> None:
    assert TOOL_NAME_RE.match("add_numbers")
    assert TOOL_NAME_RE.match("_private")
    assert TOOL_NAME_RE.match("Tool2")


@pytest.mark.parametrize(
    "name",
    [
        "../../etc/cron.d/evil",
        "/etc/passwd",
        "has space",
        "has/slash",
        "2starts_with_digit",
        "",
    ],
)
def test_tool_name_re_rejects_path_like_or_invalid_names(name: str) -> None:
    assert TOOL_NAME_RE.match(name) is None


def test_content_hash_re_accepts_valid_sha256_hex() -> None:
    assert CONTENT_HASH_RE.match("a" * 64)
    assert CONTENT_HASH_RE.match("0123456789abcdef" * 4)


@pytest.mark.parametrize(
    "content_hash",
    [
        "../../etc/passwd",
        "/etc/passwd",
        "a" * 63,  # too short
        "a" * 65,  # too long
        "A" * 64,  # uppercase hex not accepted -- hashlib.hexdigest() is always lowercase
        "g" * 64,  # not a hex character
    ],
)
def test_content_hash_re_rejects_invalid_hashes(content_hash: str) -> None:
    assert CONTENT_HASH_RE.match(content_hash) is None


def test_local_path_for_content_hash_derives_sharded_path() -> None:
    content_hash = "a" * 64
    path = local_path_for_content_hash(content_hash)
    assert path == get_settings().files_dir / content_hash[:2] / content_hash


@pytest.mark.parametrize(
    "content_hash",
    [
        "../../etc/passwd",
        "/etc/passwd",
        "not-hex",
        "a" * 63,
    ],
)
def test_local_path_for_content_hash_rejects_invalid_hashes(content_hash: str) -> None:
    with pytest.raises(ValueError, match="Invalid content_hash"):
        local_path_for_content_hash(content_hash)
