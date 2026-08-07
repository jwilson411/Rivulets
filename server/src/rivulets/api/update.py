"""Auto-update endpoints (issue #11). GET checks GitHub Releases for a
newer tagged version than the running binary; POST is the single approval
action that downloads, verifies, swaps the binary in, and restarts.

No automatic/background check runs from here -- unlike backups.py's daily
snapshot, there's no equivalent existing "runs on every startup" loop to
hang this off (see main.py's own docstring: the real process supervisor is
still #12's unimplemented future work). The UI checks on demand instead
(Sidebar.svelte, alongside its other startup counts).
"""

import os
import time

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel

from rivulets.api.deps import CurrentWorkspaceId
from rivulets.update import (
    UpdateNotApplicableError,
    UpdateNotAvailableError,
    UpdateStatus,
    UpdateVerificationError,
    apply_update,
    check_for_update,
)

router = APIRouter(prefix="/update", tags=["update"])


class UpdateStatusOut(BaseModel):
    current_version: str
    latest_version: str | None
    update_available: bool
    applicable: bool

    model_config = {"from_attributes": True}

    @classmethod
    def from_status(cls, info: UpdateStatus) -> "UpdateStatusOut":
        return cls(
            current_version=info.current_version,
            latest_version=info.latest_version,
            update_available=info.update_available,
            applicable=info.applicable,
        )


class UpdateApplyOut(BaseModel):
    status: str


@router.get("/status", response_model=UpdateStatusOut)
async def get_update_status(_: CurrentWorkspaceId) -> UpdateStatusOut:
    return UpdateStatusOut.from_status(await check_for_update())


def _exit_after_response() -> None:
    """Runs as a BackgroundTask, which Starlette only starts after the
    response has already been sent -- this sleep is just slack for the TCP
    write itself, not for the response to be scheduled. os._exit rather
    than a graceful uvicorn shutdown: main.py owns the running Server
    instance via uvicorn.run(), not reachable from this layer. The fresh
    process this hands off to (main.py's wait_for_port_free) already has to
    tolerate an abrupt predecessor exit -- no different from a user killing
    the process directly, which backup.py's DR posture already assumes for.
    """
    time.sleep(0.3)
    os._exit(0)


@router.post("/apply", response_model=UpdateApplyOut, status_code=status.HTTP_202_ACCEPTED)
async def apply_update_endpoint(
    background_tasks: BackgroundTasks, _: CurrentWorkspaceId
) -> UpdateApplyOut:
    try:
        await apply_update()
    except UpdateNotApplicableError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except UpdateNotAvailableError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except UpdateVerificationError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc
    background_tasks.add_task(_exit_after_response)
    return UpdateApplyOut(status="restarting")
