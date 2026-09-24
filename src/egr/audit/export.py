"""Pacote de evidência: exportar a trilha de forma verificável offline.

`egr audit export` gera JSONL (fidelidade total) ou CSV (planilha do auditor)
com manifesto + eventos + hashes. `egr audit verify-export` confere o arquivo
sem banco: recomputa cada hash, confere o encadeamento quando a faixa é
contígua e valida o manifesto.

Subconjuntos filtrados não têm como provar encadeamento (há lacunas): o
veredito continua válido se nenhum evento foi adulterado, mas `complete`
só é verdadeiro para a exportação total íntegra até o `ledger_head`.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from ..core.hashing import GENESIS, chain_hash
from ..core.timeutil import iso, utcnow
from ..domain.event import Event

COLUMNS = (
    "seq",
    "id",
    "type",
    "actor",
    "task_id",
    "agent_id",
    "environment",
    "created_at",
    "prev_hash",
    "hash",
    "payload",
)


def event_dict(event: Event) -> dict[str, Any]:
    """Forma canônica do evento na exportação (a mesma que o hash cobre)."""
    created = event.created_at
    return {
        "seq": event.seq,
        "id": event.id,
        "type": str(event.type),
        "actor": event.actor,
        "task_id": event.task_id,
        "agent_id": event.agent_id,
        "environment": str(event.environment),
        "created_at": iso(created) if not isinstance(created, str) else created,
        "prev_hash": event.prev_hash,
        "hash": event.hash,
        "payload": event.payload or {},
    }


def _hash_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": data["id"],
        "type": data["type"],
        "actor": data["actor"],
        "task_id": data.get("task_id"),
        "agent_id": data.get("agent_id"),
        "environment": data["environment"],
        "payload": data.get("payload") or {},
        "created_at": data["created_at"],
    }


def build_manifest(
    events: list[dict[str, Any]],
    *,
    format: str,
    filters: dict[str, Any],
    ledger_head: str | None,
    exporter: str = "cli",
) -> dict[str, Any]:
    seqs = [e["seq"] for e in events if e.get("seq") is not None]
    contiguous = bool(seqs) and seqs == list(range(seqs[0], seqs[0] + len(seqs)))
    from_genesis = bool(events) and events[0].get("prev_hash") == GENESIS
    return {
        "generator": "egr audit export",
        "format": format,
        "exported_at": iso(utcnow()),
        "exporter": exporter,
        "filters": {k: v for k, v in filters.items() if v is not None},
        "events": len(events),
        "first_seq": seqs[0] if seqs else None,
        "last_seq": seqs[-1] if seqs else None,
        "contiguous": contiguous,
        "from_genesis": from_genesis,
        "ledger_head": ledger_head,
    }


def to_jsonl(manifest: dict[str, Any], events: list[dict[str, Any]]) -> str:
    lines = [json.dumps({"_export": manifest}, ensure_ascii=False)]
    lines += [json.dumps(e, ensure_ascii=False) for e in events]
    return "\n".join(lines) + "\n"


def to_csv(manifest: dict[str, Any], events: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    buf.write("# egr audit export\n")
    for key in (
        "exported_at",
        "exporter",
        "format",
        "events",
        "first_seq",
        "last_seq",
        "contiguous",
        "from_genesis",
        "ledger_head",
    ):
        buf.write(f"# {key}: {manifest.get(key)}\n")
    buf.write(f"# filters: {json.dumps(manifest.get('filters') or {}, ensure_ascii=False)}\n")
    writer = csv.DictWriter(buf, fieldnames=list(COLUMNS))
    writer.writeheader()
    for event in events:
        row = dict(event)
        row["payload"] = json.dumps(row.get("payload") or {}, ensure_ascii=False)
        writer.writerow(row)
    return buf.getvalue()


def _parse_csv_manifest(comment_lines: list[str]) -> dict[str, Any]:
    manifest: dict[str, Any] = {"generator": "egr audit export", "filters": {}}
    for line in comment_lines:
        if ":" not in line:
            continue
        key, _, value = line[1:].strip().partition(":")
        key, value = key.strip(), value.strip()
        if key == "filters":
            try:
                manifest[key] = json.loads(value)
            except ValueError:
                manifest[key] = {}
        elif key in ("events", "first_seq", "last_seq"):
            manifest[key] = int(value) if value not in ("", "None") else (0 if key == "events" else None)
        elif key in ("contiguous", "from_genesis"):
            manifest[key] = value == "True"
        elif key in ("exported_at", "exporter", "format", "ledger_head"):
            manifest[key] = None if value == "None" else value
    manifest.setdefault("events", 0)
    return manifest


def _read_export(path: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    text = Path(path).read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    comments = [ln for ln in lines if ln.startswith("#")]
    data = [ln for ln in lines if not ln.startswith("#")]
    if data and data[0].lstrip().startswith('{"_export"'):
        manifest = json.loads(data[0])["_export"]
        events = [json.loads(ln) for ln in data[1:]]
        return manifest, events
    manifest = _parse_csv_manifest(comments)
    events = []
    reader = csv.DictReader(io.StringIO("\n".join(data)))
    for row in reader:
        row["seq"] = int(row["seq"]) if row.get("seq") not in (None, "") else None
        for key in ("task_id", "agent_id"):
            if row.get(key) == "":
                row[key] = None
        row["payload"] = json.loads(row.get("payload") or "{}")
        events.append(row)
    return manifest, events


def verify_export_file(path: str | Path) -> dict[str, Any]:
    """Verifica um arquivo exportado, sem banco. Nunca levanta para conteúdo ruim."""
    try:
        manifest, events = _read_export(path)
    except FileNotFoundError:
        raise
    except Exception as exc:  # arquivo ilegível: veredito, não exceção
        return {
            "valid": False,
            "events": 0,
            "error": f"arquivo ilegível: {exc}",
            "broken": [],
        }
    seqs = [e["seq"] for e in events if e.get("seq") is not None]
    contiguous = bool(seqs) and seqs == list(range(seqs[0], seqs[0] + len(seqs)))
    broken: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        try:
            expected = chain_hash(event.get("prev_hash"), _hash_payload(event))
        except Exception as exc:
            broken.append({"index": index, "id": event.get("id"), "reason": f"hash: {exc}"})
            continue
        if expected != event.get("hash"):
            broken.append({"index": index, "id": event.get("id"), "reason": "hash mismatch"})
            continue
        # Subconjunto filtrado tem lacunas por construção: encadeamento só
        # é exigível em faixa contígua; conteúdo é sempre exigível.
        if contiguous and index > 0 and event.get("prev_hash") != events[index - 1].get("hash"):
            broken.append({"index": index, "id": event.get("id"), "reason": "linkage"})
    manifest_ok = manifest.get("events") == len(events)
    if not manifest_ok:
        broken.append({"index": -1, "id": None, "reason": "manifest count"})
    from_genesis = bool(events) and events[0].get("prev_hash") == GENESIS
    complete = (
        not broken
        and bool(events)
        and contiguous
        and from_genesis
        and events[-1].get("hash") == manifest.get("ledger_head")
    )
    return {
        "valid": not broken,
        "events": len(events),
        "first_seq": seqs[0] if seqs else None,
        "last_seq": seqs[-1] if seqs else None,
        "contiguous": contiguous,
        "linkage_checked": contiguous,
        "from_genesis": from_genesis,
        "complete": complete,
        "manifest_ok": manifest_ok,
        "broken": broken,
    }
