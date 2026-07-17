//! Backend-neutral raw values returned over the packed Python boundary.

use crate::error::{CoreError, CoreResult};

/// Frozen query operation tag shared with the Python backend.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
#[repr(u8)]
pub enum QueryKind {
    Satisfiable = 0,
    EquivalentClasses = 1,
    Subclasses = 2,
    Superclasses = 3,
    Instances = 4,
}

impl TryFrom<u8> for QueryKind {
    type Error = CoreError;

    fn try_from(value: u8) -> CoreResult<Self> {
        match value {
            0 => Ok(Self::Satisfiable),
            1 => Ok(Self::EquivalentClasses),
            2 => Ok(Self::Subclasses),
            3 => Ok(Self::Superclasses),
            4 => Ok(Self::Instances),
            _ => Err(CoreError::invalid(format!("unknown query kind {value}"))),
        }
    }
}

/// Canonical quotient taxonomy with `sub -> super` direct edges.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RawTaxonomy {
    pub nodes: Vec<Vec<u32>>,
    pub direct_edges: Vec<(u32, u32)>,
    pub top: u32,
    pub bottom: u32,
}

/// Canonical individual quotient and minimal named types.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RawRealization {
    pub class_taxonomy: RawTaxonomy,
    pub instance_nodes: Vec<Vec<u32>>,
    pub direct_types: Vec<(u32, u32)>,
}

/// Canonical class-query result in the ontology-plus-fresh numeric namespace.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RawQueryResult {
    pub kind: QueryKind,
    pub boolean: Option<bool>,
    pub nodes: Vec<Vec<u32>>,
}

impl RawQueryResult {
    pub fn boolean(kind: QueryKind, value: bool) -> Self {
        Self {
            kind,
            boolean: Some(value),
            nodes: Vec::new(),
        }
    }

    pub fn nodes(kind: QueryKind, nodes: Vec<Vec<u32>>) -> Self {
        Self {
            kind,
            boolean: None,
            nodes,
        }
    }
}
