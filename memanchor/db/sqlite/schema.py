"""SQLite schema: `facts`, `entity_registry`, `validators`, and
`decay_metrics`, plus `contradiction_log`, which backs
`GET /telemetry/contradictions`'s durable unresolved-contradiction audit
log."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,
    vector_id TEXT NOT NULL,
    entity TEXT NOT NULL,
    attribute TEXT NOT NULL,
    value TEXT NOT NULL,
    content TEXT NOT NULL,
    provenance TEXT NOT NULL,
    source_channel TEXT NOT NULL,
    provenance_confidence REAL NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL,  -- active | pending | provenance_pending | tombstoned
    conflict_type TEXT,    -- cardinality | null
    created_at TEXT NOT NULL,
    last_validated TEXT,
    superseded_by TEXT
);

CREATE TABLE IF NOT EXISTS entity_registry (
    entity TEXT NOT NULL,
    attribute TEXT NOT NULL,
    cardinality TEXT NOT NULL,  -- ONE | MANY
    active_fact_id TEXT,
    PRIMARY KEY (entity, attribute)
);

CREATE TABLE IF NOT EXISTS validators (
    entity TEXT NOT NULL,
    attribute TEXT NOT NULL,
    validator_tool TEXT NOT NULL,
    adapter TEXT NOT NULL,
    observed_inputs TEXT NOT NULL,  -- JSON
    paired_at TEXT NOT NULL,
    PRIMARY KEY (entity, attribute)
);

CREATE TABLE IF NOT EXISTS decay_metrics (
    fact_id TEXT PRIMARY KEY,
    retrieval_count INTEGER DEFAULT 0,
    miss_count INTEGER DEFAULT 0,
    last_retrieved TEXT
);

CREATE TABLE IF NOT EXISTS contradiction_log (
    id TEXT PRIMARY KEY,
    query_text TEXT NOT NULL,
    conflicting_fact_ids TEXT NOT NULL,  -- JSON list
    entity TEXT,
    attribute TEXT,
    detected_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_facts_entity_attribute ON facts (entity, attribute);
"""
