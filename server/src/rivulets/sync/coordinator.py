"""Capability scoring for workspace-singleton coordinator election (#101).

Deliberately stdlib-only, no `psutil`. This app is packaged with PyInstaller
across three platforms (packaging/{macos,windows,linux}.spec) and has no
existing binary-dependency-for-system-metrics precedent anywhere in the
codebase -- adding one just for an election heuristic isn't worth the
per-platform hidden-import/wheel risk when a rougher stdlib approximation is
enough to rank peers. Total-RAM detection in particular has no single stdlib
call that works on Linux/macOS/Windows alike, hence the platform branches in
`_total_memory_gb` below, each wrapped so a detection failure just zeroes
that one term instead of breaking the whole score.

This score is self-reported and unverified, same trust model as
`node_capabilities` (engine.py) -- every peer in a workspace already shares
the same trust level (see docs/security.md), so a peer lying about its own
specs to win coordinator status is an accepted risk, not a new one.
"""

import ctypes
import os
import platform
import shutil
from pathlib import Path

_CPU_WEIGHT = 10.0
_RAM_WEIGHT = 3.0
_DISK_WEIGHT = 1.0
_DISK_CAP_GB = 500.0  # more free disk than this stops mattering for ranking purposes
_LOAD_WEIGHT = 25.0
_UPTIME_WEIGHT = 15.0
_UPTIME_SATURATION_SECONDS = 600.0  # 10 continuous minutes maxes out the uptime term


def _total_memory_gb() -> float:
    system = platform.system()
    try:
        if system == "Linux":
            with open("/proc/meminfo", encoding="ascii") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) / (1024**2)
        elif system == "Darwin":
            libc = ctypes.CDLL("libc.dylib")
            size = ctypes.c_uint64(0)
            size_len = ctypes.c_size_t(ctypes.sizeof(size))
            rc = libc.sysctlbyname(
                b"hw.memsize", ctypes.byref(size), ctypes.byref(size_len), None, 0
            )
            if rc == 0:
                return size.value / (1024**3)
        elif system == "Windows":

            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = (
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_uint64),
                    ("ullAvailPhys", ctypes.c_uint64),
                    ("ullTotalPageFile", ctypes.c_uint64),
                    ("ullAvailPageFile", ctypes.c_uint64),
                    ("ullTotalVirtual", ctypes.c_uint64),
                    ("ullAvailVirtual", ctypes.c_uint64),
                    ("ullAvailExtendedVirtual", ctypes.c_uint64),
                )

            stat = _MemoryStatusEx()
            stat.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
                return stat.ullTotalPhys / (1024**3)
    except Exception:  # noqa: BLE001, S110 - best-effort; an undetectable RAM size just zeroes this term
        return 0.0
    return 0.0


def _load_factor() -> float:
    """1.0 = idle, 0.0 = saturated across every core. Windows has no
    getloadavg() -- treated as always-idle rather than penalizing every
    Windows peer for a signal it can't produce."""
    cpu = os.cpu_count() or 1
    try:
        load1, _, _ = os.getloadavg()
    except (OSError, AttributeError):
        return 1.0
    return max(0.0, 1.0 - min(load1 / cpu, 1.0))


def compute_capability_score(base_path: Path, uptime_seconds: float) -> float:
    """Higher is better. `base_path` is used only for a free-disk-space
    check (sync_dir is a reasonable stand-in for "this node's storage").
    `uptime_seconds` is this SyncEngine session's own continuous runtime,
    not a persisted historical reliability record -- a deliberately
    narrower v1 of the "uptime/reliability history" the issue describes
    (see engine.py's _started_monotonic): enough to stop a peer that just
    joined from instantly outranking an already-stable coordinator on raw
    spec alone, without building a persisted-across-restarts tracker."""
    cpu = os.cpu_count() or 1
    try:
        disk_free_gb = shutil.disk_usage(base_path).free / (1024**3)
    except OSError:
        disk_free_gb = 0.0
    uptime_factor = min(max(uptime_seconds, 0.0) / _UPTIME_SATURATION_SECONDS, 1.0)
    return (
        cpu * _CPU_WEIGHT
        + _total_memory_gb() * _RAM_WEIGHT
        + min(disk_free_gb, _DISK_CAP_GB) * _DISK_WEIGHT
        + _load_factor() * _LOAD_WEIGHT
        + uptime_factor * _UPTIME_WEIGHT
    )


def outranks(
    candidate_id: str, candidate_score: float, current_id: str, current_score: float
) -> bool:
    """Deterministic total order over (score, peer_id): higher score wins;
    equal scores break the tie by peer_id string comparison. Every peer
    resolving the same comparison from the same gossiped inputs the same
    way is what lets a bully-style election converge without a
    coordinating third party or a request/response round trip."""
    if candidate_score != current_score:
        return candidate_score > current_score
    return candidate_id > current_id
