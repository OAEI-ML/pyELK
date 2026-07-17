from __future__ import annotations

from pyelk.indexing.ir import EntityId, ExpressionId
from pyelk.reasoning.conclusions import (
    ContextInitialization,
    SubClassInclusionComposed,
    SubClassInclusionDecomposed,
    SubContextInitialization,
)
from pyelk.reasoning.rules import (
    context_initialization_no_premises,
    subclass_inclusion_composed_defined_class,
    subclass_inclusion_composed_of_decomposed,
    subclass_inclusion_expanded_definition,
    subclass_inclusion_expanded_first_equivalent_class,
    subclass_inclusion_expanded_second_equivalent_class,
    subclass_inclusion_expanded_subclass_of,
    subclass_inclusion_owl_thing,
    subclass_inclusion_tautology,
    subcontext_initialization_no_premises,
)

_ROOT = ExpressionId(1)
_FIRST = ExpressionId(2)
_SECOND = ExpressionId(3)


def test_context_initialization_no_premises_inference() -> None:
    assert context_initialization_no_premises(_ROOT) == ContextInitialization(_ROOT)


def test_subcontext_initialization_no_premises_inference() -> None:
    assert subcontext_initialization_no_premises(_ROOT, EntityId(4)) == SubContextInitialization(
        _ROOT, EntityId(4)
    )


def test_subclass_inclusion_tautology_inference() -> None:
    premise = ContextInitialization(_ROOT)
    assert subclass_inclusion_tautology(premise) == SubClassInclusionDecomposed(_ROOT, _ROOT)
    assert subclass_inclusion_tautology(None) is None


def test_subclass_inclusion_owl_thing_inference() -> None:
    premise = ContextInitialization(_ROOT)
    assert subclass_inclusion_owl_thing(premise, _SECOND) == SubClassInclusionComposed(
        _ROOT, _SECOND
    )
    assert subclass_inclusion_owl_thing(None, _SECOND) is None


def test_subclass_inclusion_expanded_subclass_of_inference() -> None:
    premise = SubClassInclusionComposed(_ROOT, _FIRST)
    assert subclass_inclusion_expanded_subclass_of(
        premise, _FIRST, _SECOND
    ) == SubClassInclusionDecomposed(_ROOT, _SECOND)
    assert subclass_inclusion_expanded_subclass_of(None, _FIRST, _SECOND) is None
    assert subclass_inclusion_expanded_subclass_of(premise, _SECOND, _FIRST) is None


def test_subclass_inclusion_expanded_definition_inference() -> None:
    premise = SubClassInclusionDecomposed(_ROOT, _FIRST)
    assert subclass_inclusion_expanded_definition(
        premise, _FIRST, _SECOND
    ) == SubClassInclusionDecomposed(_ROOT, _SECOND)
    assert subclass_inclusion_expanded_definition(None, _FIRST, _SECOND) is None
    assert subclass_inclusion_expanded_definition(premise, _SECOND, _FIRST) is None


def test_subclass_inclusion_expanded_first_equivalent_class_inference() -> None:
    premise = SubClassInclusionComposed(_ROOT, _SECOND)
    assert subclass_inclusion_expanded_first_equivalent_class(
        premise, _FIRST, _SECOND
    ) == SubClassInclusionDecomposed(_ROOT, _FIRST)
    assert subclass_inclusion_expanded_first_equivalent_class(None, _FIRST, _SECOND) is None
    assert subclass_inclusion_expanded_first_equivalent_class(premise, _SECOND, _FIRST) is None


def test_subclass_inclusion_expanded_second_equivalent_class_inference() -> None:
    premise = SubClassInclusionComposed(_ROOT, _FIRST)
    assert subclass_inclusion_expanded_second_equivalent_class(
        premise, _FIRST, _SECOND
    ) == SubClassInclusionDecomposed(_ROOT, _SECOND)
    assert subclass_inclusion_expanded_second_equivalent_class(None, _FIRST, _SECOND) is None
    assert subclass_inclusion_expanded_second_equivalent_class(premise, _SECOND, _FIRST) is None


def test_subclass_inclusion_composed_defined_class_inference() -> None:
    premise = SubClassInclusionComposed(_ROOT, _SECOND)
    assert subclass_inclusion_composed_defined_class(
        premise, _SECOND, _FIRST
    ) == SubClassInclusionComposed(_ROOT, _FIRST)
    assert subclass_inclusion_composed_defined_class(None, _SECOND, _FIRST) is None
    assert subclass_inclusion_composed_defined_class(premise, _FIRST, _SECOND) is None


def test_subclass_inclusion_composed_of_decomposed_inference() -> None:
    premise = SubClassInclusionDecomposed(_ROOT, _FIRST)
    assert subclass_inclusion_composed_of_decomposed(premise) == SubClassInclusionComposed(
        _ROOT, _FIRST
    )
    assert subclass_inclusion_composed_of_decomposed(None) is None
