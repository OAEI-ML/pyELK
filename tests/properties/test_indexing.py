from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from pyelk.indexing.builder import IndexTransaction
from pyelk.indexing.compiler import compile_ontology
from pyelk.indexing.ir import CompiledOntology, EntityKind, EntityRecord, ExpressionTag
from pyelk.indexing.polarity import IndexPolarity
from tests.unit.indexing._support import load_functional

_NAME = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=8)


@settings(max_examples=30, deadline=None)
@given(
    data=st.data(),
    names=st.sets(_NAME, min_size=2, max_size=8),
)
def test_compile_freeze_roundtrip_is_insertion_order_independent(
    data: st.DataObject,
    names: set[str],
) -> None:
    ordered_names = sorted(names)
    axioms = tuple(
        [*(f"Declaration(Class(:{name}))" for name in ordered_names)]
        + [
            f"SubClassOf(:{ordered_names[index]} :{ordered_names[index + 1]})"
            for index in range(len(ordered_names) - 1)
        ]
    )
    permutation = data.draw(st.permutations(axioms), label="axiom-order")
    baseline = compile_ontology(
        load_functional(" ".join(axioms), ontology_iri="urn:indexing-property")
    )
    permuted = compile_ontology(
        load_functional("\n".join(permutation), ontology_iri="urn:indexing-property")
    )
    assert permuted.encode() == baseline.encode()
    assert CompiledOntology.decode(permuted.encode()) == permuted


@settings(max_examples=40, deadline=None)
@given(iri=_NAME, payload=st.binary(min_size=0, max_size=12))
def test_structural_interning_identity_excludes_polarity_but_includes_payload(
    iri: str,
    payload: bytes,
) -> None:
    transaction = IndexTransaction()
    entity = EntityRecord(EntityKind.CLASS, f"urn:property#{iri}")
    negative = transaction.intern_expression(
        ExpressionTag.CLASS,
        entities=(entity,),
        payload=payload,
        polarity=IndexPolarity.NEGATIVE,
    )
    positive = transaction.intern_expression(
        ExpressionTag.CLASS,
        entities=(entity,),
        payload=payload,
        polarity=IndexPolarity.POSITIVE,
    )
    different = transaction.intern_expression(
        ExpressionTag.CLASS,
        entities=(entity,),
        payload=payload + b"x",
        polarity=IndexPolarity.NEUTRAL,
    )
    assert positive == negative
    assert transaction.expression_occurrences[negative] == [1, 1]
    assert different != negative


@settings(max_examples=24, deadline=None)
@given(ghost=_NAME, leaf=_NAME.filter(lambda value: value != "ghost"))
def test_unsupported_axiom_never_leaks_random_ghost_entities(ghost: str, leaf: str) -> None:
    compiled = compile_ontology(
        load_functional(
            f"SubClassOf(ObjectIntersectionOf(:{ghost} ObjectAllValuesFrom(:p :{leaf})) :Target)"
        )
    )
    iris = {record.iri for record in compiled.entities}
    assert f"urn:test#{ghost}" not in iris
    assert f"urn:test#{leaf}" not in iris
    assert "urn:test#Target" not in iris
    assert "urn:test#p" not in iris
