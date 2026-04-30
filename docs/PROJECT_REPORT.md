# x1025 Maritime AI Assistant — Project Report

**Authors:** Beckner, Ismael
**Program:** IMPACT, UMass Boston Venture Development Center
**Industry partner:** x1025 (maritime ship management)


---

## 1. Problem statement

Vessel ship-management companies operate at the intersection of two
information silos that today are kept entirely separate:

1. The **Safety Management System (SMS)** — a static, audited, regulator-
   facing body of ISM Code procedures running to thousands of pages per
   vessel: fuel transfer protocols, fire emergency response, watchkeeping
   handover, certificate management, man-overboard recovery, and so on.
2. The **operational system of record** — a dynamic, vessel-by-vessel
   relational database holding noon reports, fuel ROB by grade, ETAs and
   destinations, certificate expiries, charter-party warranties, and the
   stream of daily updates from each vessel.

A Master, Superintendent, or charterer who needs to act on a question that
spans both — for example, *"Vessel X has an expired Safety Radio
certificate. What does the SMS say we do, and which port can we reach
before then?"* — currently has to consult the SMS document by hand and
then run a SQL query against the operational DB, then synthesise the
answer themselves. The same lookups happen every day across every vessel.

The proposal from x1025 was to build an AI assistant that unifies both
information sources behind a single chat interface, with the
non-negotiable constraint that procedural answers must be traceable to
their source ISM document and operational answers must come from the live
system of record (not from the LLM's training data, which is wrong and
out of date).

## 2. Approach

The architecture we converged on splits the problem into three
independently testable components:

- **Layer 1: SMS RAG.** A retrieval-augmented generation pipeline over
  the ISM corpus. Documents are chunked along section headings, embedded
  with a sentence-transformer (`all-MiniLM-L6-v2`), stored in ChromaDB,
  and queried with the user's question. Retrieved chunks are passed to
  the LLM with a strict instruction to answer only from the provided
  context and to emit inline citations as `[1] [2]` referencing the
  retrieved chunks.
- **Layer 2: Operational tool dispatch.** Five deterministic tools
  (`get_vessel_eta`, `get_fuel_rob`, `get_speed_performance`,
  `get_certificates_expiring`, `list_vessels`), each wrapping a SQL
  query. The LLM picks the tool and emits its arguments as JSON; the
  arguments are validated against a per-tool schema; the SQL runs; the
  raw result is handed back to the LLM to be phrased in natural
  language.
- **Router.** A lightweight LLM classifier with a regex fallback that
  decides whether a question is procedural, operational, both, or out of
  scope. For "both" queries the chatbot runs Layer 1 and Layer 2 in
  parallel and synthesises the two answers.

The decision to use **tool dispatch instead of free-form text-to-SQL** is
the most consequential architectural choice. Text-to-SQL would have been
more flexible — any new question would work without a new tool — but
opens a sizeable risk surface: hallucinated joins, malformed `WHERE`
clauses, unintended `DELETE` statements, and the difficulty of validating
that the LLM's emitted SQL matches the user's intent. The five fixed
tools cover the four query types named in the proposal, are easy to
audit, and are individually unit-testable. ADR-002 records the full
reasoning.

## 3. Decisions and trade-offs

The repository contains 12 Architecture Decision Records (`docs/ADR.md`).
The five most consequential:

| ADR | Decision | Trade-off |
|-----|----------|-----------|
| 001 | RAG over fine-tuning for the SMS layer | Faster iteration, automatic propagation of SMS updates; pays a per-query retrieval cost. |
| 002 | Tool dispatch over text-to-SQL for Layer 2 | Smaller risk surface, explicit per-tool tests; new query types require new tools. |
| 003 | Mistral-7B-Instruct-v0.3 as the prototype LLM | Open weights, runs on a consumer GPU; smaller than GPT-4-class so completeness is lower. |
| 008 | Provider-agnostic LLM with Groq free tier as the default | Pilot can run at $0; couples short-term to a third party's free-tier policy. |
| 010 | Faithfulness verifier with confidence floor | Catches hallucinated procedures at runtime; costs extra LLM calls per answer. |

Each ADR records context, the decision, consequences, and the
alternatives we considered and rejected. Two patterns emerged in writing
them: first, that the right architectural choice was usually constrained
by the *audit and trust* requirements of the maritime ops context rather
than by raw model performance; and second, that "deferred" was an
honourable answer for several questions (per-vessel access controls,
final LLM provider, multi-tenant isolation) where the right call depends
on real customer information we did not have.

## 4. Evaluation

The evaluation harness in `evaluate.py` exercises every component of the
pipeline and produces a structured Markdown report. It covers five
metrics:

1. **Router accuracy** on a labelled question set (does the right layer
   get called?).
2. **Retrieval quality** as Recall@K, Precision@K, and MRR over a held-
   out set of 10 ISM Q/A pairs in `x1025/retrieval_eval.py`. Each test
   case lists the answer spans the retriever should surface, so a
   match against the right *section* counts more than against the right
   *file*.
3. **Layer 2 tool selection** — given a labelled question, did the LLM
   pick the expected tool with valid arguments?
4. **End-to-end LLM-as-judge** scoring on faithfulness, relevance, and
   completeness on a 1–5 scale.
5. **Per-claim faithfulness verification** — atomic claim extraction
   from the answer, then SUPPORTED / CONTRADICTED / NOT_FOUND verdicts
   against the retrieved evidence.

The report includes a clear caveat worth flagging explicitly: the LLM-
as-judge in the prototype shares the model under test (the same Mistral
weights are scoring Mistral output), so absolute scores are optimistic
and the numbers are most useful as *relative* signals when iterating on
prompts, chunking, or retrieval-k. Mitigation is to point the judge at
a stronger model — the `Judge` class in `evaluate.py` is the only thing
that needs to change.

In addition to the offline harness, the project has a **49-test pytest
suite** that runs in under a second with no LLM, no GPU, and no network.
It covers the deterministic components (redaction, audit, feedback,
schema validation, retrieval metrics, faithfulness parsers) and is what
we'd run on every commit if this were a CI-enabled project.

## 5. Lessons learned

A few things we changed our minds about during the build are worth
recording.

**LLM reviewers will overwhelmingly recommend more features, but most
features make a demo worse.** Over the course of the project we solicited
critique from several different language models. The pattern was
striking: each one converged on a list of "must-add" features —
rerankers, tiered routing, semantic caching, asynchronous tool dispatch,
offline quantised fallbacks — many of which were premature for a 5-
document corpus and a single-vessel pilot. Treating those reviews as
*input* rather than *prescription* was a recurring discipline. The
high-value suggestions (hybrid retrieval, chain-of-verification,
audit logs, registry-driven redaction) made it in; the rest stayed out
and the README is shorter for it.

**The right metric is not what the LLM produces but what survives the
verifier.** Early in the project the evaluation was an LLM-as-judge with
a 1–5 score. That number was both flattering and uninformative. Adding a
per-claim verifier — extract claims, check each against the retrieved
evidence, label SUPPORTED / CONTRADICTED / NOT_FOUND — changed the
question from "did the model sound good?" to "is each thing it said
backed by something?" It also gave the runtime a confidence floor: an
answer with three NOT_FOUND claims out of four gets replaced with a safe
fallback rather than shipped to the Master.

**Bad regexes are worse than no privacy controls.** Several of the LLM
reviewers proposed a vessel-name redactor of the form
`\b(MV|MS|MT)\s+[A-Z][a-z]+\b`. That pattern would silently redact
"MS Office" in a log line *and* miss "Aurora" without the prefix —
double-failure mode. Pulling the actual fleet registry out of the
SQLite mock at startup and matching against the real names is harder to
build, easier to verify, and produces a redactor with 100% test recall
on the seed fleet and zero false positives on the bug-shaped negative
cases. ADR-012 records the alternative we rejected.

**Decisions deferred are decisions made.** ADR-009 lists four items we
explicitly chose not to decide (per-vessel access, final LLM provider,
multi-tenant isolation, free-form text-to-SQL). Writing them down rather
than glossing over them turned out to be useful: in two of them
(provider, access control) the right answer depends on information we
don't have yet, and in the other two (multi-tenant, text-to-
SQL) building them now would over-fit the prototype to the wrong shape
of problem. "Not yet, and here's why" is a real engineering output.

## 6. What did not get built, and why

Four things we considered and rejected during the
build, each with a brief reason:

- **A Cohere reranker.** The free tier is small (1,000 calls/month on
  the trial key, expires) and adds a second network dependency on the
  critical path. For a 5-document corpus where hybrid retrieval already
  does the heavy lifting, a reranker is over-engineered.
- **Tiered routing (small model for "simple" questions, big model for
  "complex" ones).** Misclassification routes a safety-critical
  question to the weaker model; "we used the small model on the engine-
  room fire question" is not a defensible failure mode in maritime ops.
- **Asynchronous tool dispatch.** Premature optimisation for a chatbot
  serving one user at a time.
- **Offline "Pocket RAG" with a quantised local LLM.** Adds ~5 GB of
  dependencies (`llama-cpp-python`, GGUF model, FAISS) for a use case
  ("vessel loses VSAT") that wasn't in the proposal and isn't in the
  pilot scope.

If x1025 commits to a 200+ vessel deployment, several of these become
worth revisiting; at prototype scale they would have been distractions.

## 7. What we would do next

In priority order, if the project continued:

1. **Wire to real x1025 data.** Each Layer 2 tool takes a `db_path`
   parameter; pointing them at a real schema is a mechanical change.
   The seed ISM corpus is similarly trivial to swap.
2. **Run the eval against Groq + the real corpus.** Numbers in the
   sample report are from a deliberately weakened sandbox stack and
   should improve materially against the production stack.
3. **Per-vessel access controls** (ADR-009 deferred item). The current
   `OpsAgent` sees the entire fleet; a real deployment needs Master /
   Superintendent / Admin scopes.
4. **Hybrid retrieval (BM25 + dense).** Maritime queries include exact
   regulatory codes ("MARPOL Annex VI", "SOLAS II-2") where keyword
   match outperforms vector match. This was on the cut list for the
   prototype but is the right next thing to add when chasing retrieval
   quality.

## 8. Acknowledgements

We want to acknowledge:

- **Hash Harshvardhan** of x1025 for the project itself, the proposal
  document, and his patience with the inevitable mid-build questions.
- **The IMPACT program at the UMass Boston VDC** for the structure that
  made this possible.
- **The faculty advisors** who reviewed early architecture sketches.

The fact that a student in the IMPACT program could ship a real maritime
AI prototype with a documented architecture, a 49-test suite, and an
evaluation harness in eight weeks is a function of the program; the
project is the visible output but the program is what produced it.

---

## Appendix A — File index

For evaluators who want to dig in, the most important reading paths:

- **For architecture:** `docs/ADR.md` (13 records) → `x1025/chatbot.py`
  (orchestrator) → `x1025/layer1.py` and `x1025/layer2.py` (the two
  layers) → `x1025/router.py`.
- **For evaluation methodology:** `evaluate.py` → `x1025/retrieval_eval.py`
  → `x1025/faithfulness.py` → `docs/eval_report.md` (sample run).
- **For safety / production rails:** `x1025/redact.py` → `x1025/audit.py`
  → `x1025/feedback.py` → ADR-010, ADR-011, ADR-012.
- **For cost analysis:** `cost_model.py` → `docs/cost_model_report.md`.

## Appendix B — How to reproduce

```bash
make install setup test          # 49 tests pass in <1 second, no LLM needed
make eval                        # full evaluation, requires LLM_PROVIDER set
make demo                        # Gradio UI on http://localhost:7860
```

## Appendix C — Mapping to the proposal's Section 4 deliverables

The proposal's Section 4 ("Technical Architecture and Key Design Questions")
defines the architectural deliverables of the project. This appendix maps
each of its sub-sections and open questions to the corresponding decision
in this prototype, so a reviewer can verify coverage at a glance.

### Section 4.1 — RAG vs. fine-tuning

The proposal recommended RAG as the primary architecture for both layers,
with fine-tuning as a supplementary exercise for tone and vocabulary. The
deliverable specified was *"a structured analysis document comparing RAG
and fine-tuning outcomes."*

**This is captured in ADR-001** (`docs/ADR.md`). RAG was selected as the
primary architecture; fine-tuning was rejected because the SMS corpus is
authoritative and must be cited accurately, fine-tuning has poor knowledge
freshness (re-training on every SMS update), and a small fine-tuned model
loses the factual grounding that maritime ops actually requires. The ADR
records the trade-off matrix the proposal asked for.

### Section 4.2 — LLM hosting (cloud API vs. self-hosted)

The proposal recommended starting with a cloud API for rapid prototyping
while designing the abstraction layer to be LLM-agnostic from day one. The
deliverable specified was *"a cost model comparing cloud API costs vs.
self-hosted deployment costs at three traffic scenarios (10 ships, 50
ships, 200 ships), with a break-even analysis."*

**Both deliverables exist.** ADR-008 records the provider-agnostic decision
and lists the four supported targets (`local`, `groq`, `github`,
`anthropic`). The `cost_model.py` script and its generated
`docs/cost_model_report.md` produce the three-tier scenario analysis the
proposal asked for, with the Groq free tier as a fourth comparison
baseline. The break-even point between cloud APIs and self-hosted GPU
falls at roughly 200 vessels under our workload assumption, matching the
proposal's prediction.

### Section 4.3 — RAG framework (LlamaIndex vs. LangChain)

The proposal recommended LlamaIndex for Layer 1 and LangChain agent
patterns for Layer 2.

**This decision went the other way, and the reasoning is in ADR-013.** We
chose to build directly on the ChromaDB Python client and a custom
~50-line tool dispatcher, rather than adopt either framework. The short
version: at the scale of this prototype (5 documents, 5 tools, single-turn
queries), the frameworks add boilerplate without subtracting code, and
their high churn rate creates a real risk for handoff to industry. ADR-013
records the trade-offs in detail and explicitly notes that LangGraph
becomes a real candidate if and when Layer 3 (autonomous agents) is built.

This is the one place where the prototype's architecture diverges from the
proposal's recommendation. The deviation is documented and defensible.

### Section 4.4 — Vector database selection

The proposal recommended Chroma for development and migration to Qdrant or
pgvector for production.

**Captured in ADR-004.** Chroma is the prototype's choice. The reasoning
on a future production migration is included as a deferred decision in
ADR-009 — Qdrant is the natural successor for a Docker-deployable
production target.

### Section 4.5 — Open research questions

The proposal listed seven open questions students would research before
writing code. Each is addressed below, with a pointer to where the work
lives in the repository.

| # | Question (paraphrased) | Where addressed | Status |
|---|------------------------|-----------------|--------|
| 1 | Chunking strategy: chunk size, procedural context preservation, retrieval impact | `x1025/layer1.py` (heading-aware chunker, ~400 char target with section metadata) | ✅ Addressed |
| 2 | Embedding model selection | ADR-005 (`all-MiniLM-L6-v2` chosen, with reasoning and the alternatives considered) | ✅ Addressed |
| 3 | Hybrid search (vector + BM25) for exact terminology like "VLSFO ROB", "AIS", "ISM DOC" | Considered and rejected for prototype scope; see "On Q3 and Q4" below | ⚠️ Documented but not implemented |
| 4 | Structured vs. unstructured retrieval for Layer 2 (text-to-SQL vs. natural language summary) | ADR-002 (chose tool dispatch — a third option not in the proposal) | ✅ Addressed via tool dispatch |
| 5 | Evaluation methodology, ground truth, building an eval dataset | `evaluate.py`, `x1025/retrieval_eval.py`, `x1025/faithfulness.py`, `docs/eval_report.md` | ✅ Addressed |
| 6 | Security and role-based access (which users can query which vessel data) | ADR-009 (deferred — depends on x1025's real user roles) | ⚠️ Deferred with reasoning |
| 7 | Prompt engineering vs. system architecture vs. LLM choice — where does performance come from? | Eval harness designed for A/B comparison across all three; LLM-as-judge documented in `evaluate.py` with the same-model-bias caveat | ✅ Methodology in place |

### On Q3 and Q4 — the non-obvious choices

Two of the seven questions deserve more than a row in a table.

**Q3 (hybrid search).** Maritime queries do contain exact-match terminology
where pure vector search underperforms — "MARPOL Annex VI", "ISM DOC",
"VLSFO ROB" are real examples. Adding BM25 keyword search alongside vector
search is a textbook fix and the proposal called it out specifically.

We considered it and chose not to implement it for the prototype. The
reasoning: at the scale of a 5-document seed corpus, BM25 is over-
engineered. The right place for a hybrid retriever is when retrieval
quality is the measured bottleneck on a real corpus — which is exactly
what the eval harness is built to measure (`x1025/retrieval_eval.py`
produces Recall@K and MRR per query). When x1025 swaps in a real ISM
corpus and retrieval scores plateau, hybrid search becomes the highest-
leverage change to add. This is recorded as a "next steps" item in
section 7 of this report.

**Q4 (structured vs. unstructured retrieval for Layer 2).** The proposal
framed this as a binary: text-to-SQL (LLM writes SQL directly) versus
pre-generated natural language summaries (the database is rendered as
narrative text and retrieved like any other document). The prototype
chose neither. ADR-002 records the third option: **tool dispatch**, where
the LLM picks one of five fixed Python functions and emits arguments as
JSON, the function runs SQL, and the LLM phrases the result.

The reasoning: text-to-SQL has a real risk surface (hallucinated joins,
malformed `WHERE` clauses, ambiguous schema interpretation) that a
maritime ops system can't tolerate; pre-generated summaries lose the
freshness that's the entire point of querying live data. Tool dispatch
gets the safety of fixed SQL queries with the flexibility of natural-
language phrasing, at the cost of needing a new tool definition for every
new query type. ADR-002 records the alternative we rejected and the
acceptance criteria for revisiting.

### Summary

Of the deliverables Section 4 of the proposal asked for, **all five
sub-section decisions are documented in ADRs**. Of the seven Section 4.5
open research questions, **five are fully addressed**, **one (Q3) is
documented and deferred with explicit reasoning**, and **one (Q6,
role-based access) is intentionally deferred until the pilot phase
because it depends on x1025's real user roles**. The single deviation
from a proposal recommendation — using neither LlamaIndex nor LangChain
in Section 4.3 — is captured in ADR-013 and is defensible on the
prototype's specific scale and lifecycle.

This appendix exists so a reviewer can verify the project's coverage of
the proposal's architectural deliverables in 60 seconds rather than
reading the entire repository.
