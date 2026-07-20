//! Defensive structural validation for pyowl-core encoded-view schema 1.
//!
//! This module borrows the eleven public columns directly.  It establishes the
//! shape, bounds, scalar arena, root-category, canonical dense ordering,
//! reachability, and acyclic graph invariants before the ELK-specific compiler
//! allocates permanent IR.  It does not advertise schema support; semantic
//! compilation remains a separate gate.

use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet};

use crate::error::{CoreError, CoreResult};
use crate::ir::{
    Entity, EntityKind, Expression, ExpressionTag, FEATURE_VECTOR_LENGTH,
    OWL_BOTTOM_OBJECT_PROPERTY_IRI, OWL_NOTHING_IRI, OWL_THING_IRI, OWL_TOP_OBJECT_PROPERTY_IRI,
    Occurrence, Ontology,
};

/// Frozen SHA-256 of the canonical pyowl-core structural-columns v1 descriptor.
pub const DESCRIPTOR_SHA256_V1: [u8; 32] = [
    0x9a, 0xd2, 0x9d, 0xb6, 0xa7, 0xe6, 0x16, 0xf6, 0x5c, 0xea, 0x29, 0x57, 0xbc, 0x5b, 0xa8, 0xd1,
    0xf9, 0xb9, 0x9e, 0xf0, 0xeb, 0x1f, 0xe1, 0x43, 0x2c, 0x09, 0xbe, 0x25, 0x78, 0x62, 0x67, 0xb5,
];

const ROOT_ONTOLOGY_ANNOTATION: u8 = 1;
const ROOT_AXIOM: u8 = 2;
const ROOT_EXTENSION: u8 = 3;

const COMPONENT_NONE: u8 = 0;
const COMPONENT_NODE: u8 = 1;
const COMPONENT_TEXT: u8 = 2;
const COMPONENT_BYTES: u8 = 3;
const COMPONENT_INTEGER: u8 = 4;
const COMPONENT_ENUM: u8 = 5;
const COMPONENT_SET: u8 = 6;
const COMPONENT_SEQUENCE: u8 = 7;

// Frozen pyELK compiler feature-vector positions shared with indexing/conversion.py.
const FEATURE_OBJECT_PROPERTY_CHAIN: usize = 40;
const FEATURE_OBJECT_PROPERTY_RANGE: usize = 41;
const FEATURE_DIFFERENT_INDIVIDUALS: usize = 15;
const FEATURE_DISJOINT_CLASSES: usize = 16;
const FEATURE_DISJOINT_UNION: usize = 19;
const FEATURE_OBJECT_HAS_VALUE_POSITIVE: usize = 34;
const FEATURE_OBJECT_PROPERTY_ASSERTION: usize = 39;
const FEATURE_OWL_NOTHING_POSITIVE: usize = 43;
const FEATURE_REFLEXIVE_OBJECT_PROPERTY: usize = 44;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum EntityKindRole {
    Class,
    Datatype,
    ObjectProperty,
    DataProperty,
    AnnotationProperty,
    NamedIndividual,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum NodeRole {
    Iri,
    Entity,
    Class,
    Datatype,
    ObjectProperty,
    DataProperty,
    AnnotationProperty,
    Literal,
    Annotation,
    ObjectPropertyExpression,
    SubObjectPropertyExpression,
    FacetRestriction,
    DataRange,
    ClassExpression,
    Individual,
    AnnotationValue,
    AnnotationSubject,
    IndividualArgument,
    DataArgument,
    Atom,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum FieldRole {
    Scalar(u8),
    EntityKind,
    OptionalText,
    Node(NodeRole),
    Set(NodeRole),
    Sequence(NodeRole),
}

const TEXT: FieldRole = FieldRole::Scalar(COMPONENT_TEXT);
const BYTES: FieldRole = FieldRole::Scalar(COMPONENT_BYTES);
const INTEGER: FieldRole = FieldRole::Scalar(COMPONENT_INTEGER);
const ENTITY_KIND: FieldRole = FieldRole::EntityKind;
const OPTIONAL_TEXT: FieldRole = FieldRole::OptionalText;

const N_IRI: FieldRole = FieldRole::Node(NodeRole::Iri);
const N_ENTITY: FieldRole = FieldRole::Node(NodeRole::Entity);
const N_CLASS: FieldRole = FieldRole::Node(NodeRole::Class);
const N_DATATYPE: FieldRole = FieldRole::Node(NodeRole::Datatype);
const N_OBJECT_PROPERTY: FieldRole = FieldRole::Node(NodeRole::ObjectProperty);
const N_DATA_PROPERTY: FieldRole = FieldRole::Node(NodeRole::DataProperty);
const N_ANNOTATION_PROPERTY: FieldRole = FieldRole::Node(NodeRole::AnnotationProperty);
const N_LITERAL: FieldRole = FieldRole::Node(NodeRole::Literal);
const N_OBJECT_PROPERTY_EXPRESSION: FieldRole = FieldRole::Node(NodeRole::ObjectPropertyExpression);
const N_SUB_OBJECT_PROPERTY_EXPRESSION: FieldRole =
    FieldRole::Node(NodeRole::SubObjectPropertyExpression);
const N_DATA_RANGE: FieldRole = FieldRole::Node(NodeRole::DataRange);
const N_CLASS_EXPRESSION: FieldRole = FieldRole::Node(NodeRole::ClassExpression);
const N_INDIVIDUAL: FieldRole = FieldRole::Node(NodeRole::Individual);
const N_ANNOTATION_VALUE: FieldRole = FieldRole::Node(NodeRole::AnnotationValue);
const N_ANNOTATION_SUBJECT: FieldRole = FieldRole::Node(NodeRole::AnnotationSubject);
const N_INDIVIDUAL_ARGUMENT: FieldRole = FieldRole::Node(NodeRole::IndividualArgument);
const N_DATA_ARGUMENT: FieldRole = FieldRole::Node(NodeRole::DataArgument);

const SET_ANNOTATION: FieldRole = FieldRole::Set(NodeRole::Annotation);
const SET_DATA_RANGE: FieldRole = FieldRole::Set(NodeRole::DataRange);
const SET_LITERAL: FieldRole = FieldRole::Set(NodeRole::Literal);
const SET_FACET_RESTRICTION: FieldRole = FieldRole::Set(NodeRole::FacetRestriction);
const SET_CLASS_EXPRESSION: FieldRole = FieldRole::Set(NodeRole::ClassExpression);
const SET_INDIVIDUAL: FieldRole = FieldRole::Set(NodeRole::Individual);
const SET_OBJECT_PROPERTY_EXPRESSION: FieldRole =
    FieldRole::Set(NodeRole::ObjectPropertyExpression);
const SET_DATA_PROPERTY: FieldRole = FieldRole::Set(NodeRole::DataProperty);
const SET_ATOM: FieldRole = FieldRole::Set(NodeRole::Atom);

const SEQUENCE_OBJECT_PROPERTY_EXPRESSION: FieldRole =
    FieldRole::Sequence(NodeRole::ObjectPropertyExpression);
const SEQUENCE_DATA_PROPERTY: FieldRole = FieldRole::Sequence(NodeRole::DataProperty);
const SEQUENCE_DATA_ARGUMENT: FieldRole = FieldRole::Sequence(NodeRole::DataArgument);

macro_rules! constructor_role_ledger {
    ($( $tag:literal => [$($role:expr),* $(,)?]),+ $(,)?) => {
        fn constructor_roles(tag: u16) -> Option<&'static [FieldRole]> {
            match tag {
                $($tag => Some(&[$($role),*]),)+
                _ => None,
            }
        }

        #[cfg(test)]
        const CONSTRUCTOR_ROLE_LEDGER: &[(u16, &[FieldRole])] = &[
            $(($tag, &[$($role),*]),)+
        ];
    };
}

// Generated from pyowl-core model schema 1 constructor annotations and the
// frozen structural-columns descriptor. One row is retained for every tag so
// an arity-preserving kind or node-category substitution cannot cross the ABI.
constructor_role_ledger! {
    1 => [TEXT],
    2 => [ENTITY_KIND, N_IRI],
    3 => [BYTES, BYTES],
    4 => [TEXT, N_DATATYPE, OPTIONAL_TEXT],
    5 => [N_ANNOTATION_PROPERTY, N_ANNOTATION_VALUE, SET_ANNOTATION],
    10 => [N_OBJECT_PROPERTY],
    11 => [SEQUENCE_OBJECT_PROPERTY_EXPRESSION],
    20 => [N_IRI, N_LITERAL],
    21 => [SET_DATA_RANGE],
    22 => [SET_DATA_RANGE],
    23 => [N_DATA_RANGE],
    24 => [SET_LITERAL],
    25 => [N_DATATYPE, SET_FACET_RESTRICTION],
    30 => [SET_CLASS_EXPRESSION],
    31 => [SET_CLASS_EXPRESSION],
    32 => [N_CLASS_EXPRESSION],
    33 => [SET_INDIVIDUAL],
    34 => [N_OBJECT_PROPERTY_EXPRESSION, N_CLASS_EXPRESSION],
    35 => [N_OBJECT_PROPERTY_EXPRESSION, N_CLASS_EXPRESSION],
    36 => [N_OBJECT_PROPERTY_EXPRESSION, N_INDIVIDUAL],
    37 => [N_OBJECT_PROPERTY_EXPRESSION],
    38 => [INTEGER, N_OBJECT_PROPERTY_EXPRESSION, N_CLASS_EXPRESSION],
    39 => [INTEGER, N_OBJECT_PROPERTY_EXPRESSION, N_CLASS_EXPRESSION],
    40 => [INTEGER, N_OBJECT_PROPERTY_EXPRESSION, N_CLASS_EXPRESSION],
    41 => [SEQUENCE_DATA_PROPERTY, N_DATA_RANGE],
    42 => [SEQUENCE_DATA_PROPERTY, N_DATA_RANGE],
    43 => [N_DATA_PROPERTY, N_LITERAL],
    44 => [INTEGER, N_DATA_PROPERTY, N_DATA_RANGE],
    45 => [INTEGER, N_DATA_PROPERTY, N_DATA_RANGE],
    46 => [INTEGER, N_DATA_PROPERTY, N_DATA_RANGE],
    60 => [N_ENTITY, SET_ANNOTATION],
    61 => [N_CLASS_EXPRESSION, N_CLASS_EXPRESSION, SET_ANNOTATION],
    62 => [SET_CLASS_EXPRESSION, SET_ANNOTATION],
    63 => [SET_CLASS_EXPRESSION, SET_ANNOTATION],
    64 => [N_CLASS, SET_CLASS_EXPRESSION, SET_ANNOTATION],
    70 => [N_SUB_OBJECT_PROPERTY_EXPRESSION, N_OBJECT_PROPERTY_EXPRESSION, SET_ANNOTATION],
    71 => [SET_OBJECT_PROPERTY_EXPRESSION, SET_ANNOTATION],
    72 => [SET_OBJECT_PROPERTY_EXPRESSION, SET_ANNOTATION],
    73 => [N_OBJECT_PROPERTY_EXPRESSION, N_OBJECT_PROPERTY_EXPRESSION, SET_ANNOTATION],
    74 => [N_OBJECT_PROPERTY_EXPRESSION, N_CLASS_EXPRESSION, SET_ANNOTATION],
    75 => [N_OBJECT_PROPERTY_EXPRESSION, N_CLASS_EXPRESSION, SET_ANNOTATION],
    76 => [N_OBJECT_PROPERTY_EXPRESSION, SET_ANNOTATION],
    77 => [N_OBJECT_PROPERTY_EXPRESSION, SET_ANNOTATION],
    78 => [N_OBJECT_PROPERTY_EXPRESSION, SET_ANNOTATION],
    79 => [N_OBJECT_PROPERTY_EXPRESSION, SET_ANNOTATION],
    80 => [N_OBJECT_PROPERTY_EXPRESSION, SET_ANNOTATION],
    81 => [N_OBJECT_PROPERTY_EXPRESSION, SET_ANNOTATION],
    82 => [N_OBJECT_PROPERTY_EXPRESSION, SET_ANNOTATION],
    90 => [N_DATA_PROPERTY, N_DATA_PROPERTY, SET_ANNOTATION],
    91 => [SET_DATA_PROPERTY, SET_ANNOTATION],
    92 => [SET_DATA_PROPERTY, SET_ANNOTATION],
    93 => [N_DATA_PROPERTY, N_CLASS_EXPRESSION, SET_ANNOTATION],
    94 => [N_DATA_PROPERTY, N_DATA_RANGE, SET_ANNOTATION],
    95 => [N_DATA_PROPERTY, SET_ANNOTATION],
    100 => [N_DATATYPE, N_DATA_RANGE, SET_ANNOTATION],
    101 => [N_CLASS_EXPRESSION, SET_OBJECT_PROPERTY_EXPRESSION, SET_DATA_PROPERTY, SET_ANNOTATION],
    110 => [SET_INDIVIDUAL, SET_ANNOTATION],
    111 => [SET_INDIVIDUAL, SET_ANNOTATION],
    112 => [N_CLASS_EXPRESSION, N_INDIVIDUAL, SET_ANNOTATION],
    113 => [N_OBJECT_PROPERTY_EXPRESSION, N_INDIVIDUAL, N_INDIVIDUAL, SET_ANNOTATION],
    114 => [N_OBJECT_PROPERTY_EXPRESSION, N_INDIVIDUAL, N_INDIVIDUAL, SET_ANNOTATION],
    115 => [N_DATA_PROPERTY, N_INDIVIDUAL, N_LITERAL, SET_ANNOTATION],
    116 => [N_DATA_PROPERTY, N_INDIVIDUAL, N_LITERAL, SET_ANNOTATION],
    120 => [N_ANNOTATION_PROPERTY, N_ANNOTATION_SUBJECT, N_ANNOTATION_VALUE, SET_ANNOTATION],
    121 => [N_ANNOTATION_PROPERTY, N_ANNOTATION_PROPERTY, SET_ANNOTATION],
    122 => [N_ANNOTATION_PROPERTY, N_IRI, SET_ANNOTATION],
    123 => [N_ANNOTATION_PROPERTY, N_IRI, SET_ANNOTATION],
    140 => [N_IRI],
    141 => [N_CLASS_EXPRESSION, N_INDIVIDUAL_ARGUMENT],
    142 => [N_DATA_RANGE, N_DATA_ARGUMENT],
    143 => [N_OBJECT_PROPERTY_EXPRESSION, N_INDIVIDUAL_ARGUMENT, N_INDIVIDUAL_ARGUMENT],
    144 => [N_DATA_PROPERTY, N_INDIVIDUAL_ARGUMENT, N_DATA_ARGUMENT],
    145 => [N_IRI, SEQUENCE_DATA_ARGUMENT],
    146 => [N_INDIVIDUAL_ARGUMENT, N_INDIVIDUAL_ARGUMENT],
    147 => [N_INDIVIDUAL_ARGUMENT, N_INDIVIDUAL_ARGUMENT],
    148 => [SET_ATOM, SET_ATOM, SET_ANNOTATION],
}

/// Minimal immutable byte-source contract used by both Rust slices and PyO3
/// read-only buffer cells. Implementations must return one stable byte for the
/// duration of a validation call.
pub trait ByteSource: Copy {
    fn len(self) -> usize;
    fn byte(self, index: usize) -> Option<u8>;

    fn is_empty(self) -> bool {
        self.len() == 0
    }
}

impl ByteSource for &[u8] {
    fn len(self) -> usize {
        <[u8]>::len(self)
    }

    fn byte(self, index: usize) -> Option<u8> {
        self.get(index).copied()
    }
}

/// Borrowed public encoded-view columns, each exposed through one immutable
/// byte-source type. No input column is copied by validation.
#[derive(Clone, Copy, Debug)]
pub struct EncodedColumns<B: ByteSource> {
    pub root_kinds: B,
    pub root_ids: B,
    pub node_tags: B,
    pub node_field_offsets: B,
    pub field_kinds: B,
    pub field_values: B,
    pub field_lengths: B,
    pub item_kinds: B,
    pub item_values: B,
    pub item_lengths: B,
    pub scalar_bytes: B,
}

/// Consumer-side safety ceilings applied before permanent compilation.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct EncodedLimits {
    pub max_roots: usize,
    pub max_nodes: usize,
    pub max_fields: usize,
    pub max_items: usize,
    pub max_scalar_bytes: usize,
    pub max_work: u64,
}

impl Default for EncodedLimits {
    fn default() -> Self {
        Self {
            max_roots: 100_000_000,
            max_nodes: 100_000_000,
            max_fields: 400_000_000,
            max_items: 400_000_000,
            max_scalar_bytes: usize::try_from(8_589_934_592_u64).unwrap_or(usize::MAX),
            max_work: 2_000_000_000,
        }
    }
}

/// Bounded facts established without retaining a second structural graph.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ValidatedEncodedColumns {
    pub root_count: usize,
    pub node_count: usize,
    pub field_count: usize,
    pub item_count: usize,
    pub scalar_bytes: usize,
    pub work: u64,
}

#[derive(Clone, Copy, Debug)]
struct DfsFrame {
    node: usize,
    field_cursor: usize,
    field_end: usize,
    item_cursor: usize,
    item_end: usize,
}

impl DfsFrame {
    fn new<B: ByteSource>(node: usize, columns: &EncodedColumns<B>) -> CoreResult<Self> {
        let field_cursor = usize_at(columns.node_field_offsets, node, "node field offset")?;
        let field_end = usize_at(
            columns.node_field_offsets,
            node.checked_add(1)
                .ok_or_else(|| CoreError::capacity("node field offset index overflow"))?,
            "node field offset",
        )?;
        Ok(Self {
            node,
            field_cursor,
            field_end,
            item_cursor: 0,
            item_end: 0,
        })
    }
}

/// Validate structural-columns v1 before the private compiler consumes it.
pub fn validate_columns<B: ByteSource>(
    columns: EncodedColumns<B>,
    limits: EncodedLimits,
) -> CoreResult<ValidatedEncodedColumns> {
    let root_count = aligned_count(columns.root_ids, 4, "root_ids")?;
    if columns.root_kinds.len() != root_count {
        return Err(CoreError::protocol(
            "encoded root kind and root ID counts differ",
        ));
    }
    let node_count = aligned_count(columns.node_tags, 2, "node_tags")?;
    let offset_count = aligned_count(columns.node_field_offsets, 8, "node_field_offsets")?;
    if offset_count
        != node_count
            .checked_add(1)
            .ok_or_else(|| CoreError::capacity("encoded node offset count overflow"))?
    {
        return Err(CoreError::protocol(
            "encoded node field offsets must contain node_count + 1 rows",
        ));
    }
    let field_count = columns.field_kinds.len();
    if aligned_count(columns.field_values, 8, "field_values")? != field_count
        || aligned_count(columns.field_lengths, 8, "field_lengths")? != field_count
    {
        return Err(CoreError::protocol(
            "encoded field component columns differ in length",
        ));
    }
    let item_count = columns.item_kinds.len();
    if aligned_count(columns.item_values, 8, "item_values")? != item_count
        || aligned_count(columns.item_lengths, 8, "item_lengths")? != item_count
    {
        return Err(CoreError::protocol(
            "encoded item component columns differ in length",
        ));
    }
    enforce_count(root_count, limits.max_roots, "encoded root count")?;
    enforce_count(node_count, limits.max_nodes, "encoded node count")?;
    enforce_count(field_count, limits.max_fields, "encoded field count")?;
    enforce_count(item_count, limits.max_items, "encoded item count")?;
    enforce_count(
        columns.scalar_bytes.len(),
        limits.max_scalar_bytes,
        "encoded scalar byte count",
    )?;

    let mut work = 0_u64;
    claim_work(&mut work, 1, limits.max_work)?;
    if u64_at(columns.node_field_offsets, 0, "node field offset")? != 0 {
        return Err(CoreError::protocol(
            "encoded node field offsets must start at zero",
        ));
    }
    let mut prior_offset = 0_usize;
    for node in 0..node_count {
        claim_work(&mut work, 1, limits.max_work)?;
        let start = usize_at(columns.node_field_offsets, node, "node field offset")?;
        let end = usize_at(columns.node_field_offsets, node + 1, "node field offset")?;
        if start != prior_offset || end < start || end > field_count {
            return Err(CoreError::protocol(
                "encoded node field offsets are not contiguous and bounded",
            ));
        }
        let tag = u16_at(columns.node_tags, node, "node tag")?;
        let roles = constructor_roles(tag)
            .ok_or_else(|| CoreError::protocol(format!("unsupported encoded node tag {tag}")))?;
        if end - start != roles.len() {
            return Err(CoreError::protocol(format!(
                "encoded node tag {tag} has the wrong field arity"
            )));
        }
        prior_offset = end;
    }
    if prior_offset != field_count {
        return Err(CoreError::protocol(
            "encoded node field offsets do not cover every field",
        ));
    }

    let mut item_cursor = 0_usize;
    let mut scalar_cursor = 0_usize;
    for node in 0..node_count {
        let tag = u16_at(columns.node_tags, node, "node tag")?;
        let roles = constructor_roles(tag)
            .ok_or_else(|| CoreError::protocol(format!("unsupported encoded node tag {tag}")))?;
        let start = usize_at(columns.node_field_offsets, node, "node field offset")?;
        for (position, role) in roles.iter().copied().enumerate() {
            claim_work(&mut work, 1, limits.max_work)?;
            let field = start
                .checked_add(position)
                .ok_or_else(|| CoreError::capacity("encoded field index overflow"))?;
            let location = FieldLocation { tag, position };
            let kind = byte_at(columns.field_kinds, field, "field kind")?;
            let value = usize_at(columns.field_values, field, "field value")?;
            let length = usize_at(columns.field_lengths, field, "field length")?;
            let collection = match role {
                FieldRole::Set(item_role) => Some((COMPONENT_SET, item_role)),
                FieldRole::Sequence(item_role) => Some((COMPONENT_SEQUENCE, item_role)),
                _ => None,
            };
            if let Some((expected_kind, item_role)) = collection {
                if kind != expected_kind {
                    return Err(field_role_error(location));
                }
                if value != item_cursor {
                    return Err(CoreError::protocol(
                        "encoded collection fields do not exactly cover item rows",
                    ));
                }
                let end = value
                    .checked_add(length)
                    .ok_or_else(|| CoreError::capacity("encoded item range overflow"))?;
                if end > item_count {
                    return Err(CoreError::protocol(
                        "encoded collection field exceeds item rows",
                    ));
                }
                let mut previous_set_item = None;
                for item in value..end {
                    claim_work(&mut work, 1, limits.max_work)?;
                    let item_kind = byte_at(columns.item_kinds, item, "item kind")?;
                    let item_value = usize_at(columns.item_values, item, "item value")?;
                    let item_length = usize_at(columns.item_lengths, item, "item length")?;
                    validate_collection_item_role(
                        location, item_role, item_kind, item_value, &columns, node_count,
                    )?;
                    if expected_kind == COMPONENT_SET {
                        let identifier =
                            node_id_at(columns.item_values, item, "canonical-set item node ID")?;
                        if previous_set_item.is_some_and(|prior| prior >= identifier) {
                            return Err(CoreError::protocol(
                                "encoded canonical-set node IDs are not strictly ascending and unique",
                            ));
                        }
                        previous_set_item = Some(identifier);
                    }
                    claim_leaf_scan_work(item_kind, item_length, &mut work, limits.max_work)?;
                    validate_leaf_component(
                        item_kind,
                        item_value,
                        item_length,
                        node_count,
                        columns.scalar_bytes,
                        &mut scalar_cursor,
                    )?;
                }
                item_cursor = end;
            } else {
                validate_field_role(location, role, kind, value, length, &columns, node_count)?;
                claim_leaf_scan_work(kind, length, &mut work, limits.max_work)?;
                validate_leaf_component(
                    kind,
                    value,
                    length,
                    node_count,
                    columns.scalar_bytes,
                    &mut scalar_cursor,
                )?;
            }
        }
    }
    if item_cursor != item_count {
        return Err(CoreError::protocol(
            "encoded item rows are not exactly covered by collection fields",
        ));
    }
    if scalar_cursor != columns.scalar_bytes.len() {
        return Err(CoreError::protocol(
            "encoded scalar arena is not exactly covered by components",
        ));
    }

    // Dense-node validation below proves that the one-based IDs are ranks in
    // canonical-model-v1 byte order, making this tuple comparison equivalent
    // to the descriptor's `(root kind, canonical bytes)` ordering rule.
    let mut previous_root = None;
    for root in 0..root_count {
        claim_work(&mut work, 1, limits.max_work)?;
        let kind = byte_at(columns.root_kinds, root, "root kind")?;
        let identifier = u32_at(columns.root_ids, root, "root ID")?;
        let node = node_index(identifier, node_count)?;
        let tag = u16_at(columns.node_tags, node, "root node tag")?;
        if !root_accepts(kind, tag) {
            return Err(CoreError::protocol(
                "encoded root kind is inconsistent with its constructor tag",
            ));
        }
        if previous_root.is_some_and(|prior| prior >= (kind, identifier)) {
            return Err(CoreError::protocol(
                "encoded roots are not strictly ordered and unique",
            ));
        }
        previous_root = Some((kind, identifier));
    }

    let canonical_lengths =
        validate_graph_and_lengths(&columns, root_count, node_count, &mut work, limits.max_work)?;
    validate_dense_node_order(&columns, &canonical_lengths, &mut work, limits.max_work)?;
    Ok(ValidatedEncodedColumns {
        root_count,
        node_count,
        field_count,
        item_count,
        scalar_bytes: columns.scalar_bytes.len(),
        work,
    })
}

/// Compile the first exact, deliberately narrow encoded-ontology slice.
///
/// This stage accepts unannotated declarations of classes, named individuals, and object
/// properties plus unannotated named `SubClassOf`, `EquivalentClasses`, `ClassAssertion`,
/// `DisjointClasses`, named `DisjointUnion`, `SameIndividual`, `DifferentIndividuals`,
/// named object-property assertions, `SubObjectPropertyOf`, `EquivalentObjectProperties`,
/// object-property domain/range, reflexivity, and transitivity axioms. Ontology annotations and
/// annotation-property declarations have no ELK effect and are ignored. Every other logical
/// constructor fails closed, irrespective of the scalar compiler's unsupported policy.
/// Consequently this function is safe to extend and test while encoded-schema capability
/// advertisement remains disabled.
///
/// `source_fingerprint` is already bound by the caller to the core snapshot and compiler options;
/// the structural columns intentionally do not carry pyELK's private cache-key material.
pub fn compile_named_hierarchy<B: ByteSource>(
    columns: EncodedColumns<B>,
    limits: EncodedLimits,
    source_fingerprint: [u8; 32],
) -> CoreResult<Ontology> {
    let validated = validate_columns(columns, limits)?;
    let mut builder = NamedHierarchyBuilder::new();
    for root in 0..validated.root_count {
        let kind = byte_at(columns.root_kinds, root, "root kind")?;
        if kind == ROOT_ONTOLOGY_ANNOTATION {
            continue;
        }
        if kind != ROOT_AXIOM {
            return Err(CoreError::invalid(
                "encoded named-hierarchy compiler does not support extensions",
            ));
        }
        let identifier = u32_at(columns.root_ids, root, "root ID")?;
        let node = node_index(identifier, validated.node_count)?;
        match u16_at(columns.node_tags, node, "root node tag")? {
            60 => compile_declaration(node, &columns, &mut builder)?,
            61 => compile_named_subclass(node, &columns, &mut builder)?,
            62 => compile_named_equivalence(node, &columns, &mut builder)?,
            63 => compile_disjoint_named_classes(node, &columns, &mut builder)?,
            64 => compile_named_disjoint_union(node, &columns, &mut builder)?,
            70 => compile_named_subproperty(node, &columns, &mut builder)?,
            71 => compile_equivalent_named_properties(node, &columns, &mut builder)?,
            74 => compile_named_property_domain(node, &columns, &mut builder)?,
            75 => compile_named_property_range(node, &columns, &mut builder)?,
            78 => compile_reflexive_named_property(node, &columns, &mut builder)?,
            82 => compile_transitive_named_property(node, &columns, &mut builder)?,
            110 => compile_same_named_individuals(node, &columns, &mut builder)?,
            111 => compile_different_named_individuals(node, &columns, &mut builder)?,
            112 => compile_named_class_assertion(node, &columns, &mut builder)?,
            113 => compile_named_object_property_assertion(node, &columns, &mut builder)?,
            tag => {
                return Err(CoreError::invalid(format!(
                    "encoded named-hierarchy compiler does not support axiom tag {tag}"
                )));
            }
        }
    }
    builder.freeze(source_fingerprint)
}

struct NamedHierarchyBuilder {
    entities: BTreeSet<Entity>,
    occurrences: BTreeMap<Entity, Occurrence>,
    property_occurrences: BTreeMap<Entity, Occurrence>,
    property_chains: BTreeSet<Vec<Entity>>,
    subclass_axioms: BTreeSet<(Entity, Entity)>,
    intersection_occurrences: BTreeMap<(Entity, Entity), Occurrence>,
    intersection_subclass_axioms: BTreeSet<((Entity, Entity), Entity)>,
    existential_occurrences: BTreeMap<(Entity, Entity), Occurrence>,
    existential_subclass_axioms: BTreeSet<((Entity, Entity), Entity)>,
    subclass_existential_axioms: BTreeSet<(Entity, (Entity, Entity))>,
    has_self_occurrences: BTreeMap<Entity, Occurrence>,
    subclass_has_self_axioms: BTreeSet<(Entity, Entity)>,
    equivalent_class_axioms: BTreeSet<(Entity, Entity)>,
    disjoint_groups: BTreeSet<Vec<Entity>>,
    subproperty_axioms: BTreeSet<(Vec<Entity>, Entity)>,
    property_ranges: BTreeSet<(Entity, Entity)>,
    feature_counts: Vec<u64>,
}

impl Default for NamedHierarchyBuilder {
    fn default() -> Self {
        Self {
            entities: BTreeSet::new(),
            occurrences: BTreeMap::new(),
            property_occurrences: BTreeMap::new(),
            property_chains: BTreeSet::new(),
            subclass_axioms: BTreeSet::new(),
            intersection_occurrences: BTreeMap::new(),
            intersection_subclass_axioms: BTreeSet::new(),
            existential_occurrences: BTreeMap::new(),
            existential_subclass_axioms: BTreeSet::new(),
            subclass_existential_axioms: BTreeSet::new(),
            has_self_occurrences: BTreeMap::new(),
            subclass_has_self_axioms: BTreeSet::new(),
            equivalent_class_axioms: BTreeSet::new(),
            disjoint_groups: BTreeSet::new(),
            subproperty_axioms: BTreeSet::new(),
            property_ranges: BTreeSet::new(),
            feature_counts: vec![0; FEATURE_VECTOR_LENGTH],
        }
    }
}

impl NamedHierarchyBuilder {
    fn new() -> Self {
        let mut builder = Self::default();
        for (kind, iri) in [
            (EntityKind::Class, OWL_THING_IRI),
            (EntityKind::Class, OWL_NOTHING_IRI),
            (EntityKind::ObjectProperty, OWL_TOP_OBJECT_PROPERTY_IRI),
            (EntityKind::ObjectProperty, OWL_BOTTOM_OBJECT_PROPERTY_IRI),
        ] {
            builder.entities.insert(Entity {
                kind,
                iri: iri.to_owned(),
            });
        }
        builder
    }

    fn add_declaration(&mut self, entity: Entity) -> CoreResult<()> {
        match entity.kind {
            EntityKind::Class | EntityKind::NamedIndividual | EntityKind::ObjectProperty => {
                self.entities.insert(entity);
                Ok(())
            }
            EntityKind::AnnotationProperty => Ok(()),
            EntityKind::DataProperty | EntityKind::Datatype => Err(CoreError::invalid(format!(
                "encoded named-hierarchy compiler does not support {:?} declarations",
                entity.kind
            ))),
        }
    }

    fn add_subclass(&mut self, sub: Entity, super_: Entity) -> CoreResult<()> {
        self.add_named_occurrence(&sub, true, false)?;
        self.add_named_occurrence(&super_, false, true)?;
        self.subclass_axioms.insert((sub, super_));
        Ok(())
    }

    fn add_equivalent_classes(&mut self, members: Vec<Entity>) -> CoreResult<()> {
        let Some(first) = members.first().cloned() else {
            return Ok(());
        };
        for member in &members {
            self.entities.insert(member.clone());
            let occurrence = self.occurrences.entry(member.clone()).or_default();
            increment_occurrence(occurrence, false)?;
            increment_occurrence(occurrence, true)?;
        }
        for member in members.into_iter().skip(1) {
            self.equivalent_class_axioms.insert((first.clone(), member));
        }
        Ok(())
    }

    fn add_same_individuals(&mut self, members: Vec<Entity>) -> CoreResult<()> {
        let Some(first) = members.first().cloned() else {
            return Ok(());
        };
        for member in &members {
            self.entities.insert(member.clone());
            let occurrence = self.occurrences.entry(member.clone()).or_default();
            increment_occurrence(occurrence, false)?;
            increment_occurrence(occurrence, true)?;
        }
        for member in members.into_iter().skip(1) {
            self.subclass_axioms.insert((first.clone(), member.clone()));
            self.subclass_axioms.insert((member, first.clone()));
        }
        Ok(())
    }

    fn add_disjoint(&mut self, members: Vec<Entity>, feature: Option<usize>) -> CoreResult<()> {
        if let Some(index) = feature {
            self.add_feature(index, 1)?;
        }
        if members.len() > 2 {
            for member in &members {
                self.add_named_occurrence(member, true, false)?;
            }
            self.disjoint_groups.insert(members);
            return Ok(());
        }

        let bottom = Entity {
            kind: EntityKind::Class,
            iri: OWL_NOTHING_IRI.to_owned(),
        };
        self.add_named_occurrence(&bottom, false, true)?;
        self.add_feature(FEATURE_OWL_NOTHING_POSITIVE, 1)?;
        for (first_position, first) in members.iter().enumerate() {
            self.add_named_occurrence(first, true, false)?;
            for second in members.iter().skip(first_position + 1) {
                self.add_named_occurrence(second, true, false)?;
                let key = (first.clone(), second.clone());
                increment_occurrence(
                    self.intersection_occurrences
                        .entry(key.clone())
                        .or_default(),
                    false,
                )?;
                self.intersection_subclass_axioms
                    .insert((key, bottom.clone()));
            }
        }
        Ok(())
    }

    fn add_disjoint_union(&mut self, defined: Entity, members: Vec<Entity>) -> CoreResult<()> {
        self.add_disjoint(members.clone(), None)?;
        match members.as_slice() {
            [] => {
                let bottom = Entity {
                    kind: EntityKind::Class,
                    iri: OWL_NOTHING_IRI.to_owned(),
                };
                self.add_named_occurrence(&defined, false, true)?;
                self.add_named_occurrence(&bottom, false, true)?;
                self.add_feature(FEATURE_OWL_NOTHING_POSITIVE, 1)?;
                self.equivalent_class_axioms.insert((defined, bottom));
            }
            [member] => {
                self.add_named_occurrence(&defined, true, true)?;
                self.add_named_occurrence(member, true, true)?;
                self.equivalent_class_axioms
                    .insert((defined, member.clone()));
            }
            _ => {
                self.add_feature(FEATURE_DISJOINT_UNION, 1)?;
                self.add_named_occurrence(&defined, false, true)?;
                for member in members {
                    self.add_named_occurrence(&member, true, false)?;
                    self.subclass_axioms.insert((member, defined.clone()));
                }
            }
        }
        Ok(())
    }

    fn add_named_occurrence(
        &mut self,
        entity: &Entity,
        negative: bool,
        positive: bool,
    ) -> CoreResult<()> {
        self.entities.insert(entity.clone());
        let occurrence = self.occurrences.entry(entity.clone()).or_default();
        if negative {
            increment_occurrence(occurrence, false)?;
        }
        if positive {
            increment_occurrence(occurrence, true)?;
        }
        Ok(())
    }

    fn add_subproperty(&mut self, chain: Vec<Entity>, super_: Entity) -> CoreResult<()> {
        for property in &chain {
            self.entities.insert(property.clone());
            increment_occurrence(
                self.property_occurrences
                    .entry(property.clone())
                    .or_default(),
                false,
            )?;
        }
        self.entities.insert(super_.clone());
        increment_occurrence(
            self.property_occurrences.entry(super_.clone()).or_default(),
            true,
        )?;
        if chain.len() > 1 {
            self.add_feature(
                FEATURE_OBJECT_PROPERTY_CHAIN,
                u64::try_from(chain.len() - 1).map_err(|_| {
                    CoreError::capacity("encoded property-chain feature count exceeds u64")
                })?,
            )?;
        }
        self.insert_subproperty_rule(chain, super_);
        Ok(())
    }

    fn add_equivalent_properties(&mut self, members: Vec<Entity>) -> CoreResult<()> {
        let Some(first) = members.first().cloned() else {
            return Ok(());
        };
        for member in &members {
            self.entities.insert(member.clone());
            let occurrence = self.property_occurrences.entry(member.clone()).or_default();
            increment_occurrence(occurrence, false)?;
            increment_occurrence(occurrence, true)?;
            self.property_chains.insert(vec![member.clone()]);
        }
        for member in members.into_iter().skip(1) {
            self.subproperty_axioms
                .insert((vec![first.clone()], member.clone()));
            self.subproperty_axioms
                .insert((vec![member], first.clone()));
        }
        Ok(())
    }

    fn add_transitive_property(&mut self, property: Entity) -> CoreResult<()> {
        self.entities.insert(property.clone());
        let occurrence = self
            .property_occurrences
            .entry(property.clone())
            .or_default();
        increment_occurrence(occurrence, false)?;
        increment_occurrence(occurrence, true)?;
        self.add_feature(FEATURE_OBJECT_PROPERTY_CHAIN, 1)?;
        self.insert_subproperty_rule(vec![property.clone(), property.clone()], property);
        Ok(())
    }

    fn add_property_range(&mut self, property: Entity, range: Entity) -> CoreResult<()> {
        self.entities.insert(property.clone());
        self.entities.insert(range.clone());
        increment_occurrence(
            self.property_occurrences
                .entry(property.clone())
                .or_default(),
            false,
        )?;
        increment_occurrence(self.occurrences.entry(range.clone()).or_default(), true)?;
        self.property_ranges.insert((property, range));
        self.add_feature(FEATURE_OBJECT_PROPERTY_RANGE, 1)
    }

    fn add_property_domain(&mut self, property: Entity, domain: Entity) -> CoreResult<()> {
        let thing = Entity {
            kind: EntityKind::Class,
            iri: OWL_THING_IRI.to_owned(),
        };
        self.add_property_occurrence(&property, true, false)?;
        self.add_named_occurrence(&thing, true, false)?;
        self.add_named_occurrence(&domain, false, true)?;
        let existential = (property, thing);
        increment_occurrence(
            self.existential_occurrences
                .entry(existential.clone())
                .or_default(),
            false,
        )?;
        self.existential_subclass_axioms
            .insert((existential, domain));
        Ok(())
    }

    fn add_object_property_assertion(
        &mut self,
        property: Entity,
        source: Entity,
        target: Entity,
    ) -> CoreResult<()> {
        self.add_feature(FEATURE_OBJECT_PROPERTY_ASSERTION, 1)?;
        self.add_named_occurrence(&source, true, false)?;
        self.add_property_occurrence(&property, false, true)?;
        self.add_named_occurrence(&target, false, true)?;
        let existential = (property, target);
        increment_occurrence(
            self.existential_occurrences
                .entry(existential.clone())
                .or_default(),
            true,
        )?;
        self.add_feature(FEATURE_OBJECT_HAS_VALUE_POSITIVE, 1)?;
        self.subclass_existential_axioms
            .insert((source, existential));
        Ok(())
    }

    fn add_reflexive_property(&mut self, property: Entity) -> CoreResult<()> {
        let thing = Entity {
            kind: EntityKind::Class,
            iri: OWL_THING_IRI.to_owned(),
        };
        self.add_feature(FEATURE_REFLEXIVE_OBJECT_PROPERTY, 1)?;
        self.add_named_occurrence(&thing, true, false)?;
        self.add_property_occurrence(&property, false, true)?;
        increment_occurrence(
            self.has_self_occurrences
                .entry(property.clone())
                .or_default(),
            true,
        )?;
        self.subclass_has_self_axioms.insert((thing, property));
        Ok(())
    }

    fn add_property_occurrence(
        &mut self,
        property: &Entity,
        negative: bool,
        positive: bool,
    ) -> CoreResult<()> {
        self.entities.insert(property.clone());
        let occurrence = self
            .property_occurrences
            .entry(property.clone())
            .or_default();
        if negative {
            increment_occurrence(occurrence, false)?;
        }
        if positive {
            increment_occurrence(occurrence, true)?;
        }
        Ok(())
    }

    fn insert_subproperty_rule(&mut self, chain: Vec<Entity>, super_: Entity) {
        self.property_chains.insert(chain.clone());
        self.subproperty_axioms.insert((chain, super_));
    }

    fn add_feature(&mut self, index: usize, count: u64) -> CoreResult<()> {
        self.feature_counts[index] = self.feature_counts[index]
            .checked_add(count)
            .ok_or_else(|| CoreError::capacity("encoded compiler feature count exceeds u64"))?;
        Ok(())
    }

    fn freeze(self, source_fingerprint: [u8; 32]) -> CoreResult<Ontology> {
        let entities = self.entities.into_iter().collect::<Vec<_>>();
        if entities.len() >= u32::MAX as usize {
            return Err(CoreError::capacity(
                "encoded compiler entity table exceeds the reserved u32 namespace",
            ));
        }
        let entity_ids = entities
            .iter()
            .cloned()
            .enumerate()
            .map(|(index, entity)| {
                u32::try_from(index)
                    .map(|identifier| (entity, identifier))
                    .map_err(|_| CoreError::capacity("encoded compiler entity ID exceeds u32"))
            })
            .collect::<CoreResult<BTreeMap<_, _>>>()?;

        let mut expressions = Vec::new();
        let mut expression_occurrences = Vec::new();
        let mut expression_ids = BTreeMap::new();
        for (tag, kind) in [
            (ExpressionTag::Class, EntityKind::Class),
            (ExpressionTag::Individual, EntityKind::NamedIndividual),
        ] {
            for entity in entities.iter().filter(|entity| entity.kind == kind) {
                let identifier = u32::try_from(expressions.len()).map_err(|_| {
                    CoreError::capacity("encoded compiler expression ID exceeds u32")
                })?;
                if identifier == u32::MAX {
                    return Err(CoreError::capacity(
                        "encoded compiler expression table reaches the reserved u32 ID",
                    ));
                }
                expressions.push(Expression {
                    tag,
                    payload: Vec::new(),
                    arguments: vec![entity_ids[entity]],
                });
                expression_occurrences
                    .push(self.occurrences.get(entity).copied().unwrap_or_default());
                expression_ids.insert(entity.clone(), identifier);
            }
        }

        let mut intersection_rows = self
            .intersection_occurrences
            .into_iter()
            .collect::<Vec<_>>();
        intersection_rows.sort_by_key(|((first, second), _occurrence)| {
            (expression_ids[first], expression_ids[second])
        });
        let mut intersection_ids = BTreeMap::new();
        for ((first, second), occurrence) in intersection_rows {
            let identifier = u32::try_from(expressions.len())
                .map_err(|_| CoreError::capacity("encoded compiler expression ID exceeds u32"))?;
            if identifier == u32::MAX {
                return Err(CoreError::capacity(
                    "encoded compiler expression table reaches the reserved u32 ID",
                ));
            }
            expressions.push(Expression {
                tag: ExpressionTag::ObjectIntersectionOf,
                payload: Vec::new(),
                arguments: vec![expression_ids[&first], expression_ids[&second]],
            });
            expression_occurrences.push(occurrence);
            intersection_ids.insert((first, second), identifier);
        }

        let mut existential_rows = self.existential_occurrences.into_iter().collect::<Vec<_>>();
        existential_rows.sort_by_key(|((property, filler), _occurrence)| {
            (entity_ids[property], expression_ids[filler])
        });
        let mut existential_ids = BTreeMap::new();
        for ((property, filler), occurrence) in existential_rows {
            let identifier = u32::try_from(expressions.len())
                .map_err(|_| CoreError::capacity("encoded compiler expression ID exceeds u32"))?;
            if identifier == u32::MAX {
                return Err(CoreError::capacity(
                    "encoded compiler expression table reaches the reserved u32 ID",
                ));
            }
            expressions.push(Expression {
                tag: ExpressionTag::ObjectSomeValuesFrom,
                payload: Vec::new(),
                arguments: vec![entity_ids[&property], expression_ids[&filler]],
            });
            expression_occurrences.push(occurrence);
            existential_ids.insert((property, filler), identifier);
        }

        let mut has_self_rows = self.has_self_occurrences.into_iter().collect::<Vec<_>>();
        has_self_rows.sort_by_key(|(property, _occurrence)| entity_ids[property]);
        let mut has_self_ids = BTreeMap::new();
        for (property, occurrence) in has_self_rows {
            let identifier = u32::try_from(expressions.len())
                .map_err(|_| CoreError::capacity("encoded compiler expression ID exceeds u32"))?;
            if identifier == u32::MAX {
                return Err(CoreError::capacity(
                    "encoded compiler expression table reaches the reserved u32 ID",
                ));
            }
            expressions.push(Expression {
                tag: ExpressionTag::ObjectHasSelf,
                payload: Vec::new(),
                arguments: vec![entity_ids[&property]],
            });
            expression_occurrences.push(occurrence);
            has_self_ids.insert(property, identifier);
        }

        let object_properties = entities
            .iter()
            .filter(|entity| entity.kind == EntityKind::ObjectProperty)
            .cloned()
            .collect::<Vec<_>>();
        let mut subclass_axioms = self
            .subclass_axioms
            .into_iter()
            .map(|(sub, super_)| Ok((expression_ids[&sub], expression_ids[&super_])))
            .collect::<CoreResult<BTreeSet<_>>>()?;
        subclass_axioms.extend(self.intersection_subclass_axioms.into_iter().map(
            |(intersection, super_)| (intersection_ids[&intersection], expression_ids[&super_]),
        ));
        subclass_axioms.extend(
            self.existential_subclass_axioms
                .into_iter()
                .map(|(existential, super_)| {
                    (existential_ids[&existential], expression_ids[&super_])
                }),
        );
        subclass_axioms.extend(
            self.subclass_existential_axioms
                .into_iter()
                .map(|(sub, existential)| (expression_ids[&sub], existential_ids[&existential])),
        );
        subclass_axioms.extend(
            self.subclass_has_self_axioms
                .into_iter()
                .map(|(sub, property)| (expression_ids[&sub], has_self_ids[&property])),
        );
        let equivalent_class_axioms = self
            .equivalent_class_axioms
            .into_iter()
            .map(|(first, second)| Ok((expression_ids[&first], expression_ids[&second])))
            .collect::<CoreResult<Vec<_>>>()?;
        let disjoint_groups = self
            .disjoint_groups
            .into_iter()
            .map(|group| {
                group
                    .into_iter()
                    .map(|member| expression_ids[&member])
                    .collect::<Vec<_>>()
            })
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        let mut property_chain_values = self
            .property_chains
            .into_iter()
            .map(|chain| {
                chain
                    .into_iter()
                    .map(|property| Ok(entity_ids[&property]))
                    .collect::<CoreResult<Vec<_>>>()
            })
            .collect::<CoreResult<BTreeSet<_>>>()?;
        property_chain_values.extend(
            object_properties
                .iter()
                .map(|property| vec![entity_ids[property]]),
        );
        let property_chains = property_chain_values.into_iter().collect::<Vec<_>>();
        let property_chain_ids = property_chains
            .iter()
            .cloned()
            .enumerate()
            .map(|(index, chain)| {
                u32::try_from(index)
                    .map(|identifier| (chain, identifier))
                    .map_err(|_| {
                        CoreError::capacity("encoded compiler property-chain ID exceeds u32")
                    })
            })
            .collect::<CoreResult<BTreeMap<_, _>>>()?;
        let subproperty_axioms = self
            .subproperty_axioms
            .into_iter()
            .map(|(chain, super_)| {
                let chain = chain
                    .into_iter()
                    .map(|property| entity_ids[&property])
                    .collect::<Vec<_>>();
                Ok((property_chain_ids[&chain], entity_ids[&super_]))
            })
            .collect::<CoreResult<BTreeSet<_>>>()?
            .into_iter()
            .collect();
        let property_ranges = self
            .property_ranges
            .into_iter()
            .map(|(property, range)| (entity_ids[&property], expression_ids[&range]))
            .collect();

        Ok(Ontology {
            entities,
            expressions,
            expression_occurrences,
            property_occurrences: object_properties
                .iter()
                .map(|property| {
                    self.property_occurrences
                        .get(property)
                        .copied()
                        .unwrap_or_default()
                })
                .collect(),
            property_chains,
            subclass_axioms: subclass_axioms.into_iter().collect(),
            equivalent_class_axioms,
            disjoint_groups,
            subproperty_axioms,
            property_ranges,
            feature_counts: self.feature_counts,
            source_fingerprint,
        })
    }
}

fn compile_declaration<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> CoreResult<()> {
    require_empty_annotations(node, 1, columns)?;
    builder.add_declaration(decode_entity(node_field(node, 0, columns)?, columns)?)
}

fn compile_named_subclass<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> CoreResult<()> {
    require_empty_annotations(node, 2, columns)?;
    let sub = decode_named_class(node_field(node, 0, columns)?, columns)?;
    let super_ = decode_named_class(node_field(node, 1, columns)?, columns)?;
    builder.add_subclass(sub, super_)
}

fn compile_named_equivalence<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> CoreResult<()> {
    require_empty_annotations(node, 1, columns)?;
    let members = node_collection(node, 0, columns)?
        .into_iter()
        .map(|identifier| decode_named_class(identifier, columns))
        .collect::<CoreResult<Vec<_>>>()?;
    builder.add_equivalent_classes(members)
}

fn compile_disjoint_named_classes<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> CoreResult<()> {
    require_empty_annotations(node, 1, columns)?;
    let members = node_collection(node, 0, columns)?
        .into_iter()
        .map(|identifier| decode_named_class(identifier, columns))
        .collect::<CoreResult<Vec<_>>>()?;
    builder.add_disjoint(members, Some(FEATURE_DISJOINT_CLASSES))
}

fn compile_named_disjoint_union<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> CoreResult<()> {
    require_empty_annotations(node, 2, columns)?;
    let defined = decode_named_class(node_field(node, 0, columns)?, columns)?;
    let members = node_collection(node, 1, columns)?
        .into_iter()
        .map(|identifier| decode_named_class(identifier, columns))
        .collect::<CoreResult<Vec<_>>>()?;
    builder.add_disjoint_union(defined, members)
}

fn compile_named_subproperty<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> CoreResult<()> {
    require_empty_annotations(node, 2, columns)?;
    let chain = decode_named_property_chain(node_field(node, 0, columns)?, columns)?;
    let super_ = decode_named_object_property(node_field(node, 1, columns)?, columns)?;
    builder.add_subproperty(chain, super_)
}

fn compile_equivalent_named_properties<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> CoreResult<()> {
    require_empty_annotations(node, 1, columns)?;
    let members = node_collection(node, 0, columns)?
        .into_iter()
        .map(|identifier| decode_named_object_property(identifier, columns))
        .collect::<CoreResult<Vec<_>>>()?;
    builder.add_equivalent_properties(members)
}

fn compile_named_property_domain<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> CoreResult<()> {
    require_empty_annotations(node, 2, columns)?;
    let property = decode_named_object_property(node_field(node, 0, columns)?, columns)?;
    let domain = decode_named_class(node_field(node, 1, columns)?, columns)?;
    builder.add_property_domain(property, domain)
}

fn compile_named_property_range<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> CoreResult<()> {
    require_empty_annotations(node, 2, columns)?;
    let property = decode_named_object_property(node_field(node, 0, columns)?, columns)?;
    let range = decode_named_class(node_field(node, 1, columns)?, columns)?;
    builder.add_property_range(property, range)
}

fn compile_reflexive_named_property<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> CoreResult<()> {
    require_empty_annotations(node, 1, columns)?;
    let property = decode_named_object_property(node_field(node, 0, columns)?, columns)?;
    builder.add_reflexive_property(property)
}

fn compile_transitive_named_property<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> CoreResult<()> {
    require_empty_annotations(node, 1, columns)?;
    let property = decode_named_object_property(node_field(node, 0, columns)?, columns)?;
    builder.add_transitive_property(property)
}

fn compile_named_class_assertion<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> CoreResult<()> {
    require_empty_annotations(node, 2, columns)?;
    let class = decode_named_class(node_field(node, 0, columns)?, columns)?;
    let individual = decode_named_individual(node_field(node, 1, columns)?, columns)?;
    builder.add_subclass(individual, class)
}

fn compile_named_object_property_assertion<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> CoreResult<()> {
    require_empty_annotations(node, 3, columns)?;
    let property = decode_named_object_property(node_field(node, 0, columns)?, columns)?;
    let source = decode_named_individual(node_field(node, 1, columns)?, columns)?;
    let target = decode_named_individual(node_field(node, 2, columns)?, columns)?;
    builder.add_object_property_assertion(property, source, target)
}

fn compile_same_named_individuals<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> CoreResult<()> {
    require_empty_annotations(node, 1, columns)?;
    let members = node_collection(node, 0, columns)?
        .into_iter()
        .map(|identifier| decode_named_individual(identifier, columns))
        .collect::<CoreResult<Vec<_>>>()?;
    builder.add_same_individuals(members)
}

fn compile_different_named_individuals<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> CoreResult<()> {
    require_empty_annotations(node, 1, columns)?;
    let members = node_collection(node, 0, columns)?
        .into_iter()
        .map(|identifier| decode_named_individual(identifier, columns))
        .collect::<CoreResult<Vec<_>>>()?;
    builder.add_disjoint(members, Some(FEATURE_DIFFERENT_INDIVIDUALS))
}

fn decode_named_class<B: ByteSource>(
    identifier: u32,
    columns: &EncodedColumns<B>,
) -> CoreResult<Entity> {
    let entity = decode_entity(identifier, columns)?;
    if entity.kind != EntityKind::Class {
        return Err(CoreError::internal(
            "validated named class expression resolved to a non-class entity",
        ));
    }
    Ok(entity)
}

fn decode_named_individual<B: ByteSource>(
    identifier: u32,
    columns: &EncodedColumns<B>,
) -> CoreResult<Entity> {
    let entity = decode_entity(identifier, columns)?;
    if entity.kind != EntityKind::NamedIndividual {
        return Err(CoreError::internal(
            "validated named individual resolved to the wrong entity kind",
        ));
    }
    Ok(entity)
}

fn decode_named_object_property<B: ByteSource>(
    identifier: u32,
    columns: &EncodedColumns<B>,
) -> CoreResult<Entity> {
    let entity = decode_entity(identifier, columns)?;
    if entity.kind != EntityKind::ObjectProperty {
        return Err(CoreError::internal(
            "validated named object property resolved to the wrong entity kind",
        ));
    }
    Ok(entity)
}

fn decode_named_property_chain<B: ByteSource>(
    identifier: u32,
    columns: &EncodedColumns<B>,
) -> CoreResult<Vec<Entity>> {
    let node_count = aligned_count(columns.node_tags, 2, "node_tags")?;
    let node = node_index(identifier, node_count)?;
    match u16_at(columns.node_tags, node, "sub-property node tag")? {
        2 => Ok(vec![decode_named_object_property(identifier, columns)?]),
        11 => {
            let members = node_collection(node, 0, columns)?;
            if members.len() < 2 {
                return Err(CoreError::invalid(
                    "encoded object property chain must contain at least two members",
                ));
            }
            members
                .into_iter()
                .map(|member| decode_named_object_property(member, columns))
                .collect()
        }
        _ => Err(CoreError::invalid(
            "encoded named-hierarchy compiler does not support inverse object properties",
        )),
    }
}

fn decode_entity<B: ByteSource>(
    identifier: u32,
    columns: &EncodedColumns<B>,
) -> CoreResult<Entity> {
    let node_count = aligned_count(columns.node_tags, 2, "node_tags")?;
    let node = node_index(identifier, node_count)?;
    if u16_at(columns.node_tags, node, "entity node tag")? != 2 {
        return Err(CoreError::invalid(
            "encoded named-hierarchy compiler requires named entities",
        ));
    }
    let kind = match entity_kind_at_node(node, columns)? {
        EntityKindRole::Class => EntityKind::Class,
        EntityKindRole::NamedIndividual => EntityKind::NamedIndividual,
        EntityKindRole::ObjectProperty => EntityKind::ObjectProperty,
        EntityKindRole::DataProperty => EntityKind::DataProperty,
        EntityKindRole::Datatype => EntityKind::Datatype,
        EntityKindRole::AnnotationProperty => EntityKind::AnnotationProperty,
    };
    let iri_node = node_field(node, 1, columns)?;
    let iri_index = node_index(iri_node, node_count)?;
    if u16_at(columns.node_tags, iri_index, "IRI node tag")? != 1 {
        return Err(CoreError::internal(
            "validated entity IRI resolved to a non-IRI node",
        ));
    }
    let iri = text_field(iri_index, 0, columns)?;
    if iri.is_empty() {
        return Err(CoreError::protocol("encoded entity IRI must be nonempty"));
    }
    Ok(Entity { kind, iri })
}

fn node_field<B: ByteSource>(
    node: usize,
    position: usize,
    columns: &EncodedColumns<B>,
) -> CoreResult<u32> {
    let start = usize_at(columns.node_field_offsets, node, "node field offset")?;
    let field = start
        .checked_add(position)
        .ok_or_else(|| CoreError::capacity("encoded compiler field index overflow"))?;
    node_id_at(columns.field_values, field, "field node ID")
}

fn node_collection<B: ByteSource>(
    node: usize,
    position: usize,
    columns: &EncodedColumns<B>,
) -> CoreResult<Vec<u32>> {
    let start = usize_at(columns.node_field_offsets, node, "node field offset")?;
    let field = start
        .checked_add(position)
        .ok_or_else(|| CoreError::capacity("encoded compiler field index overflow"))?;
    let item_start = usize_at(columns.field_values, field, "collection item offset")?;
    let length = usize_at(columns.field_lengths, field, "collection item length")?;
    let item_end = item_start
        .checked_add(length)
        .ok_or_else(|| CoreError::capacity("encoded compiler item range overflow"))?;
    let mut identifiers = Vec::new();
    identifiers
        .try_reserve_exact(length)
        .map_err(|_| CoreError::capacity("encoded compiler item allocation failed"))?;
    for item in item_start..item_end {
        identifiers.push(node_id_at(columns.item_values, item, "item node ID")?);
    }
    Ok(identifiers)
}

fn require_empty_annotations<B: ByteSource>(
    node: usize,
    position: usize,
    columns: &EncodedColumns<B>,
) -> CoreResult<()> {
    let start = usize_at(columns.node_field_offsets, node, "node field offset")?;
    let field = start
        .checked_add(position)
        .ok_or_else(|| CoreError::capacity("encoded compiler field index overflow"))?;
    if usize_at(columns.field_lengths, field, "annotation count")? != 0 {
        return Err(CoreError::invalid(
            "encoded named-hierarchy compiler does not yet deduplicate annotated axioms",
        ));
    }
    Ok(())
}

fn text_field<B: ByteSource>(
    node: usize,
    position: usize,
    columns: &EncodedColumns<B>,
) -> CoreResult<String> {
    let start = usize_at(columns.node_field_offsets, node, "node field offset")?;
    let field = start
        .checked_add(position)
        .ok_or_else(|| CoreError::capacity("encoded compiler field index overflow"))?;
    let scalar_start = usize_at(columns.field_values, field, "text scalar offset")?;
    let length = usize_at(columns.field_lengths, field, "text scalar length")?;
    let end = scalar_start
        .checked_add(length)
        .ok_or_else(|| CoreError::capacity("encoded compiler text range overflow"))?;
    let mut bytes = Vec::new();
    bytes
        .try_reserve_exact(length)
        .map_err(|_| CoreError::capacity("encoded compiler text allocation failed"))?;
    for index in scalar_start..end {
        bytes.push(byte_at(columns.scalar_bytes, index, "text scalar byte")?);
    }
    String::from_utf8(bytes)
        .map_err(|_| CoreError::internal("validated encoded text was not UTF-8"))
}

fn increment_occurrence(occurrence: &mut Occurrence, positive: bool) -> CoreResult<()> {
    let value = if positive {
        &mut occurrence.positive
    } else {
        &mut occurrence.negative
    };
    *value = value
        .checked_add(1)
        .ok_or_else(|| CoreError::capacity("encoded expression occurrence exceeds u64"))?;
    Ok(())
}

fn validate_graph_and_lengths<B: ByteSource>(
    columns: &EncodedColumns<B>,
    root_count: usize,
    node_count: usize,
    work: &mut u64,
    max_work: u64,
) -> CoreResult<Vec<u64>> {
    let mut states = Vec::new();
    states
        .try_reserve_exact(node_count)
        .map_err(|_| CoreError::capacity("encoded reachability state allocation failed"))?;
    states.resize(node_count, 0_u8);
    let mut canonical_lengths = Vec::new();
    canonical_lengths
        .try_reserve_exact(node_count)
        .map_err(|_| CoreError::capacity("encoded canonical length allocation failed"))?;
    canonical_lengths.resize(node_count, 0_u64);
    let mut stack = Vec::<DfsFrame>::new();
    for root in 0..root_count {
        let identifier = u32_at(columns.root_ids, root, "root ID")?;
        let node = node_index(identifier, node_count)?;
        if states[node] == 2 {
            continue;
        }
        if states[node] == 1 {
            return Err(CoreError::protocol("encoded structural graph is cyclic"));
        }
        states[node] = 1;
        push_dfs_frame(&mut stack, DfsFrame::new(node, columns)?)?;
        while let Some(frame) = stack.last_mut() {
            claim_work(work, 1, max_work)?;
            let child = if frame.item_cursor < frame.item_end {
                let item = frame.item_cursor;
                frame.item_cursor += 1;
                (byte_at(columns.item_kinds, item, "item kind")? == COMPONENT_NODE)
                    .then(|| node_id_at(columns.item_values, item, "item node ID"))
                    .transpose()?
            } else if frame.field_cursor < frame.field_end {
                let field = frame.field_cursor;
                frame.field_cursor += 1;
                match byte_at(columns.field_kinds, field, "field kind")? {
                    COMPONENT_NODE => {
                        Some(node_id_at(columns.field_values, field, "field node ID")?)
                    }
                    COMPONENT_SET | COMPONENT_SEQUENCE => {
                        frame.item_cursor =
                            usize_at(columns.field_values, field, "field item offset")?;
                        frame.item_end = frame
                            .item_cursor
                            .checked_add(usize_at(
                                columns.field_lengths,
                                field,
                                "field item length",
                            )?)
                            .ok_or_else(|| CoreError::capacity("encoded item range overflow"))?;
                        None
                    }
                    _ => None,
                }
            } else {
                let completed = frame.node;
                states[completed] = 2;
                stack.pop();
                canonical_lengths[completed] =
                    canonical_node_length(completed, columns, &canonical_lengths, work, max_work)?;
                continue;
            };
            let Some(child) = child else {
                continue;
            };
            let child = node_index(child, node_count)?;
            match states[child] {
                0 => {
                    states[child] = 1;
                    push_dfs_frame(&mut stack, DfsFrame::new(child, columns)?)?;
                }
                1 => return Err(CoreError::protocol("encoded structural graph is cyclic")),
                2 => {}
                _ => return Err(CoreError::internal("invalid encoded DFS state")),
            }
        }
    }
    claim_work(
        work,
        u64::try_from(node_count)
            .map_err(|_| CoreError::capacity("encoded reachability scan exceeds u64"))?,
        max_work,
    )?;
    if states.iter().any(|state| *state != 2) {
        return Err(CoreError::protocol(
            "encoded structural graph contains unreachable nodes",
        ));
    }
    Ok(canonical_lengths)
}

fn push_dfs_frame(stack: &mut Vec<DfsFrame>, frame: DfsFrame) -> CoreResult<()> {
    stack
        .try_reserve(1)
        .map_err(|_| CoreError::capacity("encoded graph stack allocation failed"))?;
    stack.push(frame);
    Ok(())
}

fn canonical_node_length<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    canonical_lengths: &[u64],
    work: &mut u64,
    max_work: u64,
) -> CoreResult<u64> {
    let tag = u16_at(columns.node_tags, node, "node tag")?;
    let mut total = u64::try_from(canonical_varint_width(u64::from(tag)))
        .map_err(|_| CoreError::capacity("encoded canonical length exceeds u64"))?;
    let start = usize_at(columns.node_field_offsets, node, "node field offset")?;
    let end = usize_at(columns.node_field_offsets, node + 1, "node field offset")?;
    for field in start..end {
        claim_work(work, 1, max_work)?;
        let kind = byte_at(columns.field_kinds, field, "field kind")?;
        let value = usize_at(columns.field_values, field, "field value")?;
        let length = usize_at(columns.field_lengths, field, "field length")?;
        if matches!(kind, COMPONENT_SET | COMPONENT_SEQUENCE) {
            let count = u64::try_from(length)
                .map_err(|_| CoreError::capacity("encoded collection length exceeds u64"))?;
            add_canonical_length(&mut total, 1)?;
            add_canonical_length(
                &mut total,
                u64::try_from(canonical_varint_width(count))
                    .map_err(|_| CoreError::capacity("encoded canonical length exceeds u64"))?,
            )?;
            let item_end = value
                .checked_add(length)
                .ok_or_else(|| CoreError::capacity("encoded item range overflow"))?;
            for item in value..item_end {
                claim_work(work, 1, max_work)?;
                let item_kind = byte_at(columns.item_kinds, item, "item kind")?;
                let item_value = usize_at(columns.item_values, item, "item value")?;
                let item_length = usize_at(columns.item_lengths, item, "item length")?;
                let item_size = canonical_leaf_length(
                    item_kind,
                    item_value,
                    item_length,
                    columns,
                    canonical_lengths,
                )?;
                if kind == COMPONENT_SET {
                    // Canonical sets frame node bytes directly; sequence items
                    // retain their component marker before the node frame.
                    let framed = item_size.checked_sub(1).ok_or_else(|| {
                        CoreError::internal("canonical set item has no node marker")
                    })?;
                    add_canonical_length(&mut total, framed)?;
                } else {
                    add_canonical_length(&mut total, item_size)?;
                }
            }
        } else {
            add_canonical_length(
                &mut total,
                canonical_leaf_length(kind, value, length, columns, canonical_lengths)?,
            )?;
        }
    }
    Ok(total)
}

fn canonical_leaf_length<B: ByteSource>(
    kind: u8,
    value: usize,
    length: usize,
    columns: &EncodedColumns<B>,
    canonical_lengths: &[u64],
) -> CoreResult<u64> {
    match kind {
        COMPONENT_NONE => Ok(1),
        COMPONENT_NODE => {
            let identifier = u32::try_from(value)
                .map_err(|_| CoreError::protocol("encoded node ID exceeds u32"))?;
            let node = node_index(identifier, canonical_lengths.len())?;
            let nested = canonical_lengths[node];
            if nested == 0 {
                return Err(CoreError::internal(
                    "canonical child length was not computed before its parent",
                ));
            }
            let mut total = 1_u64;
            add_canonical_length(
                &mut total,
                u64::try_from(canonical_varint_width(nested))
                    .map_err(|_| CoreError::capacity("encoded canonical length exceeds u64"))?,
            )?;
            add_canonical_length(&mut total, nested)?;
            Ok(total)
        }
        COMPONENT_TEXT | COMPONENT_BYTES | COMPONENT_ENUM => {
            let payload = u64::try_from(length)
                .map_err(|_| CoreError::capacity("encoded scalar length exceeds u64"))?;
            let mut total = 1_u64;
            add_canonical_length(
                &mut total,
                u64::try_from(canonical_varint_width(payload))
                    .map_err(|_| CoreError::capacity("encoded canonical length exceeds u64"))?,
            )?;
            add_canonical_length(&mut total, payload)?;
            Ok(total)
        }
        COMPONENT_INTEGER => {
            let width = canonical_integer_varint_width(columns.scalar_bytes, value, length)?;
            1_u64
                .checked_add(
                    u64::try_from(width)
                        .map_err(|_| CoreError::capacity("encoded integer width exceeds u64"))?,
                )
                .ok_or_else(|| CoreError::capacity("encoded canonical length exceeds u64"))
        }
        COMPONENT_SET | COMPONENT_SEQUENCE => Err(CoreError::internal(
            "nested collection reached canonical leaf sizing",
        )),
        _ => Err(CoreError::internal(
            "invalid component reached canonical leaf sizing",
        )),
    }
}

fn add_canonical_length(total: &mut u64, amount: u64) -> CoreResult<()> {
    *total = total
        .checked_add(amount)
        .ok_or_else(|| CoreError::capacity("encoded canonical model length exceeds u64"))?;
    Ok(())
}

#[derive(Clone, Copy)]
enum ComponentRow {
    Field(usize),
    Item(usize),
}

#[derive(Clone, Copy)]
struct ScalarRange {
    start: usize,
    length: usize,
}

#[derive(Clone, Copy)]
enum CanonicalCompareTask {
    Node {
        left: usize,
        right: usize,
    },
    Fields {
        left: usize,
        right: usize,
        remaining: usize,
    },
    Collection {
        kind: u8,
        left: usize,
        right: usize,
        remaining: usize,
    },
}

fn validate_dense_node_order<B: ByteSource>(
    columns: &EncodedColumns<B>,
    canonical_lengths: &[u64],
    work: &mut u64,
    max_work: u64,
) -> CoreResult<()> {
    for right in 1..canonical_lengths.len() {
        let left = right - 1;
        if compare_canonical_nodes(left, right, columns, canonical_lengths, work, max_work)?
            != Ordering::Less
        {
            return Err(CoreError::protocol(
                "encoded structural node IDs are not canonical and unique",
            ));
        }
    }
    Ok(())
}

fn compare_canonical_nodes<B: ByteSource>(
    left: usize,
    right: usize,
    columns: &EncodedColumns<B>,
    canonical_lengths: &[u64],
    work: &mut u64,
    max_work: u64,
) -> CoreResult<Ordering> {
    let mut tasks = Vec::new();
    push_compare_task(&mut tasks, CanonicalCompareTask::Node { left, right })?;
    while let Some(task) = tasks.pop() {
        claim_work(work, 1, max_work)?;
        match task {
            CanonicalCompareTask::Node { left, right } => {
                if left == right {
                    continue;
                }
                let left_tag = u64::from(u16_at(columns.node_tags, left, "node tag")?);
                let right_tag = u64::from(u16_at(columns.node_tags, right, "node tag")?);
                let ordering = compare_u64_varints(left_tag, right_tag);
                if ordering != Ordering::Equal {
                    return Ok(ordering);
                }
                let left_start = usize_at(columns.node_field_offsets, left, "node field offset")?;
                let left_end = usize_at(columns.node_field_offsets, left + 1, "node field offset")?;
                let right_start = usize_at(columns.node_field_offsets, right, "node field offset")?;
                let right_end =
                    usize_at(columns.node_field_offsets, right + 1, "node field offset")?;
                let remaining = left_end - left_start;
                if right_end - right_start != remaining {
                    return Err(CoreError::internal(
                        "equal constructor tags have different validated arities",
                    ));
                }
                push_compare_task(
                    &mut tasks,
                    CanonicalCompareTask::Fields {
                        left: left_start,
                        right: right_start,
                        remaining,
                    },
                )?;
            }
            CanonicalCompareTask::Fields {
                left,
                right,
                remaining,
            } => {
                if remaining == 0 {
                    continue;
                }
                push_compare_task(
                    &mut tasks,
                    CanonicalCompareTask::Fields {
                        left: left
                            .checked_add(1)
                            .ok_or_else(|| CoreError::capacity("encoded field index overflow"))?,
                        right: right
                            .checked_add(1)
                            .ok_or_else(|| CoreError::capacity("encoded field index overflow"))?,
                        remaining: remaining - 1,
                    },
                )?;
                if let Some(ordering) = schedule_component_comparison(
                    ComponentRow::Field(left),
                    ComponentRow::Field(right),
                    columns,
                    canonical_lengths,
                    &mut tasks,
                    work,
                    max_work,
                )? {
                    return Ok(ordering);
                }
            }
            CanonicalCompareTask::Collection {
                kind,
                left,
                right,
                remaining,
            } => {
                if remaining == 0 {
                    continue;
                }
                push_compare_task(
                    &mut tasks,
                    CanonicalCompareTask::Collection {
                        kind,
                        left: left
                            .checked_add(1)
                            .ok_or_else(|| CoreError::capacity("encoded item index overflow"))?,
                        right: right
                            .checked_add(1)
                            .ok_or_else(|| CoreError::capacity("encoded item index overflow"))?,
                        remaining: remaining - 1,
                    },
                )?;
                if kind == COMPONENT_SET {
                    let left_node = node_index(
                        node_id_at(columns.item_values, left, "set item node ID")?,
                        canonical_lengths.len(),
                    )?;
                    let right_node = node_index(
                        node_id_at(columns.item_values, right, "set item node ID")?,
                        canonical_lengths.len(),
                    )?;
                    let ordering = compare_u64_varints(
                        canonical_lengths[left_node],
                        canonical_lengths[right_node],
                    );
                    if ordering != Ordering::Equal {
                        return Ok(ordering);
                    }
                    push_compare_task(
                        &mut tasks,
                        CanonicalCompareTask::Node {
                            left: left_node,
                            right: right_node,
                        },
                    )?;
                } else if let Some(ordering) = schedule_component_comparison(
                    ComponentRow::Item(left),
                    ComponentRow::Item(right),
                    columns,
                    canonical_lengths,
                    &mut tasks,
                    work,
                    max_work,
                )? {
                    return Ok(ordering);
                }
            }
        }
    }
    Ok(Ordering::Equal)
}

fn schedule_component_comparison<B: ByteSource>(
    left: ComponentRow,
    right: ComponentRow,
    columns: &EncodedColumns<B>,
    canonical_lengths: &[u64],
    tasks: &mut Vec<CanonicalCompareTask>,
    work: &mut u64,
    max_work: u64,
) -> CoreResult<Option<Ordering>> {
    let (left_kind, left_value, left_length) = component_parts(left, columns)?;
    let (right_kind, right_value, right_length) = component_parts(right, columns)?;
    let ordering = left_kind.cmp(&right_kind);
    if ordering != Ordering::Equal {
        return Ok(Some(ordering));
    }
    match left_kind {
        COMPONENT_NONE => Ok(None),
        COMPONENT_NODE => {
            let left_node = node_index(
                u32::try_from(left_value)
                    .map_err(|_| CoreError::protocol("encoded node ID exceeds u32"))?,
                canonical_lengths.len(),
            )?;
            let right_node = node_index(
                u32::try_from(right_value)
                    .map_err(|_| CoreError::protocol("encoded node ID exceeds u32"))?,
                canonical_lengths.len(),
            )?;
            let ordering =
                compare_u64_varints(canonical_lengths[left_node], canonical_lengths[right_node]);
            if ordering != Ordering::Equal {
                return Ok(Some(ordering));
            }
            push_compare_task(
                tasks,
                CanonicalCompareTask::Node {
                    left: left_node,
                    right: right_node,
                },
            )?;
            Ok(None)
        }
        COMPONENT_TEXT | COMPONENT_BYTES | COMPONENT_ENUM => {
            let left_size = u64::try_from(left_length)
                .map_err(|_| CoreError::capacity("encoded scalar length exceeds u64"))?;
            let right_size = u64::try_from(right_length)
                .map_err(|_| CoreError::capacity("encoded scalar length exceeds u64"))?;
            let ordering = compare_u64_varints(left_size, right_size);
            if ordering != Ordering::Equal {
                return Ok(Some(ordering));
            }
            let ordering = compare_scalar_ranges(
                columns.scalar_bytes,
                left_value,
                right_value,
                left_length,
                work,
                max_work,
            )?;
            Ok((ordering != Ordering::Equal).then_some(ordering))
        }
        COMPONENT_INTEGER => {
            let ordering = compare_integer_components(
                columns.scalar_bytes,
                ScalarRange {
                    start: left_value,
                    length: left_length,
                },
                ScalarRange {
                    start: right_value,
                    length: right_length,
                },
                work,
                max_work,
            )?;
            Ok((ordering != Ordering::Equal).then_some(ordering))
        }
        COMPONENT_SET | COMPONENT_SEQUENCE => {
            let left_size = u64::try_from(left_length)
                .map_err(|_| CoreError::capacity("encoded collection length exceeds u64"))?;
            let right_size = u64::try_from(right_length)
                .map_err(|_| CoreError::capacity("encoded collection length exceeds u64"))?;
            let ordering = compare_u64_varints(left_size, right_size);
            if ordering != Ordering::Equal {
                return Ok(Some(ordering));
            }
            push_compare_task(
                tasks,
                CanonicalCompareTask::Collection {
                    kind: left_kind,
                    left: left_value,
                    right: right_value,
                    remaining: left_length,
                },
            )?;
            Ok(None)
        }
        _ => Err(CoreError::internal(
            "invalid component reached canonical comparison",
        )),
    }
}

fn component_parts<B: ByteSource>(
    row: ComponentRow,
    columns: &EncodedColumns<B>,
) -> CoreResult<(u8, usize, usize)> {
    match row {
        ComponentRow::Field(index) => Ok((
            byte_at(columns.field_kinds, index, "field kind")?,
            usize_at(columns.field_values, index, "field value")?,
            usize_at(columns.field_lengths, index, "field length")?,
        )),
        ComponentRow::Item(index) => Ok((
            byte_at(columns.item_kinds, index, "item kind")?,
            usize_at(columns.item_values, index, "item value")?,
            usize_at(columns.item_lengths, index, "item length")?,
        )),
    }
}

fn compare_scalar_ranges<B: ByteSource>(
    scalars: B,
    left: usize,
    right: usize,
    length: usize,
    work: &mut u64,
    max_work: u64,
) -> CoreResult<Ordering> {
    if left == right {
        return Ok(Ordering::Equal);
    }
    claim_work(
        work,
        u64::try_from(length)
            .map_err(|_| CoreError::capacity("encoded scalar comparison exceeds u64"))?,
        max_work,
    )?;
    for offset in 0..length {
        let left_byte = byte_at(
            scalars,
            left.checked_add(offset)
                .ok_or_else(|| CoreError::capacity("encoded scalar offset overflow"))?,
            "canonical scalar",
        )?;
        let right_byte = byte_at(
            scalars,
            right
                .checked_add(offset)
                .ok_or_else(|| CoreError::capacity("encoded scalar offset overflow"))?,
            "canonical scalar",
        )?;
        let ordering = left_byte.cmp(&right_byte);
        if ordering != Ordering::Equal {
            return Ok(ordering);
        }
    }
    Ok(Ordering::Equal)
}

fn compare_integer_components<B: ByteSource>(
    scalars: B,
    left: ScalarRange,
    right: ScalarRange,
    work: &mut u64,
    max_work: u64,
) -> CoreResult<Ordering> {
    let left_width = canonical_integer_varint_width(scalars, left.start, left.length)?;
    let right_width = canonical_integer_varint_width(scalars, right.start, right.length)?;
    let compared = left_width.max(right_width);
    claim_work(
        work,
        u64::try_from(compared)
            .map_err(|_| CoreError::capacity("encoded integer comparison exceeds u64"))?,
        max_work,
    )?;
    for index in 0..compared {
        let left_byte = (index < left_width)
            .then(|| integer_varint_byte(scalars, left.start, left.length, index, left_width));
        let right_byte = (index < right_width)
            .then(|| integer_varint_byte(scalars, right.start, right.length, index, right_width));
        let ordering = match (left_byte, right_byte) {
            (Some(left_byte), Some(right_byte)) => left_byte?.cmp(&right_byte?),
            (Some(_), None) => Ordering::Greater,
            (None, Some(_)) => Ordering::Less,
            (None, None) => Ordering::Equal,
        };
        if ordering != Ordering::Equal {
            return Ok(ordering);
        }
    }
    Ok(Ordering::Equal)
}

fn canonical_integer_varint_width<B: ByteSource>(
    scalars: B,
    start: usize,
    length: usize,
) -> CoreResult<usize> {
    let last = length
        .checked_sub(1)
        .ok_or_else(|| CoreError::internal("validated integer has an empty payload"))?;
    let high = byte_at(
        scalars,
        start
            .checked_add(last)
            .ok_or_else(|| CoreError::capacity("encoded integer offset overflow"))?,
        "integer scalar",
    )?;
    let lower_bits = last
        .checked_mul(8)
        .ok_or_else(|| CoreError::capacity("encoded integer bit length overflow"))?;
    let high_bits = usize::try_from(u8::BITS - high.leading_zeros())
        .map_err(|_| CoreError::capacity("encoded integer bit length exceeds usize"))?;
    let bit_length = lower_bits
        .checked_add(high_bits)
        .ok_or_else(|| CoreError::capacity("encoded integer bit length overflow"))?;
    Ok(bit_length.div_ceil(7).max(1))
}

fn integer_varint_byte<B: ByteSource>(
    scalars: B,
    start: usize,
    payload_length: usize,
    index: usize,
    encoded_width: usize,
) -> CoreResult<u8> {
    let bit_offset = index
        .checked_mul(7)
        .ok_or_else(|| CoreError::capacity("encoded integer bit offset overflow"))?;
    let source_index = bit_offset / 8;
    let shift = u32::try_from(bit_offset % 8)
        .map_err(|_| CoreError::capacity("encoded integer bit shift exceeds u32"))?;
    let absolute = start
        .checked_add(source_index)
        .ok_or_else(|| CoreError::capacity("encoded integer offset overflow"))?;
    let mut window = u16::from(byte_at(scalars, absolute, "integer scalar")?) >> shift;
    if shift != 0 && source_index + 1 < payload_length {
        window |= u16::from(byte_at(scalars, absolute + 1, "integer scalar")?) << (8 - shift);
    }
    let mut output = u8::try_from(window & 0x7f)
        .map_err(|_| CoreError::internal("integer varint chunk exceeds u8"))?;
    if index + 1 < encoded_width {
        output |= 0x80;
    }
    Ok(output)
}

fn compare_u64_varints(left: u64, right: u64) -> Ordering {
    let left_width = canonical_varint_width(left);
    let right_width = canonical_varint_width(right);
    for index in 0..left_width.max(right_width) {
        let left_byte = (index < left_width).then(|| u64_varint_byte(left, index, left_width));
        let right_byte = (index < right_width).then(|| u64_varint_byte(right, index, right_width));
        let ordering = left_byte.cmp(&right_byte);
        if ordering != Ordering::Equal {
            return ordering;
        }
    }
    Ordering::Equal
}

fn canonical_varint_width(value: u64) -> usize {
    let bits = (u64::BITS - value.leading_zeros()) as usize;
    bits.div_ceil(7).max(1)
}

fn u64_varint_byte(value: u64, index: usize, width: usize) -> u8 {
    debug_assert!(index < width && width <= 10);
    let shift = (index * 7) as u32;
    let mut output = ((value >> shift) & 0x7f) as u8;
    if index + 1 < width {
        output |= 0x80;
    }
    output
}

fn push_compare_task(
    tasks: &mut Vec<CanonicalCompareTask>,
    task: CanonicalCompareTask,
) -> CoreResult<()> {
    tasks
        .try_reserve(1)
        .map_err(|_| CoreError::capacity("encoded canonical comparison stack allocation failed"))?;
    tasks.push(task);
    Ok(())
}

#[derive(Clone, Copy)]
struct FieldLocation {
    tag: u16,
    position: usize,
}

fn validate_field_role<B: ByteSource>(
    location: FieldLocation,
    role: FieldRole,
    kind: u8,
    value: usize,
    length: usize,
    columns: &EncodedColumns<B>,
    node_count: usize,
) -> CoreResult<()> {
    match role {
        FieldRole::Scalar(expected) if kind == expected => Ok(()),
        FieldRole::EntityKind if kind == COMPONENT_ENUM => {
            entity_kind_scalar(columns.scalar_bytes, value, length).map(drop)
        }
        FieldRole::OptionalText if matches!(kind, COMPONENT_NONE | COMPONENT_TEXT) => Ok(()),
        FieldRole::Node(expected) if kind == COMPONENT_NODE => {
            if node_role_accepts(expected, value, columns, node_count)? {
                Ok(())
            } else {
                Err(field_role_error(location))
            }
        }
        FieldRole::Set(_) | FieldRole::Sequence(_) => Err(CoreError::internal(
            "collection role reached scalar field validation",
        )),
        _ => Err(field_role_error(location)),
    }
}

fn validate_collection_item_role<B: ByteSource>(
    location: FieldLocation,
    role: NodeRole,
    kind: u8,
    value: usize,
    columns: &EncodedColumns<B>,
    node_count: usize,
) -> CoreResult<()> {
    if kind == COMPONENT_NODE && node_role_accepts(role, value, columns, node_count)? {
        Ok(())
    } else {
        let FieldLocation { tag, position } = location;
        Err(CoreError::protocol(format!(
            "encoded node tag {tag} field {position} collection item has the wrong schema role"
        )))
    }
}

fn node_role_accepts<B: ByteSource>(
    role: NodeRole,
    identifier: usize,
    columns: &EncodedColumns<B>,
    node_count: usize,
) -> CoreResult<bool> {
    let identifier = u32::try_from(identifier)
        .map_err(|_| CoreError::protocol("encoded node ID exceeds u32"))?;
    let node = node_index(identifier, node_count)?;
    let tag = u16_at(columns.node_tags, node, "referenced node tag")?;
    match role {
        NodeRole::Iri => Ok(tag == 1),
        NodeRole::Entity => entity_role_accepts(tag, node, None, columns),
        NodeRole::Class => entity_role_accepts(tag, node, Some(EntityKindRole::Class), columns),
        NodeRole::Datatype => {
            entity_role_accepts(tag, node, Some(EntityKindRole::Datatype), columns)
        }
        NodeRole::ObjectProperty => {
            entity_role_accepts(tag, node, Some(EntityKindRole::ObjectProperty), columns)
        }
        NodeRole::DataProperty => {
            entity_role_accepts(tag, node, Some(EntityKindRole::DataProperty), columns)
        }
        NodeRole::AnnotationProperty => {
            entity_role_accepts(tag, node, Some(EntityKindRole::AnnotationProperty), columns)
        }
        NodeRole::Literal => Ok(tag == 4),
        NodeRole::Annotation => Ok(tag == 5),
        NodeRole::ObjectPropertyExpression => {
            if tag == 10 {
                Ok(true)
            } else {
                entity_role_accepts(tag, node, Some(EntityKindRole::ObjectProperty), columns)
            }
        }
        NodeRole::SubObjectPropertyExpression => {
            if matches!(tag, 10 | 11) {
                Ok(true)
            } else {
                entity_role_accepts(tag, node, Some(EntityKindRole::ObjectProperty), columns)
            }
        }
        NodeRole::FacetRestriction => Ok(tag == 20),
        NodeRole::DataRange => {
            if matches!(tag, 21..=25) {
                Ok(true)
            } else {
                entity_role_accepts(tag, node, Some(EntityKindRole::Datatype), columns)
            }
        }
        NodeRole::ClassExpression => {
            if matches!(tag, 30..=46) {
                Ok(true)
            } else {
                entity_role_accepts(tag, node, Some(EntityKindRole::Class), columns)
            }
        }
        NodeRole::Individual => {
            if tag == 3 {
                Ok(true)
            } else {
                entity_role_accepts(tag, node, Some(EntityKindRole::NamedIndividual), columns)
            }
        }
        NodeRole::AnnotationValue => Ok(matches!(tag, 1 | 3 | 4)),
        NodeRole::AnnotationSubject => Ok(matches!(tag, 1 | 3)),
        NodeRole::IndividualArgument => {
            if matches!(tag, 3 | 140) {
                Ok(true)
            } else {
                entity_role_accepts(tag, node, Some(EntityKindRole::NamedIndividual), columns)
            }
        }
        NodeRole::DataArgument => Ok(matches!(tag, 4 | 140)),
        NodeRole::Atom => Ok(matches!(tag, 141..=147)),
    }
}

fn entity_role_accepts<B: ByteSource>(
    tag: u16,
    node: usize,
    expected: Option<EntityKindRole>,
    columns: &EncodedColumns<B>,
) -> CoreResult<bool> {
    if tag != 2 {
        return Ok(false);
    }
    let actual = entity_kind_at_node(node, columns)?;
    Ok(expected.is_none_or(|selected| selected == actual))
}

fn entity_kind_at_node<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
) -> CoreResult<EntityKindRole> {
    let field = usize_at(columns.node_field_offsets, node, "entity field offset")?;
    if byte_at(columns.field_kinds, field, "entity kind")? != COMPONENT_ENUM {
        return Err(CoreError::protocol(
            "encoded entity kind has the wrong schema role",
        ));
    }
    entity_kind_scalar(
        columns.scalar_bytes,
        usize_at(columns.field_values, field, "entity kind value")?,
        usize_at(columns.field_lengths, field, "entity kind length")?,
    )
}

fn entity_kind_scalar<B: ByteSource>(
    scalars: B,
    start: usize,
    length: usize,
) -> CoreResult<EntityKindRole> {
    const KINDS: &[(EntityKindRole, &[u8])] = &[
        (EntityKindRole::Class, b"class"),
        (EntityKindRole::Datatype, b"datatype"),
        (EntityKindRole::ObjectProperty, b"object_property"),
        (EntityKindRole::DataProperty, b"data_property"),
        (EntityKindRole::AnnotationProperty, b"annotation_property"),
        (EntityKindRole::NamedIndividual, b"named_individual"),
    ];
    for (kind, expected) in KINDS {
        if scalar_equals(scalars, start, length, expected)? {
            return Ok(*kind);
        }
    }
    Err(CoreError::protocol(
        "encoded entity kind is not a model-schema-1 value",
    ))
}

fn scalar_equals<B: ByteSource>(
    scalars: B,
    start: usize,
    length: usize,
    expected: &[u8],
) -> CoreResult<bool> {
    let end = start
        .checked_add(length)
        .ok_or_else(|| CoreError::capacity("encoded scalar range overflow"))?;
    if end > scalars.len() {
        return Err(CoreError::protocol(
            "encoded scalar component is out of bounds",
        ));
    }
    if length != expected.len() {
        return Ok(false);
    }
    for (offset, byte) in expected.iter().copied().enumerate() {
        if byte_at(scalars, start + offset, "entity kind scalar")? != byte {
            return Ok(false);
        }
    }
    Ok(true)
}

fn field_role_error(location: FieldLocation) -> CoreError {
    let FieldLocation { tag, position } = location;
    CoreError::protocol(format!(
        "encoded node tag {tag} field {position} has the wrong schema role"
    ))
}

fn validate_leaf_component<B: ByteSource>(
    kind: u8,
    value: usize,
    length: usize,
    node_count: usize,
    scalars: B,
    scalar_cursor: &mut usize,
) -> CoreResult<()> {
    match kind {
        COMPONENT_NONE => {
            if value != 0 || length != 0 {
                return Err(CoreError::protocol(
                    "encoded none component must have zero value and length",
                ));
            }
        }
        COMPONENT_NODE => {
            if length != 0 {
                return Err(CoreError::protocol(
                    "encoded node component must have zero length",
                ));
            }
            let identifier = u32::try_from(value)
                .map_err(|_| CoreError::protocol("encoded node ID exceeds u32"))?;
            node_index(identifier, node_count)?;
        }
        COMPONENT_TEXT | COMPONENT_BYTES | COMPONENT_INTEGER | COMPONENT_ENUM => {
            if value != *scalar_cursor {
                return Err(CoreError::protocol(
                    "encoded scalar components do not exactly cover the scalar arena",
                ));
            }
            let end = value
                .checked_add(length)
                .ok_or_else(|| CoreError::capacity("encoded scalar range overflow"))?;
            if end > scalars.len() {
                return Err(CoreError::protocol(
                    "encoded scalar component is out of bounds",
                ));
            }
            match kind {
                COMPONENT_TEXT => validate_utf8(scalars, value, end)?,
                COMPONENT_INTEGER => {
                    if length == 0
                        || (length > 1 && byte_at(scalars, end - 1, "integer scalar")? == 0)
                    {
                        return Err(CoreError::protocol(
                            "encoded integer component is not minimal little-endian",
                        ));
                    }
                }
                COMPONENT_ENUM => {
                    if length == 0 {
                        return Err(CoreError::protocol(
                            "encoded enum component must be nonempty ASCII",
                        ));
                    }
                    for index in value..end {
                        if !byte_at(scalars, index, "enum scalar")?.is_ascii() {
                            return Err(CoreError::protocol(
                                "encoded enum component must be nonempty ASCII",
                            ));
                        }
                    }
                }
                COMPONENT_BYTES => {}
                _ => unreachable!(),
            }
            *scalar_cursor = end;
        }
        COMPONENT_SET | COMPONENT_SEQUENCE => {
            return Err(CoreError::protocol(
                "encoded nested collection item is not supported by schema 1",
            ));
        }
        _ => return Err(CoreError::protocol("unknown encoded component kind")),
    }
    Ok(())
}

fn validate_utf8<B: ByteSource>(bytes: B, start: usize, end: usize) -> CoreResult<()> {
    let mut cursor = start;
    while cursor < end {
        let first = byte_at(bytes, cursor, "text scalar")?;
        cursor += 1;
        match first {
            0x00..=0x7f => {}
            0xc2..=0xdf => {
                require_continuation(bytes, &mut cursor, end)?;
            }
            0xe0 => {
                let second = next_text_byte(bytes, &mut cursor, end)?;
                if !(0xa0..=0xbf).contains(&second) {
                    return Err(CoreError::protocol(
                        "encoded text component is not valid UTF-8",
                    ));
                }
                require_continuation(bytes, &mut cursor, end)?;
            }
            0xe1..=0xec | 0xee..=0xef => {
                require_continuation(bytes, &mut cursor, end)?;
                require_continuation(bytes, &mut cursor, end)?;
            }
            0xed => {
                let second = next_text_byte(bytes, &mut cursor, end)?;
                if !(0x80..=0x9f).contains(&second) {
                    return Err(CoreError::protocol(
                        "encoded text component is not valid UTF-8",
                    ));
                }
                require_continuation(bytes, &mut cursor, end)?;
            }
            0xf0 => {
                let second = next_text_byte(bytes, &mut cursor, end)?;
                if !(0x90..=0xbf).contains(&second) {
                    return Err(CoreError::protocol(
                        "encoded text component is not valid UTF-8",
                    ));
                }
                require_continuation(bytes, &mut cursor, end)?;
                require_continuation(bytes, &mut cursor, end)?;
            }
            0xf1..=0xf3 => {
                require_continuation(bytes, &mut cursor, end)?;
                require_continuation(bytes, &mut cursor, end)?;
                require_continuation(bytes, &mut cursor, end)?;
            }
            0xf4 => {
                let second = next_text_byte(bytes, &mut cursor, end)?;
                if !(0x80..=0x8f).contains(&second) {
                    return Err(CoreError::protocol(
                        "encoded text component is not valid UTF-8",
                    ));
                }
                require_continuation(bytes, &mut cursor, end)?;
                require_continuation(bytes, &mut cursor, end)?;
            }
            _ => {
                return Err(CoreError::protocol(
                    "encoded text component is not valid UTF-8",
                ));
            }
        }
    }
    Ok(())
}

fn next_text_byte<B: ByteSource>(bytes: B, cursor: &mut usize, end: usize) -> CoreResult<u8> {
    if *cursor >= end {
        return Err(CoreError::protocol(
            "encoded text component is not valid UTF-8",
        ));
    }
    let byte = byte_at(bytes, *cursor, "text scalar")?;
    *cursor += 1;
    Ok(byte)
}

fn require_continuation<B: ByteSource>(bytes: B, cursor: &mut usize, end: usize) -> CoreResult<()> {
    if !(0x80..=0xbf).contains(&next_text_byte(bytes, cursor, end)?) {
        return Err(CoreError::protocol(
            "encoded text component is not valid UTF-8",
        ));
    }
    Ok(())
}

fn root_accepts(kind: u8, tag: u16) -> bool {
    match kind {
        ROOT_ONTOLOGY_ANNOTATION => tag == 5,
        ROOT_AXIOM => matches!(
            tag,
            60..=64 | 70..=82 | 90..=95 | 100..=101 | 110..=116 | 120..=123
        ),
        ROOT_EXTENSION => tag == 148,
        _ => false,
    }
}

fn node_index(identifier: u32, node_count: usize) -> CoreResult<usize> {
    let index = identifier
        .checked_sub(1)
        .and_then(|value| usize::try_from(value).ok())
        .ok_or_else(|| CoreError::protocol("encoded node IDs are one-based and nonzero"))?;
    if index >= node_count {
        return Err(CoreError::protocol("encoded node ID is out of range"));
    }
    Ok(index)
}

fn byte_at<B: ByteSource>(bytes: B, index: usize, name: &str) -> CoreResult<u8> {
    bytes
        .byte(index)
        .ok_or_else(|| CoreError::protocol(format!("encoded {name} is truncated")))
}

fn aligned_count<B: ByteSource>(bytes: B, width: usize, name: &str) -> CoreResult<usize> {
    if bytes.len() % width != 0 {
        return Err(CoreError::protocol(format!(
            "encoded {name} is not aligned to {width} bytes"
        )));
    }
    Ok(bytes.len() / width)
}

fn u16_at<B: ByteSource>(bytes: B, index: usize, name: &str) -> CoreResult<u16> {
    let start = index
        .checked_mul(2)
        .ok_or_else(|| CoreError::capacity(format!("encoded {name} offset overflow")))?;
    let raw = [
        byte_at(bytes, start, name)?,
        byte_at(bytes, start + 1, name)?,
    ];
    Ok(u16::from_le_bytes(raw))
}

fn u32_at<B: ByteSource>(bytes: B, index: usize, name: &str) -> CoreResult<u32> {
    let start = index
        .checked_mul(4)
        .ok_or_else(|| CoreError::capacity(format!("encoded {name} offset overflow")))?;
    let raw = [
        byte_at(bytes, start, name)?,
        byte_at(bytes, start + 1, name)?,
        byte_at(bytes, start + 2, name)?,
        byte_at(bytes, start + 3, name)?,
    ];
    Ok(u32::from_le_bytes(raw))
}

fn u64_at<B: ByteSource>(bytes: B, index: usize, name: &str) -> CoreResult<u64> {
    let start = index
        .checked_mul(8)
        .ok_or_else(|| CoreError::capacity(format!("encoded {name} offset overflow")))?;
    let raw = [
        byte_at(bytes, start, name)?,
        byte_at(bytes, start + 1, name)?,
        byte_at(bytes, start + 2, name)?,
        byte_at(bytes, start + 3, name)?,
        byte_at(bytes, start + 4, name)?,
        byte_at(bytes, start + 5, name)?,
        byte_at(bytes, start + 6, name)?,
        byte_at(bytes, start + 7, name)?,
    ];
    Ok(u64::from_le_bytes(raw))
}

fn node_id_at<B: ByteSource>(bytes: B, index: usize, name: &str) -> CoreResult<u32> {
    u32::try_from(u64_at(bytes, index, name)?)
        .map_err(|_| CoreError::protocol(format!("encoded {name} exceeds u32")))
}

fn usize_at<B: ByteSource>(bytes: B, index: usize, name: &str) -> CoreResult<usize> {
    usize::try_from(u64_at(bytes, index, name)?)
        .map_err(|_| CoreError::capacity(format!("encoded {name} exceeds usize")))
}

fn enforce_count(value: usize, maximum: usize, name: &str) -> CoreResult<()> {
    if value > maximum {
        Err(CoreError::capacity(format!("{name} exceeds its limit")))
    } else {
        Ok(())
    }
}

fn claim_work(work: &mut u64, amount: u64, maximum: u64) -> CoreResult<()> {
    let following = work
        .checked_add(amount)
        .ok_or_else(|| CoreError::capacity("encoded validation work overflow"))?;
    if following > maximum {
        return Err(CoreError::capacity(
            "encoded validation exceeds its work limit",
        ));
    }
    *work = following;
    Ok(())
}

fn claim_leaf_scan_work(kind: u8, length: usize, work: &mut u64, maximum: u64) -> CoreResult<()> {
    if matches!(kind, COMPONENT_TEXT | COMPONENT_ENUM) {
        claim_work(
            work,
            u64::try_from(length)
                .map_err(|_| CoreError::capacity("encoded scalar scan exceeds u64"))?,
            maximum,
        )?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Clone, Copy)]
    struct IndexedBytes<'a>(&'a [u8]);

    impl ByteSource for IndexedBytes<'_> {
        fn len(self) -> usize {
            self.0.len()
        }

        fn byte(self, index: usize) -> Option<u8> {
            self.0.get(index).copied()
        }
    }

    #[derive(Clone, Debug)]
    struct OwnedColumns {
        root_kinds: Vec<u8>,
        root_ids: Vec<u8>,
        node_tags: Vec<u8>,
        node_field_offsets: Vec<u8>,
        field_kinds: Vec<u8>,
        field_values: Vec<u8>,
        field_lengths: Vec<u8>,
        item_kinds: Vec<u8>,
        item_values: Vec<u8>,
        item_lengths: Vec<u8>,
        scalar_bytes: Vec<u8>,
    }

    impl OwnedColumns {
        fn borrowed(&self) -> EncodedColumns<&[u8]> {
            EncodedColumns {
                root_kinds: self.root_kinds.as_slice(),
                root_ids: self.root_ids.as_slice(),
                node_tags: self.node_tags.as_slice(),
                node_field_offsets: self.node_field_offsets.as_slice(),
                field_kinds: self.field_kinds.as_slice(),
                field_values: self.field_values.as_slice(),
                field_lengths: self.field_lengths.as_slice(),
                item_kinds: self.item_kinds.as_slice(),
                item_values: self.item_values.as_slice(),
                item_lengths: self.item_lengths.as_slice(),
                scalar_bytes: self.scalar_bytes.as_slice(),
            }
        }

        fn indexed(&self) -> EncodedColumns<IndexedBytes<'_>> {
            EncodedColumns {
                root_kinds: IndexedBytes(&self.root_kinds),
                root_ids: IndexedBytes(&self.root_ids),
                node_tags: IndexedBytes(&self.node_tags),
                node_field_offsets: IndexedBytes(&self.node_field_offsets),
                field_kinds: IndexedBytes(&self.field_kinds),
                field_values: IndexedBytes(&self.field_values),
                field_lengths: IndexedBytes(&self.field_lengths),
                item_kinds: IndexedBytes(&self.item_kinds),
                item_values: IndexedBytes(&self.item_values),
                item_lengths: IndexedBytes(&self.item_lengths),
                scalar_bytes: IndexedBytes(&self.scalar_bytes),
            }
        }
    }

    fn le16(values: &[u16]) -> Vec<u8> {
        values
            .iter()
            .flat_map(|value| value.to_le_bytes())
            .collect()
    }

    fn le32(values: &[u32]) -> Vec<u8> {
        values
            .iter()
            .flat_map(|value| value.to_le_bytes())
            .collect()
    }

    fn le64(values: &[u64]) -> Vec<u8> {
        values
            .iter()
            .flat_map(|value| value.to_le_bytes())
            .collect()
    }

    fn empty() -> OwnedColumns {
        OwnedColumns {
            root_kinds: Vec::new(),
            root_ids: Vec::new(),
            node_tags: Vec::new(),
            node_field_offsets: le64(&[0]),
            field_kinds: Vec::new(),
            field_values: Vec::new(),
            field_lengths: Vec::new(),
            item_kinds: Vec::new(),
            item_values: Vec::new(),
            item_lengths: Vec::new(),
            scalar_bytes: Vec::new(),
        }
    }

    fn declaration() -> OwnedColumns {
        OwnedColumns {
            root_kinds: vec![ROOT_AXIOM],
            root_ids: le32(&[3]),
            node_tags: le16(&[1, 2, 60]),
            node_field_offsets: le64(&[0, 1, 3, 5]),
            field_kinds: vec![
                COMPONENT_TEXT,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_SET,
            ],
            field_values: le64(&[0, 5, 1, 2, 0]),
            field_lengths: le64(&[5, 5, 0, 0, 0]),
            item_kinds: Vec::new(),
            item_values: Vec::new(),
            item_lengths: Vec::new(),
            scalar_bytes: b"urn:Cclass".to_vec(),
        }
    }

    fn annotation() -> OwnedColumns {
        OwnedColumns {
            root_kinds: vec![ROOT_ONTOLOGY_ANNOTATION],
            root_ids: le32(&[3]),
            node_tags: le16(&[1, 2, 5]),
            node_field_offsets: le64(&[0, 1, 3, 6]),
            field_kinds: vec![
                COMPONENT_TEXT,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_SET,
            ],
            field_values: le64(&[0, 5, 1, 2, 1, 0]),
            field_lengths: le64(&[5, 19, 0, 0, 0, 0]),
            item_kinds: Vec::new(),
            item_values: Vec::new(),
            item_lengths: Vec::new(),
            scalar_bytes: b"urn:aannotation_property".to_vec(),
        }
    }

    fn property_chain() -> OwnedColumns {
        OwnedColumns {
            root_kinds: vec![ROOT_AXIOM],
            root_ids: le32(&[4]),
            node_tags: le16(&[1, 2, 11, 70]),
            node_field_offsets: le64(&[0, 1, 3, 4, 7]),
            field_kinds: vec![
                COMPONENT_TEXT,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_SEQUENCE,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_SET,
            ],
            field_values: le64(&[0, 5, 1, 0, 3, 2, 1]),
            field_lengths: le64(&[5, 15, 0, 1, 0, 0, 0]),
            item_kinds: vec![COMPONENT_NODE],
            item_values: le64(&[2]),
            item_lengths: le64(&[0]),
            scalar_bytes: b"urn:pobject_property".to_vec(),
        }
    }

    fn data_range_cycle() -> OwnedColumns {
        OwnedColumns {
            root_kinds: vec![ROOT_AXIOM],
            root_ids: le32(&[4]),
            node_tags: le16(&[1, 2, 23, 94]),
            node_field_offsets: le64(&[0, 1, 3, 4, 7]),
            field_kinds: vec![
                COMPONENT_TEXT,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_SET,
            ],
            field_values: le64(&[0, 5, 1, 3, 2, 3, 0]),
            field_lengths: le64(&[5, 13, 0, 0, 0, 0, 0]),
            item_kinds: Vec::new(),
            item_values: Vec::new(),
            item_lengths: Vec::new(),
            scalar_bytes: b"urn:pdata_property".to_vec(),
        }
    }

    fn equivalent_classes() -> OwnedColumns {
        OwnedColumns {
            root_kinds: vec![ROOT_AXIOM],
            root_ids: le32(&[5]),
            node_tags: le16(&[1, 1, 2, 2, 62]),
            node_field_offsets: le64(&[0, 1, 2, 4, 6, 8]),
            field_kinds: vec![
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_SET,
                COMPONENT_SET,
            ],
            field_values: le64(&[0, 5, 10, 1, 15, 2, 0, 2]),
            field_lengths: le64(&[5, 5, 5, 0, 5, 0, 2, 0]),
            item_kinds: vec![COMPONENT_NODE, COMPONENT_NODE],
            item_values: le64(&[3, 4]),
            item_lengths: le64(&[0, 0]),
            scalar_bytes: b"urn:Aurn:Bclassclass".to_vec(),
        }
    }

    fn disjoint_named_classes() -> OwnedColumns {
        let mut columns = equivalent_classes();
        columns.node_tags = le16(&[1, 1, 2, 2, 63]);
        columns
    }

    fn nary_disjoint_named_classes() -> OwnedColumns {
        OwnedColumns {
            root_kinds: vec![ROOT_AXIOM],
            root_ids: le32(&[7]),
            node_tags: le16(&[1, 1, 1, 2, 2, 2, 63]),
            node_field_offsets: le64(&[0, 1, 2, 3, 5, 7, 9, 11]),
            field_kinds: vec![
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_SET,
                COMPONENT_SET,
            ],
            field_values: le64(&[0, 5, 10, 15, 1, 20, 2, 25, 3, 0, 3]),
            field_lengths: le64(&[5, 5, 5, 5, 0, 5, 0, 5, 0, 3, 0]),
            item_kinds: vec![COMPONENT_NODE, COMPONENT_NODE, COMPONENT_NODE],
            item_values: le64(&[4, 5, 6]),
            item_lengths: le64(&[0, 0, 0]),
            scalar_bytes: b"urn:Aurn:Burn:Cclassclassclass".to_vec(),
        }
    }

    fn binary_named_disjoint_union() -> OwnedColumns {
        OwnedColumns {
            root_kinds: vec![ROOT_AXIOM],
            root_ids: le32(&[7]),
            node_tags: le16(&[1, 1, 1, 2, 2, 2, 64]),
            node_field_offsets: le64(&[0, 1, 2, 3, 5, 7, 9, 12]),
            field_kinds: vec![
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_SET,
                COMPONENT_SET,
            ],
            field_values: le64(&[0, 5, 10, 15, 1, 20, 2, 25, 3, 6, 0, 2]),
            field_lengths: le64(&[5, 5, 5, 5, 0, 5, 0, 5, 0, 0, 2, 0]),
            item_kinds: vec![COMPONENT_NODE, COMPONENT_NODE],
            item_values: le64(&[4, 5]),
            item_lengths: le64(&[0, 0]),
            scalar_bytes: b"urn:Aurn:Burn:Dclassclassclass".to_vec(),
        }
    }

    fn two_declarations() -> OwnedColumns {
        OwnedColumns {
            root_kinds: vec![ROOT_AXIOM, ROOT_AXIOM],
            root_ids: le32(&[5, 6]),
            node_tags: le16(&[1, 1, 2, 2, 60, 60]),
            node_field_offsets: le64(&[0, 1, 2, 4, 6, 8, 10]),
            field_kinds: vec![
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_SET,
                COMPONENT_NODE,
                COMPONENT_SET,
            ],
            field_values: le64(&[0, 5, 10, 1, 15, 2, 3, 0, 4, 0]),
            field_lengths: le64(&[5, 5, 5, 0, 5, 0, 0, 0, 0, 0]),
            item_kinds: Vec::new(),
            item_values: Vec::new(),
            item_lengths: Vec::new(),
            scalar_bytes: b"urn:Aurn:Bclassclass".to_vec(),
        }
    }

    fn named_subclass() -> OwnedColumns {
        OwnedColumns {
            root_kinds: vec![ROOT_AXIOM],
            root_ids: le32(&[5]),
            node_tags: le16(&[1, 1, 2, 2, 61]),
            node_field_offsets: le64(&[0, 1, 2, 4, 6, 9]),
            field_kinds: vec![
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_SET,
            ],
            field_values: le64(&[0, 5, 10, 1, 15, 2, 3, 4, 0]),
            field_lengths: le64(&[5, 5, 5, 0, 5, 0, 0, 0, 0]),
            item_kinds: Vec::new(),
            item_values: Vec::new(),
            item_lengths: Vec::new(),
            scalar_bytes: b"urn:Aurn:Bclassclass".to_vec(),
        }
    }

    fn named_class_assertion() -> OwnedColumns {
        OwnedColumns {
            root_kinds: vec![ROOT_AXIOM],
            root_ids: le32(&[5]),
            node_tags: le16(&[1, 1, 2, 2, 112]),
            node_field_offsets: le64(&[0, 1, 2, 4, 6, 9]),
            field_kinds: vec![
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_SET,
            ],
            field_values: le64(&[0, 5, 10, 1, 15, 2, 3, 4, 0]),
            field_lengths: le64(&[5, 5, 5, 0, 16, 0, 0, 0, 0]),
            item_kinds: Vec::new(),
            item_values: Vec::new(),
            item_lengths: Vec::new(),
            scalar_bytes: b"urn:Aurn:iclassnamed_individual".to_vec(),
        }
    }

    fn same_named_individuals() -> OwnedColumns {
        OwnedColumns {
            root_kinds: vec![ROOT_AXIOM],
            root_ids: le32(&[5]),
            node_tags: le16(&[1, 1, 2, 2, 110]),
            node_field_offsets: le64(&[0, 1, 2, 4, 6, 8]),
            field_kinds: vec![
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_SET,
                COMPONENT_SET,
            ],
            field_values: le64(&[0, 5, 10, 1, 26, 2, 0, 2]),
            field_lengths: le64(&[5, 5, 16, 0, 16, 0, 2, 0]),
            item_kinds: vec![COMPONENT_NODE, COMPONENT_NODE],
            item_values: le64(&[3, 4]),
            item_lengths: le64(&[0, 0]),
            scalar_bytes: b"urn:iurn:jnamed_individualnamed_individual".to_vec(),
        }
    }

    fn different_named_individuals() -> OwnedColumns {
        let mut columns = same_named_individuals();
        columns.node_tags = le16(&[1, 1, 2, 2, 111]);
        columns
    }

    fn named_subproperty() -> OwnedColumns {
        OwnedColumns {
            root_kinds: vec![ROOT_AXIOM],
            root_ids: le32(&[5]),
            node_tags: le16(&[1, 1, 2, 2, 70]),
            node_field_offsets: le64(&[0, 1, 2, 4, 6, 9]),
            field_kinds: vec![
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_SET,
            ],
            field_values: le64(&[0, 5, 10, 1, 25, 2, 3, 4, 0]),
            field_lengths: le64(&[5, 5, 15, 0, 15, 0, 0, 0, 0]),
            item_kinds: Vec::new(),
            item_values: Vec::new(),
            item_lengths: Vec::new(),
            scalar_bytes: b"urn:purn:qobject_propertyobject_property".to_vec(),
        }
    }

    fn equivalent_named_properties() -> OwnedColumns {
        OwnedColumns {
            root_kinds: vec![ROOT_AXIOM],
            root_ids: le32(&[5]),
            node_tags: le16(&[1, 1, 2, 2, 71]),
            node_field_offsets: le64(&[0, 1, 2, 4, 6, 8]),
            field_kinds: vec![
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_SET,
                COMPONENT_SET,
            ],
            field_values: le64(&[0, 5, 10, 1, 25, 2, 0, 2]),
            field_lengths: le64(&[5, 5, 15, 0, 15, 0, 2, 0]),
            item_kinds: vec![COMPONENT_NODE, COMPONENT_NODE],
            item_values: le64(&[3, 4]),
            item_lengths: le64(&[0, 0]),
            scalar_bytes: b"urn:purn:qobject_propertyobject_property".to_vec(),
        }
    }

    fn named_property_chain_axiom() -> OwnedColumns {
        OwnedColumns {
            root_kinds: vec![ROOT_AXIOM],
            root_ids: le32(&[8]),
            node_tags: le16(&[1, 1, 1, 2, 2, 2, 11, 70]),
            node_field_offsets: le64(&[0, 1, 2, 3, 5, 7, 9, 10, 13]),
            field_kinds: vec![
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_SEQUENCE,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_SET,
            ],
            field_values: le64(&[0, 5, 10, 15, 1, 30, 2, 45, 3, 0, 7, 6, 2]),
            field_lengths: le64(&[5, 5, 5, 15, 0, 15, 0, 15, 0, 2, 0, 0, 0]),
            item_kinds: vec![COMPONENT_NODE, COMPONENT_NODE],
            item_values: le64(&[4, 5]),
            item_lengths: le64(&[0, 0]),
            scalar_bytes: b"urn:purn:qurn:robject_propertyobject_propertyobject_property".to_vec(),
        }
    }

    fn transitive_named_property() -> OwnedColumns {
        OwnedColumns {
            root_kinds: vec![ROOT_AXIOM],
            root_ids: le32(&[3]),
            node_tags: le16(&[1, 2, 82]),
            node_field_offsets: le64(&[0, 1, 3, 5]),
            field_kinds: vec![
                COMPONENT_TEXT,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_SET,
            ],
            field_values: le64(&[0, 5, 1, 2, 0]),
            field_lengths: le64(&[5, 15, 0, 0, 0]),
            item_kinds: Vec::new(),
            item_values: Vec::new(),
            item_lengths: Vec::new(),
            scalar_bytes: b"urn:pobject_property".to_vec(),
        }
    }

    fn named_property_range() -> OwnedColumns {
        OwnedColumns {
            root_kinds: vec![ROOT_AXIOM],
            root_ids: le32(&[5]),
            node_tags: le16(&[1, 1, 2, 2, 75]),
            node_field_offsets: le64(&[0, 1, 2, 4, 6, 9]),
            field_kinds: vec![
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_SET,
            ],
            field_values: le64(&[0, 5, 10, 1, 15, 2, 4, 3, 0]),
            field_lengths: le64(&[5, 5, 5, 0, 15, 0, 0, 0, 0]),
            item_kinds: Vec::new(),
            item_values: Vec::new(),
            item_lengths: Vec::new(),
            scalar_bytes: b"urn:Aurn:pclassobject_property".to_vec(),
        }
    }

    fn named_property_domain() -> OwnedColumns {
        let mut columns = named_property_range();
        columns.node_tags = le16(&[1, 1, 2, 2, 74]);
        columns
    }

    fn reflexive_named_property() -> OwnedColumns {
        let mut columns = transitive_named_property();
        columns.node_tags = le16(&[1, 2, 78]);
        columns
    }

    fn named_object_property_assertion() -> OwnedColumns {
        OwnedColumns {
            root_kinds: vec![ROOT_AXIOM],
            root_ids: le32(&[7]),
            node_tags: le16(&[1, 1, 1, 2, 2, 2, 113]),
            node_field_offsets: le64(&[0, 1, 2, 3, 5, 7, 9, 13]),
            field_kinds: vec![
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_SET,
            ],
            field_values: le64(&[0, 5, 10, 15, 3, 30, 1, 46, 2, 4, 5, 6, 0]),
            field_lengths: le64(&[5, 5, 5, 15, 0, 16, 0, 16, 0, 0, 0, 0, 0]),
            item_kinds: Vec::new(),
            item_values: Vec::new(),
            item_lengths: Vec::new(),
            scalar_bytes: b"urn:iurn:jurn:pobject_propertynamed_individualnamed_individual"
                .to_vec(),
        }
    }

    fn equivalent_class_pair() -> OwnedColumns {
        OwnedColumns {
            root_kinds: vec![ROOT_AXIOM, ROOT_AXIOM],
            root_ids: le32(&[7, 8]),
            node_tags: le16(&[1, 1, 1, 2, 2, 2, 62, 62]),
            node_field_offsets: le64(&[0, 1, 2, 3, 5, 7, 9, 11, 13]),
            field_kinds: vec![
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_SET,
                COMPONENT_SET,
                COMPONENT_SET,
                COMPONENT_SET,
            ],
            field_values: le64(&[0, 5, 10, 15, 1, 20, 2, 25, 3, 0, 2, 2, 4]),
            field_lengths: le64(&[5, 5, 5, 5, 0, 5, 0, 5, 0, 2, 0, 2, 0]),
            item_kinds: vec![
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_NODE,
            ],
            item_values: le64(&[4, 5, 4, 6]),
            item_lengths: le64(&[0, 0, 0, 0]),
            scalar_bytes: b"urn:Aurn:Burn:Cclassclassclass".to_vec(),
        }
    }

    fn cardinality_pair() -> OwnedColumns {
        let mut scalar_bytes = b"urn:Curn:pclassobject_property".to_vec();
        scalar_bytes.extend([0x00, 0x01, 0xff]);
        OwnedColumns {
            root_kinds: vec![ROOT_AXIOM],
            root_ids: le32(&[7]),
            node_tags: le16(&[1, 1, 2, 2, 38, 38, 62]),
            node_field_offsets: le64(&[0, 1, 2, 4, 6, 9, 12, 14]),
            field_kinds: vec![
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_INTEGER,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_INTEGER,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_SET,
                COMPONENT_SET,
            ],
            field_values: le64(&[0, 5, 10, 1, 15, 2, 30, 4, 3, 32, 4, 3, 0, 2]),
            field_lengths: le64(&[5, 5, 5, 0, 15, 0, 2, 0, 0, 1, 0, 0, 2, 0]),
            item_kinds: vec![COMPONENT_NODE, COMPONENT_NODE],
            item_values: le64(&[5, 6]),
            item_lengths: le64(&[0, 0]),
            scalar_bytes,
        }
    }

    fn assert_protocol_contains(columns: &OwnedColumns, expected: &str) {
        assert!(matches!(
            validate_columns(columns.borrowed(), EncodedLimits::default()),
            Err(CoreError::Protocol(message)) if message.contains(expected)
        ));
    }

    fn assert_role_error(columns: &OwnedColumns) {
        assert_protocol_contains(columns, "schema role");
    }

    #[test]
    fn constructor_role_ledger_covers_every_frozen_model_tag() {
        const TAGS: [u16; 76] = [
            1, 2, 3, 4, 5, 10, 11, 20, 21, 22, 23, 24, 25, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39,
            40, 41, 42, 43, 44, 45, 46, 60, 61, 62, 63, 64, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79,
            80, 81, 82, 90, 91, 92, 93, 94, 95, 100, 101, 110, 111, 112, 113, 114, 115, 116, 120,
            121, 122, 123, 140, 141, 142, 143, 144, 145, 146, 147, 148,
        ];
        assert_eq!(
            CONSTRUCTOR_ROLE_LEDGER
                .iter()
                .map(|(tag, _roles)| *tag)
                .collect::<Vec<_>>(),
            TAGS
        );
        assert_eq!(
            CONSTRUCTOR_ROLE_LEDGER
                .iter()
                .map(|(_tag, roles)| roles.len())
                .sum::<usize>(),
            176
        );
        for (tag, roles) in CONSTRUCTOR_ROLE_LEDGER {
            assert_eq!(constructor_roles(*tag), Some(*roles));
        }
        assert!(constructor_roles(0).is_none());
        assert!(constructor_roles(149).is_none());
    }

    #[test]
    fn arity_preserving_scalar_node_and_collection_role_confusions_fail_closed() {
        let mut malformed = declaration();
        malformed.field_kinds[0] = COMPONENT_BYTES;
        assert_role_error(&malformed);

        let mut malformed = declaration();
        malformed.field_kinds[1] = COMPONENT_TEXT;
        assert_role_error(&malformed);

        let mut malformed = declaration();
        malformed.field_kinds[4] = COMPONENT_SEQUENCE;
        assert_role_error(&malformed);

        let mut malformed = annotation();
        malformed.field_values = le64(&[0, 5, 1, 1, 2, 0]);
        assert_role_error(&malformed);

        let mut malformed = annotation();
        malformed.field_values = le64(&[0, 5, 1, 2, 1, 0]);
        malformed.field_lengths = le64(&[5, 19, 0, 0, 0, 1]);
        malformed.item_kinds = vec![COMPONENT_NODE];
        malformed.item_values = le64(&[1]);
        malformed.item_lengths = le64(&[0]);
        assert_role_error(&malformed);

        let mut malformed = property_chain();
        malformed.field_kinds[3] = COMPONENT_SET;
        assert_role_error(&malformed);

        let mut malformed = property_chain();
        malformed.item_values = le64(&[1]);
        assert_role_error(&malformed);
    }

    #[test]
    fn entity_kind_scalar_is_bound_to_the_model_schema() {
        let mut malformed = declaration();
        malformed.scalar_bytes[5..10].copy_from_slice(b"other");
        assert!(matches!(
            validate_columns(malformed.borrowed(), EncodedLimits::default()),
            Err(CoreError::Protocol(message)) if message.contains("model-schema-1")
        ));
    }

    #[test]
    fn column_offsets_and_arenas_require_exact_nonoverlapping_coverage() {
        validate_columns(equivalent_classes().borrowed(), EncodedLimits::default()).unwrap();

        let mut malformed = equivalent_classes();
        malformed.node_field_offsets = le64(&[1, 1, 2, 4, 6, 8]);
        assert_protocol_contains(&malformed, "offsets must start at zero");

        let mut malformed = equivalent_classes();
        malformed.node_field_offsets = le64(&[0, 1, 2, 4, 6, 9]);
        assert_protocol_contains(&malformed, "offsets are not contiguous and bounded");

        let mut malformed = equivalent_classes();
        malformed.field_kinds.push(COMPONENT_NONE);
        malformed.field_values.extend(le64(&[0]));
        malformed.field_lengths.extend(le64(&[0]));
        assert_protocol_contains(&malformed, "offsets do not cover every field");

        let mut malformed = equivalent_classes();
        malformed.field_values = le64(&[0, 5, 10, 1, 15, 2, 1, 2]);
        assert_protocol_contains(&malformed, "exactly cover item rows");

        let mut malformed = equivalent_classes();
        malformed.field_values = le64(&[0, 5, 10, 1, 15, 2, 0, 1]);
        assert_protocol_contains(&malformed, "exactly cover item rows");

        let mut malformed = equivalent_classes();
        malformed.field_lengths = le64(&[5, 5, 5, 0, 5, 0, 3, 0]);
        assert_protocol_contains(&malformed, "collection field exceeds item rows");

        let mut malformed = equivalent_classes();
        malformed.item_kinds.push(COMPONENT_NODE);
        malformed.item_values.extend(le64(&[4]));
        malformed.item_lengths.extend(le64(&[0]));
        assert_protocol_contains(&malformed, "item rows are not exactly covered");

        let mut malformed = equivalent_classes();
        malformed.field_values = le64(&[1, 5, 10, 1, 15, 2, 0, 2]);
        assert_protocol_contains(&malformed, "exactly cover the scalar arena");

        let mut malformed = equivalent_classes();
        malformed.field_values = le64(&[0, 4, 10, 1, 15, 2, 0, 2]);
        assert_protocol_contains(&malformed, "exactly cover the scalar arena");

        let mut malformed = equivalent_classes();
        malformed.field_lengths = le64(&[21, 5, 5, 0, 5, 0, 2, 0]);
        assert_protocol_contains(&malformed, "scalar component is out of bounds");

        let mut malformed = equivalent_classes();
        malformed.scalar_bytes.push(b'x');
        assert_protocol_contains(&malformed, "scalar arena is not exactly covered");
    }

    #[test]
    fn node_references_are_one_based_bounded_and_canonical_sets_are_sorted() {
        let mut malformed = declaration();
        malformed.root_ids = le32(&[0]);
        assert_protocol_contains(&malformed, "one-based and nonzero");

        let mut malformed = declaration();
        malformed.root_ids = le32(&[4]);
        assert_protocol_contains(&malformed, "node ID is out of range");

        let mut malformed = declaration();
        malformed.field_values = le64(&[0, 5, 0, 2, 0]);
        assert_protocol_contains(&malformed, "one-based and nonzero");

        let mut malformed = declaration();
        malformed.field_values = le64(&[0, 5, 4, 2, 0]);
        assert_protocol_contains(&malformed, "node ID is out of range");

        let mut malformed = equivalent_classes();
        malformed.item_values = le64(&[0, 4]);
        assert_protocol_contains(&malformed, "one-based and nonzero");

        let mut malformed = equivalent_classes();
        malformed.item_values = le64(&[3, 6]);
        assert_protocol_contains(&malformed, "node ID is out of range");

        let mut malformed = equivalent_classes();
        malformed.item_values = le64(&[4, 3]);
        assert_protocol_contains(&malformed, "not strictly ascending and unique");

        let mut malformed = equivalent_classes();
        malformed.item_values = le64(&[3, 3]);
        assert_protocol_contains(&malformed, "not strictly ascending and unique");

        let mut ordered_sequence = property_chain();
        ordered_sequence.field_values = le64(&[0, 5, 1, 0, 3, 2, 2]);
        ordered_sequence.field_lengths = le64(&[5, 15, 0, 2, 0, 0, 0]);
        ordered_sequence.item_kinds = vec![COMPONENT_NODE, COMPONENT_NODE];
        ordered_sequence.item_values = le64(&[2, 2]);
        ordered_sequence.item_lengths = le64(&[0, 0]);
        validate_columns(ordered_sequence.borrowed(), EncodedLimits::default()).unwrap();
    }

    #[test]
    fn root_kind_tag_and_order_rules_cover_the_frozen_constructor_ledger() {
        const AXIOM_TAGS: [u16; 37] = [
            60, 61, 62, 63, 64, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 90, 91, 92, 93,
            94, 95, 100, 101, 110, 111, 112, 113, 114, 115, 116, 120, 121, 122, 123,
        ];
        for (tag, _roles) in CONSTRUCTOR_ROLE_LEDGER {
            assert_eq!(root_accepts(ROOT_ONTOLOGY_ANNOTATION, *tag), *tag == 5);
            assert_eq!(root_accepts(ROOT_AXIOM, *tag), AXIOM_TAGS.contains(tag));
            assert_eq!(root_accepts(ROOT_EXTENSION, *tag), *tag == 148);
            assert!(!root_accepts(0, *tag));
            assert!(!root_accepts(4, *tag));
        }
        for tag in [0, 6, 59, 65, 83, 96, 102, 117, 124, 139, 149] {
            assert!(!root_accepts(ROOT_ONTOLOGY_ANNOTATION, tag));
            assert!(!root_accepts(ROOT_AXIOM, tag));
            assert!(!root_accepts(ROOT_EXTENSION, tag));
        }

        validate_columns(annotation().borrowed(), EncodedLimits::default()).unwrap();

        let mut malformed = declaration();
        malformed.root_kinds[0] = ROOT_ONTOLOGY_ANNOTATION;
        assert_protocol_contains(&malformed, "inconsistent with its constructor tag");

        let mut malformed = annotation();
        malformed.root_kinds[0] = ROOT_AXIOM;
        assert_protocol_contains(&malformed, "inconsistent with its constructor tag");

        let mut malformed = equivalent_classes();
        malformed.root_ids = le32(&[2]);
        assert_protocol_contains(&malformed, "inconsistent with its constructor tag");

        let mut malformed = equivalent_classes();
        malformed.root_kinds = vec![ROOT_AXIOM, ROOT_AXIOM];
        malformed.root_ids = le32(&[5, 5]);
        assert_protocol_contains(&malformed, "strictly ordered and unique");
    }

    #[test]
    fn dense_node_and_root_order_follow_canonical_model_v1_bytes() {
        validate_columns(equivalent_classes().borrowed(), EncodedLimits::default()).unwrap();
        validate_columns(two_declarations().borrowed(), EncodedLimits::default()).unwrap();
        validate_columns(equivalent_class_pair().borrowed(), EncodedLimits::default()).unwrap();
        validate_columns(cardinality_pair().borrowed(), EncodedLimits::default()).unwrap();

        let mut malformed = equivalent_classes();
        malformed.scalar_bytes = b"urn:Burn:Aclassclass".to_vec();
        assert_protocol_contains(&malformed, "node IDs are not canonical and unique");

        let mut malformed = equivalent_classes();
        malformed.scalar_bytes = b"urn:Aurn:Aclassclass".to_vec();
        assert_protocol_contains(&malformed, "node IDs are not canonical and unique");

        let mut malformed = equivalent_classes();
        malformed.field_values = le64(&[0, 5, 10, 2, 15, 1, 0, 2]);
        assert_protocol_contains(&malformed, "node IDs are not canonical and unique");

        let mut malformed = equivalent_class_pair();
        malformed.item_values = le64(&[4, 6, 4, 5]);
        assert_protocol_contains(&malformed, "node IDs are not canonical and unique");

        let mut framed = equivalent_classes();
        framed.field_values = le64(&[0, 3, 7, 1, 12, 2, 0, 2]);
        framed.field_lengths = le64(&[3, 4, 5, 0, 5, 0, 2, 0]);
        framed.scalar_bytes = b"z:aaa:bclassclass".to_vec();
        validate_columns(framed.borrowed(), EncodedLimits::default()).unwrap();

        let mut malformed = equivalent_classes();
        malformed.field_values = le64(&[0, 4, 7, 1, 12, 2, 0, 2]);
        malformed.field_lengths = le64(&[4, 3, 5, 0, 5, 0, 2, 0]);
        malformed.scalar_bytes = b"aa:bz:aclassclass".to_vec();
        assert_protocol_contains(&malformed, "node IDs are not canonical and unique");

        let mut malformed = cardinality_pair();
        malformed.scalar_bytes.truncate(30);
        malformed.scalar_bytes.extend([0xff, 0x00, 0x01]);
        malformed.field_values = le64(&[0, 5, 10, 1, 15, 2, 30, 4, 3, 31, 4, 3, 0, 2]);
        malformed.field_lengths = le64(&[5, 5, 5, 0, 15, 0, 1, 0, 0, 2, 0, 0, 2, 0]);
        assert_protocol_contains(&malformed, "node IDs are not canonical and unique");

        let mut malformed = two_declarations();
        malformed.root_ids = le32(&[6, 5]);
        assert_protocol_contains(&malformed, "roots are not strictly ordered and unique");

        let owned = equivalent_classes();
        let columns = owned.borrowed();
        let mut work = 0;
        let lengths = validate_graph_and_lengths(&columns, 1, 5, &mut work, u64::MAX).unwrap();
        let mut comparison_work = 0;
        assert!(matches!(
            compare_canonical_nodes(
                0,
                1,
                &columns,
                &lengths,
                &mut comparison_work,
                1,
            ),
            Err(CoreError::Capacity(message)) if message.contains("work limit")
        ));
    }

    #[test]
    fn canonical_scalar_and_graph_integrity_fail_before_ordering() {
        fn oracle_varint(mut value: u64) -> Vec<u8> {
            let mut output = Vec::new();
            loop {
                let chunk = (value & 0x7f) as u8;
                value >>= 7;
                output.push(chunk | if value == 0 { 0 } else { 0x80 });
                if value == 0 {
                    return output;
                }
            }
        }

        let varint_values = [
            0,
            1,
            0x7f,
            0x80,
            0xff,
            0x100,
            0x3fff,
            0x4000,
            u32::MAX.into(),
            u64::MAX,
        ];
        for left in varint_values {
            for right in varint_values {
                assert_eq!(
                    compare_u64_varints(left, right),
                    oracle_varint(left).cmp(&oracle_varint(right))
                );
            }
        }

        for (payload, expected) in [
            (&[0x00][..], &[0x00][..]),
            (&[0x7f][..], &[0x7f][..]),
            (&[0x80][..], &[0x80, 0x01][..]),
            (&[0xff][..], &[0xff, 0x01][..]),
            (&[0x00, 0x01][..], &[0x80, 0x02][..]),
            (&[0x00, 0x40][..], &[0x80, 0x80, 0x01][..]),
        ] {
            let width = canonical_integer_varint_width(payload, 0, payload.len()).unwrap();
            let actual = (0..width)
                .map(|index| integer_varint_byte(payload, 0, payload.len(), index, width).unwrap())
                .collect::<Vec<_>>();
            assert_eq!(actual, expected);
        }

        let mut cursor = 0;
        validate_leaf_component(COMPONENT_ENUM, 0, 5, 0, b"class".as_slice(), &mut cursor).unwrap();

        let mut cursor = 0;
        assert!(matches!(
            validate_leaf_component(COMPONENT_ENUM, 0, 1, 0, &[0x80][..], &mut cursor),
            Err(CoreError::Protocol(message)) if message.contains("nonempty ASCII")
        ));

        let mut malformed = cardinality_pair();
        malformed.scalar_bytes[31] = 0;
        assert_protocol_contains(&malformed, "integer component is not minimal");

        let mut malformed = cardinality_pair();
        malformed.field_lengths = le64(&[5, 5, 5, 0, 15, 0, 0, 0, 0, 1, 0, 0, 2, 0]);
        assert_protocol_contains(&malformed, "integer component is not minimal");

        assert_protocol_contains(&data_range_cycle(), "structural graph is cyclic");

        let mut malformed = declaration();
        malformed.node_tags = le16(&[1, 2, 60, 1]);
        malformed.node_field_offsets = le64(&[0, 1, 3, 5, 6]);
        malformed.field_kinds.push(COMPONENT_TEXT);
        malformed.field_values.extend(le64(&[10]));
        malformed.field_lengths.extend(le64(&[1]));
        malformed.scalar_bytes.push(b'z');
        assert_protocol_contains(&malformed, "contains unreachable nodes");
    }

    #[test]
    fn empty_and_declaration_columns_validate_without_a_graph_copy() {
        let empty = validate_columns(empty().borrowed(), EncodedLimits::default()).unwrap();
        assert_eq!(empty.node_count, 0);
        assert_eq!(empty.scalar_bytes, 0);

        let declaration = validate_columns(
            declaration().borrowed(),
            EncodedLimits {
                max_roots: 1,
                max_nodes: 3,
                max_fields: 5,
                max_items: 0,
                max_scalar_bytes: 10,
                max_work: 64,
            },
        )
        .unwrap();
        assert_eq!(declaration.root_count, 1);
        assert_eq!(declaration.node_count, 3);
        assert_eq!(declaration.field_count, 5);
        assert_eq!(declaration.scalar_bytes, 10);
    }

    #[test]
    fn named_hierarchy_compiler_matches_the_frozen_ir_contract() {
        let fingerprint = [7; 32];
        let compiled = compile_named_hierarchy(
            named_subclass().borrowed(),
            EncodedLimits::default(),
            fingerprint,
        )
        .unwrap();

        assert_eq!(
            compiled.entities,
            vec![
                Entity {
                    kind: EntityKind::Class,
                    iri: OWL_NOTHING_IRI.to_owned(),
                },
                Entity {
                    kind: EntityKind::Class,
                    iri: OWL_THING_IRI.to_owned(),
                },
                Entity {
                    kind: EntityKind::Class,
                    iri: "urn:A".to_owned(),
                },
                Entity {
                    kind: EntityKind::Class,
                    iri: "urn:B".to_owned(),
                },
                Entity {
                    kind: EntityKind::ObjectProperty,
                    iri: OWL_BOTTOM_OBJECT_PROPERTY_IRI.to_owned(),
                },
                Entity {
                    kind: EntityKind::ObjectProperty,
                    iri: OWL_TOP_OBJECT_PROPERTY_IRI.to_owned(),
                },
            ]
        );
        assert_eq!(
            compiled.expressions,
            (0..4)
                .map(|identifier| Expression {
                    tag: ExpressionTag::Class,
                    payload: Vec::new(),
                    arguments: vec![identifier],
                })
                .collect::<Vec<_>>()
        );
        assert_eq!(
            compiled.expression_occurrences,
            vec![
                Occurrence::default(),
                Occurrence::default(),
                Occurrence {
                    negative: 1,
                    positive: 0,
                },
                Occurrence {
                    negative: 0,
                    positive: 1,
                },
            ]
        );
        assert_eq!(
            compiled.property_occurrences,
            vec![Occurrence::default(), Occurrence::default()]
        );
        assert_eq!(compiled.property_chains, vec![vec![4], vec![5]]);
        assert_eq!(compiled.subclass_axioms, vec![(2, 3)]);
        assert_eq!(compiled.feature_counts, vec![0; FEATURE_VECTOR_LENGTH]);
        assert_eq!(compiled.source_fingerprint, fingerprint);
        assert!(compiled.equivalent_class_axioms.is_empty());
        assert!(compiled.disjoint_groups.is_empty());
        assert!(compiled.subproperty_axioms.is_empty());
        assert!(compiled.property_ranges.is_empty());

        assert_eq!(
            compile_named_hierarchy(
                named_subclass().indexed(),
                EncodedLimits::default(),
                fingerprint,
            )
            .unwrap(),
            compiled
        );
    }

    #[test]
    fn named_hierarchy_declarations_follow_exact_elk_entity_policy() {
        fn declaration_of(kind: &str) -> OwnedColumns {
            let mut columns = declaration();
            columns.field_lengths = le64(&[5, kind.len() as u64, 0, 0, 0]);
            columns.scalar_bytes = format!("urn:C{kind}").into_bytes();
            columns
        }

        let classes = compile_named_hierarchy(
            declaration_of("class").borrowed(),
            EncodedLimits::default(),
            [0; 32],
        )
        .unwrap();
        assert!(
            classes
                .entities
                .iter()
                .any(|entity| { entity.kind == EntityKind::Class && entity.iri == "urn:C" })
        );

        let individuals = compile_named_hierarchy(
            declaration_of("named_individual").borrowed(),
            EncodedLimits::default(),
            [0; 32],
        )
        .unwrap();
        assert!(individuals.expressions.iter().any(|expression| {
            expression.tag == ExpressionTag::Individual && expression.arguments == [2]
        }));

        let properties = compile_named_hierarchy(
            declaration_of("object_property").borrowed(),
            EncodedLimits::default(),
            [0; 32],
        )
        .unwrap();
        assert_eq!(properties.property_chains.len(), 3);
        assert_eq!(properties.property_occurrences.len(), 3);

        let ignored = compile_named_hierarchy(
            declaration_of("annotation_property").borrowed(),
            EncodedLimits::default(),
            [0; 32],
        )
        .unwrap();
        assert_eq!(ignored.entities.len(), 4);

        for kind in ["data_property", "datatype"] {
            assert!(matches!(
                compile_named_hierarchy(
                    declaration_of(kind).borrowed(),
                    EncodedLimits::default(),
                    [0; 32],
                ),
                Err(CoreError::InvalidInput(message)) if message.contains("does not support")
            ));
        }
    }

    #[test]
    fn named_equivalence_compiles_with_dual_occurrences() {
        let compiled = compile_named_hierarchy(
            equivalent_classes().borrowed(),
            EncodedLimits::default(),
            [9; 32],
        )
        .unwrap();

        assert_eq!(compiled.subclass_axioms, Vec::<(u32, u32)>::new());
        assert_eq!(compiled.equivalent_class_axioms, vec![(2, 3)]);
        assert_eq!(
            compiled.expression_occurrences,
            vec![
                Occurrence::default(),
                Occurrence::default(),
                Occurrence {
                    negative: 1,
                    positive: 1,
                },
                Occurrence {
                    negative: 1,
                    positive: 1,
                },
            ]
        );
    }

    #[test]
    fn binary_named_disjointness_matches_scalar_binarization() {
        let compiled = compile_named_hierarchy(
            disjoint_named_classes().borrowed(),
            EncodedLimits::default(),
            [12; 32],
        )
        .unwrap();

        assert_eq!(
            compiled.expressions[4].tag,
            ExpressionTag::ObjectIntersectionOf
        );
        assert_eq!(compiled.expressions[4].arguments, [2, 3]);
        assert_eq!(compiled.subclass_axioms, vec![(4, 0)]);
        assert!(compiled.disjoint_groups.is_empty());
        assert_eq!(
            compiled.expression_occurrences,
            vec![
                Occurrence {
                    negative: 0,
                    positive: 1,
                },
                Occurrence::default(),
                Occurrence {
                    negative: 1,
                    positive: 0,
                },
                Occurrence {
                    negative: 2,
                    positive: 0,
                },
                Occurrence {
                    negative: 1,
                    positive: 0,
                },
            ]
        );
        assert_eq!(compiled.feature_counts[FEATURE_DISJOINT_CLASSES], 1);
        assert_eq!(compiled.feature_counts[FEATURE_OWL_NOTHING_POSITIVE], 1);
    }

    #[test]
    fn nary_named_disjointness_preserves_one_canonical_group() {
        let compiled = compile_named_hierarchy(
            nary_disjoint_named_classes().borrowed(),
            EncodedLimits::default(),
            [14; 32],
        )
        .unwrap();

        assert_eq!(compiled.disjoint_groups, vec![vec![2, 3, 4]]);
        assert!(compiled.subclass_axioms.is_empty());
        assert_eq!(compiled.expression_occurrences[0], Occurrence::default());
        assert_eq!(compiled.expression_occurrences[2].negative, 1);
        assert_eq!(compiled.expression_occurrences[3].negative, 1);
        assert_eq!(compiled.expression_occurrences[4].negative, 1);
        assert_eq!(compiled.feature_counts[FEATURE_DISJOINT_CLASSES], 1);
        assert_eq!(compiled.feature_counts[FEATURE_OWL_NOTHING_POSITIVE], 0);
    }

    #[test]
    fn binary_named_disjoint_union_matches_scalar_lowering() {
        let compiled = compile_named_hierarchy(
            binary_named_disjoint_union().borrowed(),
            EncodedLimits::default(),
            [15; 32],
        )
        .unwrap();

        assert_eq!(
            compiled.expressions[5].tag,
            ExpressionTag::ObjectIntersectionOf
        );
        assert_eq!(compiled.expressions[5].arguments, [2, 3]);
        assert_eq!(compiled.subclass_axioms, vec![(2, 4), (3, 4), (5, 0)]);
        assert_eq!(compiled.expression_occurrences[0].positive, 1);
        assert_eq!(compiled.expression_occurrences[2].negative, 2);
        assert_eq!(compiled.expression_occurrences[3].negative, 3);
        assert_eq!(compiled.expression_occurrences[4].positive, 1);
        assert_eq!(compiled.expression_occurrences[5].negative, 1);
        assert_eq!(compiled.feature_counts[FEATURE_DISJOINT_UNION], 1);
        assert_eq!(compiled.feature_counts[FEATURE_DISJOINT_CLASSES], 0);
        assert_eq!(compiled.feature_counts[FEATURE_OWL_NOTHING_POSITIVE], 1);
    }

    #[test]
    fn named_class_assertion_compiles_as_individual_subsumption() {
        let compiled = compile_named_hierarchy(
            named_class_assertion().borrowed(),
            EncodedLimits::default(),
            [3; 32],
        )
        .unwrap();

        assert_eq!(compiled.subclass_axioms, vec![(3, 2)]);
        assert_eq!(compiled.expressions[3].tag, ExpressionTag::Individual);
        assert_eq!(compiled.expressions[3].arguments, [3]);
        assert_eq!(
            compiled.expression_occurrences,
            vec![
                Occurrence::default(),
                Occurrence::default(),
                Occurrence {
                    negative: 0,
                    positive: 1,
                },
                Occurrence {
                    negative: 1,
                    positive: 0,
                },
            ]
        );
    }

    #[test]
    fn same_named_individuals_compile_as_dual_bidirectional_rows() {
        let compiled = compile_named_hierarchy(
            same_named_individuals().borrowed(),
            EncodedLimits::default(),
            [4; 32],
        )
        .unwrap();

        assert_eq!(compiled.subclass_axioms, vec![(2, 3), (3, 2)]);
        assert_eq!(compiled.expressions[2].tag, ExpressionTag::Individual);
        assert_eq!(compiled.expressions[3].tag, ExpressionTag::Individual);
        assert_eq!(
            &compiled.expression_occurrences[2..],
            &[
                Occurrence {
                    negative: 1,
                    positive: 1,
                },
                Occurrence {
                    negative: 1,
                    positive: 1,
                },
            ]
        );
    }

    #[test]
    fn binary_different_individuals_reuses_exact_disjoint_binarization() {
        let compiled = compile_named_hierarchy(
            different_named_individuals().borrowed(),
            EncodedLimits::default(),
            [13; 32],
        )
        .unwrap();

        assert_eq!(
            compiled.expressions[4].tag,
            ExpressionTag::ObjectIntersectionOf
        );
        assert_eq!(compiled.expressions[4].arguments, [2, 3]);
        assert_eq!(compiled.subclass_axioms, vec![(4, 0)]);
        assert_eq!(compiled.expression_occurrences[0].positive, 1);
        assert_eq!(compiled.expression_occurrences[2].negative, 1);
        assert_eq!(compiled.expression_occurrences[3].negative, 2);
        assert_eq!(compiled.expression_occurrences[4].negative, 1);
        assert_eq!(compiled.feature_counts[FEATURE_DIFFERENT_INDIVIDUALS], 1);
        assert_eq!(compiled.feature_counts[FEATURE_OWL_NOTHING_POSITIVE], 1);
    }

    #[test]
    fn named_subproperty_compiles_occurrences_chains_and_rule() {
        let compiled = compile_named_hierarchy(
            named_subproperty().borrowed(),
            EncodedLimits::default(),
            [5; 32],
        )
        .unwrap();

        assert_eq!(
            compiled.property_occurrences,
            vec![
                Occurrence::default(),
                Occurrence::default(),
                Occurrence {
                    negative: 1,
                    positive: 0,
                },
                Occurrence {
                    negative: 0,
                    positive: 1,
                },
            ]
        );
        assert_eq!(
            compiled.property_chains,
            vec![vec![2], vec![3], vec![4], vec![5]]
        );
        assert_eq!(compiled.subproperty_axioms, vec![(2, 5)]);
    }

    #[test]
    fn equivalent_named_properties_compile_dual_bidirectional_rules() {
        let compiled = compile_named_hierarchy(
            equivalent_named_properties().borrowed(),
            EncodedLimits::default(),
            [6; 32],
        )
        .unwrap();

        assert_eq!(
            &compiled.property_occurrences[2..],
            &[
                Occurrence {
                    negative: 1,
                    positive: 1,
                },
                Occurrence {
                    negative: 1,
                    positive: 1,
                },
            ]
        );
        assert_eq!(compiled.subproperty_axioms, vec![(2, 5), (3, 4)]);
    }

    #[test]
    fn complex_named_property_chain_preserves_order_and_feature_count() {
        let compiled = compile_named_hierarchy(
            named_property_chain_axiom().borrowed(),
            EncodedLimits::default(),
            [8; 32],
        )
        .unwrap();

        assert_eq!(
            compiled.property_chains,
            vec![vec![2], vec![3], vec![4], vec![4, 5], vec![5], vec![6]]
        );
        assert_eq!(compiled.subproperty_axioms, vec![(3, 6)]);
        assert_eq!(
            &compiled.property_occurrences[2..],
            &[
                Occurrence {
                    negative: 1,
                    positive: 0,
                },
                Occurrence {
                    negative: 1,
                    positive: 0,
                },
                Occurrence {
                    negative: 0,
                    positive: 1,
                },
            ]
        );
        assert_eq!(compiled.feature_counts[FEATURE_OBJECT_PROPERTY_CHAIN], 1);
    }

    #[test]
    fn transitive_named_property_uses_dual_occurrence_and_repeated_chain() {
        let compiled = compile_named_hierarchy(
            transitive_named_property().borrowed(),
            EncodedLimits::default(),
            [10; 32],
        )
        .unwrap();

        assert_eq!(
            compiled.property_chains,
            vec![vec![2], vec![3], vec![4], vec![4, 4]]
        );
        assert_eq!(compiled.subproperty_axioms, vec![(3, 4)]);
        assert_eq!(
            compiled.property_occurrences[2],
            Occurrence {
                negative: 1,
                positive: 1,
            }
        );
        assert_eq!(compiled.feature_counts[FEATURE_OBJECT_PROPERTY_CHAIN], 1);
    }

    #[test]
    fn named_property_range_compiles_directly_with_exact_polarity() {
        let compiled = compile_named_hierarchy(
            named_property_range().borrowed(),
            EncodedLimits::default(),
            [11; 32],
        )
        .unwrap();

        assert_eq!(compiled.property_ranges, vec![(5, 2)]);
        assert_eq!(
            compiled.property_occurrences[2],
            Occurrence {
                negative: 1,
                positive: 0,
            }
        );
        assert_eq!(
            compiled.expression_occurrences[2],
            Occurrence {
                negative: 0,
                positive: 1,
            }
        );
        assert_eq!(compiled.feature_counts[FEATURE_OBJECT_PROPERTY_RANGE], 1);
    }

    #[test]
    fn named_property_domain_compiles_through_negative_existential() {
        let compiled = compile_named_hierarchy(
            named_property_domain().borrowed(),
            EncodedLimits::default(),
            [16; 32],
        )
        .unwrap();

        assert_eq!(
            compiled.expressions[3].tag,
            ExpressionTag::ObjectSomeValuesFrom
        );
        assert_eq!(compiled.expressions[3].arguments, [5, 1]);
        assert_eq!(compiled.subclass_axioms, vec![(3, 2)]);
        assert_eq!(compiled.expression_occurrences[1].negative, 1);
        assert_eq!(compiled.expression_occurrences[2].positive, 1);
        assert_eq!(compiled.expression_occurrences[3].negative, 1);
        assert_eq!(compiled.property_occurrences[2].negative, 1);
    }

    #[test]
    fn reflexive_named_property_compiles_has_self_rule() {
        let compiled = compile_named_hierarchy(
            reflexive_named_property().borrowed(),
            EncodedLimits::default(),
            [17; 32],
        )
        .unwrap();

        assert_eq!(compiled.expressions[2].tag, ExpressionTag::ObjectHasSelf);
        assert_eq!(compiled.expressions[2].arguments, [4]);
        assert_eq!(compiled.subclass_axioms, vec![(1, 2)]);
        assert_eq!(compiled.expression_occurrences[1].negative, 1);
        assert_eq!(compiled.expression_occurrences[2].positive, 1);
        assert_eq!(compiled.property_occurrences[2].positive, 1);
        assert_eq!(
            compiled.feature_counts[FEATURE_REFLEXIVE_OBJECT_PROPERTY],
            1
        );
    }

    #[test]
    fn named_object_property_assertion_compiles_nominal_existential() {
        let compiled = compile_named_hierarchy(
            named_object_property_assertion().borrowed(),
            EncodedLimits::default(),
            [18; 32],
        )
        .unwrap();

        assert_eq!(
            compiled.expressions[4].tag,
            ExpressionTag::ObjectSomeValuesFrom
        );
        assert_eq!(compiled.expressions[4].arguments, [6, 3]);
        assert_eq!(compiled.subclass_axioms, vec![(2, 4)]);
        assert_eq!(compiled.expression_occurrences[2].negative, 1);
        assert_eq!(compiled.expression_occurrences[3].positive, 1);
        assert_eq!(compiled.expression_occurrences[4].positive, 1);
        assert_eq!(compiled.property_occurrences[2].positive, 1);
        assert_eq!(
            compiled.feature_counts[FEATURE_OBJECT_PROPERTY_ASSERTION],
            1
        );
        assert_eq!(
            compiled.feature_counts[FEATURE_OBJECT_HAS_VALUE_POSITIVE],
            1
        );
    }

    #[test]
    fn named_hierarchy_compiler_fails_closed_outside_its_exact_slice() {
        assert!(matches!(
            compile_named_hierarchy(
                property_chain().borrowed(),
                EncodedLimits::default(),
                [0; 32],
            ),
            Err(CoreError::InvalidInput(message)) if message.contains("at least two")
        ));

        let mut malformed = named_subclass();
        malformed.root_ids = le32(&[0]);
        assert!(matches!(
            compile_named_hierarchy(
                malformed.borrowed(),
                EncodedLimits::default(),
                [0; 32],
            ),
            Err(CoreError::Protocol(message)) if message.contains("one-based")
        ));
    }

    #[test]
    fn indexed_byte_sources_validate_without_materializing_slices() {
        let owned = declaration();
        assert_eq!(
            validate_columns(owned.indexed(), EncodedLimits::default()).unwrap(),
            validate_columns(owned.borrowed(), EncodedLimits::default()).unwrap()
        );
    }

    #[test]
    fn scalar_validation_accepts_unicode_and_rejects_invalid_utf8() {
        for valid in ["", "plain", "é", "€", "𐍈"] {
            validate_utf8(valid.as_bytes(), 0, valid.len()).unwrap();
        }
        for invalid in [
            &[0x80][..],
            &[0xc0, 0x80],
            &[0xe0, 0x80, 0x80],
            &[0xed, 0xa0, 0x80],
            &[0xf0, 0x80, 0x80, 0x80],
            &[0xf4, 0x90, 0x80, 0x80],
            &[0xf5, 0x80, 0x80, 0x80],
        ] {
            assert!(validate_utf8(invalid, 0, invalid.len()).is_err());
        }

        let mut malformed = declaration();
        malformed.scalar_bytes[0] = 0xff;
        assert!(validate_columns(malformed.borrowed(), EncodedLimits::default()).is_err());

        let mut malformed = declaration();
        malformed.scalar_bytes[5] = 0xff;
        assert!(validate_columns(malformed.borrowed(), EncodedLimits::default()).is_err());
    }

    #[test]
    fn hostile_shape_scalar_graph_and_resource_inputs_fail_closed() {
        let mut malformed = declaration();
        malformed.root_ids.pop();
        assert!(validate_columns(malformed.borrowed(), EncodedLimits::default()).is_err());

        let mut malformed = declaration();
        malformed.node_field_offsets = le64(&[0, 1, 3, 4]);
        assert!(validate_columns(malformed.borrowed(), EncodedLimits::default()).is_err());

        let mut malformed = declaration();
        malformed.field_values = le64(&[1, 5, 1, 2, 0]);
        assert!(validate_columns(malformed.borrowed(), EncodedLimits::default()).is_err());

        assert!(matches!(
            validate_columns(data_range_cycle().borrowed(), EncodedLimits::default()),
            Err(CoreError::Protocol(message)) if message.contains("cyclic")
        ));

        let mut malformed = declaration();
        malformed.root_kinds[0] = ROOT_EXTENSION;
        assert!(validate_columns(malformed.borrowed(), EncodedLimits::default()).is_err());

        let tight = EncodedLimits {
            max_nodes: 2,
            ..EncodedLimits::default()
        };
        assert!(matches!(
            validate_columns(declaration().borrowed(), tight),
            Err(CoreError::Capacity(_))
        ));
        let tight = EncodedLimits {
            max_work: 1,
            ..EncodedLimits::default()
        };
        assert!(matches!(
            validate_columns(declaration().borrowed(), tight),
            Err(CoreError::Capacity(_))
        ));
    }
}
