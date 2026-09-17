"""Persistence for every domain object.

Repositories are the single place that knows about SQL. Swapping SQLite for
PostgreSQL later means reimplementing this module only.
"""

from __future__ import annotations

import json
from typing import Any

from ..core.timeutil import iso, utcnow
from ..domain.agent import AgentSpec
from ..domain.approval import Approval
from ..domain.artifact import Artifact
from ..domain.enterprise import Enterprise
from ..domain.evaluation import EvaluationRun, EvaluationSuite
from ..domain.memory import MemoryRecord
from ..domain.policy import Policy
from ..domain.proposal import ChangeProposal
from ..domain.run import WorkflowRun
from ..domain.task import Task
from ..security.identity import IdentityToken, Principal
from ..security.vault import SecretRecord
from .database import Database


def _dump(model: Any) -> str:
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False, default=str)


def _load(row, model):
    return model.model_validate(json.loads(row["data"]))


class EnterpriseRepository:
    def __init__(self, db: Database):
        self.db = db

    def save(self, enterprise: Enterprise) -> Enterprise:
        self.db.execute(
            "INSERT INTO enterprises (id, name, data, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name = excluded.name, data = excluded.data",
            (enterprise.id, enterprise.name, _dump(enterprise), iso(enterprise.created_at)),
        )
        self.db.commit()
        return enterprise

    def get(self, enterprise_id: str) -> Enterprise | None:
        row = self.db.query_one("SELECT * FROM enterprises WHERE id = ?", (enterprise_id,))
        return _load(row, Enterprise) if row else None

    def first(self) -> Enterprise | None:
        row = self.db.query_one("SELECT * FROM enterprises ORDER BY created_at LIMIT 1")
        return _load(row, Enterprise) if row else None


class AgentRepository:
    def __init__(self, db: Database):
        self.db = db

    def save(self, agent: AgentSpec) -> AgentSpec:
        agent.updated_at = utcnow()
        self.db.execute(
            "INSERT INTO agents (id, version, environment, data, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET version = excluded.version, environment = excluded.environment, "
            "data = excluded.data, updated_at = excluded.updated_at",
            (
                agent.id,
                agent.version,
                str(agent.environment),
                _dump(agent),
                iso(agent.updated_at),
            ),
        )
        self.db.commit()
        return agent

    def get(self, agent_id: str) -> AgentSpec | None:
        row = self.db.query_one("SELECT * FROM agents WHERE id = ?", (agent_id,))
        return _load(row, AgentSpec) if row else None

    def list(self, environment: str | None = None) -> list[AgentSpec]:
        if environment:
            rows = self.db.query(
                "SELECT * FROM agents WHERE environment = ? ORDER BY updated_at DESC", (environment,)
            )
        else:
            rows = self.db.query("SELECT * FROM agents ORDER BY updated_at DESC")
        return [_load(row, AgentSpec) for row in rows]

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM agents") or 0)


class TaskRepository:
    def __init__(self, db: Database):
        self.db = db

    def save(self, task: Task) -> Task:
        task.updated_at = utcnow()
        self.db.execute(
            "INSERT INTO tasks (id, status, environment, agent_id, objective, data, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET status = excluded.status, "
            "environment = excluded.environment, agent_id = excluded.agent_id, objective = excluded.objective, "
            "data = excluded.data, updated_at = excluded.updated_at",
            (
                task.id,
                str(task.status),
                str(task.environment),
                task.agent_id,
                task.objective,
                _dump(task),
                iso(task.created_at),
                iso(task.updated_at),
            ),
        )
        self.db.commit()
        return task

    def get(self, task_id: str) -> Task | None:
        row = self.db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return _load(row, Task) if row else None

    def list(
        self,
        status: str | None = None,
        environment: str | None = None,
        limit: int = 50,
    ) -> list[Task]:
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if environment:
            clauses.append("environment = ?")
            params.append(environment)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query(
            f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT ?", (*params, limit)
        )
        return [_load(row, Task) for row in rows]

    def count_by_status(self) -> dict[str, int]:
        rows = self.db.query("SELECT status, COUNT(*) AS total FROM tasks GROUP BY status")
        return {row["status"]: row["total"] for row in rows}


class PolicyRepository:
    def __init__(self, db: Database):
        self.db = db

    def save(self, policy: Policy) -> Policy:
        self.db.execute(
            "INSERT INTO policies (id, priority, enabled, data) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET priority = excluded.priority, enabled = excluded.enabled, "
            "data = excluded.data",
            (policy.id, policy.priority, 1 if policy.enabled else 0, _dump(policy)),
        )
        self.db.commit()
        return policy

    def list(self, enabled_only: bool = True) -> list[Policy]:
        sql = "SELECT * FROM policies"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY priority DESC, id"
        return [_load(row, Policy) for row in self.db.query(sql)]

    def get(self, policy_id: str) -> Policy | None:
        row = self.db.query_one("SELECT * FROM policies WHERE id = ?", (policy_id,))
        return _load(row, Policy) if row else None

    def delete(self, policy_id: str) -> None:
        self.db.execute("DELETE FROM policies WHERE id = ?", (policy_id,))
        self.db.commit()


class ApprovalRepository:
    def __init__(self, db: Database):
        self.db = db

    def save(self, approval: Approval) -> Approval:
        approval.updated_at = utcnow()
        self.db.execute(
            "INSERT INTO approvals (id, status, task_id, environment, data, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET status = excluded.status, "
            "data = excluded.data, updated_at = excluded.updated_at",
            (
                approval.id,
                str(approval.status),
                approval.task_id,
                str(approval.environment),
                _dump(approval),
                iso(approval.created_at),
                iso(approval.updated_at),
            ),
        )
        self.db.commit()
        return approval

    def get(self, approval_id: str) -> Approval | None:
        row = self.db.query_one("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        return _load(row, Approval) if row else None

    def list(self, status: str | None = None, limit: int = 50) -> list[Approval]:
        if status:
            rows = self.db.query(
                "SELECT * FROM approvals WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            rows = self.db.query("SELECT * FROM approvals ORDER BY created_at DESC LIMIT ?", (limit,))
        return [_load(row, Approval) for row in rows]

    def pending_for_task(self, task_id: str) -> list[Approval]:
        rows = self.db.query(
            "SELECT * FROM approvals WHERE task_id = ? AND status = 'pending' ORDER BY created_at",
            (task_id,),
        )
        return [_load(row, Approval) for row in rows]


class ArtifactRepository:
    def __init__(self, db: Database):
        self.db = db

    def save(self, artifact: Artifact) -> Artifact:
        self.db.execute(
            "INSERT INTO artifacts (id, kind, task_id, environment, data, created_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET data = excluded.data",
            (
                artifact.id,
                str(artifact.kind),
                artifact.task_id,
                str(artifact.environment),
                _dump(artifact),
                iso(artifact.created_at),
            ),
        )
        self.db.commit()
        return artifact

    def list(self, task_id: str | None = None, limit: int = 50) -> list[Artifact]:
        if task_id:
            rows = self.db.query(
                "SELECT * FROM artifacts WHERE task_id = ? ORDER BY created_at DESC LIMIT ?", (task_id, limit)
            )
        else:
            rows = self.db.query("SELECT * FROM artifacts ORDER BY created_at DESC LIMIT ?", (limit,))
        return [_load(row, Artifact) for row in rows]

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM artifacts") or 0)


class MemoryRepository:
    """Knowledge / Operational / Episodic / Semantic records with full-text search."""

    def __init__(self, db: Database):
        self.db = db

    def save(self, record: MemoryRecord) -> MemoryRecord:
        self.db.execute(
            "INSERT INTO memory_records (id, namespace, kind, data, created_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET data = excluded.data",
            (record.id, record.namespace, str(record.kind), _dump(record), iso(record.created_at)),
        )
        # FTS5 não tem PK: reindexar sem remover a linha antiga duplicaria o
        # registro na busca (e o reforço de saliência reescreve o tempo todo).
        self.db.execute("DELETE FROM memory_fts WHERE id = ?", (record.id,))
        self.db.execute(
            "INSERT INTO memory_fts (id, namespace, content) VALUES (?, ?, ?)",
            (record.id, record.namespace, f"{record.summary}\n{record.content}"),
        )
        self.db.commit()
        return record

    def get(self, record_id: str) -> MemoryRecord | None:
        row = self.db.query_one("SELECT * FROM memory_records WHERE id = ?", (record_id,))
        return _load(row, MemoryRecord) if row else None

    def search(
        self,
        query: str,
        namespaces: list[str] | None = None,
        kinds: list[str] | None = None,
        limit: int = 5,
        include_archived: bool = False,
    ) -> list[MemoryRecord]:
        namespace_filter = namespaces or []
        kind_filter = kinds or []

        if query.strip():
            try:
                rows = self.db.query(
                    "SELECT m.id, m.data, bm25(memory_fts) AS score "
                    "FROM memory_fts f JOIN memory_records m ON m.id = f.id "
                    "WHERE memory_fts MATCH ? ORDER BY score LIMIT ?",
                    (self._fts_query(query), limit * 3),
                )
            except Exception:
                rows = self.db.query(
                    "SELECT id, data, 0.0 AS score FROM memory_records "
                    "WHERE data LIKE ? ORDER BY created_at DESC LIMIT ?",
                    (f"%{query}%", limit * 3),
                )
        else:
            rows = self.db.query(
                "SELECT id, data, 0.0 AS score FROM memory_records ORDER BY created_at DESC LIMIT ?",
                (limit * 3,),
            )

        results: list[MemoryRecord] = []
        for row in rows:
            record = MemoryRecord.model_validate(json.loads(row["data"]))
            record.score = float(row["score"]) if row["score"] is not None else None
            if namespace_filter and record.namespace not in namespace_filter:
                continue
            if kind_filter and str(record.kind) not in kind_filter:
                continue
            if record.archived and not include_archived:
                continue
            results.append(record)
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _fts_query(query: str) -> str:
        tokens = [token.strip('"').replace('"', "") for token in query.split() if token.strip()]
        if not tokens:
            return '""'
        return " OR ".join(f'"{token}"' for token in tokens)

    def list(
        self,
        namespace: str | None = None,
        limit: int = 20,
        include_archived: bool = False,
        kind: str | None = None,
    ) -> list[MemoryRecord]:
        clauses, params = [], []
        if namespace:
            clauses.append("namespace = ?")
            params.append(namespace)
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if not include_archived:
            clauses.append("(data NOT LIKE '%\"archived\":true%')")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query(
            f"SELECT * FROM memory_records {where} ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        )
        return [_load(row, MemoryRecord) for row in rows]

    def all_records(self, include_archived: bool = True) -> list[MemoryRecord]:
        """Cursor completo do acervo (para reindexar e consolidar)."""

        rows = self.db.query("SELECT * FROM memory_records ORDER BY created_at")
        records = [_load(row, MemoryRecord) for row in rows]
        if include_archived:
            return records
        return [record for record in records if not record.archived]

    def stats(self) -> dict[str, int]:
        rows = self.db.query("SELECT kind, COUNT(*) AS total FROM memory_records GROUP BY kind")
        return {row["kind"]: row["total"] for row in rows}

    def namespace_stats(self) -> dict[str, int]:
        rows = self.db.query(
            "SELECT namespace, COUNT(*) AS total FROM memory_records GROUP BY namespace ORDER BY total DESC"
        )
        return {row["namespace"]: row["total"] for row in rows}

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM memory_records") or 0)

    def delete(self, record_id: str) -> bool:
        self.db.execute("DELETE FROM memory_fts WHERE id = ?", (record_id,))
        self.db.execute("DELETE FROM memory_vectors WHERE id = ?", (record_id,))
        cursor = self.db.execute("DELETE FROM memory_records WHERE id = ?", (record_id,))
        self.db.commit()
        return bool(cursor.rowcount)

    # ---- vetores semânticos (Fase 5) ---------------------------------
    def save_vector(self, record_id: str, model: str, dim: int, vector: list[float]) -> None:
        from ..memory.embeddings import to_bytes

        self.db.execute(
            "INSERT INTO memory_vectors (id, model, dim, data, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET model = excluded.model, dim = excluded.dim, "
            "data = excluded.data, updated_at = excluded.updated_at",
            (record_id, model, dim, to_bytes(vector), iso()),
        )
        self.db.commit()

    def get_vector(self, record_id: str) -> tuple[str, list[float]] | None:
        from ..memory.embeddings import from_bytes

        row = self.db.query_one("SELECT model, data FROM memory_vectors WHERE id = ?", (record_id,))
        if row is None:
            return None
        return row["model"], from_bytes(row["data"])

    def vectors(self, model: str | None = None) -> dict[str, list[float]]:
        """id -> vetor. `model` filtra vetores gerados pelo algoritmo atual."""

        from ..memory.embeddings import from_bytes

        if model:
            rows = self.db.query("SELECT id, data FROM memory_vectors WHERE model = ?", (model,))
        else:
            rows = self.db.query("SELECT id, data FROM memory_vectors")
        return {row["id"]: from_bytes(row["data"]) for row in rows}

    def vector_ids(self, model: str | None = None) -> set[str]:
        if model:
            rows = self.db.query("SELECT id FROM memory_vectors WHERE model = ?", (model,))
        else:
            rows = self.db.query("SELECT id FROM memory_vectors")
        return {row["id"] for row in rows}

    def count_vectors(self, model: str | None = None) -> int:
        if model:
            return int(self.db.scalar("SELECT COUNT(*) FROM memory_vectors WHERE model = ?", (model,)) or 0)
        return int(self.db.scalar("SELECT COUNT(*) FROM memory_vectors") or 0)

    def delete_vector(self, record_id: str) -> None:
        self.db.execute("DELETE FROM memory_vectors WHERE id = ?", (record_id,))
        self.db.commit()


class ModelUsageRepository:
    """Telemetria de modelos: custo, latência e tokens por task/agente/provider."""

    def __init__(self, db: Database):
        self.db = db

    def record(self, call: dict) -> None:
        self.db.execute(
            "INSERT INTO model_calls (id, task_id, agent_id, provider, model, capability, external, "
            "latency_ms, input_tokens, output_tokens, cost, status, error, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                call["id"],
                call.get("task_id"),
                call.get("agent_id"),
                call.get("provider"),
                call.get("model"),
                call.get("capability"),
                1 if call.get("external") else 0,
                int(call.get("latency_ms", 0) or 0),
                int(call.get("input_tokens", 0) or 0),
                int(call.get("output_tokens", 0) or 0),
                float(call.get("cost", 0.0) or 0.0),
                call.get("status", "ok"),
                call.get("error"),
                call.get("created_at") or iso(),
            ),
        )
        self.db.commit()

    def _aggregates(self, where: str = "", params: tuple = ()) -> dict:
        row = self.db.query_one(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(cost), 0) AS total_cost, "
            "COALESCE(SUM(input_tokens), 0) AS input_tokens, "
            "COALESCE(SUM(output_tokens), 0) AS output_tokens, "
            "COALESCE(AVG(latency_ms), 0) AS avg_latency "
            f"FROM model_calls {where}",
            params,
        )
        by_provider = self.db.query(
            "SELECT provider, COUNT(*) AS calls, COALESCE(SUM(cost), 0) AS cost, "
            "COALESCE(SUM(input_tokens), 0) AS input_tokens, "
            "COALESCE(SUM(output_tokens), 0) AS output_tokens, "
            "COALESCE(AVG(latency_ms), 0) AS avg_latency "
            f"FROM model_calls {where} GROUP BY provider ORDER BY cost DESC",
            params,
        )
        by_model = self.db.query(
            "SELECT COALESCE(model, '-') AS model, provider, COUNT(*) AS calls, "
            "COALESCE(SUM(cost), 0) AS cost "
            f"FROM model_calls {where} GROUP BY model, provider ORDER BY cost DESC",
            params,
        )
        return {
            "calls": int(row["calls"] or 0),
            "total_cost": round(float(row["total_cost"] or 0.0), 8),
            "input_tokens": int(row["input_tokens"] or 0),
            "output_tokens": int(row["output_tokens"] or 0),
            "avg_latency_ms": int(row["avg_latency"] or 0),
            "by_provider": [dict(item) for item in by_provider],
            "by_model": [dict(item) for item in by_model],
        }

    def totals(self, task_id: str | None = None, since: str | None = None) -> dict:
        clauses, params = [], []
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if since:
            clauses.append("created_at >= ?")
            params.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self._aggregates(where, tuple(params))

    def totals_today(self) -> dict:
        return self.totals(since=utcnow().strftime("%Y-%m-%d"))

    def recent(self, limit: int = 20) -> list[dict]:
        rows = self.db.query("SELECT * FROM model_calls ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(row) for row in rows]


class SettingsRepository:
    def __init__(self, db: Database):
        self.db = db

    def set(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False, default=str)),
        )
        self.db.commit()

    def get(self, key: str, default: Any = None) -> Any:
        row = self.db.query_one("SELECT value FROM settings WHERE key = ?", (key,))
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return row["value"]


class IdentityRepository:
    """Principais + tokens. Tokens guardam apenas o hash do segredo."""

    def __init__(self, db: Database):
        self.db = db

    # ---- principals ---------------------------------------------------
    def save_principal(self, principal: Principal) -> Principal:
        principal.updated_at = utcnow()
        self.db.execute(
            "INSERT INTO principals (id, kind, status, data, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET kind = excluded.kind, "
            "status = excluded.status, data = excluded.data, updated_at = excluded.updated_at",
            (
                principal.id,
                str(principal.kind),
                str(principal.status),
                _dump(principal),
                iso(principal.created_at),
                iso(principal.updated_at),
            ),
        )
        self.db.commit()
        return principal

    def get_principal(self, principal_id: str) -> Principal | None:
        row = self.db.query_one("SELECT * FROM principals WHERE id = ?", (principal_id,))
        return _load(row, Principal) if row else None

    def list_principals(self, kind: str | None = None, status: str | None = None) -> list[Principal]:
        clauses, params = [], []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query(
            f"SELECT * FROM principals {where} ORDER BY created_at DESC",
            tuple(params),
        )
        return [_load(row, Principal) for row in rows]

    def count_principals(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM principals") or 0)

    def delete_principal(self, principal_id: str) -> bool:
        self.db.execute("DELETE FROM identity_tokens WHERE principal_id = ?", (principal_id,))
        cursor = self.db.execute("DELETE FROM principals WHERE id = ?", (principal_id,))
        self.db.commit()
        return bool(cursor.rowcount)

    # ---- tokens -------------------------------------------------------
    def save_token(self, token: IdentityToken) -> IdentityToken:
        self.db.execute(
            "INSERT INTO identity_tokens (id, principal_id, status, data, created_at, expires_at, "
            "revoked_at, last_used_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "status = excluded.status, data = excluded.data, revoked_at = excluded.revoked_at, "
            "last_used_at = excluded.last_used_at",
            (
                token.id,
                token.principal_id,
                token.status,
                _dump(token),
                iso(token.created_at),
                iso(token.expires_at) if token.expires_at else None,
                iso(token.revoked_at) if token.revoked_at else None,
                iso(token.last_used_at) if token.last_used_at else None,
            ),
        )
        self.db.commit()
        return token

    def get_token(self, token_id: str) -> IdentityToken | None:
        row = self.db.query_one("SELECT * FROM identity_tokens WHERE id = ?", (token_id,))
        return _load(row, IdentityToken) if row else None

    def list_tokens(self, principal_id: str | None = None) -> list[IdentityToken]:
        if principal_id:
            rows = self.db.query(
                "SELECT * FROM identity_tokens WHERE principal_id = ? ORDER BY created_at DESC",
                (principal_id,),
            )
        else:
            rows = self.db.query("SELECT * FROM identity_tokens ORDER BY created_at DESC")
        return [_load(row, IdentityToken) for row in rows]

    def count_tokens(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM identity_tokens WHERE status = 'active'") or 0)


class SecretRepository:
    """Envelopes cifrados. Nenhum método devolve o valor em claro."""

    def __init__(self, db: Database):
        self.db = db

    def save(self, record: SecretRecord) -> SecretRecord:
        self.db.execute(
            "INSERT INTO secrets (id, name, provider, key_id, data, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET provider = excluded.provider, "
            "key_id = excluded.key_id, data = excluded.data, updated_at = excluded.updated_at",
            (
                record.id,
                record.name,
                record.provider,
                record.key_id,
                _dump(record),
                iso(record.created_at),
                iso(record.updated_at),
            ),
        )
        self.db.commit()
        return record

    def get(self, name: str) -> SecretRecord | None:
        row = self.db.query_one("SELECT * FROM secrets WHERE name = ?", (name,))
        return _load(row, SecretRecord) if row else None

    def list_records(self) -> list[SecretRecord]:
        rows = self.db.query("SELECT * FROM secrets ORDER BY name")
        return [_load(row, SecretRecord) for row in rows]

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM secrets") or 0)

    def delete(self, name: str) -> bool:
        cursor = self.db.execute("DELETE FROM secrets WHERE name = ?", (name,))
        self.db.commit()
        return bool(cursor.rowcount)


class KeyRepository:
    """Histórico de chaves mestras (só metadados — nunca o material da chave)."""

    def __init__(self, db: Database):
        self.db = db

    def record(self, key_id: str, source: str, actor: str = "cli", rotated: bool = False) -> None:
        now = iso()
        self.db.execute(
            "INSERT INTO master_keys (key_id, source, actor, created_at, rotated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(key_id) DO UPDATE SET rotated_at = excluded.rotated_at",
            (key_id, source, actor, now, now if rotated else None),
        )
        self.db.commit()

    def list(self) -> list[dict]:
        rows = self.db.query("SELECT * FROM master_keys ORDER BY created_at DESC")
        return [dict(row) for row in rows]

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM master_keys") or 0)


class WorkflowRunRepository:
    """Execuções de workflow: o histórico auditável da orquestração."""

    def __init__(self, db: Database):
        self.db = db

    def save(self, run: WorkflowRun) -> WorkflowRun:
        run.updated_at = utcnow()
        self.db.execute(
            "INSERT INTO workflow_runs (id, workflow_id, status, environment, trigger, data, "
            "created_at, updated_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET status = excluded.status, data = excluded.data, "
            "updated_at = excluded.updated_at, finished_at = excluded.finished_at",
            (
                run.id,
                run.workflow_id,
                str(run.status),
                str(run.environment),
                run.trigger,
                _dump(run),
                iso(run.created_at),
                iso(run.updated_at),
                iso(run.finished_at) if run.finished_at else None,
            ),
        )
        self.db.commit()
        return run

    def get(self, run_id: str) -> WorkflowRun | None:
        row = self.db.query_one("SELECT * FROM workflow_runs WHERE id = ?", (run_id,))
        return _load(row, WorkflowRun) if row else None

    def list(
        self,
        workflow_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[WorkflowRun]:
        clauses, params = [], []
        if workflow_id:
            clauses.append("workflow_id = ?")
            params.append(workflow_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query(
            f"SELECT * FROM workflow_runs {where} ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        )
        return [_load(row, WorkflowRun) for row in rows]

    def last_run(self, workflow_id: str, trigger: str | None = None) -> WorkflowRun | None:
        if trigger:
            row = self.db.query_one(
                "SELECT * FROM workflow_runs WHERE workflow_id = ? AND trigger = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (workflow_id, trigger),
            )
        else:
            row = self.db.query_one(
                "SELECT * FROM workflow_runs WHERE workflow_id = ? ORDER BY created_at DESC LIMIT 1",
                (workflow_id,),
            )
        return _load(row, WorkflowRun) if row else None

    def stats(self) -> dict[str, int]:
        rows = self.db.query("SELECT status, COUNT(*) AS total FROM workflow_runs GROUP BY status")
        return {row["status"]: row["total"] for row in rows}

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM workflow_runs") or 0)


class ChangeProposalRepository:
    """Fase 7: o histórico de como o Runtime mudou a si mesmo."""

    def __init__(self, db: Database):
        self.db = db

    def save(self, proposal: ChangeProposal) -> ChangeProposal:
        proposal.updated_at = utcnow()
        self.db.execute(
            "INSERT INTO change_proposals (id, kind, name, status, origin, environment, fingerprint, data, "
            "created_at, updated_at, decided_at, applied_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET status = excluded.status, data = excluded.data, "
            "fingerprint = excluded.fingerprint, updated_at = excluded.updated_at, "
            "decided_at = excluded.decided_at, applied_at = excluded.applied_at",
            (
                proposal.id,
                str(proposal.kind),
                proposal.name,
                str(proposal.status),
                proposal.origin,
                str(proposal.environment),
                proposal.fingerprint,
                _dump(proposal),
                iso(proposal.created_at),
                iso(proposal.updated_at),
                iso(proposal.decided_at) if proposal.decided_at else None,
                iso(proposal.applied_at) if proposal.applied_at else None,
            ),
        )
        self.db.commit()
        return proposal

    def get(self, proposal_id: str) -> ChangeProposal | None:
        row = self.db.query_one("SELECT * FROM change_proposals WHERE id = ?", (proposal_id,))
        return _load(row, ChangeProposal) if row else None

    def list(
        self,
        status: str | None = None,
        kind: str | None = None,
        limit: int = 20,
    ) -> list[ChangeProposal]:
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(str(status))
        if kind:
            clauses.append("kind = ?")
            params.append(str(kind))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query(
            f"SELECT * FROM change_proposals {where} ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        )
        return [_load(row, ChangeProposal) for row in rows]

    def stats(self) -> dict[str, int]:
        rows = self.db.query("SELECT status, COUNT(*) AS total FROM change_proposals GROUP BY status")
        return {row["status"]: row["total"] for row in rows}

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM change_proposals") or 0)


class EvaluationSuiteRepository:
    """Fase 8: suítes declaradas (YAML) espelhadas para consulta e histórico."""

    def __init__(self, db: Database):
        self.db = db

    def save(self, suite: EvaluationSuite) -> EvaluationSuite:
        suite.updated_at = utcnow()
        self.db.execute(
            "INSERT INTO evaluation_suites (id, target_kind, target, data, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET data = excluded.data, target = excluded.target, "
            "target_kind = excluded.target_kind, updated_at = excluded.updated_at",
            (
                suite.id,
                str(suite.target_kind),
                suite.target,
                _dump(suite),
                iso(suite.created_at),
                iso(suite.updated_at),
            ),
        )
        self.db.commit()
        return suite

    def get(self, suite_id: str) -> EvaluationSuite | None:
        row = self.db.query_one("SELECT * FROM evaluation_suites WHERE id = ?", (suite_id,))
        return _load(row, EvaluationSuite) if row else None

    def list(
        self, target_kind: str | None = None, target: str | None = None, limit: int = 50
    ) -> list[EvaluationSuite]:
        clauses, params = [], []
        if target_kind:
            clauses.append("target_kind = ?")
            params.append(str(target_kind))
        if target:
            clauses.append("target = ?")
            params.append(target)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query(
            f"SELECT * FROM evaluation_suites {where} ORDER BY updated_at DESC LIMIT ?",
            (*params, limit),
        )
        return [_load(row, EvaluationSuite) for row in rows]

    def delete(self, suite_id: str) -> bool:
        self.db.execute("DELETE FROM evaluation_suites WHERE id = ?", (suite_id,))
        self.db.commit()
        return True

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM evaluation_suites") or 0)


class EvaluationRunRepository:
    """Cada execução medida — a memória numérica do que já foi provado."""

    def __init__(self, db: Database):
        self.db = db

    def save(self, run: EvaluationRun) -> EvaluationRun:
        self.db.execute(
            "INSERT INTO evaluation_runs (id, suite_id, target_kind, target, status, environment, data, "
            "created_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET status = excluded.status, data = excluded.data, "
            "finished_at = excluded.finished_at",
            (
                run.id,
                run.suite_id,
                str(run.target_kind),
                run.target,
                str(run.status),
                str(run.environment),
                _dump(run),
                iso(run.created_at),
                iso(run.finished_at) if run.finished_at else None,
            ),
        )
        self.db.commit()
        return run

    def get(self, run_id: str) -> EvaluationRun | None:
        row = self.db.query_one("SELECT * FROM evaluation_runs WHERE id = ?", (run_id,))
        return _load(row, EvaluationRun) if row else None

    def list(
        self,
        suite_id: str | None = None,
        status: str | None = None,
        target: str | None = None,
        limit: int = 20,
    ) -> list[EvaluationRun]:
        clauses, params = [], []
        if suite_id:
            clauses.append("suite_id = ?")
            params.append(suite_id)
        if status:
            clauses.append("status = ?")
            params.append(str(status))
        if target:
            clauses.append("target = ?")
            params.append(target)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query(
            f"SELECT * FROM evaluation_runs {where} ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        )
        return [_load(row, EvaluationRun) for row in rows]

    def last(self, suite_id: str, status: str | None = None) -> EvaluationRun | None:
        rows = self.list(suite_id=suite_id, status=status, limit=1)
        return rows[0] if rows else None

    def stats(self) -> dict[str, int]:
        rows = self.db.query("SELECT status, COUNT(*) AS total FROM evaluation_runs GROUP BY status")
        return {row["status"]: row["total"] for row in rows}

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM evaluation_runs") or 0)
