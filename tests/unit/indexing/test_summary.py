from __future__ import annotations

from pyelk.indexing.ir import FEATURE_VECTOR_LENGTH
from pyelk.indexing.summary import compiler_digest, compiler_section_counts
from tests.helpers.contracts import TinyCompiledOntologyBuilder


def test_compiler_summary_covers_every_private_ir_ledger() -> None:
    compiled = (
        TinyCompiledOntologyBuilder()
        .add_subclass("urn:summary:A", "urn:summary:B")
        .add_object_property("urn:summary:p")
        .build()
    )
    counts = compiler_section_counts(compiled)
    assert counts == {
        "entities": len(compiled.entities),
        "expressions": len(compiled.expressions),
        "expression_occurrences": len(compiled.expression_occurrences),
        "property_occurrences": len(compiled.property_occurrences),
        "property_chains": len(compiled.property_chains),
        "subclass_axioms": 1,
        "equivalent_class_axioms": 0,
        "disjoint_groups": 0,
        "subproperty_axioms": 0,
        "property_ranges": 0,
        "feature_counts": FEATURE_VECTOR_LENGTH,
        "source_fingerprint": 1,
    }
    assert len(compiler_digest(compiled)) == 32
    assert compiler_digest(compiled) == compiler_digest(compiled)


def test_compiler_digest_changes_with_a_covered_section() -> None:
    baseline = TinyCompiledOntologyBuilder().add_class("urn:summary:A").build()
    changed = (
        TinyCompiledOntologyBuilder().add_class("urn:summary:A").set_feature_count(0, 1).build()
    )
    assert compiler_section_counts(baseline) == compiler_section_counts(changed)
    assert compiler_digest(baseline) != compiler_digest(changed)
