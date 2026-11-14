"""OpenAI-compatible chat providers (Groq, DeepSeek) over async httpx.

Each provider gets `timeout_s` in total (retry included) and at most ONE retry,
only for fast transient failures (connection error, 429, 5xx). A timeout is not
retried: the budget is spent. Errors are reported as `ProviderError` with a
short reason that never contains the API key, the request or the response body.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ProviderError(Exception):
    """A provider did not produce an answer. `str()` is safe to log."""


class ChatProvider(Protocol):
    name: str

    async def complete(self, messages: list[dict[str, str]]) -> str: ...


@dataclass
class OpenAICompatibleProvider:
    name: str
    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout_s: float = 8.0
    max_tokens: int = 600
    # Provider-specific request fields (reasoning switches), see build_providers().
    extra_body: dict[str, Any] = field(default_factory=dict)
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)  # tests

    async def complete(self, messages: list[dict[str, str]]) -> str:
        try:
            return await asyncio.wait_for(self._with_retry(messages), timeout=self.timeout_s)
        except TimeoutError as exc:
            raise ProviderError(f"{self.name}: timeout after {self.timeout_s:g} s") from exc

    async def _with_retry(self, messages: list[dict[str, str]]) -> str:
        start = time.monotonic()
        try:
            return await self._once(messages)
        except _Retryable as first:
            # Retry once, only if at least half of the budget is left.
            if time.monotonic() - start > self.timeout_s / 2:
                raise ProviderError(f"{self.name}: {first}") from None
            await asyncio.sleep(0.3)
            try:
                return await self._once(messages)
            except _Retryable as second:
                raise ProviderError(f"{self.name}: {second} (after 1 retry)") from None

    async def _once(self, messages: list[dict[str, str]]) -> str:
        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": 0.3,
            "stream": False,
            **self.extra_body,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s, transport=self.transport) as client:
                res = await client.post(f"{self.base_url.rstrip('/')}/chat/completions",
                                        json=body, headers=headers)
        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as exc:
            raise _Retryable(f"connection error ({type(exc).__name__})") from None
        except httpx.TimeoutException as exc:
            raise ProviderError(f"{self.name}: timeout ({type(exc).__name__})") from None
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name}: HTTP client error ({type(exc).__name__})") from None

        if res.status_code in RETRYABLE_STATUS:
            raise _Retryable(f"HTTP {res.status_code}")
        if res.status_code != 200:
            raise ProviderError(f"{self.name}: HTTP {res.status_code}")
        try:
            content = res.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProviderError(f"{self.name}: unexpected response shape") from None
        if not isinstance(content, str) or not content.strip():
            raise ProviderError(f"{self.name}: empty answer")
        return content.strip()


class _Retryable(Exception):
    pass


def build_providers(cfg) -> list[OpenAICompatibleProvider]:
    """Groq first, DeepSeek second; a provider without an API key is skipped."""
    providers = []
    if cfg.groq_api_key:
        providers.append(OpenAICompatibleProvider(
            name="groq", base_url=cfg.groq_base_url, api_key=cfg.groq_api_key, model=cfg.groq_model,
            timeout_s=cfg.provider_timeout_s, max_tokens=cfg.max_tokens,
            # gpt-oss on Groq: minimal reasoning, and keep it out of the response
            # (https://console.groq.com/docs/reasoning). Only for gpt-oss models.
            extra_body={"reasoning_effort": "low", "include_reasoning": False}
            if "gpt-oss" in cfg.groq_model else {},
        ))
    if cfg.deepseek_api_key:
        providers.append(OpenAICompatibleProvider(
            name="deepseek", base_url=cfg.deepseek_base_url, api_key=cfg.deepseek_api_key,
            model=cfg.deepseek_model, timeout_s=cfg.provider_timeout_s, max_tokens=cfg.max_tokens,
            # Thinking is on by default for deepseek-flash: off, to answer within the timeout
            # (https://api-docs.deepseek.com/guides/thinking_mode).
            extra_body={"thinking": {"type": "disabled"}},
        ))
    return providers
