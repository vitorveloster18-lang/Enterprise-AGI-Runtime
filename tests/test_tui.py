"""A interface de terminal: catálogo de comandos, configuração e o app em si."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from egr.cli.main import app as cli_app

catalog = pytest.importorskip("egr.tui.catalog", reason="textual não instalado (extra .[tui])")
config_form = pytest.importorskip("egr.tui.config_form", reason="textual não instalado (extra .[tui])")
screens_settings = pytest.importorskip("egr.tui.screens.settings", reason="textual não instalado")
textual_widgets = pytest.importorskip("textual.widgets", reason="textual não instalado")

FieldRow = screens_settings.FieldRow
Input = textual_widgets.Input


# ── catálogo ─────────────────────────────────────────────────────────────────
def test_catalogo_cobre_os_comandos_da_cli():
    paths = {spec.path for spec in catalog.build_catalog(cli_app)}

    assert "task exec" in paths
    assert "memory search" in paths
    assert "policy list" in paths
    assert "audit verify" in paths


def test_catalogo_bloqueia_o_que_derrubaria_a_interface():
    """`serve` sobe servidor e `tui` abriria outra interface: fora da paleta."""

    paths = {spec.path for spec in catalog.build_catalog(cli_app)}

    assert "serve" not in paths
    assert "tui" not in paths


def test_montagem_de_argumentos_separa_posicionais_de_opcoes():
    spec = next(spec for spec in catalog.build_catalog(cli_app) if spec.path == "memory search")

    args = catalog.build_args(spec, {"query": "contrato de fornecedor", "limit": "5"})

    # objetivo/consulta é um argumento único: vai inteiro, sem virar três flags
    assert args[:3] == ["memory", "search", "contrato de fornecedor"]
    assert args[-2:] == ["--limit", "5"]


def test_flags_so_entram_quando_ligadas():
    spec = next(spec for spec in catalog.build_catalog(cli_app) if spec.path == "task exec")

    sem_flag = catalog.build_args(spec, {"objective": "olá", "as_json": False})
    com_flag = catalog.build_args(spec, {"objective": "olá", "as_json": True})

    assert "--json" not in sem_flag
    assert "--json" in com_flag


def test_run_command_captura_saida_e_codigo(workspace):
    code, output = catalog.run_command(cli_app, ["status"], str(workspace))

    assert code == 0
    assert "EGR Runtime" in output
    assert "environment" in output.lower() or "ambiente" in output.lower()


# ── configuração ─────────────────────────────────────────────────────────────
def test_configuracao_vai_e_volta_do_yaml(workspace):
    data = config_form.load_config_dict(workspace)
    config_form.write_value(data, "memory.retrieval", "fts")
    config_form.write_value(data, "runtime.max_steps", 12)

    config_form.save_config_dict(workspace, data)

    text = (workspace / "egr.yaml").read_text(encoding="utf-8")
    assert "retrieval: fts" in text
    assert "max_steps: 12" in text


def test_configuracao_invalida_e_recusada(workspace):
    data = config_form.load_config_dict(workspace)
    config_form.write_value(data, "memory.retrieval", "inventado")

    with pytest.raises(ValidationError):
        config_form.save_config_dict(workspace, data)


def test_coerce_converte_e_recusa_fora_da_lista():
    spec = config_form.fields_of("Memória")[0]  # memory.retrieval (choice)

    assert config_form.coerce(spec, "fts") == "fts"
    with pytest.raises(ValueError):
        config_form.coerce(spec, "inventado")


def test_provedor_nunca_grava_a_chave(workspace):
    data = config_form.load_config_dict(workspace)
    provider = {
        "name": "groq",
        "type": "openai_compat",
        "enabled": True,
        "model": "llama-3.1-70b",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "external": True,
        "priority": 42,
        "capabilities": ["reasoning", "chat"],
        "pricing": {"currency": "USD", "input_per_1m": 0.59, "output_per_1m": 0.79, "per_call": 0.0},
    }
    config_form.set_providers(data, [provider])
    config_form.save_config_dict(workspace, data)

    text = (workspace / "egr.yaml").read_text(encoding="utf-8")
    assert "GROQ_API_KEY" in text        # o nome da variável, que é o certo
    assert "api_key_env: GROQ_API_KEY" in text
    assert "sk-" not in text


# ── o app ────────────────────────────────────────────────────────────────────
def _app(workspace):
    egr_tui = pytest.importorskip("egr.tui", reason="textual não instalado (extra .[tui])")
    return egr_tui.EGRApp(workspace)


def test_interface_abre_e_mostra_o_status(workspace):
    async def run() -> str:
        app = _app(workspace)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            status = app.query_one("#status")
            return str(status.render_line(0))

    line = asyncio.run(run())
    assert workspace.name in line


def test_interface_abre_a_paleta_com_todos_os_comandos(workspace):
    async def run() -> int:
        app = _app(workspace)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+p")
            await pilot.pause(0.5)
            palette = app.screen
            total = len(palette.shown)
            await pilot.press("escape")
            await pilot.pause(0.2)
            return total

    assert asyncio.run(run()) > 50


def test_interface_grava_configuracao_sem_tocar_em_codigo(workspace):
    async def run() -> bool:
        app = _app(workspace)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+s")
            await pilot.pause(0.5)
            for row in app.screen.query(FieldRow):
                if row.spec.path == "runtime.max_steps":
                    row.query_one("#control", Input).value = "9"
            await pilot.press("ctrl+s")
            await pilot.pause(0.6)
            return (workspace / "egr.yaml").exists()

    assert asyncio.run(run())
    assert "max_steps: 9" in (workspace / "egr.yaml").read_text(encoding="utf-8")


def test_objetivo_em_linguagem_natural_vira_task_governada(workspace):
    async def run() -> tuple[bool, int]:
        app = _app(workspace)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            prompt = app.query_one("#prompt")
            prompt.value = "listar os documentos do workspace"
            await pilot.press("enter")
            for _ in range(80):
                await pilot.pause(0.1)
                if not app.busy:
                    break
            return not app.busy, app.runtime.tasks.count_by_status().get("completed", 0)

    finished, completed = asyncio.run(run())
    assert finished
    assert completed >= 1


# ── provedores (regressão do DuplicateKey visto no Termux) ────────────────────
providers_screen = pytest.importorskip("egr.tui.screens.providers", reason="textual não instalado")
ProviderForm = providers_screen.ProviderForm
DataTable = textual_widgets.DataTable


def _google(name="google"):
    return {
        "name": name,
        "type": "openai_compat",
        "enabled": True,
        "model": "gemini-2.0-flash",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api_key_env": "GOOGLE_API_KEY",
        "external": True,
        "priority": 100,
        "capabilities": ["reasoning", "chat"],
        "pricing": {"currency": "USD", "input_per_1m": 0.0, "output_per_1m": 0.0, "per_call": 0.0},
    }


def test_nome_de_variavel_recusa_texto_fora_do_formato():
    assert providers_screen.valid_env_name("GOOGLE_API_KEY")
    assert providers_screen.valid_env_name("_X9")
    assert not providers_screen.valid_env_name("GOOGLE_API_KEY (gemini-3.5-flash)")
    assert not providers_screen.valid_env_name("minha chave")
    assert not providers_screen.valid_env_name("9LIVES")


def test_tabela_de_provedores_aguenta_nomes_repetidos(workspace):
    """Dois 'google' + refresh duplo: era o DuplicateKey (a chave é o índice)."""

    async def run() -> int:
        app = _app(workspace)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+m")
            await pilot.pause(0.5)
            screen = app.screen
            screen.providers.append(_google("google"))
            screen.providers.append(_google("google"))
            screen._refresh()
            screen._refresh()
            return screen.query_one("#table", DataTable).row_count

    assert asyncio.run(run()) >= 2


def test_nome_repetido_reabre_o_formulario_em_vez_de_duplicar(workspace):
    async def run():
        app = _app(workspace)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+m")
            await pilot.pause(0.5)
            screen = app.screen
            before = len(screen.providers)
            screen._added(_google("google"))
            after_first = len(screen.providers)
            screen._added(_google("google"))  # repetido: recusa e reabre preenchido
            await pilot.pause(0.3)
            return before, after_first, len(screen.providers), type(app.screen).__name__

    before, after_first, after_second, top = asyncio.run(run())
    assert after_first == before + 1
    assert after_second == after_first
    assert top == "ProviderForm"


def test_formulario_recusa_variavel_invalida_e_nome_vazio(workspace):
    async def run():
        app = _app(workspace)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.push_screen(ProviderForm())
            await pilot.pause(0.3)
            form = app.screen
            form.query_one("#f_api_key_env", Input).value = "GOOGLE_API_KEY (gemini-3.5-flash)"
            junk = form._collect()
            form.query_one("#f_api_key_env", Input).value = "GOOGLE_API_KEY"
            form.query_one("#f_name", Input).value = ""
            empty_name = form._collect()
            form.query_one("#f_name", Input).value = "google"
            fixed = form._collect()
            return junk is None, empty_name is None, fixed

    junk_refused, empty_refused, fixed = asyncio.run(run())
    assert junk_refused
    assert empty_refused
    assert fixed is not None
    assert fixed["api_key_env"] == "GOOGLE_API_KEY"
