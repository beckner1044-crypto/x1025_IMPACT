"""
evaluate.py
Evaluation harness for the x1025 prototype. Five components:

  1. Router accuracy on a labeled question set.
  2. Layer 1 retrieval@k: does the top-k include the expected source file?
  3. Layer 2 tool dispatch: did the LLM pick the right tool?
  4. End-to-end LLM-as-judge scoring on three rubrics (faithfulness,
     relevance, completeness).
  5. Faithfulness verifier: extract claims from each answer, check support
     against retrieved evidence, report supported / contradicted / not_found.

Plus a separate retrieval-quality pass on a held-out IR test set
(x1025/retrieval_eval.py) that produces Recall@K, Precision@K, and MRR.

Important caveat
----------------
The judge and verifier in the prototype share the LLM under test, so they
have correlated blind spots. Use these numbers for relative A/B comparisons
(prompt changes, chunking changes, retrieval-k sweeps), not as a quality
stamp for x1025. For the IMPACT final report, swap in a stronger model by
replacing `Judge(bot.llm)` and `Verifier(bot.llm)` with one pointed at
Claude or GPT-5.

Usage
-----
    python evaluate.py                          # everything
    python evaluate.py --skip-judge             # skip LLM-as-judge
    python evaluate.py --skip-verify            # skip faithfulness verifier
    python evaluate.py --skip-retrieval-eval    # skip Recall@K / MRR pass
    python evaluate.py --report path.md         # custom report path
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional

from x1025.chatbot import X1025Chatbot
from x1025.faithfulness import Verifier, FaithfulnessResult
from x1025.retrieval_eval import (
    RETRIEVAL_CASES, RetrievalCase, evaluate_case as eval_retrieval_case,
    aggregate as aggregate_retrieval, RetrievalResult, RetrievalSummary,
)


# --------------------------------------------------------------------------- #
# Labeled set
# --------------------------------------------------------------------------- #
@dataclass
class EvalCase:
    question: str
    expected_route: str
    expected_source: Optional[str] = None     # for procedural
    expected_tool: Optional[str] = None       # for operational
    must_mention: List[str] = field(default_factory=list)
    """
    Substrings the final answer ought to contain. Used by the judge as a
    completeness anchor and surfaced as a separate `must_mention_hits` metric
    that does NOT depend on the LLM.
    """


EVAL_SET: List[EvalCase] = [
    EvalCase(
        question="How do I release the fixed CO2 system in the engine room?",
        expected_route="procedural",
        expected_source="02_fire_emergency.md",
        must_mention=["Master", "head count", "20 second"],
    ),
    EvalCase(
        question="What goes in a noon report?",
        expected_route="procedural",
        expected_source="03_daily_reporting.md",
        must_mention=["fuel", "position", "speed"],
    ),
    EvalCase(
        question="What does the Master do if a certificate expires while at sea?",
        expected_route="procedural",
        expected_source="04_certificate_management.md",
        must_mention=["flag", "extension", "DPA"],
    ),
    EvalCase(
        question="What is the Williamson turn?",
        expected_route="procedural",
        expected_source="05_man_overboard.md",
        must_mention=["60", "rudder", "reciprocal"],
    ),
    EvalCase(
        question="What's the ETA for MV Boreas?",
        expected_route="operational",
        expected_tool="get_vessel_eta",
        must_mention=["Boreas", "Singapore"],
    ),
    EvalCase(
        question="How much fuel does MV Aurora have on board?",
        expected_route="operational",
        expected_tool="get_fuel_rob",
        must_mention=["Aurora"],
    ),
    EvalCase(
        question="Is MV Boreas meeting charter party speed?",
        expected_route="operational",
        expected_tool="get_speed_performance",
        must_mention=["Boreas", "12.5"],
    ),
    EvalCase(
        question="Which certificates are expiring in the next 30 days?",
        expected_route="operational",
        expected_tool="get_certificates_expiring",
        must_mention=["Cassini", "Boreas"],
    ),
    EvalCase(
        question="List all vessels in the fleet.",
        expected_route="operational",
        expected_tool="list_vessels",
        must_mention=["Aurora", "Boreas", "Cassini", "Dorado", "Equinox"],
    ),
]


# --------------------------------------------------------------------------- #
# LLM-as-judge
# --------------------------------------------------------------------------- #
_SCORE_RE = re.compile(
    r'"?\s*(faithfulness|relevance|completeness)"?\s*:\s*([1-5])',
    re.IGNORECASE,
)


@dataclass
class JudgeScore:
    faithfulness: int = 0
    relevance: int = 0
    completeness: int = 0
    rationale: str = ""

    @property
    def average(self) -> float:
        return (self.faithfulness + self.relevance + self.completeness) / 3.0


class Judge:
    """LLM-as-judge wrapper. Uses the bot's own LLM for the prototype.
    Swap the constructor to inject a stronger model in production."""

    def __init__(self, llm):
        self.llm = llm

    def score(self, question: str, evidence: str, answer: str) -> JudgeScore:
        system = (
            "You are an evaluator for a maritime AI assistant. Score the assistant's answer "
            "on three rubrics, each from 1 (worst) to 5 (best):\n"
            "  - faithfulness: the answer only uses information from the evidence below "
            "and does not invent procedures, vessels, fuel figures, or dates.\n"
            "  - relevance:    the answer directly addresses the question, not a related "
            "but different question.\n"
            "  - completeness: the answer covers the key information needed to act on the "
            "question. Omitting a critical step or figure lowers this score.\n"
            "Reply ONLY with a single JSON object: "
            '{"faithfulness": N, "relevance": N, "completeness": N, "rationale": "..."}. '
            "No prose outside the JSON, no code fences."
        )
        user = (
            f"Question:\n{question}\n\n"
            f"Evidence the assistant was given:\n{evidence}\n\n"
            f"Assistant's answer:\n{answer}\n\n"
            "JSON:"
        )
        raw = self.llm.instruct(system, user, max_new_tokens=200)
        return _parse_judge(raw)


def _parse_judge(raw: str) -> JudgeScore:
    """Best-effort JSON extraction. If the LLM emits prose around the JSON
    (Mistral-7B sometimes does), fall back to regex over the three keys."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    start = raw.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(raw[start:], start=start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    blob = raw[start:i + 1]
                    try:
                        d = json.loads(blob)
                        return JudgeScore(
                            faithfulness=int(d.get("faithfulness", 0)),
                            relevance=int(d.get("relevance", 0)),
                            completeness=int(d.get("completeness", 0)),
                            rationale=str(d.get("rationale", ""))[:500],
                        )
                    except (json.JSONDecodeError, ValueError, TypeError):
                        break

    # Regex fallback
    s = JudgeScore()
    for m in _SCORE_RE.finditer(raw):
        key, val = m.group(1).lower(), int(m.group(2))
        setattr(s, key, val)
    return s


def evidence_for(case: EvalCase, result: dict) -> str:
    """Construct the evidence string the judge sees, depending on which layer
    answered."""
    parts: List[str] = []
    l1 = result.get("layer1")
    l2 = result.get("layer2")
    if l1 and l1.get("context"):
        parts.append(f"[Procedure excerpts]\n{l1['context']}")
    if l2 and l2.get("result") is not None:
        parts.append(f"[Tool: {l2.get('tool')}({l2.get('args', {})})]\n"
                     f"{json.dumps(l2['result'], indent=2, default=str)}")
    return "\n\n".join(parts) if parts else "(no evidence retrieved)"


# --------------------------------------------------------------------------- #
# Per-case runner
# --------------------------------------------------------------------------- #
@dataclass
class CaseResult:
    question: str
    expected_route: str
    actual_route: str
    route_ok: bool
    retrieval_ok: Optional[bool]
    tool_ok: Optional[bool]
    must_mention_hits: int
    must_mention_total: int
    answer: str
    judge: Optional[JudgeScore]
    faithfulness: Optional[FaithfulnessResult]
    elapsed_s: float


def run_case(bot: X1025Chatbot, judge: Optional[Judge],
             verifier: Optional[Verifier], case: EvalCase) -> CaseResult:
    t0 = time.time()
    out = bot.ask(case.question)
    elapsed = time.time() - t0

    # Component checks
    route_ok = out["route"] == case.expected_route

    retrieval_ok: Optional[bool] = None
    if case.expected_source and out.get("layer1"):
        sources = [s["source"] for s in out["layer1"].get("sources", [])]
        retrieval_ok = case.expected_source in sources

    tool_ok: Optional[bool] = None
    if case.expected_tool and out.get("layer2"):
        tool_ok = out["layer2"].get("tool") == case.expected_tool

    answer = out["answer"]
    hits = sum(1 for kw in case.must_mention if kw.lower() in answer.lower())

    score: Optional[JudgeScore] = None
    if judge is not None:
        score = judge.score(case.question, evidence_for(case, out), answer)

    faith: Optional[FaithfulnessResult] = None
    if verifier is not None:
        evidence = evidence_for(case, out)
        if evidence and evidence != "(no evidence retrieved)":
            faith = verifier.verify(answer, evidence)

    return CaseResult(
        question=case.question,
        expected_route=case.expected_route,
        actual_route=out["route"],
        route_ok=route_ok,
        retrieval_ok=retrieval_ok,
        tool_ok=tool_ok,
        must_mention_hits=hits,
        must_mention_total=len(case.must_mention),
        answer=answer,
        judge=score,
        faithfulness=faith,
        elapsed_s=elapsed,
    )


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def print_summary(results: List[CaseResult]) -> None:
    n = len(results)
    route_ok = sum(r.route_ok for r in results)
    retr = [r for r in results if r.retrieval_ok is not None]
    tool = [r for r in results if r.tool_ok is not None]
    judged = [r for r in results if r.judge is not None]
    verified = [r for r in results if r.faithfulness is not None]

    print()
    print(f"Router accuracy:        {route_ok}/{n}")
    if retr:
        print(f"Layer1 retrieval@k:     {sum(r.retrieval_ok for r in retr)}/{len(retr)}")
    if tool:
        print(f"Layer2 tool pick:       {sum(r.tool_ok for r in tool)}/{len(tool)}")

    mm_hits = sum(r.must_mention_hits for r in results)
    mm_tot  = sum(r.must_mention_total for r in results)
    if mm_tot:
        print(f"Must-mention coverage:  {mm_hits}/{mm_tot} ({100*mm_hits/mm_tot:.0f}%)")

    if judged:
        f = sum(r.judge.faithfulness  for r in judged) / len(judged)
        rel = sum(r.judge.relevance    for r in judged) / len(judged)
        c = sum(r.judge.completeness for r in judged) / len(judged)
        print(f"Judge faithfulness:     {f:.2f} / 5")
        print(f"Judge relevance:        {rel:.2f} / 5")
        print(f"Judge completeness:     {c:.2f} / 5")
    if verified:
        total_claims = sum(r.faithfulness.n_claims      for r in verified)
        total_sup    = sum(r.faithfulness.n_supported   for r in verified)
        total_con    = sum(r.faithfulness.n_contradicted for r in verified)
        total_nf     = sum(r.faithfulness.n_not_found   for r in verified)
        ratio = total_sup / total_claims if total_claims else 0.0
        print(f"Verifier supported:     {total_sup}/{total_claims} claims "
              f"({100*ratio:.0f}%)")
        if total_con or total_nf:
            print(f"  contradicted: {total_con}, not_found: {total_nf}")
    print(f"Avg latency:            {sum(r.elapsed_s for r in results)/n:.2f} s")


def write_report(path: str, results: List[CaseResult],
                 retrieval_summary: Optional[RetrievalSummary] = None) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# x1025 Evaluation Report\n\n")
        f.write("Generated by `evaluate.py`. The judge and verifier in this prototype "
                "share the LLM under test, so absolute scores are optimistic. "
                "Use these numbers for relative A/B comparisons.\n\n")

        n = len(results)
        f.write("## End-to-end summary\n\n")
        f.write(f"- Router accuracy: **{sum(r.route_ok for r in results)}/{n}**\n")
        retr = [r for r in results if r.retrieval_ok is not None]
        if retr:
            f.write(f"- Layer 1 source-in-topk: **{sum(r.retrieval_ok for r in retr)}/{len(retr)}**\n")
        tool = [r for r in results if r.tool_ok is not None]
        if tool:
            f.write(f"- Layer 2 tool pick: **{sum(r.tool_ok for r in tool)}/{len(tool)}**\n")
        mm_hits = sum(r.must_mention_hits for r in results)
        mm_tot  = sum(r.must_mention_total for r in results)
        if mm_tot:
            f.write(f"- Must-mention coverage: **{mm_hits}/{mm_tot}** "
                    f"({100*mm_hits/mm_tot:.0f}%)\n")
        judged = [r for r in results if r.judge is not None]
        if judged:
            f_avg   = sum(r.judge.faithfulness  for r in judged) / len(judged)
            r_avg   = sum(r.judge.relevance     for r in judged) / len(judged)
            c_avg   = sum(r.judge.completeness  for r in judged) / len(judged)
            f.write(f"- Judge faithfulness: **{f_avg:.2f} / 5**\n")
            f.write(f"- Judge relevance: **{r_avg:.2f} / 5**\n")
            f.write(f"- Judge completeness: **{c_avg:.2f} / 5**\n")
        verified = [r for r in results if r.faithfulness is not None]
        if verified:
            total = sum(r.faithfulness.n_claims      for r in verified)
            sup   = sum(r.faithfulness.n_supported   for r in verified)
            con   = sum(r.faithfulness.n_contradicted for r in verified)
            nf    = sum(r.faithfulness.n_not_found   for r in verified)
            ratio = sup / total if total else 0.0
            f.write(f"- Verifier supported: **{sup}/{total}** claims "
                    f"({100*ratio:.0f}%) — contradicted: {con}, not_found: {nf}\n")
        f.write(f"- Avg latency: **{sum(r.elapsed_s for r in results)/n:.2f} s**\n\n")

        # Retrieval-quality section (Recall@K, MRR, etc.)
        if retrieval_summary is not None:
            f.write(f"## Retrieval quality (held-out set, k={retrieval_summary.k}, "
                    f"n={retrieval_summary.n})\n\n")
            f.write(f"- Mean Recall@{retrieval_summary.k}: **{retrieval_summary.mean_recall:.2f}**\n")
            f.write(f"- Mean Precision@{retrieval_summary.k}: **{retrieval_summary.mean_precision:.2f}**\n")
            f.write(f"- Mean MRR: **{retrieval_summary.mean_mrr:.2f}**\n")
            f.write(f"- Source-hit rate: **{retrieval_summary.source_hit_rate:.2f}**\n\n")
            f.write("| qid | recall | precision | MRR | source hit | spans found |\n")
            f.write("|-----|-------:|----------:|----:|:----------:|-------------|\n")
            for c in retrieval_summary.per_case:
                f.write(f"| {c.qid} | {c.recall_at_k:.2f} | {c.precision_at_k:.2f} | "
                        f"{c.mrr:.2f} | {'✓' if c.source_in_topk else '✗'} | "
                        f"{c.spans_found}/{c.spans_total} |\n")
            f.write("\n")

        f.write("## Per-question detail\n\n")
        for i, r in enumerate(results, start=1):
            f.write(f"### {i}. {r.question}\n\n")
            f.write(f"- Route: `{r.actual_route}` (expected `{r.expected_route}`) "
                    f"{'✓' if r.route_ok else '✗'}\n")
            if r.retrieval_ok is not None:
                f.write(f"- Retrieval: {'✓' if r.retrieval_ok else '✗'}\n")
            if r.tool_ok is not None:
                f.write(f"- Tool pick: {'✓' if r.tool_ok else '✗'}\n")
            if r.must_mention_total:
                f.write(f"- Must-mention: {r.must_mention_hits}/{r.must_mention_total}\n")
            if r.judge:
                f.write(f"- Judge: faithfulness {r.judge.faithfulness}/5, "
                        f"relevance {r.judge.relevance}/5, "
                        f"completeness {r.judge.completeness}/5\n")
                if r.judge.rationale:
                    f.write(f"  - rationale: {r.judge.rationale}\n")
            if r.faithfulness:
                fa = r.faithfulness
                f.write(f"- Verifier: {fa.n_supported}/{fa.n_claims} claims supported "
                        f"(contradicted {fa.n_contradicted}, not_found {fa.n_not_found})\n")
                for v in fa.verdicts:
                    flag = {"SUPPORTED": "✓", "CONTRADICTED": "✗", "NOT_FOUND": "?"}.get(v.label, "?")
                    f.write(f"  - {flag} {v.claim}\n")
            f.write(f"- Latency: {r.elapsed_s:.2f} s\n\n")
            f.write(f"**Answer:**\n\n```\n{r.answer.strip()}\n```\n\n")


# --------------------------------------------------------------------------- #
# Retrieval-quality pass (separate from end-to-end Q&A)
# --------------------------------------------------------------------------- #
def run_retrieval_eval(bot: X1025Chatbot, k: int = 4) -> RetrievalSummary:
    """Run the held-out IR test set against the current vector store."""
    results: List[RetrievalResult] = []
    for case in RETRIEVAL_CASES:
        hits = bot.layer1.retrieve(case.question)
        chunks = hits.get("documents", [])
        sources = [m.get("source", "") for m in hits.get("metadatas", [])]
        results.append(eval_retrieval_case(case, chunks, sources, k=k))
    return aggregate_retrieval(results)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-judge", action="store_true",
                    help="Skip LLM-as-judge scoring.")
    ap.add_argument("--skip-verify", action="store_true",
                    help="Skip the faithfulness verifier.")
    ap.add_argument("--skip-retrieval-eval", action="store_true",
                    help="Skip the held-out Recall@K / MRR pass.")
    ap.add_argument("--report", default="docs/eval_report.md",
                    help="Path to write the markdown report.")
    args = ap.parse_args()

    bot = X1025Chatbot()
    bot.setup()
    judge    = None if args.skip_judge  else Judge(bot.llm)
    verifier = None if args.skip_verify else Verifier(bot.llm)

    # Retrieval-quality pass first (it's fast and doesn't need the judge)
    retrieval_summary: Optional[RetrievalSummary] = None
    if not args.skip_retrieval_eval:
        print(f"\n[retrieval] running held-out IR set ({len(RETRIEVAL_CASES)} cases)...")
        retrieval_summary = run_retrieval_eval(bot, k=bot.cfg.top_k)
        print(f"[retrieval] mean Recall@{retrieval_summary.k}={retrieval_summary.mean_recall:.2f} "
              f"MRR={retrieval_summary.mean_mrr:.2f} "
              f"source-hit={retrieval_summary.source_hit_rate:.2f}")

    # End-to-end pass
    print(f"\n[end-to-end] running {len(EVAL_SET)} cases...")
    results: List[CaseResult] = []
    for i, case in enumerate(EVAL_SET, start=1):
        print(f"[{i}/{len(EVAL_SET)}] {case.question}")
        results.append(run_case(bot, judge, verifier, case))

    print_summary(results)
    write_report(args.report, results, retrieval_summary)
    print(f"\nWrote report to {args.report}")


if __name__ == "__main__":
    main()
