# Speaker notes — x1025 IMPACT presentation

This is for you, not the audience. It's the script behind the slides.

**Total time:** ~10 minutes presentation + 5 minutes Q&A.
**Format:** read it the morning of. Don't memorise it word-for-word — you
sound more credible if you're talking *from* the material than reciting
it. The bullet prompts in each section are what to make sure you say,
not what to read off.

---

## Before you start

**One thing to remember above everything else:** you built this. You
made every decision in the ADRs. When someone asks a question, the
honest answer is almost always the best one — including "we considered
that and chose not to do it because…" or "I deferred that decision until
real customer data was available."

**The frame you want the audience to walk away with:** "this isn't a
toy. There's a clean architecture, real evaluation, real safety rails,
and a credible pilot path."

**Do not say:**
- "Just" (as in "I just built a RAG system") — diminishes your work
- "AI / ML" without specifying what kind — vague
- "It's pretty cool" — let them decide that
- "I think" / "I guess" / "kind of" — own your decisions

**Do say:**
- Specific numbers ("13 ADRs", "49 tests", "5 tools", "$0 free tier")
- "we decided" / "the trade-off was" / "we rejected X because" — confident
- "The honest answer is…" when something didn't go to plan

---

## Slide 1 — Title (30 seconds)

**On screen:** big "x1025" logo with three pillars below it.

**What to say (in your own words):**
- Hi, I'm Beckner, and this is Ismael. We're from the IMPACT program at the UMass Boston VDC.
- This is the x1025 maritime AI prototype — eight weeks of work in
  partnership with x1025.
- Three things: SMS retrieval, operational tools, safety rails. I'll
  walk through each, then a live demo, then numbers.

**Pacing tip:** don't rush this slide. People are still settling in.
Take a breath after "eight weeks of work."

---

## Slide 2 — The problem (45 seconds)

**On screen:** two columns — Safety Management System on the left,
Operational data on the right, "x1025 unifies both" arrow at the bottom.

**Cover these points:**
- Vessel ops sits at the intersection of two information silos.
- The SMS — static, audited, regulator-facing. Hundreds of pages of ISM
  procedures.
- The operational DB — dynamic, vessel-by-vessel. Noon reports, fuel,
  ETAs, certificate expiries.
- A Master who needs to act on a question that spans both — *"my Safety
  Radio cert expired and I'm at sea, what do I do?"* — has to consult
  the SMS by hand AND run a SQL query AND synthesise the answer
  themselves.
- That's the daily friction x1025 wants to eliminate.

**Stat to drop if asked:** "Same lookups happen every day across every
vessel in the fleet."

---

## Slide 3 — Architecture (60 seconds)

**On screen:** the router fan-out diagram — question → router → Layer 1
or Layer 2 (or both) → verifier → answer.

**Walk through it left to right. Use the diagram, point at boxes:**
1. User question comes in.
2. Router classifies it: procedural, operational, both, or out of scope.
   Lightweight LLM call with a regex fallback.
3. **Layer 1 — RAG over the SMS.** ISM markdown documents, chunked by
   section header, embedded with MiniLM, stored in ChromaDB. Retrieval
   returns the relevant chunks with citation metadata.
4. **Layer 2 — tool dispatch over the system of record.** Five
   deterministic tools, each wrapping a SQL query. The LLM picks the
   tool and emits its arguments as JSON; we validate against a per-tool
   schema before any SQL runs.
5. Both feed into the verifier — per-claim faithfulness check, audit
   log, citations.

**The line worth landing:** "Layer 2 uses tool dispatch *instead of*
free-form text-to-SQL. That's the most consequential architectural
choice — text-to-SQL would have been more flexible but with a much
larger risk surface. The five fixed tools cover the four query types
in the proposal and are individually unit-testable."

---

## Slide 4 — Layer 1 demo (in slides) (45 seconds)

**On screen:** the CO2 system question and the cited answer with [1] and
[2] markers, sources box at the bottom.

**Cover:**
- Procedural question: "what is the procedure for releasing the fixed
  CO2 system in an engine room fire?"
- The answer comes back with the Master / Chief Engineer authorisation
  chain, the head-count requirement, the 20-second pre-alarm.
- Every claim has an inline citation. `[1]` and `[2]` reference the
  retrieved chunks, both from `02_fire_emergency.md`.
- The retriever, the verifier, and the audit log all reference the same
  chunk IDs — there's no point at which a citation can drift from its
  source.

**One sentence to say verbatim:** "Citations aren't a UI feature, they
are the contract between what the model says and what's in the SMS."

---

## Slide 5 — Layer 2 demo (in slides) (45 seconds)

**On screen:** the charter-party speed question, the tool call in a
code block, the answer with red callouts on the underperformance.

**Cover:**
- Operational question: "Is MV Boreas meeting its charter-party speed?"
- The LLM picks `get_speed_performance(vessel="Boreas", days=14)` —
  shown literally on screen.
- Schema validation runs on those arguments before any SQL touches the
  database.
- Tool returns the numbers; LLM phrases them: averaged 11.72 knots
  against a CP warranty of 12.5 knots, fuel consumption 46.5 vs CP 44.
- **Verdict: underperforming charter party.** That sentence costs the
  charterer money — exactly the kind of question x1025's customers care
  about.

**If the audience looks confused about "charter party":** brief
parenthetical — "the contract between vessel owner and the charterer
that warranties certain speed and fuel-consumption levels."

---

## Slide 6 — Safety rail (60 seconds)

**On screen:** three cards — verifier, audit log, redaction.

**This is the differentiating slide. Slow down here.**

**Cover all three:**
- **Verifier.** Every answer is decomposed into atomic claims. Each
  claim is checked against the retrieved evidence and labelled
  SUPPORTED, CONTRADICTED, or NOT_FOUND. If support drops below 50%,
  the user sees a fallback message — *"I don't have enough verified
  information to answer that confidently"* — and the original answer
  is logged for review rather than shown to a Master.
- **Audit log.** Every answer (including refusals) writes an
  append-only JSONL row with the question, the route, the retrieved
  sources, the tool arguments, and the verifier verdicts. Foreign-keyed
  to a feedback table by UUID.
- **Redaction.** PII is scrubbed before write. Vessel names use the
  actual fleet registry — not a regex pattern — which means it catches
  "Aurora" without the prefix and never produces false positives on
  text like "MS Office".

**The line that lands:** "Maritime ops can't ship hallucinated
procedures. We don't."

**If asked why this matters:** "A Master taking action on a wrong
procedural answer is exactly the failure mode this domain can't tolerate.
Confident wrong answers are worse than no answer."

---

## Slide 7 — Evaluation (60 seconds)

**On screen:** four big-number callouts (9/9 router, 5/5 tools, 0.50
recall, 73% verifier) and the list of what the harness measures.

**Cover:**
- The harness exercises every component, produces a structured Markdown
  report.
- Five metrics: router accuracy, retrieval quality (Recall@K, MRR,
  Precision@K), tool selection, LLM-as-judge, and per-claim
  verification.
- **Be honest about the numbers on screen.** They're from a deliberately
  weakened sandbox run — TF-IDF retriever instead of MiniLM, a stub
  LLM instead of Mistral, no GPU, no network. The harness, the
  metrics, and the report structure are real; the absolute numbers
  reflect the sandbox stack.
- Against the production stack (Mistral + MiniLM + Groq), retrieval
  scores materially higher.

**One sentence about same-model bias:** "The judge in the prototype is
the same model under test, so absolute scores are optimistic — these
numbers are best read as relative signals when iterating on prompts or
retrieval."

**If asked about the 0.50 Recall@4:** that's the TF-IDF noise floor on
a 5-document corpus. Real MiniLM does materially better and the
production rerun is on the next-steps list.

---

## Slide 8 — Cost (45 seconds)

**On screen:** giant "$0" callout on the left, scaling table on the
right, free-tier reminder strip at the bottom.

**Cover:**
- The IMPACT phase and an early single-vessel pilot run for $0 on
  Groq's free tier — Llama 3.1 8B Instant, 14,400 requests per day,
  no credit card.
- That covers any single-vessel or even 50-vessel deployment without
  hitting a rate limit.
- Crossover with self-hosting on a rented GPU is around 200 vessels.
- The LLM is provider-agnostic. Groq, GitHub Models, Anthropic, local
  Mistral — one environment variable.

**The line that lands:** "Free-tier policy risk is policy risk, not
architecture risk."

---

## Slide 9 — Roadmap (45 seconds)

**On screen:** three columns — Done, Pilot phase, Deferred.

**Walk the columns left to right:**
- **Done.** Two-layer architecture, router, verifier, audit, redaction,
  tool validation, eval harness, cost model, 13 ADRs.
- **Pilot phase.** Wire to real x1025 data, replace seed corpus,
  collect feedback, retune against actual usage, add per-vessel access
  controls.
- **Deferred — *deliberately*.** Multi-tenant isolation, final LLM
  provider commitment, free-form text-to-SQL, stronger embedder. Each
  is in ADR-009 with a reason. "Decisions deferred are decisions made."

**The line that lands:** "Saying 'not yet, and here's why' is a real
engineering output."

---

## Slide 10 — The ask (45 seconds)

**On screen:** four numbered asks on dark background.

**Cover, one at a time:**
1. **Sample SMS documents** — 10 to 20 anonymised real ISM procedures
   to replace the seed corpus.
2. **DB schema** — a sketch or read replica so Layer 2's tools can
   point at the real x1025 schema.
3. **Anonymised query log** — even 50 real user questions lets us
   retune the router and chunker against actual usage.
4. **A 4-week pilot window** — one vessel, one Master, free-tier Groq,
   weekly eval reports.

**Soft close:** "None are blocking. Any subset is useful. Thank you."

**Then stop talking.** Don't fill the silence after the ask. Let the
audience or Hash speak first.

---

## Live demo flow (3 minutes during slides 4 and 5, OR a separate slot)

If you have time for a live demo, here's the script. Have the Gradio UI
running on your laptop before the talk starts. Have these three queries
typed and ready in a notes file so you can paste them.

**Setup (10 seconds):** "Quick live demo to show this is real, not just
slides."

**Query 1 — procedural (60 seconds):**
- Type: *"What is the procedure for releasing the fixed CO2 system in an
  engine room fire?"*
- Wait for the answer to render.
- Point at the citations: "Inline citations to `02_fire_emergency.md`,
  Master and Chief Engineer authorisation chain, the head-count
  requirement, the 20-second pre-alarm."
- Click **Explain this answer**: "Per-claim verifier verdicts — every
  claim is SUPPORTED with rationale tied to the retrieved chunk."

**Query 2 — operational (60 seconds):**
- Type: *"Is MV Boreas meeting its charter-party speed?"*
- Wait for the answer.
- Point at the route badge: "Routed to Layer 2 — operational, not
  procedural."
- Point at the tool call in the evidence pane: "LLM picked
  `get_speed_performance` with `vessel="Boreas"`, args were
  schema-validated before any SQL ran."
- Read the verdict: "MV Boreas is averaging 11.72 knots against a CP
  warranty of 12.5. Underperforming."

**Query 3 — both layers (60 seconds):**
- Type: *"If MV Cassini has an expired Safety Radio certificate while
  at sea, what should the Master do?"*
- Point at the route badge: "Both — needs procedural guidance and
  operational data."
- Point at the synthesised answer: notify office and DPA, request a
  flag-state extension, avoid entering port until granted in writing.
- "And on the right, the tool result confirms Cassini's Safety Radio
  expired April 24th."

**Close demo (10 seconds):** "That's the system. Back to slides."

---

## Q&A prep — likely questions and confident answers

These are the questions someone might actually ask. The answers in
parentheses are the version *for you* — don't read them out, paraphrase.

### Architecture questions

> "Why not just use ChatGPT / GPT-4 / Claude on top of your data?"

(You can. The provider abstraction supports Anthropic and any other
OpenAI-compatible endpoint. The reason the default is Groq is cost — $0
for the IMPACT phase. The architecture is independent of which LLM
sits behind it. Switching is one environment variable.)

> "Why ChromaDB and not <FAISS / Pinecone / Weaviate / Qdrant>?"

(For a 5-document corpus and a single-vessel pilot, the choice doesn't
matter much — they all work. ChromaDB's persistent client gives us
filesystem persistence with no server, which is the right shape for a
single-vessel deployment. Pinecone and Weaviate are server products that
make sense at multi-tenant scale; over-engineered here. ADR-004.)

> "Why MiniLM and not a bigger embedder like BGE-large?"

(MiniLM is small, fast, well-tested, and good enough for a 5-document
corpus. BGE-large would probably score higher on retrieval but the
gains depend on whether the bottleneck is the embedder or the chunker.
That's exactly what the eval harness is designed to measure — switch
the embedder, rerun the eval, look at the Recall@K and MRR delta. Worth
doing during the pilot, not for the prototype. ADR-005.)

> "Why not text-to-SQL for Layer 2?"

(Risk surface. A hallucinated `WHERE` clause silently returning the
wrong vessel's data is exactly the failure mode this domain can't
tolerate. The five fixed tools cover the four query types in the
proposal, are individually unit-testable, and don't open a SQL
injection / wrong-data vector. When there's a concrete analytics use
case the fixed tools can't cover, we add it; until then, we don't.
ADR-002.)

### Reliability and safety questions

> "How do you know the LLM isn't hallucinating?"

(Two answers. Layer 1: every answer is anchored to retrieved chunks
with inline citations, and the per-claim verifier checks each generated
claim against the evidence. If support drops below the floor, the
user sees a safe fallback rather than the answer. Layer 2: the answer
is generated from real SQL output — there's no opportunity to
hallucinate the data, just to phrase it. The verifier still runs and
catches the case where the LLM phrasing drifts from the SQL result.)

> "What happens if Groq goes down?"

(Two paths. Short-term, the LLM is provider-agnostic — switching to
GitHub Models or Anthropic is one env var, no application code change.
Longer-term, a self-hosted Mistral instance on rented GPU is in the
cost model and supported by the same abstraction. We deliberately did
not build an offline quantised fallback because it adds 5 GB of
dependencies for a use case that isn't in the pilot scope.)

> "How do you handle PII?"

(Three layers. First, the audit log uses registry-driven redaction at
the persistence boundary — vessel names, IMO numbers, coordinates,
emails, phones are scrubbed before any disk write. Second, the redactor
is fleet-aware: it pulls the actual vessel registry from the DB, so it
catches names like "Aurora" without the "MV" prefix and never
falsely matches "MS Office". Third, redaction is the only thing that
touches disk-bound data — the user always sees their original message.)

### Process questions

> "How long did this take you?"

(Eight weeks. Most of the build time was in iteration on the Layer 2
tool design and the verifier; the RAG pipeline came together early.
Adding the production rails — audit, redaction, feedback, schema
validation — was the last two weeks.)

> "What was hardest?"

(Two honest answers. First, deciding what *not* to build. Several LLM
reviewers piled on with feature suggestions — rerankers, tiered
routing, semantic caching, offline fallbacks — most of which were
premature for a prototype at this scale. Saying no to those took
discipline. Second, getting the verifier to produce labels I trusted —
the same model judging itself has correlated blind spots, and the
honest answer is the absolute scores are optimistic. The fix is to
point the judge at a stronger model in the production rerun.)

> "What would you change if you started over?"

(Two things. We'd write the eval harness on day one instead of
mid-project — it's the one thing that makes architectural choices
testable. And we'd write down the rejected ideas with reasons earlier;
the ADRs ended up being the most reused document in the project, and
the ones that capture *why we said no* were the most useful in
conversations with reviewers.)

> "Could a junior engineer take this over?"

(Yes. The README + Makefile gets you running in three commands.
HANDOVER.md describes deployment. PROJECT_REPORT.md describes
methodology. The 49-test pytest suite gives you a regression net for
any changes. ADR.md tells you the reasoning behind every decision so
you don't relitigate them. That's the documentation surface I tried
to build.)

### Hard questions

> "How is this different from any other RAG demo I've seen?"

(Three things. First, the operational layer — most RAG demos are pure
retrieval; we have tool dispatch over a real database, with schema
validation and verifiable arguments. Second, the safety rail — the
faithfulness verifier with a confidence floor isn't standard in RAG
demos and it's the thing that makes this credible for maritime ops.
Third, the audit / feedback / redaction infrastructure — most demos
ship without those, which is fine for demos and not fine for pilots.)

> "What's the actual business value to x1025?"

(Two things from the proposal. One, time saved per query — Master and
Superintendent stop hand-translating between the SMS and the operational
DB for every recurring question. Two, defensibility — when a Master
follows the assistant's answer, the audit log is a defensible record
of what was asked, what was retrieved, what was verified, and what
was shown. That second one is what x1025's customers will care about
when this gets audited.)

> "What if x1025 doesn't continue the project?"

(That's their call. The prototype stands on its own as IMPACT-program
work — every architectural decision is documented, the eval harness
runs, the test suite passes. The handover doc is written so any
engineer x1025 hires next can pick it up. And the LLM-provider
abstraction means the work isn't bound to any one vendor.)

---

## What you do with your hands and voice

**Pacing:** 10-minute talk, 10 slides — that's roughly one minute per
slide. Don't go faster than 45 seconds on any slide; you'll feel
rushed. Don't go slower than 90 seconds on any slide; you'll feel like
you're filling time.

**Where to look:** at the audience, not the slides. Glance at the slide
to confirm what's there, then back to the audience.

**Hands:** if you don't know what to do with them, hold the clicker
or the laptop. Avoid pockets. Avoid crossing your arms.

**Pauses:** after each big claim, pause for one beat before the next
sentence. *"Free-tier policy risk is policy risk, not architecture
risk." [pause] "That keeps the LLM choice reversible."*

**The single most important thing:** when someone asks a question you
don't know the answer to, the answer is **"that's a great question. The
honest answer is I don't know. My best guess is X — and I can dig into
it after this."** Don't make things up. Don't bluff. Don't fill silence
with words.

You built this thing. You're allowed to be confident about it.
