"""Lacuna 10b — anexos e mídia dos canais, e botões que não são atalhos.

Um arquivo que entra pelo Telegram, Slack ou Web é **conteúdo**: tipo e tamanho
por lista branca, destino dentro do workspace, impressão digital na trilha e
recusa visível quando não passa. Um botão é uma mensagem com melhor aparência:
ele repete um comando e passa pelo mesmo governo.
"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from egr.api.server import create_app
from egr.core.config import ChannelConfig, EGRConfig, Settings, dump_config
from egr.domain.approval import Approval
from egr.domain.channel import Attachment, InboundAttachment, InboundMessage
from egr.domain.enums import ApprovalStatus, AttachmentStatus, Environment, EventType, TaskStatus
from egr.gateway.attachments import AttachmentService, safe_name
from egr.gateway.channels import ConsoleChannel, WebChannel
from egr.gateway.slack import SlackChannel
from egr.gateway.telegram import TelegramChannel
from egr.runtime.runtime import Runtime


# ----------------------------------------------------------------------
# apoio
# ----------------------------------------------------------------------
def _gateway(workspace, **overrides) -> Runtime:
    """Runtime com gateway habilitado e um canal web que aceita anexos."""

    config = EGRConfig()
    config.gateway.enabled = True
    config.gateway.channels = [
        ChannelConfig(
            name="web",
            type="web",
            enabled=True,
            default_agent="document-agent",
            allow_decisions=True,
            allow_attachments=True,
        )
    ]
    for key, value in overrides.items():
        if key == "channels":
            config.gateway.channels = value
        elif key == "attachments":
            for field, field_value in value.items():
                setattr(config.gateway.attachments, field, field_value)
        else:
            setattr(config.gateway, key, value)
    return Runtime(Settings(workspace=workspace, config=config), enable_logging=False)


def _paired(runtime, external_id: str = "u1", roles: list[str] | None = None):
    return runtime.channels.pair("web", external_id, roles=roles or ["operator", "approver"])


def _persist(workspace, runtime: Runtime) -> None:
    """O CLI monta o Runtime do zero: a configuração precisa estar no egr.yaml."""

    (workspace / "egr.yaml").write_text(dump_config(runtime.settings.config))


def _pending(**overrides) -> InboundAttachment:
    payload = dict(name="nota.txt", mime="text/plain", content=b"cliente ACME valor 1200", remote_ref="ref-1")
    payload.update(overrides)
    return InboundAttachment(**payload)


class FakeResponse:
    def __init__(self, payload: dict | None = None, content: bytes = b""):
        self._payload = payload if payload is not None else {}
        self.content = content

    def json(self) -> dict:
        return self._payload


class FakeClient:
    """Cliente HTTP de mentira com `get` (download) e `post` (upload)."""

    def __init__(self, *, payloads: list[dict] | None = None, files: dict[str, bytes] | None = None):
        self.payloads = list(payloads or [])
        self.files = files or {}
        self.posts: list[dict] = []
        self.gets: list[str] = []

    def post(self, url, json=None, timeout=None, headers=None, data=None, files=None):
        self.posts.append({"url": url, "json": json, "data": data, "files": files, "headers": headers})
        return FakeResponse(self.payloads.pop(0) if self.payloads else {"ok": True})

    def get(self, url, timeout=None, headers=None):
        self.gets.append(url)
        return FakeResponse(content=self.files.get(url, b"conteudo"))


# ----------------------------------------------------------------------
# domínio
# ----------------------------------------------------------------------
def test_safe_name_refuses_traversal_and_strangeness():
    assert safe_name("../../etc/passwd") == "passwd"  # o Path fica só com o último pedaço
    assert safe_name("relatório final.pdf") == "relat-rio-final.pdf"
    assert safe_name("") == "anexo"


def test_attachment_summary_and_state():
    item = Attachment(id="att_1", channel="web", external_id="u1", name="nota.txt", size=10)
    assert item.status == AttachmentStatus.RECEIVED
    assert not item.stored
    item.attach("tsk_1")
    assert item.summary()["task"] == "tsk_1"


# ----------------------------------------------------------------------
# serviço de anexos
# ----------------------------------------------------------------------
def test_receive_stores_inside_workspace_with_checksum(workspace):
    runtime = _gateway(workspace)
    service = runtime.channels.attachments

    stored = service.receive("web", "u1", _pending(), actor="web.u1")

    assert stored.status == AttachmentStatus.STORED
    assert stored.path.startswith("artifacts/inbox/web/u1/")
    assert (workspace / stored.path).read_bytes() == b"cliente ACME valor 1200"
    assert len(stored.checksum) == 64
    assert stored.text_chars == len(b"cliente ACME valor 1200")
    assert runtime.gateway_attachments.get(stored.id) is not None
    runtime.close()


def test_receive_refuses_extension_outside_allowlist(workspace):
    runtime = _gateway(workspace)

    refused = runtime.channels.attachments.receive("web", "u1", _pending(name="x.exe", mime=""), actor="web.u1")

    assert refused.status == AttachmentStatus.REJECTED
    assert "extensão" in refused.reason
    assert refused.path == ""
    assert runtime.gateway_attachments.get(refused.id).status == AttachmentStatus.REJECTED
    runtime.close()


def test_receive_refuses_mime_outside_allowlist(workspace):
    runtime = _gateway(workspace)

    refused = runtime.channels.attachments.receive(
        "web", "u1", _pending(name="x.txt", mime="application/x-msdownload"), actor="web.u1"
    )

    assert refused.status == AttachmentStatus.REJECTED
    assert "tipo" in refused.reason
    runtime.close()


def test_receive_refuses_declared_size_above_limit(workspace):
    runtime = _gateway(workspace)
    config = runtime.settings.config.gateway.attachments
    config.max_bytes = 10

    refused = runtime.channels.attachments.receive("web", "u1", _pending(size=999), actor="web.u1")

    assert refused.status == AttachmentStatus.REJECTED
    assert "limite" in refused.reason
    runtime.close()


def test_receive_refuses_when_content_exceeds_limit_after_download(workspace):
    """O canal pode mentir sobre o tamanho: o limite vale sobre o que chegou."""

    runtime = _gateway(workspace)
    config = runtime.settings.config.gateway.attachments
    config.max_bytes = 5

    refused = runtime.channels.attachments.receive("web", "u1", _pending(size=1), actor="web.u1")

    assert refused.status == AttachmentStatus.REJECTED
    assert "excede" in refused.reason
    runtime.close()


def test_receive_uses_fetch_only_after_policy(workspace):
    """Conteúdo só é buscado quando tipo e tamanho já foram aceitos."""

    runtime = _gateway(workspace)
    calls: list[int] = []

    def fetch() -> bytes:
        calls.append(1)
        return b"baixado"

    stored = runtime.channels.attachments.receive("web", "u1", _pending(content=None, fetch=fetch), actor="web.u1")

    assert calls == [1]
    assert stored.stored
    assert (workspace / stored.path).read_bytes() == b"baixado"
    runtime.close()


def test_receive_refuses_when_fetch_fails(workspace):
    runtime = _gateway(workspace)

    def broken() -> bytes:
        raise RuntimeError("503 do provedor")

    refused = runtime.channels.attachments.receive("web", "u1", _pending(content=None, fetch=broken), actor="web.u1")

    assert refused.status == AttachmentStatus.REJECTED
    assert "falha ao baixar" in refused.reason
    runtime.close()


def test_receive_respects_disabled_attachments(workspace):
    runtime = _gateway(workspace, attachments={"enabled": False})

    refused = runtime.channels.attachments.receive("web", "u1", _pending(), actor="web.u1")

    assert refused.status == AttachmentStatus.REJECTED
    assert refused.reason == "anexos desabilitados na configuração"
    runtime.close()


def test_receive_respects_channel_without_attachments(workspace):
    runtime = _gateway(workspace)
    runtime.settings.config.gateway.channels[0].allow_attachments = False

    refused = runtime.channels.attachments.receive("web", "u1", _pending(), actor="web.u1")

    assert refused.status == AttachmentStatus.REJECTED
    assert "não aceita anexos" in refused.reason
    runtime.close()


def test_receive_many_refuses_beyond_per_message_limit(workspace):
    runtime = _gateway(workspace, attachments={"max_files": 1})

    results = runtime.channels.attachments.receive_many("web", "u1", [_pending(), _pending()], actor="web.u1")

    assert results[0].stored
    assert results[1].status == AttachmentStatus.REJECTED
    assert "por mensagem" in results[1].reason
    runtime.close()


def test_repeated_name_does_not_overwrite(workspace):
    runtime = _gateway(workspace)

    first = runtime.channels.attachments.receive("web", "u1", _pending(), actor="web.u1")
    second = runtime.channels.attachments.receive("web", "u1", _pending(content=b"outro"), actor="web.u1")

    assert first.path != second.path
    assert second.path.endswith("nota-2.txt")
    runtime.close()


def test_preview_is_redacted_and_truncated(workspace):
    runtime = _gateway(workspace, attachments={"extract_chars": 10})
    content = b"segredo: abcdefghijklmnop"

    stored = runtime.channels.attachments.receive("web", "u1", _pending(content=content), actor="web.u1")

    assert len(stored.preview) == 10
    assert stored.text_chars == 10
    runtime.close()


def test_binary_files_have_no_preview(workspace):
    runtime = _gateway(workspace)

    stored = runtime.channels.attachments.receive(
        "web", "u1", _pending(name="foto.png", mime="image/png", content=b"\x89PNG"), actor="web.u1"
    )

    assert stored.stored
    assert stored.preview == ""
    runtime.close()


def test_manifest_reports_acceptance_and_refusal(workspace):
    runtime = _gateway(workspace)
    accepted = runtime.channels.attachments.receive("web", "u1", _pending(), actor="web.u1")
    refused = runtime.channels.attachments.receive("web", "u1", _pending(name="x.exe", mime=""), actor="web.u1")

    text = runtime.channels.attachments.manifest([accepted, refused])

    assert accepted.path in text
    assert "recusado" in text
    runtime.close()


def test_outbound_only_from_declared_roots(workspace):
    runtime = _gateway(workspace)
    (workspace / "artifacts").mkdir(exist_ok=True)
    (workspace / "artifacts" / "saida.txt").write_text("ok")

    assert runtime.channels.attachments.outbound("artifacts/saida.txt")["ok"] is True
    assert "raízes" in runtime.channels.attachments.outbound("documents/nota.md")["erro"]
    assert "escapes" in runtime.channels.attachments.outbound("../../etc/passwd")["erro"]
    runtime.close()


def test_outbound_respects_size_limit(workspace):
    runtime = _gateway(workspace, attachments={"outbound_max_bytes": 4})
    (workspace / "artifacts").mkdir(exist_ok=True)
    (workspace / "artifacts" / "grande.txt").write_text("bem maior que quatro")

    assert "limite de saída" in runtime.channels.attachments.outbound("artifacts/grande.txt")["erro"]
    runtime.close()


def test_attachments_service_status(workspace):
    runtime = _gateway(workspace)
    runtime.channels.attachments.receive("web", "u1", _pending(), actor="web.u1")

    status = runtime.channels.attachments.status()

    assert status["habilitado"] is True
    assert status["por_situação"]["stored"] == 1
    assert status["bytes_guardados"] == len(b"cliente ACME valor 1200")
    runtime.close()


# ----------------------------------------------------------------------
# gateway: mensagem com anexo
# ----------------------------------------------------------------------
def test_message_with_attachment_becomes_governed_task(workspace):
    runtime = _gateway(workspace)
    _paired(runtime)

    reply = runtime.channels.handle_inbound(
        InboundMessage(channel="web", external_id="u1", text="classifique o documento", attachments=[_pending()])
    )

    task = runtime.tasks.get(reply.task_id)
    assert "Anexos recebidos" in task.objective
    linked = runtime.gateway_attachments.list(task_id=task.id)
    assert len(linked) == 1 and linked[0].stored
    runtime.close()


def test_attachment_alone_is_enough_to_run(workspace):
    runtime = _gateway(workspace)
    _paired(runtime)

    reply = runtime.channels.handle_inbound(
        InboundMessage(channel="web", external_id="u1", text="", attachments=[_pending()])
    )

    assert reply.task_id
    assert "Analise os anexos" in runtime.tasks.get(reply.task_id).objective
    runtime.close()


def test_refused_attachment_still_answers_the_sender(workspace):
    runtime = _gateway(workspace)
    _paired(runtime)

    reply = runtime.channels.handle_inbound(
        InboundMessage(channel="web", external_id="u1", text="olha esse arquivo", attachments=[_pending(name="x.exe")])
    )

    task = runtime.tasks.get(reply.task_id)
    assert "recusado" in task.objective
    runtime.close()


def test_attachments_command_lists_only_the_sender(workspace):
    runtime = _gateway(workspace)
    _paired(runtime)
    _paired(runtime, external_id="u2")
    runtime.channels.attachments.receive("web", "u1", _pending(), actor="web.u1")

    mine = runtime.channels.handle("web", "u1", "/anexos")
    others = runtime.channels.handle("web", "u2", "/anexos")

    assert "nota.txt" in mine.text
    assert "ainda não mandou anexos" in others.text
    runtime.close()


def test_attachment_detail_refuses_other_peoples_files(workspace):
    runtime = _gateway(workspace)
    _paired(runtime)
    _paired(runtime, external_id="u2")
    stored = runtime.channels.attachments.receive("web", "u1", _pending(), actor="web.u1")

    mine = runtime.channels.handle("web", "u1", f"/anexo {stored.id}")
    theirs = runtime.channels.handle("web", "u2", f"/anexo {stored.id}")

    assert stored.path in mine.text
    assert theirs.denied and theirs.reason == "anexo de outro remetente"
    runtime.close()


def test_unknown_attachment_is_not_found(workspace):
    runtime = _gateway(workspace)
    _paired(runtime)

    reply = runtime.channels.handle("web", "u1", "/anexo att_nada")

    assert reply.denied and reply.reason == "anexo inexistente"
    runtime.close()


def test_file_command_sends_only_what_policy_allows(workspace):
    runtime = _gateway(workspace)
    _paired(runtime)
    stored = runtime.channels.attachments.receive("web", "u1", _pending(), actor="web.u1")

    ok = runtime.channels.handle("web", "u1", f"/arquivo {stored.path}")
    denied = runtime.channels.handle("web", "u1", "/arquivo documents/nota-fiscal.txt")

    assert ok.attachments and ok.attachments[0]["name"] == "nota.txt"
    assert denied.denied and "raízes" in denied.reason
    runtime.close()


def test_attachment_audit_events(workspace):
    runtime = _gateway(workspace)
    runtime.channels.attachments.receive("web", "u1", _pending(), actor="web.u1")
    runtime.channels.attachments.receive("web", "u1", _pending(name="x.exe", mime=""), actor="web.u1")

    kinds = [event.type for event in runtime.audit.list(limit=50)]
    assert EventType.GATEWAY_ATTACHMENT in kinds
    assert EventType.GATEWAY_ATTACHMENT_REJECTED in kinds
    runtime.close()


# ----------------------------------------------------------------------
# botões
# ----------------------------------------------------------------------
def _task_waiting_approval(runtime, monkeypatch) -> tuple[object, Approval]:
    created = runtime.submit("objetivo aprovável", agent_id="document-agent", created_by="web.u1")
    created.status = TaskStatus.WAITING
    runtime.tasks.save(created)
    approval = Approval(
        id="apr_teste",
        action="gravar relatório",
        tool="filesystem.write",
        args={"path": "artifacts/x.md"},
        requested_by="document-agent",
        task_id=created.id,
        required_role="approver",
        environment=Environment.DEVELOPMENT,
    )
    runtime.approvals.save(approval)
    monkeypatch.setattr(runtime, "submit", lambda *args, **kwargs: created)
    return created, approval


def test_buttons_appear_only_for_who_can_decide(workspace, monkeypatch):
    runtime = _gateway(workspace)
    _paired(runtime)
    _task_waiting_approval(runtime, monkeypatch)

    reply = runtime.channels.handle("web", "u1", "/run qualquer coisa")

    assert [choice.action for choice in reply.choices] == ["aprovar", "recusar"]
    runtime.close()


def test_viewer_gets_no_buttons(workspace, monkeypatch):
    runtime = _gateway(workspace)
    _paired(runtime, roles=["viewer"])
    _task_waiting_approval(runtime, monkeypatch)

    reply = runtime.channels.handle("web", "u1", "/run qualquer coisa")

    assert reply.denied  # viewer não envia tasks
    runtime.close()


def test_button_press_approves_through_the_same_government(workspace, monkeypatch):
    runtime = _gateway(workspace)
    _paired(runtime)
    _, approval = _task_waiting_approval(runtime, monkeypatch)

    reply = runtime.channels.handle_interaction("web", "u1", "aprovar", approval.id)

    assert reply.command == "aprovar"
    assert runtime.approvals.get("apr_teste").status == ApprovalStatus.APPROVED
    assert EventType.GATEWAY_INTERACTION in [event.type for event in runtime.audit.list(limit=20)]
    runtime.close()


def test_button_press_can_refuse(workspace, monkeypatch):
    runtime = _gateway(workspace)
    _paired(runtime)
    _, approval = _task_waiting_approval(runtime, monkeypatch)

    reply = runtime.channels.handle_interaction("web", "u1", "recusar", approval.id)

    assert reply.command == "recusar"
    assert runtime.approvals.get("apr_teste").status == ApprovalStatus.DENIED
    runtime.close()


def test_unknown_action_is_refused(workspace):
    runtime = _gateway(workspace)
    _paired(runtime)

    reply = runtime.channels.handle_interaction("web", "u1", "deletar-tudo", "x")

    assert reply.denied and reply.reason == "ação desconhecida"
    runtime.close()


def test_recusar_text_command_needs_allow_decisions(workspace):
    runtime = _gateway(workspace)
    _paired(runtime)
    runtime.settings.config.gateway.channels[0].allow_decisions = False

    reply = runtime.channels.handle("web", "u1", "/recusar apr_teste")

    assert reply.denied and reply.reason == "canal sem allow_decisions"
    runtime.close()


# ----------------------------------------------------------------------
# canais
# ----------------------------------------------------------------------
def test_telegram_describes_media_without_downloading(workspace):
    channel = TelegramChannel("telegram", None, client=FakeClient(), token="t")

    found = channel.attachments_from(
        {
            "document": {"file_id": "f1", "file_name": "nota.txt", "mime_type": "text/plain", "file_size": 42},
            "photo": [{"file_id": "p1", "file_unique_id": "x", "file_size": 10}],
        }
    )

    assert [item.name for item in found] == ["nota.txt", "foto-x.jpg"]
    assert found[0].mime == "text/plain" and found[0].size == 42
    assert found[0].fetch is not None


def test_telegram_download_uses_getfile_then_file_url(workspace):
    client = FakeClient(payloads=[{"ok": True, "result": {"file_path": "documents/nota.txt"}}], files={})
    channel = TelegramChannel("telegram", None, client=client, token="tok")

    content = channel.download("f1")

    assert content == b"conteudo"
    assert client.gets[0].endswith("/file/bottok/documents/nota.txt")


def test_telegram_update_with_document_enters_governed(workspace):
    runtime = _gateway(workspace)
    runtime.channels.register(TelegramChannel("telegram", None, client=FakeClient(
        payloads=[
            {"ok": True, "result": {"file_path": "documents/nota.txt"}},
            {"ok": True},
        ],
        files={"https://api.telegram.org/file/bottok/documents/nota.txt": b"nota fiscal 123"},
    ), token="tok"))
    runtime.channels.pair("telegram", "42", roles=["operator"])
    update = {
        "update_id": 7,
        "message": {
            "chat": {"id": 42},
            "from": {"first_name": "Vitor"},
            "caption": "classifique",
            "document": {"file_id": "f1", "file_name": "nota.txt", "mime_type": "text/plain", "file_size": 15},
        },
    }

    reply = runtime.channels.channel("telegram").handle_update(update, runtime.channels.handle_inbound)

    assert reply.task_id
    assert "Anexos recebidos" in runtime.tasks.get(reply.task_id).objective
    stored = runtime.gateway_attachments.list(limit=1)[0]
    assert stored.stored and (workspace / stored.path).read_bytes() == b"nota fiscal 123"
    runtime.close()


def test_telegram_send_attachment_posts_multipart(workspace):
    client = FakeClient(payloads=[{"ok": True}])
    channel = TelegramChannel("telegram", None, client=client, token="tok")
    target = workspace / "artifacts" / "saida.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("relatório")

    channel.send_attachment("42", str(target), name="saida.txt", mime="text/plain")

    call = client.posts[-1]
    assert call["url"].endswith("/sendDocument")
    assert call["data"]["chat_id"] == "42"
    assert call["files"]["document"][0] == "saida.txt"


def test_slack_describes_files_and_downloads_with_token(workspace):
    channel = SlackChannel("slack", None, client=FakeClient(), token="xoxb")
    event = {
        "files": [
            {"id": "F1", "name": "extrato.csv", "mimetype": "text/csv", "size": 30, "url_private": "https://files/x"}
        ]
    }

    found = channel.attachments_from(event)

    assert found[0].name == "extrato.csv"
    assert found[0].fetch() == b"conteudo"


def test_slack_payload_with_file_enters_governed(workspace):
    runtime = _gateway(workspace)
    runtime.channels.register(
        SlackChannel(
            "slack",
            None,
            client=FakeClient(files={"https://files/x": b"valor;1200"}),
            token="xoxb",
        )
    )
    runtime.channels.pair("slack", "U1", roles=["operator"])
    payload = {
        "type": "event_callback",
        "event": {
            "type": "message",
            "user": "U1",
            "channel": "C1",
            "text": "segue o extrato",
            "files": [{"id": "F1", "name": "extrato.csv", "mimetype": "text/csv", "size": 11, "url_private": "https://files/x"}],
        },
    }

    reply = runtime.channels.channel("slack").handle_payload(payload, runtime.channels.handle_inbound)

    assert reply.task_id
    assert runtime.gateway_attachments.list(limit=1)[0].stored
    runtime.close()


def test_web_channel_collects_outbound_files(workspace):
    channel = WebChannel("web", None)
    channel.send("u1", "texto")
    channel.send_attachment("u1", "artifacts/x.md", name="x.md", mime="text/markdown")

    drained = channel.drain()

    assert drained[0]["texto"] == "texto"
    assert drained[1]["arquivo"]["nome"] == "x.md"


def test_console_channel_prints_the_file(workspace, capsys):
    channel = ConsoleChannel("console", None)
    channel.send_attachment("u1", "artifacts/x.md", name="x.md")

    assert "[arquivo] x.md" in capsys.readouterr().out


def test_deliver_sends_text_and_attachments(workspace):
    channel = WebChannel("web", None)
    reply = runtime_free_reply()

    channel.deliver("u1", reply)

    drained = channel.drain()
    assert drained[0]["texto"] == "seu arquivo"
    assert drained[1]["arquivo"]["nome"] == "x.md"


def runtime_free_reply():
    from egr.domain.channel import GatewayReply, ReplyChoice

    reply = GatewayReply(text="seu arquivo", channel="web", external_id="u1")
    reply.attachments = [
        {"name": "x.md", "path": "artifacts/x.md", "absolute": "artifacts/x.md", "mime": "text/markdown"}
    ]
    reply.choices = [ReplyChoice(label="Aprovar", action="aprovar", value="apr_1")]
    return reply


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------
def test_api_upload_and_list(workspace):
    runtime = _gateway(workspace)
    _paired(runtime)
    client = TestClient(create_app(runtime))

    created = client.post(
        "/v1/gateway/attachments",
        json={
            "channel": "web",
            "external_id": "u1",
            "name": "nota.txt",
            "mime": "text/plain",
            "content_base64": base64.b64encode(b"cliente ACME").decode(),
        },
    )

    assert created.status_code == 200
    body = created.json()["anexo"]
    assert body["situação"] == "stored"
    listed = client.get("/v1/gateway/attachments").json()
    assert listed[0]["id"] == body["id"]
    detail = client.get(f"/v1/gateway/attachments/{body['id']}").json()
    assert "cliente ACME" in detail["trecho"]
    assert client.get("/v1/gateway/attachments/att_nada").status_code == 404
    runtime.close()


def test_api_upload_requires_pairing(workspace):
    runtime = _gateway(workspace)  # require_pairing padrão
    client = TestClient(create_app(runtime))

    response = client.post(
        "/v1/gateway/attachments",
        json={
            "channel": "web",
            "external_id": "anonimo",
            "name": "a.txt",
            "content_base64": base64.b64encode(b"x").decode(),
        },
    )

    assert response.status_code == 403
    runtime.close()


def test_api_upload_refuses_bad_extension(workspace):
    runtime = _gateway(workspace)
    _paired(runtime)
    client = TestClient(create_app(runtime))

    response = client.post(
        "/v1/gateway/attachments",
        json={
            "channel": "web",
            "external_id": "u1",
            "name": "x.exe",
            "content_base64": base64.b64encode(b"x").decode(),
        },
    )

    assert response.status_code == 200
    assert response.json()["anexo"]["situação"] == "rejected"
    runtime.close()


def test_api_message_with_attachment(workspace):
    runtime = _gateway(workspace)
    _paired(runtime)
    client = TestClient(create_app(runtime))

    response = client.post(
        "/v1/gateway/messages",
        json={
            "channel": "web",
            "external_id": "u1",
            "text": "leia o anexo",
            "attachments": [
                {"name": "nota.txt", "mime": "text/plain", "content_base64": base64.b64encode(b"total 42").decode()}
            ],
        },
    )

    assert response.status_code == 200
    task = runtime.tasks.get(response.json()["task"])
    assert "Anexos recebidos" in task.objective
    runtime.close()


def test_api_interaction_presses_the_button(workspace, monkeypatch):
    runtime = _gateway(workspace)
    _paired(runtime)
    _, approval = _task_waiting_approval(runtime, monkeypatch)
    client = TestClient(create_app(runtime))

    ok = client.post(
        "/v1/gateway/interactions",
        json={"channel": "web", "external_id": "u1", "action": "aprovar", "value": approval.id},
    )
    unknown = client.post(
        "/v1/gateway/interactions",
        json={"channel": "web", "external_id": "u1", "action": "nada", "value": ""},
    )

    assert ok.status_code == 200 and ok.json()["comando"] == "aprovar"
    assert runtime.approvals.get(approval.id).status == ApprovalStatus.APPROVED
    assert unknown.json()["recusada"] is True
    runtime.close()


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def test_cli_attachments_and_attachment(workspace, capsys):
    from typer.testing import CliRunner

    from egr.cli.main import app as cli_app

    runtime = _gateway(workspace)
    _paired(runtime)
    stored = runtime.channels.attachments.receive("web", "u1", _pending(), actor="web.u1")
    runtime.close()

    runner = CliRunner()
    listed = runner.invoke(cli_app, ["gateway", "attachments", "-w", str(workspace)])
    detail = runner.invoke(cli_app, ["gateway", "attachment", stored.id, "-w", str(workspace)])
    missing = runner.invoke(cli_app, ["gateway", "attachment", "att_nada", "-w", str(workspace)])

    assert listed.exit_code == 0 and "nota.txt" in listed.output
    assert detail.exit_code == 0 and stored.path in detail.output
    assert missing.exit_code == 1


def test_cli_interact_presses_button(workspace):
    from typer.testing import CliRunner

    from egr.cli.main import app as cli_app

    runtime = _gateway(workspace)
    _paired(runtime)
    approval = Approval(
        id="apr_cli",
        action="gravar",
        tool="filesystem.write",
        args={},
        requested_by="document-agent",
        required_role="approver",
        environment=Environment.DEVELOPMENT,
    )
    runtime.approvals.save(approval)
    _persist(workspace, runtime)

    result = CliRunner().invoke(
        cli_app, ["gateway", "interact", "web", "u1", "aprovar", approval.id, "-w", str(workspace)]
    )

    assert result.exit_code == 0, result.output
    assert runtime.approvals.get(approval.id).status == ApprovalStatus.APPROVED
    runtime.close()


def test_cli_send_file_refuses_paths_outside_roots(workspace):
    from typer.testing import CliRunner

    from egr.cli.main import app as cli_app

    runtime = _gateway(workspace)
    _persist(workspace, runtime)
    runtime.close()

    result = CliRunner().invoke(cli_app, ["gateway", "send-file", "web", "u1", "documents/x.md", "-w", str(workspace)])

    assert result.exit_code == 1
    assert "raízes" in result.output


# ----------------------------------------------------------------------
# saúde
# ----------------------------------------------------------------------
def test_channel_status_reports_attachments(workspace):
    runtime = _gateway(workspace)
    runtime.channels.attachments.receive("web", "u1", _pending(), actor="web.u1")

    status = runtime.channel_status()

    assert status["anexos"]["por_situação"]["stored"] == 1
    runtime.close()


def test_help_mentions_attachments():
    from egr.gateway.service import HELP

    assert "/anexos" in HELP and "/arquivo" in HELP


def test_attachment_service_is_reachable_from_runtime(workspace):
    runtime = _gateway(workspace)

    assert isinstance(runtime.channels.attachments, AttachmentService)


@pytest.mark.parametrize("channel_kind", ["web", "console"])
def test_every_channel_can_receive_files(workspace, channel_kind):
    from egr.gateway.channels import BaseChannel

    runtime = _gateway(workspace)
    instance = WebChannel("web", None) if channel_kind == "web" else ConsoleChannel("console", None)

    assert isinstance(instance, BaseChannel)
    assert hasattr(instance, "send_attachment")
    runtime.close()
