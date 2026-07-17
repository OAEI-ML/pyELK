//! Defensive decoder for pyELK ontology and query IR v1.0.

use std::cmp::Reverse;
use std::collections::{BTreeMap, BTreeSet, BinaryHeap};

use blake2::digest::consts::U32;
use blake2::{Blake2b, Digest};

use crate::error::{CoreError, CoreResult};

type Blake2b256 = Blake2b<U32>;

pub const IR_MAJOR: u16 = 1;
pub const IR_MINOR: u16 = 0;
pub const U32_RESERVED: u32 = u32::MAX;
pub const FEATURE_VECTOR_LENGTH: usize = 79;

const IR_MAGIC: &[u8; 8] = b"PYELKIR\0";
const QUERY_MAGIC: &[u8; 8] = b"PYELKQ\0\0";
const HEADER_SIZE: usize = 16;
const DIRECTORY_ENTRY_SIZE: usize = 26;
const CHECKSUM_SIZE: usize = 32;
const MAX_SECTIONS: usize = 256;
const OPTIONAL_TAG_START: u16 = 0x8000;

pub const OWL_NOTHING_IRI: &str = "http://www.w3.org/2002/07/owl#Nothing";
pub const OWL_THING_IRI: &str = "http://www.w3.org/2002/07/owl#Thing";
pub const OWL_BOTTOM_OBJECT_PROPERTY_IRI: &str =
    "http://www.w3.org/2002/07/owl#bottomObjectProperty";
pub const OWL_TOP_OBJECT_PROPERTY_IRI: &str = "http://www.w3.org/2002/07/owl#topObjectProperty";

/// Entity-kind discriminator in the frozen compiled representation.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
#[repr(u8)]
pub enum EntityKind {
    Class = 0,
    NamedIndividual = 1,
    ObjectProperty = 2,
    DataProperty = 3,
    Datatype = 4,
    AnnotationProperty = 5,
}

impl TryFrom<u8> for EntityKind {
    type Error = CoreError;

    fn try_from(value: u8) -> CoreResult<Self> {
        match value {
            0 => Ok(Self::Class),
            1 => Ok(Self::NamedIndividual),
            2 => Ok(Self::ObjectProperty),
            3 => Ok(Self::DataProperty),
            4 => Ok(Self::Datatype),
            5 => Ok(Self::AnnotationProperty),
            _ => Err(CoreError::protocol(format!("invalid entity kind {value}"))),
        }
    }
}

/// Indexed-expression tag in the frozen compiled representation.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
#[repr(u8)]
pub enum ExpressionTag {
    Class = 0,
    Individual = 1,
    ObjectIntersectionOf = 2,
    ObjectSomeValuesFrom = 3,
    ObjectHasSelf = 4,
    DataHasValue = 5,
    ObjectComplementOf = 6,
    ObjectUnionOf = 7,
}

impl TryFrom<u8> for ExpressionTag {
    type Error = CoreError;

    fn try_from(value: u8) -> CoreResult<Self> {
        match value {
            0 => Ok(Self::Class),
            1 => Ok(Self::Individual),
            2 => Ok(Self::ObjectIntersectionOf),
            3 => Ok(Self::ObjectSomeValuesFrom),
            4 => Ok(Self::ObjectHasSelf),
            5 => Ok(Self::DataHasValue),
            6 => Ok(Self::ObjectComplementOf),
            7 => Ok(Self::ObjectUnionOf),
            _ => Err(CoreError::protocol(format!(
                "invalid expression tag {value}"
            ))),
        }
    }
}

/// Ontology symbol-table row.
#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct Entity {
    pub kind: EntityKind,
    pub iri: String,
}

/// Canonical expression row.
#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct Expression {
    pub tag: ExpressionTag,
    pub payload: Vec<u8>,
    pub arguments: Vec<u32>,
}

/// Positive/negative occurrence pair used by linked-rule registration.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct Occurrence {
    pub negative: u64,
    pub positive: u64,
}

/// Fully validated ontology transferred once into a native session.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Ontology {
    pub entities: Vec<Entity>,
    pub expressions: Vec<Expression>,
    pub expression_occurrences: Vec<Occurrence>,
    pub property_occurrences: Vec<Occurrence>,
    pub property_chains: Vec<Vec<u32>>,
    pub subclass_axioms: Vec<(u32, u32)>,
    pub equivalent_class_axioms: Vec<(u32, u32)>,
    pub disjoint_groups: Vec<Vec<u32>>,
    pub subproperty_axioms: Vec<(u32, u32)>,
    pub property_ranges: Vec<(u32, u32)>,
    pub feature_counts: Vec<u64>,
    pub source_fingerprint: [u8; 32],
}

impl Ontology {
    /// Decode and validate a frozen ontology without trusting any length or ID field.
    pub fn decode(data: &[u8]) -> CoreResult<Self> {
        let sections = decode_container(data, IR_MAGIC, &(1_u16..=20).collect())?;
        let entities = decode_entities(&sections, 1, 2, 3)?;
        validate_entities(&entities, true)?;
        let expressions = decode_expressions(&sections, 4, 5, 6, 7, 8, &entities)?;
        validate_named_expressions(&entities, &expressions)?;
        let expression_occurrences = decode_occurrences(section(&sections, 9)?)?;
        let property_occurrences = decode_occurrences(section(&sections, 10)?)?;
        if expression_occurrences.len() != expressions.len() {
            return Err(CoreError::protocol(
                "expression occurrence count does not match expressions",
            ));
        }
        let object_property_count = entities
            .iter()
            .filter(|entity| entity.kind == EntityKind::ObjectProperty)
            .count();
        if property_occurrences.len() != object_property_count {
            return Err(CoreError::protocol(
                "property occurrence count does not match object properties",
            ));
        }
        let property_chains = decode_csr(section(&sections, 11)?, section(&sections, 12)?)?;
        validate_property_chains(&property_chains, &entities)?;
        let subclass_axioms = decode_pairs(section(&sections, 13)?)?;
        validate_pairs(
            &subclass_axioms,
            expressions.len(),
            expressions.len(),
            "subclass axioms",
        )?;
        let equivalent_class_axioms = decode_pairs(section(&sections, 14)?)?;
        validate_pairs(
            &equivalent_class_axioms,
            expressions.len(),
            expressions.len(),
            "equivalent-class axioms",
        )?;
        let disjoint_groups = decode_csr(section(&sections, 15)?, section(&sections, 16)?)?;
        validate_disjoint_groups(&disjoint_groups, expressions.len())?;
        let subproperty_axioms = decode_pairs(section(&sections, 17)?)?;
        validate_pairs(
            &subproperty_axioms,
            property_chains.len(),
            entities.len(),
            "subproperty axioms",
        )?;
        for &(_, super_property) in &subproperty_axioms {
            validate_entity_kind(super_property, &entities, EntityKind::ObjectProperty)?;
        }
        let property_ranges = decode_pairs(section(&sections, 18)?)?;
        validate_pairs(
            &property_ranges,
            entities.len(),
            expressions.len(),
            "property ranges",
        )?;
        for &(property, _) in &property_ranges {
            validate_entity_kind(property, &entities, EntityKind::ObjectProperty)?;
        }
        let feature_counts = decode_u64(section(&sections, 19)?)?;
        if feature_counts.len() != FEATURE_VECTOR_LENGTH {
            return Err(CoreError::protocol(format!(
                "feature vector must contain {FEATURE_VECTOR_LENGTH} entries"
            )));
        }
        let fingerprint = section(&sections, 20)?;
        if fingerprint.count != 1 || fingerprint.payload.len() != 32 {
            return Err(CoreError::protocol(
                "source fingerprint must be one 32-byte value",
            ));
        }
        let mut source_fingerprint = [0_u8; 32];
        source_fingerprint.copy_from_slice(fingerprint.payload);
        Ok(Self {
            entities,
            expressions,
            expression_occurrences,
            property_occurrences,
            property_chains,
            subclass_axioms,
            equivalent_class_axioms,
            disjoint_groups,
            subproperty_axioms,
            property_ranges,
            feature_counts,
            source_fingerprint,
        })
    }

    /// Find one predefined or named entity.
    pub fn entity_id(&self, kind: EntityKind, iri: &str) -> CoreResult<u32> {
        self.entities
            .iter()
            .position(|entity| entity.kind == kind && entity.iri == iri)
            .and_then(|index| u32::try_from(index).ok())
            .ok_or_else(|| CoreError::internal(format!("missing {kind:?} entity {iri}")))
    }

    /// Find the unique class/individual expression for an entity ID.
    pub fn named_expression(&self, tag: ExpressionTag, entity: u32) -> CoreResult<u32> {
        self.expressions
            .iter()
            .position(|expression| expression.tag == tag && expression.arguments == [entity])
            .and_then(|index| u32::try_from(index).ok())
            .ok_or_else(|| {
                CoreError::internal(format!("missing {tag:?} expression for entity {entity}"))
            })
    }
}

/// Shape tag of a self-contained query mini-IR.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u8)]
pub enum QueryIrKind {
    ClassExpression = 0,
    Entailment = 1,
}

impl TryFrom<u8> for QueryIrKind {
    type Error = CoreError;

    fn try_from(value: u8) -> CoreResult<Self> {
        match value {
            0 => Ok(Self::ClassExpression),
            1 => Ok(Self::Entailment),
            _ => Err(CoreError::protocol(format!(
                "invalid query IR kind {value}"
            ))),
        }
    }
}

/// Query entity plus an optional corresponding ontology entity ID.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct QueryEntity {
    pub entity: Entity,
    pub ontology_id: Option<u32>,
}

/// Validated class-expression or entailment query mini-IR.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct QueryIr {
    pub kind: QueryIrKind,
    pub entities: Vec<QueryEntity>,
    pub expressions: Vec<Expression>,
    pub expression_occurrences: Vec<Occurrence>,
    pub property_occurrences: Vec<Occurrence>,
    pub root_expression: Option<u32>,
    pub subsumption_obligations: Vec<(u32, u32)>,
}

impl QueryIr {
    /// Decode and defensively validate one query payload.
    pub fn decode(data: &[u8]) -> CoreResult<Self> {
        let sections = decode_container(data, QUERY_MAGIC, &(1_u16..=14).collect())?;
        let kinds = decode_u8(section(&sections, 1)?)?;
        if kinds.len() != 1 {
            return Err(CoreError::protocol("query IR requires exactly one kind"));
        }
        let kind = QueryIrKind::try_from(kinds[0])?;
        let entity_values = decode_entities(&sections, 2, 3, 4)?;
        validate_entities(&entity_values, false)?;
        let ontology_ids = decode_u32(section(&sections, 5)?)?;
        if ontology_ids.len() != entity_values.len() {
            return Err(CoreError::protocol(
                "query entity and ontology-ID counts differ",
            ));
        }
        let mut seen_ontology_ids = BTreeSet::new();
        let entities = entity_values
            .into_iter()
            .zip(ontology_ids)
            .map(|(entity, value)| {
                let ontology_id = (value != U32_RESERVED).then_some(value);
                if let Some(identifier) = ontology_id
                    && !seen_ontology_ids.insert(identifier)
                {
                    return Err(CoreError::protocol(
                        "query ontology entity IDs must be unique",
                    ));
                }
                Ok(QueryEntity {
                    entity,
                    ontology_id,
                })
            })
            .collect::<CoreResult<Vec<_>>>()?;
        let plain_entities = entities
            .iter()
            .map(|record| record.entity.clone())
            .collect::<Vec<_>>();
        let expressions = decode_expressions(&sections, 6, 7, 8, 9, 10, &plain_entities)?;
        validate_named_expressions(&plain_entities, &expressions)?;
        let expression_occurrences = decode_occurrences(section(&sections, 11)?)?;
        if expression_occurrences.len() != expressions.len() {
            return Err(CoreError::protocol(
                "query expression occurrence count does not match expressions",
            ));
        }
        let property_occurrences = decode_occurrences(section(&sections, 12)?)?;
        let object_property_count = plain_entities
            .iter()
            .filter(|entity| entity.kind == EntityKind::ObjectProperty)
            .count();
        if property_occurrences.len() != object_property_count {
            return Err(CoreError::protocol(
                "query property occurrence count does not match object properties",
            ));
        }
        let roots = decode_u32(section(&sections, 13)?)?;
        if roots.len() != 1 {
            return Err(CoreError::protocol("query IR requires one root field"));
        }
        let root_expression = (roots[0] != U32_RESERVED).then_some(roots[0]);
        if root_expression.is_some_and(|root| root as usize >= expressions.len()) {
            return Err(CoreError::protocol("query root expression is out of range"));
        }
        let subsumption_obligations = decode_pairs(section(&sections, 14)?)?;
        validate_pairs(
            &subsumption_obligations,
            expressions.len(),
            expressions.len(),
            "query subsumption obligations",
        )?;
        match kind {
            QueryIrKind::ClassExpression => {
                if root_expression.is_none() || !subsumption_obligations.is_empty() {
                    return Err(CoreError::protocol(
                        "class query requires a root and no obligations",
                    ));
                }
            }
            QueryIrKind::Entailment => {
                if root_expression.is_some() {
                    return Err(CoreError::protocol(
                        "entailment query cannot contain a class root",
                    ));
                }
            }
        }
        Ok(Self {
            kind,
            entities,
            expressions,
            expression_occurrences,
            property_occurrences,
            root_expression,
            subsumption_obligations,
        })
    }
}

#[derive(Clone, Copy)]
struct Section<'a> {
    count: u64,
    payload: &'a [u8],
}

fn section<'a>(sections: &BTreeMap<u16, Section<'a>>, tag: u16) -> CoreResult<Section<'a>> {
    sections
        .get(&tag)
        .copied()
        .ok_or_else(|| CoreError::protocol(format!("missing required section {tag}")))
}

fn decode_container<'a>(
    data: &'a [u8],
    magic: &[u8; 8],
    required: &BTreeSet<u16>,
) -> CoreResult<BTreeMap<u16, Section<'a>>> {
    let minimum = HEADER_SIZE + CHECKSUM_SIZE;
    if data.len() < minimum {
        return Err(CoreError::protocol(format!(
            "payload is shorter than {minimum} bytes"
        )));
    }
    if &data[..8] != magic {
        return Err(CoreError::protocol("invalid protocol magic"));
    }
    let major = read_u16(data, 8)?;
    if major != IR_MAJOR {
        return Err(CoreError::protocol(format!(
            "unsupported protocol major {major}"
        )));
    }
    let section_count = read_u32(data, 12)? as usize;
    if section_count > MAX_SECTIONS {
        return Err(CoreError::protocol("too many protocol sections"));
    }
    let directory_bytes = section_count
        .checked_mul(DIRECTORY_ENTRY_SIZE)
        .and_then(|value| value.checked_add(HEADER_SIZE))
        .ok_or_else(|| CoreError::protocol("section directory size overflow"))?;
    let checksum_start = data
        .len()
        .checked_sub(CHECKSUM_SIZE)
        .ok_or_else(|| CoreError::protocol("missing protocol checksum"))?;
    if directory_bytes > checksum_start {
        return Err(CoreError::protocol("truncated section directory"));
    }
    let expected_checksum = Blake2b256::digest(&data[directory_bytes..checksum_start]);
    if expected_checksum.as_slice() != &data[checksum_start..] {
        return Err(CoreError::protocol("invalid BLAKE2b-256 section checksum"));
    }

    let mut sections = BTreeMap::new();
    let mut previous_tag = None;
    let mut expected_offset = directory_bytes;
    for index in 0..section_count {
        let entry = HEADER_SIZE + index * DIRECTORY_ENTRY_SIZE;
        let tag = read_u16(data, entry)?;
        if previous_tag.is_some_and(|previous| tag <= previous) {
            return Err(CoreError::protocol(
                "section tags are not strictly increasing",
            ));
        }
        previous_tag = Some(tag);
        let offset = usize_from_u64(read_u64(data, entry + 2)?, "section offset")?;
        let length = usize_from_u64(read_u64(data, entry + 10)?, "section length")?;
        let count = read_u64(data, entry + 18)?;
        if offset != expected_offset {
            return Err(CoreError::protocol("section offsets are not contiguous"));
        }
        let end = offset
            .checked_add(length)
            .ok_or_else(|| CoreError::protocol("section end overflow"))?;
        if end > checksum_start {
            return Err(CoreError::protocol("section exceeds payload bounds"));
        }
        if !required.contains(&tag) && tag < OPTIONAL_TAG_START {
            return Err(CoreError::protocol(format!(
                "unknown required section tag {tag}"
            )));
        }
        if required.contains(&tag) {
            sections.insert(
                tag,
                Section {
                    count,
                    payload: &data[offset..end],
                },
            );
        }
        expected_offset = end;
    }
    if expected_offset != checksum_start {
        return Err(CoreError::protocol("gap or trailing protocol section data"));
    }
    if !required.iter().all(|tag| sections.contains_key(tag)) {
        return Err(CoreError::protocol(
            "one or more required sections are missing",
        ));
    }
    Ok(sections)
}

fn decode_entities(
    sections: &BTreeMap<u16, Section<'_>>,
    kinds_tag: u16,
    offsets_tag: u16,
    bytes_tag: u16,
) -> CoreResult<Vec<Entity>> {
    let kinds = decode_u8(section(sections, kinds_tag)?)?;
    let iris = decode_byte_csr(
        section(sections, offsets_tag)?,
        section(sections, bytes_tag)?,
    )?;
    if kinds.len() != iris.len() {
        return Err(CoreError::protocol("entity kind and IRI counts differ"));
    }
    kinds
        .into_iter()
        .zip(iris)
        .enumerate()
        .map(|(index, (kind, bytes))| {
            let iri = std::str::from_utf8(bytes)
                .map_err(|_| CoreError::protocol(format!("entity IRI {index} is not valid UTF-8")))?
                .to_owned();
            if iri.is_empty() {
                return Err(CoreError::protocol("entity IRI must be nonempty"));
            }
            Ok(Entity {
                kind: EntityKind::try_from(kind)?,
                iri,
            })
        })
        .collect()
}

fn validate_entities(entities: &[Entity], predefined: bool) -> CoreResult<()> {
    for pair in entities.windows(2) {
        let first = (pair[0].kind, pair[0].iri.as_bytes());
        let second = (pair[1].kind, pair[1].iri.as_bytes());
        if first >= second {
            return Err(CoreError::protocol(
                "entities must be strictly sorted and unique",
            ));
        }
    }
    if predefined {
        for &(kind, iri) in &[
            (EntityKind::Class, OWL_NOTHING_IRI),
            (EntityKind::Class, OWL_THING_IRI),
            (EntityKind::ObjectProperty, OWL_BOTTOM_OBJECT_PROPERTY_IRI),
            (EntityKind::ObjectProperty, OWL_TOP_OBJECT_PROPERTY_IRI),
        ] {
            if !entities
                .iter()
                .any(|entity| entity.kind == kind && entity.iri == iri)
            {
                return Err(CoreError::protocol(format!(
                    "compiled ontology is missing predefined entity {iri}"
                )));
            }
        }
    }
    Ok(())
}

fn decode_expressions(
    sections: &BTreeMap<u16, Section<'_>>,
    tags_tag: u16,
    argument_offsets_tag: u16,
    arguments_tag: u16,
    payload_offsets_tag: u16,
    payload_tag: u16,
    entities: &[Entity],
) -> CoreResult<Vec<Expression>> {
    let tags = decode_u8(section(sections, tags_tag)?)?;
    let arguments = decode_csr(
        section(sections, argument_offsets_tag)?,
        section(sections, arguments_tag)?,
    )?;
    let payloads = decode_byte_csr(
        section(sections, payload_offsets_tag)?,
        section(sections, payload_tag)?,
    )?;
    if tags.len() != arguments.len() || tags.len() != payloads.len() {
        return Err(CoreError::protocol(
            "expression tag, argument, and payload counts differ",
        ));
    }
    let expressions = tags
        .into_iter()
        .zip(arguments)
        .zip(payloads)
        .map(|((tag, arguments), payload)| {
            Ok(Expression {
                tag: ExpressionTag::try_from(tag)?,
                arguments,
                payload: payload.to_vec(),
            })
        })
        .collect::<CoreResult<Vec<_>>>()?;
    validate_expressions(&expressions, entities)?;
    Ok(expressions)
}

fn validate_expressions(expressions: &[Expression], entities: &[Entity]) -> CoreResult<()> {
    let mut keys = BTreeSet::new();
    let mut dependents = vec![Vec::<usize>::new(); expressions.len()];
    let mut remaining = vec![0_usize; expressions.len()];
    let mut available = BinaryHeap::<Reverse<(Expression, usize)>>::new();
    for (index, expression) in expressions.iter().enumerate() {
        validate_expression(index, expression, entities)?;
        if !keys.insert(expression.clone()) {
            return Err(CoreError::protocol(
                "expressions must be structurally unique",
            ));
        }
        let dependencies = expression_dependencies(expression);
        remaining[index] = dependencies.len();
        if dependencies.is_empty() {
            available.push(Reverse((expression.clone(), index)));
        }
        for dependency in dependencies {
            dependents[dependency].push(index);
        }
    }
    for expected in 0..expressions.len() {
        let Some(Reverse((_key, actual))) = available.pop() else {
            return Err(CoreError::protocol("cyclic expression dependency graph"));
        };
        if actual != expected {
            return Err(CoreError::protocol(
                "expressions violate deterministic topological order",
            ));
        }
        for &dependent in &dependents[actual] {
            remaining[dependent] -= 1;
            if remaining[dependent] == 0 {
                available.push(Reverse((expressions[dependent].clone(), dependent)));
            }
        }
    }
    Ok(())
}

fn validate_expression(
    index: usize,
    expression: &Expression,
    entities: &[Entity],
) -> CoreResult<()> {
    let require_empty_payload = || {
        if expression.payload.is_empty() {
            Ok(())
        } else {
            Err(CoreError::protocol(format!(
                "expression {index} forbids a payload"
            )))
        }
    };
    match expression.tag {
        ExpressionTag::Class => {
            validate_one_entity_argument(expression, entities, EntityKind::Class)?;
            require_empty_payload()
        }
        ExpressionTag::Individual => {
            validate_one_entity_argument(expression, entities, EntityKind::NamedIndividual)?;
            require_empty_payload()
        }
        ExpressionTag::ObjectIntersectionOf => {
            if expression.arguments.len() != 2 {
                return Err(CoreError::protocol(
                    "intersection requires exactly two arguments",
                ));
            }
            validate_dependencies(index, &expression.arguments)?;
            require_empty_payload()
        }
        ExpressionTag::ObjectSomeValuesFrom => {
            if expression.arguments.len() != 2 {
                return Err(CoreError::protocol(
                    "existential requires property and filler arguments",
                ));
            }
            validate_entity_kind(
                expression.arguments[0],
                entities,
                EntityKind::ObjectProperty,
            )?;
            validate_dependencies(index, &expression.arguments[1..])?;
            require_empty_payload()
        }
        ExpressionTag::ObjectHasSelf => {
            validate_one_entity_argument(expression, entities, EntityKind::ObjectProperty)?;
            require_empty_payload()
        }
        ExpressionTag::DataHasValue => {
            validate_one_entity_argument(expression, entities, EntityKind::DataProperty)?;
            if expression.payload.is_empty() {
                Err(CoreError::protocol(
                    "data-has-value expression requires a payload",
                ))
            } else {
                Ok(())
            }
        }
        ExpressionTag::ObjectComplementOf => {
            if expression.arguments.len() != 1 {
                return Err(CoreError::protocol(
                    "complement requires exactly one argument",
                ));
            }
            validate_dependencies(index, &expression.arguments)?;
            require_empty_payload()
        }
        ExpressionTag::ObjectUnionOf => {
            if expression.arguments.len() < 2 {
                return Err(CoreError::protocol("union requires at least two arguments"));
            }
            validate_dependencies(index, &expression.arguments)?;
            require_empty_payload()
        }
    }
}

fn expression_dependencies(expression: &Expression) -> BTreeSet<usize> {
    match expression.tag {
        ExpressionTag::ObjectSomeValuesFrom => BTreeSet::from([expression.arguments[1] as usize]),
        ExpressionTag::ObjectIntersectionOf
        | ExpressionTag::ObjectComplementOf
        | ExpressionTag::ObjectUnionOf => expression
            .arguments
            .iter()
            .map(|&value| value as usize)
            .collect(),
        _ => BTreeSet::new(),
    }
}

fn validate_one_entity_argument(
    expression: &Expression,
    entities: &[Entity],
    expected: EntityKind,
) -> CoreResult<()> {
    if expression.arguments.len() != 1 {
        return Err(CoreError::protocol(
            "named expression requires exactly one entity argument",
        ));
    }
    validate_entity_kind(expression.arguments[0], entities, expected)
}

fn validate_entity_kind(value: u32, entities: &[Entity], expected: EntityKind) -> CoreResult<()> {
    let Some(entity) = entities.get(value as usize) else {
        return Err(CoreError::protocol("entity ID is out of range"));
    };
    if entity.kind != expected {
        return Err(CoreError::protocol(format!(
            "entity {value} has kind {:?}, expected {expected:?}",
            entity.kind
        )));
    }
    Ok(())
}

fn validate_dependencies(index: usize, dependencies: &[u32]) -> CoreResult<()> {
    if dependencies.iter().any(|&value| value as usize >= index) {
        return Err(CoreError::protocol(
            "expression dependencies must precede their parent",
        ));
    }
    Ok(())
}

fn validate_named_expressions(entities: &[Entity], expressions: &[Expression]) -> CoreResult<()> {
    let expected = entities
        .iter()
        .enumerate()
        .filter_map(|(index, entity)| {
            let tag = match entity.kind {
                EntityKind::Class => ExpressionTag::Class,
                EntityKind::NamedIndividual => ExpressionTag::Individual,
                _ => return None,
            };
            Some((tag, index as u32))
        })
        .collect::<BTreeSet<_>>();
    let actual = expressions
        .iter()
        .filter_map(|expression| match expression.tag {
            ExpressionTag::Class | ExpressionTag::Individual => {
                Some((expression.tag, expression.arguments[0]))
            }
            _ => None,
        })
        .collect::<BTreeSet<_>>();
    if actual != expected {
        return Err(CoreError::protocol(
            "named classes and individuals require exactly one expression",
        ));
    }
    Ok(())
}

fn validate_property_chains(chains: &[Vec<u32>], entities: &[Entity]) -> CoreResult<()> {
    for chain in chains {
        if chain.is_empty() {
            return Err(CoreError::protocol("property chains must be nonempty"));
        }
        for &property in chain {
            validate_entity_kind(property, entities, EntityKind::ObjectProperty)?;
        }
    }
    if chains.windows(2).any(|pair| pair[0] >= pair[1]) {
        return Err(CoreError::protocol(
            "property chains must be strictly sorted and unique",
        ));
    }
    Ok(())
}

fn validate_pairs(
    pairs: &[(u32, u32)],
    first_limit: usize,
    second_limit: usize,
    name: &str,
) -> CoreResult<()> {
    if pairs.windows(2).any(|pair| pair[0] >= pair[1]) {
        return Err(CoreError::protocol(format!(
            "{name} must be strictly sorted and unique"
        )));
    }
    if pairs
        .iter()
        .any(|&(first, second)| first as usize >= first_limit || second as usize >= second_limit)
    {
        return Err(CoreError::protocol(format!(
            "{name} contains an out-of-range ID"
        )));
    }
    Ok(())
}

fn validate_disjoint_groups(groups: &[Vec<u32>], expression_count: usize) -> CoreResult<()> {
    if groups.windows(2).any(|pair| pair[0] >= pair[1]) {
        return Err(CoreError::protocol(
            "disjoint groups must be strictly sorted and unique",
        ));
    }
    for group in groups {
        if group.len() < 2 {
            return Err(CoreError::protocol(
                "disjoint groups require at least two positions",
            ));
        }
        if group
            .iter()
            .any(|&expression| expression as usize >= expression_count)
        {
            return Err(CoreError::protocol(
                "disjoint group contains an out-of-range expression",
            ));
        }
    }
    Ok(())
}

fn decode_u8(section: Section<'_>) -> CoreResult<Vec<u8>> {
    let count = count_as_usize(section.count, section.payload.len(), 1, "u8 section")?;
    if section.payload.len() != count {
        return Err(CoreError::protocol("u8 section length mismatch"));
    }
    Ok(section.payload.to_vec())
}

fn decode_u32(section: Section<'_>) -> CoreResult<Vec<u32>> {
    let count = count_as_usize(section.count, section.payload.len(), 4, "u32 section")?;
    (0..count)
        .map(|index| read_u32(section.payload, index * 4))
        .collect()
}

fn decode_u64(section: Section<'_>) -> CoreResult<Vec<u64>> {
    let count = count_as_usize(section.count, section.payload.len(), 8, "u64 section")?;
    (0..count)
        .map(|index| read_u64(section.payload, index * 8))
        .collect()
}

fn decode_pairs(section: Section<'_>) -> CoreResult<Vec<(u32, u32)>> {
    let count = count_as_usize(section.count, section.payload.len(), 8, "pair section")?;
    (0..count)
        .map(|index| {
            let offset = index * 8;
            Ok((
                read_u32(section.payload, offset)?,
                read_u32(section.payload, offset + 4)?,
            ))
        })
        .collect()
}

fn decode_occurrences(section: Section<'_>) -> CoreResult<Vec<Occurrence>> {
    let count = count_as_usize(
        section.count,
        section.payload.len(),
        16,
        "occurrence section",
    )?;
    (0..count)
        .map(|index| {
            let offset = index * 16;
            Ok(Occurrence {
                negative: read_u64(section.payload, offset)?,
                positive: read_u64(section.payload, offset + 8)?,
            })
        })
        .collect()
}

fn decode_offsets(section: Section<'_>) -> CoreResult<Vec<usize>> {
    let count = usize_from_u64(section.count, "CSR row count")?;
    let value_count = count
        .checked_add(1)
        .ok_or_else(|| CoreError::protocol("CSR offset count overflow"))?;
    let expected = value_count
        .checked_mul(8)
        .ok_or_else(|| CoreError::protocol("CSR offset size overflow"))?;
    if expected != section.payload.len() {
        return Err(CoreError::protocol("CSR offset section length mismatch"));
    }
    let offsets = (0..value_count)
        .map(|index| {
            read_u64(section.payload, index * 8)
                .and_then(|value| usize_from_u64(value, "CSR offset"))
        })
        .collect::<CoreResult<Vec<_>>>()?;
    if offsets.first() != Some(&0) || offsets.windows(2).any(|pair| pair[0] > pair[1]) {
        return Err(CoreError::protocol(
            "CSR offsets must begin at zero and be monotone",
        ));
    }
    Ok(offsets)
}

fn decode_csr(offsets: Section<'_>, values: Section<'_>) -> CoreResult<Vec<Vec<u32>>> {
    let offsets = decode_offsets(offsets)?;
    let values = decode_u32(values)?;
    if offsets.last() != Some(&values.len()) {
        return Err(CoreError::protocol(
            "CSR final offset does not match values",
        ));
    }
    Ok(offsets
        .windows(2)
        .map(|pair| values[pair[0]..pair[1]].to_vec())
        .collect())
}

fn decode_byte_csr<'a>(offsets: Section<'a>, values: Section<'a>) -> CoreResult<Vec<&'a [u8]>> {
    let offsets = decode_offsets(offsets)?;
    let count = usize_from_u64(values.count, "byte section count")?;
    if count != values.payload.len() || offsets.last() != Some(&values.payload.len()) {
        return Err(CoreError::protocol(
            "byte CSR final offset/count does not match payload",
        ));
    }
    Ok(offsets
        .windows(2)
        .map(|pair| &values.payload[pair[0]..pair[1]])
        .collect())
}

fn count_as_usize(count: u64, payload_len: usize, width: usize, name: &str) -> CoreResult<usize> {
    let value = usize_from_u64(count, name)?;
    let expected = value
        .checked_mul(width)
        .ok_or_else(|| CoreError::protocol(format!("{name} length overflow")))?;
    if expected != payload_len {
        return Err(CoreError::protocol(format!("{name} length mismatch")));
    }
    Ok(value)
}

fn usize_from_u64(value: u64, name: &str) -> CoreResult<usize> {
    usize::try_from(value).map_err(|_| CoreError::protocol(format!("{name} exceeds usize")))
}

fn read_u16(data: &[u8], offset: usize) -> CoreResult<u16> {
    let bytes = read_array::<2>(data, offset)?;
    Ok(u16::from_le_bytes(bytes))
}

fn read_u32(data: &[u8], offset: usize) -> CoreResult<u32> {
    let bytes = read_array::<4>(data, offset)?;
    Ok(u32::from_le_bytes(bytes))
}

fn read_u64(data: &[u8], offset: usize) -> CoreResult<u64> {
    let bytes = read_array::<8>(data, offset)?;
    Ok(u64::from_le_bytes(bytes))
}

fn read_array<const N: usize>(data: &[u8], offset: usize) -> CoreResult<[u8; N]> {
    let end = offset
        .checked_add(N)
        .ok_or_else(|| CoreError::protocol("integer field offset overflow"))?;
    let bytes = data
        .get(offset..end)
        .ok_or_else(|| CoreError::protocol("truncated integer field"))?;
    bytes
        .try_into()
        .map_err(|_| CoreError::protocol("invalid integer field width"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_short_and_wrong_magic_without_panicking() {
        assert!(Ontology::decode(&[]).is_err());
        let mut payload = vec![0_u8; HEADER_SIZE + CHECKSUM_SIZE];
        payload[..8].copy_from_slice(b"NOTELK!!");
        assert!(Ontology::decode(&payload).is_err());
    }

    #[test]
    fn entity_and_expression_tags_reject_unknown_values() {
        assert!(EntityKind::try_from(6).is_err());
        assert!(ExpressionTag::try_from(8).is_err());
        assert!(QueryIrKind::try_from(2).is_err());
    }
}
