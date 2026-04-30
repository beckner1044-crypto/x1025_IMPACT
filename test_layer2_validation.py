"""Tests for Layer 2 tool-contract validation (x1025/layer2.py)."""
import pytest

# We import only the validator + tool functions; the OpsAgent itself depends
# on torch via x1025.core, so we don't construct an agent here.
from x1025.layer2 import (
    validate_tool_args, ToolValidationError,
    tool_get_vessel_eta, tool_get_fuel_rob, tool_get_speed_performance,
    tool_get_certificates_expiring, tool_list_vessels,
)


# --- Validator ------------------------------------------------------------ #
def test_valid_args_pass_through():
    assert validate_tool_args("get_vessel_eta", {"vessel": "Boreas"}) == {"vessel": "Boreas"}


def test_default_value_applied_when_optional_arg_missing():
    out = validate_tool_args("get_speed_performance", {"vessel": "Boreas"})
    assert out == {"vessel": "Boreas", "days": 14}


def test_string_int_coercion_for_common_llm_mistake():
    out = validate_tool_args("get_speed_performance", {"vessel": "Boreas", "days": "30"})
    assert out["days"] == 30


def test_missing_required_arg_raises():
    with pytest.raises(ToolValidationError, match="missing required"):
        validate_tool_args("get_vessel_eta", {})


def test_unexpected_arg_rejected():
    with pytest.raises(ToolValidationError, match="unexpected"):
        validate_tool_args("get_vessel_eta", {"vessel": "Boreas", "made_up": "field"})


def test_int_out_of_range_rejected():
    with pytest.raises(ToolValidationError, match="max"):
        validate_tool_args("get_speed_performance", {"vessel": "Boreas", "days": 9999})
    with pytest.raises(ToolValidationError, match="min"):
        validate_tool_args("get_speed_performance", {"vessel": "Boreas", "days": 0})


def test_string_too_short_rejected():
    with pytest.raises(ToolValidationError, match="too short"):
        validate_tool_args("get_vessel_eta", {"vessel": "B"})


def test_uncoercible_int_rejected():
    with pytest.raises(ToolValidationError, match="expected int"):
        validate_tool_args("get_speed_performance", {"vessel": "Boreas", "days": "fourteen"})


def test_unknown_tool_rejected():
    with pytest.raises(ToolValidationError, match="unknown tool"):
        validate_tool_args("does_not_exist", {})


def test_no_args_tool_accepts_empty():
    assert validate_tool_args("list_vessels", {}) == {}


# --- Tool functions against the mock DB ----------------------------------- #
def test_get_vessel_eta_returns_destination(tmp_db):
    out = tool_get_vessel_eta(tmp_db, "Boreas")
    assert out["destination_port"] == "Singapore"
    assert out["eta"] == "2026-05-10"


def test_get_vessel_eta_unknown_returns_error(tmp_db):
    out = tool_get_vessel_eta(tmp_db, "MV Imaginary")
    assert "error" in out


def test_get_fuel_rob(tmp_db):
    out = tool_get_fuel_rob(tmp_db, "Aurora")
    assert out["fuel_rob_hfo_mt"] == 1500.0
    assert out["fuel_rob_mgo_mt"] == 240.0


def test_speed_performance_underperformer(tmp_db):
    out = tool_get_speed_performance(tmp_db, "Boreas", days=30)
    # The fixture has Boreas at 11.72 vs CP 12.5
    assert out["speed_diff_kn"] < 0
    assert "underperforming" in out["verdict"]


def test_certificates_expiring_within_30_days(tmp_db):
    out = tool_get_certificates_expiring(tmp_db, within_days=30)
    cert_types = {c["cert_type"] for c in out["certificates"]}
    assert "Safety Radio" in cert_types       # already expired
    assert "Safety Equipment" in cert_types   # imminent
    assert "IOPP" not in cert_types           # ~3 years out


def test_list_vessels_returns_all(tmp_db):
    out = tool_list_vessels(tmp_db)
    assert out["count"] == 3
    names = {v["name"] for v in out["vessels"]}
    assert names == {"MV Aurora", "MV Boreas", "MV Cassini"}
