from __future__ import annotations

from typing import cast

import pyowl_core as owl
import pytest

from pyelk.exceptions import IncompleteReasoningError
from pyelk.reasoning.contracts import CompletenessIssue, ReasoningTask
from pyelk.result import EntityNode, InstanceTaxonomy, ReasoningResult, Taxonomy


def _class(name: str) -> owl.Class:
    return owl.Class(owl.IRI(f"urn:result#{name}"))


def _individual(name: str) -> owl.NamedIndividual:
    return owl.NamedIndividual(owl.IRI(f"urn:result#{name}"))


def _issue(feature: str) -> CompletenessIssue:
    return CompletenessIssue(
        ReasoningTask.CLASS_TAXONOMY,
        (feature,),
        (feature.title(),),
        ("ANY",),
    )


def _taxonomy() -> Taxonomy[owl.Class]:
    bottom = EntityNode((owl.OWL_NOTHING,))
    child = EntityNode((_class("B"), _class("A")))
    parent = EntityNode((_class("C"),))
    top = EntityNode((owl.OWL_THING,))
    return Taxonomy(
        nodes=(top, child, bottom, parent, child),
        direct_edges=((child, parent), (parent, top), (bottom, child), (child, parent)),
        top=top,
        bottom=bottom,
    )


def test_reasoning_result_canonicalizes_reasons_and_requires_completeness() -> None:
    first = _issue("B")
    second = _issue("A")
    value = ReasoningResult(7, False, (first, second, first))
    assert value.reasons == (second, first)
    with pytest.raises(IncompleteReasoningError) as caught:
        value.require_complete()
    assert caught.value.reasons == value.reasons
    assert ReasoningResult(8, True, ()).require_complete() == 8


@pytest.mark.parametrize(
    ("complete", "reasons"),
    [(True, (_issue("A"),)), (False, ())],
)
def test_reasoning_result_enforces_complete_equivalence(
    complete: bool, reasons: tuple[CompletenessIssue, ...]
) -> None:
    with pytest.raises(ValueError, match="exactly"):
        ReasoningResult(None, complete, reasons)


def test_entity_node_is_nonempty_deduplicated_and_structurally_sorted() -> None:
    first = _class("long-name")
    second = _class("x")
    node = EntityNode((first, second, first))
    assert node.members == tuple(sorted({first, second}, key=owl.canonical_bytes))
    assert node.canonical_member is node.members[0]
    with pytest.raises(ValueError, match="empty"):
        EntityNode[owl.Class](())
    with pytest.raises(TypeError, match="Entity"):
        EntityNode[owl.Entity]((first, cast(owl.Entity, "not-an-entity")))


def test_taxonomy_canonicalizes_and_answers_direct_and_transitive_relatives() -> None:
    taxonomy = _taxonomy()
    child = taxonomy.node(_class("A"))
    assert child is not None and _class("B") in child.members
    assert taxonomy.node(_class("missing")) is None
    assert taxonomy.supers(_class("A"), direct=True) == (taxonomy.node(_class("C")),)
    assert set(taxonomy.supers(_class("A"))) == {
        taxonomy.node(_class("C")),
        taxonomy.top,
    }
    assert taxonomy.subs(_class("C"), direct=True) == (child,)
    assert set(taxonomy.subs(_class("C"))) == {child, taxonomy.bottom}
    assert taxonomy.subs(_class("missing")) == ()
    assert len(taxonomy.nodes) == 4
    assert len(taxonomy.direct_edges) == 3


def test_taxonomy_rejects_mixed_kinds_cycles_and_foreign_bounds() -> None:
    class_node = EntityNode((_class("A"),))
    individual_node = EntityNode((_individual("i"),))
    with pytest.raises(ValueError, match="one entity kind"):
        Taxonomy[owl.Entity](
            cast(tuple[EntityNode[owl.Entity], ...], (class_node, individual_node)),
            cast(
                tuple[tuple[EntityNode[owl.Entity], EntityNode[owl.Entity]], ...],
                ((class_node, individual_node),),
            ),
            cast(EntityNode[owl.Entity], individual_node),
            cast(EntityNode[owl.Entity], class_node),
        )
    second = EntityNode((_class("B"),))
    top = EntityNode((_class("Top"),))
    bottom = EntityNode((_class("Bottom"),))
    with pytest.raises(ValueError, match="acyclic"):
        Taxonomy(
            (bottom, class_node, second, top),
            (
                (bottom, class_node),
                (class_node, second),
                (second, class_node),
                (second, top),
            ),
            top,
            bottom,
        )
    with pytest.raises(ValueError, match="bounds"):
        Taxonomy((class_node,), (), second, class_node)


def test_instance_taxonomy_canonicalizes_edges_and_validates_membership() -> None:
    taxonomy = _taxonomy()
    instance = EntityNode((_individual("b"), _individual("a")))
    class_node = taxonomy.node(_class("A"))
    assert class_node is not None
    value = InstanceTaxonomy(
        taxonomy,
        (instance,),
        ((instance, class_node), (instance, class_node)),
    )
    assert value.instances == (instance,)
    assert value.direct_types == ((instance, class_node),)
    with pytest.raises(ValueError, match="at least one direct type"):
        InstanceTaxonomy(taxonomy, (instance,), ())
    with pytest.raises(TypeError, match="NamedIndividual"):
        InstanceTaxonomy(
            taxonomy,
            (EntityNode((_class("wrong"),)),),  # type: ignore[arg-type]
            (),
        )
