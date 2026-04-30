"""Tests for x1025/redact.py — registry-driven PII redaction."""
from x1025.redact import Redactor, redactor_from_db


def test_redacts_vessel_names_in_registry():
    r = Redactor(vessel_names=["MV Boreas", "MV Aurora"])
    assert r.redact("ETA for MV Boreas") == "ETA for [VESSEL]"


def test_redacts_bare_vessel_name_without_prefix():
    r = Redactor(vessel_names=["MV Aurora"])
    assert "[VESSEL]" in r.redact("Aurora is approaching Rotterdam")


def test_redacts_multiple_vessels():
    r = Redactor(vessel_names=["MV Cassini", "MV Equinox"])
    out = r.redact("Cassini and Equinox both at sea")
    assert out == "[VESSEL] and [VESSEL] both at sea"


def test_redacts_imo_with_explicit_prefix():
    r = Redactor()
    assert r.redact("Vessel IMO 9456789 reported a leak") == "Vessel [IMO] reported a leak"


def test_redacts_decimal_coordinates():
    r = Redactor()
    assert "[COORDINATES]" in r.redact("Position: 51.5074N, -0.1278W")


def test_redacts_email_addresses():
    r = Redactor()
    assert r.redact("Email captain@vessel.com") == "Email [EMAIL]"


# --- False-positive guards ------------------------------------------------ #
# The pattern-only redactors that other LLM reviewers proposed had FPs on
# these. Our registry-driven approach must not.

def test_does_not_redact_ms_office():
    r = Redactor(vessel_names=["MV Boreas"])
    assert r.redact("Open the file in MS Office") == "Open the file in MS Office"


def test_does_not_redact_seven_digit_number_without_imo_context():
    r = Redactor()
    assert r.redact("The tank holds 1234567 liters") == "The tank holds 1234567 liters"


def test_does_not_redact_aurora_inside_other_word():
    r = Redactor(vessel_names=["MV Aurora"])
    assert r.redact("the auroral display was beautiful") == "the auroral display was beautiful"


def test_handles_empty_input():
    r = Redactor(vessel_names=["MV Boreas"])
    assert r.redact("") == ""
    assert r.redact(None) == ""


# --- DB-driven redactor --------------------------------------------------- #
def test_redactor_from_db(tmp_db):
    r = redactor_from_db(tmp_db)
    # tmp_db has Aurora, Boreas, Cassini
    assert "[VESSEL]" in r.redact("MV Boreas eta")
    assert "[VESSEL]" in r.redact("Cassini's certificate")


def test_redactor_from_db_missing_db_falls_back_to_patterns(tmp_path):
    """Bug-shaped: redaction should still partially work without a DB."""
    nonexistent = tmp_path / "nope.db"
    r = redactor_from_db(str(nonexistent))
    # Patterns still work; vessel names from registry don't (no registry)
    assert r.redact("IMO 9456789") == "[IMO]"
