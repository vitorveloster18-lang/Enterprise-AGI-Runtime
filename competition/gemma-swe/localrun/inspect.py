#!/usr/bin/env python3
"""Descreve o dataset baixado para adaptar o harness. Uso: `python inspect.py [DATA_DIR]`.

Mande a saída inteira no chat: a partir dela eu ajusto tasks.py/graph.py
aos formatos reais (chaves da task, snapshots, grafos, embeddings).
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path


def tree(root: Path, depth: int = 2) -> None:
    print(f"# {root} ({'AUSENTE' if not root.exists() else 'ok'})")
    if not root.exists():
        return
    items = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name))
    for item in items[:40]:
        if item.is_file():
            print(f"  f {item.name} ({item.stat().st_size} bytes)")
        else:
            inside = sum(1 for _ in item.iterdir())
            print(f"  d {item.name}/ ({inside} itens)")
            if depth > 1:
                for sub in sorted(item.iterdir())[:8]:
                    print(f"    - {sub.name}{'/' if sub.is_dir() else ''}")


def sample_jsonl(path: Path) -> None:
    print(f"\n# {path.name}")
    if not path.exists():
        print("  AUSENTE")
        return
    with open(path, encoding="utf-8") as fh:
        lines = [line for line in fh if line.strip()]
    print(f"  linhas: {len(lines)}")
    if not lines:
        return
    first = json.loads(lines[0])
    print(f"  chaves: {sorted(first)}")
    for key, value in first.items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        print(f"  - {key}: {str(text)[:160]}")


def sample_dir(path: Path, pattern: str, head: int = 300) -> None:
    print(f"\n# {path}")
    if not path.is_dir():
        print("  AUSENTE")
        return
    files = sorted(path.glob(pattern))[:5]
    print(f"  arquivos {pattern}: {len(files)} (amostra)")
    for item in files:
        print(f"  - {item.name} ({item.stat().st_size} bytes)")
        try:
            if item.suffix == ".npz":
                with zipfile.ZipFile(item) as zf:
                    print(f"    npz: {zf.namelist()[:6]}")
            else:
                raw = item.read_bytes()[:head]
                print(f"    início: {raw[:head]!r}")
        except Exception as exc:  # noqa: BLE001 - inspeção não pode quebrar
            print(f"    (não li: {exc})")


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
    tree(root)
    tree(root / "sample_submission")
    sample_jsonl(root / "tasks.jsonl")
    sample_dir(root / "graphs", "*")
    sample_dir(root / "embeddings", "*")
    sample_dir(root / "snapshots", "*")
    print("\n# fim — cole tudo acima no chat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
