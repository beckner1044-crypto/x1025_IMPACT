"""
retrieval_eval.py
Retrieval-quality metrics for Layer 1.

The existing eval just asks "did the expected source file appear in top-k?".
That's a coarse signal — a wrong section of the right file scores the same
as the exact paragraph we needed. This module adds the standard IR metrics:

  - Recall@K       fraction of expected answer spans that appear in top-K chunks
  - MRR            mean reciprocal rank of the first chunk that contains a span
  - Precision@K    fraction of top-K chunks that contain at least one span
  - source_in_topk legacy signal kept for back-compat and quick smoke tests

A "span" is a sentence (or short phrase) from the source document that the
retriever should surface for a question. Matching is normalized: lowercase,
whitespace collapsed, leading/trailing punctuation stripped. We use substring
match rather than exact-match because chunkers may merge or split sentences
in ways that alter punctuation but preserve content.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional


# --------------------------------------------------------------------------- #
# Q/A test set
# --------------------------------------------------------------------------- #
@dataclass
class RetrievalCase:
    qid: str
    question: str
    expected_source: str            # e.g. "02_fire_emergency.md"
    answer_spans: List[str]         # sentences/phrases the retriever should surface


# Held-out cases for the prototype's actual ISM corpus.
# Each span is a phrase that should appear (after normalization) somewhere in
# at least one retrieved chunk for the case to count as a recall hit.
RETRIEVAL_CASES: List[RetrievalCase] = [
    RetrievalCase(
        qid="r01",
        question="What is the procedure for releasing the fixed CO2 system in the engine room?",
        expected_source="02_fire_emergency.md",
        answer_spans=[
            "fixed CO2 system may only be released",
            "head count of all engine room personnel",
            "sound the CO2 pre-alarm for at least 20 seconds",
        ],
    ),
    RetrievalCase(
        qid="r02",
        question="Who can authorize CO2 release on a vessel?",
        expected_source="02_fire_emergency.md",
        answer_spans=[
            "the Master",
            "Chief Engineer with the Master's verbal authorization",
        ],
    ),
    RetrievalCase(
        qid="r03",
        question="What fields are required in the noon report?",
        expected_source="03_daily_reporting.md",
        answer_spans=[
            "Fuel ROB",
            "Course and speed over ground",
            "Position (latitude, longitude)",
        ],
    ),
    RetrievalCase(
        qid="r04",
        question="When is the noon report submitted?",
        expected_source="03_daily_reporting.md",
        answer_spans=[
            "every day at 1200 hours ship's local time",
        ],
    ),
    RetrievalCase(
        qid="r05",
        question="What does the Master do if a certificate expires while at sea?",
        expected_source="04_certificate_management.md",
        answer_spans=[
            "Notify the office and DPA immediately",
            "Notify flag administration and request a short-term extension",
            "Avoid entering port until the extension is granted",
        ],
    ),
    RetrievalCase(
        qid="r06",
        question="How long is the IOPP certificate valid?",
        expected_source="04_certificate_management.md",
        answer_spans=[
            "IOPP Certificate",
            "5-year validity",
        ],
    ),
    RetrievalCase(
        qid="r07",
        question="Describe the Williamson turn for man overboard.",
        expected_source="05_man_overboard.md",
        answer_spans=[
            "hard rudder to the side of the casualty",
            "swing 60",
            "reciprocal course",
        ],
    ),
    RetrievalCase(
        qid="r08",
        question="What are the immediate actions when someone is sighted falling overboard?",
        expected_source="05_man_overboard.md",
        answer_spans=[
            "MAN OVERBOARD",
            "throw the nearest lifebuoy",
            "Maintain visual contact",
        ],
    ),
    RetrievalCase(
        qid="r09",
        question="What pre-transfer checks are required before bunkering?",
        expected_source="01_fuel_transfer.md",
        answer_spans=[
            "scuppers are plugged",
            "Display the \"Bravo\" (red) flag",
            "Record initial tank soundings",
        ],
    ),
    RetrievalCase(
        qid="r10",
        question="What is the maximum fill level for a bunker tank?",
        expected_source="01_fuel_transfer.md",
        answer_spans=[
            "Stop transfer at 90% tank capacity",
            "Never exceed 95",
        ],
    ),
]


# --------------------------------------------------------------------------- #
# Span matching
# --------------------------------------------------------------------------- #
_NORMALIZE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Lowercase, collapse whitespace, strip surrounding punctuation. We do
    NOT strip internal punctuation because some answer spans (e.g. "5-year"
    or "20 seconds") rely on it for disambiguation."""
    text = text.lower().strip()
    text = _NORMALIZE_RE.sub(" ", text)
    text = text.strip(" .,;:!?\"'()[]{}")
    return text


def _chunk_contains_span(chunk_text: str, span: str) -> bool:
    return _normalize(span) in _normalize(chunk_text)


# --------------------------------------------------------------------------- #
# Per-case computation
# --------------------------------------------------------------------------- #
@dataclass
class RetrievalResult:
    qid: str
    k: int
    recall_at_k: float                # spans found / spans expected
    precision_at_k: float             # chunks with ≥1 span / k
    mrr: float                        # 1/rank of first chunk containing any span (0 if none)
    source_in_topk: bool
    spans_found: int
    spans_total: int


def evaluate_case(
    case: RetrievalCase,
    retrieved_chunks: List[str],
    retrieved_sources: List[str],
    k: int,
) -> RetrievalResult:
    """Score one case given the top-K retrieved chunk texts and their sources."""
    chunks = retrieved_chunks[:k]
    sources = retrieved_sources[:k]

    # Per-span: did *any* of the top-K chunks contain it?
    span_hits = [
        any(_chunk_contains_span(c, s) for c in chunks)
        for s in case.answer_spans
    ]
    spans_found = sum(span_hits)
    recall = spans_found / len(case.answer_spans) if case.answer_spans else 0.0

    # Per-chunk: did this chunk contain at least one expected span?
    chunk_is_relevant = [
        any(_chunk_contains_span(c, s) for s in case.answer_spans)
        for c in chunks
    ]
    precision = sum(chunk_is_relevant) / k if k else 0.0

    # First chunk that contains any expected span
    mrr = 0.0
    for rank, relevant in enumerate(chunk_is_relevant, start=1):
        if relevant:
            mrr = 1.0 / rank
            break

    return RetrievalResult(
        qid=case.qid,
        k=k,
        recall_at_k=recall,
        precision_at_k=precision,
        mrr=mrr,
        source_in_topk=case.expected_source in sources,
        spans_found=spans_found,
        spans_total=len(case.answer_spans),
    )


# --------------------------------------------------------------------------- #
# Aggregate
# --------------------------------------------------------------------------- #
@dataclass
class RetrievalSummary:
    n: int
    k: int
    mean_recall: float
    mean_precision: float
    mean_mrr: float
    source_hit_rate: float
    per_case: List[RetrievalResult] = field(default_factory=list)


def aggregate(results: List[RetrievalResult]) -> RetrievalSummary:
    if not results:
        return RetrievalSummary(0, 0, 0.0, 0.0, 0.0, 0.0)
    n = len(results)
    return RetrievalSummary(
        n=n,
        k=results[0].k,
        mean_recall    = sum(r.recall_at_k    for r in results) / n,
        mean_precision = sum(r.precision_at_k for r in results) / n,
        mean_mrr       = sum(r.mrr            for r in results) / n,
        source_hit_rate= sum(r.source_in_topk for r in results) / n,
        per_case=results,
    )
