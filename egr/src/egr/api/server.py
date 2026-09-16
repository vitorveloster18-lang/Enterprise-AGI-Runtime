"""Local API + console do Runtime.

A interface é descartável; o núcleo é o Runtime. Telegram/Slack/Web são
gateways sobre esta mesma API (Fase 10).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ..runtime.runtime import Runtime
from ..version import PHASE, __version__

TAGS = [
    {"name": "runtime", "description": "Estado e saúde do Runtime"},
    {"name": "tasks", "description": "Submissão e inspeção de trabalho"},
    {"name": "governance", "description": "Políticas, aprovações e auditoria"},
]


class TaskCreate(BaseModel):
    objective: str
    agent_id: str | None = None
    environment: str | None = None


class DecisionRequest(BaseModel):
    decision: str = "approve"  # approve | deny
    by: str = "console"
    note: str | None = None


def create_app(runtime: Runtime) -> FastAPI:
    app = FastAPI(
        title="Enterprise AGI Runtime",
        version=__version__,
        description=f"{PHASE}. API local do Runtime.",
        openapi_tags=TAGS,
    )

    # ------------------------------------------------------------------
    @app.get("/health", tags=["runtime"])
    def health() -> dict[str, Any]:
        return runtime.health()

    @app.get("/v1/status", tags=["runtime"])
    def status() -> dict[str, Any]:
        return runtime.status()

    @app.get("/v1/agents", tags=["runtime"])
    def agents() -> list[dict[str, Any]]:
        return [agent.model_dump(mode="json") for agent in runtime.agents.values()]

    @app.get("/v1/tools", tags=["runtime"])
    def tools() -> list[dict[str, Any]]:
        return runtime.tools.list()

    @app.get("/v1/models", tags=["runtime"])
    def models() -> dict[str, Any]:
        return runtime.gateway.health()

    # ------------------------------------------------------------------
    @app.get("/v1/tasks", tags=["tasks"])
    def list_tasks(limit: int = 20, status: str | None = None) -> list[dict[str, Any]]:
        return [
            task.model_dump(mode="json")
            for task in runtime.tasks.list(status=status, limit=limit)
        ]

    @app.post("/v1/tasks", tags=["tasks"])
    def create_task(payload: TaskCreate) -> dict[str, Any]:
        task = runtime.submit(
            payload.objective,
            agent_id=payload.agent_id,
            environment=payload.environment,
            created_by="api",
        )
        return task.model_dump(mode="json")

    @app.get("/v1/tasks/{task_id}", tags=["tasks"])
    def get_task(task_id: str) -> dict[str, Any]:
        task = runtime.tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return task.model_dump(mode="json")

    @app.get("/v1/tasks/{task_id}/events", tags=["tasks"])
    def task_events(task_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return [event.model_dump(mode="json") for event in runtime.audit.list(task_id=task_id, limit=limit)]

    # ------------------------------------------------------------------
    @app.get("/v1/approvals", tags=["governance"])
    def approvals(status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return [approval.model_dump(mode="json") for approval in runtime.approvals.list(status=status, limit=limit)]

    @app.post("/v1/approvals/{approval_id}/decision", tags=["governance"])
    def decide(approval_id: str, payload: DecisionRequest) -> dict[str, Any]:
        try:
            if payload.decision == "deny":
                task = runtime.deny(approval_id, decided_by=payload.by, note=payload.note)
            else:
                task = runtime.approve(approval_id, decided_by=payload.by, note=payload.note)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "approval": approval_id,
            "decision": payload.decision,
            "task": task.model_dump(mode="json") if task else None,
        }

    @app.get("/v1/events", tags=["governance"])
    def events(limit: int = 50, type: str | None = None) -> list[dict[str, Any]]:
        return [event.model_dump(mode="json") for event in runtime.audit.list(type=type, limit=limit)]

    @app.get("/v1/audit/verify", tags=["governance"])
    def verify() -> dict[str, Any]:
        return runtime.audit.verify()

    @app.get("/v1/memory", tags=["governance"])
    def memory(query: str = "", limit: int = 10) -> list[dict[str, Any]]:
        records = runtime.memory.search(query, limit=limit) if query else runtime.memory.list(limit=limit)
        return [record.model_dump(mode="json") for record in records]

    # ------------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def console() -> str:
        return _console_html()

    return app


def _console_html() -> str:
    return """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>EGR Console</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; background: #0b0f14; color: #d7e0ea; }
  header { padding: 16px 20px; border-bottom: 1px solid #1d2733; display: flex; gap: 16px; align-items: baseline; flex-wrap: wrap; }
  h1 { font-size: 16px; margin: 0; letter-spacing: .08em; text-transform: uppercase; }
  .muted { color: #7c8b9c; }
  main { padding: 20px; display: grid; gap: 20px; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }
  section { background: #111823; border: 1px solid #1d2733; border-radius: 10px; padding: 14px 16px; }
  h2 { font-size: 12px; letter-spacing: .12em; text-transform: uppercase; color: #7c8b9c; margin: 0 0 10px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; }
  .card { background: #0e1520; border: 1px solid #1d2733; border-radius: 8px; padding: 10px; }
  .card b { display: block; font-size: 20px; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  td, th { text-align: left; padding: 4px 6px; border-bottom: 1px solid #1a2330; vertical-align: top; }
  th { color: #7c8b9c; font-weight: 500; }
  .ok { color: #4ade80; } .bad { color: #f87171; } .warn { color: #fbbf24; }
  button { background: #1d4ed8; color: white; border: 0; border-radius: 6px; padding: 4px 10px; cursor: pointer; font: inherit; }
  button.deny { background: #7f1d1d; margin-left: 6px; }
  code { color: #93c5fd; }
  .row { display: flex; gap: 8px; align-items: center; }
</style>
</head>
<body>
<header>
  <h1>Enterprise AGI Runtime</h1>
  <span class="muted" id="env"></span>
  <span class="muted" id="updated"></span>
</header>
<main>
  <section style="grid-column: 1 / -1">
    <h2>Status</h2>
    <div class="cards" id="cards"></div>
  </section>
  <section>
    <h2>Aprovações pendentes</h2>
    <table id="approvals"><tbody></tbody></table>
  </section>
  <section>
    <h2>Tasks</h2>
    <table id="tasks"><tbody></tbody></table>
  </section>
  <section style="grid-column: 1 / -1">
    <h2>Auditoria (últimos eventos)</h2>
    <table id="events"><tbody></tbody></table>
  </section>
</main>
<script>
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

async function load() {
  const [status, approvals, tasks, events] = await Promise.all([
    fetch('/v1/status').then(r => r.json()),
    fetch('/v1/approvals?status=pending').then(r => r.json()),
    fetch('/v1/tasks?limit=10').then(r => r.json()),
    fetch('/v1/events?limit=25').then(r => r.json()),
  ]);

  $('env').textContent = `ambiente: ${status.environment} · enterprise: ${status.enterprise.id} · workspace: ${status.workspace}`;
  $('updated').textContent = 'atualizado ' + new Date().toLocaleTimeString('pt-BR');

  const c = status.counts;
  const cards = [
    ['agentes', c.agents], ['ferramentas', c.tools], ['políticas', c.policies],
    ['tasks', Object.values(c.tasks).reduce((a,b)=>a+b,0)],
    ['aprovações', c.approvals_pending], ['artefatos', c.artifacts],
    ['memória', c.memory.total], ['eventos', c.events],
  ];
  $('cards').innerHTML = cards.map(([k,v]) => `<div class="card"><span class="muted">${k}</span><b>${v}</b></div>`).join('');

  $('approvals').innerHTML = '<tr><th>ação</th><th>solicitado por</th><th>papel</th><th>decisão</th></tr>' +
    (approvals.length ? approvals.map(a => `<tr>
      <td><code>${esc(a.tool)}</code><br><span class="muted">${esc(a.reason)}</span></td>
      <td>${esc(a.requested_by)}</td><td>${esc(a.required_role || '-')}</td>
      <td><button onclick="decide('${a.id}','approve')">aprovar</button><button class="deny" onclick="decide('${a.id}','deny')">negar</button></td>
    </tr>`).join('') : '<tr><td colspan="4" class="muted">nenhuma aprovação pendente</td></tr>');

  $('tasks').innerHTML = '<tr><th>id</th><th>status</th><th>agente</th><th>objetivo</th></tr>' +
    (tasks.length ? tasks.map(t => {
      const cls = t.status === 'completed' ? 'ok' : (t.status === 'failed' ? 'bad' : (t.status === 'requires_approval' ? 'warn' : ''));
      return `<tr><td><code>${esc(t.id)}</code></td><td class="${cls}">${esc(t.status)}</td><td>${esc(t.agent_id)}</td><td>${esc(t.objective)}</td></tr>`;
    }).join('') : '<tr><td colspan="4" class="muted">nenhuma task ainda</td></tr>');

  $('events').innerHTML = '<tr><th>#</th><th>evento</th><th>ator</th><th>task</th><th>detalhe</th></tr>' +
    events.map(e => `<tr><td>${e.seq}</td><td>${esc(e.type)}</td><td>${esc(e.actor)}</td><td><code>${esc(e.task_id || '')}</code></td><td>${esc(JSON.stringify(e.payload)).slice(0,140)}</td></tr>`).join('');
}

async function decide(id, decision) {
  await fetch(`/v1/approvals/${id}/decision`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({decision, by: 'console'}),
  });
  load();
}

load();
setInterval(load, 5000);
</script>
</body>
</html>
"""
