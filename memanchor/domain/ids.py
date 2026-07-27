"""Identity and clock helpers used across write/query paths.

Kept in the domain layer so use-cases never import infrastructure packages
just to mint a UUID or timestamp.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


def new_id() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
