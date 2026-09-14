//! Validated native result envelopes and core-v2 canonical ordering for requested outputs.
use crate::NativeSession;
use pyelk_core::ir::{
    Entity, EntityKind, OWL_BOTTOM_OBJECT_PROPERTY_IRI, OWL_NOTHING_IRI, OWL_THING_IRI,
    OWL_TOP_OBJECT_PROPERTY_IRI,
};
use pyelk_core::taxonomy::{strict_super_closure, transitive_reduction};
use pyelk_core::{
    CoreError, CoreResult, Ontology, QueryKind, RawQueryResult, RawRealization, RawTaxonomy,
};
use pyo3::prelude::*;
use pyo3::types::PyTuple;
use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::sync::Arc;

type Rows = Vec<Vec<u32>>;
type Pairs = Vec<(u32, u32)>;

fn invalid(message: &str) -> CoreError {
    CoreError::protocol(message)
}
fn validate_nodes(rows: &[Vec<u32>]) -> CoreResult<BTreeSet<u32>> {
    let mut seen = BTreeSet::new();
    if rows.windows(2).any(|r| r[0] >= r[1]) {
        return Err(invalid("noncanonical result nodes"));
    }
    for row in rows {
        if row.is_empty() || row.windows(2).any(|r| r[0] >= r[1]) {
            return Err(invalid("noncanonical result members"));
        }
        for &id in row {
            if !seen.insert(id) {
                return Err(invalid("duplicate result member"));
            }
        }
    }
    Ok(seen)
}
fn validate_pairs(
    rows: &[(u32, u32)],
    left: usize,
    right: usize,
    distinct: bool,
) -> CoreResult<()> {
    if rows.windows(2).any(|r| r[0] >= r[1])
        || rows
            .iter()
            .any(|&(a, b)| a as usize >= left || b as usize >= right || (distinct && a == b))
    {
        return Err(invalid("noncanonical result edges"));
    }
    Ok(())
}
fn coverage(ontology: &Ontology, actual: &BTreeSet<u32>, kind: EntityKind) -> CoreResult<()> {
    let expected = ontology
        .entities
        .iter()
        .enumerate()
        .filter(|(_, e)| e.kind == kind)
        .map(|(i, _)| i as u32);
    if !actual.iter().copied().eq(expected) {
        return Err(invalid("result entity coverage mismatch"));
    }
    Ok(())
}
fn reachable(start: u32, edges: &[Vec<u32>]) -> BTreeSet<u32> {
    let mut seen = BTreeSet::new();
    let mut pending = vec![start];
    while let Some(i) = pending.pop() {
        if seen.insert(i) {
            pending.extend(&edges[i as usize]);
        }
    }
    seen
}
pub(super) fn validate_taxonomy(o: &Ontology, t: &RawTaxonomy, kind: EntityKind) -> CoreResult<()> {
    coverage(o, &validate_nodes(&t.nodes)?, kind)?;
    let n = t.nodes.len();
    if n == 0 || t.top as usize >= n || t.bottom as usize >= n {
        return Err(invalid("invalid taxonomy bounds"));
    }
    validate_pairs(&t.direct_edges, n, n, true)?;
    let (top, bottom) = match kind {
        EntityKind::Class => (OWL_THING_IRI, OWL_NOTHING_IRI),
        EntityKind::ObjectProperty => (OWL_TOP_OBJECT_PROPERTY_IRI, OWL_BOTTOM_OBJECT_PROPERTY_IRI),
        _ => return Err(invalid("invalid taxonomy kind")),
    };
    for (index, iri) in [(t.top, top), (t.bottom, bottom)] {
        if !t.nodes[index as usize]
            .iter()
            .any(|&id| o.entities[id as usize].iri == iri)
        {
            return Err(invalid("taxonomy builtin bound mismatch"));
        }
    }
    let mut parents = vec![Vec::new(); n];
    let mut children = vec![Vec::new(); n];
    for &(a, b) in &t.direct_edges {
        parents[a as usize].push(b);
        children[b as usize].push(a);
    }
    if !parents[t.top as usize].is_empty()
        || !children[t.bottom as usize].is_empty()
        || reachable(t.bottom, &parents).len() != n
        || reachable(t.top, &children).len() != n
    {
        return Err(invalid("invalid taxonomy bound reachability"));
    }
    let mut indegrees: Vec<_> = children.iter().map(Vec::len).collect();
    let mut ready: VecDeque<_> = indegrees
        .iter()
        .enumerate()
        .filter(|(_, d)| **d == 0)
        .map(|(i, _)| i)
        .collect();
    let mut visited = 0;
    while let Some(i) = ready.pop_front() {
        visited += 1;
        for &p in &parents[i] {
            indegrees[p as usize] -= 1;
            if indegrees[p as usize] == 0 {
                ready.push_back(p as usize);
            }
        }
    }
    if visited != n {
        return Err(invalid("cyclic taxonomy"));
    }
    let edges = t.direct_edges.iter().copied().collect();
    if transitive_reduction(n, &edges)? != t.direct_edges {
        return Err(invalid("redundant taxonomy edges"));
    }
    Ok(())
}
pub(super) fn validate_realization(o: &Ontology, r: &RawRealization) -> CoreResult<()> {
    validate_taxonomy(o, &r.class_taxonomy, EntityKind::Class)?;
    coverage(
        o,
        &validate_nodes(&r.instance_nodes)?,
        EntityKind::NamedIndividual,
    )?;
    validate_pairs(
        &r.direct_types,
        r.instance_nodes.len(),
        r.class_taxonomy.nodes.len(),
        false,
    )?;
    let mut types = vec![Vec::new(); r.instance_nodes.len()];
    for &(i, c) in &r.direct_types {
        types[i as usize].push(c);
    }
    if types.iter().any(Vec::is_empty) {
        return Err(invalid("untyped realization node"));
    }
    // Same minimality predicate as the public facade, evaluated without Python graph copies.
    let supers = strict_super_closure(&r.class_taxonomy)?;
    if types.iter().any(|row| {
        row.iter().any(|a| {
            row.iter()
                .any(|b| a != b && supers[*a as usize].contains(b))
        })
    }) {
        return Err(invalid("nonminimal realization types"));
    }
    Ok(())
}
fn varint(out: &mut Vec<u8>, mut value: usize) {
    loop {
        let byte = (value & 127) as u8;
        value >>= 7;
        out.push(byte | if value == 0 { 0 } else { 128 });
        if value == 0 {
            break;
        }
    }
}
/// Frozen pyowl-core model-v2 Entity(IRI) framing; differential tests include varint boundaries.
fn entity_key(entity: &Entity) -> Vec<u8> {
    let kind = match entity.kind {
        EntityKind::Class => "class",
        EntityKind::NamedIndividual => "named_individual",
        EntityKind::ObjectProperty => "object_property",
        EntityKind::DataProperty => "data_property",
        EntityKind::Datatype => "datatype",
        EntityKind::AnnotationProperty => "annotation_property",
    };
    let mut iri = vec![1, 2];
    varint(&mut iri, entity.iri.len());
    iri.extend(entity.iri.as_bytes());
    let mut key = vec![2, 5];
    varint(&mut key, kind.len());
    key.extend(kind.as_bytes());
    key.push(1);
    varint(&mut key, iri.len());
    key.extend(iri);
    key
}
fn entity<'a>(o: &'a Ontology, fresh: &'a [Entity], id: u32) -> CoreResult<&'a Entity> {
    o.entities
        .get(id as usize)
        .or_else(|| fresh.get((id as usize).wrapping_sub(o.entities.len())))
        .ok_or_else(|| invalid("result entity ID outside source/query namespace"))
}
fn order_rows(
    o: &Ontology,
    fresh: &[Entity],
    rows: &[Vec<u32>],
    kind: EntityKind,
) -> CoreResult<(Rows, Vec<u32>)> {
    let mut keys = BTreeMap::new();
    for row in rows {
        for &id in row {
            let e = entity(o, fresh, id)?;
            if e.kind != kind {
                return Err(invalid("result entity kind mismatch"));
            }
            keys.entry(id).or_insert_with(|| entity_key(e));
        }
    }
    let mut indexed: Vec<_> = rows.iter().cloned().enumerate().collect();
    for (_, row) in &mut indexed {
        row.sort_by(|a, b| keys[a].cmp(&keys[b]));
    }
    indexed.sort_by(|(_, a), (_, b)| a.iter().map(|i| &keys[i]).cmp(b.iter().map(|i| &keys[i])));
    let mut mapping = vec![0; rows.len()];
    for (new, (old, _)) in indexed.iter().enumerate() {
        mapping[*old] = new as u32;
    }
    Ok((indexed.into_iter().map(|(_, row)| row).collect(), mapping))
}
fn remap_pairs(pairs: &[(u32, u32)], left: &[u32], right: &[u32]) -> Pairs {
    let mut rows: Vec<_> = pairs
        .iter()
        .map(|&(a, b)| (left[a as usize], right[b as usize]))
        .collect();
    rows.sort_unstable();
    rows
}
fn ordered_taxonomy(
    o: &Ontology,
    t: &RawTaxonomy,
    kind: EntityKind,
) -> CoreResult<(RawTaxonomy, Vec<u32>)> {
    let (nodes, map) = order_rows(o, &[], &t.nodes, kind)?;
    Ok((
        RawTaxonomy {
            nodes,
            direct_edges: remap_pairs(&t.direct_edges, &map, &map),
            top: map[t.top as usize],
            bottom: map[t.bottom as usize],
        },
        map,
    ))
}

pub(super) enum ValidatedResult {
    Taxonomy(RawTaxonomy, RawTaxonomy),
    Realization(RawRealization, RawRealization),
    Query(RawQueryResult, Rows),
}
impl ValidatedResult {
    pub(super) fn taxonomy(o: &Ontology, t: RawTaxonomy, kind: EntityKind) -> CoreResult<Self> {
        validate_taxonomy(o, &t, kind)?;
        let (p, _) = ordered_taxonomy(o, &t, kind)?;
        Ok(Self::Taxonomy(t, p))
    }
    pub(super) fn realization(o: &Ontology, r: RawRealization) -> CoreResult<Self> {
        validate_realization(o, &r)?;
        let (t, tm) = ordered_taxonomy(o, &r.class_taxonomy, EntityKind::Class)?;
        let (nodes, im) = order_rows(o, &[], &r.instance_nodes, EntityKind::NamedIndividual)?;
        let pairs = remap_pairs(&r.direct_types, &im, &tm);
        Ok(Self::Realization(
            r,
            RawRealization {
                class_taxonomy: t,
                instance_nodes: nodes,
                direct_types: pairs,
            },
        ))
    }
    pub(super) fn query(
        o: &Ontology,
        q: RawQueryResult,
        fresh: &[Entity],
        kind: EntityKind,
    ) -> CoreResult<Self> {
        validate_nodes(&q.nodes)?;
        if (q.kind == QueryKind::Satisfiable && (q.boolean.is_none() || !q.nodes.is_empty()))
            || (q.kind != QueryKind::Satisfiable && q.boolean.is_some())
        {
            return Err(invalid("invalid native query shape"));
        }
        if q.kind == QueryKind::EquivalentClasses && q.nodes.len() > 1 {
            return Err(invalid("multiple equivalence result nodes"));
        }
        let (p, _) = order_rows(o, fresh, &q.nodes, kind)?;
        Ok(Self::Query(q, p))
    }
}
fn rows<'py>(py: Python<'py>, v: &[Vec<u32>]) -> PyResult<Bound<'py, PyTuple>> {
    PyTuple::new(
        py,
        v.iter()
            .map(|r| PyTuple::new(py, r))
            .collect::<PyResult<Vec<_>>>()?,
    )
}
fn pairs<'py>(py: Python<'py>, v: &[(u32, u32)]) -> PyResult<Bound<'py, PyTuple>> {
    PyTuple::new(py, v.iter().map(|&(a, b)| (a, b)))
}
fn taxonomy<'py>(py: Python<'py>, v: &RawTaxonomy) -> PyResult<Bound<'py, PyTuple>> {
    PyTuple::new(
        py,
        [
            rows(py, &v.nodes)?.into_any(),
            pairs(py, &v.direct_edges)?.into_any(),
            v.top.into_pyobject(py)?.into_any(),
            v.bottom.into_pyobject(py)?.into_any(),
        ],
    )
}
fn realization<'py>(py: Python<'py>, v: &RawRealization) -> PyResult<Bound<'py, PyTuple>> {
    PyTuple::new(
        py,
        [
            taxonomy(py, &v.class_taxonomy)?.into_any(),
            rows(py, &v.instance_nodes)?.into_any(),
            pairs(py, &v.direct_types)?.into_any(),
        ],
    )
}

/// Opaque session-bound validation result; no Python constructor or writable payload.
#[pyclass(frozen, module = "pyelk._native", name = "_NativeServiceResult")]
pub(super) struct NativeServiceResult {
    session: NativeSession,
    raw: Py<PyTuple>,
    public: Py<PyTuple>,
}
impl NativeServiceResult {
    pub(super) fn publish(py: Python<'_>, s: &NativeSession, v: ValidatedResult) -> PyResult<Self> {
        let (raw, public) = match v {
            ValidatedResult::Taxonomy(r, p) => (taxonomy(py, &r)?, taxonomy(py, &p)?),
            ValidatedResult::Realization(r, p) => (realization(py, &r)?, realization(py, &p)?),
            ValidatedResult::Query(q, p) => {
                let raw = PyTuple::new(
                    py,
                    [
                        (q.kind as u8).into_pyobject(py)?.into_any(),
                        q.boolean.into_pyobject(py)?.into_any(),
                        rows(py, &q.nodes)?.into_any(),
                    ],
                )?;
                (raw, rows(py, &p)?)
            }
        };
        Ok(Self {
            session: NativeSession {
                inner: Arc::clone(&s.inner),
                creator_pid: s.creator_pid,
            },
            raw: raw.unbind(),
            public: public.unbind(),
        })
    }
}
#[pymethods]
impl NativeServiceResult {
    fn payloads(
        &self,
        py: Python<'_>,
        session: &NativeSession,
    ) -> PyResult<(Py<PyTuple>, Py<PyTuple>)> {
        self.session.detached(py, "validated_result", |_| Ok(()))?;
        if !Arc::ptr_eq(&self.session.inner, &session.inner) {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "native result belongs to a different session",
            ));
        }
        Ok((self.raw.clone_ref(py), self.public.clone_ref(py)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn ontology() -> Ontology {
        Ontology {
            entities: vec![OWL_NOTHING_IRI, OWL_THING_IRI, "urn:a"]
                .into_iter()
                .map(|iri| Entity {
                    kind: EntityKind::Class,
                    iri: iri.into(),
                })
                .collect(),
            expressions: vec![],
            expression_occurrences: vec![],
            property_occurrences: vec![],
            property_chains: vec![],
            subclass_axioms: vec![],
            equivalent_class_axioms: vec![],
            disjoint_groups: vec![],
            subproperty_axioms: vec![],
            property_ranges: vec![],
            feature_counts: vec![0; 79],
            source_fingerprint: [0; 32],
        }
    }
    fn taxonomy() -> RawTaxonomy {
        RawTaxonomy {
            nodes: vec![vec![0], vec![1], vec![2]],
            direct_edges: vec![(0, 2), (2, 1)],
            top: 1,
            bottom: 0,
        }
    }
    #[test]
    fn native_taxonomy_validation_rejects_malformed_coverage_graph_and_bounds() {
        let o = ontology();
        let t = taxonomy();
        validate_taxonomy(&o, &t, EntityKind::Class).unwrap();
        for mode in 0..7 {
            let mut bad = t.clone();
            match mode {
                0 => bad.nodes[2] = vec![0],
                1 => bad.nodes[2] = vec![999],
                2 => bad.top = 2,
                3 => bad.direct_edges = vec![(0, 1), (0, 2), (2, 1)],
                4 => bad.direct_edges = vec![(0, 2), (1, 2), (2, 1)],
                5 => bad.direct_edges = vec![(0, 2)],
                _ => bad.nodes[2] = vec![2, 2],
            }
            assert!(
                validate_taxonomy(&o, &bad, EntityKind::Class).is_err(),
                "mode {mode}"
            );
        }
    }
    #[test]
    fn native_result_namespace_checks_punning_freshness_and_query_shape() {
        let o = ontology();
        let fresh = vec![Entity {
            kind: EntityKind::Class,
            iri: "urn:new".into(),
        }];
        assert!(
            ValidatedResult::query(
                &o,
                RawQueryResult::nodes(QueryKind::EquivalentClasses, vec![vec![3]]),
                &fresh,
                EntityKind::Class
            )
            .is_ok()
        );
        for rows in [
            vec![vec![4]],
            vec![vec![0], vec![0]],
            vec![vec![2], vec![1]],
        ] {
            assert!(
                ValidatedResult::query(
                    &o,
                    RawQueryResult::nodes(QueryKind::Subclasses, rows),
                    &fresh,
                    EntityKind::Class
                )
                .is_err()
            );
        }
        assert!(
            ValidatedResult::query(
                &o,
                RawQueryResult::nodes(QueryKind::Subclasses, vec![vec![0]]),
                &fresh,
                EntityKind::ObjectProperty
            )
            .is_err()
        );
        assert!(
            ValidatedResult::query(
                &o,
                RawQueryResult::nodes(QueryKind::Satisfiable, vec![]),
                &fresh,
                EntityKind::Class
            )
            .is_err()
        );
    }
    #[test]
    fn core_v2_entity_order_preserves_framing_instead_of_iri_order() {
        let short = Entity {
            kind: EntityKind::Class,
            iri: "urn:z".into(),
        };
        let longer = Entity {
            kind: EntityKind::Class,
            iri: "urn:aa".into(),
        };
        assert!(short.iri > longer.iri);
        assert!(entity_key(&short) < entity_key(&longer));
        assert_eq!(
            entity_key(&short),
            b"\x02\x05\x05class\x01\x08\x01\x02\x05urn:z"
        );
    }
}
