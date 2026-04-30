"""
chatbot.py
The unified x1025 assistant. One ask() method that:

  1. Routes the question (procedural / operational / both / none).
  2. Calls Layer 1 and/or Layer 2 as needed.
  3. For 'both', synthesizes the two answers into one.
  4. Optionally runs the faithfulness verifier and applies a confidence floor:
     if the verifier finds contradicted/unfounded claims, the user-facing
     answer is replaced with a safe fallback, and the original answer is
     surfaced in the result for review.

Returns a dict with the answer, the route, and the raw evidence so a UI
can show citations and tool traces.
"""
from __future__ import annotations

from .core import Embedder, VectorStore, Config, setup_device
from .llm_providers import build_llm
from .layer1 import SMSRag
from .layer2 import OpsAgent
from .router import QueryRouter
from .faithfulness import Verifier, FaithfulnessResult
from .audit import AuditLogger
from .redact import redactor_from_db


_FALLBACK_ANSWER = (
    "I don't have enough verified information in the procedures or operational "
    "data to answer that confidently. Please consult the relevant ISM document "
    "directly, or rephrase the question with more specifics."
)


class X1025Chatbot:
    def __init__(self, cfg: Config | None = None,
                 docs_dir: str = "./data/ism_docs",
                 db_path: str = "./data/x1025.db",
                 verify: bool = False,
                 confidence_floor: float = 0.5,
                 audit: bool = True):
        """
        verify: if True, run the faithfulness verifier on every answer and
                apply the confidence floor.
        confidence_floor: minimum faithfulness score (supported / total claims)
                required to ship the LLM answer. Below this, the safe fallback
                is returned instead. Only applied when verify=True.
        audit: if True, write every answer to the append-only audit log at
                ./logs/audit.jsonl (with PII redacted). Default True because
                audit is cheap (one disk write) and useful from day one.
        """
        self.cfg = cfg or Config()
        self.device = setup_device() if self.cfg.provider == "local" else "cpu"
        self.embedder = Embedder(self.cfg.embedding_model, self.device)
        self.vstore = VectorStore(self.cfg.chroma_path, self.cfg.collection_name)
        self.llm = build_llm(self.cfg.provider, device=self.device,
                             local_model_name=self.cfg.llm_model)
        self.layer1 = SMSRag(self.embedder, self.vstore, self.llm,
                             docs_dir=docs_dir, top_k=self.cfg.top_k)
        self.layer2 = OpsAgent(self.llm, db_path=db_path)
        self.router = QueryRouter(self.llm)
        self.verify = verify
        self.confidence_floor = confidence_floor
        self.verifier = Verifier(self.llm) if verify else None

        # Audit log: redactor pulls the actual vessel registry from the
        # SQLite mock so vessel names get redacted accurately on persistence.
        if audit:
            self.audit_logger: AuditLogger | None = AuditLogger(redactor_from_db(db_path))
        else:
            self.audit_logger = None

    def setup(self, force_reingest: bool = False) -> None:
        """Ingest ISM documents into the vector store. Idempotent."""
        self.layer1.ingest(force=force_reingest)

    def ask(self, question: str) -> dict:
        route = self.router.classify(question)

        if route == "procedural":
            l1 = self.layer1.answer(question)
            answer = l1["answer"]
            evidence = l1.get("context", "")
            return self._finalize(route, question, answer, evidence,
                                  layer1=l1, layer2=None)

        if route == "operational":
            l2 = self.layer2.answer(question)
            answer = l2["answer"]
            evidence = self._evidence_from_l2(l2)
            return self._finalize(route, question, answer, evidence,
                                  layer1=None, layer2=l2)

        if route == "both":
            l1 = self.layer1.answer(question)
            l2 = self.layer2.answer(question)
            answer = self._synthesize(question, l1, l2)
            evidence = "\n\n".join(filter(None, [
                l1.get("context", ""),
                self._evidence_from_l2(l2),
            ]))
            return self._finalize(route, question, answer, evidence,
                                  layer1=l1, layer2=l2)

        none_answer = ("I can answer two kinds of question: (1) Safety Management System "
                       "procedures, and (2) live operational data about your fleet — ETAs, "
                       "fuel ROB, charter-party performance, certificate expiry. "
                       "Could you rephrase along those lines?")
        audit_id = None
        if self.audit_logger is not None:
            audit_id = self.audit_logger.log(
                question=question,
                answer=none_answer,
                route="none",
            )
        return {
            "route": "none",
            "answer": none_answer,
            "layer1": None, "layer2": None,
            "faithfulness": None,
            "audit_id": audit_id,
        }

    @staticmethod
    def _evidence_from_l2(l2: dict | None) -> str:
        """Stringify Layer 2's tool result so the verifier can inspect it."""
        if not l2 or l2.get("result") is None:
            return ""
        import json
        return (f"[Tool: {l2.get('tool')}({l2.get('args', {})})]\n"
                f"{json.dumps(l2['result'], indent=2, default=str)}")

    def _finalize(self, route: str, question: str, answer: str, evidence: str,
                  layer1: dict | None, layer2: dict | None) -> dict:
        """Optionally verify and apply confidence floor; assemble final dict.
        If audit logging is on, write a JSONL row and include its id in the
        return value so a UI can foreign-key feedback to it."""
        result: dict = {
            "route": route, "answer": answer,
            "layer1": layer1, "layer2": layer2,
            "faithfulness": None,
            "audit_id": None,
        }
        if self.verify and self.verifier and evidence.strip():
            f = self.verifier.verify(answer, evidence)
            result["faithfulness"] = {
                "score": f.score,
                "n_claims": f.n_claims,
                "n_supported": f.n_supported,
                "n_contradicted": f.n_contradicted,
                "n_not_found": f.n_not_found,
                "verdicts": [
                    {"claim": v.claim, "label": v.label, "rationale": v.rationale}
                    for v in f.verdicts
                ],
            }
            # Confidence floor: replace the user-facing answer if below threshold.
            if f.n_claims and f.score < self.confidence_floor:
                result["original_answer"] = answer
                result["answer"] = _FALLBACK_ANSWER
                result["fallback_reason"] = (
                    f"faithfulness {f.score:.2f} < floor {self.confidence_floor:.2f} "
                    f"({f.n_supported}/{f.n_claims} claims supported)"
                )

        # Persist to audit log (after any fallback substitution, so the log
        # reflects what the user actually saw).
        if self.audit_logger is not None:
            sources = layer1.get("sources") if layer1 else None
            tool   = layer2.get("tool")     if layer2 else None
            args   = layer2.get("args")     if layer2 else None
            confidence = (
                result["faithfulness"]["score"] if result["faithfulness"] else None
            )
            low_conf = "fallback_reason" in result
            audit_id = self.audit_logger.log(
                question=question,
                answer=result["answer"],
                route=route,
                confidence=confidence,
                low_confidence=low_conf,
                tool=tool,
                tool_args=args,
                sources=sources,
                verifier=result["faithfulness"],
            )
            result["audit_id"] = audit_id
        return result

    def _synthesize(self, question: str, l1: dict, l2: dict) -> str:
        system = (
            "You are a maritime assistant. The user's question needs both procedural guidance "
            "(from the Safety Management System) and live operational data. Combine the two "
            "sources below into one clear answer. Cite procedure source numbers like [1] when "
            "you use procedural information."
        )
        user = (
            f"Question: {question}\n\n"
            f"--- Procedural answer (from SMS) ---\n{l1['answer']}\n\n"
            f"Sources: {l1['sources']}\n\n"
            f"--- Operational answer (from x1025 database) ---\n{l2['answer']}\n\n"
            f"Combined answer:"
        )
        return self.llm.instruct(system, user, max_new_tokens=350)
