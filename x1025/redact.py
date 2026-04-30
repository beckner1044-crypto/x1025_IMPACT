"""
redact.py
PII / sensitive-data redaction before persistence.

Maritime queries routinely include vessel names, IMO numbers, crew names,
positions, charterer details. When we persist queries to an audit log or a
feedback table, we want any data that leaves the chatbot's context to be
scrubbed of identifiers.

Two design decisions worth flagging:

1. Vessel names use a *registry-driven* matcher rather than a pattern like
   ``\\b(MV|M/V|MS)\\s+[A-Z][a-z]+\\b``. The pattern approach has false
   positives ("MS Office", "MV File") and false negatives ("Aurora" without
   the prefix). Loading the actual fleet from the SQLite mock and matching
   word-by-word avoids both failure modes.

2. We never redact in place on the live chat output. Redaction runs only on
   the path *to disk* (audit log, feedback table). The user sees their
   original query and answer; persistence sees the redacted versions.

Patterns are intentionally narrow. False positives in redaction are worse
than false negatives in some ways — if "MS Office" gets redacted to
"[VESSEL]" in the audit log, the log becomes useless for debugging.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Iterable, List, Optional


# --------------------------------------------------------------------------- #
# Patterns for the universally-sensitive identifiers
# --------------------------------------------------------------------------- #
# IMO numbers: 7 digits, optionally prefixed with "IMO". Real IMO numbers
# follow ISO 6346 (7 digits). We require word boundaries to avoid matching
# inside arbitrary numbers.
IMO_RE = re.compile(r"\b(?:IMO[\s:#]*)?(\d{7})\b", re.IGNORECASE)

# Decimal-degree coordinates: handles 12.345N, 12.345 N, -12.345, with
# optional second component. Designed conservatively — only matches when both
# the latitude and longitude appear close together.
COORD_RE = re.compile(
    r"\b"
    r"-?\d{1,2}\.\d{2,6}\s*[°]?\s*[NS]?"      # latitude
    r"\s*[, ]\s*"
    r"-?\d{1,3}\.\d{2,6}\s*[°]?\s*[EW]?"      # longitude
    r"\b",
    re.IGNORECASE,
)

# Email addresses
EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

# E.164-ish phone numbers (international format with separators)
PHONE_RE = re.compile(
    r"(?:(?<=\s)|(?<=^)|(?<=\())"
    r"\+?\d{1,3}[\s.-]?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}"
)


# --------------------------------------------------------------------------- #
# Redactor
# --------------------------------------------------------------------------- #
class Redactor:
    """Redacts PII / fleet-identifying data before persistence.

    Vessel names are matched against an explicit list (loaded from the
    fleet registry). This avoids the false-positives a generic pattern
    creates and gives perfect recall on the actual fleet.
    """

    def __init__(self, vessel_names: Iterable[str] = ()):
        # Build a deduplicated set of name forms to match. For every entry
        # like "MV Aurora", we also accept the bare form "Aurora", since
        # operators frequently drop the prefix in conversation.
        forms: set[str] = set()
        for n in vessel_names:
            n = (n or "").strip()
            if not n:
                continue
            forms.add(n)
            # Strip a leading MV/MS/MT prefix to add the bare form
            bare = re.sub(r"^M[VST]\s+", "", n, flags=re.IGNORECASE).strip()
            if bare and bare != n and len(bare) >= 3:
                forms.add(bare)

        names = sorted(forms, key=len, reverse=True)  # longest first
        if names:
            joined = "|".join(re.escape(n) for n in names)
            self.vessel_re: Optional[re.Pattern] = re.compile(
                rf"\b(?:M[VST]\s+)?(?:{joined})\b",
                re.IGNORECASE,
            )
        else:
            self.vessel_re = None

    def redact(self, text: Optional[str]) -> str:
        """Apply all redactions in order. Empty/None input passes through."""
        if not text:
            return ""
        out = text
        if self.vessel_re is not None:
            out = self.vessel_re.sub("[VESSEL]", out)
        out = IMO_RE.sub(lambda m: f"[IMO]" if _looks_like_imo(m, out) else m.group(0), out)
        out = COORD_RE.sub("[COORDINATES]", out)
        out = EMAIL_RE.sub("[EMAIL]", out)
        out = PHONE_RE.sub("[PHONE]", out)
        return out


def _looks_like_imo(match: re.Match, full_text: str) -> bool:
    """A bare 7-digit number is only treated as an IMO if it appears in IMO-
    related context. Avoids redacting things like distances, fuel quantities,
    ZIP codes that happen to be 7 digits long."""
    matched = match.group(0)
    if matched.lower().lstrip().startswith("imo"):
        return True
    # Look at the 30-char window before the match for IMO context
    start = max(0, match.start() - 30)
    window = full_text[start:match.start()].lower()
    return "imo" in window or "vessel" in window or "ship" in window


# --------------------------------------------------------------------------- #
# Convenience: build a redactor from the same SQLite mock the chatbot uses
# --------------------------------------------------------------------------- #
def redactor_from_db(db_path: str) -> Redactor:
    """Load the vessel registry from the x1025 mock DB and build a redactor."""
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute("SELECT name FROM vessels").fetchall()
        names = [r[0] for r in rows]
    except sqlite3.Error:
        # DB not present yet (e.g. setup_data.py hasn't run); fall back to
        # patterns-only redaction.
        names = []
    return Redactor(vessel_names=names)
