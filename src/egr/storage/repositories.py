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
from ..domain.memory import MemoryRecord
from ..domain.policy import Policy
from ..domain.task import Task
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

    def list(self, namespace: str | None = None, limit: int = 20) -> list[MemoryRecord]:
        if namespace:
            rows = self.db.query(
                "SELECT * FROM memory_records WHERE namespace = ? ORDER BY created_at DESC LIMIT ?",
                (namespace, limit),
            )
        else:
            rows = self.db.query("SELECT * FROM memory_records ORDER BY created_at DESC LIMIT ?", (limit,))
        return [_load(row, MemoryRecord) for row in rows]

    def stats(self) -> dict[str, int]:
        rows = self.db.query("SELECT kind, COUNT(*) AS total FROM memory_records GROUP BY kind")
        return {row["kind"]: row["total"] for row in rows}

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM memory_records") or 0)


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
