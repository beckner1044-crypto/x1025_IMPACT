"""
core.py
Shared infrastructure: device, embedder, persistent vector store, LLM.

The LLM is loaded once and shared across the router, Layer 1 generation,
and Layer 2 tool dispatch. ChromaDB is persistent so re-running the
chatbot does not re-embed the corpus on every start.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import chromadb
from sentence_transformers import SentenceTransformer

# torch + transformers are only needed for the local LLM. Importing them
# lazily means a remote-provider deployment doesn't need a 2 GB torch install.
def _import_torch():
    import torch
    return torch


def _import_transformers():
    from transformers import AutoTokenizer, AutoModelForCausalLM
    return AutoTokenizer, AutoModelForCausalLM


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
@dataclass
class Config:
    embedding_model: str = "all-MiniLM-L6-v2"
    llm_model: str = "mistralai/Mistral-7B-Instruct-v0.3"
    chroma_path: str = "./data/chroma"
    collection_name: str = "x1025_sms"
    top_k: int = 4
    max_new_tokens: int = 256
    # provider: "local" | "groq" | "github" | "anthropic"
    # Defaults to env var LLM_PROVIDER, falling back to "local" if unset.
    provider: str = os.environ.get("LLM_PROVIDER", "local")


# --------------------------------------------------------------------------- #
# Device
# --------------------------------------------------------------------------- #
def setup_device() -> str:
    torch = _import_torch()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[core] device: {device}")
    if device == "cuda":
        print(f"[core] gpu:    {torch.cuda.get_device_name(0)}")
    return device


# --------------------------------------------------------------------------- #
# Embedder
# --------------------------------------------------------------------------- #
class Embedder:
    """Thin wrapper around SentenceTransformer."""

    def __init__(self, model_name: str, device: str):
        print(f"[core] loading embedder: {model_name}")
        self.model = SentenceTransformer(model_name, device=device)

    def encode(self, texts):
        if isinstance(texts, str):
            texts = [texts]
        return self.model.encode(texts, show_progress_bar=False).tolist()


# --------------------------------------------------------------------------- #
# Vector store
# --------------------------------------------------------------------------- #
class VectorStore:
    """Persistent ChromaDB wrapper with a single collection."""

    def __init__(self, path: str, collection_name: str):
        os.makedirs(path, exist_ok=True)
        self.client = chromadb.PersistentClient(path=path)
        # get_or_create lets us re-run without re-ingesting
        self.collection = self.client.get_or_create_collection(name=collection_name)

    def __len__(self):
        return self.collection.count()

    def add(self, ids, documents, embeddings, metadatas=None):
        self.collection.add(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )

    def query(self, query_embedding, n_results=4):
        res = self.collection.query(
            query_embeddings=[query_embedding] if isinstance(query_embedding[0], float)
                             else query_embedding,
            n_results=n_results,
        )
        # Flatten ChromaDB's list-of-lists shape for the single-query case
        return {
            "documents": res["documents"][0],
            "metadatas": res["metadatas"][0] if res.get("metadatas") else [{}] * len(res["documents"][0]),
            "distances": res["distances"][0] if res.get("distances") else [],
        }


# --------------------------------------------------------------------------- #
# LLM
# --------------------------------------------------------------------------- #
class LLM:
    """Mistral-7B-Instruct (local). For remote providers see llm_providers.py."""

    def __init__(self, model_name: str, device: str):
        torch = _import_torch()
        AutoTokenizer, AutoModelForCausalLM = _import_transformers()
        print(f"[core] loading LLM:    {model_name}")
        self._torch = torch
        self.device = device
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForCausalLM.from_pretrained(
            model_name,
            torch_dtype=torch.float16 if device == "cuda" else torch.float32,
            device_map="auto" if device == "cuda" else None,
        )
        if device != "cuda":
            self.model.to(device)

    def generate(self, prompt: str, max_new_tokens: int = 256, temperature: float = 0.0) -> str:
        """Return the model's continuation only — never the prompt."""
        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.model.device)
        do_sample = temperature > 0
        outputs = self.model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=do_sample,
            temperature=temperature if do_sample else 1.0,
            pad_token_id=self.tokenizer.eos_token_id,
        )
        new_tokens = outputs[0][inputs["input_ids"].shape[-1]:]
        return self.tokenizer.decode(new_tokens, skip_special_tokens=True).strip()

    def instruct(self, system: str, user: str, **kw) -> str:
        """Apply Mistral-Instruct chat template and generate."""
        messages = [{"role": "user", "content": f"{system}\n\n{user}"}]
        prompt = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        return self.generate(prompt, **kw)
