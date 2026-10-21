from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone


class UserError(Exception):
    """An actionable message that is safe to return without raw provider errors."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()
