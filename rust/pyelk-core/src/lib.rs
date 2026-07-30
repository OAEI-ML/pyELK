//! Python-free native reasoning engine for pyELK.

#![forbid(unsafe_code)]

pub mod encoded;
pub mod error;
pub mod ir;
pub mod properties;
pub mod query;
pub mod reasoning;
pub mod result;
pub mod session;
pub mod taxonomy;
pub mod wire;

pub use error::{CoreError, CoreResult};
pub use ir::{IR_MAJOR, IR_MINOR, Ontology, QueryIr};
pub use result::{QueryKind, RawQueryResult, RawRealization, RawTaxonomy};
pub use session::{DiagnosticValue, NativeCoreSession};

/// Native implementation identifier handshaken by the Python dispatcher.
pub const IMPLEMENTATION_VERSION: &str = "0.1.0";
