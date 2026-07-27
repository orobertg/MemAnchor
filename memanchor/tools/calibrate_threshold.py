"""Threshold calibration: reports false positive / false negative rates for
candidate `contradiction_similarity_threshold` values against a labeled
corpus (format described below), so an operator can pick a real value
instead of the unvalidated default (0.92).

Usage:
    python -m memanchor.tools.calibrate_threshold \
      --corpus ./data/calibration_pairs.jsonl \
      --thresholds 0.85,0.88,0.90,0.92,0.95

Uses the same embedding function ChromaStore uses
(memanchor/db/chroma/store.py) and the same cosine_similarity used by the
live query pipeline (memanchor/domain/contradiction.py), so calibration
measures the exact behavior contradiction detection relies on.

Deliberately stricter than a naive similarity-only corpus schema: production's
resolve_contradictions() only calls something a contradiction when BOTH
cosine_similarity > threshold AND values_conflict(fact_a.value, fact_b.value)
are true (domain/contradiction.py). A corpus with only doc_a/doc_b (full
sentences) and no analog to the raw fact `value` field would systematically
diverge from
production -- e.g. two paraphrases of the same underlying value ("Gateway
runs on port 8890" vs "The gateway's port is 8890") are high-similarity and
would be misreported as a false positive even though production would never
call them a contradiction. `CalibrationPair` therefore adds required
`value_a` / `value_b` fields (first build, no existing corpora to preserve
compatibility with) so `evaluate_threshold` can gate on values_conflict
exactly like production does.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass

from chromadb.utils import embedding_functions

from memanchor.config import DEFAULT_CONFIG_PATH, load_config
from memanchor.domain.contradiction import cosine_similarity, values_conflict


@dataclass
class CalibrationPair:
    query: str
    doc_a: str
    doc_b: str
    value_a: str
    value_b: str
    label: str  # "same" | "conflict"


@dataclass
class ThresholdReport:
    threshold: float
    false_positives: int
    false_negatives: int
    total_same: int
    total_conflict: int

    @property
    def false_positive_rate(self) -> float:
        return self.false_positives / self.total_same if self.total_same else 0.0

    @property
    def false_negative_rate(self) -> float:
        return self.false_negatives / self.total_conflict if self.total_conflict else 0.0


def load_corpus(path: str) -> list[CalibrationPair]:
    pairs = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            pairs.append(CalibrationPair(**raw))
    return pairs


def compute_similarities(pairs: list[CalibrationPair], embedding_model: str) -> list[float]:
    embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=embedding_model)
    texts = [text for pair in pairs for text in (pair.doc_a, pair.doc_b)]
    vectors = embed_fn(texts)
    return [
        cosine_similarity(vectors[2 * i], vectors[2 * i + 1])
        for i in range(len(pairs))
    ]


def evaluate_threshold(
    pairs: list[CalibrationPair], similarities: list[float], threshold: float
) -> ThresholdReport:
    total_same = sum(1 for p in pairs if p.label == "same")
    total_conflict = sum(1 for p in pairs if p.label == "conflict")
    false_positives = 0
    false_negatives = 0
    for pair, similarity in zip(pairs, similarities):
        predicted_conflict = similarity > threshold and values_conflict(pair.value_a, pair.value_b)
        if pair.label == "same" and predicted_conflict:
            false_positives += 1
        elif pair.label == "conflict" and not predicted_conflict:
            false_negatives += 1
    return ThresholdReport(threshold, false_positives, false_negatives, total_same, total_conflict)


def format_report_table(reports: list[ThresholdReport]) -> str:
    header = f"{'threshold':>10} {'fp_rate':>10} {'fn_rate':>10} {'fp':>6} {'fn':>6}"
    lines = [header]
    for r in reports:
        lines.append(
            f"{r.threshold:>10.3f} {r.false_positive_rate:>10.3f} {r.false_negative_rate:>10.3f} "
            f"{r.false_positives:>6} {r.false_negatives:>6}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Contradiction threshold calibration")
    parser.add_argument("--corpus", required=True, help="path to calibration_pairs.jsonl")
    parser.add_argument(
        "--thresholds", required=True, help="comma-separated candidate thresholds, e.g. 0.85,0.88,0.90,0.92,0.95"
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="memanchor.yaml path")
    args = parser.parse_args()

    config = load_config(args.config)
    pairs = load_corpus(args.corpus)
    if not pairs:
        raise SystemExit(f"no calibration pairs found in {args.corpus}")

    thresholds = [float(t) for t in args.thresholds.split(",")]
    similarities = compute_similarities(pairs, config.embedding_model)
    reports = [evaluate_threshold(pairs, similarities, t) for t in thresholds]

    print(f"corpus: {args.corpus} ({len(pairs)} pairs, embedding model: {config.embedding_model})")
    print(format_report_table(reports))


if __name__ == "__main__":
    main()
