"""
layer2.py
Layer 2 — Live operational queries against the x1025 system of record.

We expose a small, fixed set of tools (Python functions wrapping SQL).
The LLM picks which tool to call and extracts its arguments by emitting
a JSON object. We execute the tool, then ask the LLM to phrase the
result as a natural-language answer.

This is safer than free-form text-to-SQL for a prototype:
  - no risk of the model writing destructive SQL
  - deterministic for the routes we care about
  - easy to extend by adding a new tool entry

Tool contracts: each tool entry in TOOLS includes a `schema` dict that
declares argument names, types, required/optional, and value ranges.
The OpsAgent validates LLM-emitted args against this schema before
executing the tool, so malformed output from the model fails fast with
a clear error rather than blowing up inside the SQL function.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .core import LLM


# --------------------------------------------------------------------------- #
# Tool implementations (pure functions, easy to unit-test)
# --------------------------------------------------------------------------- #
def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _resolve_vessel(conn, vessel: str) -> Optional[sqlite3.Row]:
    cur = conn.execute(
        "SELECT * FROM vessels WHERE LOWER(name)=LOWER(?) OR imo=?",
        (vessel, vessel),
    )
    row = cur.fetchone()
    if row:
        return row
    # Fall back to LIKE matching on name
    cur = conn.execute(
        "SELECT * FROM vessels WHERE LOWER(name) LIKE LOWER(?)",
        (f"%{vessel}%",),
    )
    return cur.fetchone()


def tool_get_vessel_eta(db_path: str, vessel: str) -> Dict[str, Any]:
    with _connect(db_path) as conn:
        v = _resolve_vessel(conn, vessel)
        if not v:
            return {"error": f"vessel '{vessel}' not found"}
        cur = conn.execute(
            "SELECT report_date, destination_port, eta, position_lat, position_lon "
            "FROM daily_reports WHERE vessel_id=? ORDER BY report_date DESC LIMIT 1",
            (v["id"],),
        )
        r = cur.fetchone()
        if not r:
            return {"error": f"no reports for {v['name']}"}
        return {
            "vessel": v["name"], "imo": v["imo"],
            "destination_port": r["destination_port"],
            "eta": r["eta"],
            "last_position": [r["position_lat"], r["position_lon"]],
            "as_of": r["report_date"],
        }


def tool_get_fuel_rob(db_path: str, vessel: str) -> Dict[str, Any]:
    with _connect(db_path) as conn:
        v = _resolve_vessel(conn, vessel)
        if not v:
            return {"error": f"vessel '{vessel}' not found"}
        cur = conn.execute(
            "SELECT report_date, fuel_rob_hfo_mt, fuel_rob_mgo_mt, "
            "fuel_consumption_24h_mt FROM daily_reports "
            "WHERE vessel_id=? ORDER BY report_date DESC LIMIT 1",
            (v["id"],),
        )
        r = cur.fetchone()
        if not r:
            return {"error": f"no reports for {v['name']}"}
        return {
            "vessel": v["name"],
            "as_of": r["report_date"],
            "fuel_rob_hfo_mt": r["fuel_rob_hfo_mt"],
            "fuel_rob_mgo_mt": r["fuel_rob_mgo_mt"],
            "consumption_last_24h_mt": r["fuel_consumption_24h_mt"],
        }


def tool_get_speed_performance(db_path: str, vessel: str, days: int = 14) -> Dict[str, Any]:
    """Compare actual speed and fuel consumption to charter party warranties."""
    with _connect(db_path) as conn:
        v = _resolve_vessel(conn, vessel)
        if not v:
            return {"error": f"vessel '{vessel}' not found"}
        cur = conn.execute(
            "SELECT AVG(avg_speed_kn) AS avg_speed, "
            "       AVG(fuel_consumption_24h_mt) AS avg_cons, "
            "       COUNT(*) AS n "
            "FROM daily_reports "
            "WHERE vessel_id=? AND report_date >= date('now', ?)",
            (v["id"], f"-{int(days)} day"),
        )
        r = cur.fetchone()
        if not r or r["n"] == 0:
            return {"error": f"no reports in last {days} days for {v['name']}"}
        speed_diff = r["avg_speed"] - v["cp_speed_kn"]
        cons_diff = r["avg_cons"] - v["cp_consumption_mt"]
        verdict = "compliant"
        if speed_diff < -0.5 or cons_diff > 2.0:
            verdict = "underperforming vs charter party"
        elif speed_diff > 0.3 and cons_diff < 0:
            verdict = "outperforming charter party"
        return {
            "vessel": v["name"],
            "window_days": days,
            "avg_speed_kn": round(r["avg_speed"], 2),
            "cp_speed_kn": v["cp_speed_kn"],
            "speed_diff_kn": round(speed_diff, 2),
            "avg_consumption_mt_day": round(r["avg_cons"], 2),
            "cp_consumption_mt_day": v["cp_consumption_mt"],
            "consumption_diff_mt_day": round(cons_diff, 2),
            "verdict": verdict,
        }


def tool_get_certificates_expiring(db_path: str, within_days: int = 90,
                                    vessel: Optional[str] = None) -> Dict[str, Any]:
    with _connect(db_path) as conn:
        params: List[Any] = [f"+{int(within_days)} day"]
        sql = ("SELECT v.name AS vessel, v.imo, c.cert_type, c.expiry_date "
               "FROM certificates c JOIN vessels v ON v.id = c.vessel_id "
               "WHERE c.expiry_date <= date('now', ?) ")
        if vessel:
            v = _resolve_vessel(conn, vessel)
            if not v:
                return {"error": f"vessel '{vessel}' not found"}
            sql += "AND c.vessel_id = ? "
            params.append(v["id"])
        sql += "ORDER BY c.expiry_date ASC"
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        today = date.today()
        for r in rows:
            r["days_to_expiry"] = (date.fromisoformat(r["expiry_date"]) - today).days
        return {"within_days": within_days, "count": len(rows), "certificates": rows}


def tool_list_vessels(db_path: str) -> Dict[str, Any]:
    with _connect(db_path) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT name, imo, vessel_type, dwt, cp_speed_kn, cp_consumption_mt FROM vessels"
        ).fetchall()]
        return {"count": len(rows), "vessels": rows}


# --------------------------------------------------------------------------- #
# Tool registry: spec the LLM sees + the function we call
# --------------------------------------------------------------------------- #
TOOLS: Dict[str, Dict[str, Any]] = {
    "get_vessel_eta": {
        "description": "Get the latest ETA, destination port, and last reported position for a vessel.",
        "args": {"vessel": "vessel name or IMO number (string, required)"},
        "schema": {
            "vessel": {"type": str, "required": True, "min_len": 2, "max_len": 60},
        },
        "fn": tool_get_vessel_eta,
    },
    "get_fuel_rob": {
        "description": "Get the latest fuel Remaining On Board (HFO and MGO) and last 24h consumption.",
        "args": {"vessel": "vessel name or IMO number (string, required)"},
        "schema": {
            "vessel": {"type": str, "required": True, "min_len": 2, "max_len": 60},
        },
        "fn": tool_get_fuel_rob,
    },
    "get_speed_performance": {
        "description": "Compare a vessel's actual speed and consumption against its charter party warranty over the last N days.",
        "args": {
            "vessel": "vessel name or IMO number (string, required)",
            "days":   "lookback window in days (integer, optional, default 14)",
        },
        "schema": {
            "vessel": {"type": str, "required": True, "min_len": 2, "max_len": 60},
            "days":   {"type": int, "required": False, "min": 1, "max": 365, "default": 14},
        },
        "fn": tool_get_speed_performance,
    },
    "get_certificates_expiring": {
        "description": "List statutory certificates expiring within the next N days, optionally filtered to one vessel.",
        "args": {
            "within_days": "lookahead window in days (integer, optional, default 90)",
            "vessel":      "vessel name or IMO to filter by (string, optional)",
        },
        "schema": {
            "within_days": {"type": int, "required": False, "min": 1, "max": 1825, "default": 90},
            "vessel":      {"type": str, "required": False, "min_len": 2, "max_len": 60},
        },
        "fn": tool_get_certificates_expiring,
    },
    "list_vessels": {
        "description": "List all vessels in the fleet with their type, DWT, and charter party terms.",
        "args": {},
        "schema": {},
        "fn": tool_list_vessels,
    },
}


class ToolValidationError(ValueError):
    """Raised when LLM-emitted tool arguments fail the tool's schema."""


def validate_tool_args(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce, default, and validate args for a tool. Returns a cleaned dict
    safe to splat into the tool function. Raises ToolValidationError on
    anything the schema can't accept."""
    if tool_name not in TOOLS:
        raise ToolValidationError(f"unknown tool {tool_name!r}")
    schema = TOOLS[tool_name].get("schema", {})

    # Reject unexpected keys outright — the LLM occasionally invents them
    extra = set(args) - set(schema)
    if extra:
        raise ToolValidationError(
            f"tool {tool_name!r} got unexpected args: {sorted(extra)}"
        )

    cleaned: Dict[str, Any] = {}
    for name, rule in schema.items():
        present = name in args and args[name] not in (None, "", [])
        if not present:
            if rule.get("required"):
                raise ToolValidationError(f"tool {tool_name!r} missing required arg {name!r}")
            if "default" in rule:
                cleaned[name] = rule["default"]
            continue

        value = args[name]
        expected = rule["type"]

        # Coerce common LLM mistakes: numeric strings → int, ints → str when needed
        if expected is int and isinstance(value, str):
            try:
                value = int(value.strip())
            except ValueError as e:
                raise ToolValidationError(
                    f"tool {tool_name!r} arg {name!r} expected int, got {args[name]!r}"
                ) from e
        elif expected is str and not isinstance(value, str):
            value = str(value)

        if not isinstance(value, expected):
            raise ToolValidationError(
                f"tool {tool_name!r} arg {name!r} expected {expected.__name__}, "
                f"got {type(value).__name__}"
            )

        # Range checks
        if expected is int:
            lo, hi = rule.get("min"), rule.get("max")
            if lo is not None and value < lo:
                raise ToolValidationError(f"tool {tool_name!r} arg {name!r}={value} < min {lo}")
            if hi is not None and value > hi:
                raise ToolValidationError(f"tool {tool_name!r} arg {name!r}={value} > max {hi}")
        elif expected is str:
            lo, hi = rule.get("min_len"), rule.get("max_len")
            if lo is not None and len(value) < lo:
                raise ToolValidationError(f"tool {tool_name!r} arg {name!r} too short")
            if hi is not None and len(value) > hi:
                raise ToolValidationError(f"tool {tool_name!r} arg {name!r} too long")

        cleaned[name] = value
    return cleaned


def _tools_spec_text() -> str:
    lines = []
    for name, t in TOOLS.items():
        args = ", ".join(f"{k}: {v}" for k, v in t["args"].items()) or "(no args)"
        lines.append(f"- {name}({args})\n    {t['description']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Layer 2 chatbot
# --------------------------------------------------------------------------- #
class OpsAgent:
    """LLM-driven tool dispatch over the x1025 SQLite database."""

    def __init__(self, llm: LLM, db_path: str):
        self.llm = llm
        self.db_path = db_path

    # ----- step 1: pick a tool ----------------------------------------------
    def _select_tool(self, question: str) -> dict:
        system = (
            "You are a routing layer for a maritime operations assistant. "
            "Given a user question, choose ONE tool from the list and extract its arguments. "
            "Reply with a SINGLE JSON object only, no prose, no code fences. "
            "Schema: {\"tool\": \"<name>\", \"args\": {...}}. "
            "If no tool fits, reply {\"tool\": \"none\", \"args\": {}}."
        )
        user = f"Available tools:\n{_tools_spec_text()}\n\nUser question: {question}\n\nJSON:"
        raw = self.llm.instruct(system, user, max_new_tokens=120)
        return _parse_tool_call(raw)

    # ----- step 2: phrase the result ----------------------------------------
    def _phrase_result(self, question: str, tool_name: str, result: Any) -> str:
        system = (
            "You are a maritime operations assistant. The user asked a question and a tool "
            "has been executed against the company database. Use ONLY the tool result to "
            "answer in clear, concise English suitable for a Master or Superintendent. "
            "Do not invent fields. If the result contains an 'error', say so plainly."
        )
        user = (
            f"Question: {question}\n"
            f"Tool called: {tool_name}\n"
            f"Tool result (JSON):\n{json.dumps(result, indent=2, default=str)}\n\n"
            "Answer:"
        )
        return self.llm.instruct(system, user, max_new_tokens=250)

    # ----- public -----------------------------------------------------------
    def answer(self, question: str) -> dict:
        call = self._select_tool(question)
        tool_name = call.get("tool")
        raw_args = call.get("args", {}) or {}

        if tool_name == "none" or tool_name not in TOOLS:
            return {
                "answer": ("I couldn't map that to an operational query. Try asking about a "
                           "specific vessel's ETA, fuel ROB, charter-party performance, or "
                           "certificate expiries."),
                "tool": tool_name,
                "args": raw_args,
                "result": None,
            }

        # Validate and coerce LLM-emitted args before touching the DB
        try:
            args = validate_tool_args(tool_name, raw_args)
        except ToolValidationError as e:
            return {
                "answer": f"The tool '{tool_name}' was called with invalid arguments: {e}. "
                          "Could you rephrase your question?",
                "tool": tool_name, "args": raw_args, "result": None,
                "validation_error": str(e),
            }

        try:
            fn: Callable = TOOLS[tool_name]["fn"]
            result = fn(self.db_path, **args)
        except Exception as e:
            return {
                "answer": f"The tool '{tool_name}' failed: {e}",
                "tool": tool_name, "args": args, "result": None,
            }

        natural = self._phrase_result(question, tool_name, result)
        return {"answer": natural, "tool": tool_name, "args": args, "result": result}


# --------------------------------------------------------------------------- #
# JSON parsing (Mistral often wraps JSON in prose, fences, or trailing text)
# --------------------------------------------------------------------------- #
def _parse_tool_call(raw: str) -> dict:
    raw = raw.strip()
    # Strip code fences
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    # Find the first balanced { ... } block
    start = raw.find("{")
    if start == -1:
        return {"tool": "none", "args": {}}
    depth = 0
    for i, ch in enumerate(raw[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blob = raw[start:i + 1]
                try:
                    return json.loads(blob)
                except json.JSONDecodeError:
                    return {"tool": "none", "args": {}}
    return {"tool": "none", "args": {}}
