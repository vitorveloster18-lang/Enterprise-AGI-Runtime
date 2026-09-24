"""Fase 4 — Segurança + Política: identidade verificável, RBAC, cofre e chaves."""

from __future__ import annotations

from typing import ClassVar

import pytest

from egr.core.config import EGRConfig, Settings
from egr.core.errors import AuthenticationError, AuthorizationError, KeyStoreError, VaultError
from egr.domain.enums import ApprovalStatus, EventType
from egr.runtime.runtime import Runtime
from egr.security.crypto import decrypt_text, encrypt, key_id, new_master_key
from egr.security.identity import PrincipalKind
from egr.security.rbac import APPROVAL_DECIDE, has_permission, permissions_for, require, role_satisfies


@pytest.fixture()
def runtime(workspace):
    instance = Runtime(Settings(workspace=workspace, config=EGRConfig()), enable_logging=False)
    yield instance
    instance.close()


@pytest.fixture()
def enforcing(workspace):
    """Runtime que exige identidade verificável para decidir aprovações."""

    config = EGRConfig()
    config.security.identity_required = True
    instance = Runtime(Settings(workspace=workspace, config=config), enable_logging=False)
    yield instance
    instance.close()


# ----------------------------------------------------------------------
# criptografia
# ----------------------------------------------------------------------
def test_envelope_roundtrip():
    master = new_master_key()
    envelope, salt = encrypt("sk-segredo-123", master)
    assert envelope.startswith("EGR1.")
    assert decrypt_text(envelope, master, salt) == "sk-segredo-123"


def test_envelope_rejects_tampering():
    master = new_master_key()
    envelope, salt = encrypt("sk-segredo-123", master)
    parts = envelope.split(".")
    tampered = f"{parts[0]}.{parts[1]}.{'A' + parts[2][1:]}.{parts[3]}"
    with pytest.raises(VaultError, match="autenticação"):
        decrypt_text(tampered, master, salt)


def test_envelope_rejects_wrong_key():
    envelope, salt = encrypt("sk-segredo-123", new_master_key())
    with pytest.raises(VaultError):
        decrypt_text(envelope, new_master_key(), salt)


def test_key_id_is_stable_and_public():
    master = new_master_key()
    assert key_id(master).startswith("key_")
    assert key_id(master) == key_id(master)
    assert master.hex() not in key_id(master)


# ----------------------------------------------------------------------
# chaves
# ----------------------------------------------------------------------
def test_key_init_creates_protected_file(runtime):
    status = runtime.init_master_key(actor="vitor")

    assert status["present"] is True
    assert status["key_id"].startswith("key_")
    assert runtime.keystore.key_path.exists()
    assert runtime.keystore.permission_problem() is None
    assert runtime.key_repository.count() == 1


def test_vault_refuses_without_master_key(runtime):
    with pytest.raises(KeyStoreError, match="egr key init"):
        runtime.vault.put("openai", "sk-123")


def test_key_rotation_reencrypts_every_secret(runtime):
    runtime.init_master_key()
    runtime.vault.put("openai", "sk-antes")
    before = runtime.vault.get("openai")

    result = runtime.rotate_master_key(actor="vitor")

    assert result["secrets_reencrypted"] == 1
    assert result["key_id"] != result["previous_key_id"]
    after = runtime.vault.get("openai")
    assert after.key_id == result["key_id"]
    assert runtime.vault.reveal("openai") == "sk-antes"
    assert before.ciphertext != after.ciphertext  # envelope recifrado


# ----------------------------------------------------------------------
# cofre
# ----------------------------------------------------------------------
def test_vault_stores_and_reveals(runtime):
    runtime.init_master_key()
    runtime.vault.put("openai", "sk-vivo", provider="openai", actor="vitor")

    assert runtime.vault.reveal("openai") == "sk-vivo"
    assert runtime.vault.count() == 1


def test_vault_listing_never_exposes_plaintext(runtime):
    runtime.init_master_key()
    runtime.vault.put("openai", "sk-super-secreto", provider="openai")

    rows = runtime.vault.list()
    assert rows and "sk-super-secreto" not in str(rows)
    assert "ciphertext" not in rows[0]


def test_secret_rotation_bumps_version(runtime):
    runtime.init_master_key()
    runtime.vault.put("smtp", "senha-antiga")
    rotated = runtime.vault.rotate("smtp", "senha-nova", actor="vitor")

    assert rotated.version == 2
    assert runtime.vault.reveal("smtp") == "senha-nova"


def test_vault_reference_resolution(runtime):
    runtime.init_master_key()
    runtime.vault.put("openai", "sk-do-cofre")

    assert runtime.resolve_secret("vault:openai") == "sk-do-cofre"
    assert runtime.resolve_secret("vault:inexistente") is None
    assert runtime.resolve_secret(None) is None


def test_secret_value_never_reaches_the_audit_ledger(runtime):
    runtime.init_master_key()
    runtime.vault.put("openai", "sk-nunca-pode-aparecer", actor="vitor")

    payloads = str([event.payload for event in runtime.audit.list(limit=100)])
    assert "sk-nunca-pode-aparecer" not in payloads
    assert any(event.type == str(EventType.SECRET_STORED) for event in runtime.audit.list(limit=100))


# ----------------------------------------------------------------------
# identidade
# ----------------------------------------------------------------------
def test_issue_token_and_authenticate(runtime):
    runtime.identity.create_principal("vitor", roles=["approver"], kind=PrincipalKind.HUMAN)
    raw, record = runtime.identity.issue_token("vitor", ttl_days=30)

    assert raw.startswith("egr_")
    principal = runtime.identity.authenticate(raw)
    assert principal.id == "vitor"
    assert record.token_hash != raw  # só o hash é persistido


def test_authentication_rejects_bad_secret_and_revocation(runtime):
    runtime.identity.create_principal("vitor", roles=["approver"])
    raw, record = runtime.identity.issue_token("vitor")
    token_id, _secret = raw[len("egr_") :].split(".")
    forged = f"egr_{token_id}.{'x' * 43}"

    with pytest.raises(AuthenticationError):
        runtime.identity.authenticate(forged)

    runtime.identity.revoke_token(record.id)
    with pytest.raises(AuthenticationError, match="revogado"):
        runtime.identity.authenticate(raw)


def test_disabled_principal_cannot_authenticate(runtime):
    runtime.identity.create_principal("ex-funcionario", roles=["approver"])
    raw, _record = runtime.identity.issue_token("ex-funcionario")
    runtime.identity.set_status("ex-funcionario", "disabled")

    with pytest.raises(AuthenticationError, match="desabilitado"):
        runtime.identity.authenticate(raw)


def test_expired_token_is_refused(runtime):
    runtime.identity.create_principal("bot", roles=["approver"])
    raw, record = runtime.identity.issue_token("bot", ttl_days=1)
    record.expires_at = record.created_at  # expirou no mesmo instante
    runtime.identity.repository.save_token(record)

    with pytest.raises(AuthenticationError, match="expirado"):
        runtime.identity.authenticate(raw)


# ----------------------------------------------------------------------
# RBAC
# ----------------------------------------------------------------------
def test_permissions_are_additive_and_hierarchical():
    assert APPROVAL_DECIDE in permissions_for(["approver"])
    assert APPROVAL_DECIDE not in permissions_for(["viewer"])
    assert APPROVAL_DECIDE not in permissions_for(["operator"])
    assert APPROVAL_DECIDE in permissions_for(["admin"])


def test_role_satisfies_required_role():
    assert role_satisfies(["approver"], "approver") is True
    assert role_satisfies(["admin"], "approver") is True
    assert role_satisfies(["viewer"], "approver") is False


def test_require_fails_closed():
    class Fake:
        id = "estagiario"
        roles: ClassVar[list[str]] = ["viewer"]

    with pytest.raises(AuthorizationError, match=r"approval\.decide"):
        require(Fake(), APPROVAL_DECIDE)
    assert has_permission(Fake(), "task.read") is True


# ----------------------------------------------------------------------
# enforcement: quem pode decidir o crítico
# ----------------------------------------------------------------------
def test_anonymous_decision_is_refused_when_identity_is_required(enforcing):
    approval = _pending_approval(enforcing)

    with pytest.raises(AuthenticationError, match="identidade não verificada"):
        enforcing.approve(approval.id, decided_by="alguém-qualquer")

    assert enforcing.approvals.get(approval.id).status == ApprovalStatus.PENDING
    failed = [event for event in enforcing.audit.list(limit=50) if event.type == str(EventType.AUTH_FAILED)]
    assert failed and failed[-1].payload["approval"] == approval.id


def test_operator_without_approval_permission_is_refused(enforcing):
    enforcement = enforcing
    enforcement.identity.create_principal("ops", roles=["operator"])
    raw, _token = enforcement.identity.issue_token("ops")
    approval = _pending_approval(enforcement)

    with pytest.raises(AuthorizationError, match=r"approval\.decide"):
        enforcement.approve(approval.id, decided_by="ops", token=raw)

    denied = [
        event for event in enforcement.audit.list(limit=50) if event.type == str(EventType.AUTHORIZATION_DENIED)
    ]
    assert denied and denied[-1].payload["approval"] == approval.id


def test_approver_with_insufficient_role_is_refused(enforcing):
    enforcing.identity.create_principal("chefe", roles=["approver"])
    raw, _token = enforcing.identity.issue_token("chefe")
    approval = _pending_approval(enforcing)
    approval.required_role = "admin"
    enforcing.approvals.save(approval)

    with pytest.raises(AuthorizationError, match="admin"):
        enforcing.approve(approval.id, decided_by="chefe", token=raw)


def test_verified_approver_can_decide_and_is_recorded(enforcing):
    enforcing.identity.create_principal("vitor", roles=["approver"], kind=PrincipalKind.HUMAN)
    raw, _token = enforcing.identity.issue_token("vitor")
    approval = _pending_approval(enforcing)

    enforcing.approve(approval.id, decided_by="vitor", token=raw, note="ok")

    decided = enforcing.approvals.get(approval.id)
    assert decided.status == ApprovalStatus.APPROVED
    assert decided.decided_by == "vitor"
    human_event = [event for event in enforcing.audit.list(limit=50) if event.type == str(EventType.HUMAN_DECISION)]
    assert human_event and human_event[-1].actor == "vitor"
    assert human_event[-1].payload["identity_verified"] is True


def test_agent_principal_cannot_approve(enforcing):
    enforcing.identity.create_principal("runtime-agent", roles=["admin"], kind=PrincipalKind.AGENT)
    raw, _token = enforcing.identity.issue_token("runtime-agent")
    approval = _pending_approval(enforcing)

    with pytest.raises(AuthorizationError, match="não pode aprovar"):
        enforcing.approve(approval.id, decided_by="runtime-agent", token=raw)


def test_without_enforcement_the_decision_is_audited_as_unverified(runtime):
    approval = _pending_approval(runtime)

    runtime.approve(approval.id, decided_by="humano-sem-token")

    assert runtime.approvals.get(approval.id).status == ApprovalStatus.APPROVED
    events = [event for event in runtime.audit.list(limit=50) if event.type == str(EventType.HUMAN_DECISION)]
    assert events[-1].payload["identity_verified"] is False


# ----------------------------------------------------------------------
# postura
# ----------------------------------------------------------------------
def test_security_status_exposes_gaps(runtime):
    status = runtime.security_status()

    assert status["identity_required"] is False
    assert any("identidade" in gap for gap in status["gaps"])
    assert any("chave mestra" in gap for gap in status["gaps"])
    assert status["rbac"]["roles"][0] == "viewer"


def test_whoami_reports_authentication(runtime):
    runtime.identity.create_principal("vitor", roles=["approver"])
    raw, _token = runtime.identity.issue_token("vitor")

    assert runtime.identity.whoami(raw)["authenticated"] is True
    assert runtime.identity.whoami("ninguem")["authenticated"] is False


def _pending_approval(runtime) -> object:
    request, decision = runtime.request_action("email.send", {"to": "x@acme.com", "subject": "oi"})
    return runtime.request_approval(request, decision, agent=None)


# ----------------------------------------------------------------------
# API: a fronteira remota obedece ao mesmo RBAC
# ----------------------------------------------------------------------
def test_api_decision_requires_a_verifiable_credential(enforcing):
    from fastapi.testclient import TestClient

    from egr.api.server import create_app

    client = TestClient(create_app(enforcing))
    enforcing.identity.create_principal("diretora", roles=["admin"])
    raw, _token = enforcing.identity.issue_token("diretora")
    approval = _pending_approval(enforcing)

    denied = client.post(
        f"/v1/approvals/{approval.id}/decision",
        json={"decision": "approve", "by": "ninguem"},
    )
    assert denied.status_code == 401

    allowed = client.post(
        f"/v1/approvals/{approval.id}/decision",
        json={"decision": "approve", "by": "diretora"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert allowed.status_code == 200
    assert enforcing.approvals.get(approval.id).status == ApprovalStatus.APPROVED


def test_api_exposes_security_posture_without_secrets(runtime):
    from fastapi.testclient import TestClient

    from egr.api.server import create_app

    runtime.init_master_key()
    runtime.vault.put("openai", "sk-do-cofre", provider="openai")
    client = TestClient(create_app(runtime))

    payload = client.get("/v1/security").json()
    assert payload["vault"]["secrets"] == 1
    assert "sk-do-cofre" not in str(payload)
    assert client.get("/v1/principals/whoami").json()["authenticated"] is False
