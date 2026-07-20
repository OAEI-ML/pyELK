//! Monotone cached native session orchestrating all core stages.

use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;

use rayon::ThreadPool;
use rayon::prelude::*;

use crate::error::{CoreError, CoreResult};
use crate::ir::{
    EntityKind, ExpressionTag, OWL_BOTTOM_OBJECT_PROPERTY_IRI, OWL_THING_IRI,
    OWL_TOP_OBJECT_PROPERTY_IRI, Ontology, QueryIr,
};
use crate::properties::PropertyClosure;
use crate::query::{QueryEvaluation, decide_entailment, inconsistent_result, unindexed_result};
use crate::reasoning::{ContextSnapshot, PreparedSaturation, SaturationCounters, saturate_roots};
use crate::result::{QueryKind, RawQueryResult, RawRealization, RawTaxonomy};
use crate::taxonomy::{class_taxonomy, named_expressions, object_property_taxonomy, realization};

/// Scalar diagnostics value kept independent of Python objects.
#[derive(Clone, Debug, PartialEq)]
pub enum DiagnosticValue {
    Integer(u64),
    Boolean(bool),
    Text(String),
}

/// Complete native reasoning session over one immutable compiled ontology.
pub struct NativeCoreSession {
    ontology: Arc<Ontology>,
    properties: Arc<PropertyClosure>,
    compiler_digest: [u8; 32],
    compiler_counts: BTreeMap<&'static str, u64>,
    effective_workers: usize,
    pool: Option<ThreadPool>,
    contexts: BTreeMap<u32, ContextSnapshot>,
    counters: SaturationCounters,
    inconsistent: Option<bool>,
    class_taxonomy: Option<RawTaxonomy>,
    object_taxonomy: Option<RawTaxonomy>,
    realization: Option<RawRealization>,
    query_evaluations: BTreeMap<Vec<u8>, QueryEvaluation>,
    query_results: BTreeMap<(Option<Vec<u8>>, QueryKind, bool), RawQueryResult>,
    entailment_results: BTreeMap<Option<Vec<u8>>, bool>,
}

impl NativeCoreSession {
    /// Decode one ontology transfer and prepare the immutable property stage.
    pub fn create(encoded: &[u8], workers: usize) -> CoreResult<Self> {
        Self::from_ontology(Ontology::decode(encoded)?, workers)
    }

    /// Prepare a session from an already validated, Rust-owned compiled ontology.
    ///
    /// The encoded structural compiler uses this constructor so it does not serialize its
    /// private ELK IR merely to invoke the permanent reasoning session.  Validation belongs
    /// to that compiler before publication; the scalar-wire constructor above retains the
    /// defensive IR decoder for compatibility.
    pub fn from_ontology(ontology: Ontology, workers: usize) -> CoreResult<Self> {
        let compiler_digest = ontology.compiler_digest()?;
        let compiler_counts = ontology.compiler_section_counts()?;
        let ontology = Arc::new(ontology);
        let properties = Arc::new(PropertyClosure::build(&ontology)?);
        let effective_workers = if workers == 0 {
            std::thread::available_parallelism().map_or(1, usize::from)
        } else {
            workers
        };
        if effective_workers == 0 {
            return Err(CoreError::invalid("worker count cannot resolve to zero"));
        }
        let pool = if effective_workers == 1 {
            None
        } else {
            Some(
                rayon::ThreadPoolBuilder::new()
                    .num_threads(effective_workers)
                    .thread_name(|index| format!("pyelk-{index}"))
                    .build()
                    .map_err(|error| {
                        CoreError::internal(format!("cannot create worker pool: {error}"))
                    })?,
            )
        };
        Ok(Self {
            ontology,
            properties,
            compiler_digest,
            compiler_counts,
            effective_workers,
            pool,
            contexts: BTreeMap::new(),
            counters: SaturationCounters::default(),
            inconsistent: None,
            class_taxonomy: None,
            object_taxonomy: None,
            realization: None,
            query_evaluations: BTreeMap::new(),
            query_results: BTreeMap::new(),
            entailment_results: BTreeMap::new(),
        })
    }

    /// Effective worker count after resolving `workers=0`.
    pub fn effective_workers(&self) -> usize {
        self.effective_workers
    }

    /// Determine ontology inconsistency without classifying every named class.
    pub fn is_inconsistent(&mut self) -> CoreResult<bool> {
        if let Some(value) = self.inconsistent {
            return Ok(value);
        }
        let thing_entity = self.ontology.entity_id(EntityKind::Class, OWL_THING_IRI)?;
        let thing_root = self
            .ontology
            .named_expression(ExpressionTag::Class, thing_entity)?;
        let individual_roots = self
            .ontology
            .expressions
            .iter()
            .enumerate()
            .filter_map(|(index, expression)| {
                (expression.tag == ExpressionTag::Individual && {
                    let occurrence = self.ontology.expression_occurrences[index];
                    occurrence.negative > 0 || occurrence.positive > 0
                })
                .then_some(index as u32)
            })
            .collect::<Vec<_>>();
        self.ensure_contexts(std::iter::once(thing_root).chain(individual_roots.iter().copied()))?;
        let class_inconsistent = self.contexts[&thing_root].inconsistent
            || individual_roots
                .iter()
                .any(|root| self.contexts[root].inconsistent);
        let top_property = self
            .ontology
            .entity_id(EntityKind::ObjectProperty, OWL_TOP_OBJECT_PROPERTY_IRI)?;
        let bottom_property = self
            .ontology
            .entity_id(EntityKind::ObjectProperty, OWL_BOTTOM_OBJECT_PROPERTY_IRI)?;
        let top_chain = self.properties.singleton_chain(top_property)?;
        let bottom_chain = self.properties.singleton_chain(bottom_property)?;
        let property_inconsistent = self
            .properties
            .super_chains(top_chain)
            .contains(&bottom_chain);
        let value = class_inconsistent || property_inconsistent;
        self.inconsistent = Some(value);
        Ok(value)
    }

    /// Classify all committed named classes.
    pub fn class_taxonomy(&mut self) -> CoreResult<RawTaxonomy> {
        if let Some(value) = &self.class_taxonomy {
            return Ok(value.clone());
        }
        let inconsistent = self.is_inconsistent()?;
        let roots = named_expressions(&self.ontology, ExpressionTag::Class)
            .into_values()
            .collect::<Vec<_>>();
        self.ensure_contexts(roots)?;
        let value = class_taxonomy(&self.ontology, &self.contexts, inconsistent)?;
        self.class_taxonomy = Some(value.clone());
        Ok(value)
    }

    /// Classify all committed named object properties.
    pub fn object_property_taxonomy(&mut self) -> CoreResult<RawTaxonomy> {
        if let Some(value) = &self.object_taxonomy {
            return Ok(value.clone());
        }
        let inconsistent = self.is_inconsistent()?;
        let value = object_property_taxonomy(&self.ontology, &self.properties, inconsistent)?;
        self.object_taxonomy = Some(value.clone());
        Ok(value)
    }

    /// Realize all committed named individuals.
    pub fn realization(&mut self) -> CoreResult<RawRealization> {
        if let Some(value) = &self.realization {
            return Ok(value.clone());
        }
        let inconsistent = self.is_inconsistent()?;
        let taxonomy = self.class_taxonomy()?;
        let roots = named_expressions(&self.ontology, ExpressionTag::Individual)
            .into_values()
            .collect::<Vec<_>>();
        self.ensure_contexts(roots)?;
        let value = realization(&self.ontology, &self.contexts, &taxonomy, inconsistent)?;
        self.realization = Some(value.clone());
        Ok(value)
    }

    /// Evaluate a class-expression query or its exact unindexed fallback.
    pub fn query_class_expression(
        &mut self,
        encoded: Option<&[u8]>,
        kind: QueryKind,
        direct: bool,
    ) -> CoreResult<RawQueryResult> {
        let key = encoded.map(<[u8]>::to_vec);
        let cache_key = (key.clone(), kind, direct);
        if let Some(value) = self.query_results.get(&cache_key) {
            return Ok(value.clone());
        }
        let taxonomy = self.class_taxonomy()?;
        let realized = self.realization()?;
        let value = if self.is_inconsistent()? {
            inconsistent_result(kind, &taxonomy, &realized)
        } else if let Some(payload) = encoded {
            if !self.query_evaluations.contains_key(payload) {
                let query = QueryIr::decode(payload)?;
                self.query_evaluations.insert(
                    payload.to_vec(),
                    QueryEvaluation::new(&self.ontology, query)?,
                );
            }
            self.query_evaluations
                .get_mut(payload)
                .ok_or_else(|| CoreError::internal("query cache insertion failed"))?
                .select(&self.ontology, &taxonomy, &realized, kind, direct)?
        } else {
            unindexed_result(kind, direct, &taxonomy)
        };
        self.query_results.insert(cache_key, value.clone());
        Ok(value)
    }

    /// Decide one normalized entailment query; unsupported `None` is always false.
    pub fn entails(&mut self, encoded: Option<&[u8]>) -> CoreResult<bool> {
        let key = encoded.map(<[u8]>::to_vec);
        if let Some(value) = self.entailment_results.get(&key) {
            return Ok(*value);
        }
        let value = if let Some(payload) = encoded {
            let query = QueryIr::decode(payload)?;
            if self.is_inconsistent()? {
                true
            } else {
                decide_entailment(&self.ontology, &query)?
            }
        } else {
            false
        };
        self.entailment_results.insert(key, value);
        Ok(value)
    }

    /// Encode a bounded, deterministic fixed-point snapshot for differential tests.
    ///
    /// This intentionally remains a private native diagnostic rather than a public result
    /// contract.  It contains only numeric IR identities and never retains a Python object.
    pub fn debug_snapshot(&mut self, realize: bool, limit: usize) -> CoreResult<Vec<u8>> {
        if limit == 0 {
            return Err(CoreError::invalid(
                "debug snapshot limit must be greater than zero",
            ));
        }
        let inconsistent = self.is_inconsistent()?;
        let mut roots = named_expressions(&self.ontology, ExpressionTag::Class)
            .into_values()
            .collect::<Vec<_>>();
        if realize {
            roots
                .extend(named_expressions(&self.ontology, ExpressionTag::Individual).into_values());
        } else {
            roots.extend(self.ontology.expressions.iter().enumerate().filter_map(
                |(index, expression)| {
                    (expression.tag == ExpressionTag::Individual && {
                        let occurrence = self.ontology.expression_occurrences[index];
                        occurrence.negative > 0 || occurrence.positive > 0
                    })
                    .then_some(index as u32)
                },
            ));
        }
        roots.sort_unstable();
        roots.dedup();
        let (debug_contexts, _) = saturate_roots(&self.ontology, &self.properties, &roots)?;

        let mut records = self.properties.chains.len();
        records = records
            .checked_add(self.ontology.entities.len())
            .and_then(|value| value.checked_add(debug_contexts.len()))
            .ok_or_else(|| CoreError::capacity("debug snapshot record count overflow"))?;
        for chain in 0..self.properties.chains.len() {
            records = records
                .checked_add(self.properties.super_chains(chain as u32).len())
                .ok_or_else(|| CoreError::capacity("debug snapshot record count overflow"))?;
        }
        for entity in 0..self.ontology.entities.len() {
            records = records
                .checked_add(self.properties.ranges(entity as u32).len())
                .ok_or_else(|| CoreError::capacity("debug snapshot record count overflow"))?;
        }
        for context in debug_contexts.values() {
            records = records
                .checked_add(context_record_count(context))
                .ok_or_else(|| CoreError::capacity("debug snapshot record count overflow"))?;
        }
        if records > limit {
            return Err(CoreError::capacity(format!(
                "debug snapshot contains {records} records, exceeding limit {limit}"
            )));
        }

        let mut output = Vec::new();
        output.extend_from_slice(b"PYELKDBG");
        output.extend_from_slice(&1_u16.to_le_bytes());
        push_len(&mut output, self.properties.chains.len())?;
        for chain in 0..self.properties.chains.len() {
            push_slice(&mut output, self.properties.super_chains(chain as u32))?;
        }
        push_len(&mut output, self.ontology.entities.len())?;
        for entity in 0..self.ontology.entities.len() {
            push_slice(&mut output, self.properties.ranges(entity as u32))?;
        }
        output.push(u8::from(inconsistent));
        push_len(&mut output, debug_contexts.len())?;
        for (&root, context) in &debug_contexts {
            push_u32(&mut output, root);
            output.push(u8::from(context.inconsistent));
            push_set(&mut output, &context.composed_subsumers)?;
            push_set(&mut output, &context.decomposed_subsumers)?;
            push_map(&mut output, &context.forward_links)?;
            push_map(&mut output, &context.backward_links)?;
            push_map(&mut output, &context.propagations)?;
            push_map(&mut output, &context.disjoint_positions)?;
            push_set(&mut output, &context.initialized_subcontexts)?;
        }
        Ok(output)
    }

    /// Stable scalar diagnostics; never exposes internal Rust handles.
    pub fn diagnostics(&self) -> BTreeMap<String, DiagnosticValue> {
        let stage = if self.realization.is_some() {
            "realized"
        } else if self.class_taxonomy.is_some() {
            "classified"
        } else if self.inconsistent.is_some() {
            "consistency"
        } else {
            "properties"
        };
        let mut diagnostics = BTreeMap::from([
            (
                "cached_class_queries".to_owned(),
                DiagnosticValue::Integer(self.query_evaluations.len() as u64),
            ),
            (
                "cached_entailment_queries".to_owned(),
                DiagnosticValue::Integer(self.entailment_results.len() as u64),
            ),
            (
                "class_taxonomy_cached".to_owned(),
                DiagnosticValue::Boolean(self.class_taxonomy.is_some()),
            ),
            (
                "conclusion_candidates".to_owned(),
                DiagnosticValue::Integer(self.counters.conclusion_candidates),
            ),
            (
                "conclusions_inserted".to_owned(),
                DiagnosticValue::Integer(self.counters.conclusions_inserted),
            ),
            (
                "context_count".to_owned(),
                DiagnosticValue::Integer(self.contexts.len() as u64),
            ),
            (
                "contexts_created".to_owned(),
                DiagnosticValue::Integer(self.counters.contexts_created),
            ),
            (
                "duplicate_candidates".to_owned(),
                DiagnosticValue::Integer(self.counters.duplicate_candidates),
            ),
            (
                "effective_workers".to_owned(),
                DiagnosticValue::Integer(self.effective_workers as u64),
            ),
            (
                "inconsistent_ontology".to_owned(),
                DiagnosticValue::Boolean(self.inconsistent.unwrap_or(false)),
            ),
            (
                "object_property_taxonomy_cached".to_owned(),
                DiagnosticValue::Boolean(self.object_taxonomy.is_some()),
            ),
            (
                "product_candidates".to_owned(),
                DiagnosticValue::Integer(self.counters.product_candidates),
            ),
            (
                "realization_cached".to_owned(),
                DiagnosticValue::Boolean(self.realization.is_some()),
            ),
            (
                "rule_dispatches".to_owned(),
                DiagnosticValue::Integer(self.counters.rule_dispatches),
            ),
            ("stage".to_owned(), DiagnosticValue::Text(stage.to_owned())),
        ]);
        diagnostics.insert(
            "compiler_digest".to_owned(),
            DiagnosticValue::Text(hex_digest(&self.compiler_digest)),
        );
        diagnostics.insert(
            "compiler_source_fingerprint".to_owned(),
            DiagnosticValue::Text(hex_digest(&self.ontology.source_fingerprint)),
        );
        for (name, count) in &self.compiler_counts {
            diagnostics.insert(
                format!("compiler_{name}_count"),
                DiagnosticValue::Integer(*count),
            );
        }
        diagnostics
    }

    /// Read-only ontology reference for core differential tests.
    pub fn ontology(&self) -> &Ontology {
        &self.ontology
    }

    fn ensure_contexts<I>(&mut self, roots: I) -> CoreResult<()>
    where
        I: IntoIterator<Item = u32>,
    {
        let missing = roots
            .into_iter()
            .filter(|root| !self.contexts.contains_key(root))
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect::<Vec<_>>();
        if missing.is_empty() {
            return Ok(());
        }
        let ontology = Arc::clone(&self.ontology);
        let properties = Arc::clone(&self.properties);
        let prepared = PreparedSaturation::new(&ontology, &properties)?;
        let outcomes = if let Some(pool) = &self.pool {
            pool.install(|| {
                missing
                    .par_iter()
                    .map_init(
                        || prepared.workspace(),
                        |workspace, &root| {
                            workspace
                                .run_root(root)
                                .map(|(context, counters)| (root, context, counters))
                        },
                    )
                    .collect::<Vec<_>>()
            })
        } else {
            let mut workspace = prepared.workspace();
            missing
                .iter()
                .map(|&root| {
                    workspace
                        .run_root(root)
                        .map(|(context, counters)| (root, context, counters))
                })
                .collect::<Vec<_>>()
        };
        for outcome in outcomes {
            let (root, context, counters) = outcome?;
            self.contexts.insert(root, context);
            self.accumulate(counters);
        }
        Ok(())
    }

    fn accumulate(&mut self, counters: SaturationCounters) {
        self.counters.contexts_created = self
            .counters
            .contexts_created
            .saturating_add(counters.contexts_created);
        self.counters.conclusion_candidates = self
            .counters
            .conclusion_candidates
            .saturating_add(counters.conclusion_candidates);
        self.counters.conclusions_inserted = self
            .counters
            .conclusions_inserted
            .saturating_add(counters.conclusions_inserted);
        self.counters.duplicate_candidates = self
            .counters
            .duplicate_candidates
            .saturating_add(counters.duplicate_candidates);
        self.counters.rule_dispatches = self
            .counters
            .rule_dispatches
            .saturating_add(counters.rule_dispatches);
        self.counters.product_candidates = self
            .counters
            .product_candidates
            .saturating_add(counters.product_candidates);
    }
}

fn hex_digest(value: &[u8; 32]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(64);
    for byte in value {
        output.push(char::from(HEX[usize::from(byte >> 4)]));
        output.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    output
}

fn context_record_count(context: &ContextSnapshot) -> usize {
    let maps = (
        &context.forward_links,
        &context.backward_links,
        &context.propagations,
        &context.disjoint_positions,
    );
    context.composed_subsumers.len()
        + context.decomposed_subsumers.len()
        + context.initialized_subcontexts.len()
        + maps.0.len()
        + maps.0.values().map(BTreeSet::len).sum::<usize>()
        + maps.1.len()
        + maps.1.values().map(BTreeSet::len).sum::<usize>()
        + maps.2.len()
        + maps.2.values().map(BTreeSet::len).sum::<usize>()
        + maps.3.len()
        + maps.3.values().map(BTreeSet::len).sum::<usize>()
}

fn push_len(output: &mut Vec<u8>, value: usize) -> CoreResult<()> {
    let value = u32::try_from(value)
        .map_err(|_| CoreError::capacity("debug snapshot length exceeds u32"))?;
    push_u32(output, value);
    Ok(())
}

fn push_u32(output: &mut Vec<u8>, value: u32) {
    output.extend_from_slice(&value.to_le_bytes());
}

fn push_slice(output: &mut Vec<u8>, values: &[u32]) -> CoreResult<()> {
    push_len(output, values.len())?;
    for &value in values {
        push_u32(output, value);
    }
    Ok(())
}

fn push_set(output: &mut Vec<u8>, values: &BTreeSet<u32>) -> CoreResult<()> {
    push_len(output, values.len())?;
    for &value in values {
        push_u32(output, value);
    }
    Ok(())
}

fn push_map(output: &mut Vec<u8>, values: &BTreeMap<u32, BTreeSet<u32>>) -> CoreResult<()> {
    push_len(output, values.len())?;
    for (&key, members) in values {
        push_u32(output, key);
        push_set(output, members)?;
    }
    Ok(())
}
