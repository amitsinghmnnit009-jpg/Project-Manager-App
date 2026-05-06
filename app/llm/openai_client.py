"""OpenAI-compatible LLM client (Step 3 — STUB).

Will use the `openai` Python SDK pointed at an internal OpenAI-compatible
endpoint (e.g. company gateway, vLLM, internal Ollama). Supports custom
auth headers per config.yaml.
"""
from __future__ import annotations
from app.llm.base import LLMClient, CompletionResult, EmbeddingResult


class OpenAICompatibleClient(LLMClient):
    @property
    def mode(self) -> str:
        return "openai"

    def complete(self, system_prompt, user_prompt, *, temperature=0.2,
                 max_tokens=None, json_output=False) -> CompletionResult:
        raise NotImplementedError("Step 3 — OpenAI-compatible client implementation pending")

    def embed(self, text: str) -> EmbeddingResult:
        raise NotImplementedError("Step 3 — OpenAI-compatible embeddings pending")
