# Vessel Dashboard v6
### UMass Boston — IMPACT Program

Maritime AI operational dashboard with NMEA 2000 telemetry, Signal K, AI copilot,
and a connectable Python backend.

---

## Project Structure

```
vessel_dashboard_v6/
├── index.html               # Main UI
├── styles.css               # Styles (dark/light mode, responsive)
├── app.js                   # Dashboard logic and chatbot
├── boat-telemetry.json      # Simulated NMEA 2000 + Signal K data
├── python_api_example.py    # Example FastAPI backend
├── requirements.txt         # Python dependencies
└── README.md
```

---

## How to Open the Dashboard

> **Important:** Browsers block `fetch()` from `file://`.
> Always use a local server.

### Option A — Python (no extra install)
```bash
cd vessel_dashboard_v6
python -m http.server 8080
```
Then open: **http://localhost:8080**

### Option B — Node.js
```bash
npx serve .
```

### Option C — VS Code
Install the **Live Server** extension and click *Go Live*.

---

## How to Run the Python Backend

```bash
pip install -r requirements.txt
uvicorn python_api_example:app --reload --port 8000
```

Interactive docs (Swagger): **http://localhost:8000/docs**

---

## Connecting the Dashboard to the Backend

1. Open the dashboard in your browser.
2. In the sidebar, type in the **"Copilot endpoint URL"** field:
   ```
   http://localhost:8000/api/copilot/chat
   ```
3. Click **Connect endpoint**.

From that point the chatbot sends every question to your Python backend.

---

## API Contract

### Request
```
POST /api/copilot/chat
Content-Type: application/json
```
```json
{
  "question":  "What is the engine status?",
  "telemetry": { "...full boat-telemetry.json object..." },
  "source":    "vessel-dashboard-v6"
}
```

### Response
```json
{
  "answer": "Engine at 1780 rpm, coolant 91.4 °C...",
  "source": "vessel-dashboard-v6",
  "api":    "vessel-dashboard-fastapi"
}
```

---

## Integrating a Real LLM

Edit the `_build_answer` function in `python_api_example.py`:

**OpenAI example:**
```python
from openai import OpenAI
import json
client = OpenAI()

def _build_answer(question: str, telemetry: dict) -> str:
    context = json.dumps(telemetry, ensure_ascii=False)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": f"You are an expert maritime copilot. Current telemetry: {context}"},
            {"role": "user",   "content": question}
        ]
    )
    return response.choices[0].message.content
```

**Ollama (free, local) example:**
```python
import requests, json

def _build_answer(question: str, telemetry: dict) -> str:
    context = json.dumps(telemetry, ensure_ascii=False)
    res = requests.post("http://localhost:11434/api/generate", json={
        "model": "llama3",
        "prompt": f"Telemetry: {context}\nQuestion: {question}",
        "stream": False
    })
    return res.json()["response"]
```

---

## Free Stack

| Component | Role | Link |
|-----------|------|------|
| Signal K Server | NMEA 2000 → JSON gateway | https://signalk.org |
| OpenCPN | Open-source navigation software | https://opencpn.org |
| Node-RED | Flow-based automation | https://nodered.org |
| FastAPI | Python backend | https://fastapi.tiangolo.com |
| Ollama | Free local LLM | https://ollama.com |

---

## Simulated NMEA 2000 PGNs

| PGN | Data |
|-----|------|
| 129026 | COG and SOG |
| 129029 | GNSS Position |
| 127488 | Engine RPM |
| 127489 | Engine Parameters |
| 127505 | Tank Levels |
| 127508 | Battery Status |
| 130306 | Wind Speed and Angle |
| 128267 | Water Depth |

---

## Credits

Developed as part of the **IMPACT Program** — UMass Boston.  
Proposal: Maritime AI for monitoring, safety and on-board decision-making.
