"""Extensões cognitivas: provider local validado + overflow/escalação.

Fatia 6 do fechamento do runtime: o Ollama é validado por contrato (httpx
dublado — sem rede nos testes) e o gateway ganha política de overflow:
truncate | escalate | deny, tudo auditado em `model.overflow`.
"""

from __future__ import annotations

import httpx
import pytest

from egr.core.config import ProviderConfig
from egr.core.errors import ContextOverflow
from egr.models.gateway import (
    CompletionRequest,
    CompletionResponse,
    Message,
    ModelGateway,
    ModelProvider,
    ProviderUnavailable,
    build_providers,
    estimate_tokens,
    truncate_to_fit,
)
from egr.models.providers.ollama import OllamaProvider

OVERFLOW = "model.overflow"


class FakeResponse:
    def __init__(self, payload=None, status_code: int = 200, error=None):
        self._payload = payload or {}
        self.status_code = status_code
        self._error = error

    def raise_for_status(self):
        if self._error:
            raise self._error

    def json(self):
        return self._payload


def _ollama(**over) -> OllamaProvider:
    config = ProviderConfig(name="local", type="ollama", model="llama3.1:8b")
    for key, value in over.items():
        setattr(config, key, value)
    return OllamaProvider(config, timeout=5)


def test_ollama_complete_ok(monkeypatch):
    seen = {}

    def fake_post(url, json=None, timeout=None):
        seen.update({"url": url, "json": json})
        return FakeResponse(
            {
                "message": {"content": "olá"},
                "model": "llama3.1:8b",
                "total_duration": 2_000_000_000,
                "prompt_eval_count": 10,
                "eval_count": 5,
            }
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    response = _ollama().complete(
        CompletionRequest(
            messages=[Message(role="user", content="oi")], temperature=0.1, max_tokens=64
        )
    )

    assert response.text == "olá"
    assert response.model == "llama3.1:8b"
    assert response.latency_ms == 2000
    assert response.usage == {"prompt_tokens": 10, "completion_tokens": 5}
    assert response.external is False
    assert seen["url"] == "http://localhost:11434/api/chat"
    assert seen["json"]["model"] == "llama3.1:8b"
    assert seen["json"]["stream"] is False
    assert seen["json"]["options"]["temperature"] == 0.1
    assert seen["json"]["options"]["num_predict"] == 64
    assert "format" not in seen["json"]


def test_ollama_json_mode_envia_format(monkeypatch):
    seen = {}

    def fake_post(url, json=None, timeout=None):
        seen["json"] = json
        return FakeResponse({"message": {"content": "{}"}})

    monkeypatch.setattr(httpx, "post", fake_post)
    _ollama().complete(CompletionRequest(messages=[Message(role="user", content="x")], json_mode=True))

    assert seen["json"]["format"] == "json"


def test_ollama_falha_vira_unavailable(monkeypatch):
    def fake_down(url, json=None, timeout=None):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", fake_down)
    with pytest.raises(ProviderUnavailable, match="unreachable"):
        _ollama().complete(CompletionRequest(messages=[Message(role="user", content="x")]))

    def fake_500(url, json=None, timeout=None):
        return FakeResponse({}, status_code=500, error=httpx.HTTPError("boom"))

    monkeypatch.setattr(httpx, "post", fake_500)
    with pytest.raises(ProviderUnavailable):
        _ollama().complete(CompletionRequest(messages=[Message(role="user", content="x")]))


def test_ollama_health(monkeypatch):
    monkeypatch.setattr(
        httpx, "get", lambda url, timeout=None: FakeResponse({"models": [{"name": "llama3.1:8b"}]})
    )
    ok, detail = _ollama().health()
    assert ok and "1 model" in detail

    monkeypatch.setattr(httpx, "get", lambda url, timeout=None: FakeResponse({}, status_code=500))
    ok, detail = _ollama().health()
    assert not ok and "HTTP 500" in detail

    def fake_down(url, timeout=None):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "get", fake_down)
    ok, detail = _ollama().health()
    assert not ok and "unreachable" in detail


def test_build_providers_monta_ollama():
    providers = build_providers(
        [ProviderConfig(name="local", type="ollama", model="qwen2.5:14b", priority=10)]
    )

    assert len(providers) == 1
    assert isinstance(providers[0], OllamaProvider)
    assert providers[0].name == "local"
    assert providers[0].priority == 10


class CapturingProvider(ModelProvider):
    type = "capture"

    def __init__(self, config: ProviderConfig):
        super().__init__(config, timeout=5)
        self.captured: CompletionRequest | None = None
        self.calls = 0

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.captured = request
        self.calls += 1
        return CompletionResponse(text="ok", provider=self.name, model="cap")


def _capture(name: str, priority: int, max_ctx: int | None) -> CapturingProvider:
    return CapturingProvider(
        ProviderConfig(
            name=name,
            type="capture",
            capabilities=["reasoning"],
            priority=priority,
            max_context_tokens=max_ctx,
        )
    )


def _big_request() -> CompletionRequest:
    messages = [Message(role="system", content="seja breve")]
    for index in range(5):
        messages.append(Message(role="user", content=f"m{index}:" + "x" * 400))
    return CompletionRequest(messages=messages, max_tokens=16)


def test_estimate_tokens_aproxima():
    assert estimate_tokens("a" * 400) == 100
    assert estimate_tokens("") == 1


def test_truncate_preserva_system_e_cauda():
    truncated, info = truncate_to_fit(_big_request(), 300)

    assert truncated.messages[0].role == "system"
    assert truncated.messages[-1].content.startswith("m4:")
    assert info["total_messages"] == 6
    assert info["kept_messages"] == 3
    assert info["dropped_chars"] == 3 * 403
    assert truncated.metadata["overflow"] == {"action": "truncated"}


def test_gateway_truncate_corta_na_janela(runtime):
    small = _capture("pequeno", 10, 300)
    gateway = ModelGateway([small], audit=runtime.audit, overflow="truncate")

    gateway.complete(_big_request())

    assert small.calls == 1
    assert len(small.captured.messages) == 3
    events = runtime.audit.list(type=OVERFLOW, limit=5)
    assert events and events[0].payload["action"] == "truncated"
    assert events[0].payload["provider"] == "pequeno"


def test_escalate_sobe_para_quem_comporta(runtime):
    small = _capture("pequeno", 10, 300)
    big = _capture("grande", 1, 100_000)
    gateway = ModelGateway([small, big], audit=runtime.audit, overflow="escalate")

    gateway.complete(_big_request())

    assert small.calls == 0
    assert big.calls == 1
    assert len(big.captured.messages) == 6
    events = runtime.audit.list(type=OVERFLOW, limit=5)
    assert events and events[0].payload["action"] == "escalated"
    assert events[0].payload["from"] == "pequeno"
    assert events[0].payload["to"] == "grande"


def test_escalate_sem_saida_trunca_no_primeiro(runtime):
    small = _capture("pequeno", 10, 300)
    gateway = ModelGateway([small], audit=runtime.audit, overflow="escalate")

    gateway.complete(_big_request())

    assert small.calls == 1
    assert len(small.captured.messages) == 3
    actions = [e.payload["action"] for e in runtime.audit.list(type=OVERFLOW, limit=5)]
    assert "escalate_failed" in actions
    assert "truncated" in actions


def test_deny_barra_com_overflow(runtime):
    small = _capture("pequeno", 10, 300)
    gateway = ModelGateway([small], audit=runtime.audit, overflow="deny")

    with pytest.raises(ContextOverflow, match="excede a janela"):
        gateway.complete(_big_request())

    assert small.calls == 0
    events = runtime.audit.list(type=OVERFLOW, limit=5)
    assert events and events[0].payload["action"] == "denied"


def test_sem_max_nada_muda(runtime):
    runtime.gateway.complete(CompletionRequest(messages=[Message(role="user", content="ping")]))

    assert runtime.audit.list(type=OVERFLOW, limit=5) == []
