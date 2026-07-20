#![no_main]

use libfuzzer_sys::fuzz_target;
use pyelk_core::encoded::{
    EncodedColumns, EncodedLimits, EncodedUnsupportedPolicy, compile_encoded_hierarchy_with_policy,
};

const COLUMN_COUNT: usize = 11;
const SPLIT_COUNT: usize = COLUMN_COUNT - 1;
const HEADER_BYTES: usize = SPLIT_COUNT * 4;

fn columns(data: &[u8]) -> EncodedColumns<&[u8]> {
    let header_length = data.len().min(HEADER_BYTES);
    let (header, payload) = data.split_at(header_length);
    let mut offsets = [0_usize; SPLIT_COUNT];
    for (index, offset) in offsets.iter_mut().enumerate() {
        let start = index * 4;
        let bytes = [
            header.get(start).copied().unwrap_or(0),
            header.get(start + 1).copied().unwrap_or(0),
            header.get(start + 2).copied().unwrap_or(0),
            header.get(start + 3).copied().unwrap_or(0),
        ];
        *offset =
            usize::try_from(u32::from_le_bytes(bytes)).unwrap_or(usize::MAX) % (payload.len() + 1);
    }
    offsets.sort_unstable();

    let mut values = [&[][..]; COLUMN_COUNT];
    let mut start = 0;
    for (index, end) in offsets.into_iter().enumerate() {
        values[index] = &payload[start..end];
        start = end;
    }
    values[SPLIT_COUNT] = &payload[start..];
    EncodedColumns {
        root_kinds: values[0],
        root_ids: values[1],
        node_tags: values[2],
        node_field_offsets: values[3],
        field_kinds: values[4],
        field_values: values[5],
        field_lengths: values[6],
        item_kinds: values[7],
        item_values: values[8],
        item_lengths: values[9],
        scalar_bytes: values[10],
    }
}

fuzz_target!(|data: &[u8]| {
    let limits = EncodedLimits {
        max_roots: 4_096,
        max_nodes: 4_096,
        max_fields: 16_384,
        max_items: 16_384,
        max_scalar_bytes: 65_536,
        max_work: 1_000_000,
    };
    let input = columns(data);
    let _ = compile_encoded_hierarchy_with_policy(input, limits, EncodedUnsupportedPolicy::Ignore);
    let _ = compile_encoded_hierarchy_with_policy(input, limits, EncodedUnsupportedPolicy::Error);
});
