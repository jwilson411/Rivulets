"""Workspace-wide configuration.

Defaults mirror docs/infrastructure/deployment-and-networking.md and
docs/infrastructure/compute-and-storage.md. Values are overridable via
AGENT_HIVE_* environment variables (see pydantic-settings env_prefix below).
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENT_HIVE_")

    workspace_dir: Path = Path.home() / ".agent-hive"

    app_server_host: str = "127.0.0.1"
    app_server_port: int = 8484

    agentos_host: str = "127.0.0.1"
    agentos_port: int = 7777

    @property
    def db_path(self) -> Path:
        return self.workspace_dir / "agent-hive.db"

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
