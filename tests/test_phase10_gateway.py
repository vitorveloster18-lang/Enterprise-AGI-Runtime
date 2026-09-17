"""Fase 10 — Remote Control: mensagem de fora, trabalho governado por dentro."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from egr.api.server import create_app
from egr.core.config import ChannelConfig, EGRConfig, Settings
from egr.domain.enums import BindingStatus, EventType, TaskStatus
from egr.gateway.channels import ConsoleChannel, WebChannel
from egr.gateway.slack import SlackChannel, signature_for, verify_signature
from egr.gateway.telegram import TelegramChannel
from egr.runtime.runtime import Runtime


# ----------------------------------------------------------------------
# apoio
# ----------------------------------------------------------------------
def _gateway(workspace, **overrides) -> Runtime:
    """Runtime com gateway habilitado e um canal web (o mais simples de todos)."""

    config = EGRConfig()
    config.gateway.enabled = True
    config.gateway.channels = [
        ChannelConfig(name="web", type="web", enabled=True, default_agent="document-agent")
    ]
    for key, value in overrides.items():
        if key == "channels":
            config.gateway.channels = value
        else:
            setattr(config.gateway, key, value)
    return Runtime(Settings(workspace=workspace, config=config), enable_logging=False)


def _paired(runtime, external_id: str = "u1", roles: list[str] | None = None, channel: str = "web"):
    return runtime.channels.pair(channel, external_id, roles=roles or ["operator"])


class FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class FakeClient:
    """Cliente HTTP de mentira: o canal não sabe que não está na internet."""

    def __init__(self, *responses: dict):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url, json=None, timeout=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        payload = self.responses.pop(0) if self.responses else {"ok": True, "result": []}
        return FakeResponse(payload)


def _update(text: str, chat_id: int = 42, first_name: str = "Vitor") -> dict:
    return {
        "update_id": 7,
        "message": {
            "text": text,
            "chat": {"id": chat_id},
            "from": {"first_name": first_name, "username": "vitor"},
        },
    }


# ----------------------------------------------------------------------
# default deny e pareamento
# ----------------------------------------------------------------------
def test_gateway_is_disabled_by_default(runtime):
    reply = runtime.channels.handle("web", "ninguem", "olá")

    assert reply.denied
    assert "gateway desabilitado" in reply.reason
    assert runtime.tasks.count_by_status() == {}


def test_the_first_message_only_creates_a_pending_binding(workspace):
    runtime = _gateway(workspace)

    reply = runtime.channels.handle("web", "u1", "olá")

    binding = runtime.channels.binding_for("web", "u1")
    assert binding.status == BindingStatus.PENDING
    assert binding.pairing_code
    assert reply.denied
    assert binding.pairing_code in reply.text  # o caminho para entrar é explícito
    runtime.close()


def test_gateway_status_reports_the_ladder_of_trust(workspace):
    runtime = _gateway(workspace)
    _paired(runtime)

    data = runtime.channel_status()

    assert data["habilitado"] is True
    assert data["pareamento_exigido"] is True
    assert data["canais"][0]["nome"] == "web"
    assert data["pareamentos"]["total"] == 1
    runtime.close()


def test_pairing_creates_a_principal_with_roles(workspace):
    runtime = _gateway(workspace)

    binding = runtime.channels.pair("web", "u1", roles=["operator"], actor="human:vitor")

    principal = runtime.identity.get(binding.principal_id)
    assert principal is not None
    assert "operator" in principal.roles
    assert binding.status == BindingStatus.ACTIVE
    runtime.close()


def test_pairing_requires_the_code_shown_to_the_sender(workspace):
    runtime = _gateway(workspace)
    code = runtime.channels.binding_for("web", "u1").pairing_code

    with pytest.raises(ValueError, match="código de pareamento inválido"):
        runtime.channels.pair("web", "u1", code="000000")
    assert runtime.channels.pair("web", "u1", code=code).active
    runtime.close()


def test_unpair_returns_the_sender_to_nobody(workspace):
    runtime = _gateway(workspace)
    _paired(runtime)

    binding = runtime.channels.unpair("web", "u1", actor="human:vitor")

    assert binding.status == BindingStatus.PENDING
    assert runtime.channels.handle("web", "u1", "olá").denied
    runtime.close()


def test_blocking_a_sender_is_stronger_than_unpairing(workspace):
    runtime = _gateway(workspace)
    _paired(runtime)

    runtime.channels.unpair("web", "u1", actor="human:vitor", block=True)
    reply = runtime.channels.handle("web", "u1", "olá")

    assert "bloqueado" in reply.reason
    assert runtime.identity.get("web.u1").status == "disabled"
    runtime.close()


# ----------------------------------------------------------------------
# permissão: falar não é executar
# ----------------------------------------------------------------------
def test_a_paired_viewer_reads_but_does_not_run_tasks(workspace):
    runtime = _gateway(workspace)
    _paired(runtime, roles=["viewer"])

    status = runtime.channels.handle("web", "u1", "/status")
    denied = runtime.channels.handle("web", "u1", "faça algo por mim")

    assert not status.denied
    assert "ambiente" in status.text
    assert denied.denied
    assert "task.submit" in denied.reason
    assert runtime.tasks.count_by_status() == {}
    runtime.close()


def test_an_operator_runs_a_governed_task(workspace):
    runtime = _gateway(workspace)
    _paired(runtime, roles=["operator"])

    reply = runtime.channels.handle("web", "u1", "resuma os documentos")

    assert not reply.denied
    assert reply.task_id
    task = runtime.tasks.get(reply.task_id)
    assert task.status == TaskStatus.COMPLETED
    assert task.created_by == "web.u1"  # a autoria é do principal pareado
    assert task.agent_id == "document-agent"
    runtime.close()


def test_run_command_is_equivalent_to_free_text(workspace):
    runtime = _gateway(workspace)
    _paired(runtime)

    reply = runtime.channels.handle("web", "u1", "/run gere um inventário")

    assert reply.task_id
    assert runtime.tasks.get(reply.task_id).status == TaskStatus.COMPLETED
    runtime.close()


def test_help_and_who_commands(workspace):
    runtime = _gateway(workspace)
    _paired(runtime)

    help_reply = runtime.channels.handle("web", "u1", "/ajuda")
    who = runtime.channels.handle("web", "u1", "/quem")

    assert "/run" in help_reply.text
    assert "web.u1" in who.text
    assert "operator" in who.text
    runtime.close()


def test_tasks_command_lists_recent_work(workspace):
    runtime = _gateway(workspace)
    _paired(runtime)
    runtime.channels.handle("web", "u1", "inventário")

    reply = runtime.channels.handle("web", "u1", "/tasks")

    assert reply.task_id is None
    assert "tsk_" in reply.text
    runtime.close()


def test_approving_by_channel_requires_an_enabled_channel(workspace):
    runtime = _gateway(workspace)
    _paired(runtime, roles=["approver"])

    reply = runtime.channels.handle("web", "u1", "/aprovar apr_nao_existe")

    assert reply.denied
    assert "allow_decisions" in reply.reason
    runtime.close()


# ----------------------------------------------------------------------
# contenção: lista branca, ritmo e redação
# ----------------------------------------------------------------------
def test_allowlist_refuses_senders_outside_the_list(workspace):
    runtime = _gateway(workspace)
    runtime.settings.config.gateway.channels[0].allowed_chat_ids = ["111"]
    _paired(runtime, external_id="222")

    reply = runtime.channels.handle("web", "222", "olá")

    assert reply.denied
    assert "lista branca" in reply.reason
    runtime.close()


def test_rate_limit_survives_process_restart(workspace):
    """O ritmo é contado no banco: reiniciar não limpa a janela."""

    runtime = _gateway(workspace, rate_limit_per_minute=2)
    _paired(runtime)
    for _ in range(2):
        assert not runtime.channels.handle("web", "u1", "/quem").denied

    denied = runtime.channels.handle("web", "u1", "/quem")

    assert denied.denied
    assert "ritmo excedido" in denied.reason
    # um novo Runtime sobre o mesmo workspace continua contando
    fresh = Runtime(Settings(workspace=workspace, config=runtime.settings.config), enable_logging=False)
    assert fresh.channels.handle("web", "u1", "/quem").denied
    fresh.close()
    runtime.close()


def test_secrets_are_redacted_before_being_stored(workspace):
    """O canal não é cofre: o que chega já é guardado riscado."""

    runtime = _gateway(workspace)
    _paired(runtime)

    runtime.channels.handle("web", "u1", "meu token é sk-abcdefghijklmnop")

    stored = runtime.gateway_messages.list(direction="in", limit=1)[0]
    assert "sk-abcdefghijklmnop" not in stored.text
    assert "***redacted***" in stored.text
    runtime.close()


def test_scrub_follows_the_configuration(workspace):
    runtime = _gateway(workspace, redact=False, max_reply_chars=40)

    assert runtime.channels._scrub("Bearer abcdefghijklmnop") == "Bearer abcdefghijklmnop"
    runtime.close()


def test_scrub_cuts_long_replies(workspace):
    runtime = _gateway(workspace, max_reply_chars=20)

    assert runtime.channels._scrub("a" * 50) == "a" * 17 + "..."
    assert runtime.channels._scrub("sk-abcdefghijklmnop") == "***redacted***"
    runtime.close()





def test_conversation_is_logged_in_both_directions(workspace):
    runtime = _gateway(workspace)
    _paired(runtime)
    runtime.channels.handle("web", "u1", "olá")

    inbound = runtime.gateway_messages.list(direction="in", limit=10)
    outbound = runtime.gateway_messages.list(direction="out", limit=10)

    assert [item.text for item in inbound]
    assert outbound
    assert all(item.channel == "web" for item in inbound + outbound)
    runtime.close()


def test_the_trail_records_who_talked(workspace):
    runtime = _gateway(workspace)
    _paired(runtime)
    runtime.channels.handle("web", "u1", "olá")

    kinds = {event.type for event in runtime.audit.list(limit=200)}

    assert {
        EventType.GATEWAY_PAIRED,
        EventType.GATEWAY_MESSAGE_RECEIVED,
        EventType.GATEWAY_MESSAGE_SENT,
    } <= kinds
    runtime.close()


def test_denials_are_audited(workspace):
    runtime = _gateway(workspace)

    runtime.channels.handle("web", "desconhecido", "olá")

    denied = [event for event in runtime.audit.list(limit=100) if event.type == EventType.GATEWAY_DENIED]
    assert denied
    assert "sem pareamento" in denied[0].payload["motivo"]
    runtime.close()


# ----------------------------------------------------------------------
# canais
# ----------------------------------------------------------------------
def test_web_channel_keeps_an_outbox(workspace):
    runtime = _gateway(workspace)
    channel = runtime.channels.channel("web")

    channel.send("u1", "olá")

    assert isinstance(channel, WebChannel)
    assert channel.outbox == [{"canal": "web", "remetente": "u1", "texto": "olá"}]
    assert channel.drain()[0]["texto"] == "olá"
    assert channel.outbox == []
    runtime.close()


def test_console_channel_round_trip(workspace):
    runtime = _gateway(workspace)
    said: list[str] = []
    channel = ConsoleChannel("console", None, input_func=lambda _: "olá", output_func=said.append)
    runtime.channels.register(channel)
    _paired(runtime, channel="console", external_id="console:operador")

    handled = channel.poll_once(runtime.channels.handle_inbound)

    assert handled == 1
    assert said and "egr>" in said[0]
    runtime.close()


def test_telegram_polling_answers_through_the_bot_api(workspace):
    runtime = _gateway(workspace)
    client = FakeClient(
        {"ok": True, "result": [_update("olá")]},
        {"ok": True},
    )
    channel = TelegramChannel(
        "telegram",
        ChannelConfig(name="telegram", type="telegram", enabled=True),
        client=client,
        token="123:abc",
    )
    runtime.channels.register(channel)
    runtime.settings.config.gateway.channels.append(ChannelConfig(name="telegram", type="telegram", enabled=True))

    handled = channel.poll_once(runtime.channels.handle_inbound)

    assert handled == 1
    assert channel._offset == 8  # próximo update
    sent = client.calls[-1]
    assert sent["url"].endswith("/sendMessage")
    assert sent["json"]["chat_id"] == "42"
    assert "pareamento" in sent["json"]["text"]  # ninguém fala sem ser pareado
    runtime.close()


def test_telegram_runs_tasks_for_paired_senders(workspace):
    runtime = _gateway(workspace)
    client = FakeClient({"ok": True, "result": [_update("resuma os documentos")]}, {"ok": True})
    channel = TelegramChannel(
        "telegram",
        ChannelConfig(name="telegram", type="telegram", enabled=True),
        client=client,
        token="123:abc",
    )
    runtime.channels.register(channel)
    runtime.settings.config.gateway.channels.append(
        ChannelConfig(name="telegram", type="telegram", enabled=True, default_agent="document-agent")
    )
    runtime.channels.pair("telegram", "42", roles=["operator"])

    channel.poll_once(runtime.channels.handle_inbound)

    assert "Não" not in client.calls[-1]["json"]["text"]
    assert runtime.tasks.count_by_status().get("completed") == 1
    runtime.close()


def test_telegram_ignores_updates_without_text(workspace):
    runtime = _gateway(workspace)
    client = FakeClient({"ok": True, "result": [{"update_id": 1, "message": {"photo": []}}]})
    channel = TelegramChannel(
        "telegram",
        ChannelConfig(name="telegram", type="telegram", enabled=True),
        client=client,
        token="123:abc",
    )

    assert channel.poll_once(runtime.channels.handle_inbound) == 0
    assert len(client.calls) == 1  # só o getUpdates
    runtime.close()


def test_telegram_without_token_says_nothing(workspace):
    runtime = _gateway(workspace)
    channel = TelegramChannel("telegram", ChannelConfig(name="telegram", type="telegram", enabled=True), token="")

    assert channel.poll_once(runtime.channels.handle_inbound) == 0
    runtime.close()


def test_slack_signature_is_verified(monkeypatch):
    secret = "s3cr3t"
    body = json.dumps({"type": "event_callback"})
    stamp = str(int(time.time()))
    good = signature_for(secret, stamp, body)

    assert verify_signature(secret, stamp, body, good) is True
    assert verify_signature(secret, stamp, body, "v0=deadbeef") is False
    # payload antigo: replay não passa
    assert verify_signature(secret, "1000000000", body, good, now=time.time()) is False


def test_slack_answers_the_url_verification_challenge(workspace):
    runtime = _gateway(workspace)
    channel = SlackChannel("slack", ChannelConfig(name="slack", type="slack", enabled=True))

    reply = channel.handle_payload(
        {"type": "url_verification", "challenge": "abc123"}, runtime.channels.handle_inbound
    )

    assert reply is not None
    assert reply.command == "url_verification"
    assert reply.text == "abc123"
    runtime.close()


def test_slack_ignores_its_own_messages(workspace):
    runtime = _gateway(workspace)
    channel = SlackChannel("slack", ChannelConfig(name="slack", type="slack", enabled=True))

    payload = {
        "type": "event_callback",
        "event": {"type": "message", "bot_id": "B1", "user": "U1", "text": "oi", "channel": "C1"},
    }
    reply = channel.handle_payload(payload, runtime.channels.handle_inbound)

    assert reply is None
    runtime.close()


def test_slack_runs_a_task_and_answers_in_the_same_channel(workspace):
    runtime = _gateway(workspace)
    client = FakeClient({"ok": True})
    channel = SlackChannel(
        "slack",
        ChannelConfig(name="slack", type="slack", enabled=True, default_agent="document-agent"),
        client=client,
        token="xoxb-123",
    )
    runtime.channels.register(channel)
    runtime.settings.config.gateway.channels.append(
        ChannelConfig(name="slack", type="slack", enabled=True, default_agent="document-agent")
    )
    runtime.channels.pair("slack", "U1", roles=["operator"])

    reply = channel.handle_payload(
        {"type": "event_callback", "event": {"type": "message", "user": "U1", "channel": "C1", "text": "resuma"}},
        runtime.channels.handle_inbound,
    )

    assert reply is not None and not reply.denied
    sent = client.calls[-1]
    assert sent["json"]["channel"] == "C1"
    assert sent["headers"]["Authorization"] == "Bearer xoxb-123"
    runtime.close()


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------
def test_api_gateway_status_and_messages(workspace):
    runtime = _gateway(workspace)
    client = TestClient(create_app(runtime))

    assert client.get("/v1/gateway").json()["habilitado"] is True
    denied = client.post("/v1/gateway/messages", json={"channel": "web", "external_id": "u9", "text": "olá"})
    assert denied.json()["recusada"] is True

    paired = client.post("/v1/gateway/pair", json={"channel": "web", "external_id": "u9", "roles": ["operator"]})
    assert paired.status_code == 200
    ok = client.post("/v1/gateway/messages", json={"channel": "web", "external_id": "u9", "text": "resuma"})
    assert ok.json()["recusada"] is False
    assert ok.json()["task"].startswith("tsk_")

    bindings = client.get("/v1/gateway/bindings").json()
    assert bindings and bindings[0]["status"] == "active"
    assert client.get("/v1/gateway/messages").json()
    assert client.delete("/v1/gateway/bindings/web/u9").status_code == 200
    runtime.close()


def test_api_slack_events_requires_a_valid_signature(workspace, monkeypatch):
    runtime = _gateway(workspace)
    runtime.settings.config.gateway.channels.append(ChannelConfig(name="slack", type="slack", enabled=True))
    channel = SlackChannel("slack", ChannelConfig(name="slack", type="slack", enabled=True), token="xoxb-1")
    runtime.channels.register(channel)
    monkeypatch.setenv("EGR_SLACK_SIGNING_SECRET", "s3cr3t")
    client = TestClient(create_app(runtime))
    body = json.dumps({"type": "url_verification", "challenge": "xyz"})
    stamp = str(int(time.time()))

    bad = client.post(
        "/v1/gateway/slack/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": stamp,
            "X-Slack-Signature": "v0=errado",
        },
    )
    assert bad.status_code == 401

    good = client.post(
        "/v1/gateway/slack/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": stamp,
            "X-Slack-Signature": signature_for("s3cr3t", stamp, body),
        },
    )
    assert good.status_code == 200
    assert good.json()["challenge"] == "xyz"
    runtime.close()


def test_api_telegram_webhook_checks_the_secret_token(workspace, monkeypatch):
    runtime = _gateway(workspace)
    channel = TelegramChannel(
        "telegram",
        ChannelConfig(name="telegram", type="telegram", enabled=True, webhook_secret_env="EGR_TG_SECRET"),
        client=FakeClient({"ok": True}),
        token="123:abc",
    )
    runtime.channels.register(channel)
    runtime.settings.config.gateway.channels.append(
        ChannelConfig(name="telegram", type="telegram", enabled=True, webhook_secret_env="EGR_TG_SECRET")
    )
    monkeypatch.setenv("EGR_TG_SECRET", "segredo-do-webhook")
    client = TestClient(create_app(runtime))
    payload = _update("olá")

    assert client.post("/v1/gateway/telegram/webhook", json=payload).status_code == 401
    ok = client.post(
        "/v1/gateway/telegram/webhook",
        json=payload,
        headers={"X-Telegram-Bot-Api-Secret-Token": "segredo-do-webhook"},
    )
    assert ok.status_code == 200
    assert ok.json()["handled"] is True
    runtime.close()


def test_api_rejects_a_pairing_with_the_wrong_code(workspace):
    runtime = _gateway(workspace)
    runtime.channels.binding_for("web", "u1")
    client = TestClient(create_app(runtime))

    response = client.post("/v1/gateway/pair", json={"channel": "web", "external_id": "u1", "code": "000000"})

    assert response.status_code == 400
    runtime.close()


# ----------------------------------------------------------------------
# Runtime: status e saúde
# ----------------------------------------------------------------------
def test_channels_show_up_in_runtime_status(workspace):
    runtime = _gateway(workspace)

    assert "channels" in runtime.status()
    assert runtime.status()["channels"]["canais"][0]["nome"] == "web"
    runtime.close()


def test_health_reports_channels_and_warns_without_pairing(workspace):
    runtime = _gateway(workspace, require_pairing=False)
    checks = {item["check"]: item for item in runtime.health()["checks"]}

    assert checks["channels"]["ok"] is True
    assert checks["channels:pareamento"]["ok"] is False
    runtime.close()


def test_bindings_survive_a_restart(workspace):
    runtime = _gateway(workspace)
    _paired(runtime, roles=["operator"])
    runtime.close()

    fresh = Runtime(Settings(workspace=workspace, config=EGRConfig()), enable_logging=False)
    fresh.settings.config.gateway.enabled = True

    binding = fresh.channels.binding_for("web", "u1")
    assert binding.active
    assert fresh.channels.handle("web", "u1", "/quem").denied is False
    fresh.close()


def test_message_window_counts_only_the_sender(workspace):
    runtime = _gateway(workspace, rate_limit_per_minute=1)
    _paired(runtime, external_id="u1")
    _paired(runtime, external_id="u2")
    runtime.channels.handle("web", "u1", "/quem")
    runtime.channels.handle("web", "u1", "/quem")

    assert runtime.channels.handle("web", "u1", "/quem").denied  # u1 estourou
    assert not runtime.channels.handle("web", "u2", "/quem").denied  # u2 não
    runtime.close()


def test_disabled_channel_refuses_messages(workspace):
    runtime = _gateway(
        workspace,
        channels=[
            ChannelConfig(name="web", type="web", enabled=True),
            ChannelConfig(name="suspenso", type="web", enabled=False),
        ],
    )
    _paired(runtime, channel="suspenso")

    reply = runtime.channels.handle("suspenso", "u1", "olá")

    assert reply.denied
    assert "desabilitado" in reply.reason
    runtime.close()


def test_last_seen_and_count_are_updated(workspace):
    runtime = _gateway(workspace)
    _paired(runtime)
    before = runtime.channels.binding_for("web", "u1")

    runtime.channels.handle("web", "u1", "/quem")

    after = runtime.channels.binding_for("web", "u1")
    assert after.message_count == before.message_count + 1
    assert before.last_seen_at is None  # pareado, mas ainda não conversou
    assert after.last_seen_at is not None
    runtime.close()


def test_pairing_code_is_random_per_sender(workspace):
    runtime = _gateway(workspace)

    first = runtime.channels.binding_for("web", "u1").pairing_code
    second = runtime.channels.binding_for("web", "u2").pairing_code

    assert first != second
    runtime.close()


def test_help_is_the_same_for_every_channel(workspace):
    runtime = _gateway(workspace)
    _paired(runtime)

    reply = runtime.channels.handle("web", "u1", "/help")

    assert reply.command == "help"
    assert "/aprovar" in reply.text
    runtime.close()


def test_unknown_channel_command_falls_back_to_a_task(workspace):
    """Sem prefixo de comando, a mensagem é objetivo — e o Runtime governa."""

    runtime = _gateway(workspace)
    _paired(runtime)

    reply = runtime.channels.handle("web", "u1", "qual o total de documentos?")

    assert reply.task_id
    assert reply.command == ""
    runtime.close()
