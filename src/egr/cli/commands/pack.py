"""Pacotes verticais: a empresa não começa do zero (Fase 12).

    egr pack list                       # catálogo e o que já está instalado
    egr pack show finance               # o que vem dentro
    egr pack check finance              # requisitos e colisões
    egr pack install finance            # cria a proposta (não escreve nada)
    egr proposal approve <id> && egr proposal apply <id>
    egr pack status                     # instalados, versões e defasagens
    egr pack remove finance --yes       # remove o que não foi mexido
"""

from __future__ import annotations

from pathlib import Path

import typer

from ..context import get_runtime
from ..formatting import error, info, json_output, kv, success, table, warning

app = typer.Typer(
    no_args_is_help=True,
    help="Packs verticais: Financeiro, Contábil, Vendas, Operações, RH, Marketing, Suporte",
)


@app.command(name="list")
def list_packs(
    as_json: bool = typer.Option(False, "--json", help="saída JSON"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Catálogo (embutido + do workspace) e o que já está instalado."""

    runtime = get_runtime(workspace)
    data = runtime.packs_status()
    if as_json:
        json_output(data)
        return
    kv(
        "Packs",
        {
            "no catálogo": data["catálogo"]["total"],
            "embutidos": data["catálogo"]["embutidos"],
            "do workspace": data["catálogo"]["do_workspace"],
            "instalados": data["instalados"],
            "diretório": data["diretório"],
        },
    )
    table(
        "Catálogo",
        ["id", "nome", "versão", "vertical", "status", "artefatos", "origem"],
        [
            [
                item["id"],
                item["nome"],
                item["versão"],
                item["vertical"],
                item["status"],
                item["artefatos"],
                item["origem"],
            ]
            for item in data["pacotes"]
        ],
    )


@app.command(name="show")
def show(
    pack: str = typer.Argument(..., help="id do pack"),
    as_json: bool = typer.Option(False, "--json", help="saída JSON"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """O que vem dentro do pack — e o que ele exige antes de entrar."""

    runtime = get_runtime(workspace)
    try:
        item = runtime.packs.get(pack)
    except Exception as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        json_output(item.model_dump(mode="json"))
        return
    kv(f"Pack — {item.title}", item.summary())
    table(
        "Arquivos que a instalação escreve",
        ["#", "arquivo"],
        [[index, name] for index, name in enumerate(runtime.packs.plan(item), start=1)],
    )
    if item.requires.tools or item.requires.connectors or item.requires.agents:
        kv(
            "Requisitos",
            {
                "ferramentas": ", ".join(item.requires.tools) or "-",
                "conectores": ", ".join(item.requires.connectors) or "-",
                "agentes": ", ".join(item.requires.agents) or "-",
                "ambiente mínimo": item.requires.min_environment,
            },
        )


@app.command(name="check")
def check(
    pack: str = typer.Argument(..., help="id do pack"),
    as_json: bool = typer.Option(False, "--json", help="saída JSON"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Verificação estática: requisitos, colisões e ambiente. Não escreve nada."""

    runtime = get_runtime(workspace)
    try:
        item = runtime.packs.get(pack)
    except Exception as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    checks = runtime.packs.check(item)
    if as_json:
        json_output(checks)
        return
    table(
        f"Verificação — {item.id}",
        ["checagem", "ok", "nível", "detalhe"],
        [
            [item_["nome"], "sim" if item_["ok"] else "não", item_["nível"], item_["detalhe"] or "-"]
            for item_ in checks
        ],
    )
    erros = [item_ for item_ in checks if not item_["ok"] and item_["nível"] == "error"]
    if erros:
        warning(f"{len(erros)} impedimento(s): o pack não pode ser instalado agora")
        raise typer.Exit(code=1)
    success("sem impedimentos — pode propor a instalação")


@app.command(name="install")
def install(
    pack: str = typer.Argument(..., help="id do pack"),
    by: str = typer.Option("human:cli", "--by", "-b"),
    rationale: str = typer.Option("", "--rationale", "-r"),
    as_json: bool = typer.Option(False, "--json", help="saída JSON"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Propõe a instalação. Quem escreve no workspace é a aprovação humana."""

    runtime = get_runtime(workspace)
    try:
        proposal = runtime.packs.propose(pack, actor=by, rationale=rationale)
    except Exception as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        json_output(proposal.summary())
        return
    success(f"proposta {proposal.id} criada ({proposal.status})")
    kv(
        "Proposta",
        {
            "pack": proposal.name,
            "tipo": str(proposal.kind),
            "destino": proposal.target,
            "risco": str(proposal.risk),
            "estado": str(proposal.status),
        },
    )
    info(f"ver: egr proposal show {proposal.id}")
    info(f"aprovar: egr proposal approve {proposal.id} --by {by}")
    info(f"aplicar: egr proposal apply {proposal.id} --by {by}")


@app.command(name="status")
def status(
    as_json: bool = typer.Option(False, "--json", help="saída JSON"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """O que está instalado, por quem, com qual versão e impressão digital."""

    runtime = get_runtime(workspace)
    instalados = runtime.packs.installed()
    if as_json:
        json_output([item.summary() for item in instalados])
        return
    if not instalados:
        info("nenhum pack instalado — `egr pack list` mostra o catálogo")
        return
    table(
        "Instalados",
        ["id", "versão", "status", "impressão", "arquivos", "instalado por", "proposta", "quando"],
        [
            [
                item.id,
                item.version,
                str(item.status),
                item.checksum,
                len(item.files),
                item.installed_by,
                item.proposal or "-",
                item.summary()["quando"],
            ]
            for item in instalados
        ],
    )


@app.command(name="add")
def add(
    arquivo: Path = typer.Argument(..., help="arquivo YAML do pack"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Copia um pack do time para `packs/` do workspace (passa a valer no catálogo)."""

    runtime = get_runtime(workspace)
    if not arquivo.exists():
        error(f"arquivo não encontrado: {arquivo}")
        raise typer.Exit(code=1)
    from ...packs.catalog import load_pack_file

    try:
        pack = load_pack_file(arquivo, origin="workspace")
    except Exception as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    destino = Path(runtime.settings.workspace) / "packs" / f"{pack.id}.yaml"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(arquivo.read_text(encoding="utf-8"), encoding="utf-8")
    success(f"pack '{pack.id}' disponível em packs/{destino.name}")
    info("instalar: egr pack install " + pack.id)


@app.command(name="remove")
def remove(
    pack: str = typer.Argument(..., help="id do pack"),
    yes: bool = typer.Option(False, "--yes", "-y", help="confirma a remoção"),
    by: str = typer.Option("human:cli", "--by", "-b"),
    as_json: bool = typer.Option(False, "--json", help="saída JSON"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Remove o que o pack escreveu — e preserva o que foi editado depois."""

    runtime = get_runtime(workspace)
    if not yes:
        warning(f"isto remove os arquivos do pack '{pack}' que não foram alterados (use --yes)")
        raise typer.Exit(code=1)
    try:
        resultado = runtime.packs.remove(pack, actor=by)
    except Exception as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        json_output(resultado)
        return
    success(f"{len(resultado['removidos'])} arquivo(s) removido(s)")
    for nome in resultado["mantidos"]:
        warning(f"mantido (foi alterado depois da instalação): {nome}")


__all__ = ["app"]
