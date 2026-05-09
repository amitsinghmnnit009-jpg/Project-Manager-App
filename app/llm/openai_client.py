"""OpenAI-compatible LLM client.

Uses the `openai` Python SDK pointed at an internal OpenAI-compatible endpoint
(your company's hosted gpt-oss gateway, vLLM, internal Ollama proxy, etc.).
Supports custom auth headers per config.json (header-based auth on internal
gateways that don't use the OpenAI Bearer token convention).

Used when llm.provider == 'openai'. Logs every call to logs/ai_compute.jsonl
per NFR §6 observability.
"""
from __future__ import annotations
import time
from typing import Any

from openai import OpenAI

from app.llm.base import LLMClient, CompletionResult, EmbeddingResult
from app.config import get_config
from app.utils.logging import ai_compute_log, prompt_log, system_log


class OpenAICompatibleClient(LLMClient):
    def __init__(self):
        cfg = get_config().llm
        self._cfg = cfg.openai
        self._temperature = cfg.temperature

        # OpenAI SDK requires *some* api_key string. When the gateway uses
        # header-based auth we still pass a placeholder so the SDK accepts the
        # request; the real auth flows via default_headers.
        self._client = OpenAI(
            base_url=self._cfg.base_url,
            api_key=self._cfg.api_key or "placeholder-not-used",
            default_headers=self._cfg.custom_headers or None,
            timeout=float(self._cfg.timeout_seconds),
        )

    @property
    def mode(self) -> str:
        return "openai"

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

        kwargs: dict[str, Any] = {
            "model": self._cfg.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temp,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if json_output:
            kwargs["response_format"] = {"type": "json_object"}

        t0 = time.time()
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as e:
            sys.error(
                "openai chat failed",
                extra={
                    "event": "llm_error", "mode": "openai",
                    "model": self._cfg.model, "error": str(e),
                    "duration_seconds": round(time.time() - t0, 3),
                },
            )
            raise

        duration = round(time.time() - t0, 3)
        text = (resp.choices[0].message.content or "") if resp.choices else ""
        usage = getattr(resp, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0

        log.info(
            "llm complete",
            extra={
                "event": "llm_complete", "mode": "openai",
                "model": self._cfg.model, "duration_seconds": duration,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "json_output": json_output,
                "response_excerpt": text[:200],
            },
        )
        # Full prompt + raw response → separate JSONL for debugging /
        # prompt-tuning workflows. Gated by config.logging.log_full_llm_prompts
        # so it can be turned off in production once prompts are stable.
        # When off, the compact audit trail in ai_compute.jsonl + the
        # AIComputeLog DB table is still written (those are unconditional).
        if get_config().logging.log_full_llm_prompts:
            prompt_log().info(
                "llm full call",
                extra={
                    "event": "llm_full_call", "mode": "openai",
                    "model": self._cfg.model, "duration_seconds": duration,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "json_output": json_output,
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                    "response": text,
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
            resp = self._client.embeddings.create(
                model=self._cfg.embed_model, input=text,
            )
        except Exception as e:
            sys.error(
                "openai embeddings failed",
                extra={
                    "event": "llm_error", "mode": "openai",
                    "model": self._cfg.embed_model, "error": str(e),
                    "duration_seconds": round(time.time() - t0, 3),
                },
            )
            raise

        duration = round(time.time() - t0, 3)
        vec = list(resp.data[0].embedding) if resp.data else []

        log.info(
            "llm embed",
            extra={
                "event": "llm_embed", "mode": "openai",
                "model": self._cfg.embed_model,
                "duration_seconds": duration, "vector_dim": len(vec),
            },
        )
        return EmbeddingResult(vector=vec, model=self._cfg.embed_model)
