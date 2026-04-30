# Architecture Decision Record — x1025 AI Intelligence Layer

This document records the architectural decisions made in building the x1025
prototype. Each ADR follows the convention: **Context → Decision → Consequences →
Alternatives Considered**. Update the *Status* line if any decision is revisited.

---

## ADR-001: RAG over fine-tuning for the SMS layer

**Status:** Accepted

**Context.** Layer 1 must answer questions about the Safety Management System
(ISM procedures, checklists, emergency protocols). The corpus is curated, updates
periodically (every SMS revision cycle), and answers must be traceable to a
specific procedure for audit and regulatory reasons.

**Decision.** Use Retrieval-Augmented Generation (RAG): index the SMS corpus in a
vector database, retrieve the top-k chunks at query time, and pass them to the
LLM as context. Do not fine-tune.

**Consequences.**
- Updating an ISM procedure means re-embedding one document, not re-training a
  model. Ship operators revise SMS often; fine-tuning would create an
  unacceptable change-management burden.
- Every answer can cite the source document and section. This is required for
  regulator and Port State Control audits.
- The base model stays general-purpose, so the same LLM serves Layer 1 and
  Layer 2 without a separate fine-tune per layer.
- We pay retrieval latency (~50 ms) and a larger context window per call.

**Alternatives considered.**
- *Fine-tune on SMS Q&A pairs.* Rejected: change management, no citations,
  hallucination risk on procedures the operator is legally bound to follow.
- *Long-context only (paste the whole SMS into the prompt).* Rejected: cost
  scales linearly with corpus size, latency suffers, and there's no way for
  the model to indicate which procedure it relied on.

---

## ADR-002: Tool dispatch over text-to-SQL for the operational layer

**Status:** Accepted

**Context.** Layer 2 must answer questions against the live x1025 system of
record (vessels, daily reports, certificates, charter party terms). Free-form
text-to-SQL with a 7B local model has well-documented failure modes: hallucinated
table names, wrong joins, occasional destructive statements when tested
adversarially.

**Decision.** Expose a small fixed registry of Python tools, each wrapping a
parameterized SQL query. The LLM picks one tool and emits a JSON object with
its arguments; we execute the tool and feed the result back to the LLM for
natural-language phrasing.

**Consequences.**
- Deterministic for the question shapes that matter (ETA, fuel ROB, charter
  party performance, certificate expiry, fleet listing). Adding a capability
  is one entry in `TOOLS` in `x1025/layer2.py`.
- No risk of write-side SQL — the connection is read-only by construction.
- Easy to evaluate: tool selection accuracy is a single metric on a labeled
  set, separable from end-to-end answer quality.
- Out-of-domain questions ("aggregate fleet-wide fuel consumption by month for
  vessels under charter X") need a new tool entry — this is a feature, not a bug,
  because each new capability gets its own evaluation row.

**Alternatives considered.**
- *Free-form text-to-SQL.* Rejected for the prototype as too risky with a 7B
  model. May revisit as a *separate* "advanced analytics" surface guarded by a
  read-only sandbox, once the deterministic tools cover routine queries.
- *Retrieval over a query-template library.* Considered. Tool dispatch dominates
  it: the LLM is already classifying intent and extracting params, so an
  intermediate retrieval step adds latency without changing the surface area.

---

## ADR-003: Self-hosted Mistral-7B-Instruct as the prototype LLM

**Status:** Accepted for prototype offline mode; superseded as default by ADR-008 (provider-agnostic LLM)

**Context.** The proposal flags "cloud API vs. self-hosted LLM" as an
architecture decision. The development environment has GPU access; data
sensitivity (vessel positions, charter party terms, crew details) suggests
on-prem or VPC-isolated inference is desirable for production.

**Decision.** Use `mistralai/Mistral-7B-Instruct-v0.3` for the prototype. It
fits in 24 GB VRAM at FP16 (runs on an L4 — see the cost model), is
permissively licensed, and is the same model already in the starter code.

**Consequences.**
- Zero per-query inference cost during prototype development.
- All data stays within the development environment — important when working
  with realistic SMS extracts.
- 7B is below current frontier quality. The router and tool-selection prompts
  may need more careful crafting than they would for a Sonnet-tier model.
- Cold-start time on model load (~30 s on an A100) is real but irrelevant for
  a long-running service.

**Alternatives considered.**
- *Cloud API (Claude / GPT).* Cheaper at small scale per the cost model, but
  introduces a third-party data-residency story to negotiate with x1025
  customers, and ongoing cost scales with vessel count.
- *Larger open model (Llama-3 70B, Mixtral 8x22B).* Better quality but needs
  multi-GPU or aggressive quantization. Out of scope for the prototype; reasonable
  upgrade once an evaluation harness is producing quality numbers (see ADR-006).

---

## ADR-004: ChromaDB as the vector store

**Status:** Accepted

**Context.** Layer 1 needs a vector store. The SMS corpus is small (tens to low
hundreds of documents, hundreds to low thousands of chunks). Operational
constraints: must persist across restarts, must run in the same process as the
chatbot for the prototype, must not require a separate server.

**Decision.** Use ChromaDB in `PersistentClient` mode, storing data in
`./data/chroma/`.

**Consequences.**
- Zero infrastructure overhead. Single dependency, single directory.
- Performance is fine at SMS scale. Approximate-NN search over a few thousand
  384-dim vectors is sub-millisecond.
- Re-running the chatbot does not re-embed the corpus — `get_or_create_collection`
  reuses what's there.
- For a multi-tenant production deployment (one collection per customer or one
  per vessel group), we may need a managed vector DB. ChromaDB has Cloud and
  self-hosted server modes that are drop-in for the same client API.

**Alternatives considered.**
- *FAISS (in-memory).* Faster but no persistence story without writing one
  ourselves. Rejected.
- *pgvector.* A reasonable production target, especially if x1025 already runs
  Postgres. Rejected for the prototype because it adds a database dependency.
- *Pinecone / Weaviate / Qdrant Cloud.* Managed services. Defer the decision —
  the same `VectorStore` wrapper makes a swap mechanical.

---

## ADR-005: `all-MiniLM-L6-v2` as the embedding model

**Status:** Accepted

**Context.** Need an embedding model for the SMS corpus. Constraints: must
run on the same GPU as the LLM without contention, must produce reasonable
retrieval quality on procedure-style English text.

**Decision.** Use `sentence-transformers/all-MiniLM-L6-v2`. 384-dim output,
~80 MB on disk, runs on CPU or GPU.

**Consequences.**
- Negligible footprint and startup cost.
- Quality is well-documented on MTEB; mid-tier overall but solid on the
  short-document retrieval workloads we care about.
- 384 dim keeps the vector store small (34 chunks × 384 × 4 bytes = ~50 KB
  for the prototype corpus; even 10 000 chunks is ~15 MB).

**Alternatives considered.**
- *`bge-large-en-v1.5`.* Higher MTEB scores but ~1.3 GB and 1024 dim. Overkill
  for the corpus size; revisit if retrieval@k metrics from `evaluate.py` are
  borderline.
- *OpenAI `text-embedding-3-small`.* Excellent quality, $0.02/M tokens. Would
  run our entire SMS corpus for under $0.10. Rejected for the prototype to
  keep the system fully self-contained, but it is a strong cloud option for
  production.

---

## ADR-006: LLM classification with a heuristic fallback for routing

**Status:** Accepted

**Context.** Each incoming question must be routed to Layer 1 (SMS), Layer 2
(operational), both, or politely refused. The router runs on every query so
its latency and reliability are first-order concerns.

**Decision.** Prompt the same Mistral-7B model to emit one of four labels.
On any output that does not parse cleanly to one of those labels, fall back
to a regex-based heuristic over high-signal vocabulary (vessel names, "ETA",
"ROB", "procedure", "emergency", etc.).

**Consequences.**
- One LLM call adds ~150 input + 5 output tokens — small compared to the
  downstream RAG or tool-dispatch call.
- The heuristic fallback means a bad LLM output never breaks the pipeline.
  Worst case the router degrades to a rule-based system that still routes most
  questions correctly.
- The router can be evaluated independently of the layers — see `evaluate.py`.

**Alternatives considered.**
- *Pure heuristics.* Faster and free, but brittle on questions phrased in
  unexpected ways. Useful as a fallback, not as the primary classifier.
- *A separate small classifier (DistilBERT fine-tuned on labeled queries).*
  Better latency than an LLM call, but needs a labeled corpus we don't have
  yet. Worth revisiting once we have query logs from a pilot.
- *Skip routing — always run both layers.* Doubles cost on every query and
  wastes the router's signal for the synthesis path.

---

## ADR-007: Filesystem persistence; SQLite for the operational mock

**Status:** Accepted (prototype only)

**Context.** The prototype needs persistence for the vector store and a stand-in
for the x1025 system of record.

**Decision.** ChromaDB writes to `./data/chroma/`. The operational data lives in
`./data/x1025.db`, a SQLite file populated by `setup_data.py`.

**Consequences.**
- Single-machine, single-process — fine for the prototype, not for production.
- Each Layer 2 tool function takes a `db_path`; swapping for an authenticated
  client to the real x1025 cloud DB is mechanical.
- `setup_data.py` is the single source of truth for the mock schema, which
  means schema drift between the mock and the real x1025 DB is something we
  catch the moment we wire to production.

**Alternatives considered.**
- *Postgres in Docker.* More realistic, more setup. Defer.
- *Mock the real x1025 API directly.* Would need API specs we don't have yet.
  SQLite gives us a defensible architecture without blocking on integration.

---

## ADR-008: Provider-agnostic LLM with a free-tier default for the prototype

**Status:** Accepted

**Context.** The original prototype hard-coded Mistral-7B via `transformers`,
which makes the IMPACT demo dependent on a GPU machine (~14 GB model download,
CUDA toolchain, ~$300/month if rented). That's wrong for the demo phase: x1025
is a startup, the IMPACT submission needs to be runnable by reviewers on a
laptop, and free LLM API tiers exist that meet the workload.

**Decision.** Abstract the LLM behind a small interface (`instruct(system, user)`)
and provide multiple backends:

| Provider | Model | Cost | When to use |
|---|---|---|---|
| `local` | Mistral-7B-Instruct-v0.3 | Free + GPU | Offline / sensitive data |
| `groq` | Llama 3.1 8B Instant | Free (14,400 req/day) | **Default for IMPACT demo and pilot** |
| `github` | GPT-4o-mini | Free, low limits, prototyping-only TOS | Backup if Groq is down |
| `anthropic` | Claude Haiku 4.5 | Paid | Eval judge upgrade; production at small fleets |

Selection is via a single env var (`LLM_PROVIDER`); the rest of the code does
not change. `httpx` is the HTTP client (already a transitive dep of ChromaDB,
so no new requirement). All four backends speak OpenAI-compatible
chat/completions, so one `RemoteLLM` class serves three of them.

**Consequences.**
- IMPACT submission runs on any laptop. No GPU required. Quickstart is "get a
  free Groq key, set one env var, run."
- Production deployments are not locked to a provider. Switching from Groq to
  Claude to a self-hosted vLLM endpoint is a config change, not a code change.
- Llama 3.1 8B (Groq's default) is slightly different from Mistral-7B in
  prompt formatting and behaviour. Empirically the existing prompts work
  unchanged, but the eval harness should be re-run after a provider swap to
  confirm router accuracy and tool-pick rates.
- Free tiers are subject to unilateral change by the provider. The cost model
  now includes a $0 column with this caveat front-and-center; the
  recommendation is to plan a paid fallback before scaling beyond the pilot.

**Alternatives considered.**
- *Stay local-only.* Rejected: gates the IMPACT demo on GPU access and forces
  every reviewer to download Mistral.
- *Use the OpenAI Python SDK as the abstraction.* Pulls in a heavier dependency
  and a class hierarchy we don't need. A 100-line `RemoteLLM` over `httpx` is
  more direct.
- *Always use a remote provider.* Rejected: the local path stays useful for
  data-residency-sensitive customers and for offline development.

---

## ADR-010: Faithfulness verifier and confidence floor

**Status:** Accepted

**Context.** The original prototype generated answers from retrieved context
but did nothing to detect when the answer drifted from the evidence. The
LLM-as-judge in `evaluate.py` produces a soft 1–5 score after the fact, but
nothing acts on it at runtime — a low-confidence answer would still be shown
to the Master or Superintendent. This was previously flagged in the
deferred-decisions list ("Hallucination guard for Layer 1") and is exactly
the failure mode that matters in a maritime ops context, where a confidently
wrong procedural answer can have safety consequences.

**Decision.** Add a two-step verifier (`x1025/faithfulness.py`) that:

1. **Extracts atomic claims** from each generated answer via one LLM call.
2. **Verifies each claim** against the retrieved evidence (procedure chunks
   or Layer 2 tool result), labelling it `SUPPORTED`, `CONTRADICTED`, or
   `NOT_FOUND`.

The chatbot exposes a `verify=True, confidence_floor=0.5` toggle. When
enabled, if the supported-claim ratio is below the floor, the user-facing
answer is replaced with a safe fallback ("I don't have enough verified
information…"); the original answer and the per-claim verdicts are still
surfaced in the result dict for review and logging.

**Consequences.**
- Adds two extra LLM calls per query when verification is on (claim
  extraction + per-claim verification × N claims). At 4–6 claims per answer,
  that's roughly 5–8× the LLM-call cost of unverified mode. Off by default
  for the demo path; opt-in for production.
- Same-model bias: in the prototype the verifier shares the LLM under test,
  so the verifier inherits the model's blind spots. Documented in the
  evaluation report; mitigation is to pass a stronger model into
  `Verifier(...)` for the IMPACT final report and for production.
- Unparseable verifier output defaults to `NOT_FOUND`, biasing toward the
  safe fallback rather than auto-shipping an unverified answer.
- Resolves the "hallucination guard" item that was previously deferred.

**Alternatives considered.**
- *Retrieval-distance threshold only.* Cheaper but a hard floor on cosine
  distance doesn't catch the "right document, wrong section, plausible-
  sounding answer" failure mode. Verification reads the actual evidence.
- *NLI model (e.g. DeBERTa).* Better quality and 100× cheaper per claim, but
  adds another model dependency. Worth revisiting if verification cost
  becomes a problem; the `Verifier` class is the only place that needs to
  change.
- *No verifier; rely on the judge.* Rejected: the judge is post-hoc and
  produces soft scores; the verifier produces actionable per-claim labels
  that drive a runtime guard.

---

## ADR-011: Tool-contract validation for Layer 2

**Status:** Accepted

**Context.** Each Layer 2 tool entry previously had a human-readable `args`
field used to populate the LLM's tool-spec prompt, but the actual function
calls relied on Python's `**kwargs` to pass through whatever JSON the LLM
emitted. When the model invented an argument name, used the wrong type, or
omitted a required argument, the failure showed up as an opaque `TypeError`
inside the SQL function — far from the tool boundary, and unhelpful to debug
at runtime.

**Decision.** Add a structured `schema` field to each `TOOLS` entry
declaring argument types, required/optional, default values, and value
ranges. A `validate_tool_args` function runs at the tool boundary in
`OpsAgent.answer`, before the SQL function is called. It coerces common LLM
mistakes (e.g. `"30"` → `30`), rejects invented argument names, and raises
`ToolValidationError` with a clear message on anything else.

**Consequences.**
- The agent fails fast and informatively when the LLM emits malformed
  arguments. The user sees "I couldn't parse those arguments — could you
  rephrase?" instead of an opaque crash.
- Adding a tool is now slightly more involved: one entry must specify the
  schema as well as the description and function. This is desirable —
  forcing the schema makes the tool boundary explicit.
- The validator is a pure function with no LLM dependency, so it can be
  unit-tested deterministically.

**Alternatives considered.**
- *Pydantic models per tool.* Cleaner type hints but adds a heavyweight
  dependency for what fits in 50 lines of plain Python. Worth revisiting if
  the tool count grows to 20+.
- *Trust the LLM and catch the TypeError.* What we had. The error surface
  was wrong (deep in SQL, not at the tool boundary) and the messages were
  unhelpful.

---

## ADR-012: Audit log, feedback, and registry-driven redaction

**Status:** Accepted

**Context.** The verifier (ADR-010) makes runtime decisions about whether
to ship an answer, but those decisions vanished once the response left the
chatbot. There was no record of *what was asked, what we said, what
evidence backed it*, and no way for crew to mark an answer as wrong. For
the IMPACT pilot review and any future regulatory conversation, that
record matters. Several reviewers also flagged that maritime queries
include identifiers (vessel names, IMO numbers, positions, emails) that
shouldn't sit in plaintext audit logs.

**Decision.** Three small, composable additions:

1. **Append-only audit log** (`x1025/audit.py`). Every answer is written
   to `logs/audit.jsonl` as a self-contained record: question, answer,
   route, tool name and args, retrieved sources, verifier verdicts,
   confidence score, and a UUID. JSONL because append-only is the property
   that matters for trust — each row is a complete record, no
   transactions, no risk of mid-write corruption.

2. **Feedback table** (`x1025/feedback.py`). Thumbs-up/down stored in
   SQLite, foreign-keyed to the audit row by UUID. SQLite (not JSONL) for
   feedback specifically because it's queried more than written ("show me
   thumbs-down answers from last month sorted by route"), and indexed
   access matters for that workflow.

3. **Registry-driven redaction** (`x1025/redact.py`). The redactor pulls
   the actual vessel registry from the SQLite mock to drive vessel-name
   matching, instead of a generic pattern like `\b(MV|MS|MT)\s+[A-Z][a-z]+\b`.
   Pattern-based matching has both false positives ("MS Office" → redacted)
   and false negatives ("Aurora" without prefix → not redacted). Registry
   matching has neither. IMO numbers, coordinates, emails, and phone
   numbers use narrow patterns with context guards (a bare 7-digit number
   is only redacted if "imo" or "vessel" appears nearby). All redaction
   runs at the persistence boundary — the user sees the original answer;
   the audit log sees the redacted version.

The Gradio app surfaces all three: every answer carries an audit_id;
thumbs-up/down buttons foreign-key feedback to that id via `gr.State` (not
a module-level global, which the other reviewers proposed but breaks
across users); and an "Explain this answer" panel renders per-claim
verifier verdicts.

**Consequences.**
- Every answer becomes auditable. The "monthly review of thumbs-down
  answers" workflow (often called the improvement backlog) is supported
  out of the box: query feedback by `thumb='down'`, join to audit_id, see
  what was asked and what evidence the system used.
- Audit logging adds one synchronous disk write per answer (~1ms). This
  is fast enough to run unconditionally and is on by default.
- Verifier verdicts get persisted only when verification is on. With
  verification off, the audit log still records the answer and route but
  has no per-claim breakdown, which is fine for the demo path.
- Redaction gets accuracy on the actual fleet from the seed data. When
  x1025 swaps SQLite for their real database, `redactor_from_db(...)`
  picks up the real vessel registry automatically.

**Alternatives considered.**
- *Gradio's built-in flagging callback.* Several reviewers suggested it.
  Rejected because it's tied to `gr.ChatInterface`, which doesn't fit our
  two-pane layout (chat + evidence), and because it doesn't foreign-key to
  an audit row.
- *Pattern-only PII redaction.* Multiple LLM reviewers proposed this. Has
  false positives ("MS Office") and misses bare names ("Aurora"). The
  registry approach is more accurate at the cost of one SQL query at
  startup — a worthwhile trade.
- *Module-level `last_result` global for the Gradio session.* Multiple
  reviewers proposed it. Breaks with multiple users or page refreshes.
  `gr.State` is the right tool.
- *Subprocess-based health-check script.* Proposed by reviewers. Each
  invocation would re-load the model (~30s on local). The existing
  `evaluate.py --skip-judge --skip-verify` is the in-process equivalent
  and runs in seconds.

---

## ADR-013: RAG framework selection — using neither LlamaIndex nor LangChain

**Status:** Accepted

**Context.** The original IMPACT proposal (Section 4.3) recommends evaluating
LlamaIndex and LangChain as RAG orchestration frameworks. Specifically, it
suggests LlamaIndex for Layer 1 (document retrieval over the SMS corpus)
"given its superior out-of-the-box RAG performance and gentler learning
curve," and LangChain's agent/tool-calling patterns for Layer 2 (structured
database queries). The proposal frames this as a decision students will
make and document — not as a prescription.

The selection step is the deliverable; the conclusion can legitimately go
either way as long as the reasoning is sound.

**Decision.** Use neither framework. Layer 1 is built directly on the
ChromaDB Python client (`x1025/layer1.py`). Layer 2's tool dispatch is a
~50-line custom router (`x1025/layer2.py`) that picks one of five fixed
tools from a Python dict and validates LLM-emitted JSON against a per-tool
schema (ADR-011).

**Reasoning.**

LlamaIndex and LangChain are abstractions over the underlying primitives
(vector store, embedder, LLM, prompt templates, tool dispatch). Abstractions
earn their place when (a) they reduce code, (b) they make swapping
implementations easier, or (c) they expose advanced features that would be
hard to write from scratch. None of those applied here:

- *Code reduction.* The Layer 1 retrieval path is six lines of ChromaDB
  client code: encode the question, query the collection with `n_results=k`,
  return documents and metadata. Wrapping that in `VectorStoreIndex` and
  `RetrieverQueryEngine` adds boilerplate, not subtraction.
- *Swappability.* The proposal's own recommendation — design the LLM
  abstraction to be provider-agnostic from day one — is satisfied by
  `x1025/llm_providers.py`, a 170-line provider-agnostic client supporting
  local Mistral, Groq, GitHub Models, and Anthropic. LangChain's LLM
  abstraction would add a layer of indirection without changing the
  swappability story.
- *Advanced features.* The features LangChain is famous for — agent loops,
  multi-step chain orchestration, conversational memory, complex tool
  graphs — are not needed for a single-turn router-and-dispatch flow over
  five tools. LangGraph would matter if Layer 3 (the autonomous
  superintendent agent in the proposal's future scope) were being built;
  it isn't.

A second, more honest reason: framework dependencies in this space are
*high-churn*. Both LangChain and LlamaIndex have published breaking changes
on a roughly quarterly cadence. For a prototype that's about to be handed
off to industry, depending on a framework that may rename its core APIs
between when the prototype is built and when it's deployed is a risk that
trades against the framework's value. Plain ChromaDB and ~250 lines of
custom orchestration are stable across the same window.

**Consequences.**

- The codebase is smaller and easier to read than a framework-based
  equivalent (`x1025/` is ~1,500 lines of code total). Reviewers can
  trace a query end-to-end without knowing a framework's conventions.
- The prototype is deliberately *learnable* — every component is plain
  Python with no decorators or registry magic. This is desirable for a
  handoff to a junior engineer at x1025 or for academic review.
- We give up out-of-the-box features we'd otherwise get for free: response
  streaming, conversation memory, retry/backoff on LLM calls, observability
  hooks. Of these, only retry/backoff matters for a pilot — and that's a
  ~20-line addition to `llm_providers.py` when needed.
- If Layer 3 (autonomous agents) is ever built, LangGraph becomes a real
  candidate. The provider-agnostic LLM abstraction means swapping into a
  framework at that point doesn't require rewriting Layers 1 and 2.

**Alternatives considered.**

- *LlamaIndex for Layer 1, plain Python for Layer 2.* The proposal's
  recommendation. Rejected because the marginal value over a direct
  ChromaDB client is small for a 5-document corpus, and mixing
  framework-based and non-framework code creates two mental models for
  reviewers.
- *LangChain for both layers.* Rejected for the framework-churn reason
  above, plus the same code-reduction argument.
- *LlamaIndex + LangGraph hybrid* (also recommended in the proposal).
  Worth revisiting if and when Layer 3 lands. Out of scope for this
  prototype.

**What this decision does not say.** This is not an argument that
LlamaIndex or LangChain are bad tools. They're excellent tools for the
problems they're designed for — multi-step agent orchestration, RAG over
heterogeneous document sources, conversational memory. For *this* problem
shape (single-turn router + RAG + tool dispatch over a small corpus),
they're solving a problem we don't have.

---

## ADR-009: Decisions intentionally deferred

The following decisions are flagged for deliberation **before** production
deployment, not at prototype time:

- **Per-vessel access control.** The current OpsAgent sees the whole fleet;
  a real deployment needs Master/Superintendent/Admin scopes that constrain
  which `vessel_id` values a tool function will accept.
- **Multi-tenant isolation.** One ChromaDB collection per customer, separate
  SQLite (or DB) per customer. Deferred until a second customer is on the table.
- **Final LLM provider choice.** See the cost model — depends on fleet size
  and the data-residency conversations with x1025 customers.
