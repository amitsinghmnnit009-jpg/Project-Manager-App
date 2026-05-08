"""LLM client interface — implemented by ollama_client and openai_client.

Step 3 fills these in. Aggregation/highlights/status engines depend ONLY on
this interface so the implementation can be swapped via config.json.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class CompletionResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_seconds: float = 0.0


@dataclass
class EmbeddingResult:
    vector: list[float]
    model: str


class LLMClient(ABC):
    """Single interface for chat completion + embedding."""

    @abstractmethod
    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_output: bool = False,
    ) -> CompletionResult:
        """Run a single chat completion."""

    @abstractmethod
    def embed(self, text: str) -> EmbeddingResult:
        """Produce an embedding vector for a piece of text."""

    @property
    @abstractmethod
    def mode(self) -> str:
        """Return 'ollama' or 'openai' — used for logging/audit."""


def get_llm_client() -> LLMClient:
    """Factory — returns the configured LLM client.

    Reads llm.provider from config.json. Implemented in Step 3.
    """
    from app.config import get_config
    cfg = get_config()
    if cfg.llm.provider == "ollama":
        from app.llm.ollama_client import OllamaClient
        return OllamaClient()
    elif cfg.llm.provider == "openai":
        from app.llm.openai_client import OpenAICompatibleClient
        return OpenAICompatibleClient()
    else:
        raise ValueError(f"Unknown LLM provider: {cfg.llm.provider}")
