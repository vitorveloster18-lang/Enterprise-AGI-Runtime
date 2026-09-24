"""Telas da interface."""

from __future__ import annotations

__all__ = [
    "ApprovalsScreen",
    "ArgsForm",
    "CommandPalette",
    "HelpScreen",
    "ProvidersScreen",
    "SettingsScreen",
]

from .approvals import ApprovalsScreen
from .help import HelpScreen
from .palette import ArgsForm, CommandPalette
from .providers import ProvidersScreen
from .settings import SettingsScreen
