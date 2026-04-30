# x1025 — maritime AI assistant

An AI assistant for maritime ship management that answers two distinct kinds
of question with one chat interface:

1. **Procedural questions** drawn from the vessel's Safety Management System
   ("what is the procedure for releasing the fixed CO2 system?") via
   retrieval-augmented generation with inline citations to the source ISM
   document and section.

2. **Operational questions** drawn from the company's system of record
   ("what's the ETA for MV Boreas? Is it meeting charter-party speed?") via
   tool dispatch over a SQLite mock — five deterministic tools, schema-
   validated, no free-form text-to-SQL.

Built for the IMPACT program at the UMass Boston Venture Development Center,
in partnership with [x1025](https://x1025.com).

```
┌───────────┐   ┌─────────┐    ┌─────────────┐    ┌──────────────┐
│ user      │ → │ router  │ →  │ Layer 1: RAG│ →  │ verifier +   │
│ question  │   │ (LLM +  │    │   over SMS  │    │ confidence   │
└───────────┘   │  rules) │ ↘  ├─────────────┤ ↗  │ floor +      │
                └─────────┘    │ Layer 2:    │    │ audit log    │
                               │   tools     │    └──────────────┘
                               └─────────────┘
```

## Quickstart (free-tier path, $0)

```bash
git clone <this-repo> && cd x1025_prototype
pip install -r requirements.txt
python setup_data.py                          # build the SQLite mock

export LLM_PROVIDER=groq
export GROQ_API_KEY=<your key from https://console.groq.com>
python app.py                                  # opens http://localhost:7860
```

The default Groq free tier (Llama 3.1 8B Instant) gives you 14,400 requests
per day at $0. The LLM is provider-agnostic — see the `LLM_PROVIDER` line in
`.env.example` to switch between Groq, GitHub Models, Anthropic, or a local
Mistral instance.

## Common workflows

```bash
make help          # list every target
make setup         # build the SQLite mock and ingest the SMS corpus
make demo          # launch the Gradio UI
make cli           # launch the REPL CLI
make test          # run the 49-test pytest suite (~1 second, no LLM)
make eval          # full evaluation; writes docs/eval_report.md
make eval-fast     # eval without judge/verifier (components only)
make cost          # regenerate docs/cost_model_report.md
make clean         # remove caches and the SQLite mock
```

## Repository layout

```
x1025_prototype/
├── README.md                       this file
├── Makefile                        common workflows
├── requirements.txt
├── .env.example                    Groq / GitHub / Anthropic / local
│
├── app.py                          Gradio web UI
├── cli.py                          REPL CLI
├── setup_data.py                   builds data/x1025.db (SQLite mock)
├── evaluate.py                     evaluation harness — 5 metrics + report
├── cost_model.py                   cost projections at 10/50/200 vessels
│
├── x1025/                          the package itself
│   ├── core.py                     device, Embedder, VectorStore, LLM
│   ├── llm_providers.py            provider-agnostic LLM client
│   ├── layer1.py                   SMS RAG (chunking, retrieval, citations)
│   ├── layer2.py                   OpsAgent + tool registry + schema validation
│   ├── router.py                   QueryRouter (LLM + heuristic fallback)
│   ├── faithfulness.py             claim extraction + per-claim verifier
│   ├── retrieval_eval.py           held-out IR set, Recall@K / MRR / P@K
│   ├── redact.py                   registry-driven PII redaction
│   ├── audit.py                    append-only JSONL audit log
│   ├── feedback.py                 SQLite thumbs-up/down store
│   └── chatbot.py                  X1025Chatbot (orchestrates everything)
│
├── data/
│   └── ism_docs/                   5 sample ISM markdown procedures
│
├── docs/
│   ├── ADR.md                      13 architecture decision records
│   ├── PROJECT_REPORT.md           course-style writeup for the professor
│   ├── HANDOVER.md                 deployment guide for x1025
│   ├── SPEAKER_NOTES.md            10-minute presentation script
│   ├── cost_model_report.md        generated cost projections
│   └── eval_report.md              generated evaluation results
│
├── tests/                          pytest suite (49 tests, ~1 second)
│   ├── conftest.py                 shared fixtures (tmp DB, tmp logs dir)
│   ├── test_redact.py
│   ├── test_audit_feedback.py
│   ├── test_layer2_validation.py
│   └── test_metrics_and_parsers.py
│
└── scripts/
    └── run_eval_with_stub.py       eval against a stub LLM (no GPU/network)
```

## What's in here that wasn't in the original proposal

The original proposal asked for the two-layer architecture, a query router, a
cost model, and ADRs. Those are all here. On top of that, four production-
hardening additions came out of the build:

- **Faithfulness verifier** (ADR-010). Decomposes each answer into atomic
  claims, checks each against retrieved evidence, applies a confidence
  floor. Runtime guard against hallucinated procedures.
- **Tool-contract validation** (ADR-011). Every Layer 2 tool declares a
  schema; LLM-emitted args are validated and coerced at the tool boundary.
- **Audit log + feedback + redaction** (ADR-012). Every answer persisted to
  JSONL with PII scrubbed; thumbs-up/down foreign-keyed to the audit row;
  vessel names redacted via the actual fleet registry, not a brittle regex.
- **Retrieval-quality metrics** (in `x1025/retrieval_eval.py`). Held-out
  ISM Q/A set with explicit answer-spans drives Recall@K, Precision@K, MRR
  — useful for measuring chunker or retriever changes in isolation.

See `docs/ADR.md` for the full reasoning behind each decision.

## For different audiences

- **Professor / academic review** → start with `docs/PROJECT_REPORT.md`.
  Course-style writeup of methodology, decisions, evaluation, and what was
  learned along the way.
- **x1025 / industry deployment** → start with `docs/HANDOVER.md`. What's
  shipped, how to deploy, what the pilot phase looks like, what's next.
- **Reviewers who want to read code** → `x1025/chatbot.py` is the entry
  point; everything else is one level down.
- **Reviewers who want to run the demo** → `make setup demo`.

## License

MIT. See LICENSE.
