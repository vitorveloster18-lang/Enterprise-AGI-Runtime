"""Cognitive Extensions: specialised capabilities the Runtime can call.

The Runtime stays in control: an extension is a Tool/Provider, never core code.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Extension(BaseModel):
    id: str
    name: str = ""
    kind: str = "tool"  # tool | provider | algorithm | agent
    entrypoint: str = ""  # python import path or HTTP endpoint
    capabilities: list[str] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)
    enabled: bool = True
    version: str = "0.1.0"
