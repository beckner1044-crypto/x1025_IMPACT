"""Tests for x1025/audit.py and x1025/feedback.py."""
import json

from x1025.audit import AuditLogger, AuditConfig
from x1025.feedback import FeedbackStore
from x1025.redact import Redactor


def test_audit_log_writes_jsonl_row(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    logger = AuditLogger(Redactor(), AuditConfig(path=str(audit_path)))

    audit_id = logger.log(
        question="What is X?",
        answer="X is Y.",
        route="procedural",
        confidence=0.85,
    )

    assert audit_id  # UUID returned
    assert audit_path.exists()
    rows = audit_path.read_text().strip().split("\n")
    assert len(rows) == 1
    entry = json.loads(rows[0])
    assert entry["id"] == audit_id
    assert entry["route"] == "procedural"
    assert entry["confidence"] == 0.85


def test_audit_log_is_append_only(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    logger = AuditLogger(Redactor(), AuditConfig(path=str(audit_path)))
    for i in range(3):
        logger.log(question=f"q{i}", answer=f"a{i}", route="procedural")
    assert len(audit_path.read_text().strip().split("\n")) == 3


def test_audit_log_redacts_vessel_names(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    redactor = Redactor(vessel_names=["MV Boreas"])
    logger = AuditLogger(redactor, AuditConfig(path=str(audit_path)))

    logger.log(
        question="What is the ETA for MV Boreas?",
        answer="MV Boreas arrives in Singapore on 2026-05-10.",
        route="operational",
    )
    entry = json.loads(audit_path.read_text().strip())
    assert "[VESSEL]" in entry["question"]
    assert "[VESSEL]" in entry["answer"]
    assert "Boreas" not in entry["question"]
    assert "Boreas" not in entry["answer"]


def test_audit_log_redacts_verifier_verdicts(tmp_path):
    """Bug-shaped: claim text inside verdicts must also be redacted."""
    audit_path = tmp_path / "audit.jsonl"
    redactor = Redactor(vessel_names=["MV Boreas"])
    logger = AuditLogger(redactor, AuditConfig(path=str(audit_path)))

    logger.log(
        question="status?", answer="ok", route="operational",
        verifier={
            "score": 1.0, "n_claims": 1, "n_supported": 1,
            "n_contradicted": 0, "n_not_found": 0,
            "verdicts": [{
                "claim": "MV Boreas is on schedule",
                "label": "SUPPORTED",
                "rationale": "matches Boreas data",
            }],
        },
    )
    entry = json.loads(audit_path.read_text().strip())
    verdicts = entry["verifier"]["verdicts"]
    assert "Boreas" not in verdicts[0]["claim"]
    assert "Boreas" not in verdicts[0]["rationale"]


# --- Feedback ------------------------------------------------------------- #
def test_feedback_thumbs_up_and_down(tmp_path):
    fb = FeedbackStore(db_path=str(tmp_path / "fb.db"))
    fb.add("audit-id-1", "up")
    fb.add("audit-id-2", "down", note="incomplete")

    stats = fb.stats()
    assert stats == {"up": 1, "down": 1, "total": 2}


def test_feedback_rejects_invalid_thumb(tmp_path):
    import pytest
    fb = FeedbackStore(db_path=str(tmp_path / "fb.db"))
    with pytest.raises(ValueError):
        fb.add("audit-id-1", "sideways")


def test_feedback_lists_thumbs_down_only(tmp_path):
    fb = FeedbackStore(db_path=str(tmp_path / "fb.db"))
    fb.add("a1", "up")
    fb.add("a2", "down", note="wrong source")
    fb.add("a3", "up")
    fb.add("a4", "down")
    rows = fb.list_thumbs_down()
    assert len(rows) == 2
    assert all(r["thumb"] == "down" for r in rows)
