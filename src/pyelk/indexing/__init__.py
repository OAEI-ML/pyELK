"""Backend-neutral ELK-compatible indexing contracts.

The compiler exports are loaded lazily so consumers of the standalone IR codec do not need
to import the OWL model dependency merely to decode backend data.
"""

from typing import TYPE_CHECKING, Any

from pyelk.indexing.encoded import (
    ENCODED_SCHEMA_NAME,
    ENCODED_SCHEMA_VERSION,
    EncodedStructuralHandoff,
    EncodedViewNegotiation,
    negotiate_encoded_structural_view,
)
from pyelk.indexing.ir import (
    FEATURE_VECTOR_LENGTH,
    FINGERPRINT_SIZE,
    CompiledOntology,
    CompiledQuery,
    DisjointGroupId,
    EntityId,
    EntityKind,
    EntityRecord,
    ExpressionId,
    ExpressionOccurrence,
    ExpressionRecord,
    ExpressionTag,
    PropertyChainId,
    PropertyOccurrence,
    QueryEntityRecord,
    QueryIR,
    QueryIRKind,
    ReadableBuffer,
)
from pyelk.indexing.registration import (
    REGISTRATION_BY_KEY,
    RULE_REGISTRATIONS,
    OccurrenceTrigger,
    RegistrationSource,
    RuleRegistration,
    registrations_for,
)

if TYPE_CHECKING:
    from pyelk.indexing.compiler import (
        COMPILER_SCHEMA_VERSION,
        DEFAULT_MAX_NODES_PER_AXIOM,
        ELK_COMPATIBILITY_ID,
        CompiledSymbolTable,
        SymbolTableView,
        compile_entailment_query,
        compile_ontology,
        compile_query_expression,
        symbol_table,
    )

_COMPILER_EXPORTS = frozenset(
    {
        "COMPILER_SCHEMA_VERSION",
        "DEFAULT_MAX_NODES_PER_AXIOM",
        "ELK_COMPATIBILITY_ID",
        "CompiledSymbolTable",
        "SymbolTableView",
        "compile_entailment_query",
        "compile_ontology",
        "compile_query_expression",
        "symbol_table",
    }
)


def __getattr__(name: str) -> Any:
    if name not in _COMPILER_EXPORTS:
        raise AttributeError(name)
    from pyelk.indexing import compiler

    return getattr(compiler, name)


__all__ = [
    "COMPILER_SCHEMA_VERSION",
    "DEFAULT_MAX_NODES_PER_AXIOM",
    "ELK_COMPATIBILITY_ID",
    "ENCODED_SCHEMA_NAME",
    "ENCODED_SCHEMA_VERSION",
    "FEATURE_VECTOR_LENGTH",
    "FINGERPRINT_SIZE",
    "REGISTRATION_BY_KEY",
    "RULE_REGISTRATIONS",
    "CompiledOntology",
    "CompiledQuery",
    "CompiledSymbolTable",
    "DisjointGroupId",
    "EncodedStructuralHandoff",
    "EncodedViewNegotiation",
    "EntityId",
    "EntityKind",
    "EntityRecord",
    "ExpressionId",
    "ExpressionOccurrence",
    "ExpressionRecord",
    "ExpressionTag",
    "OccurrenceTrigger",
    "PropertyChainId",
    "PropertyOccurrence",
    "QueryEntityRecord",
    "QueryIR",
    "QueryIRKind",
    "ReadableBuffer",
    "RegistrationSource",
    "RuleRegistration",
    "SymbolTableView",
    "compile_entailment_query",
    "compile_ontology",
    "compile_query_expression",
    "negotiate_encoded_structural_view",
    "registrations_for",
    "symbol_table",
]
