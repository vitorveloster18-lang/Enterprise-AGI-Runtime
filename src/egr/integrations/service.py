"""ConnectorService: a única porta de saída (e de entrada) para sistemas externos.

Ordem, sempre:

    conector existe e está habilitado
    → host permitido · método permitido · leitura/escrita
    → Policy Engine (`integration.call`)
    → transporte (REST, GraphQL, SQL)
    → registro (latência, custo, decisão, ator) + trilha

Falha de política não é exceção escondida: é chamada registrada com `ok=False` e
motivo. O que o Runtime não autorizou, o Runtime não faz — e fica registrado que
alguém tentou.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import timedelta
from typing import Any
from urllib.parse import urljoin, urlparse

from ..core.errors import ConfigError
from ..core.ids import new_id
from ..core.timeutil import utcnow
from ..domain.enums import (
    Environment,
    EventType,
    IntegrationEventStatus,
    IntegrationKind,
    JobStatus,
    RiskLevel,
)
from ..domain.integration import (
    READ_OPERATIONS,
    InboundEvent,
    Integration,
    IntegrationCall,
    IntegrationJob,
)
from ..domain.tool import ToolRequest
from ..policies.engine import PolicyContext
from ..security.redaction import redact_text
from .loader import load_integration_dir
from .transports import Transport, assert_host_allowed, graphql_call, http_call, sql_call

CALL_ACTION = "integration.call"
RECEIVE_ACTION = "integration.receive"


class ConnectorService:
    """Conectores declarados, chamadas medidas, eventos idempotentes."""

    def __init__(self, runtime: Any):
        self.runtime = runtime
        self.transport = Transport()
        self.registry: dict[str, Integration] = {}

    # ---- registro -----------------------------------------------------
    @property
    def config(self):
        return self.runtime.settings.config.integrations

    def sync(self) -> list[Integration]:
        """`integrations/*.yaml` → banco → registro em memória."""

        declared = load_integration_dir(self.runtime.settings.workspace / "integrations")
        for integration in declared:
            self.runtime.integrations_repository.save(integration)
        return self.load()

    def load(self) -> list[Integration]:
        self.registry = {
            item.id: item for item in self.runtime.integrations_repository.list(limit=200)
        }
        return list(self.registry.values())

    def register(self, integration: Integration) -> Integration:
        integration.updated_at = utcnow()
        self.runtime.integrations_repository.save(integration)
        self.registry[integration.id] = integration
        return integration

    def get(self, integration_id: str) -> Integration:
        integration = self.registry.get(integration_id) or self.runtime.integrations_repository.get(integration_id)
        if integration is None:
            raise ConfigError(f"conector não encontrado: {integration_id}")
        self.registry.setdefault(integration.id, integration)
        return integration

    def list(self) -> list[Integration]:
        if not self.registry:
            self.load()
        return list(self.registry.values())

    # ---- chamadas -------------------------------------------------------
    def call(
        self,
        integration_id: str,
        *,
        method: str = "GET",
        path: str = "",
        body: Any = None,
        query: str = "",
        variables: dict | None = None,
        actor: str = "cli",
        task_id: str | None = None,
        environment: str | None = None,
        dry_run: bool = False,
        headers: dict[str, str] | None = None,
        # True quando o chamador já passou pelo Policy Engine (engine da task ou
        # `egr tool test --execute`): a decisão continua registrada, mas o conector
        # não pede aprovação duas vezes para o mesmo pedido.
        preauthorized: bool = False,
    ) -> IntegrationCall:
        """Chama um conector declarado — passando por política e registro."""

        integration = self.get(integration_id)
        method = (method or "GET").upper()
        # A política olha a OPERAÇÃO, não o verbo HTTP: um POST de GraphQL pode ser
        # só leitura (`query`), e um `select` de SQL não é menos leitura por isso.
        operation = self._operation(integration, method, query)
        write = operation not in READ_OPERATIONS
        payload = {
            "integration": integration.id,
            "kind": integration.kind,
            "method": operation,
            "operation": operation,
            "write": write,
            "read_only": integration.read_only,
            "external": integration.external,
        }

        call = IntegrationCall(
            id=new_id("call"),
            integration=integration.id,
            kind=integration.kind,
            method=operation,
            actor=actor,
            task_id=task_id,
        )

        try:
            self._precheck(integration, method, path, query)
        except ConfigError as exc:
            call.decision = "conector"
            return self._finish(call, integration, None, error=str(exc), denied=True)

        if preauthorized:
            call.decision = f"{CALL_ACTION} (autorizado no plano)"
            call.approved = True
        else:
            decision = self._authorize(
                integration, payload, environment=environment, risk=self._risk(integration, operation)
            )
            call.decision = decision["rule_id"] or "-"
            call.approved = decision["decision"] == "allow"
            if decision["decision"] == "deny":
                return self._finish(call, integration, None, error=decision["reason"], denied=True)
            if decision["decision"] == "require_approval":
                return self._finish(
                    call,
                    integration,
                    None,
                    error=f"aprovação necessária ({decision['required_role'] or 'operador'}): {decision['reason']}",
                    denied=True,
                )

        if dry_run:
            return self._finish(
                call,
                integration,
                {"ok": True, "dry_run": True, "url": self._url(integration, path)},
                status=0,
            )

        started = time.perf_counter()
        try:
            result = self._execute(integration, method, path, body, query, variables, headers)
        except Exception as exc:  # transporte nunca derruba o Runtime
            return self._finish(call, integration, None, error=f"{type(exc).__name__}: {exc}")
        result.setdefault("latency_ms", int((time.perf_counter() - started) * 1000))
        return self._finish(call, integration, result, status=result.get("status"))

    def test(self, integration_id: str, *, actor: str = "cli") -> IntegrationCall:
        """Teste sem efeito colateral: `GET /`, `select 1` ou introspecção."""

        integration = self.get(integration_id)
        if integration.kind == IntegrationKind.REST:
            return self.call(integration_id, method="GET", path="", actor=actor)
        if integration.kind == IntegrationKind.GRAPHQL:
            return self.call(integration_id, method="POST", query="{ __typename }", actor=actor)
        if integration.kind == IntegrationKind.SQL:
            return self.call(integration_id, method="GET", query="select 1 as ok", actor=actor)
        return self.call(integration_id, method="GET", actor=actor)

    # ---- fila de saída ----------------------------------------------------
    def enqueue(
        self,
        integration_id: str,
        *,
        method: str = "GET",
        path: str = "",
        query: str = "",
        body: Any = None,
        variables: dict | None = None,
        headers: dict[str, str] | None = None,
        idempotency: str | None = None,
        max_attempts: int | None = None,
        actor: str = "cli",
    ) -> IntegrationJob:
        """Promete uma chamada. Não tenta agora — quem tenta é o `drain`."""

        if not self.config.queue.enabled:
            raise ConfigError("fila de integrações desabilitada (integrations.queue.enabled)")
        self.get(integration_id)  # conector precisa existir e estar declarado
        job = IntegrationJob(
            id=new_id("job"),
            integration=integration_id,
            method=(method or "GET").upper(),
            path=path,
            query=query,
            body=body,
            variables=variables or {},
            headers=headers or {},
            max_attempts=max_attempts or self.config.queue.max_attempts,
            idempotency=idempotency,
            actor=actor,
        )
        saved = self.runtime.integration_jobs.save(job)
        self.runtime.audit.record(
            EventType.INTEGRATION_JOB_QUEUED,
            actor=actor,
            environment=self.runtime.settings.environment,
            payload={
                "job": saved.id,
                "conector": saved.integration,
                "método": saved.method,
                "idempotência": idempotency,
            },
        )
        return saved

    def drain(self, limit: int | None = None, *, actor: str = "runtime") -> list[dict[str, Any]]:
        """Tenta os jobs cuja espera venceu. Falha vira espera maior, não silêncio."""

        queue = self.config.queue
        pendentes = self.runtime.integration_jobs.due(utcnow(), limit or queue.batch)
        resultados: list[dict[str, Any]] = []
        for job in pendentes:
            job.status = JobStatus.RUNNING
            job.attempts += 1
            try:
                call = self.call(
                    job.integration,
                    method=job.method,
                    path=job.path,
                    query=job.query,
                    body=job.body,
                    variables=job.variables,
                    headers=job.headers,
                    actor=job.actor,
                )
            except Exception as exc:  # conector removido entre enqueue e drain
                call = None
                job.last_error = str(exc)
            if call is not None and call.ok:
                job.status = JobStatus.DONE
                job.call_id = call.id
                job.last_error = ""
                job.next_attempt = None
            else:
                motivo = (call.error if call is not None else job.last_error) or "falha sem motivo registrado"
                job.last_error = motivo[:400]
                if job.exhausted:
                    job.status = JobStatus.FAILED
                    job.next_attempt = None
                    self.runtime.audit.record(
                        EventType.INTEGRATION_JOB_FAILED,
                        actor=actor,
                        environment=self.runtime.settings.environment,
                        payload={"job": job.id, "conector": job.integration, "motivo": job.last_error},
                    )
                else:
                    job.status = JobStatus.PENDING
                    job.next_attempt = utcnow() + timedelta(
                        seconds=job.wait_seconds(
                            base=queue.backoff_seconds, cap=queue.max_backoff_seconds
                        )
                    )
            self.runtime.integration_jobs.update(job)
            resultados.append(job.summary())
        return resultados

    def cancel(self, job_id: str, *, actor: str = "human:cli") -> IntegrationJob:
        job = self.runtime.integration_jobs.get(job_id)
        if job is None:
            raise ConfigError(f"job não encontrado: {job_id}")
        if str(job.status) == JobStatus.DONE:
            raise ConfigError(f"job {job_id} já foi executado")
        job.status = JobStatus.CANCELLED
        job.next_attempt = None
        return self.runtime.integration_jobs.update(job)

    # ---- entrada (webhook) ---------------------------------------------
    def receive(
        self,
        integration_id: str,
        payload: dict,
        *,
        headers: dict[str, str] | None = None,
        signature: str | None = None,
    ) -> InboundEvent:
        """Evento de um sistema externo: assinatura, idempotência e trilha."""

        headers = {key.lower(): value for key, value in (headers or {}).items()}
        event = InboundEvent(
            id=new_id("event"),
            integration=integration_id,
            event_type=str(payload.get("type") or payload.get("event") or ""),
        )
        try:
            integration = self.get(integration_id)
        except ConfigError as exc:
            event.status = IntegrationEventStatus.REJECTED
            event.error = str(exc)
            return self._record_event(event, payload)

        inbound = integration.inbound
        if not integration.enabled or not inbound.enabled:
            event.status = IntegrationEventStatus.REJECTED
            event.error = "conector ou webhook de entrada desabilitado"
            return self._record_event(event, payload)

        external_id = headers.get(inbound.event_id_header.lower()) or payload.get("id") or payload.get("event_id")
        event.external_id = str(external_id) if external_id else None
        event.event_type = inbound.event_type or event.event_type or f"integration.{integration.id}"
        if event.external_id and self.runtime.integration_events.exists(integration.id, event.external_id):
            event.status = IntegrationEventStatus.DUPLICATE
            return self._record_event(event, payload)

        secret = self.runtime.resolve_secret(inbound.secret) if inbound.secret else ""
        if secret:
            event.signature_ok = self._verify(secret, headers, payload, signature, inbound)
            if not event.signature_ok:
                event.status = IntegrationEventStatus.REJECTED
                event.error = "assinatura inválida ou fora da janela"
                self._publish(EventType.INTEGRATION_EVENT_REJECTED, integration, event)
                return self._record_event(event, payload)
        else:
            event.signature_ok = False

        saved = self._record_event(event, payload)
        self._publish(EventType.INTEGRATION_EVENT_RECEIVED, integration, event)
        return saved

    def _publish(self, event_type, integration: Integration, event: InboundEvent) -> None:
        """Publica no barramento: a trilha é a fila, o gatilho é uma leitura dela."""

        self.runtime.events.publish(
            event_type,
            actor=f"integration:{integration.id}",
            environment=self.runtime.settings.environment,
            payload={
                "conector": integration.id,
                "evento": event.event_type,
                "id_externo": event.external_id,
                "evento_id": event.id,
                "assinatura": event.signature_ok,
                "motivo": event.error,
            },
        )

    @staticmethod
    def sign(secret: str, body: str, *, timestamp: int | None = None) -> str:
        stamp = timestamp if timestamp is not None else int(time.time())
        return f"t={stamp},v1={hmac.new(secret.encode(), f'{stamp}:{body}'.encode(), hashlib.sha256).hexdigest()}"

    def _verify(self, secret: str, headers: dict[str, str], payload: dict, signature: str | None, inbound) -> bool:
        raw = signature or headers.get(inbound.signature_header.lower(), "")
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
        parts = dict(item.split("=", 1) for item in raw.split(",") if "=" in item)
        stamp, provided = parts.get("t"), parts.get("v1")
        if not stamp or not provided:
            return False
        try:
            if abs(time.time() - int(stamp)) > inbound.tolerance_seconds:
                return False
        except (TypeError, ValueError):
            return False
        expected = hmac.new(secret.encode(), f"{stamp}:{body}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, provided)

    # ---- estado --------------------------------------------------------
    def status(self) -> dict[str, Any]:
        calls = self.runtime.integration_calls.list(limit=200)
        events = self.runtime.integration_events.list(limit=50)
        return {
            "habilitado": bool(self.config.enabled),
            "redação": bool(self.config.redact),
            "drivers_sql": list(self.config.allow_sql_drivers),
            "conectores": {
                "total": len(self.registry),
                "habilitados": sum(1 for item in self.registry.values() if item.enabled),
                "itens": [item.summary() for item in self.list()],
            },
            "chamadas": {
                "total": self.runtime.integration_calls.count(),
                "recentes": [call.summary() for call in calls[:5]],
            },
            "eventos": {
                "total": self.runtime.integration_events.count(),
                "recentes": [event.summary() for event in events[:5]],
            },
            "fila": {
                "habilitada": bool(self.config.queue.enabled),
                "tentativas": self.config.queue.max_attempts,
                "espera_base": f"{self.config.queue.backoff_seconds}s",
                "por_status": self.runtime.integration_jobs.stats(),
                "recentes": [job.summary() for job in self.runtime.integration_jobs.list(limit=5)],
            },
        }

    # ---- internos -------------------------------------------------------
    def _precheck(self, integration: Integration, method: str, path: str, query: str) -> None:
        if not self.config.enabled:
            raise ConfigError("integrações desabilitadas na configuração")
        if not integration.enabled:
            raise ConfigError(f"conector '{integration.id}' desabilitado")
        operation = self._operation(integration, method, query)
        if integration.read_only and operation not in READ_OPERATIONS:
            raise ConfigError(f"conector '{integration.id}' é somente leitura: {operation} recusado")
        if not integration.allows_method(method) and integration.kind != IntegrationKind.SQL:
            permitidos = ", ".join(integration.allowed_methods)
            raise ConfigError(f"método {method} fora da lista do conector '{integration.id}' ({permitidos})")
        if integration.kind == IntegrationKind.SQL:
            if not integration.dsn:
                raise ConfigError(f"conector '{integration.id}' sem dsn declarado")
            return
        url = self._url(integration, path)
        assert_host_allowed(url, integration.allowed_hosts)

    def _authorize(self, integration: Integration, payload: dict, *, environment: str | None, risk: RiskLevel) -> dict:
        action = CALL_ACTION
        request = ToolRequest(
            tool=f"integration.{integration.kind}",
            action=action,
            args={
                **payload,
                "host": urlparse(integration.base_url or "").hostname or "",
                "target": integration.target,
            },
            environment=Environment(environment or self.runtime.settings.environment),
        )
        context = PolicyContext(
            environment=Environment(environment or self.runtime.settings.environment),
            enterprise_settings=self.runtime.settings.enterprise.settings.model_dump(),
            risk=risk,
            security={"external": integration.external},
        )
        decision = self.runtime.policy.evaluate(request, context)
        return {
            "decision": str(decision.decision),
            "rule_id": decision.rule_id,
            "reason": decision.reason,
            "required_role": decision.required_role,
        }

    @staticmethod
    def _operation(integration: Integration, method: str, query: str) -> str:
        """Normaliza o que a política vai julgar: verbo REST, QUERY/MUTATION ou verbo SQL."""

        if integration.kind == IntegrationKind.GRAPHQL:
            head = " ".join((query or "").split()).lower()
            return "MUTATION" if head.startswith("mutation") else "QUERY"
        if integration.kind == IntegrationKind.SQL:
            verb = (query or "").strip().split(" ", 1)[0]
            return verb.upper() or "SELECT"
        return method

    @staticmethod
    def _risk(integration: Integration, method: str) -> RiskLevel:
        if integration.read_only or method in READ_OPERATIONS:
            return RiskLevel.LOW
        return RiskLevel.HIGH if integration.external else RiskLevel.MEDIUM

    def _execute(
        self,
        integration: Integration,
        method: str,
        path: str,
        body: Any,
        query: str,
        variables: dict | None,
        headers: dict[str, str] | None,
    ) -> dict[str, Any]:
        merged = {**integration.headers, **(headers or {})}
        secret = self.runtime.resolve_secret(integration.auth.secret) if integration.auth.secret else ""
        if secret and integration.auth.scheme != "none":
            if integration.auth.scheme == "bearer":
                merged["Authorization"] = f"Bearer {secret}"
            elif integration.auth.scheme == "header":
                prefix = f"{integration.auth.prefix} " if integration.auth.prefix else ""
                merged[integration.auth.header] = f"{prefix}{secret}"
            elif integration.auth.scheme == "basic":
                merged["Authorization"] = f"Basic {secret}"

        if integration.kind == IntegrationKind.SQL:
            return sql_call(
                dsn=integration.dsn,
                statement=query,
                max_chars=integration.max_response_chars or self.config.max_response_chars,
                allowed_drivers=self.config.allow_sql_drivers,
            )
        if integration.kind == IntegrationKind.GRAPHQL:
            return graphql_call(
                self.transport,
                url=self._url(integration, path),
                query=query,
                variables=variables,
                headers=merged,
                timeout=integration.timeout or self.config.default_timeout,
                max_chars=integration.max_response_chars or self.config.max_response_chars,
            )
        return http_call(
            self.transport,
            method=method,
            url=self._url(integration, path),
            headers=merged,
            body=body,
            timeout=integration.timeout or self.config.default_timeout,
            max_chars=integration.max_response_chars or self.config.max_response_chars,
        )

    @staticmethod
    def _url(integration: Integration, path: str) -> str:
        base = (integration.base_url or "").rstrip("/")
        if not path:
            return base
        if path.startswith("http"):
            return path
        return urljoin(f"{base}/", path.lstrip("/"))

    def _finish(
        self,
        call: IntegrationCall,
        integration: Integration,
        result: dict | None,
        *,
        status: int | None = None,
        error: str = "",
        denied: bool = False,
    ) -> IntegrationCall:
        call.ok = bool(result and result.get("ok") and not error) if result is not None else False
        call.status = status if status is not None else (result or {}).get("status")
        call.latency_ms = int((result or {}).get("latency_ms") or 0)
        call.cost = integration.cost_per_call if call.ok else 0.0
        call.error = error or (result or {}).get("error") or ""
        call.target = self._target(integration, call)
        call.request_summary = self._scrub(call.method + " " + call.target)
        if result is not None:
            body = result.get("body") or json.dumps(result.get("rows"), ensure_ascii=False, default=str) or ""
            call.response_summary = self._scrub(str(body)[:400])
        saved = self.runtime.integration_calls.save(call)
        self.runtime.audit.record(
            EventType.INTEGRATION_DENIED if denied else EventType.INTEGRATION_CALLED,
            actor=call.actor,
            environment=str(self.runtime.settings.environment),
            task_id=call.task_id,
            payload={
                "conector": call.integration,
                "método": call.method,
                "destino": call.target,
                "ok": call.ok,
                "latência_ms": call.latency_ms,
                "custo": call.cost,
                "decisão": call.decision,
                "erro": call.error[:200],
            },
        )
        return saved

    def _target(self, integration: Integration, call: IntegrationCall) -> str:
        if integration.kind == IntegrationKind.SQL:
            return integration.dsn
        host = urlparse(integration.base_url or "").hostname or integration.base_url
        return host or integration.target

    def _scrub(self, text: str) -> str:
        return redact_text(text) if self.config.redact else text

    def _record_event(self, event: InboundEvent, payload: dict) -> InboundEvent:
        resumo = json.dumps(payload, ensure_ascii=False)[:400]
        event.payload_summary = self._scrub(resumo)
        if event.status == IntegrationEventStatus.RECEIVED and self._has_trigger(event.event_type):
            event.status = IntegrationEventStatus.TRIGGERED
        return self.runtime.integration_events.save(event)

    def _has_trigger(self, event_type: str) -> bool:
        """Existe workflow declarado para este evento? Entrada vira trabalho só assim."""

        # import local: `egr.runtime` importa este módulo (ida e volta circular)
        from ..runtime.triggers import matches as matches_pattern

        for workflow in getattr(self.runtime, "workflows", {}).values():
            trigger = getattr(workflow, "trigger", None)
            if not trigger or getattr(trigger, "type", "") != "event" or not trigger.enabled:
                continue
            if matches_pattern(getattr(trigger, "event", None), event_type):
                return True
        return False


__all__ = ["CALL_ACTION", "RECEIVE_ACTION", "ConnectorService"]
