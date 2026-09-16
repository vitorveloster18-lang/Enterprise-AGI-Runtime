"""Model Gateway: the agent never talks to a vendor directly.

    Agent -> Model Gateway -> Provider
             (routing by capability, cost, latency, privacy, policy, availability)

Fase 2: custo, latência e orçamento são cidadãos de primeira classe — cada chamada
é precificada, registrada e limitada pelo Runtime.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..core.config import BudgetConfig, ProviderConfig
from ..core.errors import (
    BudgetExceeded,
    NoProviderAvailable,
    PolicyDenied,
    ProviderError,
)
from ..core.ids import new_id
from ..core.logging import get_logger
from ..core.timeutil import iso
from ..domain.enums import EventType
from ..security.data_boundary import check_external

LOGGER = get_logger("egr.models")


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
    cost: float = 0.0
    raw: dict | None = None


class ModelProvider(ABC):
    """Abstract contract every provider must satisfy."""

    type = "base"

    def __init__(self, config: ProviderConfig, timeout: int = 120, api_key: str | None = None):
        self.config = config
        self.timeout = timeout
        #: chave resolvida fora do config (ex.: cofre cifrado) — nunca volta para o YAML
        self.api_key_override = api_key

    def api_key(self) -> str | None:
        return self.api_key_override or self.config.api_key()

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

    # ---- cost --------------------------------------------------------
    def normalize_usage(self, usage: dict | None) -> tuple[int, int]:
        """Return (input_tokens, output_tokens) in a provider-agnostic way."""

        usage = usage or {}
        return (
            int(usage.get("prompt_tokens", 0) or 0),
            int(usage.get("completion_tokens", 0) or 0),
        )

    def estimate_cost(self, usage: dict | None) -> float:
        tokens_in, tokens_out = self.normalize_usage(usage)
        pricing = self.config.pricing
        return round(
            tokens_in * pricing.input_per_token
            + tokens_out * pricing.output_per_token
            + pricing.per_call,
            8,
        )

    @property
    def unit_cost(self) -> float:
        """Estimated cost of ~1k mixed tokens — used by the `cost` routing strategy."""

        pricing = self.config.pricing
        return ((pricing.input_per_1m + pricing.output_per_1m) / 2.0) / 1000.0

    # ---- behaviour ---------------------------------------------------
    @abstractmethod
    def complete(self, request: CompletionRequest) -> CompletionResponse: ...

    def health(self) -> tuple[bool, str]:
        return True, "unknown"

    def describe(self) -> dict[str, Any]:
        pricing = self.config.pricing
        return {
            "name": self.name,
            "type": self.type,
            "model": self.config.model,
            "capabilities": sorted(self.capabilities),
            "external": self.external,
            "priority": self.priority,
            "enabled": self.config.enabled,
            "base_url": self.config.base_url,
            "unit_cost": round(self.unit_cost, 8),
            "pricing": {
                "currency": pricing.currency,
                "input_per_1m": pricing.input_per_1m,
                "output_per_1m": pricing.output_per_1m,
            },
        }


class ModelGateway:
    """Routes a capability request to the best available provider — and pays the bill."""

    def __init__(
        self,
        providers: list[ModelProvider],
        audit=None,
        external_ai: str = "allowed",
        sanitize_external: bool = True,
        timeout: int = 120,
        usage_repository=None,
        budget: BudgetConfig | None = None,
        routing: str = "priority",
    ):
        self.providers = [provider for provider in providers if provider.config.enabled]
        self.audit = audit
        self.external_ai = external_ai
        self.sanitize_external = sanitize_external
        self.timeout = timeout
        self.usage = usage_repository
        self.budget = budget or BudgetConfig()
        self.routing = routing

    # ---- routing -----------------------------------------------------
    def candidates(
        self,
        capability: str,
        allow_external: bool = True,
        strategy: str | None = None,
    ) -> list[ModelProvider]:
        strategy = strategy or self.routing
        pool = [
            provider
            for provider in self.providers
            if provider.supports(capability) and (allow_external or not provider.external)
        ]
        fallback = [provider for provider in self.providers if allow_external or not provider.external]
        pool = pool + [provider for provider in fallback if provider not in pool]

        if strategy == "cost":
            return sorted(pool, key=lambda provider: (provider.unit_cost, -provider.priority, provider.name))
        if strategy == "local_first":
            return sorted(pool, key=lambda provider: (provider.external, -provider.priority, provider.name))
        return sorted(pool, key=lambda provider: (-provider.priority, provider.name))

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

        self._enforce_budget(task_id, environment=environment, agent_id=agent_id)

        candidates = self.candidates(request.capability, allow)
        if not candidates:
            external_blocked = [
                provider for provider in self.candidates(request.capability, True) if provider.external
            ]
            if external_blocked:
                result = check_external(
                    "\n".join(message.content for message in request.messages), "forbidden"
                )
                if self.audit:
                    self.audit.record(
                        EventType.DATA_BLOCKED,
                        actor=agent_id or "runtime",
                        task_id=task_id,
                        agent_id=agent_id,
                        environment=environment or "development",
                        payload={"providers": [p.name for p in external_blocked], "reason": result.reason},
                    )
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
                self._record(
                    provider=provider,
                    request=prepared,
                    response=None,
                    task_id=task_id,
                    agent_id=agent_id,
                    environment=environment,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    status="error",
                    error=str(exc),
                )
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
            response.cost = provider.estimate_cost(response.usage)
            self._record(
                provider=provider,
                request=prepared,
                response=response,
                task_id=task_id,
                agent_id=agent_id,
                environment=environment,
                latency_ms=response.latency_ms,
                status="ok",
            )
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
                        "cost": response.cost,
                        "chars": len(response.text),
                    },
                )
            return response

        raise NoProviderAvailable(f"all providers failed: {last_error}")

    # ---- budget ------------------------------------------------------
    def _enforce_budget(self, task_id: str | None, environment: str | None, agent_id: str | None) -> None:
        if self.budget is None or self.usage is None:
            return
        checks: list[tuple[str, float | None, float]] = []
        if self.budget.per_task is not None and task_id:
            checks.append(("task", self.budget.per_task, self.usage.totals(task_id=task_id)["total_cost"]))
        if self.budget.per_day is not None:
            checks.append(("day", self.budget.per_day, self.usage.totals_today()["total_cost"]))

        for scope, limit, spent in checks:
            if limit is not None and spent >= limit:
                self._on_budget_exceeded(scope, limit, spent, task_id, agent_id, environment)
                if self.budget.on_exceeded == "deny":
                    raise BudgetExceeded(scope, limit, spent, self.budget.currency)

    def _on_budget_exceeded(
        self,
        scope: str,
        limit: float,
        spent: float,
        task_id: str | None,
        agent_id: str | None,
        environment: str | None,
    ) -> None:
        payload = {
            "scope": scope,
            "limit": limit,
            "spent": round(spent, 8),
            "currency": self.budget.currency if self.budget else "USD",
            "action": self.budget.on_exceeded if self.budget else "deny",
        }
        if self.audit:
            self.audit.record(
                EventType.MODEL_BUDGET_BLOCKED,
                actor=agent_id or "runtime",
                task_id=task_id,
                agent_id=agent_id,
                environment=environment or "development",
                payload=payload,
            )
        message = f"model budget exceeded ({scope}): {spent:.6f} >= {limit:.6f}"
        if self.budget and self.budget.on_exceeded == "warn":
            LOGGER.warning("%s — continuing (on_exceeded=warn)", message)
        else:
            LOGGER.error("%s — call blocked", message)

    # ---- telemetry ---------------------------------------------------
    def _record(
        self,
        *,
        provider: ModelProvider,
        request: CompletionRequest,
        response: CompletionResponse | None,
        task_id: str | None,
        agent_id: str | None,
        environment: str | None,
        latency_ms: int,
        status: str,
        error: str | None = None,
    ) -> None:
        if self.usage is None:
            return
        tokens_in, tokens_out = provider.normalize_usage(response.usage if response else None)
        self.usage.record(
            {
                "id": new_id("run"),
                "task_id": task_id,
                "agent_id": agent_id,
                "provider": provider.name,
                "model": (response.model if response else None) or provider.config.model,
                "capability": request.capability,
                "external": provider.external,
                "latency_ms": latency_ms,
                "input_tokens": tokens_in,
                "output_tokens": tokens_out,
                "cost": response.cost if response else 0.0,
                "status": status,
                "error": error,
                "created_at": iso(),
            }
        )

    def spend(self, task_id: str | None = None) -> dict:
        """Aggregated spend for a task (or for the whole workspace)."""

        if self.usage is None:
            return {"calls": 0, "total_cost": 0.0, "input_tokens": 0, "output_tokens": 0,
                    "avg_latency_ms": 0, "by_provider": [], "by_model": []}
        return self.usage.totals(task_id=task_id)

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
        return [provider.describe() for provider in self.candidates("reasoning", True)]

    def health(self) -> dict[str, dict[str, Any]]:
        report: dict[str, dict[str, Any]] = {}
        for provider in sorted(self.providers, key=lambda p: (-p.priority, p.name)):
            ok, detail = provider.health()
            report[provider.name] = {**provider.describe(), "healthy": ok, "detail": detail}
        return report


def build_providers(
    configs: list[ProviderConfig],
    timeout: int = 120,
    secret_resolver: Callable[[str | None], str | None] | None = None,
) -> list[ModelProvider]:
    """Factory: build provider instances from configuration (imports are lazy).

    `secret_resolver` permite que a chave venha do cofre cifrado (`vault:NOME`)
    sem que o segredo passe por `egr.yaml` ou pelo config em memória no disco.
    """

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
        api_key = secret_resolver(config.api_key_env) if secret_resolver else None
        providers.append(provider_class(config, timeout=timeout, api_key=api_key))
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
