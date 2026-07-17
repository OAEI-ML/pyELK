from __future__ import annotations

import importlib.util
import shutil
import struct
import sys
from collections.abc import Iterable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import blake2b
from pathlib import Path
from types import ModuleType

import pytest

from pyelk.indexing.compiler import compile_ontology
from pyelk.indexing.ir import CompiledOntology, ExpressionTag
from pyelk.inputs import capture_input
from pyelk.reasoning.contexts import FrozenContext
from pyelk.reasoning.saturation import SaturationSnapshot
from pyelk.reasoning.session import SaturationSession
from tests.helpers.contracts import TinyCompiledOntologyBuilder
from tests.integration.test_pure_reasoner import (
    _CLASS_CASES,
    _PROPERTY_CASES,
    _REALIZATION_CASES,
    _UPSTREAM,
    _snapshot,
)


def _native_library() -> Path:
    root = Path(__file__).parents[2]
    installed = importlib.util.find_spec("pyelk._native")
    if installed is not None and installed.origin is not None:
        candidate = Path(installed.origin).resolve()
        if root not in candidate.parents and candidate.is_file():
            return candidate
    for profile in ("release", "debug"):
        for filename in ("lib_native.dylib", "lib_native.so"):
            candidate = root / "target" / profile / filename
            if candidate.is_file():
                return candidate
    pytest.skip("build the pyelk-pyo3 crate before running native-backend tests")


@pytest.fixture(scope="session")
def native_differential_module(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ModuleType]:
    destination = tmp_path_factory.mktemp("pyelk-native-differential") / "_native.so"
    shutil.copy2(_native_library(), destination)
    spec = importlib.util.spec_from_file_location("pyelk._native", destination)
    if spec is None or spec.loader is None:
        pytest.fail(f"could not create an import spec for {destination}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get("pyelk._native")
    sys.modules["pyelk._native"] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        if previous is None:
            sys.modules.pop("pyelk._native", None)
        else:
            sys.modules["pyelk._native"] = previous


def _u32(output: bytearray, value: int) -> None:
    output.extend(struct.pack("<I", value))


def _row(output: bytearray, values: Iterable[int]) -> None:
    items = tuple(values)
    _u32(output, len(items))
    for item in items:
        _u32(output, item)


def _map(output: bytearray, values: Mapping[int, Iterable[int]]) -> None:
    items = tuple(sorted(values.items()))
    _u32(output, len(items))
    for key, members in items:
        _u32(output, key)
        _row(output, members)


def _context(output: bytearray, context: FrozenContext) -> None:
    _u32(output, context.root)
    output.append(context.inconsistent)
    _row(output, context.composed_subsumers)
    _row(output, context.decomposed_subsumers)
    _map(output, dict(context.forward_links))
    _map(output, dict(context.backward_links))
    _map(output, dict(context.propagations))
    _map(output, dict(context.disjoint_positions))
    _row(output, context.initialized_subcontexts)


def _encode_python_snapshot(snapshot: SaturationSnapshot) -> bytes:
    output = bytearray(b"PYELKDBG")
    output.extend(struct.pack("<H", 1))
    _u32(output, len(snapshot.property_subsumers))
    for row in snapshot.property_subsumers:
        _row(output, row)
    _u32(output, len(snapshot.property_ranges))
    for row in snapshot.property_ranges:
        _row(output, row)
    output.append(snapshot.inconsistent_ontology)
    _u32(output, len(snapshot.contexts))
    for context in snapshot.contexts.values():
        _context(output, context)
    return bytes(output)


def _python_debug(compiled: CompiledOntology, *, realize: bool = False) -> bytes:
    session = SaturationSession(compiled)
    snapshot = session.ensure_realized() if realize else session.ensure_classified()
    return _encode_python_snapshot(snapshot)


def _native_debug(
    native: ModuleType,
    compiled: CompiledOntology,
    *,
    workers: int = 1,
    realize: bool = False,
) -> bytes:
    session = native.create_session(compiled.encode(), workers)
    try:
        return session.debug_snapshot(realize=realize, limit=1_000_000)
    finally:
        session.close()


def _compile_fixture(relative: str) -> CompiledOntology:
    snapshot = _snapshot(_UPSTREAM / relative)
    return compile_ontology(capture_input(snapshot).ontology.view)


_CLASS_DEBUG_CASES = tuple((name, f"classification/{name}.owl", False) for name in _CLASS_CASES)
_PROPERTY_DEBUG_CASES = tuple(
    (name, f"classification/object_property/{name}.owl", False) for name in _PROPERTY_CASES
)
_REALIZATION_DEBUG_CASES = tuple(
    (name, f"realization/{name}.owl", True) for name in _REALIZATION_CASES
)


@pytest.mark.parametrize(
    ("name", "relative", "realize"),
    _CLASS_DEBUG_CASES + _PROPERTY_DEBUG_CASES + _REALIZATION_DEBUG_CASES,
)
def test_upstream_fixed_point_snapshot_matches_python(
    native_differential_module: ModuleType,
    name: str,
    relative: str,
    realize: bool,
) -> None:
    del name
    compiled = _compile_fixture(relative)
    assert _native_debug(native_differential_module, compiled, realize=realize) == _python_debug(
        compiled, realize=realize
    )


def _generated_ontology(case: int) -> CompiledOntology:
    builder = TinyCompiledOntologyBuilder()
    state = (case + 1) * 0x9E37_79B1

    def next_value() -> int:
        nonlocal state
        state = (state * 1_664_525 + 1_013_904_223) & 0xFFFF_FFFF
        return state

    count = 1 + next_value() % 5
    classes = [f"urn:generated:{case}:C{index}" for index in range(count)]
    for iri in classes:
        builder.add_class(iri)
    properties = [f"urn:generated:{case}:p{index}" for index in range(1 + next_value() % 3)]
    for iri in properties:
        builder.add_object_property(iri)
    for sub in classes:
        for super_iri in classes:
            if sub != super_iri and next_value() % 5 == 0:
                builder.add_subclass(sub, super_iri)
    compiled = builder.build()
    entity_ids = {record.iri: index for index, record in enumerate(compiled.entities)}
    expression_ids = {
        compiled.entities[record.arguments[0]].iri: index
        for index, record in enumerate(compiled.expressions)
        if record.tag is ExpressionTag.CLASS
    }
    custom_expressions = tuple(expression_ids[iri] for iri in classes)
    equivalent = {
        tuple(sorted((first, second)))
        for first in custom_expressions
        for second in custom_expressions
        if first != second and next_value() % 11 == 0
    }
    disjoint = {
        tuple(sorted((first, second)))
        for first in custom_expressions
        for second in custom_expressions
        if first < second and next_value() % 13 == 0
    }
    chain_ids = {chain: index for index, chain in enumerate(compiled.property_chains)}
    custom_properties = tuple(entity_ids[iri] for iri in properties)
    subproperties = {
        (chain_ids[(sub_property,)], super_property)
        for sub_property in custom_properties
        for super_property in custom_properties
        if sub_property != super_property and next_value() % 7 == 0
    }
    ranges = {
        (property_id, expression_id)
        for property_id in custom_properties
        for expression_id in custom_expressions
        if next_value() % 9 == 0
    }
    return replace(
        compiled,
        equivalent_class_axioms=tuple(sorted(equivalent)),
        disjoint_groups=tuple(sorted(disjoint)),
        subproperty_axioms=tuple(sorted(subproperties)),
        property_ranges=tuple(sorted(ranges)),
        source_fingerprint=blake2b(f"generated-full:{case}".encode(), digest_size=32).digest(),
    )


def test_ten_thousand_generated_fixed_points_match_python(
    native_differential_module: ModuleType,
) -> None:
    for case in range(10_000):
        compiled = _generated_ontology(case)
        assert _native_debug(native_differential_module, compiled) == _python_debug(compiled), case


def test_debug_snapshot_is_bounded(native_differential_module: ModuleType) -> None:
    compiled = _generated_ontology(0)
    session = native_differential_module.create_session(compiled.encode(), 1)
    try:
        with pytest.raises(RuntimeError, match="exceeding limit"):
            session.debug_snapshot(limit=1)
    finally:
        session.close()


def test_workers_and_repeated_runs_are_byte_deterministic(
    native_differential_module: ModuleType,
) -> None:
    builder = TinyCompiledOntologyBuilder()
    for index in range(160):
        builder.add_class(f"urn:stress:C{index}")
    for index in range(159):
        builder.add_subclass(f"urn:stress:C{index}", f"urn:stress:C{index + 1}")
    for index in range(0, 154, 7):
        builder.add_subclass(f"urn:stress:C{index}", f"urn:stress:C{index + 6}")
    compiled = builder.build()
    expected = _native_debug(native_differential_module, compiled, workers=1)
    for workers in (0, 1, 2, 4):
        for _ in range(3):
            assert _native_debug(native_differential_module, compiled, workers=workers) == expected


def test_concurrent_session_calls_do_not_strand_work_or_deadlock(
    native_differential_module: ModuleType,
) -> None:
    compiled = _generated_ontology(77)
    baseline = native_differential_module.create_session(compiled.encode(), 1)
    candidate = native_differential_module.create_session(compiled.encode(), 4)
    try:
        expected = (
            baseline.class_taxonomy(),
            baseline.object_property_taxonomy(),
            baseline.realization(),
            baseline.debug_snapshot(realize=True),
        )
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = (
                executor.submit(candidate.class_taxonomy),
                executor.submit(candidate.object_property_taxonomy),
                executor.submit(candidate.realization),
                executor.submit(candidate.debug_snapshot, True),
            )
            assert tuple(future.result(timeout=20) for future in futures) == expected
    finally:
        baseline.close()
        candidate.close()


def test_canonical_builder_output_is_insertion_order_independent() -> None:
    edges = (("urn:A", "urn:B"), ("urn:B", "urn:C"), ("urn:A", "urn:C"))
    forward = TinyCompiledOntologyBuilder()
    reverse = TinyCompiledOntologyBuilder()
    for edge in edges:
        forward.add_subclass(*edge)
    for edge in reversed(edges):
        reverse.add_subclass(*edge)
    assert forward.build().encode() == reverse.build().encode()
