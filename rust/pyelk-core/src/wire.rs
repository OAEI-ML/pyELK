//! Packed v1.0 raw-result encoder for the coarse Python boundary.

use blake2::digest::consts::U32;
use blake2::{Blake2b, Digest};

use crate::error::{CoreError, CoreResult};
use crate::result::{RawQueryResult, RawRealization, RawTaxonomy};

type Blake2b256 = Blake2b<U32>;

const RAW_MAGIC: &[u8; 8] = b"PYELKRAW";
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
    encode_container(vec![
        scalar_u8(1, 0),
        section(2, taxonomy.nodes.len(), offsets)?,
        section(3, member_count, members)?,
        section(
            4,
            taxonomy.direct_edges.len(),
            encode_pairs(&taxonomy.direct_edges),
        )?,
        section(5, 1, encode_pairs(&[(taxonomy.top, taxonomy.bottom)]))?,
    ])
}

/// Encode one canonical realization value.
pub fn encode_realization(value: &RawRealization) -> CoreResult<Vec<u8>> {
    let (class_offsets, class_members, class_member_count) =
        encode_csr(&value.class_taxonomy.nodes)?;
    let (instance_offsets, instance_members, instance_member_count) =
        encode_csr(&value.instance_nodes)?;
    encode_container(vec![
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
    ])
}

/// Encode one canonical query result.
pub fn encode_query(value: &RawQueryResult) -> CoreResult<Vec<u8>> {
    let (offsets, members, member_count) = encode_csr(&value.nodes)?;
    let (boolean_count, boolean_payload) = value
        .boolean
        .map_or((0_usize, Vec::new()), |item| (1, vec![u8::from(item)]));
    encode_container(vec![
        scalar_u8(1, 2),
        section(2, value.nodes.len(), offsets)?,
        section(3, member_count, members)?,
        scalar_u8(9, value.kind as u8),
        section(10, boolean_count, boolean_payload)?,
    ])
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

fn encode_container(mut sections: Vec<EncodedSection>) -> CoreResult<Vec<u8>> {
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
    result.extend_from_slice(RAW_MAGIC);
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
    use crate::result::QueryKind;

    #[test]
    fn query_boolean_shape_is_stable() {
        let payload = encode_query(&RawQueryResult::boolean(QueryKind::Satisfiable, true)).unwrap();
        assert!(payload.starts_with(RAW_MAGIC));
        assert!(payload.len() > HEADER_SIZE + 32);
    }
}
