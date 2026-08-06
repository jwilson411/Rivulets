"""query_workspace_db builtin tool's SELECT-only guard (tools/builtin/db_query.py).

No prior test file exercised this tool at all. get_settings().db_path
points at a real file under the per-session tempdir set up in
conftest.py (RIVULETS_WORKSPACE_DIR) -- this writes/reads that file
directly with sqlite3 rather than going through the app's async
SQLAlchemy engine (which the `client`/`db_session` fixtures point at an
unrelated in-memory database), since that's exactly the file this tool
itself opens read-only.
"""

import sqlite3
from collections.abc import Callable, Iterator
from typing import cast

import pytest

from rivulets.config import get_settings
from rivulets.tools.builtin.db_query import query_workspace_db

assert query_workspace_db.entrypoint is not None
_call = cast("Callable[..., str]", query_workspace_db.entrypoint)


@pytest.fixture
def seeded_db() -> Iterator[None]:
    db_path = get_settings().db_path
    db_path.unlink(missing_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
        conn.commit()
    finally:
        conn.close()
    yield
    db_path.unlink(missing_ok=True)


def test_rejects_non_select_statement(seeded_db: None) -> None:
    with pytest.raises(ValueError, match="Only SELECT statements are permitted"):
        _call(sql="DROP TABLE items")


def test_rejects_insert_statement(seeded_db: None) -> None:
    with pytest.raises(ValueError, match="Only SELECT statements are permitted"):
        _call(sql="INSERT INTO items (name) VALUES ('sneaky')")


def test_accepts_leading_whitespace_and_mixed_case(seeded_db: None) -> None:
    db_path = get_settings().db_path
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("INSERT INTO items (id, name) VALUES (1, 'alpha')")
        conn.commit()
    finally:
        conn.close()

    result = _call(sql="  SeLeCt name FROM items")
    assert result == "name\nalpha"


def test_returns_header_and_rows_tab_separated(seeded_db: None) -> None:
    db_path = get_settings().db_path
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("INSERT INTO items (id, name) VALUES (1, 'alpha')")
        conn.execute("INSERT INTO items (id, name) VALUES (2, 'beta')")
        conn.commit()
    finally:
        conn.close()

    result = _call(sql="SELECT id, name FROM items ORDER BY id")
    assert result == "id\tname\n1\talpha\n2\tbeta"


def test_empty_result_returns_only_header(seeded_db: None) -> None:
    result = _call(sql="SELECT id, name FROM items WHERE id = 999")
    assert result == "id\tname"


def test_row_count_is_capped_at_200(seeded_db: None) -> None:
    db_path = get_settings().db_path
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            "INSERT INTO items (id, name) VALUES (?, ?)",
            [(i, f"item{i}") for i in range(1, 251)],
        )
        conn.commit()
    finally:
        conn.close()

    result = _call(sql="SELECT id FROM items ORDER BY id")
    lines = result.split("\n")
    assert len(lines) == 201  # 1 header + 200 rows, not 250
    assert lines[1] == "1"
    assert lines[-1] == "200"


def test_query_against_read_only_connection_cannot_write(seeded_db: None) -> None:
    """The connection is opened `mode=ro` -- even if a mutating statement
    somehow got past the SELECT-prefix guard, sqlite3 itself refuses to
    write to a read-only-opened database."""
    db_path = get_settings().db_path
    conn = sqlite3.connect(db_path)
    try:
        count_before = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    finally:
        conn.close()

    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as ro_conn:
        with pytest.raises(sqlite3.OperationalError):
            ro_conn.execute("INSERT INTO items (id, name) VALUES (999, 'x')")

    conn = sqlite3.connect(db_path)
    try:
        count_after = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    finally:
        conn.close()
    assert count_after == count_before
