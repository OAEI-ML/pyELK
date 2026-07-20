//! Defensive structural validation for pyowl-core encoded-view schema 1.
//!
//! This module borrows the eleven public columns directly.  It establishes the
//! shape, bounds, scalar arena, root-category, reachability, and acyclic graph
//! invariants before the ELK-specific compiler allocates permanent IR.  It does
//! not advertise schema support; semantic compilation remains a separate gate.

use crate::error::{CoreError, CoreResult};

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

/// Borrowed public encoded-view columns, each exposed as read-only bytes.
#[derive(Clone, Copy, Debug)]
pub struct EncodedColumns<'a> {
    pub root_kinds: &'a [u8],
    pub root_ids: &'a [u8],
    pub node_tags: &'a [u8],
    pub node_field_offsets: &'a [u8],
    pub field_kinds: &'a [u8],
    pub field_values: &'a [u8],
    pub field_lengths: &'a [u8],
    pub item_kinds: &'a [u8],
    pub item_values: &'a [u8],
    pub item_lengths: &'a [u8],
    pub scalar_bytes: &'a [u8],
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
            max_scalar_bytes: 8 * 1024 * 1024 * 1024,
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
    fn new(node: usize, columns: &EncodedColumns<'_>) -> CoreResult<Self> {
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
pub fn validate_columns(
    columns: EncodedColumns<'_>,
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
        let arity = constructor_arity(tag)
            .ok_or_else(|| CoreError::protocol(format!("unsupported encoded node tag {tag}")))?;
        if end - start != arity {
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
    for field in 0..field_count {
        claim_work(&mut work, 1, limits.max_work)?;
        let kind = columns.field_kinds[field];
        let value = usize_at(columns.field_values, field, "field value")?;
        let length = usize_at(columns.field_lengths, field, "field length")?;
        match kind {
            COMPONENT_SET | COMPONENT_SEQUENCE => {
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
                for item in value..end {
                    claim_work(&mut work, 1, limits.max_work)?;
                    validate_leaf_component(
                        columns.item_kinds[item],
                        usize_at(columns.item_values, item, "item value")?,
                        usize_at(columns.item_lengths, item, "item length")?,
                        node_count,
                        columns.scalar_bytes,
                        &mut scalar_cursor,
                    )?;
                }
                item_cursor = end;
            }
            _ => validate_leaf_component(
                kind,
                value,
                length,
                node_count,
                columns.scalar_bytes,
                &mut scalar_cursor,
            )?,
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

    let mut previous_root = None;
    for root in 0..root_count {
        claim_work(&mut work, 1, limits.max_work)?;
        let kind = columns.root_kinds[root];
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

    validate_reachability(&columns, root_count, node_count, &mut work, limits.max_work)?;
    Ok(ValidatedEncodedColumns {
        root_count,
        node_count,
        field_count,
        item_count,
        scalar_bytes: columns.scalar_bytes.len(),
        work,
    })
}

fn validate_reachability(
    columns: &EncodedColumns<'_>,
    root_count: usize,
    node_count: usize,
    work: &mut u64,
    max_work: u64,
) -> CoreResult<()> {
    let mut states = Vec::new();
    states
        .try_reserve_exact(node_count)
        .map_err(|_| CoreError::capacity("encoded reachability state allocation failed"))?;
    states.resize(node_count, 0_u8);
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
        stack.push(DfsFrame::new(node, columns)?);
        while let Some(frame) = stack.last_mut() {
            claim_work(work, 1, max_work)?;
            let child = if frame.item_cursor < frame.item_end {
                let item = frame.item_cursor;
                frame.item_cursor += 1;
                (columns.item_kinds[item] == COMPONENT_NODE)
                    .then(|| node_id_at(columns.item_values, item, "item node ID"))
                    .transpose()?
            } else if frame.field_cursor < frame.field_end {
                let field = frame.field_cursor;
                frame.field_cursor += 1;
                match columns.field_kinds[field] {
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
                continue;
            };
            let Some(child) = child else {
                continue;
            };
            let child = node_index(child, node_count)?;
            match states[child] {
                0 => {
                    states[child] = 1;
                    stack.push(DfsFrame::new(child, columns)?);
                }
                1 => return Err(CoreError::protocol("encoded structural graph is cyclic")),
                2 => {}
                _ => return Err(CoreError::internal("invalid encoded DFS state")),
            }
        }
    }
    if states.iter().any(|state| *state != 2) {
        return Err(CoreError::protocol(
            "encoded structural graph contains unreachable nodes",
        ));
    }
    Ok(())
}

fn validate_leaf_component(
    kind: u8,
    value: usize,
    length: usize,
    node_count: usize,
    scalars: &[u8],
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
            let payload = scalars
                .get(value..end)
                .ok_or_else(|| CoreError::protocol("encoded scalar component is out of bounds"))?;
            match kind {
                COMPONENT_TEXT => {
                    std::str::from_utf8(payload).map_err(|_| {
                        CoreError::protocol("encoded text component is not valid UTF-8")
                    })?;
                }
                COMPONENT_INTEGER => {
                    if payload.is_empty()
                        || (payload.len() > 1 && payload.last().copied() == Some(0))
                    {
                        return Err(CoreError::protocol(
                            "encoded integer component is not minimal little-endian",
                        ));
                    }
                }
                COMPONENT_ENUM => {
                    if payload.is_empty() || !payload.is_ascii() {
                        return Err(CoreError::protocol(
                            "encoded enum component must be nonempty ASCII",
                        ));
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

fn constructor_arity(tag: u16) -> Option<usize> {
    match tag {
        1 | 10 | 11 | 21..=24 | 30..=33 | 37 | 140 => Some(1),
        2
        | 3
        | 20
        | 25
        | 34..=36
        | 41..=43
        | 60
        | 62..=63
        | 71..=72
        | 76..=82
        | 91..=92
        | 95
        | 110..=111
        | 141..=142
        | 146..=147 => Some(2),
        4
        | 5
        | 38..=40
        | 44..=46
        | 61
        | 64
        | 70
        | 73..=75
        | 90
        | 93..=94
        | 100
        | 112
        | 121..=123
        | 143..=145
        | 148 => Some(3),
        101 | 113..=116 | 120 => Some(4),
        _ => None,
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

fn aligned_count(bytes: &[u8], width: usize, name: &str) -> CoreResult<usize> {
    if bytes.len() % width != 0 {
        return Err(CoreError::protocol(format!(
            "encoded {name} is not aligned to {width} bytes"
        )));
    }
    Ok(bytes.len() / width)
}

fn u16_at(bytes: &[u8], index: usize, name: &str) -> CoreResult<u16> {
    let start = index
        .checked_mul(2)
        .ok_or_else(|| CoreError::capacity(format!("encoded {name} offset overflow")))?;
    let raw: [u8; 2] = bytes
        .get(start..start + 2)
        .and_then(|value| value.try_into().ok())
        .ok_or_else(|| CoreError::protocol(format!("encoded {name} is truncated")))?;
    Ok(u16::from_le_bytes(raw))
}

fn u32_at(bytes: &[u8], index: usize, name: &str) -> CoreResult<u32> {
    let start = index
        .checked_mul(4)
        .ok_or_else(|| CoreError::capacity(format!("encoded {name} offset overflow")))?;
    let raw: [u8; 4] = bytes
        .get(start..start + 4)
        .and_then(|value| value.try_into().ok())
        .ok_or_else(|| CoreError::protocol(format!("encoded {name} is truncated")))?;
    Ok(u32::from_le_bytes(raw))
}

fn u64_at(bytes: &[u8], index: usize, name: &str) -> CoreResult<u64> {
    let start = index
        .checked_mul(8)
        .ok_or_else(|| CoreError::capacity(format!("encoded {name} offset overflow")))?;
    let raw: [u8; 8] = bytes
        .get(start..start + 8)
        .and_then(|value| value.try_into().ok())
        .ok_or_else(|| CoreError::protocol(format!("encoded {name} is truncated")))?;
    Ok(u64::from_le_bytes(raw))
}

fn node_id_at(bytes: &[u8], index: usize, name: &str) -> CoreResult<u32> {
    u32::try_from(u64_at(bytes, index, name)?)
        .map_err(|_| CoreError::protocol(format!("encoded {name} exceeds u32")))
}

fn usize_at(bytes: &[u8], index: usize, name: &str) -> CoreResult<usize> {
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

#[cfg(test)]
mod tests {
    use super::*;

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
        fn borrowed(&self) -> EncodedColumns<'_> {
            EncodedColumns {
                root_kinds: &self.root_kinds,
                root_ids: &self.root_ids,
                node_tags: &self.node_tags,
                node_field_offsets: &self.node_field_offsets,
                field_kinds: &self.field_kinds,
                field_values: &self.field_values,
                field_lengths: &self.field_lengths,
                item_kinds: &self.item_kinds,
                item_values: &self.item_values,
                item_lengths: &self.item_lengths,
                scalar_bytes: &self.scalar_bytes,
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

        let mut malformed = declaration();
        malformed.field_values = le64(&[0, 5, 2, 2, 0]);
        assert!(matches!(
            validate_columns(malformed.borrowed(), EncodedLimits::default()),
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
