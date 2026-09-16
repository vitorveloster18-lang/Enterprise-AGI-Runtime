"""External provider: any OpenAI-compatible API (OpenAI, Groq, OpenRouter, vLLM, LM Studio).

Crossing the enterprise boundary is governed by the Data Boundary + Policy layers,
never by the provider itself.
"""

from __future__ import annotations

import httpx

from ..gateway import CompletionRequest, CompletionResponse, ModelProvider, ProviderUnavailable


class OpenAICompatProvider(ModelProvider):
    type = "openai_compat"

    @property
    def base_url(self) -> str:
        return (self.config.base_url or "https://api.openai.com/v1").rstrip("/")

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        api_key = self.config.api_key()
        if not api_key:
            raise ProviderUnavailable(
                f"provider '{self.name}' has no API key: set ${self.config.api_key_env or 'OPENAI_API_KEY'}"
            )
        payload = {
            "model": self.config.model or "gpt-4o-mini",
            "messages": [{"role": message.role, "content": message.content} for message in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            **self.config.options,
        }
        if request.json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"provider '{self.name}' failed: {exc}") from exc

        choices = data.get("choices") or []
        text = (choices[0].get("message") or {}).get("content", "") if choices else ""
        return CompletionResponse(
            text=text,
            provider=self.name,
            model=data.get("model", self.config.model or "unknown"),
            latency_ms=0,
            usage=data.get("usage", {}) or {},
            external=True,
            raw=data if isinstance(data, dict) else None,
        )

    def health(self) -> tuple[bool, str]:
        if not self.config.api_key():
            return False, f"missing ${self.config.api_key_env or 'OPENAI_API_KEY'}"
        try:
            response = httpx.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.config.api_key()}"},
                timeout=5.0,
            )
            return (True, f"HTTP {response.status_code}") if response.status_code == 200 else (
                False,
                f"HTTP {response.status_code}",
            )
        except Exception as exc:
            return False, f"unreachable ({type(exc).__name__})"


__all__ = ["OpenAICompatProvider"]
