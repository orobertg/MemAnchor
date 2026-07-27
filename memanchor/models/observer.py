from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from memanchor.models.common import ErrorDetail


class IngestRequest(BaseModel):
    adapter: str
    tool_name: str
    inputs: dict
    outputs: dict
    source_channel: str


class IngestResponse(BaseModel):
    status: Literal["ok", "error"]
    id: str | None
    deduplicated: bool
    error: ErrorDetail | None = None
