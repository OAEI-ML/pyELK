#![no_main]

use libfuzzer_sys::fuzz_target;
use pyelk_core::Ontology;

fuzz_target!(|data: &[u8]| {
    let _ = Ontology::decode(data);
});
