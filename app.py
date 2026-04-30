"""
app.py
Gradio web UI for the x1025 assistant.

Run after setup_data.py:
    python app.py

UI features:
  - Two-pane layout: chat on the left, evidence on the right.
  - Thumbs-up / thumbs-down per answer, persisted to the feedback table
    and foreign-keyed to the audit log row.
  - "Explain this answer" panel showing route, sources, tool call,
    confidence badge, and per-claim verifier verdicts (when verify=True).

Session safety:
  - We use gr.State to track the most recent answer's audit_id per
    session, instead of a module-level global. This is what survives
    multiple users, page refreshes, and the gradio reload model.
"""
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import json
import os

import gradio as gr

from x1025.chatbot import X1025Chatbot
from x1025.feedback import FeedbackStore


# --------------------------------------------------------------------------- #
# Setup
# --------------------------------------------------------------------------- #
# verify=True turns on the faithfulness verifier and the confidence floor.
# It costs more LLM calls per question, so leave it off for the initial
# free-tier demo and turn on with VERIFY=1 in the env when you want to
# show the safety-rail behaviour.
_VERIFY = os.environ.get("VERIFY", "").strip() not in ("", "0", "false", "no")

bot = X1025Chatbot(verify=_VERIFY)
bot.setup()
feedback = FeedbackStore()


EXAMPLES = [
    "What is the procedure for releasing the fixed CO2 system in an engine room fire?",
    "What's the latest ETA for MV Boreas?",
    "Is MV Boreas meeting its charter-party speed?",
    "Which certificates are expiring in the next 30 days?",
    "What's the fuel ROB for MV Aurora?",
    "If MV Cassini has an expired certificate while at sea, what should the Master do?",
]


# --------------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------------- #
def _short(obj, limit=1500):
    s = json.dumps(obj, indent=2, default=str)
    return s if len(s) <= limit else s[:limit] + "\n  ... (truncated)"


def _confidence_badge(faith: dict | None) -> str:
    """One-line confidence summary for the evidence pane."""
    if not faith:
        return ""
    score = faith["score"]
    label = (
        "🟢 high"   if score >= 0.8 else
        "🟡 medium" if score >= 0.5 else
        "🔴 low"
    )
    return (f"\n**Confidence:** {label} "
            f"({faith['n_supported']}/{faith['n_claims']} claims supported)")


def _format_evidence(out: dict) -> str:
    parts = [f"**Route:** `{out['route']}`"]

    if out.get("layer1") and out["layer1"].get("sources"):
        parts.append("\n**Procedure sources:**")
        for s in out["layer1"]["sources"]:
            parts.append(f"  - `[{s['index']}]` {s['source']} — *{s['section']}*")

    if out.get("layer2") and out["layer2"].get("tool"):
        parts.append(f"\n**Tool called:** `{out['layer2']['tool']}"
                     f"({out['layer2'].get('args', {})})`")
        if out["layer2"].get("result") is not None:
            parts.append("\n**Tool result:**")
            parts.append(f"```json\n{_short(out['layer2']['result'])}\n```")

    parts.append(_confidence_badge(out.get("faithfulness")))

    if out.get("fallback_reason"):
        parts.append(f"\n**⚠ Fallback engaged:** {out['fallback_reason']}")
        if out.get("original_answer"):
            parts.append(f"\n*Original (unverified) answer:*\n> {out['original_answer'][:300]}")

    return "\n".join(parts)


def _format_explanation(out: dict) -> str:
    """Per-claim verifier breakdown for the 'Explain this answer' panel."""
    f = out.get("faithfulness")
    if not f:
        return ("*Faithfulness verification was off for this answer.*\n\n"
                "Set `VERIFY=1` in your environment and restart to enable it.")
    lines = [
        f"**Faithfulness score:** {f['score']:.0%} "
        f"({f['n_supported']} supported, "
        f"{f['n_contradicted']} contradicted, "
        f"{f['n_not_found']} not found)\n",
        "### Per-claim verdicts",
    ]
    icon = {"SUPPORTED": "✅", "CONTRADICTED": "❌", "NOT_FOUND": "⚠️"}
    for v in f.get("verdicts", []):
        lines.append(f"{icon.get(v['label'], '?')} **{v['label']}** — {v['claim']}")
        if v.get("rationale"):
            lines.append(f"   > {v['rationale']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Event handlers
# --------------------------------------------------------------------------- #
def on_send(question: str, history: list, _state: dict):
    """Send a question; return updated chat, evidence, audit state, and reset
    feedback status."""
    if not question.strip():
        return history, "", _state, "", ""

    out = bot.ask(question)
    history = history + [(question, out["answer"])]
    new_state = {
        "audit_id":  out.get("audit_id"),
        "last_out":  out,
    }
    return (
        history,                  # chatbot
        _format_evidence(out),    # evidence pane
        new_state,                # session state
        "",                       # feedback status (reset)
        "",                       # query box (clear)
    )


def on_thumb(thumb: str, state: dict) -> str:
    if not state or not state.get("audit_id"):
        return "Ask a question first, then rate the answer."
    try:
        feedback.add(state["audit_id"], thumb)
    except Exception as e:
        return f"Couldn't save feedback: {e}"
    return f"Thanks — recorded a thumbs-{thumb}. (audit id: {state['audit_id'][:8]}…)"


def on_explain(state: dict) -> str:
    if not state or not state.get("last_out"):
        return "*Ask a question first, then click Explain to see how the answer was verified.*"
    return _format_explanation(state["last_out"])


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #
with gr.Blocks(title="x1025 Maritime Assistant") as demo:
    gr.Markdown(
        "# x1025 Maritime Assistant\n"
        "Layer 1 (SMS RAG) + Layer 2 (operational queries) + router."
        + ("  ·  *Verifier ON*" if _VERIFY else "")
    )

    # Per-session state — survives across button clicks but stays per-user.
    state = gr.State({})

    with gr.Row():
        with gr.Column(scale=2):
            chat = gr.Chatbot(height=480, label="Conversation")
            box = gr.Textbox(
                placeholder="Ask about ISM procedures or live vessel data...",
                label="Question",
            )
            with gr.Row():
                send = gr.Button("Send", variant="primary")
                up   = gr.Button("👍 Helpful")
                down = gr.Button("👎 Not helpful")
            fb_status = gr.Markdown(value="")
            gr.Examples(examples=EXAMPLES, inputs=box)

        with gr.Column(scale=1):
            evidence = gr.Markdown(
                value="*The router decision, retrieved sources, and tool calls "
                      "will appear here once you ask a question.*",
            )
            with gr.Accordion("🔍 Explain this answer", open=False):
                explain_pane = gr.Markdown(
                    value="*Click after asking a question to see verifier verdicts.*"
                )
                explain_btn = gr.Button("Refresh explanation")

    # Wiring
    send.click(on_send,
               inputs=[box, chat, state],
               outputs=[chat, evidence, state, fb_status, box])
    box.submit(on_send,
               inputs=[box, chat, state],
               outputs=[chat, evidence, state, fb_status, box])
    up.click(lambda s: on_thumb("up", s),     inputs=[state], outputs=[fb_status])
    down.click(lambda s: on_thumb("down", s), inputs=[state], outputs=[fb_status])
    explain_btn.click(on_explain, inputs=[state], outputs=[explain_pane])


if __name__ == "__main__":
    demo.launch()
