"""Local model provider: Ollama (or any Ollama-compatible local server)."""

from __future__ import annotations

import httpx

from ..gateway import (
    CompletionRequest,
    CompletionResponse,
    ModelProvider,
    ProviderUnavailable,
)


class OllamaProvider(ModelProvider):
    """Local inference. Keeps data inside the machine: no data boundary crossing."""

    type = "ollama"

    @property
    def base_url(self) -> str:
        return (self.config.base_url or "http://localhost:11434").rstrip("/")

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        payload = {
            "model": self.config.model or "llama3.1",
            "messages": [{"role": message.role, "content": message.content} for message in request.messages],
            "stream": False,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_tokens,
                **self.config.options,
            },
        }
        if request.json_mode:
            payload["format"] = "json"
        try:
            response = httpx.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"ollama '{self.name}' unreachable at {self.base_url}: {exc}") from exc

        text = (data.get("message") or {}).get("content", "")
        return CompletionResponse(
            text=text,
            provider=self.name,
            model=data.get("model", self.config.model or "unknown"),
            latency_ms=int(data.get("total_duration", 0) / 1e6) if data.get("total_duration") else 0,
            usage={
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "completion_tokens": data.get("eval_count", 0),
            },
            external=False,
            raw=data if isinstance(data, dict) else None,
        )

    def health(self) -> tuple[bool, str]:
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=5.0)
            if response.status_code != 200:
                return False, f"HTTP {response.status_code}"
            models = [item.get("name") for item in response.json().get("models", [])]
            return True, f"{len(models)} model(s) available" + (f": {', '.join(models[:3])}" if models else "")
        except Exception as exc:
            return False, f"unreachable ({type(exc).__name__})"



__all__ = ["OllamaProvider"]
