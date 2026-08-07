"""Health & info endpoints (api-design.md, compute-and-storage.md)."""

import time
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from rivulets.config import get_settings
from rivulets.version import APP_VERSION

router = APIRouter(tags=["health"])

_started_at = time.monotonic()


class ResourceUsage(BaseModel):
    db_size_mb: float
    file_store_size_mb: float
    log_size_mb: float
    uptime_seconds: float


class HealthResponse(BaseModel):
    status: str
    resources: ResourceUsage


def _dir_size_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return round(total / (1024 * 1024), 2)


def _file_size_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    return round(path.stat().st_size / (1024 * 1024), 2)


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        resources=ResourceUsage(
            db_size_mb=_file_size_mb(settings.db_path),
            file_store_size_mb=_dir_size_mb(settings.files_dir),
            log_size_mb=_dir_size_mb(settings.logs_dir),
            uptime_seconds=round(time.monotonic() - _started_at, 1),
        ),
    )


class InfoResponse(BaseModel):
    version: str


@router.get("/info", response_model=InfoResponse)
async def info() -> InfoResponse:
    return InfoResponse(version=APP_VERSION)
