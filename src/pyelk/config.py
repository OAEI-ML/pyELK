"""Immutable public configuration for one :class:`pyelk.Reasoner`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class ReasonerConfig:
    """Configuration snapshotted when a reasoner session is created."""

    backend: Literal["auto", "python", "rust"] = "auto"
    workers: int = 0
    allow_fresh_entities: bool = True
    unsupported: Literal["ignore", "error"] = "ignore"
    allow_incomplete_imports: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.backend, str) or self.backend not in {"auto", "python", "rust"}:
            raise ValueError("backend must be 'auto', 'python', or 'rust'")
        if isinstance(self.workers, bool) or not isinstance(self.workers, int):
            raise TypeError("workers must be an integer")
        if self.workers < 0:
            raise ValueError("workers must be nonnegative")
        if not isinstance(self.allow_fresh_entities, bool):
            raise TypeError("allow_fresh_entities must be a boolean")
        if not isinstance(self.unsupported, str) or self.unsupported not in {"ignore", "error"}:
            raise ValueError("unsupported must be 'ignore' or 'error'")
        if not isinstance(self.allow_incomplete_imports, bool):
            raise TypeError("allow_incomplete_imports must be a boolean")


__all__ = ["ReasonerConfig"]
