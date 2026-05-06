"""
Maritime AI Copilot — Example FastAPI Backend
==============================================
File: python_api_example.py
Project: Vessel Dashboard v6 | UMass Boston IMPACT Program

Installation:
    pip install -r requirements.txt

Run:
    uvicorn python_api_example:app --reload --port 8000

Main endpoint:
    POST http://localhost:8000/api/copilot/chat

Interactive docs:
    http://localhost:8000/docs
"""

from __future__ import annotations
from typing import Any, Dict, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# -------------------------------------------------------
app = FastAPI(
    title="Vessel Dashboard v6 — AI Copilot API",
    description="Example backend for the Vessel Dashboard v6 (UMass Boston IMPACT Program).",
    version="1.0.0",
)

# Open CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------
# Request / Response models
# -------------------------------------------------------
class ChatPayload(BaseModel):
    question: str
    telemetry: Dict[str, Any]
    source: Optional[str] = "vessel-dashboard-v6"

class ChatResponse(BaseModel):
    answer: str
    source: str
    api: str = "vessel-dashboard-fastapi"

# -------------------------------------------------------
# Helpers
# -------------------------------------------------------
def _get(data: dict, *keys, default="N/A"):
    """Safe nested key access."""
    for k in keys:
        if not isinstance(data, dict):
            return default
        data = data.get(k, {})
    return data if data != {} else default

def _has(text: str, terms: list[str]) -> bool:
    return any(t in text for t in terms)

# -------------------------------------------------------
# Routes
# -------------------------------------------------------
@app.get("/health", tags=["system"])
def health():
    """Backend health check."""
    return {"status": "ok", "service": "vessel-dashboard-copilot"}


@app.get("/api/v1/vessel/telemetry", tags=["telemetry"])
def telemetry_info():
    """
    Instructions for connecting real telemetry from Signal K.
    In production, read from your NMEA 2000 / Signal K gateway here.
    """
    return {
        "message": "Connect your Signal K gateway or transform NMEA 2000 data to JSON here.",
        "signal_k_default": "http://localhost:3000/signalk/v1/api/vessels/self",
        "source": "python_api_example"
    }


@app.post("/api/copilot/chat", response_model=ChatResponse, tags=["copilot"])
def copilot_chat(payload: ChatPayload):
    """
    Receives a question from the dashboard, the current telemetry,
    and returns a generated response.
    Replace _build_answer() with your own LLM (OpenAI, Ollama, Llama, etc.).
    """
    if not payload.question.strip():
        raise HTTPException(status_code=422, detail="Question cannot be empty.")

    answer = _build_answer(payload.question, payload.telemetry)
    return ChatResponse(answer=answer, source=payload.source or "unknown")


# -------------------------------------------------------
# Answer engine (replace with your LLM)
# -------------------------------------------------------
def _build_answer(question: str, t: dict) -> str:
    """
    Rule-based response logic using live telemetry.
    Replace this block with OpenAI, Ollama, LangChain, etc.
    """
    q        = question.lower()
    vessel   = _get(t, "vessel")
    nav      = _get(t, "navigation")
    engine   = _get(t, "propulsion", "engine1")
    battery  = _get(t, "energy", "batteries", "house")
    charging = _get(t, "energy", "charging")
    tanks    = _get(t, "tanks", "fuel", "main")
    wind     = _get(t, "environment", "wind")
    safety   = _get(t, "safety", "ais")
    analytics= _get(t, "analytics")

    if _has(q, ["summary", "status", "general", "vessel", "overview"]):
        return (
            f"{_get(vessel, 'name')} is sailing at {_get(nav, 'speedOverGroundKn')} knots, "
            f"heading {_get(nav, 'headingTrueDeg')}\u00b0. "
            f"Engine at {_get(engine, 'rpm')} rpm, coolant {_get(engine, 'coolantTempC')} \u00b0C. "
            f"House battery at {_get(battery, 'socPercent')}% ({_get(battery, 'voltage')} V). "
            f"Assessment: {_get(analytics, 'healthSummary')}"
        )

    if _has(q, ["engine", "rpm", "temperature", "coolant", "oil"]):
        return (
            f"Engine at {_get(engine, 'rpm')} rpm, load {_get(engine, 'loadPercent')}%. "
            f"Coolant: {_get(engine, 'coolantTempC')} \u00b0C. "
            f"Oil pressure: {_get(engine, 'oilPressureKpa')} kPa. "
            f"Engine hours: {_get(engine, 'engineHours')}. "
            "If temperature rises, check raw water intake, heat exchanger and filters."
        )

    if _has(q, ["power", "battery", "alternator", "solar", "voltage", "soc"]):
        return (
            f"House battery: {_get(battery, 'voltage')} V, SOC {_get(battery, 'socPercent')}%, "
            f"current {_get(battery, 'currentA')} A, "
            f"~{_get(battery, 'timeRemainingMin')} min remaining. "
            f"Alternator: {_get(charging, 'alternatorVoltage')} V. "
            f"Solar: {_get(charging, 'solarInputW')} W."
        )

    if _has(q, ["fuel", "tank", "range", "autonomy"]):
        return (
            f"Fuel at {_get(tanks, 'levelPercent')}% "
            f"({_get(tanks, 'remainingL')} L of {_get(tanks, 'capacityL')} L). "
            f"Consumption: {_get(engine, 'fuelRateLh')} L/h. "
            f"Estimated range: {_get(analytics, 'rangeEstimateNm')} nm."
        )

    if _has(q, ["wind"]):
        return (
            f"Apparent wind: {_get(wind, 'apparentSpeedKn')} kn at {_get(wind, 'apparentAngleDeg')}\u00b0. "
            f"True wind: {_get(wind, 'trueSpeedKn')} kn at {_get(wind, 'trueAngleDeg')}\u00b0."
        )

    if _has(q, ["ais", "traffic", "collision", "cpa", "tcpa"]):
        return (
            f"{_get(safety, 'targetsNearby')} AIS targets nearby. "
            f"Closest CPA: {_get(safety, 'closestCpaNm')} NM, "
            f"TCPA: {_get(safety, 'closestTcpaMin')} min. "
            "Verify with ARPA/AIS and maintain visual watch."
        )

    if _has(q, ["alert", "risk", "priority"]):
        alerts = _get(analytics, "alerts") if isinstance(_get(analytics, "alerts"), list) else []
        if not alerts:
            return "No active alerts at this time."
        return " | ".join(
            f"[{a.get('severity','').upper()}] {a.get('title')}: {a.get('recommendation')}"
            for a in alerts
        )

    if _has(q, ["mob", "man overboard"]):
        return (
            "MOB: 1) Activate MOB waypoint on plotter. "
            "2) Throw life ring. "
            "3) Assign dedicated visual observer. "
            "4) Execute recovery maneuver according to wind. "
            "5) Transmit MAYDAY if unable to recover alone."
        )

    if _has(q, ["fire", "engine room"]):
        return (
            "Fire on board: 1) Sound the alarm. "
            "2) Shut ventilation and fuel to compartment. "
            "3) Activate fixed suppression system if available. "
            "4) Use appropriate extinguisher. "
            "5) If not controlled in 60 s, transmit MAYDAY and prepare to abandon."
        )

    if _has(q, ["water", "bilge", "flooding", "ingress"]):
        return (
            "Water ingress: 1) Locate source and plug temporarily. "
            "2) Activate bilge pumps. "
            "3) Reduce speed. "
            "4) Redistribute weights if listing. "
            "5) Report situation and evaluate abandonment."
        )

    if _has(q, ["propulsion", "no engine", "loss of propulsion"]):
        return (
            "Loss of propulsion: 1) Check emergency stop and restart. "
            "2) Check fuel and filters. "
            "3) Anchor if possible. "
            "4) Broadcast PAN PAN with position and status."
        )

    if _has(q, ["mayday", "distress", "radio", "vhf"]):
        return (
            "MAYDAY on VHF channel 16: "
            "'MAYDAY MAYDAY MAYDAY, this is [vessel name], "
            "position [lat/lon], [type of emergency], "
            "[people on board], requesting immediate assistance. Out.'"
        )

    return (
        "I can answer about: vessel summary, engine, power, fuel, wind, AIS, "
        "alerts, MOB, fire, flooding, propulsion and MAYDAY. "
        "For more advanced responses, integrate an LLM in the _build_answer function."
    )
