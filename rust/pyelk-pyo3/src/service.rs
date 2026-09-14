//! Native facade symbols. The validated IR stays owned by its guarded session.
use crate::NativeSession;
use pyelk_core::ir::{
    EntityKind, FEATURE_VECTOR_LENGTH, OWL_BOTTOM_OBJECT_PROPERTY_IRI, OWL_NOTHING_IRI,
    OWL_THING_IRI, OWL_TOP_OBJECT_PROPERTY_IRI,
};
use pyelk_core::{CoreError, CoreResult, Ontology};
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::sync::atomic::{AtomicU64, Ordering};

pub(super) fn validate_metadata(ontology: &Ontology) -> CoreResult<()> {
    if ontology.entities.windows(2).any(|p| p[0] >= p[1])
        || ontology.entities.len() > u32::MAX as usize
        || ontology.feature_counts.len() != FEATURE_VECTOR_LENGTH
    {
        return Err(CoreError::protocol("noncanonical native service metadata"));
    }
    for (kind, iri) in [
        (EntityKind::Class, OWL_NOTHING_IRI),
        (EntityKind::Class, OWL_THING_IRI),
        (EntityKind::ObjectProperty, OWL_BOTTOM_OBJECT_PROPERTY_IRI),
        (EntityKind::ObjectProperty, OWL_TOP_OBJECT_PROPERTY_IRI),
    ] {
        if lookup(ontology, kind, iri).is_none() {
            return Err(CoreError::protocol("native service metadata lacks builtin"));
        }
    }
    Ok(())
}
fn lookup(ontology: &Ontology, kind: EntityKind, iri: &str) -> Option<usize> {
    ontology
        .entities
        .binary_search_by(|r| (r.kind, r.iri.as_str()).cmp(&(kind, iri)))
        .ok()
}

/// No Python constructor: only a successfully compiled native session issues this handle.
#[pyclass(frozen, module = "pyelk._native", name = "_NativeServiceSymbols")]
pub(super) struct NativeServiceSymbols {
    pub(super) session: NativeSession,
    pub(super) records: AtomicU64,
    pub(super) lookups: AtomicU64,
}

#[pymethods]
impl NativeServiceSymbols {
    fn summary(&self, py: Python<'_>) -> PyResult<(usize, Vec<u64>, Py<PyBytes>)> {
        let (count, features, fingerprint) = self.session.detached(py, "symbol_summary", |s| {
            let o = s.ontology();
            Ok((
                o.entities.len(),
                o.feature_counts.clone(),
                o.source_fingerprint,
            ))
        })?;
        Ok((count, features, PyBytes::new(py, &fingerprint).unbind()))
    }
    fn record(&self, py: Python<'_>, id: usize) -> PyResult<(u8, String)> {
        let result = self.session.detached(py, "symbol_record", move |s| {
            let r = s
                .ontology()
                .entities
                .get(id)
                .ok_or_else(|| CoreError::protocol("native symbol ID out of range"))?;
            Ok((r.kind as u8, r.iri.clone()))
        })?;
        self.records.fetch_add(1, Ordering::Relaxed);
        Ok(result)
    }
    fn find(&self, py: Python<'_>, kind: u8, iri: String) -> PyResult<Option<usize>> {
        let result = self.session.detached(py, "symbol_find", move |s| {
            Ok(lookup(s.ontology(), EntityKind::try_from(kind)?, &iri))
        })?;
        self.lookups.fetch_add(1, Ordering::Relaxed);
        Ok(result)
    }
    fn metrics(&self, py: Python<'_>) -> PyResult<(u64, u64)> {
        self.session.detached(py, "symbol_metrics", |_| Ok(()))?;
        Ok((
            self.records.load(Ordering::Relaxed),
            self.lookups.load(Ordering::Relaxed),
        ))
    }
    fn range(&self, py: Python<'_>, kind: u8) -> PyResult<(usize, usize)> {
        self.session.detached(py, "symbol_range", move |s| {
            let k = EntityKind::try_from(kind)?;
            let entities = &s.ontology().entities;
            Ok((
                entities.partition_point(|r| r.kind < k),
                entities.partition_point(|r| r.kind <= k),
            ))
        })
    }
}
