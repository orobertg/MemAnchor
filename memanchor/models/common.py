from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Provenance = Literal["system-derived", "user-stated", "inferred"]
Cardinality = Literal["ONE", "MANY"]


class ErrorDetail(BaseModel):
    code: str
    message: str
    conflicting_ids: list[str] | None = None
    warnings: list[str] | None = None
