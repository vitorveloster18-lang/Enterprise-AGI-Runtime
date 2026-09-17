"""Transportes: REST, GraphQL e SQL.

Cada função devolve um dicionário com o resultado **já limitado** — corpo de
sistema alheio não é memória do Runtime. Nenhuma delas decide: quem decide é o
`ConnectorService`, antes de chamar.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any
from urllib.parse import urlparse

from ..core.errors import ConfigError

SQLITE_PREFIXES = ("sqlite:///", "sqlite:", "file:", "/", "./")


class Transport:
    """Cliente HTTP injetável (testes não precisam de internet)."""

    def __init__(self, client: Any = None):
        self._client = client

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: Any = None,
        json_body: Any = None,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        client = self._client
        if client is None:
            import httpx

            client = httpx
        started = time.perf_counter()
        generic = getattr(client, "request", None)
        caller = generic if callable(generic) else getattr(client, method.lower(), None)
        if not callable(caller):
            return {
                "ok": False,
                "status": 0,
                "error": f"cliente HTTP sem suporte a {method}",
                "latency_ms": _elapsed(started),
            }
        try:
            response = caller(
                *((method, url) if caller is generic else (url,)),
                headers=headers or {},
                content=to_content(body),
                json=json_body,
                timeout=timeout,
            )
        except Exception as exc:  # rede, DNS, TLS: falha medida, não exceção vazada
            return {
                "ok": False,
                "status": 0,
                "error": f"{type(exc).__name__}: {exc}",
                "latency_ms": _elapsed(started),
            }
        text = _text_of(response)
        return {
            "ok": 200 <= int(getattr(response, "status_code", 0) or 0) < 400,
            "status": int(getattr(response, "status_code", 0) or 0),
            "body": text,
            "latency_ms": _elapsed(started),
        }


def to_content(body: Any) -> Any:
    if body is None or isinstance(body, (bytes, str)):
        return body
    return json.dumps(body, ensure_ascii=False)


def _text_of(response: Any) -> str:
    text = getattr(response, "text", None)
    if text is None:
        try:
            return response.json()  # clientes de teste podem expor só json()
        except Exception:
            return ""
    return str(text)


def _elapsed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def http_call(
    transport: Transport,
    *,
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    body: Any = None,
    timeout: float = 20.0,
    max_chars: int = 4000,
) -> dict[str, Any]:
    result = transport.request(method, url, headers=headers, body=body, timeout=timeout)
    if "body" in result:
        result["truncated"] = len(result["body"]) > max_chars
        result["body"] = result["body"][:max_chars]
    return result


def graphql_call(
    transport: Transport,
    *,
    url: str,
    query: str,
    variables: dict | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
    max_chars: int = 4000,
) -> dict[str, Any]:
    result = transport.request(
        "POST",
        url,
        headers={"Content-Type": "application/json", **(headers or {})},
        json_body={"query": query, "variables": variables or {}},
        timeout=timeout,
    )
    if "body" in result:
        parsed: Any = None
        try:
            parsed = json.loads(result["body"])
        except (TypeError, ValueError):
            parsed = None
        result["json"] = parsed
        result["errors"] = (parsed or {}).get("errors") if isinstance(parsed, dict) else None
        if isinstance(result["errors"], list) and result["errors"]:
            result["ok"] = False
            mensagens = [
                str(item.get("message") or item) if isinstance(item, dict) else str(item)
                for item in result["errors"]
            ]
            result["error"] = "; ".join(mensagens)
        result["truncated"] = len(result["body"]) > max_chars
        result["body"] = result["body"][:max_chars]
    return result


def sql_call(
    *,
    dsn: str,
    statement: str,
    parameters: list | tuple | dict | None = None,
    max_chars: int = 4000,
    allowed_drivers: list[str] | None = None,
) -> dict[str, Any]:
    """SQL só com drivers declarados (padrão: sqlite). Nada de `import` vivo."""

    drivers = {item.lower() for item in (allowed_drivers or ["sqlite"])}
    started = time.perf_counter()
    path = _sqlite_path(dsn)
    if path is None:
        return {
            "ok": False,
            "status": 0,
            "error": f"driver não permitido ou desconhecido para '{dsn}' (permitidos: {', '.join(sorted(drivers))})",
            "latency_ms": _elapsed(started),
        }
    if "sqlite" not in drivers:
        return {
            "ok": False,
            "status": 0,
            "error": "sqlite não está entre os drivers permitidos (integrations.allow_sql_drivers)",
            "latency_ms": _elapsed(started),
        }

    try:
        connection = sqlite3.connect(path, timeout=10.0)
        connection.row_factory = sqlite3.Row
    except Exception as exc:
        return {"ok": False, "status": 0, "error": f"conexão falhou: {exc}", "latency_ms": _elapsed(started)}

    try:
        cursor = connection.execute(statement, parameters or [])
        rows = [dict(row) for row in cursor.fetchall()]
        connection.commit() if is_write(statement) else None
    except Exception as exc:
        return {"ok": False, "status": 0, "error": f"{type(exc).__name__}: {exc}", "latency_ms": _elapsed(started)}
    finally:
        connection.close()

    payload = json.dumps(rows, ensure_ascii=False, default=str)
    return {
        "ok": True,
        "status": 200,
        "rows": rows,
        "rowcount": len(rows),
        "body": payload[:max_chars],
        "truncated": len(payload) > max_chars,
        "latency_ms": _elapsed(started),
    }


def is_write(statement: str) -> bool:
    head = (statement or "").strip().lower()
    return not head.startswith(("select", "with", "pragma", "explain"))


def _sqlite_path(dsn: str) -> str | None:
    value = (dsn or "").strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme in ("", "file"):
        return value
    if parsed.scheme == "sqlite":
        return value.split("sqlite:///", 1)[-1].split("sqlite:", 1)[-1] or None
    return None


def host_of(url: str) -> str:
    parsed = urlparse(url or "")
    return parsed.hostname or ""


def assert_host_allowed(url: str, allowed: list[str]) -> None:
    """Lista branca de hosts: sem ela, o conector não sai da máquina."""

    host = host_of(url)
    if not host:
        raise ConfigError(f"URL sem host: {url!r}")
    allowed_hosts = {item.lower() for item in allowed if item}
    if host.lower() not in allowed_hosts:
        permitidos = ", ".join(sorted(allowed_hosts)) or "vazia"
        raise ConfigError(f"host '{host}' fora da lista branca do conector ({permitidos})")


__all__ = ["Transport", "assert_host_allowed", "graphql_call", "host_of", "http_call", "is_write", "sql_call"]
