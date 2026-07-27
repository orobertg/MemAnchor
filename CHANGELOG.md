# Changelog

All notable changes to MemAnchor are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-07-27

Initial standalone release.

### Added

- Storage: SQLite (`facts`, `entity_registry`, `validators`, `decay_metrics`,
  `contradiction_log`) + ChromaDB with `sentence-transformers/all-MiniLM-L6-v2`
  embeddings.
- Write pipeline: provenance guardrail, cardinality enforcement (overwrite /
  deferred conflict), transactional write with rollback, telemetry-instrumented.
- Query pipeline: Chroma nearest-neighbor → SQLite post-filter with
  decay-weighted confidence → validator revalidation → ground-truth
  cross-check → semantic contradiction check → provenance-priority
  resolution → decay bookkeeping.
- Resolve pipeline: `cardinality_conflict_resolve` (promote/discard) and
  `reclassify_provenance`.
- Adapter interface: `MemAnchorAdapter` contract + `GenericAdapter` reference
  implementation.
- Observation layer: passive MCP observer + active `POST /observer/ingest`
  with idempotency dedup.
- Telemetry: `GET /telemetry/stats`, `GET /telemetry/contradictions`,
  `GET /telemetry/provenance-audit`.
- CLI tools: `calibrate_threshold.py`, `backup_snapshot.sh`.
- Full API: `POST /memory/store`, `POST /memory/query`, `POST /memory/resolve`,
  `POST /registry/register`, `GET /registry`, `POST /observer/ingest`,
  `GET /telemetry/*`, `DELETE /memory/{id}`, `GET /health`.
