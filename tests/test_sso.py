"""Costura de SSO: o Runtime aceita o IdP da empresa, sem virar um IdP.

Fatia 5 do fechamento do runtime: JWT HS256 do gateway/IdP autentica o humano
(`resolve` aceita JWT quando `security.sso` está habilitado), grupos viram
papéis/áreas via mapas e o principal é provisionado no primeiro login.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest
import yaml
from typer.testing import CliRunner

from egr.cli.main import app as cli_app
from egr.core.config import EGRConfig, Settings
from egr.core.errors import AuthenticationError, AuthorizationError, ConfigError
from egr.core.ids import new_id
from egr.domain.approval import Approval, ApprovalStatus
from egr.domain.enums import Environment
from egr.runtime.runtime import Runtime
from egr.security.sso import SSOConfig, SSOVerifier

runner = CliRunner()
SECRET = "segredo-sso-de-teste"
ISSUER = "https://idp.empresa.com"


def _jwt(payload: dict, secret: str = SECRET) -> str:
    def b64(obj) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    header, body = b64({"alg": "HS256", "typ": "JWT"}), b64(payload)
    sig = hmac.new(secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
    return f"{header}.{body}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"


def _claims(**over) -> dict:
    payload = {
        "sub": "ana",
        "iss": ISSUER,
        "aud": "egr",
        "exp": int(time.time()) + 3600,
        "email": "ana@empresa.com",
        "name": "Ana",
        "groups": ["fin"],
    }
    payload.update(over)
    return payload


def _enable_sso(runtime, monkeypatch, **over):
    monkeypatch.setenv("EGR_SSO_SECRET", SECRET)
    cfg = SSOConfig(
        enabled=True,
        issuer=ISSUER,
        audience="egr",
        role_map={"fin": ["approver"], "rh": ["viewer"]},
        area_map={"fin": ["finance"], "rh": ["hr"]},
        **over,
    )
    runtime.identity.sso = SSOVerifier(cfg)
    return cfg


def _approval(runtime, agent_id: str) -> str:
    task = runtime.task_engine.create(f"rotina de {agent_id}", agent_id=agent_id, created_by="cli")
    approval = Approval(
        id=new_id("ap"),
        action="execute",
        tool="database.query",
        requested_by=agent_id,
        task_id=task.id,
        environment=Environment.PRODUCTION,
        status=ApprovalStatus.PENDING,
        reason="teste de sso",
    )
    runtime.approvals.save(approval)
    return approval.id


def test_jwt_valido_provisiona_humano(runtime, monkeypatch):
    _enable_sso(runtime, monkeypatch)

    principal = runtime.identity.authenticate_sso(_jwt(_claims()))

    assert principal.id == "ana@empresa.com"
    assert principal.roles == ["approver"]
    assert principal.areas == ["finance"]
    seals = runtime.audit.list(type="sso.authenticated", limit=5)
    assert seals and seals[0].payload.get("provisioned") is True


def test_relogin_sincroniza_grupos_do_idp(runtime, monkeypatch):
    _enable_sso(runtime, monkeypatch)
    runtime.identity.authenticate_sso(_jwt(_claims()))

    principal = runtime.identity.authenticate_sso(_jwt(_claims(groups=["rh"])))

    assert principal.roles == ["viewer"]
    assert principal.areas == ["hr"]
    assert runtime.audit.list(type="identity.updated", limit=5)


def test_jwt_ruim_falha_fechado(runtime, monkeypatch):
    _enable_sso(runtime, monkeypatch)
    cases = [
        (_jwt(_claims(exp=int(time.time()) - 10)), "expirado"),
        (_jwt(_claims(), secret="outro-segredo"), "assinatura"),
        (_jwt(_claims(aud="outro-app")), "audiência"),
        (_jwt(_claims(iss="https://idp.falso.com")), "emissor"),
        (_jwt({k: v for k, v in _claims().items() if k != "exp"}), "'exp'"),
        ("nao-e-jwt", "malformado"),
    ]
    for token, match in cases:
        with pytest.raises(AuthenticationError, match=match):
            runtime.identity.authenticate_sso(token)
    assert len(runtime.audit.list(type="security.auth_failed", limit=10)) == len(cases)


def test_segredo_ausente_falha_fechado(runtime, monkeypatch):
    monkeypatch.delenv("EGR_SSO_SECRET", raising=False)
    runtime.identity.sso = SSOVerifier(SSOConfig(enabled=True, issuer=ISSUER))

    with pytest.raises(AuthenticationError, match="segredo ausente"):
        runtime.identity.authenticate_sso(_jwt(_claims()))


def test_role_map_invalido_barra_no_boot(workspace):
    with pytest.raises(ConfigError, match="papéis desconhecidos"):
        SSOVerifier(SSOConfig(enabled=True, role_map={"x": ["super"]}))

    config = EGRConfig()
    config.security.sso = SSOConfig(enabled=True, role_map={"x": ["super"]})
    with pytest.raises(ConfigError, match="papéis desconhecidos"):
        Runtime(Settings(workspace=workspace, config=config), enable_logging=False)


def test_sso_desligado_ignora_jwt(runtime):
    token = _jwt(_claims())

    assert runtime.identity.sso is None
    assert runtime.identity.resolve(token) is None
    with pytest.raises(AuthenticationError, match="SSO desabilitado"):
        runtime.identity.authenticate_sso(token)


def test_resolve_aceita_jwt_quando_habilitado(runtime, monkeypatch):
    _enable_sso(runtime, monkeypatch)
    token = _jwt(_claims())

    principal = runtime.identity.resolve(token)

    assert principal is not None and principal.id == "ana@empresa.com"
    assert runtime.identity.resolve("id-inexistente") is None


def test_jwt_aprova_na_area_e_barra_fora_dela(runtime, monkeypatch):
    _enable_sso(runtime, monkeypatch)
    fin_id = _approval(runtime, "finance-agent")
    rh_id = _approval(runtime, "finance-agent")

    runtime.approve(fin_id, token=_jwt(_claims()))

    assert runtime.approvals.get(fin_id).status == "approved"
    assert runtime.identity.get("ana@empresa.com").roles == ["approver"]
    with pytest.raises(AuthorizationError, match="não alcança a área"):
        runtime.approve(rh_id, token=_jwt(_claims(sub="bia", email="bia@empresa.com", groups=["rh"])))


def test_principal_desabilitado_barra_sso(runtime, monkeypatch):
    _enable_sso(runtime, monkeypatch)
    token = _jwt(_claims())
    runtime.identity.authenticate_sso(token)
    runtime.identity.set_status("ana@empresa.com", "disabled")

    with pytest.raises(AuthenticationError, match="desabilitado"):
        runtime.identity.authenticate_sso(token)


def test_sem_email_usa_sub_com_prefixo(runtime, monkeypatch):
    _enable_sso(runtime, monkeypatch)
    payload = {k: v for k, v in _claims().items() if k != "email"}

    principal = runtime.identity.authenticate_sso(_jwt(payload))

    assert principal.id == "sso:ana"


def test_cli_login_sso_e_whoami(workspace, monkeypatch):
    monkeypatch.setenv("EGR_SSO_SECRET", SECRET)
    (workspace / "egr.yaml").write_text(
        yaml.dump(
            {
                "security": {
                    "sso": {
                        "enabled": True,
                        "issuer": ISSUER,
                        "audience": "egr",
                        "role_map": {"fin": ["approver"]},
                        "area_map": {"fin": ["finance"]},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    token = _jwt(_claims())

    logged = runner.invoke(
        cli_app, ["identity", "login-sso", "--jwt", token, "--workspace", str(workspace)]
    )
    assert logged.exit_code == 0, logged.output
    assert "ana@empresa.com" in logged.output

    who = runner.invoke(
        cli_app, ["identity", "whoami", "--by", token, "--workspace", str(workspace)]
    )
    assert who.exit_code == 0, who.output
