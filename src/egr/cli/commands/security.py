"""egr security | identity | secret | key · identidade verificável, RBAC e cofre.

Aprovação humana sem identidade é decorativa: este módulo transforma "quem
aprovou" em um fato verificável (token + papel + auditoria) e mantém as
credenciais fora de `egr.yaml` e do banco em claro.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from ...core.errors import AuthenticationError, AuthorizationError, KeyStoreError, VaultError
from ...security.rbac import ROLE_PERMISSIONS, ROLES
from ...security.rbac import describe as describe_rbac
from ..context import get_runtime
from ..formatting import error, info, json_output, kv, secret_line, success, table, warning

app = typer.Typer(help="Segurança: postura do Runtime, papéis e permissões")
identity_app = typer.Typer(help="Identidades verificáveis (humanos, agentes, serviços)")
secret_app = typer.Typer(help="Cofre de segredos (cifrado em repouso)")
key_app = typer.Typer(help="Gestão da chave mestra do cofre")

HINT_TOKEN = "use o token: egr approval approve <id> --token egr_..."


@app.command(name="status")
def security_status(
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Postura de segurança: identidades, cofre, chave e lacunas."""

    runtime = get_runtime(workspace)
    data = runtime.security_status()
    if as_json:
        json_output(data)
        return

    vault = data["vault"]
    key = vault["master_key"]
    kv(
        "Segurança",
        {
            "identidade exigida": data["identity_required"],
            "papel mínimo p/ aprovar": data["approval_min_role"],
            "agentes podem aprovar": data["allow_agent_approval"],
            "principais": f"{data['identities']['active']} ativos / {data['identities']['total']} total",
            "tokens ativos": data["identities"]["tokens_active"],
            "chave mestra": key["key_id"] or "ausente",
            "origem da chave": key["source"] or "-",
            "permissões do arquivo": key["mode"] or "-",
            "envelope do cofre": vault["envelope"],
            "segredos no cofre": vault["secrets"],
            "chaves registradas": len(data["master_key_history"]),
        },
    )
    if data["identities"]["total"]:
        table(
            "Papéis",
            ["papel", "principais"],
            [[role, total] for role, total in data["identities"]["by_role"].items() if total],
        )
    if data["gaps"]:
        warning("lacunas de segurança:")
        for gap in data["gaps"]:
            info(f"  · {gap}")


@app.command(name="roles")
def roles(
    role: str = typer.Option(None, "--role", "-r", help="Detalhar um papel"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Matriz RBAC: papéis, herança e permissões."""

    data = describe_rbac()
    if as_json:
        json_output(data if not role else next(item for item in data["roles"] if item["role"] == role))
        return
    if role:
        if role not in ROLE_PERMISSIONS:
            error(f"papel desconhecido: {role} (válidos: {', '.join(ROLES)})")
            raise typer.Exit(code=1)
        entry = next(item for item in data["roles"] if item["role"] == role)
        kv(
            f"Papel {role}",
            {"herda": ", ".join(entry["inherits"]) or "-", "permissões": ", ".join(entry["permissions"])},
        )
        return
    table(
        "RBAC",
        ["papel", "herda", "permissões"],
        [[item["role"], ", ".join(item["inherits"]) or "-", ", ".join(item["permissions"])] for item in data["roles"]],
    )


# ----------------------------------------------------------------------
# identidades
# ----------------------------------------------------------------------
@identity_app.command(name="add")
def identity_add(
    principal_id: str = typer.Argument(..., help="Id do principal (ex.: vitor, ci-bot)"),
    name: str = typer.Option("", "--name", "-n"),
    kind: str = typer.Option("human", "--kind", "-k", help="human | agent | service"),
    roles: list[str] = typer.Option(None, "--roles", "-r", help="Papel (repetível ou separado por vírgula)"),
    areas: list[str] = typer.Option(None, "--areas", "-a", help="Áreas (repetível ou separado por vírgula)"),
    email: str = typer.Option(None, "--email"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Cria uma identidade verificável."""

    runtime = get_runtime(workspace)
    try:
        principal = runtime.identity.create_principal(
            principal_id,
            name=name,
            kind=kind,
            roles=_split_roles(roles),
            areas=_split_roles(areas),
            email=email,
            actor="cli",
        )
    except (AuthorizationError, ValueError) as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    success(f"principal '{principal.id}' criado ({principal.kind})")
    info(f"papéis: {', '.join(principal.roles)}")
    info(f"áreas: {', '.join(principal.areas) or 'todas'}")
    info(f"próximo passo: egr identity token {principal.id}")


@identity_app.command(name="list")
def identity_list(
    kind: str = typer.Option(None, "--kind", "-k"),
    status: str = typer.Option(None, "--status", "-s"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Lista identidades."""

    runtime = get_runtime(workspace)
    principals = runtime.identity.list(kind=kind, status=status)
    if as_json:
        json_output([item.as_row() for item in principals])
        return
    if not principals:
        info("nenhuma identidade cadastrada")
        return
    table(
        "Identidades",
        ["id", "nome", "tipo", "papéis", "status", "último acesso"],
        [
            [
                item.id,
                item.name,
                str(item.kind),
                ", ".join(item.roles),
                str(item.status),
                item.last_seen_at.strftime("%Y-%m-%d %H:%M") if item.last_seen_at else "-",
            ]
            for item in principals
        ],
    )


@identity_app.command(name="show")
def identity_show(
    principal_id: str = typer.Argument(...),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Mostra uma identidade e suas permissões efetivas."""

    runtime = get_runtime(workspace)
    principal = runtime.identity.get(principal_id)
    if principal is None:
        error(f"principal '{principal_id}' não encontrado")
        raise typer.Exit(code=1)
    if as_json:
        json_output(principal.as_row())
        return
    kv(
        f"Principal {principal.id}",
        {
            "nome": principal.name,
            "tipo": str(principal.kind),
            "status": str(principal.status),
            "e-mail": principal.email or "-",
            "papéis": ", ".join(principal.roles),
            "áreas": ", ".join(principal.areas) or "todas",
            "permissões": ", ".join(principal.permissions),
            "último acesso": principal.last_seen_at.isoformat() if principal.last_seen_at else "-",
        },
    )


@identity_app.command(name="roles")
def identity_roles(
    principal_id: str = typer.Argument(...),
    roles: list[str] = typer.Option(..., "--roles", "-r"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Define os papéis de uma identidade."""

    runtime = get_runtime(workspace)
    try:
        principal = runtime.identity.set_roles(principal_id, _split_roles(roles), actor="cli")
    except (AuthorizationError, AuthenticationError) as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    success(f"papéis de '{principal.id}': {', '.join(principal.roles)}")


@identity_app.command(name="areas")
def identity_areas(
    principal_id: str = typer.Argument(...),
    areas: list[str] = typer.Option(..., "--areas", "-a"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Define as áreas de uma identidade (vazio = todas)."""

    runtime = get_runtime(workspace)
    try:
        principal = runtime.identity.set_areas(principal_id, _split_roles(areas), actor="cli")
    except (AuthorizationError, AuthenticationError) as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    success(f"áreas de '{principal.id}': {', '.join(principal.areas) or 'todas'}")


@identity_app.command(name="disable")
def identity_disable(
    principal_id: str = typer.Argument(...),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Desabilita uma identidade (tokens param de funcionar na hora)."""

    runtime = get_runtime(workspace)
    try:
        principal = runtime.identity.set_status(principal_id, "disabled", actor="cli")
    except (AuthenticationError, ValueError) as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    success(f"principal '{principal.id}' desabilitado")


@identity_app.command(name="enable")
def identity_enable(
    principal_id: str = typer.Argument(...),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Reabilita uma identidade."""

    runtime = get_runtime(workspace)
    try:
        principal = runtime.identity.set_status(principal_id, "active", actor="cli")
    except (AuthenticationError, ValueError) as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    success(f"principal '{principal.id}' ativo")


@identity_app.command(name="token")
def identity_token(
    principal_id: str = typer.Argument(...),
    ttl_days: int = typer.Option(90, "--ttl-days", "-t", help="0 = sem expiração"),
    label: str = typer.Option("", "--label", "-l"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Emite um token. Ele aparece **uma única vez** — só o hash é guardado."""

    runtime = get_runtime(workspace)
    try:
        raw, record = runtime.identity.issue_token(principal_id, ttl_days=ttl_days, label=label, actor="cli")
    except AuthenticationError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    kv(
        "Token emitido",
        {
            "principal": record.principal_id,
            "token_id": record.id,
            "expira em": record.expires_at.isoformat() if record.expires_at else "nunca",
        },
    )
    secret_line("token:", raw)
    warning("guarde o token agora: o Runtime guarda apenas o hash")
    info(HINT_TOKEN)


@identity_app.command(name="tokens")
def identity_tokens(
    principal_id: str = typer.Option(None, "--principal", "-p"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Lista tokens (metadados — nunca o segredo)."""

    runtime = get_runtime(workspace)
    tokens = runtime.identity.tokens(principal_id=principal_id)
    if as_json:
        json_output([token.as_row() for token in tokens])
        return
    if not tokens:
        info("nenhum token emitido")
        return
    table(
        "Tokens",
        ["token_id", "principal", "status", "criado", "expira", "último uso"],
        [
            [
                token.id,
                token.principal_id,
                "revogado" if token.revoked else ("expirado" if token.expired else "ativo"),
                token.created_at.strftime("%Y-%m-%d"),
                token.expires_at.strftime("%Y-%m-%d") if token.expires_at else "-",
                token.last_used_at.strftime("%Y-%m-%d %H:%M") if token.last_used_at else "-",
            ]
            for token in tokens
        ],
    )


@identity_app.command(name="revoke-token")
def identity_revoke_token(
    token_id: str = typer.Argument(...),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Revoga um token imediatamente."""

    runtime = get_runtime(workspace)
    try:
        runtime.identity.revoke_token(token_id, actor="cli")
    except AuthenticationError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    success(f"token '{token_id}' revogado")


@identity_app.command(name="whoami")
def identity_whoami(
    by: str = typer.Option(None, "--by", help="id do principal, token egr_... ou JWT do IdP"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Quem é este ator, segundo o Runtime?"""

    runtime = get_runtime(workspace)
    data = runtime.identity.whoami(by)
    if as_json:
        json_output(data)
        return
    if not data["authenticated"]:
        warning(f"ator não autenticado: {by or '(vazio)'}")
        info("cadastre uma identidade: egr identity add <id> --roles approver")
        raise typer.Exit(code=1)
    kv(
        "Identidade",
        {
            "id": data["id"],
            "nome": data["name"],
            "tipo": data["kind"],
            "papéis": ", ".join(data["roles"]),
            "permissões": ", ".join(data["permissions"]),
        },
    )


@identity_app.command(name="login-sso")
def identity_login_sso(
    jwt: str = typer.Option(..., "--jwt", help="JWT emitido pelo IdP da empresa"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Autentica com o IdP (provisiona o humano no primeiro login)."""

    runtime = get_runtime(workspace)
    try:
        principal = runtime.identity.authenticate_sso(jwt)
    except AuthenticationError as exc:
        warning(f"login SSO falhou: {exc}")
        info("confira security.sso no egr.yaml e o segredo em $EGR_SSO_SECRET")
        raise typer.Exit(code=1) from exc
    data = principal.as_row()
    if as_json:
        json_output({"authenticated": True, **data})
        return
    kv(
        "SSO",
        {
            "id": data["id"],
            "nome": data["name"],
            "papéis": ", ".join(data["roles"]),
            "áreas": ", ".join(data["areas"]) or "(todas)",
        },
    )
    success(f"login SSO como {data['id']}")


# ----------------------------------------------------------------------
# cofre
# ----------------------------------------------------------------------
@secret_app.command(name="set")
def secret_set(
    name: str = typer.Argument(..., help="Nome da referência (vault:NOME)"),
    value: str = typer.Option(None, "--value", help="Valor (prefira --stdin: evita histórico do shell)"),
    stdin: bool = typer.Option(False, "--stdin", help="Ler o valor da entrada padrão"),
    provider: str = typer.Option(None, "--provider", "-p", help="openai | ollama | smtp | ..."),
    description: str = typer.Option("", "--description", "-d"),
    by: str = typer.Option("cli", "--by"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Guarda um segredo cifrado no cofre (nunca em `egr.yaml`)."""

    runtime = get_runtime(workspace)
    try:
        secret = _read_value(value, stdin)
        record = runtime.vault.put(
            name,
            secret,
            provider=provider,
            description=description,
            actor=by,
        )
    except (VaultError, KeyStoreError) as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    success(f"segredo '{record.name}' guardado (versão {record.version}, chave {record.key_id})")
    info(f"use no egr.yaml: api_key_env: vault:{record.name}")


@secret_app.command(name="list")
def secret_list(
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Lista segredos (metadados — o valor nunca aparece)."""

    runtime = get_runtime(workspace)
    rows = runtime.vault.list()
    if as_json:
        json_output(rows)
        return
    if not rows:
        info("cofre vazio")
        return
    table(
        "Cofre",
        ["nome", "provider", "chave", "versão", "ambiente", "atualizado em", "rotação"],
        [
            [
                row["name"],
                row["provider"],
                row["key_id"],
                row["version"],
                row["environment"],
                row["updated_at"][:19] if row["updated_at"] else "-",
                row["rotated_at"][:19] if row["rotated_at"] else "-",
            ]
            for row in rows
        ],
    )


@secret_app.command(name="show")
def secret_show(
    name: str = typer.Argument(...),
    reveal: bool = typer.Option(False, "--reveal", help="Mostra o valor em claro"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Mostra os metadados de um segredo (e o valor, só com --reveal)."""

    runtime = get_runtime(workspace)
    record = runtime.vault.get(name)
    if record is None:
        error(f"segredo '{name}' não encontrado")
        raise typer.Exit(code=1)
    data = {
        "nome": record.name,
        "provider": record.provider or "-",
        "descrição": record.description or "-",
        "ambiente": record.environment,
        "chave": record.key_id,
        "versão": record.version,
        "envelope": f"{len(record.ciphertext)} bytes cifrados",
        "criado por": record.created_by,
    }
    if reveal:
        try:
            data["valor"] = runtime.vault.reveal(name) or ""
        except (VaultError, KeyStoreError) as exc:
            error(str(exc))
            raise typer.Exit(code=1) from exc
        value = data.pop("valor")
        kv(f"Segredo {name}", data)
        warning("valor sensível exibido na tela")
        secret_line("valor:", value)
        return
    kv(f"Segredo {name}", data)


@secret_app.command(name="rotate")
def secret_rotate(
    name: str = typer.Argument(...),
    value: str = typer.Option(None, "--value"),
    stdin: bool = typer.Option(False, "--stdin"),
    by: str = typer.Option("cli", "--by"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Rotaciona o valor de um segredo (versiona e recifra)."""

    runtime = get_runtime(workspace)
    try:
        record = runtime.vault.rotate(name, _read_value(value, stdin), actor=by)
    except (VaultError, KeyStoreError) as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    success(f"segredo '{record.name}' rotacionado (versão {record.version})")


@secret_app.command(name="rm")
def secret_remove(
    name: str = typer.Argument(...),
    by: str = typer.Option("cli", "--by"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Remove um segredo do cofre."""

    runtime = get_runtime(workspace)
    if not runtime.vault.delete(name, actor=by):
        error(f"segredo '{name}' não encontrado")
        raise typer.Exit(code=1)
    success(f"segredo '{name}' removido")


# ----------------------------------------------------------------------
# chaves
# ----------------------------------------------------------------------
@key_app.command(name="init")
def key_init(
    force: bool = typer.Option(False, "--force", help="Gerar nova chave mesmo se já existir"),
    by: str = typer.Option("cli", "--by"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Cria a chave mestra do cofre (0600, fora do banco e do YAML)."""

    runtime = get_runtime(workspace)
    try:
        status = runtime.init_master_key(actor=by, force=force)
    except (KeyStoreError, VaultError) as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    success(f"chave mestra pronta: {status['key_id']}")
    info(f"arquivo: {status['path']} (permissões {status['mode']})")
    warning("sem backup da chave, os segredos do cofre não podem ser recuperados")


@key_app.command(name="status")
def key_status(
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Estado da chave mestra e histórico de rotações."""

    runtime = get_runtime(workspace)
    status = runtime.keystore.status()
    history = runtime.key_repository.list()
    if as_json:
        json_output({"status": status, "history": history})
        return
    kv(
        "Chave mestra",
        {
            "presente": status["present"],
            "key_id": status["key_id"] or "-",
            "origem": status["source"] or "-",
            "arquivo": status["path"],
            "permissões": status["mode"] or "-",
            "variável de ambiente": status["env_var"],
            "chaves registradas": len(history),
            "segredos no cofre": runtime.vault.count(),
        },
    )
    problem = runtime.keystore.permission_problem()
    if problem:
        warning(problem)


@key_app.command(name="rotate")
def key_rotate(
    force: bool = typer.Option(False, "--force", help="Rotacionar mesmo com chave vinda do ambiente"),
    by: str = typer.Option("cli", "--by"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Gera nova chave mestra e recifra todos os segredos."""

    runtime = get_runtime(workspace)
    try:
        result = runtime.rotate_master_key(actor=by, force=force)
    except (KeyStoreError, VaultError) as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    success(f"chave rotacionada: {result['previous_key_id'] or '-'} → {result['key_id']}")
    info(f"segredos recifrados: {result['secrets_reencrypted']}")


# ----------------------------------------------------------------------
def _split_roles(roles: list[str] | None) -> list[str]:
    values: list[str] = []
    for item in roles or []:
        values.extend(part.strip() for part in item.split(",") if part.strip())
    return values


def _read_value(value: str | None, stdin: bool) -> str:
    if value:
        return value
    if stdin or not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return typer.prompt("valor do segredo", hide_input=True, confirmation_prompt=True)


__all__ = ["app", "identity_app", "key_app", "secret_app"]
