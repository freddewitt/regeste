"""Interactive CLI facade — calls the core, implements no business logic (spec §1)."""

from __future__ import annotations

from .app import run

__all__ = ["run"]
