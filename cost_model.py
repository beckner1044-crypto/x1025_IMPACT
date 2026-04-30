"""
cost_model.py
Cost model for the x1025 AI intelligence layer at 10 / 50 / 200 vessel scale
(the tiers called out in the IMPACT proposal).

What this models
----------------
- Token cost per query, broken down by query type (procedural / operational / both).
  The token estimates come from measuring the actual prompts the prototype
  builds (router system prompt + retrieved chunks + question + answer).
- Two deployment options:
    cloud API   — billed per million tokens by the LLM provider
    self-hosted — Mistral-7B on a rented GPU, billed per hour, amortized
                  across all queries in the month
- One-time costs (embedding ingestion, vector store) are tiny at SMS scale
  (well under $5 even with hundreds of documents) and are reported separately.

Pricing
-------
All prices below are list prices retrieved as of April 2026 and are set as
constants near the top of the file so they are easy to update before the
formal IMPACT submission. Sources are noted next to each constant.

Run
---
    python cost_model.py                # prints the table + writes report
    python cost_model.py --quiet        # writes the report only

Output
------
    docs/cost_model_report.md
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List


# --------------------------------------------------------------------------- #
# Pricing constants — verified before final submission
# --------------------------------------------------------------------------- #
# Cloud LLM list prices, USD per 1M tokens (input / output).
# Source: Anthropic and OpenAI pricing pages, retrieved April 2026.
CLOUD_LLMS: Dict[str, Dict[str, float]] = {
    "Claude Haiku 4.5":  {"in": 1.00, "out":  5.00},
    "Claude Sonnet 4.6": {"in": 3.00, "out": 15.00},
    "Claude Opus 4.7":   {"in": 5.00, "out": 25.00},
    "GPT-5":             {"in": 1.25, "out": 10.00},
    "GPT-5.5":           {"in": 5.00, "out": 30.00},
}

# Boutique-cloud on-demand GPU rates, USD per hour. Source: Jarvislabs / RunPod
# / Lambda Labs price pages, retrieved April 2026. Hyperscaler pricing is 2-3x
# higher and is shown for reference only.
GPU_RATES: Dict[str, float] = {
    "NVIDIA L4 24GB":   0.45,   # cheapest viable for Mistral-7B FP16
    "NVIDIA A100 80GB": 1.49,   # comfortable headroom; common in inference
    "NVIDIA H100 80GB": 2.99,   # excess headroom for a 7B model — included for completeness
}

# A 7B model on an A100 with vLLM-style batching can sustain a few hundred
# req/min; 200 vessels × 30 queries/day = 6000/day = ~4/min average — single GPU.
# We assume one GPU instance running 24/7 in the self-hosted scenarios.
# Free-tier provider rate limits, as of April 2026. Cost is $0 by definition;
# the binding constraint is daily request quota.
FREE_TIERS: Dict[str, Dict[str, float]] = {
    "Groq free (Llama 3.1 8B)":        {"daily_quota": 14_400, "rpm": 30},
    "Google Gemini Flash free":        {"daily_quota":  1_500, "rpm": 15},   # ~1M tok/day at our query size
    "GitHub Models free (prototyping)":{"daily_quota":     150, "rpm": 15},  # tighter; TOS = experimentation only
}

HOURS_PER_MONTH = 24 * 30


# --------------------------------------------------------------------------- #
# Token budget per query — from the prototype's actual prompts
# --------------------------------------------------------------------------- #
# Procedural query (Layer 1): router classifies, then RAG retrieves k=4
# chunks and generates an answer with citations.
# Operational query (Layer 2): router classifies, tool-select call picks a
# function and extracts args, then phrase-result call writes the natural answer.
# Both: all of the above plus a synthesis call.
@dataclass
class QueryProfile:
    name: str
    input_tokens: int     # total input tokens summed across all LLM calls
    output_tokens: int    # total output tokens summed across all LLM calls
    share: float          # fraction of monthly traffic of this type


PROFILES: List[QueryProfile] = [
    # Router: ~150 in / 5 out.  RAG: ~750 in / 200 out (system + 4 chunks + Q).
    QueryProfile("procedural",   input_tokens=900,  output_tokens=205, share=0.50),
    # Router + tool select (~400 in / 50 out) + phrase result (~400 in / 100 out).
    QueryProfile("operational",  input_tokens=1100, output_tokens=200, share=0.40),
    # Procedural path + operational path + synthesis (~600 in / 250 out).
    QueryProfile("both",         input_tokens=2700, output_tokens=600, share=0.10),
]

# Workload assumption: queries per vessel per day. Maritime ops have a fairly
# bounded load — morning briefing, ops checks, certificate alerts, plus office
# superintendents. 30/vessel/day is a defensible baseline; the model is
# parameterized so x1025 can rerun with their own numbers.
QUERIES_PER_VESSEL_PER_DAY = 30
DAYS_PER_MONTH = 30


# --------------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------------- #
VESSEL_TIERS: List[int] = [10, 50, 200]


# --------------------------------------------------------------------------- #
# Cost computation
# --------------------------------------------------------------------------- #
@dataclass
class MonthlyCost:
    n_vessels: int
    queries_per_month: int
    input_tokens: int
    output_tokens: int
    rows: List[dict] = field(default_factory=list)


def monthly_volume(n_vessels: int) -> MonthlyCost:
    qpm = n_vessels * QUERIES_PER_VESSEL_PER_DAY * DAYS_PER_MONTH
    in_tok = sum(int(qpm * p.share * p.input_tokens) for p in PROFILES)
    out_tok = sum(int(qpm * p.share * p.output_tokens) for p in PROFILES)
    return MonthlyCost(n_vessels=n_vessels, queries_per_month=qpm,
                       input_tokens=in_tok, output_tokens=out_tok)


def cloud_cost(in_tok: int, out_tok: int, model: str) -> float:
    p = CLOUD_LLMS[model]
    return (in_tok / 1_000_000) * p["in"] + (out_tok / 1_000_000) * p["out"]


def selfhost_cost(gpu: str, instances: int = 1) -> float:
    """A self-hosted GPU is amortized across all traffic — cost is independent
    of query volume up to the GPU's throughput ceiling."""
    return GPU_RATES[gpu] * HOURS_PER_MONTH * instances


def build_table(volume: MonthlyCost) -> List[dict]:
    rows = []
    daily_queries = volume.queries_per_month / DAYS_PER_MONTH

    # Free tiers — feasible only if daily queries fit under the quota
    for name, t in FREE_TIERS.items():
        feasible = daily_queries <= t["daily_quota"]
        notes = (f"daily quota {int(t['daily_quota']):,}, {int(t['rpm'])} RPM"
                 if feasible
                 else f"DAILY QUOTA EXCEEDED ({int(daily_queries):,} > {int(t['daily_quota']):,})")
        rows.append({
            "deployment": "free-tier",
            "option": name,
            "monthly_cost_usd": 0.0 if feasible else float("inf"),
            "cost_per_query_usd": 0.0 if feasible else float("inf"),
            "notes": notes,
        })

    for model in CLOUD_LLMS:
        rows.append({
            "deployment": "cloud",
            "option": model,
            "monthly_cost_usd": round(cloud_cost(volume.input_tokens, volume.output_tokens, model), 2),
            "cost_per_query_usd": round(cloud_cost(volume.input_tokens, volume.output_tokens, model) / volume.queries_per_month, 4),
            "notes": "list price; -50% with batch API, -90% on cached prefixes",
        })
    for gpu in GPU_RATES:
        cost = selfhost_cost(gpu)
        rows.append({
            "deployment": "self-hosted",
            "option": f"Mistral-7B on {gpu}",
            "monthly_cost_usd": round(cost, 2),
            "cost_per_query_usd": round(cost / volume.queries_per_month, 4),
            "notes": "single instance, 24/7; flat cost regardless of volume",
        })
    rows.sort(key=lambda r: r["monthly_cost_usd"])
    return rows


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def print_table(volume: MonthlyCost, rows: List[dict]) -> None:
    print(f"\n=== {volume.n_vessels} vessels  "
          f"({volume.queries_per_month:,} queries/month, "
          f"{volume.input_tokens/1e6:.2f}M input / {volume.output_tokens/1e6:.2f}M output) ===")
    print(f"{'deployment':<13} {'option':<35} {'$/month':>10} {'$/query':>10}  notes")
    print("-" * 110)
    for r in rows:
        cost = r["monthly_cost_usd"]
        per_q = r["cost_per_query_usd"]
        cost_str = "  N/A    " if cost == float("inf") else f"{cost:>10,.2f}"
        perq_str = "  N/A    " if per_q == float("inf") else f"{per_q:>10,.4f}"
        print(f"{r['deployment']:<13} {r['option']:<35} {cost_str} {perq_str}  {r['notes']}")


def write_markdown(report_path: str, all_results: List[tuple]) -> None:
    lines: List[str] = []
    lines.append("# x1025 Cost Model — 10 / 50 / 200 Vessels\n")
    lines.append("Generated by `cost_model.py`. All figures in USD. Verify pricing "
                 "constants at the top of the script before submission.\n")

    lines.append("## Workload assumptions\n")
    lines.append(f"- **{QUERIES_PER_VESSEL_PER_DAY}** queries per vessel per day "
                 f"× **{DAYS_PER_MONTH}** days = monthly query volume\n")
    lines.append("- Query mix:\n")
    for p in PROFILES:
        lines.append(f"  - {p.name}: {int(p.share*100)}% of traffic, "
                     f"{p.input_tokens} input + {p.output_tokens} output tokens per query\n")
    lines.append("\nToken counts come from the prototype's actual prompts: router classification, "
                 "RAG retrieval over top-k=4 ISM chunks, Layer 2 tool-select + phrase-result calls, "
                 "and (for `both`) a final synthesis call.\n")

    for volume, rows in all_results:
        lines.append(f"\n## {volume.n_vessels} vessels\n")
        lines.append(f"- Queries / month: **{volume.queries_per_month:,}**\n")
        lines.append(f"- Input tokens / month: **{volume.input_tokens/1e6:.2f}M**\n")
        lines.append(f"- Output tokens / month: **{volume.output_tokens/1e6:.2f}M**\n\n")
        lines.append("| Deployment  | Option                            | $/month  | $/query | Notes |\n")
        lines.append("|-------------|-----------------------------------|---------:|--------:|-------|\n")
        for r in rows:
            cost = r["monthly_cost_usd"]
            per_q = r["cost_per_query_usd"]
            cost_str = "  —    " if cost == float("inf") else f"{cost:>8,.2f}"
            perq_str = "  —    " if per_q == float("inf") else f"{per_q:>7,.4f}"
            lines.append(f"| {r['deployment']:<11} | {r['option']:<35} "
                         f"| {cost_str} "
                         f"| {perq_str} "
                         f"| {r['notes']} |\n")

    lines.append("\n## Findings\n")
    lines.append(
        "- **The IMPACT prototype, the demo, and the early pilot can run for $0** "
        "via Groq Cloud's free tier (Llama 3.1 8B, 14,400 req/day). At 30 queries/"
        "vessel/day this carries the deployment up to ~480 vessels before the daily "
        "quota becomes the binding constraint. The risk is *policy*, not capacity: "
        "free tiers can change unilaterally, so production architecture should stay "
        "provider-portable.\n"
        "- **Cloud API wins at small paid scale.** At 10 vessels (9,000 queries/month), "
        "Claude Haiku 4.5 costs roughly an order of magnitude less than running a "
        "GPU 24/7. The break-even point is when you can keep a self-hosted GPU busy.\n"
        "- **Self-hosted wins at enterprise scale.** At 200 vessels (180,000 "
        "queries/month), an L4 instance running Mistral-7B is competitive with "
        "Haiku and significantly cheaper than Sonnet/Opus.\n"
        "- **Prompt caching is the biggest single lever** on the cloud side. The "
        "router system prompt and the Layer 2 tool spec are identical on every call "
        "— at 90% cache discount they effectively become free. Cloud-API costs above "
        "can drop by ~50–70% with aggressive caching of the static prefix.\n"
        "- **One-time costs are negligible.** Embedding the entire SMS corpus (even "
        "with hundreds of documents) is well under $1.\n"
    )

    lines.append("\n## Recommendation\n")
    lines.append(
        "- **IMPACT submission and demo:** Groq free tier. Zero dollars, no GPU "
        "needed, the prototype runs on a laptop. Set `LLM_PROVIDER=groq` and a "
        "free Groq API key; nothing else changes.\n"
        "- **Pilot (10 vessels):** Stay on Groq free tier as long as the rate "
        "limits hold; otherwise Claude Haiku 4.5 at ~$21/month. Both options vastly "
        "cheaper than self-hosting at this scale.\n"
        "- **Scale-up (50 vessels):** Re-evaluate. If Groq's free tier is still "
        "viable, no change needed. If volume grows or x1025 wants an SLA-backed "
        "provider, switch to Haiku 4.5 (~$107/mo) or move toward self-hosting.\n"
        "- **Enterprise (200+ vessels):** Self-hosted Mistral-7B (or similar "
        "open model) on a rented L4. Predictable flat monthly cost, full data "
        "control, no third-party data residency concerns — material for a "
        "maritime customer base.\n"
    )

    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--quiet", action="store_true",
                   help="Skip console output; only write the markdown report.")
    p.add_argument("--report", default="docs/cost_model_report.md",
                   help="Path to write the markdown report.")
    args = p.parse_args(argv)

    all_results = []
    for n in VESSEL_TIERS:
        v = monthly_volume(n)
        rows = build_table(v)
        all_results.append((v, rows))
        if not args.quiet:
            print_table(v, rows)

    write_markdown(args.report, all_results)
    if not args.quiet:
        print(f"\nWrote report to {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
