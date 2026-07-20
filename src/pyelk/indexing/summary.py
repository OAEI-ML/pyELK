"""Canonical private compiler summaries shared with the native ingestion gate."""

from __future__ import annotations

from hashlib import blake2b
from typing import Final, Protocol

from pyelk.indexing.ir import CompiledOntology

_PREFIX: Final = b"pyelk:compiled-ontology-digest:v1\x00"
_SECTION_NAMES: Final = (
    "entities",
    "expressions",
    "expression_occurrences",
    "property_occurrences",
    "property_chains",
    "subclass_axioms",
    "equivalent_class_axioms",
    "disjoint_groups",
    "subproperty_axioms",
    "property_ranges",
    "feature_counts",
    "source_fingerprint",
)


class _Digest(Protocol):
    def update(self, value: bytes) -> None: ...


def compiler_section_counts(compiled: CompiledOntology) -> dict[str, int]:
    """Return the exact row counts covered by :func:`compiler_digest`."""

    if not isinstance(compiled, CompiledOntology):
        raise TypeError("compiled must be a CompiledOntology")
    return {
        "entities": len(compiled.entities),
        "expressions": len(compiled.expressions),
        "expression_occurrences": len(compiled.expression_occurrences),
        "property_occurrences": len(compiled.property_occurrences),
        "property_chains": len(compiled.property_chains),
        "subclass_axioms": len(compiled.subclass_axioms),
        "equivalent_class_axioms": len(compiled.equivalent_class_axioms),
        "disjoint_groups": len(compiled.disjoint_groups),
        "subproperty_axioms": len(compiled.subproperty_axioms),
        "property_ranges": len(compiled.property_ranges),
        "feature_counts": len(compiled.feature_counts),
        "source_fingerprint": 1,
    }


def compiler_digest(compiled: CompiledOntology) -> bytes:
    """Hash canonical compiler records without serializing the private IR container."""

    counts = compiler_section_counts(compiled)
    digest = blake2b(digest_size=32)
    digest.update(_PREFIX)

    _section(digest, "entities", counts)
    for entity in compiled.entities:
        digest.update(bytes((int(entity.kind),)))
        _frame(digest, entity.iri.encode("utf-8"))

    _section(digest, "expressions", counts)
    for expression in compiled.expressions:
        digest.update(bytes((int(expression.tag),)))
        _frame(digest, expression.payload)
        _length(digest, len(expression.arguments))
        for argument in expression.arguments:
            digest.update(int(argument).to_bytes(4, "little"))

    _section(digest, "expression_occurrences", counts)
    for expression_occurrence in compiled.expression_occurrences:
        digest.update(expression_occurrence.negative.to_bytes(8, "little"))
        digest.update(expression_occurrence.positive.to_bytes(8, "little"))

    _section(digest, "property_occurrences", counts)
    for property_occurrence in compiled.property_occurrences:
        digest.update(property_occurrence.negative.to_bytes(8, "little"))
        digest.update(property_occurrence.positive.to_bytes(8, "little"))

    _u32_rows(digest, "property_chains", compiled.property_chains, counts)
    _pairs(digest, "subclass_axioms", compiled.subclass_axioms, counts)
    _pairs(
        digest,
        "equivalent_class_axioms",
        compiled.equivalent_class_axioms,
        counts,
    )
    _u32_rows(digest, "disjoint_groups", compiled.disjoint_groups, counts)
    _pairs(digest, "subproperty_axioms", compiled.subproperty_axioms, counts)
    _pairs(digest, "property_ranges", compiled.property_ranges, counts)

    _section(digest, "feature_counts", counts)
    for count in compiled.feature_counts:
        digest.update(count.to_bytes(8, "little"))

    _section(digest, "source_fingerprint", counts)
    _frame(digest, compiled.source_fingerprint)
    assert tuple(counts) == _SECTION_NAMES
    return digest.digest()


def _u32_rows(
    digest: _Digest,
    name: str,
    rows: tuple[tuple[int, ...], ...],
    counts: dict[str, int],
) -> None:
    _section(digest, name, counts)
    for row in rows:
        _length(digest, len(row))
        for value in row:
            digest.update(int(value).to_bytes(4, "little"))


def _pairs(
    digest: _Digest,
    name: str,
    rows: tuple[tuple[int, int], ...],
    counts: dict[str, int],
) -> None:
    _section(digest, name, counts)
    for first, second in rows:
        digest.update(int(first).to_bytes(4, "little"))
        digest.update(int(second).to_bytes(4, "little"))


def _section(digest: _Digest, name: str, counts: dict[str, int]) -> None:
    _frame(digest, name.encode("ascii"))
    _length(digest, counts[name])


def _frame(digest: _Digest, value: bytes) -> None:
    _length(digest, len(value))
    digest.update(value)


def _length(digest: _Digest, value: int) -> None:
    digest.update(value.to_bytes(8, "little"))


__all__ = ["compiler_digest", "compiler_section_counts"]
