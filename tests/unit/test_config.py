from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from pyelk.config import ReasonerConfig


def test_defaults_and_frozen_slots_contract() -> None:
    config = ReasonerConfig()
    assert config == ReasonerConfig(
        backend="auto",
        workers=0,
        allow_fresh_entities=True,
        unsupported="ignore",
        allow_incomplete_imports=False,
    )
    assert not hasattr(config, "__dict__")
    with pytest.raises(FrozenInstanceError):
        config.workers = 2  # type: ignore[misc]


@pytest.mark.parametrize("backend", ["", "native", "Python", 1])
def test_invalid_backend_is_rejected(backend: object) -> None:
    with pytest.raises(ValueError):
        ReasonerConfig(backend=backend)  # type: ignore[arg-type]


@pytest.mark.parametrize("workers", [-1, -100])
def test_negative_workers_are_rejected(workers: int) -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        ReasonerConfig(workers=workers)


@pytest.mark.parametrize("workers", [True, 1.5, "2"])
def test_non_integer_workers_are_rejected(workers: object) -> None:
    with pytest.raises(TypeError, match="integer"):
        ReasonerConfig(workers=workers)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["allow_fresh_entities", "allow_incomplete_imports"])
def test_boolean_flags_require_exact_booleans(field: str) -> None:
    with pytest.raises(TypeError, match="boolean"):
        ReasonerConfig(**{field: 1})  # type: ignore[arg-type]


@pytest.mark.parametrize("unsupported", ["warn", "", 1])
def test_unsupported_policy_is_validated(unsupported: object) -> None:
    with pytest.raises(ValueError):
        ReasonerConfig(unsupported=unsupported)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [True, 1.5, "100"])
def test_query_cache_requires_integer_bytes(value: object) -> None:
    with pytest.raises(TypeError, match="query_cache_bytes"):
        ReasonerConfig(query_cache_bytes=value)  # type: ignore[arg-type]


def test_query_cache_accepts_zero_and_rejects_negative_bytes() -> None:
    assert ReasonerConfig(query_cache_bytes=0).query_cache_bytes == 0
    with pytest.raises(ValueError, match="query_cache_bytes"):
        ReasonerConfig(query_cache_bytes=-1)
