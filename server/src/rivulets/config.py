"""Workspace-wide configuration.

Values are overridable via RIVULETS_* environment variables (see
pydantic-settings env_prefix below).
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RIVULETS_")

    workspace_dir: Path = Path.home() / ".rivulets"

    app_server_host: str = "127.0.0.1"
    app_server_port: int = 8484

    # #247/#318: when the app is reachable before the owner's first login,
    # whoever sends the first valid-looking POST /auth/login claims the
    # single workspace row, permanently locking out the real mnemonic.
    # `app_server_host == "0.0.0.0"` is NOT a reliable signal for that --
    # it's the Docker image's *hard-coded* internal bind (Dockerfile),
    # true whether the host only published it to loopback (safe, no race
    # possible -- same as a native install) or to the LAN (unsafe). So
    # this gate is keyed on `require_bootstrap_token` instead, an
    # operator-set signal that's independent of the in-container bind --
    # the default compose profile (loopback-only) leaves it unset; only a
    # profile that deliberately publishes beyond loopback sets it, along
    # with this token. Only gates *creating* the workspace row
    # (api/auth.py); every login afterward is unaffected.
    require_bootstrap_token: bool = False
    bootstrap_token: str | None = None

    # NFR-3.5 / ADR-008: the Code Execution tool denies outbound network
    # access from sandboxed code by default. No per-workspace UI toggle
    # exists yet (see tools/builtin/code_exec.py) — this is the only knob.
    code_exec_network_access: bool = False

    # Optional Brave Search key for the web_search builtin. When unset,
    # web_search falls back to DuckDuckGo (no key). Never a tool argument.
    brave_api_key: str | None = None

    @property
    def db_path(self) -> Path:
        return self.workspace_dir / "rivulets.db"

    @property
    def credential_fallback_db_path(self) -> Path:
        """A separate file from `db_path` (#118): provider keys must never
        enter the synced app database (see security/credentials.py's
        module docstring), so the encrypted-SQLite fallback gets its own
        local-only file instead of a table in rivulets.db."""
        return self.workspace_dir / "credentials.db"

    @property
    def agentos_db_path(self) -> Path:
        """AgentOS's own SQLite file (agentos/service.py's module docstring)
        — session/run history, kept separate from `db_path` since AgentOS
        owns its own schema and migrations internally."""
        return self.workspace_dir / "agentos.db"

    @property
    def files_dir(self) -> Path:
        return self.workspace_dir / "files"

    @property
    def tools_dir(self) -> Path:
        return self.workspace_dir / "tools"

    @property
    def logs_dir(self) -> Path:
        return self.workspace_dir / "logs"

    @property
    def backups_dir(self) -> Path:
        return self.workspace_dir / "backups"

    @property
    def sync_dir(self) -> Path:
        """Holds this installation's local libp2p node identity (sync/node_key)
        — never synced, distinct from the shared workspace key (FR-9.2's
        credential-exclusion principle extended to per-node identity)."""
        return self.workspace_dir / "sync"

    def ensure_workspace_dirs(self) -> None:
        for d in (
            self.workspace_dir,
            self.files_dir,
            self.tools_dir,
            self.logs_dir,
            self.backups_dir,
            self.sync_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)
            # main.py's process-wide umask only governs *new* files/dirs --
            # this also tightens dirs left over from before #244, on every
            # startup, so an upgrade fixes an existing install in place
            # rather than just new ones.
            d.chmod(0o700)
        for f in (self.db_path, self.credential_fallback_db_path):
            if f.exists():
                f.chmod(0o600)


@lru_cache
def get_settings() -> Settings:
    return Settings()
