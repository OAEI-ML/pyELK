from __future__ import annotations

from typing import cast

import pytest

from pyelk.backends.python import PythonBackendFactory, PythonBackendSession
from pyelk.config import ReasonerConfig
from pyelk.exceptions import ReasonerClosedError
from pyelk.reasoning.contracts import BackendConfig, QueryKind
from tests.helpers.contracts import TinyCompiledOntologyBuilder, assert_taxonomy_valid


def test_python_backend_is_lazy_cached_and_reports_single_worker() -> None:
    compiled = TinyCompiledOntologyBuilder().add_subclass("urn:backend#A", "urn:backend#B").build()
    session = PythonBackendFactory(
        native_available=True,
        fallback_reason="test fallback",
    ).create_session(compiled, cast(BackendConfig, ReasonerConfig(workers=8)))
    assert isinstance(session, PythonBackendSession)
    assert session.info.name == "python"
    assert session.info.requested_workers == 8
    assert session.info.effective_workers == 1
    assert session.info.native_available is True
    assert session.info.fallback_reason == "test fallback"
    assert session.diagnostics()["stage"] == "compiled"

    first = session.class_taxonomy()
    assert_taxonomy_valid(first)
    assert session.class_taxonomy() is first
    assert session.diagnostics()["class_taxonomy_cached"] is True
    properties = session.object_property_taxonomy()
    assert_taxonomy_valid(properties)
    assert session.realization() is session.realization()
    assert session.query_class_expression(None, QueryKind.SATISFIABLE, False).boolean is True
    assert session.entails(None) is False


def test_python_backend_close_is_idempotent_and_terminal() -> None:
    compiled = TinyCompiledOntologyBuilder().build()
    session = PythonBackendFactory().create_session(compiled, cast(BackendConfig, ReasonerConfig()))
    session.close()
    session.close()
    for operation in (
        lambda: session.info,
        session.is_inconsistent,
        session.class_taxonomy,
        session.object_property_taxonomy,
        session.realization,
        session.diagnostics,
    ):
        with pytest.raises(ReasonerClosedError):
            operation()
