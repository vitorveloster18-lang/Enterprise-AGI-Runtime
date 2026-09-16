"""Model Gateway: the agent never talks to a vendor directly.

    Agent -> Model Gateway -> Provider
             (routing by capability, cost, latency, privacy, policy, availability)
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..core.config import ProviderConfig
from ..core.errors import NoProviderAvailable, PolicyDenied, ProviderError
from ..domain.enums import EventType
from ..security.data_boundary import check_external


class ProviderUnavailable(ProviderError):
    """Provider could not serve the request (missing key, network, HTTP error)."""


@dataclass
class Message:
    role: str  # system | user | assistant
    content: str


@dataclass
class CompletionRequest:
    messages: list[Message]
    capability: str = "reasoning"
    temperature: float = 0.2
    max_tokens: int = 2048
    json_mode: bool = False
    metadata: dict = field(default_factory=dict)


@dataclass
class CompletionResponse:
    text: str
    provider: str
    model: str
    latency_ms: int = 0
    usage: dict = field(default_factory=dict)
    external: bool = False
    raw: dict | None = None


class ModelProvider(ABC):
    """Abstract contract every provider must satisfy."""

    type = "base"

    def __init__(self, config: ProviderConfig, timeout: int = 120):
        self.config = config
        self.timeout = timeout

    # ---- identity ----------------------------------------------------
    @property
    def name(self) -> str:
        return self.config.name

    @property
    def capabilities(self) -> set[str]:
        return set(self.config.capabilities or ["reasoning"])

    @property
    def external(self) -> bool:
        return bool(self.config.external)

    @property
    def priority(self) -> int:
        return self.config.priority

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities or "*" in self.capabilities

    # ---- behaviour ---------------------------------------------------
    @abstractmethod
    def complete(self, request: CompletionRequest) -> CompletionResponse: ...

    def health(self) -> tuple[bool, str]:
        return True, "unknown"

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "model": self.config.model,
            "capabilities": sorted(self.capabilities),
            "external": self.external,
            "priority": self.priority,
            "enabled": self.config.enabled,
            "base_url": self.config.base_url,
        }


class ModelGateway:
    """Routes a capability request to the best available provider."""

    def __init__(
        self,
        providers: list[ModelProvider],
        audit=None,
        external_ai: str = "allowed",
        sanitize_external: bool = True,
        timeout: int = 120,
    ):
        self.providers = [provider for provider in providers if provider.config.enabled]
        self.audit = audit
        self.external_ai = external_ai
        self.sanitize_external = sanitize_external
        self.timeout = timeout

    # ---- routing -----------------------------------------------------
    def candidates(self, capability: str, allow_external: bool = True) -> list[ModelProvider]:
        ordered = sorted(self.providers, key=lambda provider: (-provider.priority, provider.name))
        matching = [
            provider
            for provider in ordered
            if provider.supports(capability) and (allow_external or not provider.external)
        ]
        fallback = [
            provider for provider in ordered if allow_external or not provider.external
        ]
        return matching + [provider for provider in fallback if provider not in matching]

    def route(self, capability: str = "reasoning", allow_external: bool = True) -> ModelProvider:
        for provider in self.candidates(capability, allow_external):
            return provider
        raise NoProviderAvailable(
            f"no provider available for capability '{capability}' (allow_external={allow_external})"
        )

    # ---- completion --------------------------------------------------
    def complete(
        self,
        request: CompletionRequest,
        *,
        allow_external: bool | None = None,
        task_id: str | None = None,
        agent_id: str | None = None,
        environment: str | None = None,
    ) -> CompletionResponse:
        allow = self.external_ai != "forbidden" if allow_external is None else allow_external
        candidates = self.candidates(request.capability, allow)
        if not candidates:
            external_blocked = [
                provider for provider in self.candidates(request.capability, True) if provider.external
            ]
            if external_blocked:
                # Data Boundary: say *why* it was blocked, and record it.
                result = check_external(
                    "\n".join(message.content for message in request.messages), "forbidden"
                )
                self.audit.record(
                    EventType.DATA_BLOCKED,
                    actor=agent_id or "runtime",
                    task_id=task_id,
                    agent_id=agent_id,
                    environment=environment or "development",
                    payload={"providers": [p.name for p in external_blocked], "reason": result.reason},
                ) if self.audit else None
                raise PolicyDenied(result.reason)
            raise NoProviderAvailable(
                f"no provider for capability '{request.capability}' (external blocked by policy)"
            )

        last_error: Exception | None = None
        for provider in candidates:
            prepared = self._apply_data_boundary(request, provider, task_id, agent_id, environment)
            started = time.perf_counter()
            try:
                response = provider.complete(prepared)
            except ProviderUnavailable as exc:
                last_error = exc
                if self.audit:
                    self.audit.record(
                        EventType.MODEL_FAILED,
                        actor=agent_id or "runtime",
                        task_id=task_id,
                        agent_id=agent_id,
                        environment=environment or "development",
                        payload={"provider": provider.name, "error": str(exc)},
                    )
                continue
            elapsed = int((time.perf_counter() - started) * 1000)
            response.latency_ms = response.latency_ms or elapsed
            if self.audit:
                self.audit.record(
                    EventType.MODEL_CALLED,
                    actor=agent_id or "runtime",
                    task_id=task_id,
                    agent_id=agent_id,
                    environment=environment or "development",
                    payload={
                        "provider": provider.name,
                        "model": response.model,
                        "capability": request.capability,
                        "latency_ms": response.latency_ms,
                        "external": response.external,
                        "usage": response.usage,
                        "chars": len(response.text),
                    },
                )
            return response

        raise NoProviderAvailable(f"all providers failed: {last_error}")

    # ---- data boundary -----------------------------------------------
    def _apply_data_boundary(
        self,
        request: CompletionRequest,
        provider: ModelProvider,
        task_id: str | None,
        agent_id: str | None,
        environment: str | None,
    ) -> CompletionRequest:
        if not provider.external:
            return request
        text = "\n".join(message.content for message in request.messages)
        result = check_external(text, self.external_ai)
        labels = result.labels
        if self.audit and labels:
            self.audit.record(
                EventType.DATA_CLASSIFIED,
                actor=agent_id or "runtime",
                task_id=task_id,
                agent_id=agent_id,
                environment=environment or "development",
                payload={"provider": provider.name, "labels": labels},
            )
        if not result.allowed:
            if self.audit:
                self.audit.record(
                    EventType.DATA_BLOCKED,
                    actor=agent_id or "runtime",
                    task_id=task_id,
                    agent_id=agent_id,
                    environment=environment or "development",
                    payload={"provider": provider.name, "reason": result.reason},
                )
            raise PolicyDenied(result.reason)
        if self.sanitize_external and result.modified:
            from ..security.data_boundary import sanitize_payload

            sanitized, _ = sanitize_payload([message.content for message in request.messages])
            contents = sanitized if isinstance(sanitized, list) else [str(sanitized)]
            request = CompletionRequest(
                messages=[
                    Message(role=message.role, content=content)
                    for message, content in zip(request.messages, contents, strict=False)
                ],
                capability=request.capability,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                json_mode=request.json_mode,
                metadata=request.metadata,
            )
            if self.audit:
                self.audit.record(
                    EventType.DATA_SANITIZED,
                    actor=agent_id or "runtime",
                    task_id=task_id,
                    agent_id=agent_id,
                    environment=environment or "development",
                    payload={"provider": provider.name, "labels": labels},
                )
        return request

    # ---- introspection -----------------------------------------------
    def list_providers(self) -> list[dict[str, Any]]:
        return [provider.describe() for provider in sorted(self.providers, key=lambda p: (-p.priority, p.name))]

    def health(self) -> dict[str, dict[str, Any]]:
        report: dict[str, dict[str, Any]] = {}
        for provider in self.providers:
            ok, detail = provider.health()
            report[provider.name] = {**provider.describe(), "healthy": ok, "detail": detail}
        return report


def build_providers(configs: list[ProviderConfig], timeout: int = 120) -> list[ModelProvider]:
    """Factory: build provider instances from configuration (imports are lazy)."""

    from .providers.echo import EchoProvider
    from .providers.ollama import OllamaProvider
    from .providers.openai_compat import OpenAICompatProvider

    registry = {
        EchoProvider.type: EchoProvider,
        OllamaProvider.type: OllamaProvider,
        OpenAICompatProvider.type: OpenAICompatProvider,
    }
    providers: list[ModelProvider] = []
    for config in configs:
        provider_class = registry.get(config.type)
        if provider_class is None:
            continue
        providers.append(provider_class(config, timeout=timeout))
    if not providers:
        providers.append(EchoProvider(ProviderConfig(name="echo", type="echo"), timeout=timeout))
    return providers


__all__ = [
    "CompletionRequest",
    "CompletionResponse",
    "Message",
    "ModelGateway",
    "ModelProvider",
    "ProviderUnavailable",
    "build_providers",
]
