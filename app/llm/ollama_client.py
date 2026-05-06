"""Ollama LLM client (Step 3 — STUB).

Will use the `ollama` Python package to call a local Ollama server.
Reads URL/model from config.yaml's llm.ollama section.
"""
from __future__ import annotations
from app.llm.base import LLMClient, CompletionResult, EmbeddingResult


class OllamaClient(LLMClient):
    @property
    def mode(self) -> str:
        return "ollama"

    def complete(self, system_prompt, user_prompt, *, temperature=0.2,
                 max_tokens=None, json_output=False) -> CompletionResult:
        raise NotImplementedError("Step 3 — Ollama client implementation pending")

    def embed(self, text: str) -> EmbeddingResult:
        raise NotImplementedError("Step 3 — Ollama embeddings pending")
