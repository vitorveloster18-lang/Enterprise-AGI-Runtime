"""Pacote de evidência: exportar a trilha de forma verificável offline.

Fatia 4 do fechamento do runtime: `audit export` gera JSONL/CSV com manifesto,
lacra a exportação na própria trilha (`audit.exported`) e `audit verify-export`
confere o arquivo sem banco.
"""

from __future__ import annotations

import hashlib
import json

from typer.testing import CliRunner

from egr.audit.export import (
    build_manifest,
    event_dict,
    to_csv,
    to_jsonl,
    verify_export_file,
)
from egr.cli.main import app as cli_app

runner = CliRunner()


def _seed(runtime):
    runtime.audit.record("task.created", actor="cli", task_id="tsk_1")
    runtime.audit.record("task.completed", actor="runtime-agent", task_id="tsk_1")
    runtime.audit.record(
        "human.decision",
        actor="gerente-fin",
        task_id="tsk_2",
        payload={"decision": "approved", "identity_verified": True},
    )


def _package(runtime, tmp_path, format="jsonl", **filters):
    events = [event_dict(e) for e in runtime.audit.scan(**filters)]
    head_row = runtime.db.query_one("SELECT hash FROM events ORDER BY seq DESC LIMIT 1")
    manifest = build_manifest(
        events, format=format, filters=filters, ledger_head=head_row["hash"] if head_row else None
    )
    content = to_jsonl(manifest, events) if format == "jsonl" else to_csv(manifest, events)
    path = tmp_path / f"evidencia.{format}"
    path.write_text(content, encoding="utf-8")
    return path, manifest, events


def test_jsonl_round_trip_completo(runtime, tmp_path):
    _seed(runtime)
    path, manifest, _ = _package(runtime, tmp_path)

    result = verify_export_file(path)

    assert result["valid"]
    assert result["events"] == 3
    assert result["contiguous"] and result["from_genesis"] and result["complete"]
    assert manifest["ledger_head"] == runtime.audit.verify()["head"]


def test_csv_round_trip_completo(runtime, tmp_path):
    _seed(runtime)
    path, _, _ = _package(runtime, tmp_path, format="csv")

    result = verify_export_file(path)

    assert result["valid"]
    assert result["events"] == 3
    assert result["complete"]


def test_adulteracao_de_conteudo_reprova(runtime, tmp_path):
    _seed(runtime)
    path, _, _ = _package(runtime, tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[2])
    tampered["payload"]["decision"] = "denied"
    lines[2] = json.dumps(tampered, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = verify_export_file(path)

    assert not result["valid"]
    assert result["broken"]
    assert result["broken"][0]["reason"] == "hash mismatch"


def test_filtrado_valido_mas_nao_completo(runtime, tmp_path):
    _seed(runtime)
    runtime.audit.record("task.created", actor="cli", task_id="tsk_9")
    pontas, _, _ = _package(runtime, tmp_path, type="task.created")
    result = verify_export_file(pontas)
    assert result["events"] == 2
    assert result["valid"] and not result["contiguous"] and not result["complete"]

    meio, _, _ = _package(runtime, tmp_path, type="task.completed")
    result = verify_export_file(meio)
    assert result["valid"] and not result["complete"] and not result["from_genesis"]


def test_manifesto_com_contagem_errada_reprova(runtime, tmp_path):
    _seed(runtime)
    path, _, _ = _package(runtime, tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    manifest = json.loads(lines[0])
    manifest["_export"]["events"] = 99
    lines[0] = json.dumps(manifest, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = verify_export_file(path)

    assert not result["valid"]
    assert any(b["reason"] == "manifest count" for b in result["broken"])


def test_export_vazio_valido(runtime, tmp_path):
    path, manifest, _ = _package(runtime, tmp_path, type="tipo.inexistente")

    result = verify_export_file(path)

    assert result["valid"]
    assert result["events"] == 0 and manifest["events"] == 0
    assert not result["complete"]


def test_scan_filtra_e_ordena(runtime):
    _seed(runtime)
    por_task = list(runtime.audit.scan(task_id="tsk_1"))
    assert [e.seq for e in por_task] == sorted(e.seq for e in por_task)
    assert len(por_task) == 2

    assert len(list(runtime.audit.scan(type="human.decision"))) == 1
    assert len(list(runtime.audit.scan(actor="gerente-fin"))) == 1
    assert len(list(runtime.audit.scan(since="2000-01-01T00:00:00"))) == 3
    assert len(list(runtime.audit.scan(until="2000-01-01T00:00:00"))) == 0


def test_cli_export_arquivo_lacra_na_trilha(workspace, tmp_path):
    out = tmp_path / "trilha.jsonl"
    exported = runner.invoke(
        cli_app, ["audit", "export", "--out", str(out), "--workspace", str(workspace)]
    )
    assert exported.exit_code == 0, exported.output
    assert out.exists()

    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    shown = runner.invoke(
        cli_app,
        ["audit", "show", "--type", "audit.exported", "--json", "--workspace", str(workspace)],
    )
    assert shown.exit_code == 0, shown.output
    seals = json.loads(shown.stdout)
    assert seals and seals[0]["payload"]["sha256"] == digest
    assert seals[0]["payload"]["target"] == str(out)


def test_cli_stdout_e_verify_export(workspace, tmp_path):
    exported = runner.invoke(cli_app, ["audit", "export", "--workspace", str(workspace)])
    assert exported.exit_code == 0, exported.output
    first = exported.stdout.splitlines()[0]
    assert json.loads(first)["_export"]["generator"] == "egr audit export"

    path = tmp_path / "stdout.jsonl"
    path.write_text(exported.stdout, encoding="utf-8")
    checked = runner.invoke(cli_app, ["audit", "verify-export", str(path)])
    assert checked.exit_code == 0, checked.output

    lines = exported.stdout.splitlines()
    manifest = json.loads(lines[0])
    manifest["_export"]["events"] = 999
    lines[0] = json.dumps(manifest, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tampered = runner.invoke(cli_app, ["audit", "verify-export", str(path)])
    assert tampered.exit_code == 1


def test_csv_com_payload_dificil(runtime, tmp_path):
    runtime.audit.record(
        "note.test",
        actor="cli",
        payload={"texto": 'vírgula, "aspas", quebra\nde linha e çãõ', "n": 1},
    )
    path, _, _ = _package(runtime, tmp_path, format="csv")

    result = verify_export_file(path)

    assert result["valid"]
    content = path.read_text(encoding="utf-8")
    assert "vírgula" in content and "çãõ" in content
