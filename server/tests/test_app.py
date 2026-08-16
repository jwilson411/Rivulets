"""app.py -- the FastAPI app factory. Every other test in this suite goes
through the `client` fixture, which always builds an API-only app (no
built UI on disk in this checkout, see _static_dir's own docstring on when
it returns None) -- so the CSP's script-src-hashing branch and the whole
SPA-fallback/static-file-serving path (_mount_ui) are otherwise untested.
"""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.app import (
    _build_csp,  # pyright: ignore[reportPrivateUsage]
    _on_peer_connected,  # pyright: ignore[reportPrivateUsage]
    create_app,
)
from rivulets.db.session import get_engine, make_engine, override_engine
from rivulets.sync.engine import (
    SyncEngine,
    init_sync_engine,
    reset_sync_engine_for_testing,
)


def test_build_csp_with_no_static_dir_is_api_only() -> None:
    csp = _build_csp(None)
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp


def test_build_csp_hashes_the_ui_build_s_inline_script(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        "<html><body><script>window.__id = 'abc123';</script></body></html>"
    )

    csp = _build_csp(tmp_path)

    assert "script-src 'self' 'sha256-" in csp
    assert "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com" in csp
    assert "font-src https://fonts.gstatic.com" in csp


def test_build_csp_with_no_inline_script_in_index_html(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<html><body>no script here</body></html>")

    csp = _build_csp(tmp_path)

    assert "script-src 'self';" in csp or csp.endswith("script-src 'self'")


async def _noop(*_args: object, **_kwargs: object) -> None:
    return None


def test_mounted_ui_serves_static_assets_and_falls_back_to_index_for_spa_routes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html><script>boot();</script></html>")
    assets_dir = static_dir / "_app"
    assets_dir.mkdir()
    (assets_dir / "bundle.js").write_text("console.log('hi');")

    monkeypatch.setattr("rivulets.app._static_dir", lambda: static_dir)
    monkeypatch.setattr("rivulets.app.run_startup_backup_checks", _noop)
    monkeypatch.setattr(SyncEngine, "start", _noop)
    monkeypatch.setattr(SyncEngine, "stop", _noop)

    reset_sync_engine_for_testing()
    override_engine(make_engine(in_memory=True))
    app = create_app()
    try:
        with TestClient(app) as test_client:
            # A real static asset on disk is served directly.
            asset_response = test_client.get("/_app/bundle.js")
            assert asset_response.status_code == 200
            assert "console.log" in asset_response.text

            # An unmatched /api/... path stays a JSON-shaped 404, not the SPA
            # fallback -- api_router's own routes never claimed it either.
            api_404 = test_client.get("/api/this-route-does-not-exist")
            assert api_404.status_code == 404
            assert api_404.headers["content-type"].startswith("application/json")

            # Any other unmatched path (client-side router state, e.g. a hard
            # refresh on /channels/xyz) falls back to index.html.
            spa_response = test_client.get("/channels/some-id")
            assert spa_response.status_code == 200
            assert "<script>boot();</script>" in spa_response.text
    finally:
        asyncio.run(get_engine().dispose())
        override_engine(None)
        reset_sync_engine_for_testing()


async def test_on_peer_connected_is_a_noop_with_nothing_pending(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Covers all three halves of _on_peer_connected: drain_pending_outbound,
    (#347) push_snapshot_to_peer, and (issue #10) publish_capabilities --
    none may raise just because the engine isn't actually running
    (publish_capabilities and the snapshot push no-op cleanly, same as
    publish_state_change already does)."""
    del db_session  # only needed to get an overridden in-memory engine set up
    reset_sync_engine_for_testing()
    init_sync_engine(tmp_path / "sync")
    try:
        await _on_peer_connected("some-peer")  # must not raise; engine isn't running
    finally:
        reset_sync_engine_for_testing()
