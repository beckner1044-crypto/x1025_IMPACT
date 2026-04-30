"""
audit.py
Append-only audit trail for every answer the assistant returns.

Why this exists:
  - Maritime ops want a defensible trail of "what was asked, what we said,
    what evidence backed it." This is operationally useful for the monthly
    review the IMPACT proposal mentions, and is the right shape for any
    eventual regulatory conversation.
  - The verifier already produces per-claim labels at runtime (when
    verify=True). The audit log persists those alongside the answer, so a
    reviewer can see *which* claim was unsupported when an answer is
    flagged.

Why JSONL on disk (not SQLite for the audit log itself):
  - Append-only is the property that matters for trust. JSONL is
    intrinsically append-only — each line is a complete record, no
    transactions, no risk of mid-write corruption invalidating prior
    entries. SQLite would also work but adds a write lock that slows
    concurrent writes.
  - Feedback (thumbs up/down) DOES go to SQLite — that table is naturally
    relational (joining feedback to the audit entry by ID), and we want
    indexed queries for the "show me thumbs-down answers from last month"
    workflow.

Redaction is applied at the persistence boundary. The user sees the
original answer; the audit log sees a redacted version. If we ever ship
audit logs off-vessel, identifiers stay onboard.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .redact import Redactor


_LOCK = threading.Lock()


@dataclass
class AuditConfig:
    path: str = "./logs/audit.jsonl"


class AuditLogger:
    """Append-only audit log writer.

    Entries include a stable UUID so the feedback table can foreign-key to
    the audit row that prompted the feedback.
    """

    def __init__(self, redactor: Redactor, config: Optional[AuditConfig] = None):
        self.redactor = redactor
        self.config = config or AuditConfig()
        os.makedirs(os.path.dirname(self.config.path) or ".", exist_ok=True)

    # ------------------------------------------------------------------ #
    def log(self, *,
            question: str,
            answer: str,
            route: str,
            confidence: Optional[float] = None,
            low_confidence: bool = False,
            tool: Optional[str] = None,
            tool_args: Optional[Dict[str, Any]] = None,
            sources: Optional[list] = None,
            verifier: Optional[Dict[str, Any]] = None,
            extra: Optional[Dict[str, Any]] = None) -> str:
        """Write one audit row. Returns the row's UUID for foreign-keying."""
        row_id = str(uuid.uuid4())
        entry = {
            "id":              row_id,
            "ts":              datetime.now(timezone.utc).isoformat(),
            "route":           route,
            "question":        self.redactor.redact(question),
            "answer":          self.redactor.redact(answer),
            "confidence":      confidence,
            "low_confidence":  bool(low_confidence),
            "tool":            tool,
            "tool_args":       tool_args,
            "sources":         sources,
            "verifier":        _redact_verifier(self.redactor, verifier),
            "extra":           extra,
        }
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with _LOCK:
            with open(self.config.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return row_id


def _redact_verifier(redactor: Redactor, verifier: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Redact the claim text inside verifier verdicts before persistence."""
    if not verifier:
        return verifier
    out = dict(verifier)
    if "verdicts" in verifier and isinstance(verifier["verdicts"], list):
        out["verdicts"] = [
            {
                "claim":     redactor.redact(v.get("claim", "")),
                "label":     v.get("label"),
                "rationale": redactor.redact(v.get("rationale", "")),
            }
            for v in verifier["verdicts"]
        ]
    return out
