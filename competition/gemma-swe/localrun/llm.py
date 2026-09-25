"""Cliente chat-completions OpenAI-compatível (AI Studio) + loop de tool-calls."""

from __future__ import annotations

import json

import httpx


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 300):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> dict:
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        try:
            response = httpx.post(
                self.url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"API falhou: {exc}") from exc
        data = response.json()
        usage = data.get("usage") or {}
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)
        return data

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def first_choice(data: dict) -> dict:
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"resposta sem choices: {json.dumps(data)[:300]}")
    return choices[0].get("message") or {}


def tool_calls_of(message: dict) -> list[dict]:
    calls = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        try:
            args = json.loads(function.get("arguments") or "{}")
        except ValueError:
            args = {"_raw": function.get("arguments")}
        calls.append({"id": call.get("id"), "name": function.get("name"), "args": args})
    return calls
