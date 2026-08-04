"""Declarative base and ID generation shared by all ORM models.

UUIDv7 primary keys are time-sortable and globally unique across nodes,
which matters once P2P sync is in play (see docs/architecture/data-model.md).
"""

import os
import time
import uuid

from sqlalchemy.orm import DeclarativeBase


def uuid7() -> str:
    """Generate a UUIDv7 string (RFC 9562): 48-bit ms timestamp + random bits."""
    unix_ts_ms = int(time.time() * 1000)
    ts_bytes = unix_ts_ms.to_bytes(6, byteorder="big")
    rand_bytes = os.urandom(10)
    raw = bytearray(ts_bytes + rand_bytes)
    raw[6] = (raw[6] & 0x0F) | 0x70  # version 7
    raw[8] = (raw[8] & 0x3F) | 0x80  # variant 10
    return str(uuid.UUID(bytes=bytes(raw)))


def utcnow_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Base(DeclarativeBase):
    pass
