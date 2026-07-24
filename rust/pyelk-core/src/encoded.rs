//! Defensive structural validation for pyowl-core encoded-view schema 1.
//!
//! This module borrows the eleven public columns directly.  It establishes the
//! shape, bounds, scalar arena, root-category, canonical dense ordering,
//! reachability, and acyclic graph invariants before the ELK-specific compiler
//! allocates permanent IR.  It does not advertise schema support; semantic
//! compilation remains a separate gate.

use std::cmp::Ordering;
use std::cmp::Reverse;
use std::collections::{BTreeMap, BTreeSet, BinaryHeap};

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

const RDF_PLAIN_LITERAL_IRI: &str = "http://www.w3.org/1999/02/22-rdf-syntax-ns#PlainLiteral";
const RDF_LANG_STRING_IRI: &str = "http://www.w3.org/1999/02/22-rdf-syntax-ns#langString";

// Frozen pyELK compiler feature-vector positions shared with indexing/conversion.py.
const FEATURE_ANONYMOUS_INDIVIDUAL: usize = 0;
const FEATURE_BOTTOM_OBJECT_PROPERTY_POSITIVE: usize = 2;
const FEATURE_DATA_ALL_VALUES_FROM: usize = 3;
const FEATURE_DATA_EXACT_CARDINALITY: usize = 4;
const FEATURE_DATA_HAS_VALUE: usize = 5;
const FEATURE_DATA_MAX_CARDINALITY: usize = 6;
const FEATURE_DATA_MIN_CARDINALITY: usize = 7;
const FEATURE_DATA_SOME_VALUES_FROM: usize = 12;
const FEATURE_OBJECT_PROPERTY_CHAIN: usize = 40;
const FEATURE_OBJECT_PROPERTY_RANGE: usize = 41;
const FEATURE_DIFFERENT_INDIVIDUALS: usize = 15;
const FEATURE_DISJOINT_CLASSES: usize = 16;
const FEATURE_DISJOINT_UNION: usize = 19;
const FEATURE_OBJECT_ALL_VALUES_FROM: usize = 29;
const FEATURE_OBJECT_COMPLEMENT_OF_NEGATIVE: usize = 30;
const FEATURE_OBJECT_COMPLEMENT_OF_POSITIVE: usize = 31;
const FEATURE_OBJECT_EXACT_CARDINALITY: usize = 32;
const FEATURE_OBJECT_HAS_SELF_NEGATIVE: usize = 33;
const FEATURE_OBJECT_HAS_VALUE_POSITIVE: usize = 34;
const FEATURE_OBJECT_INVERSE_OF: usize = 35;
const FEATURE_OBJECT_MAX_CARDINALITY: usize = 36;
const FEATURE_OBJECT_MIN_CARDINALITY: usize = 37;
const FEATURE_OBJECT_ONE_OF: usize = 38;
const FEATURE_OBJECT_PROPERTY_ASSERTION: usize = 39;
const FEATURE_OBJECT_UNION_OF_POSITIVE: usize = 42;
const FEATURE_OWL_NOTHING_POSITIVE: usize = 43;
const FEATURE_REFLEXIVE_OBJECT_PROPERTY: usize = 44;
const FEATURE_TOP_OBJECT_PROPERTY_NEGATIVE: usize = 48;

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

/// Bounded column counts established from buffer shape before the compiler's full validation.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct EncodedColumnShape {
    pub root_count: usize,
    pub node_count: usize,
    pub field_count: usize,
    pub item_count: usize,
    pub scalar_bytes: usize,
}

/// Rust-owned compiler result plus the exact scalar cache-key observations it consumed.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EncodedCompilation {
    pub ontology: Ontology,
    pub compatibility_observations: Vec<Vec<u8>>,
}

/// Whole-axiom policy matching pyELK's scalar compiler option.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EncodedUnsupportedPolicy {
    Ignore,
    Error,
}

/// Posting policy for a referenced source's one-based local root IDs.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EncodedPostingMode {
    Include,
    Exclude,
}

/// One borrowed structural table and its optional source-local root selection.
#[derive(Clone, Copy, Debug)]
pub struct EncodedCompilationSegment<B: ByteSource, P: ByteSource> {
    pub columns: EncodedColumns<B>,
    pub posting_mode: Option<EncodedPostingMode>,
    pub postings: P,
    pub anonymous_scope_map: P,
}

#[derive(Clone, Copy, Debug)]
struct EncodedRootSelection<P: ByteSource> {
    mode: EncodedPostingMode,
    postings: P,
    count: usize,
}

struct EncodedRootCursor<P: ByteSource> {
    selection: Option<EncodedRootSelection<P>>,
    cursor: usize,
}

struct SelectedRootIter<P: ByteSource> {
    selector: EncodedRootCursor<P>,
    next_root: usize,
    root_count: usize,
}

enum CompilationRootIter<P: ByteSource> {
    Ordered(SelectedRootIter<P>),
    Remapped { roots: Vec<usize>, cursor: usize },
}

impl<P: ByteSource> EncodedRootSelection<P> {
    fn validate(mode: EncodedPostingMode, postings: P, root_count: usize) -> CoreResult<Self> {
        let count = aligned_count(postings, 4, "encoded segment root postings")?;
        if count == 0 {
            return Err(CoreError::protocol(
                "encoded include/exclude root postings must not be empty",
            ));
        }
        let mut previous = 0_usize;
        for index in 0..count {
            let posting = encoded_posting_at(postings, index)?;
            if posting <= previous || posting > root_count {
                return Err(CoreError::protocol(
                    "encoded root postings must be sorted, unique, and in range",
                ));
            }
            previous = posting;
        }
        Ok(Self {
            mode,
            postings,
            count,
        })
    }
}

impl<P: ByteSource> EncodedRootCursor<P> {
    fn new(selection: Option<EncodedRootSelection<P>>) -> Self {
        Self {
            selection,
            cursor: 0,
        }
    }

    fn includes(&mut self, root: usize) -> CoreResult<bool> {
        let Some(selection) = self.selection else {
            return Ok(true);
        };
        let ordinal = root
            .checked_add(1)
            .ok_or_else(|| CoreError::capacity("encoded root ordinal overflow"))?;
        while self.cursor < selection.count {
            let posting = encoded_posting_at(selection.postings, self.cursor)?;
            if posting >= ordinal {
                break;
            }
            self.cursor += 1;
        }
        let listed = self.cursor < selection.count
            && encoded_posting_at(selection.postings, self.cursor)? == ordinal;
        Ok(match selection.mode {
            EncodedPostingMode::Include => listed,
            EncodedPostingMode::Exclude => !listed,
        })
    }
}

impl<P: ByteSource> SelectedRootIter<P> {
    fn new(selection: Option<EncodedRootSelection<P>>, root_count: usize) -> Self {
        Self {
            selector: EncodedRootCursor::new(selection),
            next_root: 0,
            root_count,
        }
    }

    fn next(&mut self) -> CoreResult<Option<usize>> {
        while self.next_root < self.root_count {
            let root = self.next_root;
            self.next_root += 1;
            if self.selector.includes(root)? {
                return Ok(Some(root));
            }
        }
        Ok(None)
    }
}

impl<P: ByteSource> CompilationRootIter<P> {
    fn next(&mut self) -> CoreResult<Option<usize>> {
        match self {
            Self::Ordered(roots) => roots.next(),
            Self::Remapped { roots, cursor } => {
                let root = roots.get(*cursor).copied();
                *cursor = cursor
                    .checked_add(usize::from(root.is_some()))
                    .ok_or_else(|| CoreError::capacity("remapped root cursor overflow"))?;
                Ok(root)
            }
        }
    }
}

fn encoded_posting_at<P: ByteSource>(postings: P, index: usize) -> CoreResult<usize> {
    usize::try_from(u32_at(postings, index, "encoded segment root posting")?)
        .map_err(|_| CoreError::capacity("encoded root posting exceeds usize"))
}

#[derive(Clone, Debug, Eq, PartialEq)]
enum AxiomCompileError {
    Core(CoreError),
    Unsupported { feature: usize, name: &'static str },
}

impl AxiomCompileError {
    fn unsupported(feature: usize, name: &'static str) -> Self {
        Self::Unsupported { feature, name }
    }
}

impl From<CoreError> for AxiomCompileError {
    fn from(error: CoreError) -> Self {
        Self::Core(error)
    }
}

type AxiomCompileResult<T> = Result<T, AxiomCompileError>;

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
struct AnnotatedAxiomState {
    has_unannotated: bool,
    compiled: bool,
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
    validate_columns_with_lengths(columns, limits).map(|(validated, _lengths)| validated)
}

/// Validate coarse column widths, paired lengths, and allocation limits without scanning rows.
///
/// Native bindings use this before resolving segment metadata. Permanent compilation still calls
/// [`validate_columns`] (or its internal equivalent) exactly once before consuming any row.
pub fn validate_column_shape<B: ByteSource>(
    columns: EncodedColumns<B>,
    limits: EncodedLimits,
) -> CoreResult<EncodedColumnShape> {
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
    Ok(EncodedColumnShape {
        root_count,
        node_count,
        field_count,
        item_count,
        scalar_bytes: columns.scalar_bytes.len(),
    })
}

fn validate_columns_with_lengths<B: ByteSource>(
    columns: EncodedColumns<B>,
    limits: EncodedLimits,
) -> CoreResult<(ValidatedEncodedColumns, Vec<u64>)> {
    let shape = validate_column_shape(columns, limits)?;
    let EncodedColumnShape {
        root_count,
        node_count,
        field_count,
        item_count,
        scalar_bytes,
    } = shape;

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
    validate_model_scalar_constraints(&columns, node_count)?;
    Ok((
        ValidatedEncodedColumns {
            root_count,
            node_count,
            field_count,
            item_count,
            scalar_bytes,
            work,
        },
        canonical_lengths,
    ))
}

fn validate_model_scalar_constraints<B: ByteSource>(
    columns: &EncodedColumns<B>,
    node_count: usize,
) -> CoreResult<()> {
    for node in 0..node_count {
        match u16_at(columns.node_tags, node, "model scalar node tag")? {
            1 => validate_absolute_iri(&text_field(node, 0, columns)?)?,
            3 => {
                let scope_length = field_scalar_length(node, 0, columns)?;
                let local_length = field_scalar_length(node, 1, columns)?;
                if scope_length != 32 || local_length == 0 {
                    return Err(CoreError::protocol(
                        "encoded anonymous individual has an invalid scope or local key",
                    ));
                }
            }
            4 => validate_literal_constraints(node, columns)?,
            _ => {}
        }
    }
    Ok(())
}

fn field_scalar_length<B: ByteSource>(
    node: usize,
    position: usize,
    columns: &EncodedColumns<B>,
) -> CoreResult<usize> {
    let start = usize_at(columns.node_field_offsets, node, "node field offset")?;
    let field = start
        .checked_add(position)
        .ok_or_else(|| CoreError::capacity("encoded scalar field index overflow"))?;
    usize_at(columns.field_lengths, field, "scalar field length")
}

fn validate_literal_constraints<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
) -> CoreResult<()> {
    let datatype = decode_entity(node_field(node, 1, columns)?, columns)?;
    if datatype.kind != EntityKind::Datatype {
        return Err(CoreError::internal(
            "validated literal datatype has the wrong entity kind",
        ));
    }
    if datatype.iri == RDF_LANG_STRING_IRI {
        return Err(CoreError::protocol(
            "encoded rdf:langString literal is not canonical model schema 1",
        ));
    }
    if let Some(language) = optional_text_field(node, 2, columns)? {
        if datatype.iri != RDF_PLAIN_LITERAL_IRI {
            return Err(CoreError::protocol(
                "encoded literal language requires rdf:PlainLiteral",
            ));
        }
        if language.bytes().any(|byte| byte.is_ascii_uppercase()) || !valid_language_tag(&language)
        {
            return Err(CoreError::protocol(
                "encoded literal language is not a canonical BCP 47 tag",
            ));
        }
    }
    Ok(())
}

fn validate_absolute_iri(value: &str) -> CoreResult<()> {
    let bytes = value.as_bytes();
    let Some(colon) = bytes.iter().position(|byte| *byte == b':') else {
        return Err(CoreError::protocol("encoded IRI is not absolute"));
    };
    if colon == 0
        || !bytes[0].is_ascii_alphabetic()
        || bytes[1..colon]
            .iter()
            .any(|byte| !byte.is_ascii_alphanumeric() && !matches!(byte, b'+' | b'.' | b'-'))
    {
        return Err(CoreError::protocol(
            "encoded IRI has an invalid absolute scheme",
        ));
    }
    for character in value.chars() {
        let codepoint = u32::from(character);
        if matches!(
            character,
            '<' | '>' | '"' | '{' | '}' | '|' | '\\' | '^' | '`'
        ) || codepoint <= 0x20
            || (0x7f..=0x9f).contains(&codepoint)
            || (0xfdd0..=0xfdef).contains(&codepoint)
            || matches!(codepoint & 0xffff, 0xfffe | 0xffff)
        {
            return Err(CoreError::protocol(
                "encoded IRI contains a forbidden Unicode scalar",
            ));
        }
    }
    let mut index = 0_usize;
    while index < bytes.len() {
        if bytes[index] != b'%' {
            index += 1;
            continue;
        }
        if index + 2 >= bytes.len()
            || !bytes[index + 1].is_ascii_hexdigit()
            || !bytes[index + 2].is_ascii_hexdigit()
        {
            return Err(CoreError::protocol(
                "encoded IRI contains an invalid percent escape",
            ));
        }
        index += 3;
    }
    Ok(())
}

fn valid_language_tag(language: &str) -> bool {
    const GRANDFATHERED: &[&str] = &[
        "art-lojban",
        "cel-gaulish",
        "en-gb-oed",
        "i-ami",
        "i-bnn",
        "i-default",
        "i-enochian",
        "i-hak",
        "i-klingon",
        "i-lux",
        "i-mingo",
        "i-navajo",
        "i-pwn",
        "i-tao",
        "i-tay",
        "i-tsu",
        "no-bok",
        "no-nyn",
        "sgn-be-fr",
        "sgn-be-nl",
        "sgn-ch-de",
        "zh-guoyu",
        "zh-hakka",
        "zh-min",
        "zh-min-nan",
        "zh-xiang",
    ];
    if language.is_empty()
        || !language
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
    {
        return false;
    }
    let parts = language.split('-').collect::<Vec<_>>();
    if parts.iter().any(|part| part.is_empty()) {
        return false;
    }
    if GRANDFATHERED.contains(&language) {
        return true;
    }
    if parts.first() == Some(&"x") {
        return parts.len() > 1 && parts[1..].iter().all(|part| (1..=8).contains(&part.len()));
    }

    let primary = parts[0];
    if !primary.bytes().all(|byte| byte.is_ascii_alphabetic()) || !(2..=8).contains(&primary.len())
    {
        return false;
    }
    let mut index = 1_usize;
    if (2..=3).contains(&primary.len()) {
        let mut extlangs = 0_usize;
        while index < parts.len()
            && parts[index].len() == 3
            && parts[index].bytes().all(|byte| byte.is_ascii_alphabetic())
            && extlangs < 3
        {
            index += 1;
            extlangs += 1;
        }
    }
    if index < parts.len()
        && parts[index].len() == 4
        && parts[index].bytes().all(|byte| byte.is_ascii_alphabetic())
    {
        index += 1;
    }
    if index < parts.len()
        && ((parts[index].len() == 2
            && parts[index].bytes().all(|byte| byte.is_ascii_alphabetic()))
            || (parts[index].len() == 3 && parts[index].bytes().all(|byte| byte.is_ascii_digit())))
    {
        index += 1;
    }

    let mut variants = BTreeSet::new();
    while index < parts.len()
        && ((5..=8).contains(&parts[index].len())
            || (parts[index].len() == 4
                && parts[index]
                    .as_bytes()
                    .first()
                    .is_some_and(u8::is_ascii_digit)))
    {
        if !variants.insert(parts[index]) {
            return false;
        }
        index += 1;
    }

    let mut singletons = BTreeSet::new();
    while index < parts.len() && parts[index].len() == 1 && parts[index] != "x" {
        if !singletons.insert(parts[index]) {
            return false;
        }
        index += 1;
        let start = index;
        while index < parts.len() && (2..=8).contains(&parts[index].len()) {
            index += 1;
        }
        if index == start {
            return false;
        }
    }
    if index < parts.len() && parts[index] == "x" {
        index += 1;
        let start = index;
        while index < parts.len() && (1..=8).contains(&parts[index].len()) {
            index += 1;
        }
        if index == start {
            return false;
        }
    }
    index == parts.len()
}

/// Compile the installed encoded-ontology slice with strict unsupported handling.
///
/// The compatibility wrapper binds the caller-provided private source fingerprint after direct
/// compilation. Capability advertisement remains disabled until the complete schema slice and
/// segment/lifecycle/performance gates pass.
///
/// `source_fingerprint` is already bound by the caller to the core snapshot and compiler options;
/// the structural columns intentionally do not carry pyELK's private cache-key material.
pub fn compile_named_hierarchy<B: ByteSource>(
    columns: EncodedColumns<B>,
    limits: EncodedLimits,
    source_fingerprint: [u8; 32],
) -> CoreResult<Ontology> {
    compile_named_hierarchy_with_policy(
        columns,
        limits,
        source_fingerprint,
        EncodedUnsupportedPolicy::Error,
    )
}

/// Compile the installed exact slice with scalar-compatible unsupported-axiom handling.
pub fn compile_named_hierarchy_with_policy<B: ByteSource>(
    columns: EncodedColumns<B>,
    limits: EncodedLimits,
    source_fingerprint: [u8; 32],
    unsupported: EncodedUnsupportedPolicy,
) -> CoreResult<Ontology> {
    let mut compilation = compile_encoded_hierarchy_with_policy(columns, limits, unsupported)?;
    compilation.ontology.source_fingerprint = source_fingerprint;
    Ok(compilation.ontology)
}

/// Compile structural columns and return cache-key observations before fingerprint binding.
pub fn compile_encoded_hierarchy_with_policy<B: ByteSource>(
    columns: EncodedColumns<B>,
    limits: EncodedLimits,
    unsupported: EncodedUnsupportedPolicy,
) -> CoreResult<EncodedCompilation> {
    compile_encoded_hierarchy_with_selection::<B, B>(columns, limits, unsupported, None)
}

/// Compile one exact include/exclude selection over the source-local root table.
pub fn compile_encoded_hierarchy_selected_with_policy<B: ByteSource, P: ByteSource>(
    columns: EncodedColumns<B>,
    limits: EncodedLimits,
    unsupported: EncodedUnsupportedPolicy,
    mode: EncodedPostingMode,
    postings: P,
) -> CoreResult<EncodedCompilation> {
    let root_count = aligned_count(columns.root_ids, 4, "root_ids")?;
    let selection = EncodedRootSelection::validate(mode, postings, root_count)?;
    compile_encoded_hierarchy_with_selection(columns, limits, unsupported, Some(selection))
}

/// Compile an arbitrary set of already-resolved structural segments through one canonical merge.
///
/// Segment tables remain borrowed and independent. Their source-local selections are applied
/// before a k-way canonical root merge, exact structural/annotation duplicates compile once, and
/// only the final ELK representation is retained.
pub fn compile_encoded_segments_with_policy<B: ByteSource, P: ByteSource>(
    segments: &[EncodedCompilationSegment<B, P>],
    limits: EncodedLimits,
    unsupported: EncodedUnsupportedPolicy,
) -> CoreResult<EncodedCompilation> {
    if segments.is_empty() {
        return Err(CoreError::protocol(
            "encoded segment compiler requires at least one structural table",
        ));
    }
    if segments.len() > 256 {
        return Err(CoreError::capacity(
            "encoded segment compiler exceeds the table-count limit",
        ));
    }
    validate_compilation_segment_limits(segments, limits)?;

    let mut tables = Vec::new();
    tables
        .try_reserve_exact(segments.len())
        .map_err(|_| CoreError::capacity("encoded segment table allocation failed"))?;
    let mut root_iters = Vec::new();
    root_iters
        .try_reserve_exact(segments.len())
        .map_err(|_| CoreError::capacity("encoded segment cursor allocation failed"))?;
    let mut work = 0_u64;
    for segment in segments {
        let root_count = aligned_count(segment.columns.root_ids, 4, "segment root_ids")?;
        let selection = if let Some(mode) = segment.posting_mode {
            Some(EncodedRootSelection::validate(
                mode,
                segment.postings,
                root_count,
            )?)
        } else {
            if !segment.postings.is_empty() {
                return Err(CoreError::protocol(
                    "encoded ALL segment must not carry root postings",
                ));
            }
            None
        };
        let (validated, canonical_lengths) =
            validate_columns_with_lengths(segment.columns, limits)?;
        work = work
            .checked_add(validated.work)
            .ok_or_else(|| CoreError::capacity("encoded segment validation work overflow"))?;
        if work > limits.max_work {
            return Err(CoreError::capacity(
                "encoded segments exceed the combined validation work limit",
            ));
        }
        let scope_replacements = build_scope_replacements(
            segment.anonymous_scope_map,
            &segment.columns,
            validated.node_count,
            &mut work,
            limits.max_work,
        )?;
        let table_index = tables.len();
        tables.push(CompilableSegment {
            columns: segment.columns,
            canonical_lengths,
            node_count: validated.node_count,
            scope_replacements,
        });
        if tables[table_index].scope_replacements.is_empty() {
            root_iters.push(CompilationRootIter::Ordered(SelectedRootIter::new(
                selection,
                validated.root_count,
            )));
        } else {
            root_iters.push(build_remapped_root_iter(
                table_index,
                selection,
                validated.root_count,
                &tables,
                &mut work,
                limits.max_work,
            )?);
        }
    }

    let mut current_roots = Vec::new();
    current_roots
        .try_reserve_exact(root_iters.len())
        .map_err(|_| CoreError::capacity("encoded current-root allocation failed"))?;
    for roots in &mut root_iters {
        current_roots.push(roots.next()?);
    }
    let mut equal_tables = Vec::new();
    equal_tables
        .try_reserve_exact(tables.len())
        .map_err(|_| CoreError::capacity("encoded equal-root allocation failed"))?;
    let mut previous_logical_root = None;
    let mut builder = NamedHierarchyBuilder::with_policy(unsupported);
    let mut transaction = NamedHierarchyBuilder::transaction();
    while let Some(mut selected_table) = current_roots.iter().position(Option::is_some) {
        equal_tables.clear();
        equal_tables.push(selected_table);
        for candidate in selected_table + 1..current_roots.len() {
            if current_roots[candidate].is_none() {
                continue;
            }
            let ordering = compare_segment_roots(
                SegmentRootRef {
                    table: candidate,
                    root: current_roots[candidate].ok_or_else(|| {
                        CoreError::internal("candidate segment cursor unexpectedly ended")
                    })?,
                },
                SegmentRootRef {
                    table: selected_table,
                    root: current_roots[selected_table].ok_or_else(|| {
                        CoreError::internal("selected segment cursor unexpectedly ended")
                    })?,
                },
                &tables,
                &mut work,
                limits.max_work,
            )?;
            match ordering {
                Ordering::Less => {
                    selected_table = candidate;
                    equal_tables.clear();
                    equal_tables.push(candidate);
                }
                Ordering::Equal => equal_tables.push(candidate),
                Ordering::Greater => {}
            }
        }
        let selected_root = current_roots[selected_table]
            .ok_or_else(|| CoreError::internal("selected segment root disappeared"))?;
        compile_segment_root(
            SegmentRootRef {
                table: selected_table,
                root: selected_root,
            },
            &tables,
            &mut previous_logical_root,
            &mut work,
            limits.max_work,
            &mut builder,
            &mut transaction,
        )?;
        for table in equal_tables.iter().copied() {
            current_roots[table] = root_iters[table].next()?;
        }
    }
    builder.freeze([0; 32])
}

fn validate_compilation_segment_limits<B: ByteSource, P: ByteSource>(
    segments: &[EncodedCompilationSegment<B, P>],
    limits: EncodedLimits,
) -> CoreResult<()> {
    let mut roots = 0_usize;
    let mut nodes = 0_usize;
    let mut fields = 0_usize;
    let mut items = 0_usize;
    let mut scalars = 0_usize;
    let mut metadata = 0_usize;
    let add = |total: &mut usize, value: usize, name: &str| {
        *total = total
            .checked_add(value)
            .ok_or_else(|| CoreError::capacity(format!("encoded segment {name} overflow")))?;
        Ok(())
    };
    for segment in segments {
        add(
            &mut roots,
            aligned_count(segment.columns.root_ids, 4, "segment root_ids")?,
            "root count",
        )?;
        add(
            &mut nodes,
            aligned_count(segment.columns.node_tags, 2, "segment node_tags")?,
            "node count",
        )?;
        add(
            &mut fields,
            segment.columns.field_kinds.len(),
            "field count",
        )?;
        add(&mut items, segment.columns.item_kinds.len(), "item count")?;
        add(
            &mut scalars,
            segment.columns.scalar_bytes.len(),
            "scalar byte count",
        )?;
        add(&mut metadata, segment.postings.len(), "posting byte count")?;
        add(
            &mut metadata,
            segment.anonymous_scope_map.len(),
            "scope-map byte count",
        )?;
    }
    enforce_count(roots, limits.max_roots, "encoded segment root count")?;
    enforce_count(nodes, limits.max_nodes, "encoded segment node count")?;
    enforce_count(fields, limits.max_fields, "encoded segment field count")?;
    enforce_count(items, limits.max_items, "encoded segment item count")?;
    enforce_count(
        scalars,
        limits.max_scalar_bytes,
        "encoded segment scalar byte count",
    )?;
    enforce_count(
        metadata,
        limits.max_scalar_bytes,
        "encoded segment metadata byte count",
    )
}

/// Compile one direct source plus one local overlay-delta table without flattening either table.
///
/// Exact duplicate roots and annotation-only logical variants are merged structurally before
/// compilation, and all aggregate limits are enforced across both borrowed tables.
pub fn compile_encoded_overlay_delta_with_policy<B: ByteSource>(
    source_columns: EncodedColumns<B>,
    delta_columns: EncodedColumns<B>,
    limits: EncodedLimits,
    unsupported: EncodedUnsupportedPolicy,
) -> CoreResult<EncodedCompilation> {
    compile_encoded_overlay_delta_with_selection::<B, B>(
        source_columns,
        delta_columns,
        limits,
        unsupported,
        None,
    )
}

/// Compile an include/exclude source selection plus one local overlay-delta table.
pub fn compile_encoded_overlay_delta_selected_with_policy<B: ByteSource, P: ByteSource>(
    source_columns: EncodedColumns<B>,
    delta_columns: EncodedColumns<B>,
    limits: EncodedLimits,
    unsupported: EncodedUnsupportedPolicy,
    mode: EncodedPostingMode,
    postings: P,
) -> CoreResult<EncodedCompilation> {
    let source_root_count = aligned_count(source_columns.root_ids, 4, "source root_ids")?;
    let selection = EncodedRootSelection::validate(mode, postings, source_root_count)?;
    compile_encoded_overlay_delta_with_selection(
        source_columns,
        delta_columns,
        limits,
        unsupported,
        Some(selection),
    )
}

fn compile_encoded_overlay_delta_with_selection<B: ByteSource, P: ByteSource>(
    source_columns: EncodedColumns<B>,
    delta_columns: EncodedColumns<B>,
    limits: EncodedLimits,
    unsupported: EncodedUnsupportedPolicy,
    source_selection: Option<EncodedRootSelection<P>>,
) -> CoreResult<EncodedCompilation> {
    validate_combined_column_limits(&source_columns, &delta_columns, limits)?;
    let (source_validated, source_lengths) = validate_columns_with_lengths(source_columns, limits)?;
    let (delta_validated, delta_lengths) = validate_columns_with_lengths(delta_columns, limits)?;
    let mut work = source_validated
        .work
        .checked_add(delta_validated.work)
        .ok_or_else(|| CoreError::capacity("encoded overlay validation work overflow"))?;
    if work > limits.max_work {
        return Err(CoreError::capacity(
            "encoded overlay validation exceeds the combined work limit",
        ));
    }
    let tables = [
        CompilableSegment {
            columns: source_columns,
            canonical_lengths: source_lengths,
            node_count: source_validated.node_count,
            scope_replacements: Vec::new(),
        },
        CompilableSegment {
            columns: delta_columns,
            canonical_lengths: delta_lengths,
            node_count: delta_validated.node_count,
            scope_replacements: Vec::new(),
        },
    ];
    let mut source_roots = SelectedRootIter::new(source_selection, source_validated.root_count);
    let mut delta_roots = SelectedRootIter::<B>::new(None, delta_validated.root_count);
    let mut source_root = source_roots.next()?;
    let mut delta_root = delta_roots.next()?;
    let mut builder = NamedHierarchyBuilder::with_policy(unsupported);
    let mut transaction = NamedHierarchyBuilder::transaction();
    let mut previous_logical_root = None;
    while source_root.is_some() || delta_root.is_some() {
        let ordering = match (source_root, delta_root) {
            (Some(left), Some(right)) => {
                let left_kind = byte_at(source_columns.root_kinds, left, "source root kind")?;
                let right_kind = byte_at(delta_columns.root_kinds, right, "delta root kind")?;
                let kind_order = left_kind.cmp(&right_kind);
                if kind_order != Ordering::Equal {
                    kind_order
                } else {
                    let left_node = node_index(
                        u32_at(source_columns.root_ids, left, "source root node ID")?,
                        source_validated.node_count,
                    )?;
                    let right_node = node_index(
                        u32_at(delta_columns.root_ids, right, "delta root node ID")?,
                        delta_validated.node_count,
                    )?;
                    compare_canonical_nodes_between(
                        left_node,
                        right_node,
                        &tables[0].columns,
                        &tables[1].columns,
                        &tables[0].canonical_lengths,
                        &tables[1].canonical_lengths,
                        &mut work,
                        limits.max_work,
                    )?
                }
            }
            (Some(_), None) => Ordering::Less,
            (None, Some(_)) => Ordering::Greater,
            (None, None) => break,
        };
        match ordering {
            Ordering::Less => {
                compile_segment_root(
                    SegmentRootRef {
                        table: 0,
                        root: source_root.ok_or_else(|| {
                            CoreError::internal("source overlay cursor unexpectedly ended")
                        })?,
                    },
                    &tables,
                    &mut previous_logical_root,
                    &mut work,
                    limits.max_work,
                    &mut builder,
                    &mut transaction,
                )?;
                source_root = source_roots.next()?;
            }
            Ordering::Greater => {
                compile_segment_root(
                    SegmentRootRef {
                        table: 1,
                        root: delta_root.ok_or_else(|| {
                            CoreError::internal("delta overlay cursor unexpectedly ended")
                        })?,
                    },
                    &tables,
                    &mut previous_logical_root,
                    &mut work,
                    limits.max_work,
                    &mut builder,
                    &mut transaction,
                )?;
                delta_root = delta_roots.next()?;
            }
            Ordering::Equal => {
                compile_segment_root(
                    SegmentRootRef {
                        table: 0,
                        root: source_root.ok_or_else(|| {
                            CoreError::internal("equal overlay source cursor unexpectedly ended")
                        })?,
                    },
                    &tables,
                    &mut previous_logical_root,
                    &mut work,
                    limits.max_work,
                    &mut builder,
                    &mut transaction,
                )?;
                source_root = source_roots.next()?;
                delta_root = delta_roots.next()?;
            }
        }
    }
    builder.freeze([0; 32])
}

fn validate_combined_column_limits<B: ByteSource>(
    source: &EncodedColumns<B>,
    delta: &EncodedColumns<B>,
    limits: EncodedLimits,
) -> CoreResult<()> {
    let checked_sum = |left: usize, right: usize, name: &str| {
        left.checked_add(right)
            .ok_or_else(|| CoreError::capacity(format!("encoded overlay {name} overflow")))
    };
    enforce_count(
        checked_sum(
            aligned_count(source.root_ids, 4, "source root_ids")?,
            aligned_count(delta.root_ids, 4, "delta root_ids")?,
            "root count",
        )?,
        limits.max_roots,
        "encoded overlay root count",
    )?;
    enforce_count(
        checked_sum(
            aligned_count(source.node_tags, 2, "source node_tags")?,
            aligned_count(delta.node_tags, 2, "delta node_tags")?,
            "node count",
        )?,
        limits.max_nodes,
        "encoded overlay node count",
    )?;
    enforce_count(
        checked_sum(
            source.field_kinds.len(),
            delta.field_kinds.len(),
            "field count",
        )?,
        limits.max_fields,
        "encoded overlay field count",
    )?;
    enforce_count(
        checked_sum(
            source.item_kinds.len(),
            delta.item_kinds.len(),
            "item count",
        )?,
        limits.max_items,
        "encoded overlay item count",
    )?;
    enforce_count(
        checked_sum(
            source.scalar_bytes.len(),
            delta.scalar_bytes.len(),
            "scalar byte count",
        )?,
        limits.max_scalar_bytes,
        "encoded overlay scalar byte count",
    )
}

struct CompilableSegment<B: ByteSource> {
    columns: EncodedColumns<B>,
    canonical_lengths: Vec<u64>,
    node_count: usize,
    scope_replacements: Vec<ScalarScopeReplacement>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct ScalarScopeReplacement {
    start: usize,
    target: [u8; 32],
}

#[derive(Clone, Copy)]
struct ScopeMappedByteSource<'a, B: ByteSource> {
    source: B,
    replacements: &'a [ScalarScopeReplacement],
}

impl<B: ByteSource> ByteSource for ScopeMappedByteSource<'_, B> {
    fn len(self) -> usize {
        self.source.len()
    }

    fn byte(self, index: usize) -> Option<u8> {
        let insertion = self
            .replacements
            .partition_point(|replacement| replacement.start <= index);
        if insertion != 0 {
            let replacement = &self.replacements[insertion - 1];
            let offset = index.checked_sub(replacement.start)?;
            if let Some(value) = replacement.target.get(offset) {
                return Some(*value);
            }
        }
        self.source.byte(index)
    }
}

impl<B: ByteSource> CompilableSegment<B> {
    fn mapped_columns(&self) -> EncodedColumns<ScopeMappedByteSource<'_, B>> {
        let plain = |source| ScopeMappedByteSource {
            source,
            replacements: &[],
        };
        EncodedColumns {
            root_kinds: plain(self.columns.root_kinds),
            root_ids: plain(self.columns.root_ids),
            node_tags: plain(self.columns.node_tags),
            node_field_offsets: plain(self.columns.node_field_offsets),
            field_kinds: plain(self.columns.field_kinds),
            field_values: plain(self.columns.field_values),
            field_lengths: plain(self.columns.field_lengths),
            item_kinds: plain(self.columns.item_kinds),
            item_values: plain(self.columns.item_values),
            item_lengths: plain(self.columns.item_lengths),
            scalar_bytes: ScopeMappedByteSource {
                source: self.columns.scalar_bytes,
                replacements: &self.scope_replacements,
            },
        }
    }
}

fn build_scope_replacements<B: ByteSource, P: ByteSource>(
    scope_map: P,
    columns: &EncodedColumns<B>,
    node_count: usize,
    work: &mut u64,
    max_work: u64,
) -> CoreResult<Vec<ScalarScopeReplacement>> {
    if scope_map.is_empty() {
        return Ok(Vec::new());
    }
    let row_count = aligned_count(scope_map, 64, "anonymous scope map")?;
    claim_work(
        work,
        u64::try_from(scope_map.len())
            .map_err(|_| CoreError::capacity("anonymous scope-map work overflow"))?,
        max_work,
    )?;
    let mut mappings = Vec::new();
    mappings
        .try_reserve_exact(row_count)
        .map_err(|_| CoreError::capacity("anonymous scope-map allocation failed"))?;
    let mut previous = None;
    for row in 0..row_count {
        let start = row
            .checked_mul(64)
            .ok_or_else(|| CoreError::capacity("anonymous scope-map offset overflow"))?;
        let source = fixed_bytes_32(scope_map, start, "anonymous scope-map source")?;
        let target = fixed_bytes_32(
            scope_map,
            start
                .checked_add(32)
                .ok_or_else(|| CoreError::capacity("anonymous scope-map target overflow"))?,
            "anonymous scope-map target",
        )?;
        if previous.is_some_and(|value| value >= source) || source == target {
            return Err(CoreError::protocol(
                "anonymous scope-map sources must be sorted, unique, and nonidentity",
            ));
        }
        previous = Some(source);
        mappings.push((source, target));
    }

    let mut replacements = Vec::new();
    for node in 0..node_count {
        claim_work(work, 1, max_work)?;
        if u16_at(columns.node_tags, node, "scope-mapped node tag")? != 3 {
            continue;
        }
        let field = usize_at(
            columns.node_field_offsets,
            node,
            "anonymous scope field offset",
        )?;
        let (kind, start, length) = component_parts(ComponentRow::Field(field), columns)?;
        if kind != COMPONENT_BYTES || length != 32 {
            return Err(CoreError::internal(
                "validated anonymous scope field changed shape",
            ));
        }
        let source = fixed_bytes_32(columns.scalar_bytes, start, "anonymous scope")?;
        if let Ok(index) = mappings.binary_search_by_key(&source, |(candidate, _)| *candidate) {
            replacements.push(ScalarScopeReplacement {
                start,
                target: mappings[index].1,
            });
        }
    }
    replacements.sort_unstable_by_key(|replacement| replacement.start);
    Ok(replacements)
}

fn fixed_bytes_32<B: ByteSource>(source: B, start: usize, name: &str) -> CoreResult<[u8; 32]> {
    let mut value = [0_u8; 32];
    for (offset, byte) in value.iter_mut().enumerate() {
        *byte = source
            .byte(
                start
                    .checked_add(offset)
                    .ok_or_else(|| CoreError::capacity(format!("{name} offset overflow")))?,
            )
            .ok_or_else(|| CoreError::protocol(format!("{name} is truncated")))?;
    }
    Ok(value)
}

#[derive(Clone, Copy)]
struct SegmentRootRef {
    table: usize,
    root: usize,
}

fn compare_segment_roots<B: ByteSource>(
    left: SegmentRootRef,
    right: SegmentRootRef,
    tables: &[CompilableSegment<B>],
    work: &mut u64,
    max_work: u64,
) -> CoreResult<Ordering> {
    let left_table = tables
        .get(left.table)
        .ok_or_else(|| CoreError::internal("left encoded segment table is out of bounds"))?;
    let right_table = tables
        .get(right.table)
        .ok_or_else(|| CoreError::internal("right encoded segment table is out of bounds"))?;
    let left_columns = left_table.mapped_columns();
    let right_columns = right_table.mapped_columns();
    let left_kind = byte_at(left_columns.root_kinds, left.root, "left segment root kind")?;
    let right_kind = byte_at(
        right_columns.root_kinds,
        right.root,
        "right segment root kind",
    )?;
    let ordering = left_kind.cmp(&right_kind);
    if ordering != Ordering::Equal {
        return Ok(ordering);
    }
    let left_node = node_index(
        u32_at(
            left_columns.root_ids,
            left.root,
            "left segment root node ID",
        )?,
        left_table.node_count,
    )?;
    let right_node = node_index(
        u32_at(
            right_columns.root_ids,
            right.root,
            "right segment root node ID",
        )?,
        right_table.node_count,
    )?;
    compare_canonical_nodes_between(
        left_node,
        right_node,
        &left_columns,
        &right_columns,
        &left_table.canonical_lengths,
        &right_table.canonical_lengths,
        work,
        max_work,
    )
}

fn build_remapped_root_iter<B: ByteSource, P: ByteSource>(
    table: usize,
    selection: Option<EncodedRootSelection<P>>,
    root_count: usize,
    tables: &[CompilableSegment<B>],
    work: &mut u64,
    max_work: u64,
) -> CoreResult<CompilationRootIter<P>> {
    let mut selected = SelectedRootIter::new(selection, root_count);
    let mut roots = Vec::new();
    while let Some(root) = selected.next()? {
        roots
            .try_reserve(1)
            .map_err(|_| CoreError::capacity("remapped root-order allocation failed"))?;
        roots.push(root);
    }
    let mut comparison_error = None;
    roots.sort_unstable_by(|left, right| {
        if comparison_error.is_some() {
            return Ordering::Equal;
        }
        match compare_segment_roots(
            SegmentRootRef { table, root: *left },
            SegmentRootRef {
                table,
                root: *right,
            },
            tables,
            work,
            max_work,
        ) {
            Ok(ordering) => ordering,
            Err(error) => {
                comparison_error = Some(error);
                Ordering::Equal
            }
        }
    });
    if let Some(error) = comparison_error {
        return Err(error);
    }
    let mut deduplicated = Vec::new();
    deduplicated
        .try_reserve_exact(roots.len())
        .map_err(|_| CoreError::capacity("remapped root deduplication allocation failed"))?;
    for root in roots {
        let duplicate = if let Some(previous) = deduplicated.last().copied() {
            compare_segment_roots(
                SegmentRootRef {
                    table,
                    root: previous,
                },
                SegmentRootRef { table, root },
                tables,
                work,
                max_work,
            )? == Ordering::Equal
        } else {
            false
        };
        if !duplicate {
            deduplicated.push(root);
        }
    }
    Ok(CompilationRootIter::Remapped {
        roots: deduplicated,
        cursor: 0,
    })
}

#[allow(clippy::too_many_arguments)]
fn compile_segment_root<B: ByteSource>(
    current: SegmentRootRef,
    tables: &[CompilableSegment<B>],
    previous_logical_root: &mut Option<SegmentRootRef>,
    work: &mut u64,
    max_work: u64,
    builder: &mut NamedHierarchyBuilder,
    transaction: &mut NamedHierarchyBuilder,
) -> CoreResult<()> {
    let table = tables
        .get(current.table)
        .ok_or_else(|| CoreError::internal("encoded segment root table is out of bounds"))?;
    let mapped_columns = table.mapped_columns();
    let columns = &mapped_columns;
    let kind = byte_at(columns.root_kinds, current.root, "segment root kind")?;
    let node = node_index(
        u32_at(columns.root_ids, current.root, "segment root node ID")?,
        table.node_count,
    )?;
    let tag = u16_at(columns.node_tags, node, "segment root node tag")?;
    if kind == ROOT_AXIOM && !(120..=123).contains(&tag) {
        let start = usize_at(columns.node_field_offsets, node, "axiom field offset")?;
        let field_limit = annotation_field(node, columns)?
            .checked_sub(start)
            .ok_or_else(|| CoreError::internal("axiom annotation field precedes its start"))?;
        if let Some(previous) = *previous_logical_root {
            let previous_table = tables.get(previous.table).ok_or_else(|| {
                CoreError::internal("previous encoded segment root table is out of bounds")
            })?;
            let previous_columns = previous_table.mapped_columns();
            let left = node_index(
                u32_at(
                    previous_columns.root_ids,
                    previous.root,
                    "previous segment root node ID",
                )?,
                previous_table.node_count,
            )?;
            let ordering = compare_canonical_nodes_between_with_field_limit(
                left,
                node,
                &previous_columns,
                columns,
                &previous_table.canonical_lengths,
                &table.canonical_lengths,
                Some(field_limit),
                work,
                max_work,
            )?;
            if ordering == Ordering::Equal {
                return Ok(());
            }
        }
        *previous_logical_root = Some(current);
    }
    compile_root_from_columns(
        current.root,
        columns,
        table.node_count,
        builder,
        transaction,
    )
}

fn compile_root_from_columns<B: ByteSource>(
    root: usize,
    columns: &EncodedColumns<B>,
    node_count: usize,
    builder: &mut NamedHierarchyBuilder,
    transaction: &mut NamedHierarchyBuilder,
) -> CoreResult<()> {
    let kind = byte_at(columns.root_kinds, root, "root kind")?;
    if kind == ROOT_ONTOLOGY_ANNOTATION {
        return Ok(());
    }
    let node = node_index(u32_at(columns.root_ids, root, "root node ID")?, node_count)?;
    let tag = u16_at(columns.node_tags, node, "root node tag")?;
    if kind == ROOT_EXTENSION {
        if tag == 148 {
            return builder.handle_unsupported(46, "SWRL_RULE");
        }
        return Err(CoreError::internal(
            "validated extension root has an unexpected constructor tag",
        ));
    }
    if kind != ROOT_AXIOM {
        return Err(CoreError::internal(
            "validated encoded root has an unexpected category",
        ));
    }
    compile_axiom_node(tag, node, columns, builder, transaction)
}

fn compile_encoded_hierarchy_with_selection<B: ByteSource, P: ByteSource>(
    columns: EncodedColumns<B>,
    limits: EncodedLimits,
    unsupported: EncodedUnsupportedPolicy,
    selection: Option<EncodedRootSelection<P>>,
) -> CoreResult<EncodedCompilation> {
    let validated = validate_columns(columns, limits)?;
    let mut annotated_axioms = annotated_axiom_states(&columns, validated.root_count, selection)?;
    let mut observed_axiom_roots = BTreeSet::new();
    let mut builder = NamedHierarchyBuilder::with_policy(unsupported);
    let mut transaction = NamedHierarchyBuilder::transaction();
    let mut selected_roots = EncodedRootCursor::new(selection);
    for root in 0..validated.root_count {
        if !selected_roots.includes(root)? {
            continue;
        }
        let kind = byte_at(columns.root_kinds, root, "root kind")?;
        if kind == ROOT_ONTOLOGY_ANNOTATION {
            continue;
        }
        let identifier = u32_at(columns.root_ids, root, "root ID")?;
        let node = node_index(identifier, validated.node_count)?;
        let tag = u16_at(columns.node_tags, node, "root node tag")?;
        if kind == ROOT_EXTENSION {
            if tag == 148 {
                builder.handle_unsupported(46, "SWRL_RULE")?;
                continue;
            }
            return Err(CoreError::internal(
                "validated extension root has an unexpected constructor tag",
            ));
        }
        if kind != ROOT_AXIOM {
            return Err(CoreError::internal(
                "validated encoded root has an unexpected category",
            ));
        }
        if !observed_axiom_roots.insert(identifier) {
            continue;
        }
        if !(120..=123).contains(&tag) && annotation_count(node, &columns)? != 0 {
            let key = stripped_axiom_key(node, &columns)?;
            let state = annotated_axioms.get_mut(&key).ok_or_else(|| {
                CoreError::internal("annotated encoded axiom lost its deduplication state")
            })?;
            if state.has_unannotated || state.compiled {
                continue;
            }
            state.compiled = true;
        }
        compile_axiom_node(tag, node, &columns, &mut builder, &mut transaction)?;
    }
    builder.freeze([0; 32])
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum AxiomProjectionRule {
    Declaration,
    SubClass,
    EquivalentClasses,
    DisjointClasses,
    DisjointUnion,
    SubObjectProperty,
    EquivalentObjectProperties,
    ObjectPropertyDomain,
    ObjectPropertyRange,
    ReflexiveObjectProperty,
    TransitiveObjectProperty,
    SameIndividual,
    DifferentIndividuals,
    ClassAssertion,
    ObjectPropertyAssertion,
    IgnoreNonlogical,
    Unsupported(usize, &'static str),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct AxiomProjectionDispatch {
    tag: u16,
    rule: AxiomProjectionRule,
}

const AXIOM_PROJECTION_RULES: [AxiomProjectionDispatch; 37] = [
    AxiomProjectionDispatch {
        tag: 60,
        rule: AxiomProjectionRule::Declaration,
    },
    AxiomProjectionDispatch {
        tag: 61,
        rule: AxiomProjectionRule::SubClass,
    },
    AxiomProjectionDispatch {
        tag: 62,
        rule: AxiomProjectionRule::EquivalentClasses,
    },
    AxiomProjectionDispatch {
        tag: 63,
        rule: AxiomProjectionRule::DisjointClasses,
    },
    AxiomProjectionDispatch {
        tag: 64,
        rule: AxiomProjectionRule::DisjointUnion,
    },
    AxiomProjectionDispatch {
        tag: 70,
        rule: AxiomProjectionRule::SubObjectProperty,
    },
    AxiomProjectionDispatch {
        tag: 71,
        rule: AxiomProjectionRule::EquivalentObjectProperties,
    },
    AxiomProjectionDispatch {
        tag: 72,
        rule: AxiomProjectionRule::Unsupported(18, "DISJOINT_OBJECT_PROPERTIES"),
    },
    AxiomProjectionDispatch {
        tag: 73,
        rule: AxiomProjectionRule::Unsupported(25, "INVERSE_OBJECT_PROPERTIES"),
    },
    AxiomProjectionDispatch {
        tag: 74,
        rule: AxiomProjectionRule::ObjectPropertyDomain,
    },
    AxiomProjectionDispatch {
        tag: 75,
        rule: AxiomProjectionRule::ObjectPropertyRange,
    },
    AxiomProjectionDispatch {
        tag: 76,
        rule: AxiomProjectionRule::Unsupported(22, "FUNCTIONAL_OBJECT_PROPERTY"),
    },
    AxiomProjectionDispatch {
        tag: 77,
        rule: AxiomProjectionRule::Unsupported(24, "INVERSE_FUNCTIONAL_OBJECT_PROPERTY"),
    },
    AxiomProjectionDispatch {
        tag: 78,
        rule: AxiomProjectionRule::ReflexiveObjectProperty,
    },
    AxiomProjectionDispatch {
        tag: 79,
        rule: AxiomProjectionRule::Unsupported(26, "IRREFLEXIVE_OBJECT_PROPERTY"),
    },
    AxiomProjectionDispatch {
        tag: 80,
        rule: AxiomProjectionRule::Unsupported(47, "SYMMETRIC_OBJECT_PROPERTY"),
    },
    AxiomProjectionDispatch {
        tag: 81,
        rule: AxiomProjectionRule::Unsupported(1, "ASYMMETRIC_OBJECT_PROPERTY"),
    },
    AxiomProjectionDispatch {
        tag: 82,
        rule: AxiomProjectionRule::TransitiveObjectProperty,
    },
    AxiomProjectionDispatch {
        tag: 90,
        rule: AxiomProjectionRule::Unsupported(45, "SUB_DATA_PROPERTY_OF"),
    },
    AxiomProjectionDispatch {
        tag: 91,
        rule: AxiomProjectionRule::Unsupported(20, "EQUIVALENT_DATA_PROPERTIES"),
    },
    AxiomProjectionDispatch {
        tag: 92,
        rule: AxiomProjectionRule::Unsupported(17, "DISJOINT_DATA_PROPERTIES"),
    },
    AxiomProjectionDispatch {
        tag: 93,
        rule: AxiomProjectionRule::Unsupported(10, "DATA_PROPERTY_DOMAIN"),
    },
    AxiomProjectionDispatch {
        tag: 94,
        rule: AxiomProjectionRule::Unsupported(11, "DATA_PROPERTY_RANGE"),
    },
    AxiomProjectionDispatch {
        tag: 95,
        rule: AxiomProjectionRule::Unsupported(21, "FUNCTIONAL_DATA_PROPERTY"),
    },
    AxiomProjectionDispatch {
        tag: 100,
        rule: AxiomProjectionRule::Unsupported(14, "DATATYPE_DEFINITION"),
    },
    AxiomProjectionDispatch {
        tag: 101,
        rule: AxiomProjectionRule::Unsupported(23, "HAS_KEY"),
    },
    AxiomProjectionDispatch {
        tag: 110,
        rule: AxiomProjectionRule::SameIndividual,
    },
    AxiomProjectionDispatch {
        tag: 111,
        rule: AxiomProjectionRule::DifferentIndividuals,
    },
    AxiomProjectionDispatch {
        tag: 112,
        rule: AxiomProjectionRule::ClassAssertion,
    },
    AxiomProjectionDispatch {
        tag: 113,
        rule: AxiomProjectionRule::ObjectPropertyAssertion,
    },
    AxiomProjectionDispatch {
        tag: 114,
        rule: AxiomProjectionRule::Unsupported(28, "NEGATIVE_OBJECT_PROPERTY_ASSERTION"),
    },
    AxiomProjectionDispatch {
        tag: 115,
        rule: AxiomProjectionRule::Unsupported(9, "DATA_PROPERTY_ASSERTION"),
    },
    AxiomProjectionDispatch {
        tag: 116,
        rule: AxiomProjectionRule::Unsupported(27, "NEGATIVE_DATA_PROPERTY_ASSERTION"),
    },
    AxiomProjectionDispatch {
        tag: 120,
        rule: AxiomProjectionRule::IgnoreNonlogical,
    },
    AxiomProjectionDispatch {
        tag: 121,
        rule: AxiomProjectionRule::IgnoreNonlogical,
    },
    AxiomProjectionDispatch {
        tag: 122,
        rule: AxiomProjectionRule::IgnoreNonlogical,
    },
    AxiomProjectionDispatch {
        tag: 123,
        rule: AxiomProjectionRule::IgnoreNonlogical,
    },
];

fn axiom_projection_rule(tag: u16) -> Option<AxiomProjectionRule> {
    AXIOM_PROJECTION_RULES
        .binary_search_by_key(&tag, |dispatch| dispatch.tag)
        .ok()
        .map(|index| AXIOM_PROJECTION_RULES[index].rule)
}

fn compile_axiom_node<B: ByteSource>(
    tag: u16,
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
    transaction: &mut NamedHierarchyBuilder,
) -> CoreResult<()> {
    let result = match axiom_projection_rule(tag) {
        Some(AxiomProjectionRule::Declaration) => compile_declaration(node, columns, transaction),
        Some(AxiomProjectionRule::SubClass) => compile_named_subclass(node, columns, transaction),
        Some(AxiomProjectionRule::EquivalentClasses) => {
            compile_named_equivalence(node, columns, transaction)
        }
        Some(AxiomProjectionRule::DisjointClasses) => {
            compile_disjoint_named_classes(node, columns, transaction)
        }
        Some(AxiomProjectionRule::DisjointUnion) => {
            compile_named_disjoint_union(node, columns, transaction)
        }
        Some(AxiomProjectionRule::SubObjectProperty) => {
            compile_named_subproperty(node, columns, transaction)
        }
        Some(AxiomProjectionRule::EquivalentObjectProperties) => {
            compile_equivalent_named_properties(node, columns, transaction)
        }
        Some(AxiomProjectionRule::ObjectPropertyDomain) => {
            compile_named_property_domain(node, columns, transaction)
        }
        Some(AxiomProjectionRule::ObjectPropertyRange) => {
            compile_named_property_range(node, columns, transaction)
        }
        Some(AxiomProjectionRule::ReflexiveObjectProperty) => {
            compile_reflexive_named_property(node, columns, transaction)
        }
        Some(AxiomProjectionRule::TransitiveObjectProperty) => {
            compile_transitive_named_property(node, columns, transaction)
        }
        Some(AxiomProjectionRule::SameIndividual) => {
            compile_same_named_individuals(node, columns, transaction)
        }
        Some(AxiomProjectionRule::DifferentIndividuals) => {
            compile_different_named_individuals(node, columns, transaction)
        }
        Some(AxiomProjectionRule::ClassAssertion) => {
            compile_named_class_assertion(node, columns, transaction)
        }
        Some(AxiomProjectionRule::ObjectPropertyAssertion) => {
            compile_named_object_property_assertion(node, columns, transaction)
        }
        Some(AxiomProjectionRule::IgnoreNonlogical) => Ok(()),
        Some(AxiomProjectionRule::Unsupported(feature, name)) => {
            Err(AxiomCompileError::unsupported(feature, name))
        }
        None => Err(AxiomCompileError::Core(CoreError::invalid(format!(
            "encoded named-hierarchy compiler does not support axiom tag {tag}"
        )))),
    };
    match result {
        Ok(()) => builder.commit_axiom(transaction),
        Err(AxiomCompileError::Unsupported { feature, name }) => {
            transaction.reset_axiom();
            builder.handle_unsupported(feature, name)
        }
        Err(AxiomCompileError::Core(error)) => Err(error),
    }
}

fn annotated_axiom_states<B: ByteSource, P: ByteSource>(
    columns: &EncodedColumns<B>,
    root_count: usize,
    selection: Option<EncodedRootSelection<P>>,
) -> CoreResult<BTreeMap<Vec<u64>, AnnotatedAxiomState>> {
    let node_count = aligned_count(columns.node_tags, 2, "node_tags")?;
    let mut states = BTreeMap::new();
    let mut selected_roots = EncodedRootCursor::new(selection);
    for root in 0..root_count {
        if !selected_roots.includes(root)? {
            continue;
        }
        if byte_at(columns.root_kinds, root, "root kind")? != ROOT_AXIOM {
            continue;
        }
        let identifier = u32_at(columns.root_ids, root, "root ID")?;
        let node = node_index(identifier, node_count)?;
        let tag = u16_at(columns.node_tags, node, "root node tag")?;
        if (120..=123).contains(&tag) || annotation_count(node, columns)? == 0 {
            continue;
        }
        states
            .entry(stripped_axiom_key(node, columns)?)
            .or_insert_with(AnnotatedAxiomState::default);
    }
    if states.is_empty() {
        return Ok(states);
    }
    let mut selected_roots = EncodedRootCursor::new(selection);
    for root in 0..root_count {
        if !selected_roots.includes(root)? {
            continue;
        }
        if byte_at(columns.root_kinds, root, "root kind")? != ROOT_AXIOM {
            continue;
        }
        let identifier = u32_at(columns.root_ids, root, "root ID")?;
        let node = node_index(identifier, node_count)?;
        let tag = u16_at(columns.node_tags, node, "root node tag")?;
        if (120..=123).contains(&tag) || annotation_count(node, columns)? != 0 {
            continue;
        }
        if let Some(state) = states.get_mut(&stripped_axiom_key(node, columns)?) {
            state.has_unannotated = true;
        }
    }
    Ok(states)
}

fn stripped_axiom_key<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
) -> CoreResult<Vec<u64>> {
    let start = usize_at(columns.node_field_offsets, node, "node field offset")?;
    let annotation = annotation_field(node, columns)?;
    let field_count = annotation
        .checked_sub(start)
        .ok_or_else(|| CoreError::internal("encoded axiom field range is reversed"))?;
    let base_words = field_count
        .checked_mul(3)
        .and_then(|count| count.checked_add(2))
        .ok_or_else(|| CoreError::capacity("encoded axiom key size overflow"))?;
    let mut key = Vec::new();
    key.try_reserve_exact(base_words)
        .map_err(|_| CoreError::capacity("encoded axiom key allocation failed"))?;
    key.push(u64::from(u16_at(
        columns.node_tags,
        node,
        "axiom node tag",
    )?));
    key.push(
        u64::try_from(field_count)
            .map_err(|_| CoreError::capacity("encoded axiom field count exceeds u64"))?,
    );
    for field in start..annotation {
        let kind = byte_at(columns.field_kinds, field, "axiom field kind")?;
        let value = u64_at(columns.field_values, field, "axiom field value")?;
        let length = usize_at(columns.field_lengths, field, "axiom field length")?;
        key.push(u64::from(kind));
        key.push(
            u64::try_from(length)
                .map_err(|_| CoreError::capacity("encoded axiom field length exceeds u64"))?,
        );
        match kind {
            COMPONENT_NODE => key.push(value),
            COMPONENT_SET | COMPONENT_SEQUENCE => {
                let item_start = usize::try_from(value)
                    .map_err(|_| CoreError::capacity("encoded axiom item offset exceeds usize"))?;
                let item_end = item_start
                    .checked_add(length)
                    .ok_or_else(|| CoreError::capacity("encoded axiom item range overflow"))?;
                let item_words = length
                    .checked_mul(3)
                    .ok_or_else(|| CoreError::capacity("encoded axiom item key size overflow"))?;
                key.try_reserve(item_words)
                    .map_err(|_| CoreError::capacity("encoded axiom item key allocation failed"))?;
                for item in item_start..item_end {
                    let item_kind = byte_at(columns.item_kinds, item, "axiom item kind")?;
                    if item_kind != COMPONENT_NODE {
                        return Err(CoreError::internal(
                            "encoded logical axiom collection contains a scalar item",
                        ));
                    }
                    key.push(u64::from(item_kind));
                    key.push(u64_at(columns.item_values, item, "axiom item value")?);
                    key.push(u64_at(columns.item_lengths, item, "axiom item length")?);
                }
            }
            _ => {
                return Err(CoreError::internal(
                    "encoded logical axiom contains a scalar field",
                ));
            }
        }
    }
    Ok(key)
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
enum CompilerExpression {
    Named(Entity),
    Intersection(usize, usize),
    Existential(Entity, usize),
    HasSelf(Entity),
    DataHasValue(Entity, Vec<u8>),
    Complement(usize),
    Union(Vec<usize>),
}

#[derive(Clone, Debug)]
enum ExpressionTask {
    Visit {
        identifier: u32,
        negative: bool,
        positive: bool,
    },
    Finish {
        operation: ExpressionFinish,
        child_count: usize,
        negative: bool,
        positive: bool,
    },
}

#[derive(Clone, Debug)]
enum ExpressionFinish {
    Intersection,
    Union,
    Complement,
    Existential(Entity),
}

struct NamedHierarchyBuilder {
    entities: BTreeSet<Entity>,
    expressions: Vec<CompilerExpression>,
    expression_ids: BTreeMap<CompilerExpression, usize>,
    expression_occurrences: Vec<Occurrence>,
    property_occurrences: BTreeMap<Entity, Occurrence>,
    property_chains: BTreeSet<Vec<Entity>>,
    subclass_axioms: BTreeSet<(usize, usize)>,
    equivalent_class_axioms: BTreeSet<(usize, usize)>,
    disjoint_groups: BTreeSet<Vec<usize>>,
    subproperty_axioms: BTreeSet<(Vec<Entity>, Entity)>,
    property_ranges: BTreeSet<(Entity, usize)>,
    feature_counts: Vec<u64>,
    compatibility_observations: BTreeSet<Vec<u8>>,
    unsupported: EncodedUnsupportedPolicy,
}

impl Default for NamedHierarchyBuilder {
    fn default() -> Self {
        Self {
            entities: BTreeSet::new(),
            expressions: Vec::new(),
            expression_ids: BTreeMap::new(),
            expression_occurrences: Vec::new(),
            property_occurrences: BTreeMap::new(),
            property_chains: BTreeSet::new(),
            subclass_axioms: BTreeSet::new(),
            equivalent_class_axioms: BTreeSet::new(),
            disjoint_groups: BTreeSet::new(),
            subproperty_axioms: BTreeSet::new(),
            property_ranges: BTreeSet::new(),
            feature_counts: vec![0; FEATURE_VECTOR_LENGTH],
            compatibility_observations: BTreeSet::new(),
            unsupported: EncodedUnsupportedPolicy::Error,
        }
    }
}

impl NamedHierarchyBuilder {
    fn with_policy(unsupported: EncodedUnsupportedPolicy) -> Self {
        let mut builder = Self {
            unsupported,
            ..Self::default()
        };
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

    fn transaction() -> Self {
        Self::default()
    }

    fn reset_axiom(&mut self) {
        self.entities.clear();
        self.expressions.clear();
        self.expression_ids.clear();
        self.expression_occurrences.clear();
        self.property_occurrences.clear();
        self.property_chains.clear();
        self.subclass_axioms.clear();
        self.equivalent_class_axioms.clear();
        self.disjoint_groups.clear();
        self.subproperty_axioms.clear();
        self.property_ranges.clear();
        self.feature_counts.fill(0);
        self.compatibility_observations.clear();
    }

    fn commit_axiom(&mut self, transaction: &mut Self) -> CoreResult<()> {
        if transaction.expressions.len() != transaction.expression_occurrences.len() {
            return Err(CoreError::internal(
                "encoded axiom transaction expression ledgers diverged",
            ));
        }
        self.entities.extend(transaction.entities.iter().cloned());

        let mut handles = Vec::new();
        handles
            .try_reserve_exact(transaction.expressions.len())
            .map_err(|_| CoreError::capacity("encoded axiom handle allocation failed"))?;
        for (expression, occurrence) in transaction
            .expressions
            .iter()
            .zip(&transaction.expression_occurrences)
        {
            let expression = expression.remap_dependencies(&handles)?;
            let handle = self.intern_expression(expression, false, false)?;
            merge_occurrence(&mut self.expression_occurrences[handle], *occurrence)?;
            handles.push(handle);
        }

        for (property, occurrence) in &transaction.property_occurrences {
            let target = self
                .property_occurrences
                .entry(property.clone())
                .or_default();
            merge_occurrence(target, *occurrence)?;
        }
        self.property_chains
            .extend(transaction.property_chains.iter().cloned());
        for (sub, super_) in &transaction.subclass_axioms {
            self.subclass_axioms.insert((
                remapped_axiom_handle(&handles, *sub)?,
                remapped_axiom_handle(&handles, *super_)?,
            ));
        }
        for (first, second) in &transaction.equivalent_class_axioms {
            self.equivalent_class_axioms.insert((
                remapped_axiom_handle(&handles, *first)?,
                remapped_axiom_handle(&handles, *second)?,
            ));
        }
        for group in &transaction.disjoint_groups {
            self.disjoint_groups.insert(
                group
                    .iter()
                    .map(|member| remapped_axiom_handle(&handles, *member))
                    .collect::<CoreResult<Vec<_>>>()?,
            );
        }
        self.subproperty_axioms
            .extend(transaction.subproperty_axioms.iter().cloned());
        for (property, range) in &transaction.property_ranges {
            self.property_ranges
                .insert((property.clone(), remapped_axiom_handle(&handles, *range)?));
        }
        for (index, count) in transaction.feature_counts.iter().copied().enumerate() {
            if count != 0 {
                self.add_feature(index, count)?;
            }
        }
        self.compatibility_observations
            .extend(transaction.compatibility_observations.iter().cloned());
        transaction.reset_axiom();
        Ok(())
    }

    fn add_declaration(&mut self, entity: Entity) -> AxiomCompileResult<()> {
        match entity.kind {
            EntityKind::Class | EntityKind::NamedIndividual | EntityKind::ObjectProperty => {
                self.entities.insert(entity);
                Ok(())
            }
            EntityKind::AnnotationProperty => Ok(()),
            EntityKind::DataProperty => Err(AxiomCompileError::unsupported(8, "DATA_PROPERTY")),
            EntityKind::Datatype => Err(AxiomCompileError::unsupported(13, "DATATYPE")),
        }
    }

    fn intern_expression(
        &mut self,
        expression: CompilerExpression,
        negative: bool,
        positive: bool,
    ) -> CoreResult<usize> {
        match &expression {
            CompilerExpression::Named(entity) => {
                self.entities.insert(entity.clone());
            }
            CompilerExpression::Existential(property, filler) => {
                self.entities.insert(property.clone());
                if *filler >= self.expressions.len() {
                    return Err(CoreError::internal(
                        "encoded existential references an unknown expression",
                    ));
                }
            }
            CompilerExpression::HasSelf(property) => {
                self.entities.insert(property.clone());
            }
            CompilerExpression::DataHasValue(property, payload) => {
                if property.kind != EntityKind::DataProperty || payload.is_empty() {
                    return Err(CoreError::internal(
                        "encoded data-has-value expression has an invalid structural key",
                    ));
                }
                self.entities.insert(property.clone());
            }
            CompilerExpression::Intersection(first, second) => {
                if *first >= self.expressions.len() || *second >= self.expressions.len() {
                    return Err(CoreError::internal(
                        "encoded intersection references an unknown expression",
                    ));
                }
            }
            CompilerExpression::Complement(operand) => {
                if *operand >= self.expressions.len() {
                    return Err(CoreError::internal(
                        "encoded complement references an unknown expression",
                    ));
                }
            }
            CompilerExpression::Union(operands) => {
                if operands
                    .iter()
                    .any(|operand| *operand >= self.expressions.len())
                {
                    return Err(CoreError::internal(
                        "encoded union references an unknown expression",
                    ));
                }
            }
        }
        let handle = if let Some(handle) = self.expression_ids.get(&expression).copied() {
            handle
        } else {
            let handle = self.expressions.len();
            self.expressions.push(expression.clone());
            self.expression_ids.insert(expression, handle);
            self.expression_occurrences.push(Occurrence::default());
            handle
        };
        let occurrence = &mut self.expression_occurrences[handle];
        if negative {
            increment_occurrence(occurrence, false)?;
        }
        if positive {
            increment_occurrence(occurrence, true)?;
        }
        Ok(handle)
    }

    fn add_equivalent_expressions(&mut self, members: Vec<usize>) -> CoreResult<()> {
        let Some(first) = members.first().copied() else {
            return Ok(());
        };
        let first_is_class = matches!(
            self.expressions.get(first),
            Some(CompilerExpression::Named(Entity {
                kind: EntityKind::Class,
                ..
            }))
        );
        for member in members.into_iter().skip(1) {
            let member_is_class = matches!(
                self.expressions.get(member),
                Some(CompilerExpression::Named(Entity {
                    kind: EntityKind::Class,
                    ..
                }))
            );
            self.equivalent_class_axioms
                .insert(if !first_is_class && member_is_class {
                    (member, first)
                } else {
                    (first, member)
                });
        }
        Ok(())
    }

    fn add_same_individuals(&mut self, members: Vec<Entity>) -> CoreResult<()> {
        let Some(first) = members.first() else {
            return Ok(());
        };
        let first = self.add_named_occurrence(first, true, true)?;
        for member in members.iter().skip(1) {
            let member = self.add_named_occurrence(member, true, true)?;
            self.subclass_axioms.insert((first, member));
            self.subclass_axioms.insert((member, first));
        }
        Ok(())
    }

    fn add_disjoint_expressions(
        &mut self,
        members: Vec<usize>,
        feature: Option<usize>,
    ) -> CoreResult<()> {
        if let Some(index) = feature {
            self.add_feature(index, 1)?;
        }
        if members.len() > 2 {
            self.disjoint_groups.insert(members);
            return Ok(());
        }

        let bottom = Entity {
            kind: EntityKind::Class,
            iri: OWL_NOTHING_IRI.to_owned(),
        };
        let bottom = self.add_named_occurrence(&bottom, false, true)?;
        self.add_feature(FEATURE_OWL_NOTHING_POSITIVE, 1)?;
        for (first_position, first) in members.iter().copied().enumerate() {
            for second in members.iter().copied().skip(first_position + 1) {
                let intersection = self.intern_expression(
                    CompilerExpression::Intersection(first, second),
                    true,
                    false,
                )?;
                self.subclass_axioms.insert((intersection, bottom));
            }
        }
        Ok(())
    }

    fn add_named_occurrence(
        &mut self,
        entity: &Entity,
        negative: bool,
        positive: bool,
    ) -> CoreResult<usize> {
        self.intern_expression(
            CompilerExpression::Named(entity.clone()),
            negative,
            positive,
        )
    }

    fn add_subproperty(&mut self, chain: Vec<Entity>, super_: Entity) -> CoreResult<()> {
        for property in &chain {
            self.add_property_occurrence(property, true, false)?;
        }
        self.add_property_occurrence(&super_, false, true)?;
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
            self.add_property_occurrence(member, true, true)?;
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
        self.add_property_occurrence(&property, true, true)?;
        self.add_feature(FEATURE_OBJECT_PROPERTY_CHAIN, 1)?;
        self.insert_subproperty_rule(vec![property.clone(), property.clone()], property);
        Ok(())
    }

    fn add_reflexive_property(&mut self, property: Entity) -> CoreResult<()> {
        let thing = Entity {
            kind: EntityKind::Class,
            iri: OWL_THING_IRI.to_owned(),
        };
        self.add_feature(FEATURE_REFLEXIVE_OBJECT_PROPERTY, 1)?;
        let thing = self.add_named_occurrence(&thing, true, false)?;
        self.add_property_occurrence(&property, false, true)?;
        let has_self =
            self.intern_expression(CompilerExpression::HasSelf(property), false, true)?;
        self.subclass_axioms.insert((thing, has_self));
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
        if positive && property.iri == OWL_BOTTOM_OBJECT_PROPERTY_IRI {
            self.add_feature(FEATURE_BOTTOM_OBJECT_PROPERTY_POSITIVE, 1)?;
        }
        if negative && property.iri == OWL_TOP_OBJECT_PROPERTY_IRI {
            self.add_feature(FEATURE_TOP_OBJECT_PROPERTY_NEGATIVE, 1)?;
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

    fn handle_unsupported(&mut self, feature: usize, name: &'static str) -> CoreResult<()> {
        match self.unsupported {
            EncodedUnsupportedPolicy::Ignore => self.add_feature(feature, 1),
            EncodedUnsupportedPolicy::Error => Err(CoreError::unsupported(name)),
        }
    }

    fn freeze(mut self, source_fingerprint: [u8; 32]) -> CoreResult<EncodedCompilation> {
        self.ensure_named_expressions()?;
        let entities = self.entities.iter().cloned().collect::<Vec<_>>();
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
        let (expressions, expression_occurrences, expression_ids) =
            self.freeze_expressions(&entity_ids)?;

        let object_properties = entities
            .iter()
            .filter(|entity| entity.kind == EntityKind::ObjectProperty)
            .cloned()
            .collect::<Vec<_>>();
        let subclass_axioms = self
            .subclass_axioms
            .into_iter()
            .map(|(sub, super_)| (expression_ids[sub], expression_ids[super_]))
            .collect::<BTreeSet<_>>();
        let equivalent_class_axioms = self
            .equivalent_class_axioms
            .into_iter()
            .map(|(first, second)| (expression_ids[first], expression_ids[second]))
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        let disjoint_groups = self
            .disjoint_groups
            .into_iter()
            .map(|group| {
                group
                    .into_iter()
                    .map(|member| expression_ids[member])
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
            .map(|(property, range)| (entity_ids[&property], expression_ids[range]))
            .collect();
        let compatibility_observations = std::mem::take(&mut self.compatibility_observations)
            .into_iter()
            .collect();

        Ok(EncodedCompilation {
            ontology: Ontology {
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
            },
            compatibility_observations,
        })
    }

    fn ensure_named_expressions(&mut self) -> CoreResult<()> {
        for entity in self.entities.clone() {
            if matches!(entity.kind, EntityKind::Class | EntityKind::NamedIndividual) {
                self.intern_expression(CompilerExpression::Named(entity), false, false)?;
            }
        }
        Ok(())
    }

    fn freeze_expressions(
        &self,
        entity_ids: &BTreeMap<Entity, u32>,
    ) -> CoreResult<(Vec<Expression>, Vec<Occurrence>, Vec<u32>)> {
        let count = self.expressions.len();
        if count >= u32::MAX as usize {
            return Err(CoreError::capacity(
                "encoded compiler expression table reaches the reserved u32 ID",
            ));
        }
        let mut dependents = vec![Vec::<usize>::new(); count];
        let mut remaining = vec![0_usize; count];
        let mut final_ids = vec![u32::MAX; count];
        let mut available = BinaryHeap::<Reverse<((u8, Vec<u8>, Vec<u32>), usize)>>::new();
        for (handle, expression) in self.expressions.iter().enumerate() {
            let dependencies = expression.dependencies()?;
            remaining[handle] = dependencies.len();
            for dependency in dependencies {
                if dependency >= count {
                    return Err(CoreError::internal(
                        "encoded expression dependency is out of bounds",
                    ));
                }
                dependents[dependency].push(handle);
            }
            if remaining[handle] == 0 {
                available.push(Reverse((
                    (
                        expression.tag()? as u8,
                        expression.payload(),
                        expression.rewritten_arguments(entity_ids, &final_ids)?,
                    ),
                    handle,
                )));
            }
        }

        let mut expressions = Vec::new();
        let mut occurrences = Vec::new();
        while let Some(Reverse((_order, handle))) = available.pop() {
            if final_ids[handle] != u32::MAX {
                continue;
            }
            let expression = &self.expressions[handle];
            let identifier = u32::try_from(expressions.len())
                .map_err(|_| CoreError::capacity("encoded compiler expression ID exceeds u32"))?;
            let arguments = expression.rewritten_arguments(entity_ids, &final_ids)?;
            final_ids[handle] = identifier;
            expressions.push(Expression {
                tag: expression.tag()?,
                payload: expression.payload(),
                arguments,
            });
            occurrences.push(self.expression_occurrences[handle]);
            for dependent in &dependents[handle] {
                remaining[*dependent] = remaining[*dependent]
                    .checked_sub(1)
                    .ok_or_else(|| CoreError::internal("encoded dependency count underflow"))?;
                if remaining[*dependent] == 0 {
                    let dependent_expression = &self.expressions[*dependent];
                    available.push(Reverse((
                        (
                            dependent_expression.tag()? as u8,
                            dependent_expression.payload(),
                            dependent_expression.rewritten_arguments(entity_ids, &final_ids)?,
                        ),
                        *dependent,
                    )));
                }
            }
        }
        if expressions.len() != count {
            return Err(CoreError::internal(
                "encoded temporary expression graph is cyclic",
            ));
        }
        Ok((expressions, occurrences, final_ids))
    }
}

impl CompilerExpression {
    fn remap_dependencies(&self, handles: &[usize]) -> CoreResult<Self> {
        let remap = |handle: usize| {
            handles.get(handle).copied().ok_or_else(|| {
                CoreError::internal("encoded axiom expression dependency is not committed")
            })
        };
        match self {
            Self::Named(entity) => Ok(Self::Named(entity.clone())),
            Self::Intersection(first, second) => {
                Ok(Self::Intersection(remap(*first)?, remap(*second)?))
            }
            Self::Existential(property, filler) => {
                Ok(Self::Existential(property.clone(), remap(*filler)?))
            }
            Self::HasSelf(property) => Ok(Self::HasSelf(property.clone())),
            Self::DataHasValue(property, payload) => {
                Ok(Self::DataHasValue(property.clone(), payload.clone()))
            }
            Self::Complement(operand) => Ok(Self::Complement(remap(*operand)?)),
            Self::Union(operands) => Ok(Self::Union(
                operands
                    .iter()
                    .map(|operand| remap(*operand))
                    .collect::<CoreResult<Vec<_>>>()?,
            )),
        }
    }

    fn dependencies(&self) -> CoreResult<BTreeSet<usize>> {
        Ok(match self {
            Self::Named(_) | Self::HasSelf(_) | Self::DataHasValue(_, _) => BTreeSet::new(),
            Self::Existential(_, filler) => BTreeSet::from([*filler]),
            Self::Intersection(first, second) => BTreeSet::from([*first, *second]),
            Self::Complement(operand) => BTreeSet::from([*operand]),
            Self::Union(operands) => operands.iter().copied().collect(),
        })
    }

    fn tag(&self) -> CoreResult<ExpressionTag> {
        match self {
            Self::Named(entity) => match entity.kind {
                EntityKind::Class => Ok(ExpressionTag::Class),
                EntityKind::NamedIndividual => Ok(ExpressionTag::Individual),
                _ => Err(CoreError::internal(
                    "encoded named expression has a non-expression entity kind",
                )),
            },
            Self::Intersection(_, _) => Ok(ExpressionTag::ObjectIntersectionOf),
            Self::Existential(_, _) => Ok(ExpressionTag::ObjectSomeValuesFrom),
            Self::HasSelf(_) => Ok(ExpressionTag::ObjectHasSelf),
            Self::DataHasValue(_, _) => Ok(ExpressionTag::DataHasValue),
            Self::Complement(_) => Ok(ExpressionTag::ObjectComplementOf),
            Self::Union(_) => Ok(ExpressionTag::ObjectUnionOf),
        }
    }

    fn rewritten_arguments(
        &self,
        entity_ids: &BTreeMap<Entity, u32>,
        expression_ids: &[u32],
    ) -> CoreResult<Vec<u32>> {
        let entity = |value: &Entity| {
            entity_ids
                .get(value)
                .copied()
                .ok_or_else(|| CoreError::internal("encoded expression entity is not interned"))
        };
        let expression = |handle: usize| {
            expression_ids
                .get(handle)
                .copied()
                .filter(|identifier| *identifier != u32::MAX)
                .ok_or_else(|| {
                    CoreError::internal("encoded expression dependency is not finalized")
                })
        };
        match self {
            Self::Named(value) | Self::HasSelf(value) | Self::DataHasValue(value, _) => {
                Ok(vec![entity(value)?])
            }
            Self::Existential(property, filler) => {
                Ok(vec![entity(property)?, expression(*filler)?])
            }
            Self::Intersection(first, second) => {
                Ok(vec![expression(*first)?, expression(*second)?])
            }
            Self::Complement(operand) => Ok(vec![expression(*operand)?]),
            Self::Union(operands) => operands
                .iter()
                .map(|operand| expression(*operand))
                .collect(),
        }
    }

    fn payload(&self) -> Vec<u8> {
        match self {
            Self::DataHasValue(_, payload) => payload.clone(),
            _ => Vec::new(),
        }
    }
}

fn compile_declaration<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> AxiomCompileResult<()> {
    accept_annotations(node, 1, columns)?;
    builder.add_declaration(decode_entity(node_field(node, 0, columns)?, columns)?)
}

fn compile_named_subclass<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> AxiomCompileResult<()> {
    accept_annotations(node, 2, columns)?;
    let sub =
        decode_class_expression(node_field(node, 0, columns)?, true, false, columns, builder)?;
    let super_ =
        decode_class_expression(node_field(node, 1, columns)?, false, true, columns, builder)?;
    builder.subclass_axioms.insert((sub, super_));
    Ok(())
}

fn compile_named_equivalence<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> AxiomCompileResult<()> {
    accept_annotations(node, 1, columns)?;
    let identifiers = node_collection(node, 0, columns)?;
    let mut members = Vec::new();
    members
        .try_reserve_exact(identifiers.len())
        .map_err(|_| CoreError::capacity("encoded equivalent-class allocation failed"))?;
    for identifier in identifiers {
        members.push(decode_class_expression(
            identifier, true, true, columns, builder,
        )?);
    }
    Ok(builder.add_equivalent_expressions(members)?)
}

fn compile_disjoint_named_classes<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> AxiomCompileResult<()> {
    accept_annotations(node, 1, columns)?;
    let identifiers = node_collection(node, 0, columns)?;
    let mut members = Vec::new();
    members
        .try_reserve_exact(identifiers.len())
        .map_err(|_| CoreError::capacity("encoded disjoint-class allocation failed"))?;
    for identifier in &identifiers {
        members.push(decode_class_expression(
            *identifier,
            true,
            false,
            columns,
            builder,
        )?);
    }
    // ELK's binary disjoint loop visits the right operand once as a pair member and once
    // again as the second outer-loop member. Replay the complete conversion so nested
    // occurrence and feature ledgers remain byte-for-byte identical to the scalar path.
    if let [_, second] = members.as_slice() {
        let replayed = decode_class_expression(identifiers[1], true, false, columns, builder)?;
        debug_assert_eq!(*second, replayed);
    }
    Ok(builder.add_disjoint_expressions(members, Some(FEATURE_DISJOINT_CLASSES))?)
}

fn compile_named_disjoint_union<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> AxiomCompileResult<()> {
    accept_annotations(node, 2, columns)?;
    let defined_identifier = node_field(node, 0, columns)?;
    let member_identifiers = node_collection(node, 1, columns)?;
    let mut disjoint_members = Vec::new();
    disjoint_members
        .try_reserve_exact(member_identifiers.len())
        .map_err(|_| CoreError::capacity("encoded disjoint-union allocation failed"))?;
    for identifier in &member_identifiers {
        disjoint_members.push(decode_class_expression(
            *identifier,
            true,
            false,
            columns,
            builder,
        )?);
    }
    // Preserve the scalar converter's complete second visit for binary disjointness.
    if let [_, second] = disjoint_members.as_slice() {
        let replayed =
            decode_class_expression(member_identifiers[1], true, false, columns, builder)?;
        debug_assert_eq!(*second, replayed);
    }
    builder.add_disjoint_expressions(disjoint_members, None)?;

    match member_identifiers.as_slice() {
        [] => {
            let defined =
                decode_class_expression(defined_identifier, false, true, columns, builder)?;
            let bottom = Entity {
                kind: EntityKind::Class,
                iri: OWL_NOTHING_IRI.to_owned(),
            };
            let bottom = builder.add_named_occurrence(&bottom, false, true)?;
            builder.add_feature(FEATURE_OWL_NOTHING_POSITIVE, 1)?;
            builder.equivalent_class_axioms.insert((defined, bottom));
        }
        [member] => {
            let defined =
                decode_class_expression(defined_identifier, true, true, columns, builder)?;
            let member = decode_class_expression(*member, true, true, columns, builder)?;
            builder.add_equivalent_expressions(vec![defined, member])?;
        }
        _ => {
            builder.add_feature(FEATURE_DISJOINT_UNION, 1)?;
            let defined =
                decode_class_expression(defined_identifier, false, true, columns, builder)?;
            for member in member_identifiers {
                let member = decode_class_expression(member, true, false, columns, builder)?;
                builder.subclass_axioms.insert((member, defined));
            }
        }
    }
    Ok(())
}

fn compile_named_subproperty<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> AxiomCompileResult<()> {
    accept_annotations(node, 2, columns)?;
    let chain = decode_property_chain_for_axiom(node_field(node, 0, columns)?, columns)?;
    let super_ = decode_object_property_for_axiom(node_field(node, 1, columns)?, columns)?;
    Ok(builder.add_subproperty(chain, super_)?)
}

fn compile_equivalent_named_properties<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> AxiomCompileResult<()> {
    accept_annotations(node, 1, columns)?;
    let identifiers = node_collection(node, 0, columns)?;
    let mut members = Vec::new();
    members
        .try_reserve_exact(identifiers.len())
        .map_err(|_| CoreError::capacity("encoded equivalent-property allocation failed"))?;
    for identifier in identifiers {
        members.push(decode_object_property_for_axiom(identifier, columns)?);
    }
    Ok(builder.add_equivalent_properties(members)?)
}

fn compile_named_property_domain<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> AxiomCompileResult<()> {
    accept_annotations(node, 2, columns)?;
    let property = decode_object_property_for_axiom(node_field(node, 0, columns)?, columns)?;
    builder.add_property_occurrence(&property, true, false)?;
    let thing = Entity {
        kind: EntityKind::Class,
        iri: OWL_THING_IRI.to_owned(),
    };
    let thing = builder.add_named_occurrence(&thing, true, false)?;
    let existential = builder.intern_expression(
        CompilerExpression::Existential(property, thing),
        true,
        false,
    )?;
    let domain =
        decode_class_expression(node_field(node, 1, columns)?, false, true, columns, builder)?;
    builder.subclass_axioms.insert((existential, domain));
    Ok(())
}

fn compile_named_property_range<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> AxiomCompileResult<()> {
    accept_annotations(node, 2, columns)?;
    let property = decode_object_property_for_axiom(node_field(node, 0, columns)?, columns)?;
    builder.add_property_occurrence(&property, true, false)?;
    let range =
        decode_class_expression(node_field(node, 1, columns)?, false, true, columns, builder)?;
    builder.property_ranges.insert((property, range));
    Ok(builder.add_feature(FEATURE_OBJECT_PROPERTY_RANGE, 1)?)
}

fn compile_reflexive_named_property<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> AxiomCompileResult<()> {
    accept_annotations(node, 1, columns)?;
    let property = decode_object_property_for_axiom(node_field(node, 0, columns)?, columns)?;
    Ok(builder.add_reflexive_property(property)?)
}

fn compile_transitive_named_property<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> AxiomCompileResult<()> {
    accept_annotations(node, 1, columns)?;
    let property = decode_object_property_for_axiom(node_field(node, 0, columns)?, columns)?;
    Ok(builder.add_transitive_property(property)?)
}

fn compile_named_class_assertion<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> AxiomCompileResult<()> {
    accept_annotations(node, 2, columns)?;
    let individual = decode_individual_for_axiom(node_field(node, 1, columns)?, columns)?;
    let individual = builder.add_named_occurrence(&individual, true, false)?;
    let class =
        decode_class_expression(node_field(node, 0, columns)?, false, true, columns, builder)?;
    builder.subclass_axioms.insert((individual, class));
    Ok(())
}

fn compile_named_object_property_assertion<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> AxiomCompileResult<()> {
    accept_annotations(node, 3, columns)?;
    let source = decode_individual_for_axiom(node_field(node, 1, columns)?, columns)?;
    let source = builder.add_named_occurrence(&source, true, false)?;
    let property = decode_object_property_for_axiom(node_field(node, 0, columns)?, columns)?;
    builder.add_property_occurrence(&property, false, true)?;
    let target = decode_individual_for_axiom(node_field(node, 2, columns)?, columns)?;
    let target = builder.add_named_occurrence(&target, false, true)?;
    let existential = builder.intern_expression(
        CompilerExpression::Existential(property, target),
        false,
        true,
    )?;
    builder.add_feature(FEATURE_OBJECT_PROPERTY_ASSERTION, 1)?;
    builder.add_feature(FEATURE_OBJECT_HAS_VALUE_POSITIVE, 1)?;
    builder.subclass_axioms.insert((source, existential));
    Ok(())
}

fn compile_same_named_individuals<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> AxiomCompileResult<()> {
    accept_annotations(node, 1, columns)?;
    let identifiers = node_collection(node, 0, columns)?;
    let mut members = Vec::new();
    members
        .try_reserve_exact(identifiers.len())
        .map_err(|_| CoreError::capacity("encoded same-individual allocation failed"))?;
    for identifier in identifiers {
        members.push(decode_individual_for_axiom(identifier, columns)?);
    }
    Ok(builder.add_same_individuals(members)?)
}

fn compile_different_named_individuals<B: ByteSource>(
    node: usize,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> AxiomCompileResult<()> {
    accept_annotations(node, 1, columns)?;
    let identifiers = node_collection(node, 0, columns)?;
    let mut members = Vec::new();
    members
        .try_reserve_exact(identifiers.len())
        .map_err(|_| CoreError::capacity("encoded different-individual allocation failed"))?;
    for identifier in &identifiers {
        let individual = decode_individual_for_axiom(*identifier, columns)?;
        members.push(builder.add_named_occurrence(&individual, true, false)?);
    }
    // Preserve the scalar converter's complete second visit for binary disjointness.
    if let [_, second] = members.as_slice() {
        let individual = decode_individual_for_axiom(identifiers[1], columns)?;
        let replayed = builder.add_named_occurrence(&individual, true, false)?;
        debug_assert_eq!(*second, replayed);
    }
    Ok(builder.add_disjoint_expressions(members, Some(FEATURE_DIFFERENT_INDIVIDUALS))?)
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ClassProjectionRule {
    NamedClass,
    Intersection,
    Union,
    Complement,
    OneOf,
    ObjectSomeValuesFrom,
    ObjectHasValue,
    ObjectHasSelf,
    DataHasValue,
    Unsupported(usize, &'static str),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct ClassProjectionDispatch {
    tag: u16,
    rule: ClassProjectionRule,
}

const CLASS_PROJECTION_RULES: [ClassProjectionDispatch; 19] = [
    ClassProjectionDispatch {
        tag: 2,
        rule: ClassProjectionRule::NamedClass,
    },
    ClassProjectionDispatch {
        tag: 3,
        rule: ClassProjectionRule::Unsupported(
            FEATURE_ANONYMOUS_INDIVIDUAL,
            "ANONYMOUS_INDIVIDUAL",
        ),
    },
    ClassProjectionDispatch {
        tag: 30,
        rule: ClassProjectionRule::Intersection,
    },
    ClassProjectionDispatch {
        tag: 31,
        rule: ClassProjectionRule::Union,
    },
    ClassProjectionDispatch {
        tag: 32,
        rule: ClassProjectionRule::Complement,
    },
    ClassProjectionDispatch {
        tag: 33,
        rule: ClassProjectionRule::OneOf,
    },
    ClassProjectionDispatch {
        tag: 34,
        rule: ClassProjectionRule::ObjectSomeValuesFrom,
    },
    ClassProjectionDispatch {
        tag: 35,
        rule: ClassProjectionRule::Unsupported(
            FEATURE_OBJECT_ALL_VALUES_FROM,
            "OBJECT_ALL_VALUES_FROM",
        ),
    },
    ClassProjectionDispatch {
        tag: 36,
        rule: ClassProjectionRule::ObjectHasValue,
    },
    ClassProjectionDispatch {
        tag: 37,
        rule: ClassProjectionRule::ObjectHasSelf,
    },
    ClassProjectionDispatch {
        tag: 38,
        rule: ClassProjectionRule::Unsupported(
            FEATURE_OBJECT_MIN_CARDINALITY,
            "OBJECT_MIN_CARDINALITY",
        ),
    },
    ClassProjectionDispatch {
        tag: 39,
        rule: ClassProjectionRule::Unsupported(
            FEATURE_OBJECT_MAX_CARDINALITY,
            "OBJECT_MAX_CARDINALITY",
        ),
    },
    ClassProjectionDispatch {
        tag: 40,
        rule: ClassProjectionRule::Unsupported(
            FEATURE_OBJECT_EXACT_CARDINALITY,
            "OBJECT_EXACT_CARDINALITY",
        ),
    },
    ClassProjectionDispatch {
        tag: 41,
        rule: ClassProjectionRule::Unsupported(
            FEATURE_DATA_SOME_VALUES_FROM,
            "DATA_SOME_VALUES_FROM",
        ),
    },
    ClassProjectionDispatch {
        tag: 42,
        rule: ClassProjectionRule::Unsupported(
            FEATURE_DATA_ALL_VALUES_FROM,
            "DATA_ALL_VALUES_FROM",
        ),
    },
    ClassProjectionDispatch {
        tag: 43,
        rule: ClassProjectionRule::DataHasValue,
    },
    ClassProjectionDispatch {
        tag: 44,
        rule: ClassProjectionRule::Unsupported(
            FEATURE_DATA_MIN_CARDINALITY,
            "DATA_MIN_CARDINALITY",
        ),
    },
    ClassProjectionDispatch {
        tag: 45,
        rule: ClassProjectionRule::Unsupported(
            FEATURE_DATA_MAX_CARDINALITY,
            "DATA_MAX_CARDINALITY",
        ),
    },
    ClassProjectionDispatch {
        tag: 46,
        rule: ClassProjectionRule::Unsupported(
            FEATURE_DATA_EXACT_CARDINALITY,
            "DATA_EXACT_CARDINALITY",
        ),
    },
];

fn class_projection_rule(tag: u16) -> Option<ClassProjectionRule> {
    CLASS_PROJECTION_RULES
        .binary_search_by_key(&tag, |dispatch| dispatch.tag)
        .ok()
        .map(|index| CLASS_PROJECTION_RULES[index].rule)
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

fn decode_class_expression<B: ByteSource>(
    identifier: u32,
    negative: bool,
    positive: bool,
    columns: &EncodedColumns<B>,
    builder: &mut NamedHierarchyBuilder,
) -> AxiomCompileResult<usize> {
    let mut tasks = vec![ExpressionTask::Visit {
        identifier,
        negative,
        positive,
    }];
    let mut results = Vec::<usize>::new();
    while let Some(task) = tasks.pop() {
        match task {
            ExpressionTask::Finish {
                operation,
                child_count,
                negative,
                positive,
            } => {
                let child_start = results.len().checked_sub(child_count).ok_or_else(|| {
                    CoreError::internal("encoded expression task lost child results")
                })?;
                let children = results.split_off(child_start);
                results.push(finish_compiler_expression(
                    operation, children, negative, positive, builder,
                )?);
            }
            ExpressionTask::Visit {
                identifier,
                negative,
                positive,
            } => {
                let node_count = aligned_count(columns.node_tags, 2, "node_tags")?;
                let node = node_index(identifier, node_count)?;
                let tag = u16_at(columns.node_tags, node, "class-expression node tag")?;
                match class_projection_rule(tag) {
                    Some(ClassProjectionRule::NamedClass) => {
                        let entity = decode_named_class(identifier, columns)?;
                        let handle = builder.add_named_occurrence(&entity, negative, positive)?;
                        if positive && entity.iri == OWL_NOTHING_IRI {
                            builder.add_feature(FEATURE_OWL_NOTHING_POSITIVE, 1)?;
                        }
                        results.push(handle);
                    }
                    Some(
                        operation
                        @ (ClassProjectionRule::Intersection | ClassProjectionRule::Union),
                    ) => {
                        let children = node_collection(node, 0, columns)?;
                        tasks.push(ExpressionTask::Finish {
                            operation: if operation == ClassProjectionRule::Intersection {
                                ExpressionFinish::Intersection
                            } else {
                                ExpressionFinish::Union
                            },
                            child_count: children.len(),
                            negative,
                            positive,
                        });
                        tasks.extend(children.into_iter().rev().map(|identifier| {
                            ExpressionTask::Visit {
                                identifier,
                                negative,
                                positive,
                            }
                        }));
                    }
                    Some(ClassProjectionRule::Complement) => {
                        tasks.push(ExpressionTask::Finish {
                            operation: ExpressionFinish::Complement,
                            child_count: 1,
                            negative,
                            positive,
                        });
                        tasks.push(ExpressionTask::Visit {
                            identifier: node_field(node, 0, columns)?,
                            negative: positive,
                            positive: negative,
                        });
                    }
                    Some(ClassProjectionRule::OneOf) => {
                        let members = node_collection(node, 0, columns)?;
                        if !members.is_empty() {
                            builder.add_feature(FEATURE_OBJECT_ONE_OF, 1)?;
                        }
                        let mut children = Vec::new();
                        children.try_reserve_exact(members.len()).map_err(|_| {
                            CoreError::capacity("encoded one-of expression allocation failed")
                        })?;
                        for member in members {
                            let individual = decode_individual_for_axiom(member, columns)?;
                            children.push(builder.add_named_occurrence(
                                &individual,
                                negative,
                                positive,
                            )?);
                        }
                        results.push(finish_compiler_expression(
                            ExpressionFinish::Union,
                            children,
                            negative,
                            positive,
                            builder,
                        )?);
                    }
                    Some(ClassProjectionRule::ObjectSomeValuesFrom) => {
                        let property = decode_object_property_for_axiom(
                            node_field(node, 0, columns)?,
                            columns,
                        )?;
                        builder.add_property_occurrence(&property, negative, positive)?;
                        tasks.push(ExpressionTask::Finish {
                            operation: ExpressionFinish::Existential(property),
                            child_count: 1,
                            negative,
                            positive,
                        });
                        tasks.push(ExpressionTask::Visit {
                            identifier: node_field(node, 1, columns)?,
                            negative,
                            positive,
                        });
                    }
                    Some(ClassProjectionRule::ObjectHasValue) => {
                        let property = decode_object_property_for_axiom(
                            node_field(node, 0, columns)?,
                            columns,
                        )?;
                        builder.add_property_occurrence(&property, negative, positive)?;
                        let individual =
                            decode_individual_for_axiom(node_field(node, 1, columns)?, columns)?;
                        let child =
                            builder.add_named_occurrence(&individual, negative, positive)?;
                        results.push(finish_compiler_expression(
                            ExpressionFinish::Existential(property),
                            vec![child],
                            negative,
                            positive,
                            builder,
                        )?);
                    }
                    Some(ClassProjectionRule::ObjectHasSelf) => {
                        let property = decode_object_property_for_axiom(
                            node_field(node, 0, columns)?,
                            columns,
                        )?;
                        builder.add_property_occurrence(&property, negative, positive)?;
                        if negative {
                            builder.add_feature(FEATURE_OBJECT_HAS_SELF_NEGATIVE, 1)?;
                        }
                        results.push(builder.intern_expression(
                            CompilerExpression::HasSelf(property),
                            negative,
                            positive,
                        )?);
                    }
                    Some(ClassProjectionRule::DataHasValue) => {
                        let property =
                            decode_data_property(node_field(node, 0, columns)?, columns)?;
                        let (payload, observation) = decode_literal_compatibility_key(
                            node_field(node, 1, columns)?,
                            columns,
                        )?;
                        builder.add_feature(FEATURE_DATA_HAS_VALUE, 1)?;
                        builder.compatibility_observations.insert(observation);
                        results.push(builder.intern_expression(
                            CompilerExpression::DataHasValue(property, payload),
                            negative,
                            positive,
                        )?);
                    }
                    Some(ClassProjectionRule::Unsupported(feature, name)) => {
                        return Err(AxiomCompileError::unsupported(feature, name));
                    }
                    None => {
                        return Err(AxiomCompileError::Core(CoreError::invalid(format!(
                            "encoded compiler does not support class-expression tag {tag}"
                        ))));
                    }
                }
            }
        }
    }
    if results.len() != 1 {
        return Err(AxiomCompileError::Core(CoreError::internal(
            "encoded expression conversion did not produce exactly one root",
        )));
    }
    Ok(results
        .pop()
        .ok_or_else(|| CoreError::internal("encoded expression conversion lost its root"))?)
}

fn finish_compiler_expression(
    operation: ExpressionFinish,
    children: Vec<usize>,
    negative: bool,
    positive: bool,
    builder: &mut NamedHierarchyBuilder,
) -> CoreResult<usize> {
    match operation {
        ExpressionFinish::Intersection => {
            let mut children = children.into_iter();
            let Some(mut result) = children.next() else {
                let thing = Entity {
                    kind: EntityKind::Class,
                    iri: OWL_THING_IRI.to_owned(),
                };
                return builder.add_named_occurrence(&thing, negative, positive);
            };
            for child in children {
                result = builder.intern_expression(
                    CompilerExpression::Intersection(result, child),
                    negative,
                    positive,
                )?;
            }
            Ok(result)
        }
        ExpressionFinish::Union => match children.as_slice() {
            [] => {
                let nothing = Entity {
                    kind: EntityKind::Class,
                    iri: OWL_NOTHING_IRI.to_owned(),
                };
                let result = builder.add_named_occurrence(&nothing, negative, positive)?;
                if positive {
                    builder.add_feature(FEATURE_OWL_NOTHING_POSITIVE, 1)?;
                }
                Ok(result)
            }
            [single] => Ok(*single),
            _ => {
                if positive {
                    builder.add_feature(FEATURE_OBJECT_UNION_OF_POSITIVE, 1)?;
                }
                builder.intern_expression(CompilerExpression::Union(children), negative, positive)
            }
        },
        ExpressionFinish::Complement => {
            let [operand] = children.as_slice() else {
                return Err(CoreError::internal(
                    "encoded complement conversion requires one operand",
                ));
            };
            if negative {
                builder.add_feature(FEATURE_OBJECT_COMPLEMENT_OF_NEGATIVE, 1)?;
            }
            if positive {
                builder.add_feature(FEATURE_OBJECT_COMPLEMENT_OF_POSITIVE, 1)?;
            }
            builder.intern_expression(CompilerExpression::Complement(*operand), negative, positive)
        }
        ExpressionFinish::Existential(property) => {
            let [filler] = children.as_slice() else {
                return Err(CoreError::internal(
                    "encoded existential conversion requires one filler",
                ));
            };
            if positive
                && matches!(
                    &builder.expressions[*filler],
                    CompilerExpression::Named(Entity {
                        kind: EntityKind::NamedIndividual,
                        ..
                    })
                )
            {
                builder.add_feature(FEATURE_OBJECT_HAS_VALUE_POSITIVE, 1)?;
            }
            builder.intern_expression(
                CompilerExpression::Existential(property, *filler),
                negative,
                positive,
            )
        }
    }
}

fn decode_individual_for_axiom<B: ByteSource>(
    identifier: u32,
    columns: &EncodedColumns<B>,
) -> AxiomCompileResult<Entity> {
    let node_count = aligned_count(columns.node_tags, 2, "node_tags")?;
    let node = node_index(identifier, node_count)?;
    if u16_at(columns.node_tags, node, "individual node tag")? == 3 {
        return Err(AxiomCompileError::unsupported(
            FEATURE_ANONYMOUS_INDIVIDUAL,
            "ANONYMOUS_INDIVIDUAL",
        ));
    }
    Ok(decode_named_individual(identifier, columns)?)
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

fn decode_object_property_for_axiom<B: ByteSource>(
    identifier: u32,
    columns: &EncodedColumns<B>,
) -> AxiomCompileResult<Entity> {
    let node_count = aligned_count(columns.node_tags, 2, "node_tags")?;
    let node = node_index(identifier, node_count)?;
    if u16_at(
        columns.node_tags,
        node,
        "object-property expression node tag",
    )? == 10
    {
        return Err(AxiomCompileError::unsupported(
            FEATURE_OBJECT_INVERSE_OF,
            "OBJECT_INVERSE_OF",
        ));
    }
    Ok(decode_named_object_property(identifier, columns)?)
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

fn decode_data_property<B: ByteSource>(
    identifier: u32,
    columns: &EncodedColumns<B>,
) -> CoreResult<Entity> {
    let entity = decode_entity(identifier, columns)?;
    if entity.kind != EntityKind::DataProperty {
        return Err(CoreError::internal(
            "validated data property resolved to the wrong entity kind",
        ));
    }
    Ok(entity)
}

fn decode_literal_compatibility_key<B: ByteSource>(
    identifier: u32,
    columns: &EncodedColumns<B>,
) -> CoreResult<(Vec<u8>, Vec<u8>)> {
    let node_count = aligned_count(columns.node_tags, 2, "node_tags")?;
    let node = node_index(identifier, node_count)?;
    if u16_at(columns.node_tags, node, "literal node tag")? != 4 {
        return Err(CoreError::internal(
            "validated data-has-value operand is not a literal",
        ));
    }
    let lexical = text_field(node, 0, columns)?;
    let datatype = decode_entity(node_field(node, 1, columns)?, columns)?;
    if datatype.kind != EntityKind::Datatype {
        return Err(CoreError::internal(
            "validated literal datatype has the wrong entity kind",
        ));
    }
    let language = optional_text_field(node, 2, columns)?;
    let mut lexical_bytes = lexical.into_bytes();
    if datatype.iri == RDF_PLAIN_LITERAL_IRI {
        let language_length = language.as_ref().map_or(0, String::len);
        lexical_bytes
            .try_reserve(
                language_length
                    .checked_add(1)
                    .ok_or_else(|| CoreError::capacity("literal language length overflow"))?,
            )
            .map_err(|_| CoreError::capacity("literal compatibility allocation failed"))?;
        lexical_bytes.push(b'@');
        if let Some(language) = language {
            lexical_bytes.extend_from_slice(language.as_bytes());
        }
    }

    let mut payload = Vec::new();
    append_compatibility_bytes(&mut payload, b"pyelk:elk-literal-key:v1\0")?;
    append_big_endian_frame(&mut payload, &lexical_bytes)?;
    append_big_endian_frame(&mut payload, datatype.iri.as_bytes())?;

    let mut observation = Vec::new();
    append_compatibility_bytes(&mut observation, b"pyelk:elk-literal-spelling:v1\0")?;
    append_big_endian_frame(&mut observation, b"canonical-fallback")?;
    append_big_endian_frame(&mut observation, &payload)?;
    Ok((payload, observation))
}

fn optional_text_field<B: ByteSource>(
    node: usize,
    position: usize,
    columns: &EncodedColumns<B>,
) -> CoreResult<Option<String>> {
    let start = usize_at(columns.node_field_offsets, node, "node field offset")?;
    let field = start
        .checked_add(position)
        .ok_or_else(|| CoreError::capacity("encoded compiler field index overflow"))?;
    match byte_at(columns.field_kinds, field, "optional text field kind")? {
        COMPONENT_NONE => Ok(None),
        COMPONENT_TEXT => Ok(Some(text_field(node, position, columns)?)),
        _ => Err(CoreError::internal(
            "validated optional text field has an unexpected component kind",
        )),
    }
}

fn append_compatibility_bytes(target: &mut Vec<u8>, value: &[u8]) -> CoreResult<()> {
    target
        .try_reserve(value.len())
        .map_err(|_| CoreError::capacity("literal compatibility allocation failed"))?;
    target.extend_from_slice(value);
    Ok(())
}

fn append_big_endian_frame(target: &mut Vec<u8>, value: &[u8]) -> CoreResult<()> {
    let length = u64::try_from(value.len())
        .map_err(|_| CoreError::capacity("literal compatibility frame exceeds u64"))?;
    append_compatibility_bytes(target, &length.to_be_bytes())?;
    append_compatibility_bytes(target, value)
}

fn decode_property_chain_for_axiom<B: ByteSource>(
    identifier: u32,
    columns: &EncodedColumns<B>,
) -> AxiomCompileResult<Vec<Entity>> {
    let node_count = aligned_count(columns.node_tags, 2, "node_tags")?;
    let node = node_index(identifier, node_count)?;
    match u16_at(columns.node_tags, node, "sub-property node tag")? {
        2 => Ok(vec![decode_object_property_for_axiom(identifier, columns)?]),
        10 => Err(AxiomCompileError::unsupported(
            FEATURE_OBJECT_INVERSE_OF,
            "OBJECT_INVERSE_OF",
        )),
        11 => {
            let members = node_collection(node, 0, columns)?;
            if members.len() < 2 {
                return Err(AxiomCompileError::Core(CoreError::invalid(
                    "encoded object property chain must contain at least two members",
                )));
            }
            let mut properties = Vec::new();
            properties
                .try_reserve_exact(members.len())
                .map_err(|_| CoreError::capacity("encoded property-chain allocation failed"))?;
            for member in members {
                properties.push(decode_object_property_for_axiom(member, columns)?);
            }
            Ok(properties)
        }
        _ => Err(AxiomCompileError::Core(CoreError::invalid(
            "encoded named-hierarchy compiler does not support inverse object properties",
        ))),
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

fn accept_annotations<B: ByteSource>(
    node: usize,
    position: usize,
    columns: &EncodedColumns<B>,
) -> CoreResult<()> {
    let start = usize_at(columns.node_field_offsets, node, "node field offset")?;
    let expected = start
        .checked_add(position)
        .ok_or_else(|| CoreError::capacity("encoded compiler field index overflow"))?;
    if expected != annotation_field(node, columns)? {
        return Err(CoreError::internal(
            "encoded compiler annotation field is not in the frozen position",
        ));
    }
    Ok(())
}

fn annotation_count<B: ByteSource>(node: usize, columns: &EncodedColumns<B>) -> CoreResult<usize> {
    usize_at(
        columns.field_lengths,
        annotation_field(node, columns)?,
        "annotation count",
    )
}

fn annotation_field<B: ByteSource>(node: usize, columns: &EncodedColumns<B>) -> CoreResult<usize> {
    let start = usize_at(columns.node_field_offsets, node, "node field offset")?;
    let end = usize_at(
        columns.node_field_offsets,
        node.checked_add(1)
            .ok_or_else(|| CoreError::capacity("encoded compiler node index overflow"))?,
        "node field end",
    )?;
    let field = end
        .checked_sub(1)
        .ok_or_else(|| CoreError::internal("encoded axiom constructor has no annotation field"))?;
    if field < start
        || byte_at(columns.field_kinds, field, "annotation field kind")? != COMPONENT_SET
    {
        return Err(CoreError::internal(
            "encoded axiom annotation field is not a canonical set",
        ));
    }
    Ok(field)
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

fn merge_occurrence(target: &mut Occurrence, source: Occurrence) -> CoreResult<()> {
    target.negative = target
        .negative
        .checked_add(source.negative)
        .ok_or_else(|| CoreError::capacity("encoded negative occurrence exceeds u64"))?;
    target.positive = target
        .positive
        .checked_add(source.positive)
        .ok_or_else(|| CoreError::capacity("encoded positive occurrence exceeds u64"))?;
    Ok(())
}

fn remapped_axiom_handle(handles: &[usize], handle: usize) -> CoreResult<usize> {
    handles
        .get(handle)
        .copied()
        .ok_or_else(|| CoreError::internal("encoded axiom references an unknown expression"))
}

#[cfg(test)]
fn unsupported_expression_feature(tag: u16) -> Option<(usize, &'static str)> {
    if let Some(ClassProjectionRule::Unsupported(feature, name)) = class_projection_rule(tag) {
        Some((feature, name))
    } else {
        None
    }
}

#[cfg(test)]
fn unsupported_axiom_feature(tag: u16) -> Option<(usize, &'static str)> {
    if let Some(AxiomProjectionRule::Unsupported(feature, name)) = axiom_projection_rule(tag) {
        Some((feature, name))
    } else {
        None
    }
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
        field_limit: Option<usize>,
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
    push_compare_task(
        &mut tasks,
        CanonicalCompareTask::Node {
            left,
            right,
            field_limit: None,
        },
    )?;
    while let Some(task) = tasks.pop() {
        claim_work(work, 1, max_work)?;
        match task {
            CanonicalCompareTask::Node {
                left,
                right,
                field_limit,
            } => {
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
                let arity = left_end - left_start;
                if right_end - right_start != arity {
                    return Err(CoreError::internal(
                        "equal constructor tags have different validated arities",
                    ));
                }
                let remaining = field_limit.unwrap_or(arity);
                if remaining > arity {
                    return Err(CoreError::internal(
                        "canonical comparison field limit exceeds constructor arity",
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
                            field_limit: None,
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

#[allow(clippy::too_many_arguments)]
fn compare_canonical_nodes_between<L: ByteSource, R: ByteSource>(
    left: usize,
    right: usize,
    left_columns: &EncodedColumns<L>,
    right_columns: &EncodedColumns<R>,
    left_lengths: &[u64],
    right_lengths: &[u64],
    work: &mut u64,
    max_work: u64,
) -> CoreResult<Ordering> {
    compare_canonical_nodes_between_with_field_limit(
        left,
        right,
        left_columns,
        right_columns,
        left_lengths,
        right_lengths,
        None,
        work,
        max_work,
    )
}

#[allow(clippy::too_many_arguments)]
fn compare_canonical_nodes_between_with_field_limit<L: ByteSource, R: ByteSource>(
    left: usize,
    right: usize,
    left_columns: &EncodedColumns<L>,
    right_columns: &EncodedColumns<R>,
    left_lengths: &[u64],
    right_lengths: &[u64],
    field_limit: Option<usize>,
    work: &mut u64,
    max_work: u64,
) -> CoreResult<Ordering> {
    let mut tasks = Vec::new();
    push_compare_task(
        &mut tasks,
        CanonicalCompareTask::Node {
            left,
            right,
            field_limit,
        },
    )?;
    while let Some(task) = tasks.pop() {
        claim_work(work, 1, max_work)?;
        match task {
            CanonicalCompareTask::Node {
                left,
                right,
                field_limit,
            } => {
                let left_tag = u64::from(u16_at(left_columns.node_tags, left, "left node tag")?);
                let right_tag =
                    u64::from(u16_at(right_columns.node_tags, right, "right node tag")?);
                let ordering = compare_u64_varints(left_tag, right_tag);
                if ordering != Ordering::Equal {
                    return Ok(ordering);
                }
                let left_start = usize_at(
                    left_columns.node_field_offsets,
                    left,
                    "left node field offset",
                )?;
                let left_end = usize_at(
                    left_columns.node_field_offsets,
                    left + 1,
                    "left node field offset",
                )?;
                let right_start = usize_at(
                    right_columns.node_field_offsets,
                    right,
                    "right node field offset",
                )?;
                let right_end = usize_at(
                    right_columns.node_field_offsets,
                    right + 1,
                    "right node field offset",
                )?;
                let arity = left_end - left_start;
                if right_end - right_start != arity {
                    return Err(CoreError::internal(
                        "equal cross-segment constructor tags have different validated arities",
                    ));
                }
                let remaining = field_limit.unwrap_or(arity);
                if remaining > arity {
                    return Err(CoreError::internal(
                        "cross-segment comparison field limit exceeds constructor arity",
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
                if let Some(ordering) = schedule_component_comparison_between(
                    ComponentRow::Field(left),
                    ComponentRow::Field(right),
                    left_columns,
                    right_columns,
                    left_lengths,
                    right_lengths,
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
                        node_id_at(left_columns.item_values, left, "left set item node ID")?,
                        left_lengths.len(),
                    )?;
                    let right_node = node_index(
                        node_id_at(right_columns.item_values, right, "right set item node ID")?,
                        right_lengths.len(),
                    )?;
                    let ordering =
                        compare_u64_varints(left_lengths[left_node], right_lengths[right_node]);
                    if ordering != Ordering::Equal {
                        return Ok(ordering);
                    }
                    push_compare_task(
                        &mut tasks,
                        CanonicalCompareTask::Node {
                            left: left_node,
                            right: right_node,
                            field_limit: None,
                        },
                    )?;
                } else if let Some(ordering) = schedule_component_comparison_between(
                    ComponentRow::Item(left),
                    ComponentRow::Item(right),
                    left_columns,
                    right_columns,
                    left_lengths,
                    right_lengths,
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

#[allow(clippy::too_many_arguments)]
fn schedule_component_comparison_between<L: ByteSource, R: ByteSource>(
    left: ComponentRow,
    right: ComponentRow,
    left_columns: &EncodedColumns<L>,
    right_columns: &EncodedColumns<R>,
    left_lengths: &[u64],
    right_lengths: &[u64],
    tasks: &mut Vec<CanonicalCompareTask>,
    work: &mut u64,
    max_work: u64,
) -> CoreResult<Option<Ordering>> {
    let (left_kind, left_value, left_length) = component_parts(left, left_columns)?;
    let (right_kind, right_value, right_length) = component_parts(right, right_columns)?;
    let ordering = left_kind.cmp(&right_kind);
    if ordering != Ordering::Equal {
        return Ok(Some(ordering));
    }
    match left_kind {
        COMPONENT_NONE => Ok(None),
        COMPONENT_NODE => {
            let left_node = node_index(
                u32::try_from(left_value)
                    .map_err(|_| CoreError::protocol("left encoded node ID exceeds u32"))?,
                left_lengths.len(),
            )?;
            let right_node = node_index(
                u32::try_from(right_value)
                    .map_err(|_| CoreError::protocol("right encoded node ID exceeds u32"))?,
                right_lengths.len(),
            )?;
            let ordering = compare_u64_varints(left_lengths[left_node], right_lengths[right_node]);
            if ordering != Ordering::Equal {
                return Ok(Some(ordering));
            }
            push_compare_task(
                tasks,
                CanonicalCompareTask::Node {
                    left: left_node,
                    right: right_node,
                    field_limit: None,
                },
            )?;
            Ok(None)
        }
        COMPONENT_TEXT | COMPONENT_BYTES | COMPONENT_ENUM => {
            let left_size = u64::try_from(left_length)
                .map_err(|_| CoreError::capacity("left encoded scalar length exceeds u64"))?;
            let right_size = u64::try_from(right_length)
                .map_err(|_| CoreError::capacity("right encoded scalar length exceeds u64"))?;
            let ordering = compare_u64_varints(left_size, right_size);
            if ordering != Ordering::Equal {
                return Ok(Some(ordering));
            }
            let ordering = compare_scalar_ranges_between(
                left_columns.scalar_bytes,
                right_columns.scalar_bytes,
                left_value,
                right_value,
                left_length,
                work,
                max_work,
            )?;
            Ok((ordering != Ordering::Equal).then_some(ordering))
        }
        COMPONENT_INTEGER => {
            let ordering = compare_integer_components_between(
                left_columns.scalar_bytes,
                right_columns.scalar_bytes,
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
                .map_err(|_| CoreError::capacity("left encoded collection length exceeds u64"))?;
            let right_size = u64::try_from(right_length)
                .map_err(|_| CoreError::capacity("right encoded collection length exceeds u64"))?;
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
            "invalid component reached cross-segment canonical comparison",
        )),
    }
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
                    field_limit: None,
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

#[allow(clippy::too_many_arguments)]
fn compare_scalar_ranges_between<L: ByteSource, R: ByteSource>(
    left_scalars: L,
    right_scalars: R,
    left: usize,
    right: usize,
    length: usize,
    work: &mut u64,
    max_work: u64,
) -> CoreResult<Ordering> {
    claim_work(
        work,
        u64::try_from(length)
            .map_err(|_| CoreError::capacity("cross-segment scalar comparison exceeds u64"))?,
        max_work,
    )?;
    for offset in 0..length {
        let left_byte = byte_at(
            left_scalars,
            left.checked_add(offset)
                .ok_or_else(|| CoreError::capacity("left encoded scalar offset overflow"))?,
            "left canonical scalar",
        )?;
        let right_byte = byte_at(
            right_scalars,
            right
                .checked_add(offset)
                .ok_or_else(|| CoreError::capacity("right encoded scalar offset overflow"))?,
            "right canonical scalar",
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

fn compare_integer_components_between<L: ByteSource, R: ByteSource>(
    left_scalars: L,
    right_scalars: R,
    left: ScalarRange,
    right: ScalarRange,
    work: &mut u64,
    max_work: u64,
) -> CoreResult<Ordering> {
    let left_width = canonical_integer_varint_width(left_scalars, left.start, left.length)?;
    let right_width = canonical_integer_varint_width(right_scalars, right.start, right.length)?;
    let compared = left_width.max(right_width);
    claim_work(
        work,
        u64::try_from(compared)
            .map_err(|_| CoreError::capacity("cross-segment integer comparison exceeds u64"))?,
        max_work,
    )?;
    for index in 0..compared {
        let left_byte = (index < left_width)
            .then(|| integer_varint_byte(left_scalars, left.start, left.length, index, left_width));
        let right_byte = (index < right_width).then(|| {
            integer_varint_byte(right_scalars, right.start, right.length, index, right_width)
        });
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

    fn annotation_property_domain() -> OwnedColumns {
        OwnedColumns {
            root_kinds: vec![ROOT_AXIOM],
            root_ids: le32(&[4]),
            node_tags: le16(&[1, 1, 2, 122]),
            node_field_offsets: le64(&[0, 1, 2, 4, 7]),
            field_kinds: vec![
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_SET,
            ],
            field_values: le64(&[0, 5, 10, 2, 3, 1, 0]),
            field_lengths: le64(&[5, 5, 19, 0, 0, 0, 0]),
            item_kinds: Vec::new(),
            item_values: Vec::new(),
            item_lengths: Vec::new(),
            scalar_bytes: b"urn:durn:pannotation_property".to_vec(),
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

    fn named_existential_superclass() -> OwnedColumns {
        OwnedColumns {
            root_kinds: vec![ROOT_AXIOM],
            root_ids: le32(&[8]),
            node_tags: le16(&[1, 1, 1, 2, 2, 2, 34, 61]),
            node_field_offsets: le64(&[0, 1, 2, 3, 5, 7, 9, 11, 14]),
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
                COMPONENT_NODE,
                COMPONENT_SET,
            ],
            field_values: le64(&[0, 5, 10, 15, 1, 20, 2, 25, 3, 6, 5, 4, 7, 0]),
            field_lengths: le64(&[5, 5, 5, 5, 0, 5, 0, 15, 0, 0, 0, 0, 0, 0]),
            item_kinds: Vec::new(),
            item_values: Vec::new(),
            item_lengths: Vec::new(),
            scalar_bytes: b"urn:Aurn:Burn:pclassclassobject_property".to_vec(),
        }
    }

    fn named_has_self_subclass() -> OwnedColumns {
        OwnedColumns {
            root_kinds: vec![ROOT_AXIOM],
            root_ids: le32(&[6]),
            node_tags: le16(&[1, 1, 2, 2, 37, 61]),
            node_field_offsets: le64(&[0, 1, 2, 4, 6, 7, 10]),
            field_kinds: vec![
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_SET,
            ],
            field_values: le64(&[0, 5, 10, 1, 15, 2, 4, 5, 3, 0]),
            field_lengths: le64(&[5, 5, 5, 0, 15, 0, 0, 0, 0, 0]),
            item_kinds: Vec::new(),
            item_values: Vec::new(),
            item_lengths: Vec::new(),
            scalar_bytes: b"urn:Aurn:pclassobject_property".to_vec(),
        }
    }

    fn named_has_value_superclass() -> OwnedColumns {
        OwnedColumns {
            root_kinds: vec![ROOT_AXIOM],
            root_ids: le32(&[8]),
            node_tags: le16(&[1, 1, 1, 2, 2, 2, 36, 61]),
            node_field_offsets: le64(&[0, 1, 2, 3, 5, 7, 9, 11, 14]),
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
                COMPONENT_NODE,
                COMPONENT_SET,
            ],
            field_values: le64(&[0, 5, 10, 15, 1, 20, 3, 35, 2, 5, 6, 4, 7, 0]),
            field_lengths: le64(&[5, 5, 5, 5, 0, 15, 0, 16, 0, 0, 0, 0, 0, 0]),
            item_kinds: Vec::new(),
            item_values: Vec::new(),
            item_lengths: Vec::new(),
            scalar_bytes: b"urn:Aurn:iurn:pclassobject_propertynamed_individual".to_vec(),
        }
    }

    fn nested_supported_subclass() -> OwnedColumns {
        OwnedColumns {
            root_kinds: vec![ROOT_AXIOM],
            root_ids: le32(&[20]),
            node_tags: le16(&[
                1, 1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 30, 31, 32, 33, 34, 61,
            ]),
            node_field_offsets: le64(&[
                0, 1, 2, 3, 4, 5, 6, 7, 9, 11, 13, 15, 17, 19, 21, 22, 23, 24, 25, 27, 30,
            ]),
            field_kinds: vec![
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_TEXT,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_SET,
                COMPONENT_SET,
                COMPONENT_NODE,
                COMPONENT_SET,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_SET,
            ],
            field_values: le64(&[
                0, 7, 14, 21, 28, 35, 42, 49, 1, 54, 2, 59, 3, 64, 4, 69, 7, 84, 5, 100, 6,
                0, 3, 11, 5, 12, 18, 15, 16, 7,
            ]),
            field_lengths: le64(&[
                7, 7, 7, 7, 7, 7, 7, 5, 0, 5, 0, 5, 0, 5, 0, 15, 0, 16, 0, 16, 0, 3, 2,
                0, 2, 0, 0, 0, 0, 0,
            ]),
            item_kinds: vec![COMPONENT_NODE; 7],
            item_values: le64(&[8, 9, 10, 17, 19, 13, 14]),
            item_lengths: le64(&[0; 7]),
            scalar_bytes:
                b"urn:n#Aurn:n#Burn:n#Curn:n#Durn:n#iurn:n#jurn:n#pclassclassclassclassobject_propertynamed_individualnamed_individual"
                    .to_vec(),
        }
    }

    fn data_has_value_subclass() -> OwnedColumns {
        OwnedColumns {
            root_kinds: vec![ROOT_AXIOM],
            root_ids: le32(&[9]),
            node_tags: le16(&[1, 1, 1, 2, 2, 2, 4, 43, 61]),
            node_field_offsets: le64(&[0, 1, 2, 3, 5, 7, 9, 12, 14, 17]),
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
                COMPONENT_TEXT,
                COMPONENT_NODE,
                COMPONENT_TEXT,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_SET,
            ],
            field_values: le64(&[
                0, 7, 15, 70, 1, 75, 3, 83, 2, 96, 5, 101, 6, 7, 8, 4, 0,
            ]),
            field_lengths: le64(&[7, 8, 55, 5, 0, 8, 0, 13, 0, 5, 0, 2, 0, 0, 0, 0, 0]),
            item_kinds: Vec::new(),
            item_values: Vec::new(),
            item_lengths: Vec::new(),
            scalar_bytes:
                b"urn:d#Aurn:d#dphttp://www.w3.org/1999/02/22-rdf-syntax-ns#PlainLiteralclassdatatypedata_propertyhelloen"
                    .to_vec(),
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

    fn anonymous_class_assertion(scope: [u8; 32]) -> OwnedColumns {
        let mut scalar_bytes = b"urn:Aclass".to_vec();
        scalar_bytes.extend_from_slice(&scope);
        scalar_bytes.push(b'x');
        OwnedColumns {
            root_kinds: vec![ROOT_AXIOM],
            root_ids: le32(&[4]),
            node_tags: le16(&[1, 2, 3, 112]),
            node_field_offsets: le64(&[0, 1, 3, 5, 8]),
            field_kinds: vec![
                COMPONENT_TEXT,
                COMPONENT_ENUM,
                COMPONENT_NODE,
                COMPONENT_BYTES,
                COMPONENT_BYTES,
                COMPONENT_NODE,
                COMPONENT_NODE,
                COMPONENT_SET,
            ],
            field_values: le64(&[0, 5, 1, 10, 42, 2, 3, 0]),
            field_lengths: le64(&[5, 5, 0, 32, 1, 0, 0, 0]),
            item_kinds: Vec::new(),
            item_values: Vec::new(),
            item_lengths: Vec::new(),
            scalar_bytes,
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

    fn functional_named_property() -> OwnedColumns {
        let mut columns = transitive_named_property();
        columns.node_tags = le16(&[1, 2, 76]);
        columns
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
    fn axiom_projection_rule_table_is_complete_sorted_and_authoritative() {
        const TAGS: [u16; 37] = [
            60, 61, 62, 63, 64, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 90, 91, 92, 93,
            94, 95, 100, 101, 110, 111, 112, 113, 114, 115, 116, 120, 121, 122, 123,
        ];
        assert_eq!(
            AXIOM_PROJECTION_RULES
                .iter()
                .map(|dispatch| dispatch.tag)
                .collect::<Vec<_>>(),
            TAGS
        );
        assert!(
            AXIOM_PROJECTION_RULES
                .windows(2)
                .all(|pair| pair[0].tag < pair[1].tag)
        );

        let mut supported = 0;
        let mut unsupported = 0;
        let mut ignored = 0;
        for dispatch in AXIOM_PROJECTION_RULES {
            assert_eq!(axiom_projection_rule(dispatch.tag), Some(dispatch.rule));
            match dispatch.rule {
                AxiomProjectionRule::Unsupported(feature, name) => {
                    unsupported += 1;
                    assert_eq!(
                        unsupported_axiom_feature(dispatch.tag),
                        Some((feature, name))
                    );
                }
                AxiomProjectionRule::IgnoreNonlogical => {
                    ignored += 1;
                    assert_eq!(unsupported_axiom_feature(dispatch.tag), None);
                }
                _ => {
                    supported += 1;
                    assert_eq!(unsupported_axiom_feature(dispatch.tag), None);
                }
            }
        }
        assert_eq!((supported, unsupported, ignored), (15, 18, 4));
        assert_eq!(axiom_projection_rule(59), None);
        assert_eq!(axiom_projection_rule(124), None);
    }

    #[test]
    fn class_projection_rule_table_is_complete_sorted_and_authoritative() {
        const TAGS: [u16; 19] = [
            2, 3, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46,
        ];
        assert_eq!(
            CLASS_PROJECTION_RULES
                .iter()
                .map(|dispatch| dispatch.tag)
                .collect::<Vec<_>>(),
            TAGS
        );
        assert!(
            CLASS_PROJECTION_RULES
                .windows(2)
                .all(|pair| pair[0].tag < pair[1].tag)
        );

        let mut supported = 0;
        let mut unsupported = 0;
        for dispatch in CLASS_PROJECTION_RULES {
            assert_eq!(class_projection_rule(dispatch.tag), Some(dispatch.rule));
            match dispatch.rule {
                ClassProjectionRule::Unsupported(feature, name) => {
                    unsupported += 1;
                    assert_eq!(
                        unsupported_expression_feature(dispatch.tag),
                        Some((feature, name))
                    );
                }
                _ => {
                    supported += 1;
                    assert_eq!(unsupported_expression_feature(dispatch.tag), None);
                }
            }
        }
        assert_eq!((supported, unsupported), (9, 10));
        assert_eq!(class_projection_rule(1), None);
        assert_eq!(class_projection_rule(47), None);
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
    fn coarse_column_shape_avoids_a_duplicate_structural_row_scan() {
        let columns = declaration();
        let shape = validate_column_shape(columns.borrowed(), EncodedLimits::default()).unwrap();
        assert_eq!(shape.root_count, 1);
        assert_eq!(shape.node_count, 3);
        assert_eq!(shape.field_count, 5);
        assert_eq!(shape.item_count, 0);
        assert_eq!(shape.scalar_bytes, 10);

        let mut malformed = columns;
        malformed.node_tags = le16(&[1, 2, u16::MAX]);
        assert!(validate_column_shape(malformed.borrowed(), EncodedLimits::default()).is_ok());
        assert!(validate_columns(malformed.borrowed(), EncodedLimits::default()).is_err());
        assert!(
            compile_encoded_hierarchy_with_policy(
                malformed.borrowed(),
                EncodedLimits::default(),
                EncodedUnsupportedPolicy::Error,
            )
            .is_err()
        );
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
    fn named_existential_superclass_propagates_positive_polarity() {
        let compiled = compile_named_hierarchy(
            named_existential_superclass().borrowed(),
            EncodedLimits::default(),
            [20; 32],
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
    }

    #[test]
    fn nested_supported_expressions_match_scalar_topological_ir() {
        let owned = nested_supported_subclass();
        let compiled =
            compile_named_hierarchy(owned.borrowed(), EncodedLimits::default(), [29; 32]).unwrap();

        assert_eq!(
            compiled
                .expressions
                .iter()
                .map(|expression| (expression.tag, expression.arguments.as_slice()))
                .collect::<Vec<_>>(),
            vec![
                (ExpressionTag::Class, &[0][..]),
                (ExpressionTag::Class, &[1][..]),
                (ExpressionTag::Class, &[2][..]),
                (ExpressionTag::Class, &[3][..]),
                (ExpressionTag::Class, &[4][..]),
                (ExpressionTag::Class, &[5][..]),
                (ExpressionTag::Individual, &[6][..]),
                (ExpressionTag::Individual, &[7][..]),
                (ExpressionTag::ObjectIntersectionOf, &[2, 3][..]),
                (ExpressionTag::ObjectIntersectionOf, &[8, 4][..]),
                (ExpressionTag::ObjectComplementOf, &[5][..]),
                (ExpressionTag::ObjectUnionOf, &[6, 7][..]),
                (ExpressionTag::ObjectSomeValuesFrom, &[10, 11][..]),
                (ExpressionTag::ObjectUnionOf, &[10, 12][..]),
            ]
        );
        assert_eq!(
            compiled
                .expression_occurrences
                .iter()
                .map(|occurrence| (occurrence.negative, occurrence.positive))
                .collect::<Vec<_>>(),
            vec![
                (0, 0),
                (0, 0),
                (1, 0),
                (1, 0),
                (1, 0),
                (1, 0),
                (0, 1),
                (0, 1),
                (1, 0),
                (1, 0),
                (0, 1),
                (0, 1),
                (0, 1),
                (0, 1),
            ]
        );
        assert_eq!(compiled.subclass_axioms, vec![(9, 13)]);
        assert_eq!(compiled.property_occurrences[2].positive, 1);
        assert_eq!(
            compiled.feature_counts[FEATURE_OBJECT_COMPLEMENT_OF_POSITIVE],
            1
        );
        assert_eq!(compiled.feature_counts[FEATURE_OBJECT_ONE_OF], 1);
        assert_eq!(compiled.feature_counts[FEATURE_OBJECT_UNION_OF_POSITIVE], 2);
        assert_eq!(
            compile_named_hierarchy(owned.indexed(), EncodedLimits::default(), [29; 32]).unwrap(),
            compiled
        );
    }

    #[test]
    fn data_has_value_preserves_scalar_literal_key_and_cache_observation() {
        let owned = data_has_value_subclass();
        let compilation = compile_encoded_hierarchy_with_policy(
            owned.borrowed(),
            EncodedLimits::default(),
            EncodedUnsupportedPolicy::Error,
        )
        .unwrap();
        let compiled = &compilation.ontology;

        let mut payload = Vec::new();
        append_compatibility_bytes(&mut payload, b"pyelk:elk-literal-key:v1\0").unwrap();
        append_big_endian_frame(&mut payload, b"hello@en").unwrap();
        append_big_endian_frame(&mut payload, RDF_PLAIN_LITERAL_IRI.as_bytes()).unwrap();
        let mut observation = Vec::new();
        append_compatibility_bytes(&mut observation, b"pyelk:elk-literal-spelling:v1\0").unwrap();
        append_big_endian_frame(&mut observation, b"canonical-fallback").unwrap();
        append_big_endian_frame(&mut observation, &payload).unwrap();

        assert_eq!(compiled.expressions[3].tag, ExpressionTag::DataHasValue);
        assert_eq!(compiled.expressions[3].arguments, [5]);
        assert_eq!(compiled.expressions[3].payload, payload);
        assert_eq!(compiled.expression_occurrences[3].negative, 1);
        assert_eq!(compiled.subclass_axioms, vec![(3, 2)]);
        assert_eq!(compiled.feature_counts[FEATURE_DATA_HAS_VALUE], 1);
        assert_eq!(compilation.compatibility_observations, vec![observation]);
        assert_eq!(compiled.source_fingerprint, [0; 32]);

        assert_eq!(
            compile_named_hierarchy(owned.indexed(), EncodedLimits::default(), [30; 32])
                .unwrap()
                .source_fingerprint,
            [30; 32]
        );
    }

    #[test]
    fn named_has_self_subclass_tracks_negative_incompleteness() {
        let compiled = compile_named_hierarchy(
            named_has_self_subclass().borrowed(),
            EncodedLimits::default(),
            [21; 32],
        )
        .unwrap();

        assert_eq!(compiled.expressions[3].tag, ExpressionTag::ObjectHasSelf);
        assert_eq!(compiled.expressions[3].arguments, [5]);
        assert_eq!(compiled.subclass_axioms, vec![(3, 2)]);
        assert_eq!(compiled.expression_occurrences[2].positive, 1);
        assert_eq!(compiled.expression_occurrences[3].negative, 1);
        assert_eq!(compiled.property_occurrences[2].negative, 1);
        assert_eq!(compiled.feature_counts[FEATURE_OBJECT_HAS_SELF_NEGATIVE], 1);
    }

    #[test]
    fn named_has_value_superclass_reuses_nominal_existential() {
        let compiled = compile_named_hierarchy(
            named_has_value_superclass().borrowed(),
            EncodedLimits::default(),
            [22; 32],
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
            compiled.feature_counts[FEATURE_OBJECT_HAS_VALUE_POSITIVE],
            1
        );
    }

    #[test]
    fn root_postings_select_before_compilation_and_validate_exactly() {
        let columns = two_declarations();
        let excluded = compile_encoded_hierarchy_selected_with_policy(
            columns.borrowed(),
            EncodedLimits::default(),
            EncodedUnsupportedPolicy::Error,
            EncodedPostingMode::Exclude,
            le32(&[1]).as_slice(),
        )
        .unwrap()
        .ontology;
        assert!(excluded.entities.iter().any(|entity| entity.iri == "urn:B"));
        assert!(!excluded.entities.iter().any(|entity| entity.iri == "urn:A"));

        let included = compile_encoded_hierarchy_selected_with_policy(
            columns.borrowed(),
            EncodedLimits::default(),
            EncodedUnsupportedPolicy::Error,
            EncodedPostingMode::Include,
            le32(&[1]).as_slice(),
        )
        .unwrap()
        .ontology;
        assert!(included.entities.iter().any(|entity| entity.iri == "urn:A"));
        assert!(!included.entities.iter().any(|entity| entity.iri == "urn:B"));

        for postings in [le32(&[]), le32(&[1, 1]), le32(&[3])] {
            let error = compile_encoded_hierarchy_selected_with_policy(
                columns.borrowed(),
                EncodedLimits::default(),
                EncodedUnsupportedPolicy::Error,
                EncodedPostingMode::Exclude,
                postings.as_slice(),
            )
            .unwrap_err();
            assert!(matches!(error, CoreError::Protocol(_)));
        }
    }

    #[test]
    fn overlay_delta_tables_merge_canonically_without_flattening() {
        let mut source = declaration();
        source.scalar_bytes[4] = b'B';
        let mut delta = declaration();
        delta.scalar_bytes[4] = b'A';
        let expected = compile_encoded_hierarchy_with_policy(
            two_declarations().borrowed(),
            EncodedLimits::default(),
            EncodedUnsupportedPolicy::Error,
        )
        .unwrap();
        let merged = compile_encoded_overlay_delta_with_policy(
            source.borrowed(),
            delta.borrowed(),
            EncodedLimits::default(),
            EncodedUnsupportedPolicy::Error,
        )
        .unwrap();
        assert_eq!(merged, expected);

        let duplicate = compile_encoded_overlay_delta_with_policy(
            source.borrowed(),
            source.borrowed(),
            EncodedLimits::default(),
            EncodedUnsupportedPolicy::Error,
        )
        .unwrap();
        let direct = compile_encoded_hierarchy_with_policy(
            source.borrowed(),
            EncodedLimits::default(),
            EncodedUnsupportedPolicy::Error,
        )
        .unwrap();
        assert_eq!(duplicate, direct);

        let nested = named_existential_superclass();
        let duplicate = compile_encoded_overlay_delta_with_policy(
            nested.borrowed(),
            nested.borrowed(),
            EncodedLimits::default(),
            EncodedUnsupportedPolicy::Error,
        )
        .unwrap();
        let direct = compile_encoded_hierarchy_with_policy(
            nested.borrowed(),
            EncodedLimits::default(),
            EncodedUnsupportedPolicy::Error,
        )
        .unwrap();
        assert_eq!(duplicate, direct);
    }

    #[test]
    fn overlay_delta_selection_and_limits_apply_to_the_resolved_union() {
        let source = two_declarations();
        let mut delta = declaration();
        delta.scalar_bytes[4] = b'B';
        let postings = le32(&[2]);
        let selected = compile_encoded_overlay_delta_selected_with_policy(
            source.borrowed(),
            delta.borrowed(),
            EncodedLimits::default(),
            EncodedUnsupportedPolicy::Error,
            EncodedPostingMode::Exclude,
            postings.as_slice(),
        )
        .unwrap();
        let expected = compile_encoded_hierarchy_with_policy(
            source.borrowed(),
            EncodedLimits::default(),
            EncodedUnsupportedPolicy::Error,
        )
        .unwrap();
        assert_eq!(selected, expected);

        let limits = EncodedLimits {
            max_roots: 1,
            ..EncodedLimits::default()
        };
        assert!(matches!(
            compile_encoded_overlay_delta_with_policy(
                declaration().borrowed(),
                declaration().borrowed(),
                limits,
                EncodedUnsupportedPolicy::Error,
            ),
            Err(CoreError::Capacity(message)) if message.contains("overlay root count")
        ));
    }

    #[test]
    fn arbitrary_segment_groups_share_one_exact_canonical_merge() {
        let mut first = declaration();
        first.scalar_bytes[4] = b'B';
        let mut second = declaration();
        second.scalar_bytes[4] = b'A';
        let duplicate = second.clone();
        let empty_postings = &[][..];
        let segments = [
            EncodedCompilationSegment {
                columns: first.borrowed(),
                posting_mode: None,
                postings: empty_postings,
                anonymous_scope_map: empty_postings,
            },
            EncodedCompilationSegment {
                columns: second.borrowed(),
                posting_mode: None,
                postings: empty_postings,
                anonymous_scope_map: empty_postings,
            },
            EncodedCompilationSegment {
                columns: duplicate.borrowed(),
                posting_mode: None,
                postings: empty_postings,
                anonymous_scope_map: empty_postings,
            },
        ];
        let actual = compile_encoded_segments_with_policy(
            &segments,
            EncodedLimits::default(),
            EncodedUnsupportedPolicy::Error,
        )
        .unwrap();
        let expected = compile_encoded_hierarchy_with_policy(
            two_declarations().borrowed(),
            EncodedLimits::default(),
            EncodedUnsupportedPolicy::Error,
        )
        .unwrap();
        assert_eq!(actual, expected);

        let postings = le32(&[1]);
        let selected_source = two_declarations();
        let selected = [
            EncodedCompilationSegment {
                columns: selected_source.borrowed(),
                posting_mode: Some(EncodedPostingMode::Include),
                postings: postings.as_slice(),
                anonymous_scope_map: empty_postings,
            },
            EncodedCompilationSegment {
                columns: first.borrowed(),
                posting_mode: None,
                postings: empty_postings,
                anonymous_scope_map: empty_postings,
            },
        ];
        assert_eq!(
            compile_encoded_segments_with_policy(
                &selected,
                EncodedLimits::default(),
                EncodedUnsupportedPolicy::Error,
            )
            .unwrap(),
            expected
        );
    }

    #[test]
    fn segment_scope_maps_transform_anonymous_identity_before_deduplication() {
        let source_scope = [1_u8; 32];
        let target_scope = [2_u8; 32];
        let source = anonymous_class_assertion(source_scope);
        validate_columns(source.borrowed(), EncodedLimits::default()).unwrap();
        let empty = &[][..];
        let duplicate_segments = [
            EncodedCompilationSegment {
                columns: source.borrowed(),
                posting_mode: None,
                postings: empty,
                anonymous_scope_map: empty,
            },
            EncodedCompilationSegment {
                columns: source.borrowed(),
                posting_mode: None,
                postings: empty,
                anonymous_scope_map: empty,
            },
        ];
        let duplicate = compile_encoded_segments_with_policy(
            &duplicate_segments,
            EncodedLimits::default(),
            EncodedUnsupportedPolicy::Ignore,
        )
        .unwrap();
        assert_eq!(
            duplicate.ontology.feature_counts[FEATURE_ANONYMOUS_INDIVIDUAL],
            1
        );

        let mut scope_map = source_scope.to_vec();
        scope_map.extend_from_slice(&target_scope);
        let distinct_segments = [
            duplicate_segments[0],
            EncodedCompilationSegment {
                columns: source.borrowed(),
                posting_mode: None,
                postings: empty,
                anonymous_scope_map: scope_map.as_slice(),
            },
        ];
        let distinct = compile_encoded_segments_with_policy(
            &distinct_segments,
            EncodedLimits::default(),
            EncodedUnsupportedPolicy::Ignore,
        )
        .unwrap();
        assert_eq!(
            distinct.ontology.feature_counts[FEATURE_ANONYMOUS_INDIVIDUAL],
            2
        );

        let mapped = [EncodedCompilationSegment {
            columns: source.borrowed(),
            posting_mode: None,
            postings: empty,
            anonymous_scope_map: scope_map.as_slice(),
        }];
        let tight_metadata = EncodedLimits {
            max_scalar_bytes: 63,
            ..EncodedLimits::default()
        };
        assert!(matches!(
            compile_encoded_segments_with_policy(
                &mapped,
                tight_metadata,
                EncodedUnsupportedPolicy::Ignore,
            ),
            Err(CoreError::Capacity(message)) if message.contains("metadata")
        ));

        let mut identity_map = source_scope.to_vec();
        identity_map.extend_from_slice(&source_scope);
        let malformed = [EncodedCompilationSegment {
            columns: source.borrowed(),
            posting_mode: None,
            postings: empty,
            anonymous_scope_map: identity_map.as_slice(),
        }];
        assert!(matches!(
            compile_encoded_segments_with_policy(
                &malformed,
                EncodedLimits::default(),
                EncodedUnsupportedPolicy::Ignore,
            ),
            Err(CoreError::Protocol(message)) if message.contains("scope-map")
        ));
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
                Err(CoreError::Unsupported(feature)) if feature == kind.to_ascii_uppercase()
            ));
        }

        let ignored = compile_named_hierarchy_with_policy(
            declaration_of("data_property").borrowed(),
            EncodedLimits::default(),
            [0; 32],
            EncodedUnsupportedPolicy::Ignore,
        )
        .unwrap();
        assert_eq!(ignored.feature_counts[8], 1);
        assert!(
            !ignored
                .entities
                .iter()
                .any(|entity| entity.kind == EntityKind::DataProperty)
        );
    }

    #[test]
    fn unsupported_axioms_follow_whole_axiom_policy() {
        let ignored = compile_named_hierarchy_with_policy(
            functional_named_property().borrowed(),
            EncodedLimits::default(),
            [24; 32],
            EncodedUnsupportedPolicy::Ignore,
        )
        .unwrap();
        assert_eq!(ignored.feature_counts[22], 1);
        assert_eq!(ignored.entities.len(), 4);
        assert!(
            ignored
                .property_occurrences
                .iter()
                .all(|value| *value == Occurrence::default())
        );

        assert!(matches!(
            compile_named_hierarchy_with_policy(
                functional_named_property().borrowed(),
                EncodedLimits::default(),
                [24; 32],
                EncodedUnsupportedPolicy::Error,
            ),
            Err(CoreError::Unsupported(feature))
                if feature == "FUNCTIONAL_OBJECT_PROPERTY"
        ));
    }

    #[test]
    fn data_property_axiom_tags_preserve_the_frozen_feature_order() {
        assert_eq!(
            unsupported_axiom_feature(90),
            Some((45, "SUB_DATA_PROPERTY_OF"))
        );
        assert_eq!(
            unsupported_axiom_feature(91),
            Some((20, "EQUIVALENT_DATA_PROPERTIES"))
        );
        assert_eq!(
            unsupported_axiom_feature(92),
            Some((17, "DISJOINT_DATA_PROPERTIES"))
        );
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
    fn predefined_property_polarity_features_apply_to_every_compiler_path() {
        let top = Entity {
            kind: EntityKind::ObjectProperty,
            iri: OWL_TOP_OBJECT_PROPERTY_IRI.to_owned(),
        };
        let bottom = Entity {
            kind: EntityKind::ObjectProperty,
            iri: OWL_BOTTOM_OBJECT_PROPERTY_IRI.to_owned(),
        };
        let mut builder = NamedHierarchyBuilder::with_policy(EncodedUnsupportedPolicy::Error);
        builder.add_subproperty(vec![top], bottom).unwrap();
        let compiled = builder.freeze([23; 32]).unwrap().ontology;

        assert_eq!(
            compiled.feature_counts[FEATURE_TOP_OBJECT_PROPERTY_NEGATIVE],
            1
        );
        assert_eq!(
            compiled.feature_counts[FEATURE_BOTTOM_OBJECT_PROPERTY_POSITIVE],
            1
        );
    }

    #[test]
    fn annotation_axioms_are_semantically_ignored_after_validation() {
        let compiled = compile_named_hierarchy(
            annotation_property_domain().borrowed(),
            EncodedLimits::default(),
            [19; 32],
        )
        .unwrap();

        assert_eq!(compiled.entities.len(), 4);
        assert_eq!(compiled.expressions.len(), 2);
        assert!(compiled.subclass_axioms.is_empty());
        assert!(compiled.feature_counts.iter().all(|count| *count == 0));
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
    fn encoded_model_scalars_obey_native_model_constraints() {
        let mut malformed = declaration();
        malformed.scalar_bytes[0] = b'1';
        assert_protocol_contains(&malformed, "invalid absolute scheme");

        let mut malformed = declaration();
        malformed.scalar_bytes[4] = b'%';
        assert_protocol_contains(&malformed, "invalid percent escape");

        let mut malformed = data_has_value_subclass();
        let language = malformed.scalar_bytes.len() - 2;
        malformed.scalar_bytes[language..].copy_from_slice(b"EN");
        assert_protocol_contains(&malformed, "canonical BCP 47 tag");
    }

    #[test]
    fn language_tag_validation_matches_the_model_grammar() {
        for language in [
            "en",
            "zh-hant-cn",
            "de-CH-1901",
            "sl-rozaj-biske-1994",
            "en-gb-oed",
            "x-private",
        ] {
            assert!(valid_language_tag(language), "rejected {language}");
        }
        for language in [
            "",
            "e",
            "en-",
            "en-1901-1901",
            "en-a-foo-a-bar",
            "x",
            "en-x",
        ] {
            assert!(!valid_language_tag(language), "accepted {language}");
        }
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
