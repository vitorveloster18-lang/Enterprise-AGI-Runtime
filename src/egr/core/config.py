"""Configuration: egr.yaml + environment overrides.

Secrets are never stored in the config file: use `api_key_env` (name of an env var)
or the `${env:VAR}` placeholder.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from ..domain.enterprise import Enterprise
from ..domain.enums import Environment
from .errors import ConfigError

_ENV_PATTERN = re.compile(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")


def expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, list):
        return [expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: expand_env(item) for key, item in value.items()}
    return value


class PricingConfig(BaseModel):
    """Preço por 1M de tokens. Default 0.0 = provider gratuito/local."""

    currency: str = "USD"
    input_per_1m: float = 0.0
    output_per_1m: float = 0.0
    per_call: float = 0.0

    @property
    def input_per_token(self) -> float:
        return self.input_per_1m / 1_000_000

    @property
    def output_per_token(self) -> float:
        return self.output_per_1m / 1_000_000


class BudgetConfig(BaseModel):
    """Teto de gasto. Estourar o orçamento é uma decisão do Runtime, não do modelo."""

    currency: str = "USD"
    per_task: float | None = None
    per_day: float | None = None
    on_exceeded: Literal["deny", "warn"] = "deny"


class ProviderConfig(BaseModel):
    name: str
    type: str = "echo"  # echo | ollama | openai_compat
    enabled: bool = True
    model: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    capabilities: list[str] = Field(default_factory=lambda: ["reasoning", "chat"])
    priority: int = 0  # higher = preferred
    external: bool = False  # leaves the machine / the enterprise boundary
    pricing: PricingConfig = Field(default_factory=PricingConfig)
    options: dict[str, Any] = Field(default_factory=dict)

    def api_key(self) -> str | None:
        if not self.api_key_env:
            return None
        return os.environ.get(self.api_key_env)


class PathsConfig(BaseModel):
    database: str = ".egr/egr.db"
    sandbox: str = ".egr/sandbox"
    artifacts: str = "artifacts"
    logs: str = "logs"
    documents: str = "documents"


class SandboxConfig(BaseModel):
    """Fase 3: isolamento real de execução.

    auto      -> contêiner se houver runtime disponível, senão subprocesso
    container -> exige docker/podman (falha se indisponível)
    process   -> subprocesso local (isolamento fraco, só política + paths)
    """

    mode: Literal["auto", "container", "process"] = "auto"
    runtime: str = "docker"  # docker | podman
    image: str = "python:3.11-alpine"
    network: bool = False
    memory: str = "512m"
    cpus: str = "1"
    pids_limit: int = 128
    tmpfs_size: str = "64m"
    read_only_workspace: bool = True
    timeout: int = 30


class GitToolConfig(BaseModel):
    enabled: bool = True
    binary: str = "git"


class EmailToolConfig(BaseModel):
    enabled: bool = False
    smtp_host: str | None = None
    smtp_port: int = 587
    use_tls: bool = True
    username_env: str | None = None
    password_env: str | None = None
    imap_host: str | None = None
    imap_port: int = 993
    from_address: str | None = None
    cost_per_send: float = 0.0


class BrowserToolConfig(BaseModel):
    enabled: bool = False
    headless: bool = True
    timeout: int = 30
    allowed_domains: list[str] = Field(default_factory=list)


class MCPServerConfig(BaseModel):
    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    timeout: int = 15


class MCPConfig(BaseModel):
    """Model Context Protocol: servidores externos entram como Tools."""

    enabled: bool = True
    servers: list[MCPServerConfig] = Field(default_factory=list)


class ToolsConfig(BaseModel):
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    git: GitToolConfig = Field(default_factory=GitToolConfig)
    email: EmailToolConfig = Field(default_factory=EmailToolConfig)
    browser: BrowserToolConfig = Field(default_factory=BrowserToolConfig)


class ChannelConfig(BaseModel):
    """Fase 10: um canal de mensagens é uma interface, nunca um atalho de governo."""

    name: str
    type: str = "console"          # console | telegram | slack | web
    enabled: bool = False
    #: agente usado quando a mensagem não diz qual
    default_agent: str = ""
    #: ambiente das tasks criadas por este canal (vazio = ambiente do workspace)
    environment: str = ""
    #: lista branca de remetentes (vazia = qualquer pareado)
    allowed_chat_ids: list[str] = Field(default_factory=list)
    #: permite decidir aprovações por este canal (decisão é ato humano consciente)
    allow_decisions: bool = False
    # telegram
    bot_token_env: str = "EGR_TELEGRAM_TOKEN"
    polling_timeout: int = 25
    # slack
    signing_secret_env: str = "EGR_SLACK_SIGNING_SECRET"
    #: segredo opcional conferido no cabeçalho X-Telegram-Bot-Api-Secret-Token
    webhook_secret_env: str = ""


class GatewayConfig(BaseModel):
    """Fase 10: Remote Control — o Runtime atrás de Telegram/Slack/Web."""

    enabled: bool = False
    #: default deny: sem pareamento aprovado por um humano, o canal não fala
    require_pairing: bool = True
    #: papéis de um remetente recém-pareado (o operador pode elevar depois)
    default_roles: list[str] = Field(default_factory=lambda: ["viewer"])
    max_message_chars: int = 4000
    max_reply_chars: int = 3500
    rate_limit_per_minute: int = 10
    redact: bool = True
    channels: list[ChannelConfig] = Field(default_factory=list)


class IntegrationsConfig(BaseModel):
    """Fase 11: conectores declarados em `integrations/*.yaml`."""

    enabled: bool = True
    #: teto de caracteres da resposta guardada na trilha (o corpo não é cofre)
    max_response_chars: int = 4000
    #: tempo limite padrão de uma chamada externa (segundos)
    default_timeout: int = 20
    #: redigir segredos antes de registrar pedido e resposta
    redact: bool = True
    #: permitir drivers SQL além do sqlite (declarado, nunca implícito)
    allow_sql_drivers: list[str] = Field(default_factory=lambda: ["sqlite"])


class SecurityConfig(BaseModel):
    python_exec_enabled: bool = True
    allow_network_tools: bool = True
    redact_secrets: bool = True
    sanitize_external_payloads: bool = True
    allowed_write_roots: list[str] = Field(default_factory=lambda: ["artifacts", ".egr/sandbox"])

    # Fase 4 — identidade verificável, RBAC, cofre e chaves
    #: quando True, decisões críticas exigem um Principal autenticado
    identity_required: bool = False
    #: papel mínimo exigido de quem decide uma aprovação sem regra explícita
    approval_min_role: str = "approver"
    #: agentes nunca aprovam o próprio trabalho (spec: humano aprova o crítico)
    allow_agent_approval: bool = False


class MemoryConfig(BaseModel):
    """Fase 5 — memória: como recuperar, o que reforçar e o que esquecer."""

    # hybrid | fts | semantic
    retrieval: Literal["hybrid", "fts", "semantic"] = "hybrid"
    #: pesos da fusão RRF (0 desliga um dos lados)
    fts_weight: float = 1.0
    semantic_weight: float = 1.0
    #: candidatos por lado = limit * multiplicador
    candidate_multiplier: int = 4
    #: similaridade mínima para um candidato semântico entrar na fusão
    min_cosine: float = 0.12
    #: acima deste nº de registros o lado semântico deixa de varrer o acervo todo
    max_semantic_scan: int = 20000
    #: cosseno a partir do qual dois registros são a mesma memória
    duplicate_threshold: float = 0.90
    #: teto de registros comparados por consolidação (a varredura é O(n²))
    max_consolidate_scan: int = 2000
    #: meia-vida (dias) do decaimento da saliência
    half_life_days: int = 30
    #: dias que um registro arquivado espera antes de poder ser podado
    retention_days: int = 365
    auto_remember: bool = True  # tasks concluídas viram memória episódica
    auto_recall: bool = True  # planejamento consulta a memória
    max_context_chars: int = 2000


class RuntimeConfig(BaseModel):
    max_steps: int = 8
    tool_timeout: int = 30
    model_timeout: int = 120
    auto_approve_in_development: bool = False
    pause_on_approval: bool = True


class ModelsConfig(BaseModel):
    # priority    -> respeita a ordem configurada (padrão)
    # cost        -> provedor mais barato primeiro (dentro da capacidade)
    # local_first -> provedores locais primeiro, externos só se necessário
    routing: Literal["priority", "cost", "local_first"] = "priority"
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    providers: list[ProviderConfig] = Field(
        default_factory=lambda: [ProviderConfig(name="echo", type="echo", capabilities=["reasoning", "chat"])]
    )


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: bool = True


class EGRConfig(BaseModel):
    version: int = 1
    enterprise: Enterprise = Field(default_factory=Enterprise)
    environment: Environment = Environment.DEVELOPMENT
    paths: PathsConfig = Field(default_factory=PathsConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    integrations: IntegrationsConfig = Field(default_factory=IntegrationsConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


class Settings(BaseModel):
    """Resolved configuration bound to a workspace directory."""

    workspace: Path
    config: EGRConfig = Field(default_factory=EGRConfig)

    model_config = {"arbitrary_types_allowed": True}

    # ---- paths -------------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.workspace / self.config.paths.database

    @property
    def sandbox_path(self) -> Path:
        return self.workspace / self.config.paths.sandbox

    @property
    def artifacts_path(self) -> Path:
        return self.workspace / self.config.paths.artifacts

    @property
    def logs_path(self) -> Path:
        return self.workspace / self.config.paths.logs

    @property
    def documents_path(self) -> Path:
        return self.workspace / self.config.paths.documents

    @property
    def environment(self) -> Environment:
        return self.config.environment

    @property
    def enterprise(self) -> Enterprise:
        return self.config.enterprise

    def ensure_dirs(self) -> None:
        for path in (
            self.db_path.parent,
            self.sandbox_path,
            self.artifacts_path,
            self.logs_path,
            self.documents_path,
        ):
            path.mkdir(parents=True, exist_ok=True)


def default_config() -> EGRConfig:
    return EGRConfig()


def load_config(workspace: Path) -> EGRConfig:
    config_file = workspace / "egr.yaml"
    if not config_file.exists():
        return EGRConfig()
    try:
        raw = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_file}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{config_file} must contain a mapping")
    raw = expand_env(raw)
    return EGRConfig.model_validate(raw)


def load_settings(workspace: Path | None = None, environment: str | None = None) -> Settings:
    workspace = Path(workspace).resolve() if workspace else Path(os.environ["EGR_WORKSPACE"]).resolve() \
        if os.environ.get("EGR_WORKSPACE") else Path.cwd().resolve()
    config = load_config(workspace)
    env_override = environment or os.environ.get("EGR_ENVIRONMENT")
    if env_override:
        try:
            config.environment = Environment(env_override.lower())
        except ValueError as exc:
            raise ConfigError(f"invalid environment '{env_override}'") from exc
    if os.environ.get("EGR_LOG_LEVEL"):
        config.logging.level = os.environ["EGR_LOG_LEVEL"].upper()
    return Settings(workspace=workspace, config=config)


def dump_config(config: EGRConfig) -> str:
    data = config.model_dump(mode="json", exclude_none=True)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)


__all__ = [
    "BudgetConfig",
    "EGRConfig",
    "Literal",
    "LoggingConfig",
    "MCPConfig",
    "MCPServerConfig",
    "MemoryConfig",
    "ModelsConfig",
    "PathsConfig",
    "PricingConfig",
    "ProviderConfig",
    "RuntimeConfig",
    "SandboxConfig",
    "SecurityConfig",
    "Settings",
    "ToolsConfig",
    "default_config",
    "dump_config",
    "expand_env",
    "load_config",
    "load_settings",
]
