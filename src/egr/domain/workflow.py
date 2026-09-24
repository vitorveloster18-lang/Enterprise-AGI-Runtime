"""Workflow: how work happens. (Agent = who works. Workflow = how.)

Declarado em YAML, versionado no repositório, executado pelo Runtime como um
**DAG**: dependências, condições, retry, compensação e paralelismo opcional.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .enums import Environment

ON_ERROR_OPTIONS = ("fail", "continue", "compensate")


class WorkflowTrigger(BaseModel):
    type: str = "manual"  # manual | event | cron | webhook
    event: str | None = None  # ex.: task.completed ou task.* (prefixo)
    cron: str | None = None  # minuto hora dia mes dia_semana
    enabled: bool = True


class WorkflowStep(BaseModel):
    id: str
    agent: str | None = None
    task: str | None = None  # named task template
    objective: str | None = None
    tool: str | None = None
    args: dict = Field(default_factory=dict)
    policy: str | None = None
    depends_on: list[str] = Field(default_factory=list)

    # ---- Fase 6 ------------------------------------------------------
    #: expressão segura avaliada contra {inputs, steps, run}; falsa => pulado
    condition: str | None = None
    #: tentativas antes de considerar falha (1 = sem retry)
    max_attempts: int = 1
    #: fail | continue | compensate — herdado de `Workflow.on_error` quando None
    on_error: str | None = None
    #: passo executado para desfazer efeitos quando este falha
    compensate_with: str | None = None
    #: sobrescreve o ambiente do workflow para este passo
    environment: str | None = None
    #: extrai valores do resultado: {"total": "{{steps.s1.answer}}"}
    outputs: dict = Field(default_factory=dict)
    #: memória: namespace que o passo deve consultar
    namespace: str | None = None


class Workflow(BaseModel):
    id: str
    name: str = ""
    version: str = "1.0.0"
    description: str = ""
    environment: Environment = Environment.DEVELOPMENT
    trigger: WorkflowTrigger = Field(default_factory=WorkflowTrigger)
    steps: list[WorkflowStep] = Field(default_factory=list)

    # ---- Fase 6 -------------------------------------------------------
    #: executa passos independentes do mesmo nível em paralelo
    parallel: bool = False
    max_parallel: int = 4
    #: política padrão de falha dos passos
    on_error: str = "fail"
    #: valores padrão disponíveis em {{inputs.*}}
    inputs: dict = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)

    def step(self, step_id: str) -> WorkflowStep | None:
        for step in self.steps:
            if step.id == step_id:
                return step
        return None

    def on_error_for(self, step: WorkflowStep) -> str:
        return step.on_error or self.on_error


__all__ = ["ON_ERROR_OPTIONS", "Workflow", "WorkflowStep", "WorkflowTrigger"]
