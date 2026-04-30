"""
router.py
Query router. Classifies an incoming question into one of:

  procedural  -> Layer 1 (SMS RAG over ISM documents)
  operational -> Layer 2 (live SQL against x1025 system of record)
  both        -> run both, then synthesize
  none        -> politely refuse / clarify

The classifier is the same Mistral model, prompted to return a single token.
A small heuristic fallback covers edge cases where the model returns prose.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .core import LLM


_VALID = {"procedural", "operational", "both", "none"}


# Quick heuristics — used only if the LLM output can't be parsed cleanly.
_OPS_HINTS = re.compile(
    r"\b(eta|rob|fuel|speed|consum|charter\s*part|cp\b|noon\s*report|"
    r"position|knots?|mt/day|certificate.*(expir|valid)|fleet|vessel\s+\w+|"
    r"imo\s*\d+)\b",
    re.I,
)
_PROC_HINTS = re.compile(
    r"\b(procedure|protocol|how\s+do\s+i|how\s+to|sms|ism|emergency|"
    r"checklist|man\s*overboard|fire|safety|abandon|drill)\b",
    re.I,
)


class QueryRouter:
    def __init__(self, llm: LLM):
        self.llm = llm

    def classify(self, question: str) -> str:
        system = (
            "You route maritime assistant questions to one of two layers.\n"
            "  procedural  = questions about Safety Management System procedures, ISM documents, "
            "checklists, emergency protocols, or how to perform a task safely.\n"
            "  operational = questions about live vessel data: ETAs, fuel ROB, speed vs charter "
            "party, certificate expiries, fleet status, specific vessel names or IMOs.\n"
            "  both        = the question genuinely needs both kinds of information.\n"
            "  none        = neither applies.\n"
            "Reply with EXACTLY one of: procedural, operational, both, none. "
            "No explanation, no punctuation, no other words."
        )
        user = f"Question: {question}\n\nAnswer:"
        raw = self.llm.instruct(system, user, max_new_tokens=6).strip().lower()

        # Pick out the first matching label even if the model added prose
        for label in _VALID:
            if re.search(rf"\b{label}\b", raw):
                return label

        # Heuristic fallback
        ops = bool(_OPS_HINTS.search(question))
        proc = bool(_PROC_HINTS.search(question))
        if ops and proc:
            return "both"
        if ops:
            return "operational"
        if proc:
            return "procedural"
        return "none"
