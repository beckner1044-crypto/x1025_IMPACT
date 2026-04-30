# x1025 Maritime AI — Handover

**Prepared for:** x1025
**By:** Beckner, Ismael — IMPACT Program, UMass Boston Venture Development Center

This document describes the prototype that has been delivered, what it can
do today, how to put it into a pilot, and what comes after.

---

## What you have

A working two-layer AI assistant for ship management, deployable to a
single vessel today, runnable at $0 on the Groq free tier.

**Layer 1 — SMS / ISM procedures.** The crew or office asks a procedural
question in plain English; the assistant retrieves the relevant
section(s) of the Safety Management System and returns an answer with
inline citations to the source ISM document and section. Example: the
question "what is the procedure for releasing the fixed CO2 system in
an engine room fire?" returns the Master / Chief Engineer authorisation
chain, the head-count requirement, the 20-second pre-alarm, and the
opening-closure check, with `[1]` and `[2]` citations to
`02_fire_emergency.md`.

**Layer 2 — operational queries against your database.** The assistant
recognises questions about live vessel data and dispatches one of five
deterministic tools:

- `get_vessel_eta(vessel)` — destination and ETA from the latest noon
  report.
- `get_fuel_rob(vessel)` — HFO and MGO remaining on board, plus 24-hour
  consumption.
- `get_speed_performance(vessel, days=14)` — actual speed and
  consumption against charter-party warranty over a configurable window.
- `get_certificates_expiring(within_days=90, vessel=None)` — fleet-wide
  or per-vessel expiry alerts.
- `list_vessels()` — fleet snapshot with type, DWT, and CP terms.

Each tool wraps a SQL query. The LLM picks the tool and emits its
arguments as JSON; the arguments are validated against a per-tool
schema before any SQL runs. There is no free-form text-to-SQL.

**Router.** A short LLM classifier with a regex fallback decides whether
the question is procedural, operational, both, or out of scope. Mixed
questions ("Vessel X has an expired certificate; what does the SMS say
to do?") trigger both layers and a synthesis step.

**Production rails.** Four things that came out of the build that I
think are worth keeping for a real deployment:

- **Faithfulness verifier.** Every generated answer is decomposed into
  atomic claims, and each claim is checked against the retrieved
  evidence. If support drops below a configurable floor, the user sees
  a safe fallback ("I don't have enough verified information to answer
  that confidently — please consult the SMS directly") and the original
  answer is logged for review rather than shown to the user.
- **Append-only audit log** at `logs/audit.jsonl`. Every answer
  (including refusals) writes a JSONL row capturing question, answer,
  route, retrieved sources, tool arguments, and per-claim verifier
  verdicts, foreign-keyed to a feedback table by UUID.
- **PII redaction at the persistence boundary.** Vessel names, IMO
  numbers, coordinates, emails, and phone numbers are scrubbed before
  anything is written to disk. Vessel-name matching is driven by the
  actual fleet registry rather than a regex pattern, which means it
  catches "Aurora" alone (not just "MV Aurora") and never produces
  false positives on text like "MS Office".
- **Crew feedback.** The Gradio UI has thumbs-up / thumbs-down buttons
  and an "Explain this answer" panel that surfaces per-claim verifier
  verdicts. Feedback is stored in SQLite and joins back to the audit
  row, so the monthly review of low-confidence answers is one indexed
  query.

## How to deploy

### Free-tier path

This is the recommended starting point for the IMPACT phase and an
initial single-vessel pilot.

```bash
git clone <the-repo>
cd x1025_prototype
pip install -r requirements.txt
python setup_data.py

export LLM_PROVIDER=groq
export GROQ_API_KEY=<your key from https://console.groq.com>
python app.py
```

This serves the Gradio interface on `http://localhost:7860`. Default
model is Llama 3.1 8B Instant on Groq's free tier, which gives 14,400
requests per day at no cost and no credit card. At a workload of 30
queries per vessel per day this supports any single-vessel pilot
indefinitely; capacity questions only become real beyond 200 vessels.

### Switching LLM providers

The LLM is provider-agnostic. The `.env.example` file shows the four
supported targets:

```
LLM_PROVIDER=groq        # default — free, Llama 3.1 8B
LLM_PROVIDER=github      # GitHub Models — free for experimentation only
LLM_PROVIDER=anthropic   # Claude Haiku 4.5 — paid, ~$0.10/MTok in
LLM_PROVIDER=local       # self-hosted Mistral-7B — needs a GPU
```

Switching providers is a one-line change in your environment. There is
no application code to rewrite. This means free-tier policy risk is
*policy* risk, not architecture risk.

### Wiring to your real database

The Layer 2 tools all take a `db_path` argument and use plain SQL. To
point them at the real x1025 system of record:

1. Confirm the schema or share a read-replica DSN with me.
2. The five tool functions in `x1025/layer2.py` are each ~10 lines of
   SQL each — adapting them to your real schema is mechanical.
3. The fleet registry used by the redactor is loaded from the same DB
   via `redactor_from_db(db_path)`, so it picks up your real vessel
   names automatically.

I'd recommend starting against a read-only replica.

### Wiring to your real ISM corpus

The seed corpus in `data/ism_docs/` is five sample procedures. To swap:

1. Drop the real ISM markdown files (or convert from Word/PDF) into
   `data/ism_docs/`.
2. Run `make clean && make setup` to rebuild the vector store.
3. Sanity-check retrieval against a few known questions before opening
   the UI to crew.

The chunker is heading-aware and section-tagged, so well-structured
ISM documents (with `# Heading` style sections) chunk cleanly. If your
SMS is delivered as PDF, conversion to Markdown via `pandoc` or a PDF
library is a one-time preprocessing step.

## What the pilot looks like

A 4-week single-vessel pilot would, in priority order:

1. **Week 1 — wire-up.** Real ISM documents replace the seed corpus;
   one Layer 2 tool is pointed at your real DB; basic smoke tests pass.
2. **Weeks 2–3 — real usage.** Master and Superintendent for one vessel
   use the assistant for daily questions. Audit log accumulates real
   queries. Thumbs-down feedback identifies failure modes.
3. **Week 4 — measurement and report.** We run the evaluation harness
   against the real corpus, weight retrieval quality on the actual
   questions you got, and produce a weekly eval report comparing
   retrieved vs. expected answers.

The deliverables out of the pilot are: an eval report against real data,
a list of the top failure modes from the thumbs-down feedback, and a
prioritised list of the tooling and schema changes needed for a multi-
vessel rollout.

## What's deferred and what to decide before scaling

Four decisions are flagged in ADR-009 as deliberately not made yet —
because the right answer depends on information that comes out of the
pilot, not from the prototype:

- **Per-vessel access control.** The current Layer 2 sees the whole
  fleet. A multi-vessel deployment needs Master / Superintendent /
  Admin scopes that constrain which `vessel_id` values each user can
  query. This is straightforward to add but the right shape depends on
  your actual user roles.
- **Multi-tenant isolation.** One ChromaDB collection per customer,
  separate SQLite (or DB) per customer. Worth doing once a second
  customer is on the table; over-engineered before then.
- **Final LLM-provider choice.** Depends on fleet size (see the cost
  model in `docs/cost_model_report.md`) and on the data-residency
  conversations with x1025's customers. The provider-agnostic
  abstraction means this is reversible.
- **Free-form text-to-SQL.** Worth adding when there's a concrete
  analytics use case the five fixed tools can't cover, and when the
  audit / sandbox infrastructure for evaluating LLM-emitted SQL is in
  place. Premature otherwise.

## Costs at scale

From `docs/cost_model_report.md` (April 2026 pricing), at the workload
assumption of 30 queries per vessel per day:

| Fleet size | Daily queries | Cheapest path                          | Monthly cost |
|------------|---------------|----------------------------------------|--------------|
| 10 vessels | 300           | Groq free tier                         | $0           |
| 50 vessels | 1,500         | Groq free tier                         | $0           |
| 200 vessels| 6,000         | Groq free tier *or* L4 self-host       | $0 to $324   |
| 500 vessels| 15,000        | L4 self-host (Mistral-7B on rented GPU)| ~$324        |

The crossover at ~200 vessels is the point at which self-hosting becomes
cheaper than per-token API billing. The cost model assumes a fairly
average input/output token mix; the actual ratio against your real
question distribution is something the pilot will measure.

## What I'd ask from x1025 to take this further

In rough order of leverage, none blocking:

1. **10–20 anonymised real ISM procedures** to replace the seed corpus.
2. **A schema sketch or read replica** of the x1025 system of record.
3. **~50 anonymised real user questions** to retune the router and
   chunker against actual usage.
4. **A 4-week single-vessel pilot window** with weekly eval reports
   back to you.

If any subset of those is feasible, we can put work against it. None are
needed for the IMPACT review itself.

## Where to look in the repo

- **Run it:** `make help` lists every workflow. `make demo` is the
  Gradio UI; `make test` runs the 49-test suite in under a second.
- **Architecture decisions:** `docs/ADR.md` — 13 ADRs covering every
  consequential choice (RAG vs. fine-tune, tool dispatch vs. text-to-
  SQL, embedding model, LLM provider strategy, verifier, audit log).
- **Cost model:** `docs/cost_model_report.md` — full projections at
  10/50/200/500 vessels with current April 2026 API pricing.
- **Evaluation:** `docs/eval_report.md` is a sample run from the
  sandbox; `make eval` regenerates it against your configured LLM.
- **Code entry point:** `x1025/chatbot.py` — the `X1025Chatbot` class
  is what `app.py` and `cli.py` both wrap.

## Contact

Beckner, Ismael — IMPACT Program, UMass Boston VDC.
Repository: github.com/haehn/aicore.boston/IMPACT/code/x1025.
