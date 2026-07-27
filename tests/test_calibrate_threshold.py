"""Threshold calibration tool: FP/FN rate math in isolation, corpus
parsing, and one real end-to-end invocation exactly as documented."""

import subprocess
import sys

from memanchor.tools.calibrate_threshold import (
    CalibrationPair,
    evaluate_threshold,
    load_corpus,
)


def test_evaluate_threshold_computes_fp_fn_rates():
    pairs = [
        # same-pair #1: above threshold AND values conflict -- false positive.
        CalibrationPair(query="q", doc_a="a", doc_b="b", value_a="1", value_b="2", label="same"),
        # same-pair #2: below threshold -- correct regardless of values.
        CalibrationPair(query="q", doc_a="a", doc_b="b", value_a="1", value_b="1", label="same"),
        # conflict-pair #1: below threshold -- false negative regardless of values.
        CalibrationPair(query="q", doc_a="a", doc_b="b", value_a="1", value_b="2", label="conflict"),
        # conflict-pair #2: above threshold AND values conflict -- correct.
        CalibrationPair(query="q", doc_a="a", doc_b="b", value_a="1", value_b="2", label="conflict"),
    ]
    similarities = [0.95, 0.5, 0.4, 0.95]

    report = evaluate_threshold(pairs, similarities, threshold=0.9)

    assert report.total_same == 2
    assert report.total_conflict == 2
    assert report.false_positives == 1
    assert report.false_negatives == 1
    assert report.false_positive_rate == 0.5
    assert report.false_negative_rate == 0.5


def test_evaluate_threshold_gates_on_values_conflict():
    """Production's resolve_contradictions requires BOTH similarity > threshold
    AND values_conflict on the raw fact value -- a high-similarity 'same' pair
    (paraphrase, identical underlying value) must not count as a false
    positive just because the embeddings are close, and a high-similarity
    'conflict' pair with genuinely different values must still be caught."""
    pairs = [
        CalibrationPair(
            query="q",
            doc_a="Gateway runs on port 8890",
            doc_b="The gateway's port is 8890",
            value_a="8890",
            value_b="8890",
            label="same",
        ),
        CalibrationPair(
            query="q",
            doc_a="Gateway runs on port 8890",
            doc_b="Gateway listens on 8080",
            value_a="8890",
            value_b="8080",
            label="conflict",
        ),
    ]
    # Both pairs are high embedding similarity (paraphrase / same topic) --
    # only the values_conflict gate distinguishes them.
    similarities = [0.97, 0.97]

    report = evaluate_threshold(pairs, similarities, threshold=0.9)

    assert report.false_positives == 0
    assert report.false_negatives == 0


def test_evaluate_threshold_handles_empty_label_groups():
    pairs = [CalibrationPair(query="q", doc_a="a", doc_b="b", value_a="1", value_b="1", label="same")]
    report = evaluate_threshold(pairs, [0.5], threshold=0.9)
    assert report.total_conflict == 0
    assert report.false_negative_rate == 0.0


def test_load_corpus_parses_section_6_6_format(tmp_path):
    corpus_path = tmp_path / "calibration_pairs.jsonl"
    corpus_path.write_text(
        '{"query": "gateway port", "doc_a": "Gateway runs on port 8890", '
        '"doc_b": "Gateway runs on port 8890", "value_a": "8890", "value_b": "8890", "label": "same"}\n'
        '{"query": "gateway port", "doc_a": "Gateway runs on port 8890", '
        '"doc_b": "Gateway listens on 8080", "value_a": "8890", "value_b": "8080", "label": "conflict"}\n'
    )

    pairs = load_corpus(str(corpus_path))

    assert len(pairs) == 2
    assert pairs[0].label == "same"
    assert pairs[1].label == "conflict"
    assert pairs[1].doc_b == "Gateway listens on 8080"
    assert pairs[1].value_a == "8890"
    assert pairs[1].value_b == "8080"


def test_cli_end_to_end_matches_documented_usage(tmp_path):
    corpus_path = tmp_path / "calibration_pairs.jsonl"
    corpus_path.write_text(
        '{"query": "gateway port", "doc_a": "Gateway runs on port 8890", '
        '"doc_b": "Gateway runs on port 8890", "value_a": "8890", "value_b": "8890", "label": "same"}\n'
        '{"query": "gateway port", "doc_a": "Gateway runs on port 8890", '
        '"doc_b": "Gateway listens on 8080", "value_a": "8890", "value_b": "8080", "label": "conflict"}\n'
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "memanchor.tools.calibrate_threshold",
            "--corpus",
            str(corpus_path),
            "--thresholds",
            "0.85,0.90,0.92,0.95",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert "threshold" in result.stdout
    assert "fp_rate" in result.stdout
    assert "0.850" in result.stdout
    assert "0.950" in result.stdout
