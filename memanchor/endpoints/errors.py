"""HTTP status mapping for application-layer error codes.

Keeps status-code policy in one place so routers share the same mapping.
"""

from __future__ import annotations

from fastapi.responses import JSONResponse
from pydantic import BaseModel

ERROR_HTTP_STATUS = {
    "PROVENANCE_REJECTED": 400,
    "WRITE_ROLLBACK": 500,
    "QUERY_TIMEOUT": 504,
    "FACT_NOT_FOUND": 404,
    "INVALID_RESOLVE_STATE": 400,
}


def error_response(body: BaseModel, code: str | None) -> JSONResponse:
    http_status = ERROR_HTTP_STATUS.get(code or "", 500)
    return JSONResponse(status_code=http_status, content=body.model_dump())
