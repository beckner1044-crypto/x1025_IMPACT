"""
faithfulness.py
Hallucination detection for generated answers.

Pipeline:
  1. Extract atomic factual claims from the answer (one LLM call).
  2. For each claim, ask the LLM whether the retrieved evidence supports it.
  3. Aggregate: faithfulness = supported_claims / total_claims.

This is a *separate* signal from the LLM-as-judge in evaluate.py:
  - The judge gives soft 1-5 scores on faithfulness/relevance/completeness.
  - This verifier gives a hard binary per-claim label and a count of
    unsupported claims, which is what we use to drive the confidence floor.

Same caveat as the judge: in the prototype the verifier shares the LLM under
test, so it has correlated blind spots. Swap to a stronger model for the
final IMPACT report by passing a different LLM into Verifier(...).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #
@dataclass
class ClaimVerdict:
    claim: str
    label: str          # "SUPPORTED" | "CONTRADICTED" | "NOT_FOUND"
    rationale: str = ""


@dataclass
class FaithfulnessResult:
    n_claims: int
    n_supported: int
    n_contradicted: int
    n_not_found: int
    score: float                          # supported / total, or 1.0 if no claims
    verdicts: List[ClaimVerdict] = field(default_factory=list)

    @property
    def is_grounded(self) -> bool:
        """True iff no claim is contradicted or unfounded — ready to ship."""
        return self.n_contradicted == 0 and self.n_not_found == 0


# --------------------------------------------------------------------------- #
# Verifier
# --------------------------------------------------------------------------- #
class Verifier:
    """Two-step LLM-driven verifier."""

    def __init__(self, llm):
        self.llm = llm

    # ------------------------------------------------------------------ #
    # Step 1: claim extraction
    # ------------------------------------------------------------------ #
    def extract_claims(self, answer: str, max_claims: int = 8) -> List[str]:
        """Break an answer into atomic factual claims."""
        if not answer.strip():
            return []

        system = (
            "You extract atomic factual claims from text. A claim is a single, "
            "self-contained statement that could be true or false. "
            "Decompose the input into the smallest possible claims; if a sentence "
            "contains two facts joined by 'and', split them. Skip greetings, "
            "hedges ('I think'), and citation markers like [1]. "
            f"Return at most {max_claims} claims as a JSON array of strings only. "
            "No prose, no code fences."
        )
        user = f"Text:\n{answer}\n\nClaims:"
        raw = self.llm.instruct(system, user, max_new_tokens=300)
        return _parse_claim_list(raw)[:max_claims]

    # ------------------------------------------------------------------ #
    # Step 2: per-claim verification
    # ------------------------------------------------------------------ #
    def verify_claim(self, claim: str, evidence: str) -> ClaimVerdict:
        """Decide whether evidence supports a single claim."""
        system = (
            "You are a strict fact-verifier. Given a CLAIM and EVIDENCE, decide:\n"
            "  SUPPORTED    — the evidence directly states or clearly implies the claim.\n"
            "  CONTRADICTED — the evidence states the opposite of the claim.\n"
            "  NOT_FOUND    — the evidence does not address the claim either way.\n"
            "Be strict: a claim is SUPPORTED only when the evidence really says it. "
            "If the claim is more specific than the evidence, prefer NOT_FOUND. "
            "Reply with ONE JSON object only: "
            '{"label": "SUPPORTED|CONTRADICTED|NOT_FOUND", "rationale": "..."}.'
        )
        user = f"CLAIM:\n{claim}\n\nEVIDENCE:\n{evidence}\n\nJSON:"
        raw = self.llm.instruct(system, user, max_new_tokens=120)
        return _parse_verdict(claim, raw)

    # ------------------------------------------------------------------ #
    # Combined: extract + verify
    # ------------------------------------------------------------------ #
    def verify(self, answer: str, evidence: str) -> FaithfulnessResult:
        claims = self.extract_claims(answer)
        if not claims:
            # No claims to verify — vacuously faithful, but nothing to ship either
            return FaithfulnessResult(
                n_claims=0, n_supported=0, n_contradicted=0, n_not_found=0,
                score=1.0, verdicts=[],
            )

        verdicts = [self.verify_claim(c, evidence) for c in claims]
        n_sup  = sum(v.label == "SUPPORTED"    for v in verdicts)
        n_con  = sum(v.label == "CONTRADICTED" for v in verdicts)
        n_nf   = sum(v.label == "NOT_FOUND"    for v in verdicts)
        n      = len(verdicts)
        return FaithfulnessResult(
            n_claims=n,
            n_supported=n_sup,
            n_contradicted=n_con,
            n_not_found=n_nf,
            score=n_sup / n if n else 1.0,
            verdicts=verdicts,
        )


# --------------------------------------------------------------------------- #
# Robust parsing
# --------------------------------------------------------------------------- #
def _strip_fences(raw: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE).strip()


def _parse_claim_list(raw: str) -> List[str]:
    """Extract a JSON array of strings from the LLM output."""
    raw = _strip_fences(raw)
    # Locate the first [...] block
    start = raw.find("[")
    end = raw.rfind("]")
    if start != -1 and end > start:
        try:
            arr = json.loads(raw[start:end + 1])
            return [str(x).strip() for x in arr if isinstance(x, (str, int, float)) and str(x).strip()]
        except json.JSONDecodeError:
            pass
    # Fallback: numbered or bulleted list lines
    claims: List[str] = []
    for line in raw.splitlines():
        m = re.match(r"\s*(?:\d+[.\)]|[-*])\s*(.+)", line)
        if m:
            text = m.group(1).strip().strip('"')
            if text:
                claims.append(text)
    return claims


_VALID_LABELS = {"SUPPORTED", "CONTRADICTED", "NOT_FOUND"}


def _parse_verdict(claim: str, raw: str) -> ClaimVerdict:
    raw = _strip_fences(raw)
    # Try JSON first
    start = raw.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(raw[start:], start=start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    blob = raw[start:i + 1]
                    try:
                        d = json.loads(blob)
                        label = str(d.get("label", "")).upper().replace(" ", "_")
                        if label in _VALID_LABELS:
                            return ClaimVerdict(
                                claim=claim, label=label,
                                rationale=str(d.get("rationale", ""))[:400],
                            )
                    except json.JSONDecodeError:
                        break
    # Regex fallback
    m = re.search(r"\b(SUPPORTED|CONTRADICTED|NOT[ _]FOUND)\b", raw, re.IGNORECASE)
    if m:
        label = m.group(1).upper().replace(" ", "_")
        return ClaimVerdict(claim=claim, label=label, rationale=raw[:200])
    # If nothing parseable, treat as NOT_FOUND so we don't auto-ship
    return ClaimVerdict(claim=claim, label="NOT_FOUND",
                        rationale="(verifier output unparseable)")
