"""Fase 11 — Enterprise Integrations.

    conector declarado (YAML) → política → chamada medida → trilha

REST, GraphQL, SQL e webhooks de entrada entram pela mesma porta: o conector é
declarado com host, métodos e leitura/escrita; a credencial vive no cofre; a
chamada passa pelo Policy Engine; e tudo fica registrado (latência, custo,
decisão e ator).
"""

from .loader import load_integration_dir
from .service import ConnectorService
from .transports import graphql_call, http_call, sql_call

__all__ = ["ConnectorService", "graphql_call", "http_call", "load_integration_dir", "sql_call"]
