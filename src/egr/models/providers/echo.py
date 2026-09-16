"""Echo provider: deterministic, offline, zero-cost.

It exists so the Runtime can be exercised end-to-end without any model, API key
or network. It emits a *real* plan (filesystem -> sandboxed python -> report),
which makes the whole governance path testable offline.
"""

from __future__ import annotations

import json

from ..gateway import CompletionRequest, CompletionResponse, Message, ModelProvider

ANALYSIS_SCRIPT = '''"""Gerado pelo EGR: analisa documentos locais e produz um relatorio."""

import json
import os
from datetime import datetime
from pathlib import Path

WORKSPACE = Path(os.environ.get("EGR_WORKSPACE", ".")).resolve()
DOCUMENTS = WORKSPACE / "documents"
TARGET = DOCUMENTS if DOCUMENTS.exists() else WORKSPACE
SKIP = {".git", ".venv", "node_modules", "__pycache__", ".egr", ".pytest_cache", "logs"}

files = []
for path in sorted(TARGET.rglob("*")):
    if not path.is_file():
        continue
    if any(part in SKIP for part in path.parts):
        continue
    files.append(path)

lines = [
    "# Relatorio de Analise de Documentos",
    "",
    f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
    f"Origem: {TARGET.relative_to(WORKSPACE) if TARGET != WORKSPACE else '.'}",
    f"Total de arquivos: {len(files)}",
    "",
    "## Inventario",
    "",
]

total_bytes = 0
for path in files[:100]:
    stat = path.stat()
    total_bytes += stat.st_size
    rel = path.relative_to(WORKSPACE)
    entry = f"- `{rel}` - {stat.st_size} bytes"
    if path.suffix.lower() in {".md", ".txt", ".csv", ".log", ".yaml", ".yml", ".json"}:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            entry += f", {len(text.splitlines())} linhas, {len(text.split())} palavras"
        except OSError:
            entry += ", (nao legivel)"
    lines.append(entry)

lines += [
    "",
    "## Resumo",
    "",
    f"- Arquivos analisados: {len(files)}",
    f"- Volume total: {total_bytes} bytes",
    "- Ambiente: local, sem envio de dados para serviços externos",
    "",
]

report = Path("relatorio.md")
report.write_text("\\n".join(lines), encoding="utf-8")

print(json.dumps({
    "arquivos": len(files),
    "bytes": total_bytes,
    "relatorio": str(report.resolve()),
}, ensure_ascii=False))
'''


class EchoProvider(ModelProvider):
    """Offline provider used for tests, `egr doctor` and air-gapped demos."""

    type = "echo"

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        prompt = self._last_user_message(request)
        text = self.plan(prompt) if request.json_mode else self.answer(prompt)
        usage = {"prompt_tokens": len(prompt) // 4, "completion_tokens": len(text) // 4}
        return CompletionResponse(
            text=text,
            provider=self.name,
            model=self.config.model or "echo-1",
            latency_ms=1,
            usage=usage,
            external=False,
            cost=self.estimate_cost(usage),
            raw={"mode": "echo"},
        )

    def health(self) -> tuple[bool, str]:
        return True, "offline deterministic provider (always available)"

    # ---- plan / answer -----------------------------------------------
    @staticmethod
    def _last_user_message(request: CompletionRequest) -> str:
        for message in reversed(request.messages):
            if message.role == "user":
                return message.content
        return ""

    def plan(self, objective: str) -> str:
        payload = {
            "objective": objective,
            "steps": [
                {
                    "id": "s1",
                    "tool": "filesystem.list",
                    "args": {"path": "documents", "recursive": True},
                    "rationale": "Levantar o inventario de documentos disponiveis no workspace",
                },
                {
                    "id": "s2",
                    "tool": "python.execute",
                    "args": {"script": ANALYSIS_SCRIPT},
                    "rationale": "Calcular estatisticas e gerar o relatorio dentro do sandbox",
                },
            ],
            "final_answer": "Inventario local gerado e relatorio gravado no sandbox do Runtime.",
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def answer(self, prompt: str) -> str:
        return (
            "[echo provider] Nenhum modelo configurado. "
            "Resposta deterministica para: " + (prompt[:200] or "(prompt vazio)")
        )


__all__ = ["ANALYSIS_SCRIPT", "EchoProvider", "Message"]
