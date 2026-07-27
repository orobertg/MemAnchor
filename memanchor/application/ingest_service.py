"""Active observation ingest.

Wraps dedup + adapter.on_tool_call so the HTTP router stays a thin mapper.
"""

from __future__ import annotations

from memanchor.contracts.adapter import MemAnchorAdapter, ToolCallWriteFailed
from memanchor.models import ErrorDetail, IngestRequest, IngestResponse
from memanchor.observer.ingest import IngestDedupCache, compute_dedup_key


async def ingest_tool_result(
    adapter: MemAnchorAdapter,
    dedup_cache: IngestDedupCache,
    req: IngestRequest,
) -> IngestResponse:
    key = compute_dedup_key(req.adapter, req.tool_name, req.inputs, req.outputs)

    hit, cached_id = dedup_cache.get(key)
    if hit:
        return IngestResponse(status="ok", id=cached_id, deduplicated=True)

    try:
        fact_id = await adapter.on_tool_call(
            req.tool_name, req.inputs, req.outputs, req.source_channel
        )
    except ToolCallWriteFailed as exc:
        # A real write failure, not the no-match no-op -- must not be
        # dedup-cached (nothing to dedup against) or reported as "ok".
        return IngestResponse(
            status="error", id=None, deduplicated=False, error=ErrorDetail(code=exc.code, message=exc.message)
        )
    dedup_cache.put(key, fact_id)
    return IngestResponse(status="ok", id=fact_id, deduplicated=False)
