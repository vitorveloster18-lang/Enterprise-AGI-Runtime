"""Fase 12 — Vertical Packs.

    catálogo (pacote Python + packs/*.yaml do workspace)
        → verificação (requisitos e colisões)
        → proposta (Fase 7)
        → aplicação (único escritor)
        → avaliação (Fase 8) antes de promover

Pack é atalho para começar, não para governar: ele entra pelo mesmo portão de
qualquer mudança — proposta verificada, aprovação humana e trilha.
"""

from .catalog import available_packs, load_pack_dir, load_pack_file
from .service import PackService

__all__ = ["PackService", "available_packs", "load_pack_dir", "load_pack_file"]
