import pytest

from egr.core.config import ProviderConfig
from egr.core.errors import PolicyDenied
from egr.models.gateway import (
    CompletionRequest,
    CompletionResponse,
    Message,
    ModelGateway,
    ModelProvider,
)


def test_echo_provider_answers_offline(runtime):
    response = runtime.gateway.complete(
        CompletionRequest(messages=[Message(role="user", content="ping")])
    )
    assert response.provider == "echo"
    assert response.text


def test_routing_prefers_capability_and_priority(runtime):
    provider = runtime.gateway.route("reasoning")
    assert provider.name == "echo"


class CapturingProvider(ModelProvider):
    """External provider that records what crossed the boundary."""

    type = "capture"

    def __init__(self, config: ProviderConfig):
        super().__init__(config, timeout=5)
        self.captured: CompletionRequest | None = None

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.captured = request
        return CompletionResponse(text="ok", provider=self.name, model="capture", external=True)


def _gateway(external_ai: str):
    provider = CapturingProvider(
        ProviderConfig(name="ext", type="capture", external=True, capabilities=["reasoning"])
    )
    gateway = ModelGateway([provider], external_ai=external_ai, sanitize_external=True)
    return gateway, provider


def test_external_blocked_when_forbidden():
    gateway, _ = _gateway("forbidden")
    with pytest.raises(PolicyDenied):
        gateway.complete(
            CompletionRequest(
                messages=[Message(role="user", content="CPF do cliente: 123.456.789-00")]
            )
        )


def test_external_payload_is_sanitized_when_restricted():
    gateway, provider = _gateway("restricted")
    gateway.complete(
        CompletionRequest(messages=[Message(role="user", content="CPF do cliente: 123.456.789-00")])
    )
    assert provider.captured is not None
    assert "123.456.789-00" not in provider.captured.messages[0].content
    assert "CPF_REMOVED" in provider.captured.messages[0].content


def test_external_allowed_passes_through():
    gateway, provider = _gateway("allowed")
    gateway.complete(CompletionRequest(messages=[Message(role="user", content="conteúdo público")]))
    assert provider.captured.messages[0].content == "conteúdo público"


def test_no_provider_for_blocked_external(runtime):
    from egr.core.errors import NoProviderAvailable

    restricted = ModelGateway([], external_ai="allowed")
    with pytest.raises(NoProviderAvailable):
        restricted.complete(CompletionRequest(messages=[Message(role="user", content="x")]))
