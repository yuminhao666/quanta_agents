from __future__ import annotations

import json

from quanta_agents.core import llm_client


LLM_ENV_NAMES = (
    "QUANTA_AGENT_LLM_PROVIDER",
    "QUANTA_AGENT_LLM_API_KEY",
    "QUANTA_AGENT_LLM_API_KEYS",
    "QUANTA_AGENT_LLM_BASE_URL",
    "QUANTA_AGENT_LLM_MODEL",
    "LLM_PROVIDER",
    "LLM_API_KEY",
    "LLM_API_KEYS",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_API_KEYS",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
    "MINIMAX_API_KEY",
    "MINIMAX_API_KEYS",
    "MINIMAX_BASE_URL",
    "MINIMAX_MODEL",
    "MINIMAX_THINKING",
    "M3_API_KEY",
    "M3_API_KEYS",
    "M3_BASE_URL",
    "M3_MODEL",
    "M3_THINKING",
)


def _clear_llm_env(monkeypatch):
    monkeypatch.setattr(llm_client, "_load_secret_env", lambda: None)
    for name in LLM_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_get_provider_accepts_m3_alias(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("QUANTA_AGENT_LLM_PROVIDER", "m3")
    monkeypatch.setenv("MINIMAX_API_KEY", "mini-key")

    provider = llm_client.get_provider()

    assert provider.name == "minimax"
    assert provider.base_url == "https://api.minimaxi.com/v1"
    assert provider.model == "MiniMax-M3"
    assert provider.api_keys == ["mini-key"]
    assert provider.extra_body == {"thinking": {"type": "disabled"}}


def test_chat_messages_sends_minimax_m3_request(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("MINIMAX_API_KEY", "mini-key")
    captured = {}

    class FakeResponse:
        status_code = 200
        text = '{"choices":[{"message":{"content":"ok"}}]}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def post(self, url, *, headers, content):
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = json.loads(content.decode("utf-8"))
            return FakeResponse()

    monkeypatch.setattr(llm_client.httpx, "Client", FakeClient)

    content = llm_client.chat_messages(
        [{"role": "user", "content": "hello"}],
        provider="m3",
        max_tokens=123,
        temperature=0.4,
        timeout=9,
    )

    assert content == "ok"
    assert captured["url"] == "https://api.minimaxi.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer mini-key"
    assert captured["body"] == {
        "model": "MiniMax-M3",
        "temperature": 0.4,
        "max_tokens": 123,
        "messages": [{"role": "user", "content": "hello"}],
        "thinking": {"type": "disabled"},
    }
    assert captured["timeout"].read == 9
