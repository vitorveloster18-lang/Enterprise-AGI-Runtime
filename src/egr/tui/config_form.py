"""Configuração do workspace pela interface.

Tudo o que dá para mudar sem tocar em código está aqui: modelo e provedores,
orçamento, memória, segurança, sandbox, ferramentas, runtime, promoção,
coordenação, integrações, canais e logs.

Regras que a interface respeita:
  · segredo nunca é digitado no yaml — vai como nome de variável de ambiente;
  · nada é gravado sem passar pela validação do EGRConfig (pydantic);
  · gravar configuração é evento auditado (feito pelo chamador).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.config import EGRConfig, dump_config, load_config

CONFIG_FILE = "egr.yaml"


@dataclass(frozen=True)
class FieldSpec:
    """Um campo editável do `egr.yaml`."""

    section: str
    path: str
    label: str
    kind: str = "str"  # bool | int | float | str | choice
    choices: tuple[str, ...] = ()
    help: str = ""


FIELDS: tuple[FieldSpec, ...] = (
    # ── modelos e orçamento ──────────────────────────────────────────────────
    FieldSpec("Modelos", "models.routing", "Roteamento", "choice",
              ("priority", "cost", "local_first"),
              "priority = mais capaz; cost = mais barato; local_first = prefere o que não sai da máquina"),
    FieldSpec("Modelos", "models.budget.currency", "Moeda", "str", help="USD, BRL…"),
    FieldSpec("Modelos", "models.budget.per_task", "Teto por task", "float",
              help="vazio = sem teto"),
    FieldSpec("Modelos", "models.budget.per_day", "Teto por dia", "float",
              help="vazio = sem teto"),
    FieldSpec("Modelos", "models.budget.on_exceeded", "Ao estourar", "choice",
              ("deny", "warn")),
    # ── memória ──────────────────────────────────────────────────────────────
    FieldSpec("Memória", "memory.retrieval", "Recuperação", "choice",
              ("hybrid", "fts", "semantic")),
    FieldSpec("Memória", "memory.semantic_weight", "Peso semântico", "float",
              help="0 desliga o lado vetorial"),
    FieldSpec("Memória", "memory.min_cosine", "Corte de similaridade", "float"),
    FieldSpec("Memória", "memory.half_life_days", "Meia-vida (dias)", "int"),
    FieldSpec("Memória", "memory.duplicate_threshold", "Near-duplicata", "float"),
    FieldSpec("Memória", "memory.retention_days", "Retenção (dias)", "int"),
    FieldSpec("Memória", "memory.max_context_chars", "Máx. de contexto", "int"),
    FieldSpec("Memória", "memory.scrub_pii", "Limpar PII na escrita", "bool"),
    FieldSpec("Memória", "memory.auto_remember", "Lembrar sozinho", "bool"),
    FieldSpec("Memória", "memory.auto_recall", "Recuperar sozinho", "bool"),
    FieldSpec("Memória", "memory.media_enabled", "Mídia (multimodal)", "bool"),
    FieldSpec("Memória", "memory.max_media_bytes", "Máx. por arquivo", "int",
              help="bytes"),
    # ── segurança ────────────────────────────────────────────────────────────
    FieldSpec("Segurança", "security.identity_required", "Exigir identidade", "bool",
              help="decisões passam a pedir principal autenticado"),
    FieldSpec("Segurança", "security.approval_min_role", "Papel p/ aprovar", "choice",
              ("viewer", "operator", "approver", "security_admin", "admin")),
    FieldSpec("Segurança", "security.allow_agent_approval", "Agente autoaprova", "bool",
              help="deixe desligado: agente não aprova o próprio trabalho"),
    FieldSpec("Segurança", "security.python_exec_enabled", "Executar python", "bool"),
    FieldSpec("Segurança", "security.allow_network_tools", "Ferramentas com rede", "bool"),
    FieldSpec("Segurança", "security.redact_secrets", "Redigir segredos", "bool"),
    FieldSpec("Segurança", "security.sanitize_external_payloads", "Higienizar externo", "bool"),
    # ── sandbox e ferramentas ────────────────────────────────────────────────
    FieldSpec("Sandbox", "tools.sandbox.mode", "Modo", "choice",
              ("auto", "container", "process"),
              "auto usa contêiner quando existe; process é só o processo local"),
    FieldSpec("Sandbox", "tools.sandbox.runtime", "Runtime", "choice", ("docker", "podman")),
    FieldSpec("Sandbox", "tools.sandbox.image", "Imagem", "str"),
    FieldSpec("Sandbox", "tools.sandbox.network", "Rede no contêiner", "bool"),
    FieldSpec("Sandbox", "tools.sandbox.timeout", "Timeout (s)", "int"),
    FieldSpec("Sandbox", "tools.sandbox.memory", "Memória", "str", help="ex.: 512m"),
    FieldSpec("Sandbox", "tools.sandbox.cpus", "CPUs", "str"),
    FieldSpec("Sandbox", "tools.sandbox.pids_limit", "Limite de PIDs", "int"),
    FieldSpec("Ferramentas", "tools.git.enabled", "Git", "bool"),
    FieldSpec("Ferramentas", "tools.email.enabled", "E-mail", "bool",
              help="envio sempre exige aprovação humana"),
    FieldSpec("Ferramentas", "tools.browser.enabled", "Navegador", "bool"),
    FieldSpec("Ferramentas", "mcp.enabled", "MCP", "bool",
              help="servidores externos entram como Tools, default deny"),
    # ── runtime ──────────────────────────────────────────────────────────────
    FieldSpec("Runtime", "runtime.max_steps", "Máx. de passos", "int"),
    FieldSpec("Runtime", "runtime.tool_timeout", "Timeout da ferramenta (s)", "int"),
    FieldSpec("Runtime", "runtime.model_timeout", "Timeout do modelo (s)", "int"),
    FieldSpec("Runtime", "runtime.auto_approve_in_development", "Autoaprovar em dev", "bool"),
    FieldSpec("Runtime", "runtime.pause_on_approval", "Pausar ao pedir aprovação", "bool"),
    # ── promoção e ciclo de vida ─────────────────────────────────────────────
    FieldSpec("Promoção", "release.min_approvals", "Aprovações mínimas", "int"),
    FieldSpec("Promoção", "release.min_approvals_production", "Aprovações p/ produção", "int"),
    FieldSpec("Promoção", "release.allow_self_approval", "Autoaprovação", "bool"),
    # ── coordenação, integrações, canais ─────────────────────────────────────
    FieldSpec("Coordenação", "coordination.enabled", "Coordenação", "bool"),
    FieldSpec("Coordenação", "coordination.strategy", "Estratégia", "str"),
    FieldSpec("Coordenação", "coordination.max_handoffs", "Máx. de repasses", "int"),
    FieldSpec("Integrações", "integrations.enabled", "Integrações", "bool"),
    FieldSpec("Integrações", "integrations.redact", "Redigir payloads", "bool"),
    FieldSpec("Integrações", "integrations.queue.enabled", "Fila persistente", "bool"),
    FieldSpec("Integrações", "integrations.queue.max_attempts", "Tentativas", "int"),
    FieldSpec("Integrações", "integrations.queue.backoff_seconds", "Backoff (s)", "int"),
    FieldSpec("Canais", "gateway.enabled", "Gateway", "bool",
              help="Telegram/Slack/Web falam com o mesmo Runtime"),
    FieldSpec("Canais", "gateway.require_pairing", "Exigir pareamento", "bool",
              help="default deny: sem parear, nada executa"),
    FieldSpec("Canais", "gateway.rate_limit_per_minute", "Limite por minuto", "int"),
    # ── ambiente e empresa ───────────────────────────────────────────────────
    FieldSpec("Ambiente", "environment", "Ambiente", "choice",
              ("development", "staging", "production")),
    FieldSpec("Ambiente", "enterprise.settings.external_ai", "IA externa", "choice",
              ("allowed", "blocked"),
              "blocked = nada cruza a fronteira da empresa"),
    FieldSpec("Ambiente", "enterprise.settings.data_residency", "Residência", "str"),
    FieldSpec("Ambiente", "enterprise.settings.audit_retention_days", "Retenção da trilha", "int"),
    FieldSpec("Logs", "logging.level", "Nível", "choice",
              ("DEBUG", "INFO", "WARNING", "ERROR")),
    FieldSpec("Logs", "logging.file", "Gravar em arquivo", "bool"),
)


def sections() -> list[str]:
    seen: list[str] = []
    for spec in FIELDS:
        if spec.section not in seen:
            seen.append(spec.section)
    return seen


def fields_of(section: str) -> list[FieldSpec]:
    return [spec for spec in FIELDS if spec.section == section]


def read_value(data: dict[str, Any], path: str, default: Any = None) -> Any:
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def write_value(data: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node = data
    for part in parts[:-1]:
        child = node.setdefault(part, {})
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def load_config_dict(workspace: Path) -> dict[str, Any]:
    return load_config(workspace).model_dump()


def save_config_dict(workspace: Path, data: dict[str, Any]) -> None:
    """Valida e grava o `egr.yaml`. Segredo nunca vem parar aqui como valor."""

    config = EGRConfig.model_validate(data)
    target = workspace / CONFIG_FILE
    header = ""
    if target.exists():
        text = target.read_text(encoding="utf-8")
        if text.startswith("# EGR"):
            header = text.split("version:", 1)[0]
    target.write_text(header + dump_config(config), encoding="utf-8")


def coerce(spec: FieldSpec, raw: Any) -> Any:
    """Converte o que veio do widget para o tipo do campo."""

    if spec.kind == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"1", "true", "sim", "on", "yes"}
    text = "" if raw is None else str(raw).strip()
    if spec.kind == "choice":
        if text not in spec.choices:
            raise ValueError(f"use um de: {', '.join(spec.choices)}")
        return text
    if text == "":
        return None
    if spec.kind == "int":
        return int(text)
    if spec.kind == "float":
        return float(text)
    return text


def display(spec: FieldSpec, value: Any) -> str:
    if value is None:
        return ""
    if spec.kind == "bool":
        return "sim" if value else "não"
    return str(value)


PROVIDER_TYPES = ("echo", "ollama", "openai_compat")


@dataclass(frozen=True)
class ProviderField:
    key: str
    label: str
    kind: str = "str"  # bool | int | float | str | choice
    choices: tuple[str, ...] = ()
    help: str = ""


PROVIDER_FIELDS: tuple[ProviderField, ...] = (
    ProviderField("name", "Nome", "str", help="um apelido seu: local, cloud, groq…"),
    ProviderField("type", "Tipo", "choice", PROVIDER_TYPES,
                  "echo = não chama nada; ollama = local; openai_compat = OpenAI, Groq, OpenRouter, vLLM…"),
    ProviderField("enabled", "Ativo", "bool"),
    ProviderField("model", "Modelo", "str", help="ex.: llama3.1, gpt-4o-mini"),
    ProviderField("base_url", "URL base", "str", help="ex.: http://localhost:11434"),
    ProviderField("api_key_env", "Chave (nome da variável)", "str",
                  help="só o NOME da variável de ambiente — nunca a chave em si"),
    ProviderField("external", "Cruza a fronteira", "bool",
                  help="marque para modelos fora da máquina/empresa"),
    ProviderField("priority", "Prioridade", "int", help="maior = preferido"),
    ProviderField("capabilities", "Capacidades", "str", help="separadas por vírgula"),
    ProviderField("pricing.input_per_1m", "Preço entrada (1M)", "float"),
    ProviderField("pricing.output_per_1m", "Preço saída (1M)", "float"),
)


def providers_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    return list(read_value(data, "models.providers", []) or [])


def set_providers(data: dict[str, Any], providers: list[dict[str, Any]]) -> None:
    write_value(data, "models.providers", providers)
