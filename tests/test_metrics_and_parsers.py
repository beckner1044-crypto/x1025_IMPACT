"""Tests for x1025/retrieval_eval.py and x1025/faithfulness.py."""
import pytest

from x1025.retrieval_eval import (
    RetrievalCase, evaluate_case, _normalize, _chunk_contains_span,
)
from x1025.faithfulness import _parse_claim_list, _parse_verdict


# --- Retrieval metrics ---------------------------------------------------- #
def test_normalize_strips_punctuation_and_lowercases():
    assert _normalize("  Hello, World!  ") == "hello, world"


def test_normalize_preserves_internal_punctuation():
    """5-year and 20 seconds are tokens we need to match exactly."""
    assert "5-year" in _normalize("5-year validity")
    assert "20 second" in _normalize("for at least 20 seconds")


def test_chunk_contains_span_substring_match():
    chunk = "The Master must authorize CO2 release."
    assert _chunk_contains_span(chunk, "Master must authorize")
    assert not _chunk_contains_span(chunk, "Chief Engineer")


def test_evaluate_case_perfect_recall():
    case = RetrievalCase(
        qid="t1", question="?", expected_source="x.md",
        answer_spans=["alpha", "beta"],
    )
    chunks = ["doc with alpha here", "doc with beta here", "irrelevant", "irrelevant"]
    sources = ["x.md", "x.md", "y.md", "y.md"]
    r = evaluate_case(case, chunks, sources, k=4)
    assert r.recall_at_k == 1.0
    assert r.spans_found == 2
    assert r.source_in_topk is True
    assert r.mrr == 1.0  # first chunk has alpha


def test_evaluate_case_zero_recall():
    case = RetrievalCase(qid="t2", question="?", expected_source="x.md",
                         answer_spans=["alpha"])
    chunks = ["totally unrelated", "also unrelated"]
    sources = ["y.md", "y.md"]
    r = evaluate_case(case, chunks, sources, k=2)
    assert r.recall_at_k == 0.0
    assert r.mrr == 0.0
    assert r.source_in_topk is False


def test_evaluate_case_partial_recall_correct_mrr():
    case = RetrievalCase(qid="t3", question="?", expected_source="x.md",
                         answer_spans=["alpha", "beta", "gamma"])
    # Only 'alpha' present, in chunk 2 (rank 2 → MRR 0.5)
    chunks = ["irrelevant", "doc with alpha"]
    sources = ["y.md", "x.md"]
    r = evaluate_case(case, chunks, sources, k=2)
    assert r.recall_at_k == 1 / 3
    assert r.mrr == 0.5


# --- Faithfulness parsers ------------------------------------------------- #
def test_parse_claim_list_from_json_array():
    raw = '["claim 1", "claim 2", "claim 3"]'
    assert _parse_claim_list(raw) == ["claim 1", "claim 2", "claim 3"]


def test_parse_claim_list_handles_code_fences():
    raw = '```json\n["claim a", "claim b"]\n```'
    assert _parse_claim_list(raw) == ["claim a", "claim b"]


def test_parse_claim_list_falls_back_to_numbered_list():
    raw = "Here are the claims:\n1. First claim.\n2. Second claim.\n3. Third one."
    out = _parse_claim_list(raw)
    assert len(out) == 3
    assert out[0] == "First claim."


def test_parse_claim_list_handles_garbage_safely():
    """Bug-shaped: unparseable input returns empty list, not crash."""
    assert _parse_claim_list("totally not json or a list") == []


def test_parse_verdict_supported():
    raw = '{"label": "SUPPORTED", "rationale": "matches"}'
    v = _parse_verdict("test claim", raw)
    assert v.label == "SUPPORTED"
    assert v.rationale == "matches"


def test_parse_verdict_handles_code_fences():
    raw = '```\n{"label":"NOT_FOUND","rationale":"silent"}\n```'
    v = _parse_verdict("test", raw)
    assert v.label == "NOT_FOUND"


def test_parse_verdict_regex_fallback():
    """If JSON parsing fails, look for the label keyword in raw text."""
    raw = "After review: SUPPORTED — the evidence clearly says so"
    v = _parse_verdict("test", raw)
    assert v.label == "SUPPORTED"


def test_parse_verdict_unparseable_defaults_to_safe():
    """The safety-critical default: unparseable verifier output is NOT_FOUND.
    This prevents auto-shipping of un-verified answers."""
    v = _parse_verdict("test claim", "i dunno")
    assert v.label == "NOT_FOUND"
