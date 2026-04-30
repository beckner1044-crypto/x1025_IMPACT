"""
llm_providers.py
Provider-agnostic LLM wrapper. Same interface as `core.LLM` so the rest of the
prototype (router, layer1, layer2, chatbot, evaluate) does not need to change.

Supported providers:
    local      Mistral-7B via transformers   — offline, requires GPU
    groq       Groq Cloud free tier          — free, 14,400 req/day, 30 RPM
    github     GitHub Models free tier       — free, prototyping only per TOS
    anthropic  Claude via Anthropic API      — paid, recommended for eval judge

Selection happens via the LLM_PROVIDER env var or the Config.provider field.
All remote providers use OpenAI-compatible chat/completions, so one HTTP
client serves them all.
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional


# --------------------------------------------------------------------------- #
# Provider registry
# --------------------------------------------------------------------------- #
PROVIDERS = {
    "local": {
        "label":   "Mistral-7B-Instruct (local, transformers)",
        "needs":   "GPU + ~14 GB download",
        "cost":    "free, but pay for the GPU",
    },
    "groq": {
        "label":   "Llama 3.1 8B Instant (Groq free tier)",
        "endpoint": "https://api.groq.com/openai/v1",
        "model":   "llama-3.1-8b-instant",
        "env_key": "GROQ_API_KEY",
        "needs":   "free Groq account",
        "cost":    "free up to 14,400 req/day, 30 RPM",
    },
    "github": {
        "label":   "GPT-4o-mini via GitHub Models (free tier)",
        "endpoint": "https://models.github.ai/inference",
        "model":   "openai/gpt-4o-mini",
        "env_key": "GITHUB_TOKEN",
        "needs":   "GitHub PAT (any account)",
        "cost":    "free, low rate limits, TOS = experimentation only",
    },
    "anthropic": {
        "label":   "Claude Haiku 4.5 (Anthropic API)",
        "endpoint": "https://api.anthropic.com/v1/openai",
        "model":   "claude-haiku-4-5",
        "env_key": "ANTHROPIC_API_KEY",
        "needs":   "paid Anthropic account",
        "cost":    "$1 / $5 per 1M tokens (input/output)",
    },
}


def list_providers() -> str:
    lines = ["available LLM providers:"]
    for key, p in PROVIDERS.items():
        lines.append(f"  {key:<10} {p['label']}")
        lines.append(f"             needs: {p['needs']}")
        lines.append(f"             cost:  {p['cost']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Remote LLM (OpenAI-compatible)
# --------------------------------------------------------------------------- #
class RemoteLLM:
    """OpenAI-compatible chat/completions client. Works with Groq, GitHub
    Models, OpenAI, and Anthropic's OpenAI-compatible endpoint."""

    def __init__(self, endpoint: str, api_key: str, model: str,
                 max_retries: int = 3, label: str = ""):
        if not api_key:
            raise RuntimeError(
                "missing API key. Set the matching env var (see PROVIDERS) "
                "or use provider='local'."
            )
        # httpx is already a transitive dep via chromadb; no new requirement.
        import httpx
        self._httpx = httpx
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_retries = max_retries
        self.label = label or model
        print(f"[llm] remote provider: {self.label}  ({self.endpoint})")

    def generate(self, prompt: str, max_new_tokens: int = 256,
                 temperature: float = 0.0) -> str:
        # The remote model handles its own prompt format; we wrap the raw text
        # as a single user message. instruct() is the preferred entry point.
        return self._chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_new_tokens,
            temperature=temperature,
        )

    def instruct(self, system: str, user: str,
                 max_new_tokens: int = 256, temperature: float = 0.0) -> str:
        return self._chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            max_tokens=max_new_tokens,
            temperature=temperature,
        )

    # ------------------------------------------------------------------ #
    def _chat(self, messages, max_tokens: int, temperature: float) -> str:
        url = f"{self.endpoint}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type":  "application/json",
        }
        payload = {
            "model":       self.model,
            "messages":    messages,
            "max_tokens":  max_tokens,
            "temperature": temperature,
        }

        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with self._httpx.Client(timeout=60.0) as client:
                    r = client.post(url, headers=headers, json=payload)
                if r.status_code == 429:
                    # Rate-limited — back off and retry. Free tiers will hit this.
                    wait = float(r.headers.get("retry-after", 2 * attempt))
                    print(f"[llm] rate limited; waiting {wait:.1f}s "
                          f"(attempt {attempt}/{self.max_retries})")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                data = r.json()
                return data["choices"][0]["message"]["content"].strip()
            except Exception as e:                              # noqa: BLE001
                last_exc = e
                if attempt < self.max_retries:
                    time.sleep(1.5 * attempt)
                    continue
                break
        raise RuntimeError(f"LLM request failed after {self.max_retries} attempts: {last_exc}")


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
def build_llm(provider: str, device: str = "cpu",
              local_model_name: str = "mistralai/Mistral-7B-Instruct-v0.3"):
    """Construct an LLM for the given provider name.

    Returns either a `core.LLM` (local) or a `RemoteLLM`. Both expose the
    same `.instruct(system, user, max_new_tokens, temperature)` interface
    so callers don't care which is in use.
    """
    provider = (provider or "local").lower()
    if provider not in PROVIDERS:
        raise ValueError(
            f"unknown provider {provider!r}. {list_providers()}"
        )

    if provider == "local":
        from .core import LLM  # local import — avoids torch on remote-only setups
        return LLM(local_model_name, device)

    info = PROVIDERS[provider]
    return RemoteLLM(
        endpoint=info["endpoint"],
        api_key=os.environ.get(info["env_key"], ""),
        model=info["model"],
        label=info["label"],
    )
