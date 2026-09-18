"""Fase 13 — lacuna 5b: memória multimodal e limpeza de PII na escrita.

Duas ausências da Fase 5, ambas sobre **o que não devia estar na memória**:

- dado pessoal (CPF, e-mail, telefone, cartão) escrito num acervo que é
  consultado por padrão e copiado para contexto de modelo;
- mídia (print, foto, áudio) que a empresa lembra, mas que não tem onde ficar
  sem virar bagunça dentro do banco.

A regra dos dois lados é a mesma: **o binário fica fora, o texto fica
governado, e o que sai é dito** — nunca silenciosamente.
"""

from __future__ import annotations

import base64

import pytest

from egr.core.config import EGRConfig, Settings
from egr.core.errors import ConfigError
from egr.memory.media import MediaStore, sniff
from egr.memory.scrubbing import _luhn, _valid_cnpj, _valid_cpf, summarize
from egr.runtime.runtime import Runtime

PNG = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 300
MP3 = b"ID3\x03" + b"\x00" * 300
PDF = b"%PDF-1.7\n" + b"\x00" * 200


def _runtime(workspace, **overrides) -> Runtime:
    config = EGRConfig()
    for key, value in overrides.items():
        setattr(config.memory, key, value)
    return Runtime(Settings(workspace=workspace, config=config), enable_logging=False)


# ----------------------------------------------------------------------
# limpeza de PII
# ----------------------------------------------------------------------
def test_cpf_and_cnpj_are_validated_not_just_matched():
    assert _valid_cpf("123.456.789-09")
    assert not _valid_cpf("111.111.111-11")
    assert _valid_cnpj("11.222.333/0001-81")
    assert not _valid_cnpj("11.111.111/1111-11")
    assert _luhn("4111 1111 1111 1111")
    assert not _luhn("1234 5678 9012 3456")


def test_scrub_removes_personal_data_before_persisting(runtime):
    record = runtime.memory.write(
        "cliente joao@example.com, cpf 123.456.789-09, fechou o contrato"
    )

    assert "123.456.789-09" not in record.content
    assert "joao@example.com" not in record.content
    assert "[cpf removido]" in record.content
    assert record.metadata["pii_removido"] == {"cpf": 1, "e-mail": 1}


def test_scrub_covers_cards_phones_and_postal_codes(runtime):
    record = runtime.memory.write(
        "cartao 4111 1111 1111 1111, fone (51) 98888-7777, cep 90010-150"
    )

    assert record.metadata["pii_removido"]["cartão"] == 1
    assert record.metadata["pii_removido"]["telefone"] == 1
    assert "98888" not in record.content


def test_scrub_leaves_business_text_alone(runtime):
    content = "reunião com o fornecedor sobre preço e prazo de entrega"

    record = runtime.memory.write(content)

    assert record.content == content
    assert "pii_removido" not in record.metadata


def test_the_scrubbing_is_recorded_by_type_never_by_value(runtime):
    runtime.memory.write("cpf 123.456.789-09 e e-mail sigiloso@exemplo.com")

    events = [event for event in runtime.audit.list(limit=50) if str(event.type) == "memory.pii_scrubbed"]
    blob = str(events[0].payload)

    assert "123.456.789-09" not in blob
    assert "sigiloso@exemplo.com" not in blob
    assert "cpf" in blob


def test_scrub_can_be_turned_off_or_relaxed(runtime, workspace):
    off = _runtime(workspace, scrub_pii=False)
    try:
        record = off.memory.write("cpf 123.456.789-09")
        assert "123.456.789-09" in record.content
    finally:
        off.close()

    relaxed = _runtime(workspace, pii_allow=["e-mail"])
    try:
        record = relaxed.memory.write("fale com joao@example.com, cpf 123.456.789-09")
        assert "joao@example.com" in record.content
        assert "[cpf removido]" in record.content
    finally:
        relaxed.close()


def test_scan_only_counts_without_revealing(runtime):
    counts = runtime.memory.scan("cpf 123.456.789-09 e 529.982.247-25")

    assert counts == {"cpf": 2}
    assert summarize({"cpf": 2}) == "2 cpf removidos"


def test_the_summary_of_a_record_is_scrubbed_too(runtime):
    record = runtime.memory.write("texto limpo", summary="contato joao@example.com")

    assert "joao@example.com" not in record.summary


def test_stats_reports_the_scrubbing(runtime):
    runtime.memory.write("cpf 123.456.789-09")

    state = runtime.memory.stats()["limpeza_pii"]

    assert state["ativa"] is True
    assert state["registros_com_pii_removido"] == 1
    assert state["tipos_liberados"] == []


# ----------------------------------------------------------------------
# memória multimodal
# ----------------------------------------------------------------------
def test_sniff_reads_the_type_from_the_bytes():
    assert sniff(PNG) == ("image/png", "png", "imagem")
    assert sniff(MP3) == ("audio/mpeg", "mp3", "áudio")
    assert sniff(PDF) == ("application/pdf", "pdf", "documento")
    # renomear não engana: o tipo vem do conteúdo
    assert sniff(b"\x4d\x5a" + b"\x00" * 100) is None


def test_media_goes_to_disk_and_the_text_becomes_searchable(runtime):
    record = runtime.memory.remember_media(PNG, caption="print do erro no painel", filename="erro.png")

    assert record.modality == "imagem"
    assert record.asset.mime == "image/png"
    assert (runtime.settings.workspace / record.asset.path).exists()
    assert "print do erro no painel" in record.content

    found = runtime.memory.search("erro no painel", limit=3)
    assert any(item.id == record.id for item in found)


def test_the_binary_never_goes_into_the_content(runtime):
    record = runtime.memory.remember_media(PNG, caption="legenda", filename="erro.png")

    assert b"\x89PNG" not in record.content.encode("utf-8", errors="ignore")
    stored = runtime.memory.repository.get(record.id)
    assert PNG not in stored.model_dump_json().encode("utf-8")


def test_media_without_caption_says_so(runtime):
    record = runtime.memory.remember_media(PNG, filename="sem-texto.png")

    assert "sem legenda" in record.content
    assert record.asset.caption == ""


def test_media_respects_the_size_ceiling(runtime, workspace):
    tight = _runtime(workspace, max_media_bytes=100)
    try:
        with pytest.raises(ConfigError, match="teto"):
            tight.memory.remember_media(PNG, filename="grande.png")
    finally:
        tight.close()


def test_media_outside_the_allowlist_is_refused(runtime, workspace):
    strict = _runtime(workspace, media_mimes=["image/png"])
    try:
        with pytest.raises(ConfigError, match="fora da lista branca"):
            strict.memory.remember_media(MP3, filename="audio.mp3")
    finally:
        strict.close()


def test_unknown_bytes_are_refused(runtime):
    with pytest.raises(ConfigError, match="tipo não reconhecido"):
        runtime.memory.remember_media(b"\x4d\x5a" + b"\x00" * 100, filename="virus.png")


def test_media_can_be_disabled(runtime, workspace):
    off = _runtime(workspace, media_enabled=False)
    try:
        with pytest.raises(ConfigError, match="desligada"):
            off.memory.remember_media(PNG, filename="erro.png")
    finally:
        off.close()


def test_media_captions_are_scrubbed_too(runtime):
    record = runtime.memory.remember_media(PNG, caption="print enviado por joao@example.com")

    assert "joao@example.com" not in record.content
    assert record.metadata.get("pii_removido") == {"e-mail": 1}


def test_context_renders_the_media_reference(runtime):
    runtime.memory.remember_media(PNG, caption="painel com erro 500")

    rendered = runtime.memory.render(runtime.memory.search("painel", limit=3))

    assert "imagem" in rendered
    assert "painel com erro 500" in rendered


def test_media_status_reports_usage(runtime):
    runtime.memory.remember_media(PDF, caption="contrato assinado")

    state = runtime.memory.media_status()

    assert state["registros"] == 1
    assert state["disco_bytes"] > 0
    assert "application/pdf" in state["tipos"]
    assert runtime.memory.stats()["por_modalidade"]["documento"] == 1


def test_the_store_reads_back_what_it_wrote(runtime):
    asset = MediaStore(runtime.settings.workspace, config=runtime.memory.config).store(
        PNG, filename="x.png", caption="x"
    )

    payload = MediaStore(runtime.settings.workspace, config=runtime.memory.config).read(asset)

    assert payload == PNG


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def _cli(workspace):
    from typer.testing import CliRunner

    from egr.cli.commands.memory import app as memory_app

    return CliRunner(), memory_app


def test_cli_scrub_shows_what_would_go(workspace):
    runner, app = _cli(workspace)

    result = runner.invoke(app, ["scrub", "cpf 123.456.789-09", "-w", str(workspace)])

    assert result.exit_code == 0, result.output
    assert "1 cpf" in result.output
    assert "123.456.789-09" not in result.output


def test_cli_add_media_and_status(workspace):
    runner, app = _cli(workspace)
    source = workspace / "erro.png"
    source.write_bytes(PNG)

    added = runner.invoke(
        app, ["add-media", str(source), "--caption", "print do erro 500", "-w", str(workspace)]
    )
    assert added.exit_code == 0, added.output
    assert "mídia memorizada" in added.output

    state = runner.invoke(app, ["media", "-w", str(workspace)])
    assert state.exit_code == 0
    assert "ativa" in state.output


def test_cli_write_warns_about_the_scrubbing(workspace):
    runner, app = _cli(workspace)

    result = runner.invoke(
        app, ["write", "contato joao@example.com", "-w", str(workspace)]
    )

    assert result.exit_code == 0, result.output
    assert "removido" in result.output


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------
def test_api_scrub_and_media_routes(runtime):
    from fastapi.testclient import TestClient

    from egr.api.server import create_app

    client = TestClient(create_app(runtime))

    scrubbed = client.post("/v1/memory/scrub", json={"text": "cpf 123.456.789-09"})
    assert scrubbed.status_code == 200
    assert scrubbed.json()["removido"] == {"cpf": 1}
    assert "123.456.789-09" not in scrubbed.json()["texto"]

    uploaded = client.post(
        "/v1/memory/media",
        json={
            "content": base64.b64encode(PNG).decode("ascii"),
            "filename": "erro.png",
            "caption": "print do erro 500",
        },
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["mídia"]["tipo"] == "image/png"

    refused = client.post(
        "/v1/memory/media",
        json={"content": base64.b64encode(b"\x4d\x5a" + b"\x00" * 50).decode("ascii"), "filename": "x.exe"},
    )
    assert refused.status_code == 422

    broken = client.post("/v1/memory/media", json={"content": "nao-e-base64", "filename": "x.png"})
    assert broken.status_code == 422

    state = client.get("/v1/memory/media")
    assert state.status_code == 200
    assert state.json()["registros"] == 1


def test_memory_config_defaults():
    config = EGRConfig()

    assert config.memory.scrub_pii is True
    assert config.memory.pii_allow == []
    assert config.memory.media_enabled is True
    assert config.memory.max_media_bytes == 5 * 1024 * 1024
    assert "image/png" in config.memory.media_mimes
