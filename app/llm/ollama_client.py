"""Ollama LLM client.

Calls a local Ollama server via the `ollama` Python package. Reads URL/model
from config.yaml's llm.ollama section. Used when llm.provider == 'ollama'.

Logs every call (model, duration, token counts, response excerpt) to
logs/ai_compute.jsonl per NFR §6 observability.
"""
from __future__ import annotations
import time
from typing import Any

import ollama

from app.llm.base import LLMClient, CompletionResult, EmbeddingResult
from app.config import get_config
from app.utils.logging import ai_compute_log, system_log


def _extract_chat_text(resp: Any) -> tuple[str, int, int]:
    """Pull (text, prompt_tokens, completion_tokens) from an ollama chat response.

    Tolerates both the legacy dict response (ollama<0.4) and the newer
    pydantic ChatResponse (ollama>=0.4) so we stay forward-compatible.
    """
    if hasattr(resp, "message"):
        text = getattr(resp.message, "content", "") or ""
        prompt_tokens = getattr(resp, "prompt_eval_count", 0) or 0
        completion_tokens = getattr(resp, "eval_count", 0) or 0
    else:
        text = (resp.get("message") or {}).get("content", "") or ""
        prompt_tokens = resp.get("prompt_eval_count", 0) or 0
        completion_tokens = resp.get("eval_count", 0) or 0
    return text, int(prompt_tokens), int(completion_tokens)


def _extract_embed_vector(resp: Any) -> list[float]:
    """Pull the vector from an ollama embeddings response (dict or pydantic)."""
    if isinstance(resp, dict):
        return list(resp.get("embedding") or [])
    return list(getattr(resp, "embedding", []) or [])


class OllamaClient(LLMClient):
    def __init__(self):
        cfg = get_config().llm
        self._cfg = cfg.ollama
        self._temperature = cfg.temperature
        self._client = ollama.Client(host=self._cfg.base_url)

    @property
    def mode(self) -> str:
        return "ollama"

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_output: bool = False,
    ) -> CompletionResult:
        log = ai_compute_log()
        sys = system_log()
        temp = temperature if temperature is not None else self._temperature

        options: dict[str, Any] = {"temperature": temp}
        if max_tokens is not None:
            options["num_predict"] = max_tokens

        kwargs: dict[str, Any] = {
            "model": self._cfg.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "options": options,
        }
        if json_output:
            # Ollama supports format="json" as a constrained-decode hint.
            kwargs["format"] = "json"

        t0 = time.time()
        try:
            resp = self._client.chat(**kwargs)
        except Exception as e:
            sys.error(
                "ollama chat failed",
                extra={
                    "event": "llm_error", "mode": "ollama",
                    "model": self._cfg.model, "error": str(e),
                    "duration_seconds": round(time.time() - t0, 3),
                },
            )
            raise

        duration = round(time.time() - t0, 3)
        text, prompt_tokens, completion_tokens = _extract_chat_text(resp)

        log.info(
            "llm complete",
            extra={
                "event": "llm_complete", "mode": "ollama",
                "model": self._cfg.model, "duration_seconds": duration,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "json_output": json_output,
                "response_excerpt": text[:200],
            },
        )
        return CompletionResult(
            text=text, model=self._cfg.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_seconds=duration,
        )

    def embed(self, text: str) -> EmbeddingResult:
        log = ai_compute_log()
        sys = system_log()

        t0 = time.time()
        try:
            resp = self._client.embeddings(model=self._cfg.embed_model, prompt=text)
        except Exception as e:
            sys.error(
                "ollama embeddings failed",
                extra={
                    "event": "llm_error", "mode": "ollama",
                    "model": self._cfg.embed_model, "error": str(e),
                    "duration_seconds": round(time.time() - t0, 3),
                },
            )
            raise

        duration = round(time.time() - t0, 3)
        vec = _extract_embed_vector(resp)

        log.info(
            "llm embed",
            extra={
                "event": "llm_embed", "mode": "ollama",
                "model": self._cfg.embed_model,
                "duration_seconds": duration, "vector_dim": len(vec),
            },
        )
        return EmbeddingResult(vector=vec, model=self._cfg.embed_model)
