# AGENTS.md

Guidance for AI coding agents (Claude Code, Cursor, Codex, etc.) working in
this repository. Human contributors: see [CONTRIBUTING.md](CONTRIBUTING.md).

## The Mission

MemAnchor exists to fix a gap plain vector databases leave open: they find
things by meaning, but they have no way to tell that two remembered facts
contradict each other, that a fact has gone stale, or that only one value
should ever be active for a given attribute. Every design decision here
serves that gap, not general-purpose memory storage.

## Design Principles

Non-negotiable. Every PR, feature, or refactor should honor these:

- **Provenance is the arbitration mechanism, not LLM judgment.** Contradictions
  resolve deterministically from *how a fact entered the system*
  (`system-derived` / `user-stated` / `inferred`) and, where one exists, a
  validator re-check against ground truth. Don't add a code path that asks
  an LLM to guess which of two conflicting facts is "more likely correct."
- **Host-anywhere, no cloud dependency.** Runs on any machine that can run
  Python, isn't tied to any particular agent framework. Don't add a
  hard dependency on an external hosted service for core read/write paths.
- **Callers must be able to trust `status`.** Every `/memory/query` response
  leads with `status` (`ok` / `unresolved` / `degraded` / `error`) precisely
  so a caller can tell "confidently correct," "conflicting, unresolved," and
  "partially stale" apart. Don't collapse two different outcomes into the
  same status or the same `None`/`null` — see the `ToolCallWriteFailed`
  exception in `contracts/adapter.py` for why that distinction matters in
  practice (a real write failure was once silently indistinguishable from a
  harmless no-op).
- **Crash-safety ordering in write paths is load-bearing, not incidental.**
  Where a write touches both SQLite and ChromaDB, the vector is always
  committed *before* the SQLite record is activated, and a failure at any
  step reverts cleanly rather than leaving an orphaned reference. If you
  touch `write_pipeline.py` or `resolve_pipeline.py`, preserve this ordering
  and add a test that forces the failure path, not just the happy path.
- **Don't force a shared abstraction onto code that differs for real
  reasons.** `transactional_write` and `resolve_pipeline._promote` look like
  duplicated sagas but diverge in genuine ways (what "rollback" means for a
  brand-new row vs. an existing pending one, whether stale vectors get
  cleaned up). A prior attempt to unify them into one parameterized helper
  was deliberately declined for exactly this reason — see the "Won't Do"
  note on Task 14 in this repo's development history. Prefer small,
  independently-readable functions over a single function with a handful of
  boolean flags that route around real behavioral differences.

## Contributing

We welcome bug fixes, new adapters, storage backend implementations, test
coverage, and documentation improvements.

We don't accept: features that make an LLM call to resolve a contradiction
(defeats the deterministic-resolution design), a hard dependency on a hosted
service for core store/query paths, or a "unify these" refactor that papers
over a genuine behavioral difference between two code paths instead of
documenting it.

## Setup

```bash
pipenv install --dev
```

## Commands

```bash
# Run the full test suite
pipenv run pytest tests/ -v

# Run a single test file
pipenv run pytest tests/test_write_pipeline.py -v

# Run the server
pipenv run uvicorn memanchor.endpoints.app:app --port 8894

# Calibrate the semantic contradiction threshold against a labeled corpus
pipenv run python -m memanchor.tools.calibrate_threshold --corpus calibration_pairs.example.jsonl
```

There is no linter or formatter wired into this repo yet — match the
surrounding code's existing style (see Conventions below) rather than
introducing a new one unannounced.

## Project Structure

See the README's [Repo layout](README.md#repo-layout) section for the full
annotated tree. In short: `contracts/` + `ports/` are the interfaces
(adapter contract, storage Protocols); `domain/` is pure logic with no I/O;
`application/` orchestrates domain + ports into use-cases; `db/` and
`adapters/` are the concrete implementations; `endpoints/` is the FastAPI
surface.

## Conventions

- **Style**: `from __future__ import annotations` at the top of every
  module; `snake_case` for functions/variables, `PascalCase` for classes;
  type hints throughout.
- **Comments**: only when the WHY isn't obvious from the code — a hidden
  constraint, a subtle invariant, a workaround for a specific failure mode.
  Don't restate what the code already says.
- **Data shapes**: Pydantic `BaseModel` for anything that crosses the wire
  (`memanchor/models/`), plain `@dataclass` for internal value objects
  (e.g. `WriteIntent` in `write_pipeline.py`), `Protocol` for anything a
  second implementation might satisfy (`ports/stores.py`).
- **Errors**: application code returns a typed response with
  `status`/`error` fields — it does not raise past its own boundary except
  for the specific, intentional exceptions declared in `contracts/adapter.py`.
  HTTP status mapping lives in one place, `endpoints/errors.py`'s
  `ERROR_HTTP_STATUS` dict — add new codes there, not ad hoc in a router.
  Add or extend an existing single-purpose exception rather than reusing
  `None`/`null` to mean two different things (see `ToolCallWriteFailed`).
- **Telemetry**: any new write/query/resolve path should call
  `telemetry.increment(...)` for its event counters, consistent with the
  existing pipelines, so `/telemetry/stats` stays accurate.
- **Tests**: one file per module under `tests/`, isolated per-test app
  instance via `tests/helpers.py`'s `build_client` (fresh `tmp_path` SQLite +
  Chroma collection — tests never share state or touch `./data`). Write the
  failing test before the fix, not after.
- **Refactors with no behavior change**: the existing test suite is the
  regression net — no need to manufacture a new test if nothing observable
  changes, but do run the full suite, not just the file you touched.

## Architecture

```
Client → FastAPI routers (endpoints/routers/)
       → application/*.py  (write_pipeline, query_pipeline, resolve_pipeline, validator, ...)
       → ports (FactStore, VectorStore Protocols)
       → db/sqlite (facts, entity_registry, validators, decay_metrics, contradiction_log)
       → db/chroma (semantic vectors)
```

## Key Files for Common Tasks

- **Adding an endpoint**: new router in `endpoints/routers/`, registered in
  `endpoints/app.py`; map any new error codes in `endpoints/errors.py`.
- **Adding a storage backend**: implement `FactStore` and/or `VectorStore`
  from `ports/stores.py` — no application-layer code should need to change.
- **Adding an adapter**: implement `MemAnchorAdapter` from
  `contracts/adapter.py`; `adapters/generic/adapter.py` is the reference
  implementation to model against.
- **Changing contradiction detection**: `domain/contradiction.py`
  (`values_conflict`, `cosine_similarity`) plus
  `tools/calibrate_threshold.py`, which must keep using the *exact* same
  similarity function the query pipeline uses — no parallel approximation.
- **Changing write/rollback behavior**: `application/write_pipeline.py`
  (`transactional_write`) and `application/resolve_pipeline.py` (`_promote`)
  — read the Design Principles note above before touching either.
