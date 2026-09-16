"""Workspace templates used by `egr init`."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

PACKAGE = "egr.templates.workspace"

SAMPLE_DOCUMENTS = {
    "nota-fiscal-82731.md": """# Nota Fiscal 82731

- Fornecedor: Metalúrgica Sul Ltda
- CNPJ: 00.000.000/0001-91
- Emissão: 2026-08-03
- Valor total: R$ 12.480,00
- Centro de custo: Produção

## Itens

| Item | Descrição | Quantidade | Valor |
|------|-----------|------------|-------|
| 1 | Chapas de aço 2mm | 40 | R$ 8.000,00 |
| 2 | Serviço de corte | 8 h | R$ 2.480,00 |
| 3 | Frete | 1 | R$ 2.000,00 |

## Observações

Nota recebida fora do prazo de conferência. Necessário validar com o pedido de compra 4471.
""",
    "relatorio-mensal.md": """# Relatório Mensal — Agosto/2026

## Resumo

- Receita: R$ 482.300,00
- Custos: R$ 371.900,00
- Margem: 22,9%

## Pendências

1. Conciliar 14 notas fiscais de entrada.
2. Revisar classificação de despesas de frete.
3. Fechar o inventário de chapas de aço.

## Riscos identificados

- Atraso recorrente na conferência de notas de fornecedores.
- Divergência de 3,1% entre estoque físico e sistema.
""",
    "politica-gastos.md": """# Política de Gastos

## Limiares de aprovação

| Faixa | Aprovação |
|-------|-----------|
| < R$ 5.000,00 | Automática |
| >= R$ 5.000,00 | Gestor financeiro |
| >= R$ 50.000,00 | Diretoria |

## Regras

- Toda despesa precisa de nota fiscal vinculada.
- Pagamentos sem pedido de compra são exceção e exigem justificativa.
- Dados de fornecedores nunca saem da rede local sem anonimização.
""",
}


def iter_template_files():
    """Yield (relative_path, resource) for every template file."""

    base = files(PACKAGE)
    stack = [(base, "")]
    while stack:
        current, prefix = stack.pop()
        for resource in sorted(current.iterdir(), key=lambda item: item.name):
            name = f"{prefix}{resource.name}"
            if resource.is_dir():
                stack.append((resource, f"{name}/"))
            elif name.endswith((".yaml", ".yml", ".md")) or name == ".gitignore":
                yield name, resource


def render_workspace(workspace: Path, *, overwrite: bool = False, sample: bool = True) -> list[str]:
    """Copy templates into `workspace`. Returns the list of written files."""

    created: list[str] = []

    for relative, resource in iter_template_files():
        destination = workspace / Path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and not overwrite:
            continue
        destination.write_text(resource.read_text(encoding="utf-8"), encoding="utf-8")
        created.append(relative)

    for directory in ("artifacts", "logs", "documents", ".egr/sandbox"):
        (workspace / directory).mkdir(parents=True, exist_ok=True)

    if sample:
        documents = workspace / "documents"
        documents.mkdir(parents=True, exist_ok=True)
        for name, content in SAMPLE_DOCUMENTS.items():
            destination = documents / name
            if destination.exists() and not overwrite:
                continue
            destination.write_text(content, encoding="utf-8")
            created.append(f"documents/{name}")

    return created
