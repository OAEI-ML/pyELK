"""Stable exception categories shared by all pyELK components."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


class PyElkError(Exception):
    """Base class for all recoverable pyELK library errors."""


class ParseError(PyElkError):
    """A positioned Functional Syntax parse failure."""

    def __init__(
        self,
        source: str | None,
        line: int,
        column: int,
        token: str | None,
        detail: str,
    ) -> None:
        if line < 1 or column < 1:
            raise ValueError("parse error line and column must be one-based")
        self.source = source
        self.line = line
        self.column = column
        self.token = token
        self.detail = detail
        location = f"{source or '<input>'}:{line}:{column}"
        token_text = "" if token is None else f" near {token!r}"
        super().__init__(f"{location}: {detail}{token_text}")


class UnsupportedFeatureError(PyElkError):
    """Strict ontology compilation rejected an unsupported feature."""

    def __init__(self, feature: str, axiom: object) -> None:
        self.feature = feature
        self.axiom = axiom
        super().__init__(f"unsupported ontology feature {feature}: {axiom!r}")


class UnsupportedQueryError(PyElkError):
    """Strict query compilation rejected an unsupported query feature."""

    def __init__(self, feature: str, query: object) -> None:
        self.feature = feature
        self.query = query
        super().__init__(f"unsupported query feature {feature}: {query!r}")


class UnresolvedImportError(PyElkError):
    """An ontology import closure was not supplied by the caller."""

    def __init__(self, imports: Iterable[object]) -> None:
        self.imports = tuple(imports)
        super().__init__(f"unresolved ontology imports: {self.imports!r}")


class FreshEntityError(PyElkError):
    """A query used fresh entities while fresh entities were disabled."""

    def __init__(self, entities: Iterable[object]) -> None:
        self.entities = tuple(entities)
        super().__init__(f"fresh entities are disabled: {self.entities!r}")


class IncompleteReasoningError(PyElkError):
    """A caller required a complete value from an incomplete result."""

    def __init__(self, reasons: Iterable[object]) -> None:
        self.reasons = tuple(reasons)
        super().__init__(f"reasoning result is potentially incomplete: {self.reasons!r}")


class BackendUnavailableError(PyElkError):
    """An explicitly requested backend cannot be used."""

    def __init__(self, requested: str, reason: str) -> None:
        self.requested = requested
        self.reason = reason
        super().__init__(f"backend {requested!r} is unavailable: {reason}")


class BackendProtocolError(PyElkError):
    """A compiled-IR or raw-result payload violates the frozen protocol."""

    def __init__(self, expected: str, actual: Any) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"backend protocol violation: expected {expected}; got {actual!r}")


class ReasonerClosedError(PyElkError):
    """A reasoning operation was attempted on a closed session."""

    def __init__(self) -> None:
        super().__init__("reasoner session is closed")


class InternalReasonerError(PyElkError):
    """A backend failed internally while executing a named stage."""

    def __init__(self, stage: str, backend: str, detail: str) -> None:
        self.stage = stage
        self.backend = backend
        self.detail = detail
        super().__init__(f"{backend} backend failed during {stage}: {detail}")
