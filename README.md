

https://github.com/user-attachments/assets/6a5d406a-9603-4410-9858-194cccf4ed83

# MemAnchor

MemAnchor is a memory system for AI agents that solves a problem plain vector
databases can't: they find things by meaning, but they have no way to tell
that two remembered facts contradict each other, that a fact has gone stale,
or that only one value should ever be active for a given attribute. In
practice that means agents built on a vector store alone will confidently
serve an agent old, wrong, or conflicting facts with no warning.

MemAnchor addresses this by tagging every stored fact with **provenance**
(`system-derived`, `user-stated`, or `inferred`) — a record of *how the fact
entered the system* — and using that provenance to resolve contradictions
deterministically, without requiring an LLM call to reason about which fact
is more likely correct. A system-derived fact with a paired validator tool
gets re-verified against ground truth; a user-stated fact is trusted over an
automated observation; conflicting facts with no way to arbitrate are
surfaced as unresolved rather than silently guessed at.

It combines two coordinated storage layers — ChromaDB for semantic
retrieval, SQLite for structured filtering, cardinality, and provenance
metadata — behind a single FastAPI interface. It's designed to be
general-purpose and host-anywhere: no cloud dependency, runs on any machine
that can run Python, and isn't tied to any particular agent framework.

## Features

| Area | What's in |
|------|-----------|
| Storage | SQLite (`facts`, `entity_registry`, `validators`, `decay_metrics`, `contradiction_log`) + real ChromaDB with `sentence-transformers/all-MiniLM-L6-v2` embeddings |
| Write pipeline | Provenance guardrail, cardinality enforcement (overwrite / deferred conflict), transactional write with rollback, telemetry-instrumented |
| Query pipeline | Chroma nearest-neighbor → SQLite post-filter with decay-weighted confidence → validator revalidation → ground-truth cross-check → semantic contradiction check → provenance-priority resolution → decay bookkeeping, telemetry-instrumented throughout |
| Resolve pipeline | `cardinality_conflict_resolve` (promote/discard) and `reclassify_provenance`, telemetry-instrumented |
| Adapter interface | `MemAnchorAdapter` contract + `GenericAdapter` reference implementation |
| Observation layer | Passive MCP observer + active `POST /observer/ingest` with idempotency dedup |
| Telemetry | `GET /telemetry/stats`, `GET /telemetry/contradictions`, `GET /telemetry/provenance-audit` |
| CLI tools | `calibrate_threshold.py`, `backup_snapshot.sh` |
| API | `POST /memory/store`, `POST /memory/query`, `POST /memory/resolve`, `POST /registry/register`, `GET /registry`, `POST /observer/ingest`, `GET /telemetry/*`, `DELETE /memory/{id}`, `GET /health` |

Contradictions among facts with **no** registered (and callable-backed)
validator resolve via query-time provenance priority
(`system-derived > user-stated > inferred`); a tie returns
`CONFLICT_UNRESOLVABLE`. `/memory/resolve` is a separate, explicit
correction path that doesn't require a validator.

### Known limitations

MemAnchor binds to localhost by default and has no built-in authentication —
put it behind a reverse proxy if exposing it beyond a single trusted machine.
Multi-agent/multi-writer concurrency is bounded by SQLite's write
serialization. `contradiction_similarity_threshold`'s default (0.92) is
unvalidated against any particular embedding distribution — run
`calibrate_threshold.py` (below) against your own data before relying on
semantic contradiction detection in production.

## Setup

Requires Python 3.12 and [pipenv](https://pipenv.pypa.io/).

```bash
pipenv install --dev
```

This installs runtime deps (`fastapi`, `uvicorn`, `chromadb`,
`sentence-transformers`, `aiosqlite`, `pydantic`, `pyyaml`, `mcp`) and dev
deps (`pytest`, `pytest-asyncio`, `httpx`) per the `Pipfile`.

> **Disk space note:** `sentence-transformers` pulls in `torch`, which is a
> multi-GB install. If your default pip/pipenv cache or `/tmp` lives on a
> nearly-full disk, point them elsewhere first, e.g.:
> ```bash
> export PIPENV_VENV_IN_PROJECT=1
> export PIP_CACHE_DIR=/path/to/roomy/disk/.cache/pip
> export TMPDIR=/path/to/roomy/disk/.cache/tmp
> ```

Config lives in [`memanchor.yaml`](memanchor.yaml) at the repo root (bind
host/port, embedding model, contradiction threshold, timeouts, data paths).

The generic adapter's fact shape loads from `fact_shape_path` (default
`./fact_shape.json`); see [`fact_shape.example.json`](fact_shape.example.json)
for the schema. If the file doesn't exist, every attribute implicitly
defaults to `cardinality: "MANY"`.

## Running the server

```bash
pipenv run uvicorn memanchor.endpoints.app:app --port 8894
```

First startup downloads the MiniLM embedding model (cached afterward) and
runs the crash-recovery sweep for any stale pending writes. Verify with:

```bash
curl -s http://127.0.0.1:8894/health | python3 -m json.tool
```

## Running tests

```bash
pipenv run pytest tests/ -v
```

Each test builds an isolated app instance against a `tmp_path` SQLite file
and Chroma collection (see `tests/helpers.py`), so tests don't share state
or touch `./data`.

## API overview

All endpoints speak JSON. Every `/memory/query` response has `status` as
its first field and callers **must** check it before consuming `results`.

### `POST /memory/store`

```bash
curl -s -X POST http://127.0.0.1:8894/memory/store \
  -H 'Content-Type: application/json' \
  -d '{
    "entity": "gateway",
    "attribute": "port",
    "value": "8890",
    "content": "Gateway runs on port 8890",
    "provenance": "system-derived",
    "source_channel": "observer_ingest",
    "cardinality": "ONE"
  }'
```

```json
{"status": "ok", "id": "84efe888-5044-4b29-88cf-031c447d288f", "error": null}
```

Request fields:

| Field | Notes |
|-------|-------|
| `entity`, `attribute`, `value`, `content` | `value` is the raw fact value; `content` is the natural-language sentence that gets embedded |
| `provenance` | `system-derived` \| `user-stated` \| `inferred` |
| `source_channel` | free-text origin tag, e.g. `observer_ingest`, `api_store`, `llm_extraction` |
| `provenance_confidence` | optional; defaults per provenance (`1.0` system-derived, `0.95` user-stated, `0.70` inferred) |
| `cardinality` | `ONE` \| `MANY` (default `MANY`); only honored the first time an `entity+attribute` pair is registered — later writes reuse the stored value |
| `force_provenance` | overrides the guardrail that rejects `inferred` writes on `cardinality: ONE` attributes |

Cardinality behavior on a repeat write to the same `entity+attribute` with
`cardinality: ONE`:

- **Incoming provenance rank ≥ active fact's rank** (`user-stated(3) >
  system-derived(2) > inferred(1)`) → active fact is tombstoned, new
  fact becomes active (`status: "ok"`).
- **Incoming rank < active rank** → new fact is held `provenance_pending`
  with no vector written; active fact is untouched; response is
  `status: "pending"`, `error.code: "CARDINALITY_CONFLICT_PENDING"`.
  Resolve it via `POST /memory/resolve` (below).

Guardrail rejection (`inferred` + `cardinality: ONE`, no `force_provenance`)
returns HTTP 400, `status: "error"`, `error.code: "PROVENANCE_REJECTED"`.

### `POST /memory/query`

```bash
curl -s -X POST http://127.0.0.1:8894/memory/query \
  -H 'Content-Type: application/json' \
  -d '{"query": "what port does the gateway run on", "top_k": 5}'
```

```json
{
  "status": "ok",
  "results": [
    {
      "id": "84efe888-5044-4b29-88cf-031c447d288f",
      "entity": "gateway",
      "attribute": "port",
      "value": "8890",
      "content": "Gateway runs on port 8890",
      "confidence": 1.0,
      "provenance": "system-derived",
      "source_channel": "observer_ingest",
      "created_at": "2026-07-06T01:14:58.676320Z",
      "last_validated": null
    }
  ],
  "contradictions_resolved": 0,
  "tombstones_filtered": 0,
  "error": null
}
```

Request fields: `query` (str), `top_k` (default 5), `min_confidence`
(default 0.0).

`confidence` in each result is the **decayed** value:
`base_confidence × exp(-miss_count / decay_halflife)`, where `miss_count`
tracks how many past queries retrieved this fact as a candidate without it
making the final result set. A fact that keeps losing decays until it falls
below `min_confidence`, at which point it stops being returned entirely —
a passive signal distinct from the hard cardinality/contradiction paths,
meant to surface candidates that could use a registered validator.

`status` values:

| `status` | Meaning |
|----------|---------|
| `ok` | Clean results; any contradiction was resolved by ground truth or provenance priority |
| `unresolved` | Two conflicting, unvalidated facts with equal provenance rank — `results` is empty, `error.code: "CONFLICT_UNRESOLVABLE"`, `error.conflicting_ids` set |
| `degraded` | A registered validator timed out or failed this round — `error.code` is `VALIDATOR_TIMEOUT` or `VALIDATOR_FAILED`, `error.warnings` lists every failure, and each affected result has `resolution: "pending"`. Results are still returned (partial/unrefreshed). |
| `error` | `error.code: "QUERY_TIMEOUT"` if the query exceeded `query_timeout_ms` |

Every `system-derived` result with a registered **and** in-process-callable
validator gets re-checked on each query, up to `max_validators_per_query`
re-invocations per query:

- **Confirmed** — validator's returned value matches the stored value;
  `last_validated` is updated, the fact is unchanged.
- **Refreshed** — values differ; the old fact is tombstoned and a new one
  is written+activated with the validator's current value (single-fact
  refresh, distinct from two-fact contradiction resolution). The new fact's
  `id` is what `/memory/query` returns.
- A confirmed or refreshed fact is **ground truth** for that query: it beats
  any other unvalidated candidate sharing the same `entity+attribute` with
  a conflicting value, even one that would otherwise outrank it under
  provenance priority.

### `POST /memory/resolve`

Two actions, both requiring `action` and `fact_id`:

**`cardinality_conflict_resolve`** — resolves a `CARDINALITY_CONFLICT_PENDING`
fact from `/memory/store`. `resolution` is `"promote"` or `"discard"`.

```bash
curl -s -X POST http://127.0.0.1:8894/memory/resolve \
  -H 'Content-Type: application/json' \
  -d '{
    "action": "cardinality_conflict_resolve",
    "fact_id": "e1d47a10-95f7-48ae-8bac-c703275baa3b",
    "resolution": "promote"
  }'
```

```json
{"status": "ok", "id": "e1d47a10-95f7-48ae-8bac-c703275baa3b", "error": null}
```

- **`discard`** — SQLite-only: tombstones the pending record; the active
  fact and its vector are untouched (the pending record never had a
  vector).
- **`promote`** — commits the pending fact's ChromaDB vector *before*
  touching the active record (crash-safe ordering): if the vector
  upsert fails, the active fact is left completely untouched and the
  pending fact reverts to `provenance_pending` with `error.code:
  "WRITE_ROLLBACK"`; only once the vector is confirmed does it tombstone
  the old active fact, update `entity_registry`, and activate the
  promoted fact — so `/memory/query` now returns the promoted value.

**`reclassify_provenance`** — corrects a fact's provenance tag after the
fact (`confidence` is recomputed from the new provenance):

```bash
curl -s -X POST http://127.0.0.1:8894/memory/resolve \
  -H 'Content-Type: application/json' \
  -d '{
    "action": "reclassify_provenance",
    "fact_id": "84efe888-5044-4b29-88cf-031c447d288f",
    "new_provenance": "user-stated",
    "reason": "originally misclassified by adapter"
  }'
```

Error codes: `FACT_NOT_FOUND` (404, unknown `fact_id`),
`INVALID_RESOLVE_STATE` (400, e.g. resolving a fact that isn't actually a
pending cardinality conflict, or a missing `resolution`/`new_provenance`),
`WRITE_ROLLBACK` (500, promote's vector upsert failed).

### `POST /registry/register` / `GET /registry`

Persists a validator pairing — *which tool* is authoritative for an
`entity+attribute`. This alone doesn't make `/memory/query` able to
actually call that tool; see the limitation note below.

```bash
curl -s -X POST http://127.0.0.1:8894/registry/register \
  -H 'Content-Type: application/json' \
  -d '{
    "entity": "gateway",
    "attribute": "port",
    "validator_tool": "check_gateway_port",
    "adapter": "test",
    "observed_inputs": {"service": "gateway"}
  }'
```

```bash
curl -s http://127.0.0.1:8894/registry | python3 -m json.tool
```

```json
{
  "validators": [
    {
      "entity": "gateway",
      "attribute": "port",
      "validator_tool": "check_gateway_port",
      "adapter": "test",
      "observed_inputs": {"service": "gateway"},
      "paired_at": "2026-07-07T02:00:00.000000Z",
      "callable_registered": false
    }
  ]
}
```

`callable_registered` reflects whether *this process* has an in-process
callable wired up for `validator_tool` via
`app.state.adapter.register_validator_callable(tool_name, fn)` — a pure
`curl` round-trip will always show `false`, since registering a real
callable requires Python code, not an HTTP call. See
`tests/test_adapter.py` / `tests/test_validator.py` for how to wire one up
(e.g. in an embedding application, or for local testing).

**In practice:** registering a pairing via `curl` alone and then querying
that `entity+attribute` will immediately show `status: "degraded"`,
`error.code: "VALIDATOR_FAILED"` ("no callable registered in-process for
tool ..."), and `resolution: "pending"` on the result — because the
pairing exists but nothing can actually invoke it yet. That's expected;
it's the seam the adapter interface fills in.

### `POST /observer/ingest`

Active observation path: the same extraction+write+validator-pairing
logic as `on_tool_call`, over HTTP, with idempotency dedup layered
on top (in-memory, 60-second window, keyed on `adapter` + `tool_name` +
`inputs` + `outputs`).

```bash
curl -s -X POST http://127.0.0.1:8894/observer/ingest \
  -H 'Content-Type: application/json' \
  -d '{
    "adapter": "generic",
    "tool_name": "check_gateway_port",
    "inputs": {"service": "gateway"},
    "outputs": {"entity": "gateway", "attribute": "port", "value": "8890"},
    "source_channel": "observer_ingest"
  }'
```

```json
{"status": "ok", "id": "c2685368-80fc-4b38-af82-ed7d99f4eea2", "deduplicated": false, "error": null}
```

Repeating the *identical* payload within 60 seconds returns the same `id`
with `"deduplicated": true` and does not write a second fact. `outputs`
not matching the configured fact shape (missing `entity`/`attribute`/`value`)
is a no-op, not an error — `id` comes back `null`. A genuine write failure
(e.g. the storage backend is unreachable) returns `status: "error"` instead.

### Passive MCP observer

Not an HTTP endpoint — `create_observed_server` (`memanchor/observer/mcp_observer.py`)
wraps real MCP tool implementations on a genuine `mcp.server.lowlevel.Server`.
After each tool call completes, the observed `(tool_name, inputs, outputs)`
triple is forwarded to `adapter.on_tool_call(...)`, same as ingest, without
touching the tool's own return value:

```python
from memanchor.observer.mcp_observer import ObservedTool, create_observed_server

async def check_gateway_port(service: str) -> dict:
    return {"entity": "gateway", "attribute": "port", "value": "8890"}

server = create_observed_server(
    "my-mcp-server",
    adapter,  # e.g. app.state.adapter
    [ObservedTool(
        name="check_gateway_port",
        description="Checks the gateway's listening port",
        input_schema={"type": "object", "properties": {"service": {"type": "string"}}, "required": ["service"]},
        fn=check_gateway_port,
    )],
)
```

Run it like any MCP server (stdio, SSE, etc. — see the `mcp` SDK's own
transport helpers); `tests/test_mcp_observer.py` shows the in-memory
client↔server session pattern used to test it without a real transport.
A write failure during observation never affects the wrapped tool's own
result — it's swallowed, since observation is intentionally best-effort.

### `GET /telemetry/stats`

Request counts/latency, cumulative cardinality/validator/contradiction
event counts, and a live decay summary.

```bash
curl -s http://127.0.0.1:8894/telemetry/stats | python3 -m json.tool
```

```json
{
  "requests": {
    "memory_store": {"count": 10, "avg_latency_ms": 12.3},
    "memory_query": {"count": 25, "avg_latency_ms": 45.6},
    "memory_resolve": {"count": 2, "avg_latency_ms": 8.1},
    "observer_ingest": {"count": 3, "avg_latency_ms": 15.0}
  },
  "cardinality": {"enforced_overwrites": 4, "conflicts_deferred": 1, "conflicts_promoted": 1, "conflicts_discarded": 0},
  "validators": {"invocations": 6, "confirmed": 3, "refreshed": 2, "timeouts": 0, "failed": 1},
  "contradictions": {"resolved_total": 4, "unresolved_total": 1},
  "decay": {"facts_with_misses": 2, "total_retrieval_count": 30, "total_miss_count": 5},
  "provenance_rejected_total": 1,
  "write_rollback_total": 0
}
```

`requests`/`cardinality`/`validators`/`contradictions`/`provenance_rejected_total`/`write_rollback_total`
are in-memory counters — they reset when the process restarts, same as the
ingest dedup cache. `decay` is computed live from the persisted
`decay_metrics` table, so it's always accurate regardless of restarts.

### `GET /telemetry/contradictions`

The unresolved contradiction log plus decay-flagged candidates (facts whose
`miss_count` has reached a full `decay_halflife`, worth reviewing for a
registered validator).

```bash
curl -s http://127.0.0.1:8894/telemetry/contradictions | python3 -m json.tool
```

```json
{
  "unresolved_contradictions": [
    {"id": "...", "query_text": "widget status", "conflicting_fact_ids": ["a", "b"], "entity": "widget", "attribute": "status", "detected_at": "..."}
  ],
  "decay_flagged_candidates": [
    {"fact_id": "...", "entity": "gateway", "attribute": "port", "miss_count": 12, "retrieval_count": 3}
  ]
}
```

`unresolved_contradictions` is persisted (`contradiction_log` table,
appended to every time `/memory/query` returns `status: "unresolved"`) —
this one survives restarts, since it's meant as a durable audit log rather
than an ephemeral counter.

### `GET /telemetry/provenance-audit`

Counts by provenance × resolution outcome, plus anomaly detection for
"high inferred rate on cardinality-ONE attributes" — both computed live
from the `facts` and `entity_registry` tables.

```bash
curl -s http://127.0.0.1:8894/telemetry/provenance-audit | python3 -m json.tool
```

```json
{
  "counts": [
    {"provenance": "system-derived", "status": "active", "count": 5},
    {"provenance": "inferred", "status": "active", "count": 1}
  ],
  "anomalies": [
    {"fact_id": "...", "entity": "user", "attribute": "home_city", "reason": "inferred provenance on cardinality-ONE attribute"}
  ]
}
```

An anomaly here always implies the write went through with
`force_provenance: true` — the guardrail otherwise rejects exactly this
combination outright.

### `DELETE /memory/{id}`

Soft-deletes (tombstones) a fact by id. Returns `404` if the id doesn't
exist.

```bash
curl -s -X DELETE http://127.0.0.1:8894/memory/84efe888-5044-4b29-88cf-031c447d288f
```

### `GET /health`

Liveness across SQLite and ChromaDB.

```json
{"status": "ok", "components": {"sqlite": "ok", "chroma": "ok"}}
```

## CLI tools

### `calibrate_threshold.py`

`contradiction_similarity_threshold`'s default (0.92) is unvalidated
against any particular embedding distribution — this reports
false-positive/false-negative rates for candidate values against a labeled
corpus, using the exact embedding function and cosine similarity the live
query pipeline uses.

```bash
pipenv run python -m memanchor.tools.calibrate_threshold \
  --corpus calibration_pairs.example.jsonl \
  --thresholds 0.85,0.88,0.90,0.92,0.95
```

Corpus format (see [`calibration_pairs.example.jsonl`](calibration_pairs.example.jsonl)):
one JSON object per line, `{"query": "...", "doc_a": "...", "doc_b": "...", "value_a": "...", "value_b": "...", "label": "same" | "conflict"}`.
`value_a`/`value_b` matter because production's `resolve_contradictions`
only calls something a contradiction when both cosine similarity *and*
`values_conflict` on the raw fact value agree, so calibrating on similarity
alone would misreport paraphrases of the same value (high similarity,
identical value) as false positives. `"same"` pairs should **not** trigger
contradiction detection; `"conflict"` pairs should. Output is one row per
threshold with `fp_rate`/`fn_rate` — pick the threshold with an acceptable
false-positive rate for your corpus before relying on semantic
contradiction detection in production.

### `backup_snapshot.sh`

Coordinated backup: checkpoints the SQLite WAL, exports SQLite via
`.backup`, copies the ChromaDB persistent directory, and writes a manifest
with the snapshot timestamp and embedding model version.

```bash
./memanchor/tools/backup_snapshot.sh [config_path] [backup_root]
# defaults: config_path=memanchor.yaml, backup_root=backups
```

Output: `backups/<UTC-timestamp>/{memanchor.db, chroma/, manifest.json}`.
No companion restore script — recovery is manual: stop the service, replace
the SQLite file and Chroma directory from a snapshot, restart, and verify
via `GET /health`.

## Repo layout

```
memanchor/
├── config.py                  # memanchor.yaml loader
├── models/                    # pydantic request/response models
│   ├── common.py               # ErrorDetail
│   ├── memory.py               # Store/Query/Resolve request/response
│   ├── observer.py             # Ingest request/response
│   ├── registry.py             # validator registry request/response
│   └── telemetry.py            # telemetry endpoint response shapes
├── contracts/
│   └── adapter.py              # AttributeDefinition, FactShapeDefinition, FactCandidate, MemAnchorAdapter, ToolCallWriteFailed, load_fact_shape()
├── adapters/
│   └── generic/
│       └── adapter.py          # GenericAdapter(MemAnchorAdapter) -- reference implementation, no framework-specific code
├── domain/                    # pure logic, no I/O
│   ├── ids.py                  # new_id() / now_iso()
│   ├── provenance.py           # write-time / query-time rank tables, base confidence
│   ├── contradiction.py        # value normalizers, semantic contradiction resolution
│   └── decay.py                # effective_confidence, apply_decay_weighting
├── application/                # use-cases, orchestrate ports + domain
│   ├── write_pipeline.py       # guardrail -> cardinality -> transactional write
│   ├── query_pipeline.py       # retrieve -> decay filter -> revalidate -> ground-truth check -> contradiction -> resolve -> decay bookkeeping
│   ├── resolve_pipeline.py     # cardinality_conflict_resolve (promote/discard), reclassify_provenance
│   ├── validator.py            # validator re-invocation, confirm/refresh/timeout/failed
│   ├── ingest_service.py       # POST /observer/ingest use-case (dedup + adapter.on_tool_call)
│   ├── memory_service.py       # DELETE /memory/{id} use-case
│   ├── registry_service.py     # POST /registry/register, GET /registry use-cases
│   └── telemetry_service.py    # GET /telemetry/* use-cases
├── ports/
│   └── stores.py               # FactStore, VectorStore Protocols
├── db/
│   ├── sqlite/
│   │   ├── store.py             # SqliteStore -- facade over the 5 repos below, satisfies FactStore
│   │   ├── repos.py             # FactsRepo / EntityRegistryRepo / ValidatorsRepo / DecayMetricsRepo / ContradictionLogRepo
│   │   └── schema.py            # facts + entity_registry + validators + decay_metrics + contradiction_log DDL
│   ├── chroma/
│   │   └── store.py             # ChromaStore -- real MiniLM embeddings, satisfies VectorStore
│   └── validator/
│       └── registry.py          # ValidatorCallableRegistry -- in-process tool_name -> callable, composed by GenericAdapter
├── observer/
│   ├── ingest.py               # dedup key + IngestDedupCache for POST /observer/ingest
│   └── mcp_observer.py         # create_observed_server -- passive MCP observer on mcp.server.lowlevel.Server
├── telemetry/
│   └── counters.py             # TelemetryCounters -- in-memory request/event counters
├── endpoints/
│   ├── app.py                  # FastAPI app + lifespan (startup crash-recovery sweep, background sweeps)
│   ├── errors.py                # ERROR_HTTP_STATUS mapping, error_response()
│   ├── timing.py                # timed() -- shared per-route latency instrumentation
│   └── routers/                # health.py, memory.py, observer.py, registry.py, telemetry.py
└── tools/
    ├── calibrate_threshold.py  # contradiction threshold calibration
    └── backup_snapshot.sh      # coordinated SQLite + ChromaDB backup

tests/                          # pytest + pytest-asyncio + httpx, isolated per-test app instances
memanchor.yaml                  # config
fact_shape.example.json         # fact shape schema example
calibration_pairs.example.jsonl  # calibration corpus schema example
```

## Contributing

Bug fixes, new adapters, storage backends, tests, and docs are all welcome.
See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and PR guidelines. If
you're an AI coding agent working in this repo, see [AGENTS.md](AGENTS.md)
for the project's design principles and conventions.

See [CHANGELOG.md](CHANGELOG.md) for release history. The current version
is available at `memanchor.__version__`.

## License

[MIT](LICENSE)
