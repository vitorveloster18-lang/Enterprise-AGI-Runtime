"""Enterprise: the tenant. Everything in EGR belongs to an Enterprise."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from ..core.timeutil import utcnow


class EnterpriseSettings(BaseModel):
    data_residency: Literal["local", "region", "any"] = "local"
    external_ai: Literal["allowed", "restricted", "forbidden"] = "allowed"
    audit_retention_days: int = 365
    default_environment: Literal["development", "staging", "production"] = "development"


class Enterprise(BaseModel):
    id: str = "local"
    name: str = "Local Enterprise"
    settings: EnterpriseSettings = Field(default_factory=EnterpriseSettings)
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def external_allowed(self) -> bool:
        return self.settings.external_ai != "forbidden"
