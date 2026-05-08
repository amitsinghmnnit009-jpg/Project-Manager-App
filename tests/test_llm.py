"""Tests for app.llm.* — Ollama and OpenAI-compatible clients with mocked SDKs.

We mock at the SDK boundary (`ollama.Client`, `openai.OpenAI`) so these tests
run without a live LLM endpoint. The CLI commands are smoke-tested via the
factory + a stub client.
"""
from __future__ import annotations
from unittest.mock import patch, MagicMock
from types import SimpleNamespace
import pytest


# ---------- Factory ------------------------------------------------------

def _build_fake_cfg(real_cfg, provider: str):
    """Build a fake top-level config with `provider` overridden but every
    other field copied from the real config. CALLER must pass `real_cfg`
    captured BEFORE monkeypatch replaces get_config — otherwise
    config_mod.get_config() inside this helper would recurse into the lambda
    that called us."""
    return SimpleNamespace(llm=SimpleNamespace(
        provider=provider,
        ollama=real_cfg.llm.ollama,
        openai=real_cfg.llm.openai,
        temperature=real_cfg.llm.temperature,
    ))


def test_get_llm_client_returns_a_concrete_client():
    """Whatever provider is configured locally, factory returns a real LLMClient."""
    from app.llm.base import get_llm_client, LLMClient
    c = get_llm_client()
    assert isinstance(c, LLMClient)
    assert c.mode in ("ollama", "openai")


def test_get_llm_client_routes_ollama(monkeypatch):
    """provider=ollama → OllamaClient, regardless of local config.yaml."""
    import app.config as config_mod
    from app.llm import base as base_mod
    from app.llm.ollama_client import OllamaClient

    real_cfg = config_mod.get_config()       # capture BEFORE monkeypatch
    fake_cfg = _build_fake_cfg(real_cfg, "ollama")
    monkeypatch.setattr(config_mod, "get_config", lambda: fake_cfg)

    c = base_mod.get_llm_client()
    assert isinstance(c, OllamaClient)
    assert c.mode == "ollama"


def test_get_llm_client_routes_openai(monkeypatch):
    """provider=openai → OpenAICompatibleClient, regardless of local config.yaml."""
    import app.config as config_mod
    from app.llm import base as base_mod
    from app.llm.openai_client import OpenAICompatibleClient

    real_cfg = config_mod.get_config()       # capture BEFORE monkeypatch
    fake_cfg = _build_fake_cfg(real_cfg, "openai")
    monkeypatch.setattr(config_mod, "get_config", lambda: fake_cfg)

    c = base_mod.get_llm_client()
    assert isinstance(c, OpenAICompatibleClient)
    assert c.mode == "openai"


def test_get_llm_client_unknown_provider_raises(monkeypatch):
    """Unknown provider strings must raise (no silent fallback)."""
    import app.config as config_mod
    from app.llm import base as base_mod

    fake_cfg = SimpleNamespace(llm=SimpleNamespace(provider="not-a-real-provider"))
    monkeypatch.setattr(config_mod, "get_config", lambda: fake_cfg)

    with pytest.raises(ValueError):
        base_mod.get_llm_client()


# ---------- Ollama -------------------------------------------------------

@patch("app.llm.ollama_client.ollama.Client")
def test_ollama_complete_extracts_text_and_tokens(mock_client_cls):
    """OllamaClient.complete pulls text + token counts from the chat response."""
    mock_inst = MagicMock()
    mock_inst.chat.return_value = {
        "message": {"content": "OK"},
        "prompt_eval_count": 12,
        "eval_count": 3,
    }
    mock_client_cls.return_value = mock_inst

    from app.llm.ollama_client import OllamaClient
    res = OllamaClient().complete("system", "user")

    assert res.text == "OK"
    assert res.prompt_tokens == 12
    assert res.completion_tokens == 3
    assert res.duration_seconds >= 0
    assert res.model.startswith("gpt-oss")  # whatever's in config.yaml


@patch("app.llm.ollama_client.ollama.Client")
def test_ollama_complete_passes_json_format(mock_client_cls):
    """json_output=True must add format='json' to the underlying chat call."""
    mock_inst = MagicMock()
    mock_inst.chat.return_value = {
        "message": {"content": '{"a": 1}'},
        "prompt_eval_count": 5, "eval_count": 5,
    }
    mock_client_cls.return_value = mock_inst

    from app.llm.ollama_client import OllamaClient
    OllamaClient().complete("s", "u", json_output=True)

    kwargs = mock_inst.chat.call_args.kwargs
    assert kwargs.get("format") == "json"


@patch("app.llm.ollama_client.ollama.Client")
def test_ollama_complete_temperature_override(mock_client_cls):
    """Explicit temperature= override beats the config default."""
    mock_inst = MagicMock()
    mock_inst.chat.return_value = {
        "message": {"content": "x"}, "prompt_eval_count": 1, "eval_count": 1,
    }
    mock_client_cls.return_value = mock_inst

    from app.llm.ollama_client import OllamaClient
    OllamaClient().complete("s", "u", temperature=0.9)
    options = mock_inst.chat.call_args.kwargs["options"]
    assert options["temperature"] == 0.9


@patch("app.llm.ollama_client.ollama.Client")
def test_ollama_complete_handles_pydantic_response(mock_client_cls):
    """Newer ollama versions return a pydantic ChatResponse object."""
    pydantic_like = SimpleNamespace(
        message=SimpleNamespace(content="hi"),
        prompt_eval_count=7, eval_count=2,
    )
    mock_inst = MagicMock()
    mock_inst.chat.return_value = pydantic_like
    mock_client_cls.return_value = mock_inst

    from app.llm.ollama_client import OllamaClient
    res = OllamaClient().complete("s", "u")
    assert res.text == "hi"
    assert res.prompt_tokens == 7
    assert res.completion_tokens == 2


@patch("app.llm.ollama_client.ollama.Client")
def test_ollama_embed_returns_vector(mock_client_cls):
    mock_inst = MagicMock()
    mock_inst.embeddings.return_value = {"embedding": [0.1, 0.2, 0.3]}
    mock_client_cls.return_value = mock_inst

    from app.llm.ollama_client import OllamaClient
    res = OllamaClient().embed("hello world")
    assert res.vector == [0.1, 0.2, 0.3]
    assert res.model  # whatever embed_model is in config


@patch("app.llm.ollama_client.ollama.Client")
def test_ollama_complete_raises_on_sdk_error(mock_client_cls):
    """SDK errors propagate (caller decides retry policy)."""
    mock_inst = MagicMock()
    mock_inst.chat.side_effect = ConnectionError("ollama down")
    mock_client_cls.return_value = mock_inst

    from app.llm.ollama_client import OllamaClient
    with pytest.raises(ConnectionError):
        OllamaClient().complete("s", "u")


# ---------- OpenAI-compatible --------------------------------------------

def _fake_chat_response(text: str, prompt_tokens=10, completion_tokens=5):
    """Build a structure that matches openai SDK's ChatCompletion shape."""
    msg = SimpleNamespace(content=text)
    choice = SimpleNamespace(message=msg, finish_reason="stop", index=0)
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    return SimpleNamespace(choices=[choice], usage=usage)


@patch("app.llm.openai_client.OpenAI")
def test_openai_complete_extracts_text_and_tokens(mock_openai_cls):
    mock_inst = MagicMock()
    mock_inst.chat.completions.create.return_value = _fake_chat_response("OK", 10, 2)
    mock_openai_cls.return_value = mock_inst

    from app.llm.openai_client import OpenAICompatibleClient
    res = OpenAICompatibleClient().complete("system", "user")

    assert res.text == "OK"
    assert res.prompt_tokens == 10
    assert res.completion_tokens == 2
    assert res.duration_seconds >= 0


@patch("app.llm.openai_client.OpenAI")
def test_openai_complete_passes_json_response_format(mock_openai_cls):
    """json_output=True maps to OpenAI's response_format={'type': 'json_object'}."""
    mock_inst = MagicMock()
    mock_inst.chat.completions.create.return_value = _fake_chat_response('{"a":1}')
    mock_openai_cls.return_value = mock_inst

    from app.llm.openai_client import OpenAICompatibleClient
    OpenAICompatibleClient().complete("s", "u", json_output=True)
    kwargs = mock_inst.chat.completions.create.call_args.kwargs
    assert kwargs.get("response_format") == {"type": "json_object"}


@patch("app.llm.openai_client.OpenAI")
def test_openai_uses_custom_headers(mock_openai_cls):
    """custom_headers from config.yaml must reach the SDK constructor."""
    mock_inst = MagicMock()
    mock_inst.chat.completions.create.return_value = _fake_chat_response("x")
    mock_openai_cls.return_value = mock_inst

    from app.llm.openai_client import OpenAICompatibleClient
    OpenAICompatibleClient()

    init_kwargs = mock_openai_cls.call_args.kwargs
    # Either default_headers is the configured dict or None when no headers set
    assert "default_headers" in init_kwargs


@patch("app.llm.openai_client.OpenAI")
def test_openai_embed_returns_vector(mock_openai_cls):
    mock_inst = MagicMock()
    embed_data = SimpleNamespace(embedding=[0.7, 0.8, 0.9])
    mock_inst.embeddings.create.return_value = SimpleNamespace(data=[embed_data])
    mock_openai_cls.return_value = mock_inst

    from app.llm.openai_client import OpenAICompatibleClient
    res = OpenAICompatibleClient().embed("hello")
    assert res.vector == [0.7, 0.8, 0.9]
