from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path("memanchor.yaml")


@dataclass
class Config:
    bind_host: str = "127.0.0.1"
    bind_port: int = 8894
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dimensions: int = 384
    contradiction_similarity_threshold: float = 0.92
    query_timeout_ms: int = 15000
    validator_timeout_ms: int = 5000
    max_validators_per_query: int = 3
    decay_halflife: float = 10.0
    sqlite_path: str = "./data/memanchor.db"
    chroma_path: str = "./data/chroma"
    fact_shape_path: str = "./fact_shape.json"
    stale_pending_sweep_interval_seconds: float = 30.0
    ingest_dedup_sweep_interval_seconds: float = 60.0
    sqlite_ping_timeout_ms: int = 2000


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> Config:
    path = Path(path)
    if not path.exists():
        return Config()
    with path.open() as f:
        raw = yaml.safe_load(f) or {}
    known_fields = {f for f in Config.__dataclass_fields__}
    unknown_keys = sorted(set(raw) - known_fields)
    if unknown_keys:
        warnings.warn(
            f"{path}: ignoring unrecognized config key(s): {', '.join(unknown_keys)}",
            stacklevel=2,
        )
    filtered = {k: v for k, v in raw.items() if k in known_fields}
    return Config(**filtered)
