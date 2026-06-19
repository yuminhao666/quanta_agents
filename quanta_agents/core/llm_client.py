from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from typing import Any

import httpx

from quanta_agents.core.config import first_env, load_default_env


def _load_secret_env() -> None:
    load_default_env()


def split_keys(raw: str) -> list[str]:
    return [item.strip() for item in raw.replace("\n", ",").split(",") if item.strip()]


def _env_keys(*names: str) -> list[str]:
    return split_keys(",".join(os.environ.get(name, "") for name in names))


@dataclass(frozen=True)
class LLMProvider:
    name: str
    base_url: str
    api_keys: list[str]
    model: str
    api_key_hint: str
    extra_body: dict[str, Any] = field(default_factory=dict)


_PROVIDER_ALIASES = {
    "deepseek": "deepseek",
    "ds": "deepseek",
    "minimax": "minimax",
    "m3": "minimax",
    "minimax-m3": "minimax",
    "minimax_m3": "minimax",
    "minimax m3": "minimax",
    "mini-max": "minimax",
    "glm": "glm",
}


def normalize_provider_name(provider: str | None) -> str:
    raw = (provider or "").strip().lower()
    if not raw:
        return "deepseek"
    return _PROVIDER_ALIASES.get(raw, raw)


def _scoped_names(env_prefix: str, suffix: str) -> tuple[str, ...]:
    prefix = env_prefix.strip()
    if not prefix:
        return ()
    return (f"{prefix}_{suffix}",)


def _scoped_and_global_names(env_prefix: str, suffix: str) -> tuple[str, ...]:
    names = _scoped_names(env_prefix, suffix)
    if env_prefix.strip() == "QUANTA_AGENT_LLM":
        return names
    return (*names, *_scoped_names("QUANTA_AGENT_LLM", suffix))


def _minimax_extra_body() -> dict[str, Any]:
    raw_thinking = os.environ.get(
        "MINIMAX_THINKING",
        os.environ.get("M3_THINKING", "disabled"),
    ).strip()
    if not raw_thinking or raw_thinking.lower() in {"0", "false", "none", "off"}:
        return {}
    return {"thinking": {"type": raw_thinking}}


def get_provider(
    provider: str | None = None,
    *,
    env_prefix: str = "QUANTA_AGENT_LLM",
) -> LLMProvider:
    _load_secret_env()
    selected = normalize_provider_name(
        provider
        or first_env(
            *_scoped_and_global_names(env_prefix, "PROVIDER"),
            "LLM_PROVIDER",
            default="deepseek",
        )
    )
    scoped_key_names = (
        *_scoped_and_global_names(env_prefix, "API_KEYS"),
        *_scoped_and_global_names(env_prefix, "API_KEY"),
        "LLM_API_KEYS",
        "LLM_API_KEY",
    )
    scoped_base_url_names = (*_scoped_and_global_names(env_prefix, "BASE_URL"), "LLM_BASE_URL")
    scoped_model_names = (*_scoped_and_global_names(env_prefix, "MODEL"), "LLM_MODEL")

    if selected == "deepseek":
        return LLMProvider(
            name="deepseek",
            base_url=first_env(
                "DEEPSEEK_BASE_URL",
                *scoped_base_url_names,
                default="https://api.deepseek.com",
            ).rstrip("/"),
            api_keys=_env_keys("DEEPSEEK_API_KEYS", "DEEPSEEK_API_KEY", *scoped_key_names),
            model=first_env("DEEPSEEK_MODEL", *scoped_model_names, default="deepseek-chat"),
            api_key_hint="DEEPSEEK_API_KEY or DEEPSEEK_API_KEYS",
        )
    if selected == "minimax":
        return LLMProvider(
            name="minimax",
            base_url=first_env(
                "MINIMAX_BASE_URL",
                "M3_BASE_URL",
                *scoped_base_url_names,
                default="https://api.minimaxi.com/v1",
            ).rstrip("/"),
            api_keys=_env_keys(
                "MINIMAX_API_KEYS",
                "MINIMAX_API_KEY",
                "M3_API_KEYS",
                "M3_API_KEY",
                *scoped_key_names,
            ),
            model=first_env("MINIMAX_MODEL", "M3_MODEL", *scoped_model_names, default="MiniMax-M3"),
            api_key_hint="MINIMAX_API_KEY, MINIMAX_API_KEYS, M3_API_KEY, or M3_API_KEYS",
            extra_body=_minimax_extra_body(),
        )
    if selected == "glm":
        return LLMProvider(
            name="glm",
            base_url=first_env(
                "GLM_BASE_URL",
                *scoped_base_url_names,
                default="https://open.bigmodel.cn/api/paas/v4",
            ).rstrip("/"),
            api_keys=_env_keys("GLM_API_KEYS", "GLM_API_KEY", *scoped_key_names),
            model=first_env("GLM_MODEL", *scoped_model_names, default="glm-4-plus"),
            api_key_hint="GLM_API_KEY or GLM_API_KEYS",
        )
    raise RuntimeError(f"unsupported LLM provider: {selected}")


def _request_error(status_code: int, detail: str, provider: LLMProvider) -> RuntimeError:
    detail = str(detail or "")[:500]
    message = f"{provider.name} request failed: HTTP {status_code}"
    if detail:
        message += f" {detail}"
    return RuntimeError(message)


def chat_messages(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 900,
    temperature: float = 0.2,
    timeout: int = 80,
    provider: str | None = None,
    env_prefix: str = "QUANTA_AGENT_LLM",
) -> str:
    llm = get_provider(provider, env_prefix=env_prefix)
    if not llm.api_keys:
        raise RuntimeError(f"missing API key for {llm.name}; set {llm.api_key_hint}")
    body_data: dict[str, Any] = {
        "model": llm.model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    body_data.update(llm.extra_body)
    body = json.dumps(body_data, ensure_ascii=False).encode("utf-8")
    last_error: Exception | None = None
    for key in llm.api_keys:
        url = f"{llm.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        try:
            request_timeout = httpx.Timeout(
                timeout,
                connect=min(timeout, 15),
                read=timeout,
                write=min(timeout, 15),
                pool=min(timeout, 15),
            )
            with httpx.Client(timeout=request_timeout) as client:
                response = client.post(url, headers=headers, content=body)
                response.raise_for_status()
                payload = response.json()
            return str(payload["choices"][0]["message"]["content"])
        except httpx.HTTPStatusError as exc:
            last_error = _request_error(exc.response.status_code, exc.response.text, llm)
            continue
        except Exception as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    raise RuntimeError(f"no usable API key for {llm.name}")


def chat(
    prompt: str,
    *,
    max_tokens: int = 900,
    temperature: float = 0.2,
    timeout: int = 80,
    provider: str | None = None,
    env_prefix: str = "QUANTA_AGENT_LLM",
) -> str:
    return chat_messages(
        [{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
        provider=provider,
        env_prefix=env_prefix,
    )
