//! Packed v1.0 raw-result encoder for the coarse Python boundary.

use blake2::digest::consts::U32;
use blake2::{Blake2b, Digest};

use crate::error::{CoreError, CoreResult};
use crate::ir::Ontology;
use crate::result::{RawQueryResult, RawRealization, RawTaxonomy};

type Blake2b256 = Blake2b<U32>;

const RAW_MAGIC: &[u8; 8] = b"PYELKRAW";
const COMPILER_METADATA_MAGIC: &[u8; 8] = b"PYELKFAC";
const HEADER_SIZE: usize = 16;
const DIRECTORY_ENTRY_SIZE: usize = 26;

#[derive(Debug)]
struct EncodedSection {
    tag: u16,
    count: u64,
    payload: Vec<u8>,
}

/// Encode one canonical taxonomy.
pub fn encode_taxonomy(taxonomy: &RawTaxonomy) -> CoreResult<Vec<u8>> {
    let (offsets, members, member_count) = encode_csr(&taxonomy.nodes)?;
    encode_container(
        RAW_MAGIC,
        vec![
            scalar_u8(1, 0),
            section(2, taxonomy.nodes.len(), offsets)?,
            section(3, member_count, members)?,
            section(
                4,
                taxonomy.direct_edges.len(),
                encode_pairs(&taxonomy.direct_edges),
            )?,
            section(5, 1, encode_pairs(&[(taxonomy.top, taxonomy.bottom)]))?,
        ],
    )
}

/// Encode one canonical realization value.
pub fn encode_realization(value: &RawRealization) -> CoreResult<Vec<u8>> {
    let (class_offsets, class_members, class_member_count) =
        encode_csr(&value.class_taxonomy.nodes)?;
    let (instance_offsets, instance_members, instance_member_count) =
        encode_csr(&value.instance_nodes)?;
    encode_container(
        RAW_MAGIC,
        vec![
            scalar_u8(1, 1),
            section(2, value.class_taxonomy.nodes.len(), class_offsets)?,
            section(3, class_member_count, class_members)?,
            section(
                4,
                value.class_taxonomy.direct_edges.len(),
                encode_pairs(&value.class_taxonomy.direct_edges),
            )?,
            section(
                5,
                1,
                encode_pairs(&[(value.class_taxonomy.top, value.class_taxonomy.bottom)]),
            )?,
            section(6, value.instance_nodes.len(), instance_offsets)?,
            section(7, instance_member_count, instance_members)?,
            section(
                8,
                value.direct_types.len(),
                encode_pairs(&value.direct_types),
            )?,
        ],
    )
}

/// Encode one canonical query result.
pub fn encode_query(value: &RawQueryResult) -> CoreResult<Vec<u8>> {
    let (offsets, members, member_count) = encode_csr(&value.nodes)?;
    let (boolean_count, boolean_payload) = value
        .boolean
        .map_or((0_usize, Vec::new()), |item| (1, vec![u8::from(item)]));
    encode_container(
        RAW_MAGIC,
        vec![
            scalar_u8(1, 2),
            section(2, value.nodes.len(), offsets)?,
            section(3, member_count, members)?,
            scalar_u8(9, value.kind as u8),
            section(10, boolean_count, boolean_payload)?,
        ],
    )
}

/// Encode the bounded facade metadata needed without exposing private compiler IR.
pub fn encode_compiler_metadata(ontology: &Ontology) -> CoreResult<Vec<u8>> {
    let offset_capacity = ontology
        .entities
        .len()
        .checked_add(1)
        .and_then(|count| count.checked_mul(8))
        .ok_or_else(|| CoreError::capacity("compiler metadata IRI offset size overflow"))?;
    let mut iri_offsets = Vec::new();
    iri_offsets
        .try_reserve_exact(offset_capacity)
        .map_err(|_| CoreError::capacity("compiler metadata IRI offset allocation failed"))?;
    let mut iri_bytes = Vec::new();
    iri_offsets.extend_from_slice(&0_u64.to_le_bytes());
    for entity in &ontology.entities {
        iri_bytes
            .try_reserve(entity.iri.len())
            .map_err(|_| CoreError::capacity("compiler metadata IRI allocation failed"))?;
        iri_bytes.extend_from_slice(entity.iri.as_bytes());
        iri_offsets.extend_from_slice(
            &u64::try_from(iri_bytes.len())
                .map_err(|_| CoreError::capacity("compiler metadata IRI bytes exceed u64"))?
                .to_le_bytes(),
        );
    }
    let feature_capacity = ontology
        .feature_counts
        .len()
        .checked_mul(8)
        .ok_or_else(|| CoreError::capacity("compiler metadata feature byte size overflow"))?;
    let mut feature_counts = Vec::new();
    feature_counts
        .try_reserve_exact(feature_capacity)
        .map_err(|_| CoreError::capacity("compiler metadata feature allocation failed"))?;
    for count in &ontology.feature_counts {
        feature_counts.extend_from_slice(&count.to_le_bytes());
    }
    encode_container(
        COMPILER_METADATA_MAGIC,
        vec![
            section(
                1,
                ontology.entities.len(),
                ontology
                    .entities
                    .iter()
                    .map(|entity| entity.kind as u8)
                    .collect(),
            )?,
            section(2, ontology.entities.len(), iri_offsets)?,
            section(3, iri_bytes.len(), iri_bytes)?,
            section(4, ontology.feature_counts.len(), feature_counts)?,
            section(5, 1, ontology.source_fingerprint.to_vec())?,
        ],
    )
}

fn scalar_u8(tag: u16, value: u8) -> EncodedSection {
    EncodedSection {
        tag,
        count: 1,
        payload: vec![value],
    }
}

fn section(tag: u16, count: usize, payload: Vec<u8>) -> CoreResult<EncodedSection> {
    Ok(EncodedSection {
        tag,
        count: u64::try_from(count)
            .map_err(|_| CoreError::capacity("wire section count exceeds u64"))?,
        payload,
    })
}

fn encode_csr(rows: &[Vec<u32>]) -> CoreResult<(Vec<u8>, Vec<u8>, usize)> {
    let mut offsets = Vec::with_capacity((rows.len() + 1) * 8);
    let mut members = Vec::new();
    offsets.extend_from_slice(&0_u64.to_le_bytes());
    let mut count = 0_usize;
    for row in rows {
        count = count
            .checked_add(row.len())
            .ok_or_else(|| CoreError::capacity("wire CSR member count overflow"))?;
        let count_u64 = u64::try_from(count)
            .map_err(|_| CoreError::capacity("wire CSR member count exceeds u64"))?;
        offsets.extend_from_slice(&count_u64.to_le_bytes());
        for &member in row {
            members.extend_from_slice(&member.to_le_bytes());
        }
    }
    Ok((offsets, members, count))
}

fn encode_pairs(values: &[(u32, u32)]) -> Vec<u8> {
    let mut payload = Vec::with_capacity(values.len() * 8);
    for &(first, second) in values {
        payload.extend_from_slice(&first.to_le_bytes());
        payload.extend_from_slice(&second.to_le_bytes());
    }
    payload
}

fn encode_container(magic: &[u8; 8], mut sections: Vec<EncodedSection>) -> CoreResult<Vec<u8>> {
    sections.sort_by_key(|section| section.tag);
    if sections.windows(2).any(|pair| pair[0].tag == pair[1].tag) {
        return Err(CoreError::internal("duplicate raw-result section tag"));
    }
    let directory_size = sections
        .len()
        .checked_mul(DIRECTORY_ENTRY_SIZE)
        .and_then(|size| size.checked_add(HEADER_SIZE))
        .ok_or_else(|| CoreError::capacity("wire directory size overflow"))?;
    let section_count =
        u32::try_from(sections.len()).map_err(|_| CoreError::capacity("too many wire sections"))?;
    let mut directory = Vec::with_capacity(sections.len() * DIRECTORY_ENTRY_SIZE);
    let mut payload = Vec::new();
    let mut offset = directory_size;
    for section in &sections {
        let offset_u64 =
            u64::try_from(offset).map_err(|_| CoreError::capacity("wire offset exceeds u64"))?;
        let length_u64 = u64::try_from(section.payload.len())
            .map_err(|_| CoreError::capacity("wire length exceeds u64"))?;
        directory.extend_from_slice(&section.tag.to_le_bytes());
        directory.extend_from_slice(&offset_u64.to_le_bytes());
        directory.extend_from_slice(&length_u64.to_le_bytes());
        directory.extend_from_slice(&section.count.to_le_bytes());
        offset = offset
            .checked_add(section.payload.len())
            .ok_or_else(|| CoreError::capacity("wire payload size overflow"))?;
        payload.extend_from_slice(&section.payload);
    }
    let checksum = Blake2b256::digest(&payload);
    let mut result = Vec::with_capacity(directory_size + payload.len() + checksum.len());
    result.extend_from_slice(magic);
    result.extend_from_slice(&1_u16.to_le_bytes());
    result.extend_from_slice(&0_u16.to_le_bytes());
    result.extend_from_slice(&section_count.to_le_bytes());
    result.extend_from_slice(&directory);
    result.extend_from_slice(&payload);
    result.extend_from_slice(checksum.as_slice());
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ir::{FEATURE_VECTOR_LENGTH, Ontology};
    use crate::result::QueryKind;

    #[test]
    fn query_boolean_shape_is_stable() {
        let payload = encode_query(&RawQueryResult::boolean(QueryKind::Satisfiable, true)).unwrap();
        assert!(payload.starts_with(RAW_MAGIC));
        assert!(payload.len() > HEADER_SIZE + 32);
    }

    #[test]
    fn compiler_metadata_is_separate_from_private_ir() {
        let ontology = Ontology {
            entities: Vec::new(),
            expressions: Vec::new(),
            expression_occurrences: Vec::new(),
            property_occurrences: Vec::new(),
            property_chains: Vec::new(),
            subclass_axioms: Vec::new(),
            equivalent_class_axioms: Vec::new(),
            disjoint_groups: Vec::new(),
            subproperty_axioms: Vec::new(),
            property_ranges: Vec::new(),
            feature_counts: vec![0; FEATURE_VECTOR_LENGTH],
            source_fingerprint: [7; 32],
        };
        let payload = encode_compiler_metadata(&ontology).unwrap();
        assert!(payload.starts_with(COMPILER_METADATA_MAGIC));
        assert!(!payload.windows(8).any(|window| window == b"PYELKIR\0"));
    }
}
