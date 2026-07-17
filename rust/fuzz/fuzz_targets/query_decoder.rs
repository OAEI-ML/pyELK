#![no_main]

use libfuzzer_sys::fuzz_target;
use pyelk_core::QueryIr;

fuzz_target!(|data: &[u8]| {
    let _ = QueryIr::decode(data);
});
