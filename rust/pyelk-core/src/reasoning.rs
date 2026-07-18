//! Iterative occurrence-aware class saturation over one demanded root.

use std::collections::{BTreeMap, BTreeSet, HashSet, VecDeque};

use crate::error::{CoreError, CoreResult};
use crate::ir::{ExpressionTag, OWL_NOTHING_IRI, OWL_THING_IRI, Ontology};
use crate::properties::PropertyClosure;

/// Structural conclusion identities used for duplicate suppression.
#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
enum Conclusion {
    ContextInitialization(u32),
    SubContextInitialization {
        destination: u32,
        relation: u32,
    },
    Decomposed {
        destination: u32,
        subsumer: u32,
    },
    Composed {
        destination: u32,
        subsumer: u32,
    },
    ForwardLink {
        destination: u32,
        chain: u32,
        target: u32,
    },
    BackwardLink {
        destination: u32,
        relation: u32,
        source: u32,
    },
    Propagation {
        destination: u32,
        relation: u32,
        carry: u32,
    },
    DisjointSubsumer {
        destination: u32,
        group: u32,
        position: u32,
    },
    Inconsistency(u32),
}

impl Conclusion {
    fn destination(&self) -> u32 {
        match *self {
            Self::ContextInitialization(root) | Self::Inconsistency(root) => root,
            Self::SubContextInitialization { destination, .. }
            | Self::Decomposed { destination, .. }
            | Self::Composed { destination, .. }
            | Self::ForwardLink { destination, .. }
            | Self::BackwardLink { destination, .. }
            | Self::Propagation { destination, .. }
            | Self::DisjointSubsumer { destination, .. } => destination,
        }
    }
}

#[derive(Clone, Debug, Default)]
struct Context {
    root: u32,
    initialized: bool,
    inconsistent: bool,
    composed_subsumers: BTreeSet<u32>,
    decomposed_subsumers: BTreeSet<u32>,
    forward_links: BTreeMap<u32, BTreeSet<u32>>,
    backward_links: BTreeMap<u32, BTreeSet<u32>>,
    propagations: BTreeMap<u32, BTreeSet<u32>>,
    disjoint_positions: BTreeMap<u32, BTreeSet<u32>>,
    initialized_subcontexts: BTreeSet<u32>,
}

impl Context {
    fn new(root: u32) -> Self {
        Self {
            root,
            ..Self::default()
        }
    }

    fn insert(&mut self, conclusion: &Conclusion) {
        match *conclusion {
            Conclusion::ContextInitialization(_) => self.initialized = true,
            Conclusion::SubContextInitialization { relation, .. } => {
                self.initialized_subcontexts.insert(relation);
            }
            Conclusion::Decomposed { subsumer, .. } => {
                self.decomposed_subsumers.insert(subsumer);
            }
            Conclusion::Composed { subsumer, .. } => {
                self.composed_subsumers.insert(subsumer);
            }
            Conclusion::ForwardLink { chain, target, .. } => {
                self.forward_links.entry(chain).or_default().insert(target);
            }
            Conclusion::BackwardLink {
                relation, source, ..
            } => {
                self.backward_links
                    .entry(relation)
                    .or_default()
                    .insert(source);
            }
            Conclusion::Propagation {
                relation, carry, ..
            } => {
                self.propagations.entry(relation).or_default().insert(carry);
            }
            Conclusion::DisjointSubsumer {
                group, position, ..
            } => {
                self.disjoint_positions
                    .entry(group)
                    .or_default()
                    .insert(position);
            }
            Conclusion::Inconsistency(_) => self.inconsistent = true,
        }
    }
}

/// Immutable context facts consumed by taxonomy, realization, and query stages.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ContextSnapshot {
    pub root: u32,
    pub inconsistent: bool,
    pub composed_subsumers: BTreeSet<u32>,
    pub decomposed_subsumers: BTreeSet<u32>,
    pub forward_links: BTreeMap<u32, BTreeSet<u32>>,
    pub backward_links: BTreeMap<u32, BTreeSet<u32>>,
    pub propagations: BTreeMap<u32, BTreeSet<u32>>,
    pub disjoint_positions: BTreeMap<u32, BTreeSet<u32>>,
    pub initialized_subcontexts: BTreeSet<u32>,
}

impl ContextSnapshot {
    fn from_context(context: Context) -> Self {
        Self {
            root: context.root,
            inconsistent: context.inconsistent,
            composed_subsumers: context.composed_subsumers,
            decomposed_subsumers: context.decomposed_subsumers,
            forward_links: context.forward_links,
            backward_links: context.backward_links,
            propagations: context.propagations,
            disjoint_positions: context.disjoint_positions,
            initialized_subcontexts: context.initialized_subcontexts,
        }
    }

    /// Every known subsumer, including the context root itself.
    pub fn subsumers(&self) -> BTreeSet<u32> {
        let mut values = self.composed_subsumers.clone();
        values.extend(&self.decomposed_subsumers);
        values.insert(self.root);
        values
    }
}

/// Deterministic scheduler counters retained for diagnostics and stress tests.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct SaturationCounters {
    pub contexts_created: u64,
    pub conclusion_candidates: u64,
    pub conclusions_inserted: u64,
    pub duplicate_candidates: u64,
    pub rule_dispatches: u64,
    pub product_candidates: u64,
}

struct RuleDispatcher<'a> {
    ontology: &'a Ontology,
    properties: &'a PropertyClosure,
    owl_thing: u32,
    owl_nothing: u32,
    introduce_thing: bool,
    decompose_nothing: bool,
    subclasses: Vec<Vec<u32>>,
    definitions_by_class: Vec<Vec<u32>>,
    classes_by_definition: Vec<Vec<u32>>,
    equivalent_first: Vec<Vec<u32>>,
    equivalent_second: Vec<Vec<u32>>,
    intersections_by_first: Vec<Vec<(u32, u32)>>,
    intersections_by_second: Vec<Vec<(u32, u32)>>,
    unions_by_disjunct: Vec<Vec<(u32, u32)>>,
    existentials_by_filler: Vec<Vec<(u32, u32)>>,
    complements_by_negated: Vec<Vec<u32>>,
    positive_complements: Vec<Vec<u32>>,
    disjoint_by_member: Vec<Vec<(u32, u32)>>,
    told_super_properties: BTreeMap<u32, Vec<u32>>,
}

impl<'a> RuleDispatcher<'a> {
    fn new(ontology: &'a Ontology, properties: &'a PropertyClosure) -> CoreResult<Self> {
        let expression_count = ontology.expressions.len();
        let class_thing = ontology.entity_id(crate::ir::EntityKind::Class, OWL_THING_IRI)?;
        let class_nothing = ontology.entity_id(crate::ir::EntityKind::Class, OWL_NOTHING_IRI)?;
        let owl_thing = ontology.named_expression(ExpressionTag::Class, class_thing)?;
        let owl_nothing = ontology.named_expression(ExpressionTag::Class, class_nothing)?;
        let mut result = Self {
            ontology,
            properties,
            owl_thing,
            owl_nothing,
            introduce_thing: ontology.expression_occurrences[owl_thing as usize].negative > 0,
            decompose_nothing: ontology.expression_occurrences[owl_nothing as usize].positive > 0,
            subclasses: vec![Vec::new(); expression_count],
            definitions_by_class: vec![Vec::new(); expression_count],
            classes_by_definition: vec![Vec::new(); expression_count],
            equivalent_first: vec![Vec::new(); expression_count],
            equivalent_second: vec![Vec::new(); expression_count],
            intersections_by_first: vec![Vec::new(); expression_count],
            intersections_by_second: vec![Vec::new(); expression_count],
            unions_by_disjunct: vec![Vec::new(); expression_count],
            existentials_by_filler: vec![Vec::new(); expression_count],
            complements_by_negated: vec![Vec::new(); expression_count],
            positive_complements: vec![Vec::new(); expression_count],
            disjoint_by_member: vec![Vec::new(); expression_count],
            told_super_properties: BTreeMap::new(),
        };
        for &(sub, super_expression) in &ontology.subclass_axioms {
            result.subclasses[sub as usize].push(super_expression);
        }
        for &(first, second) in &ontology.equivalent_class_axioms {
            if ontology.expressions[first as usize].tag == ExpressionTag::Class {
                result.definitions_by_class[first as usize].push(second);
                result.classes_by_definition[second as usize].push(first);
            } else {
                result.equivalent_first[second as usize].push(first);
                result.equivalent_second[first as usize].push(second);
            }
        }
        for (expression_index, expression) in ontology.expressions.iter().enumerate() {
            let expression_id = expression_index as u32;
            let occurrence = ontology.expression_occurrences[expression_index];
            match expression.tag {
                ExpressionTag::ObjectIntersectionOf if occurrence.negative > 0 => {
                    result.intersections_by_first[expression.arguments[0] as usize]
                        .push((expression.arguments[1], expression_id));
                    result.intersections_by_second[expression.arguments[1] as usize]
                        .push((expression.arguments[0], expression_id));
                }
                ExpressionTag::ObjectUnionOf if occurrence.negative > 0 => {
                    for (position, &argument) in expression.arguments.iter().enumerate() {
                        result.unions_by_disjunct[argument as usize]
                            .push((expression_id, position as u32));
                    }
                }
                ExpressionTag::ObjectSomeValuesFrom if occurrence.negative > 0 => {
                    result.existentials_by_filler[expression.arguments[1] as usize]
                        .push((expression_id, expression.arguments[0]));
                }
                ExpressionTag::ObjectComplementOf if occurrence.positive > 0 => {
                    let negated = expression.arguments[0];
                    result.complements_by_negated[negated as usize].push(expression_id);
                    result.positive_complements[expression_id as usize].push(negated);
                }
                _ => {}
            }
        }
        for (group, members) in ontology.disjoint_groups.iter().enumerate() {
            for (position, &member) in members.iter().enumerate() {
                result.disjoint_by_member[member as usize].push((group as u32, position as u32));
            }
        }
        for &(compiled_chain, super_property) in &ontology.subproperty_axioms {
            let local_chain = properties.compiled_chain(compiled_chain)?;
            result
                .told_super_properties
                .entry(local_chain)
                .or_default()
                .push(super_property);
        }
        result.sort_indices();
        Ok(result)
    }

    fn sort_indices(&mut self) {
        fn sort_dedup<T: Ord>(rows: &mut [Vec<T>]) {
            for row in rows {
                row.sort();
                row.dedup();
            }
        }
        sort_dedup(&mut self.subclasses);
        sort_dedup(&mut self.definitions_by_class);
        sort_dedup(&mut self.classes_by_definition);
        sort_dedup(&mut self.equivalent_first);
        sort_dedup(&mut self.equivalent_second);
        sort_dedup(&mut self.intersections_by_first);
        sort_dedup(&mut self.intersections_by_second);
        sort_dedup(&mut self.unions_by_disjunct);
        sort_dedup(&mut self.existentials_by_filler);
        sort_dedup(&mut self.complements_by_negated);
        sort_dedup(&mut self.positive_complements);
        sort_dedup(&mut self.disjoint_by_member);
        for values in self.told_super_properties.values_mut() {
            values.sort_unstable();
            values.dedup();
        }
    }

    fn dispatch(&self, state: &Context, premise: &Conclusion) -> CoreResult<Vec<Conclusion>> {
        let mut products = Vec::new();
        match *premise {
            Conclusion::ContextInitialization(root) => {
                products.push(Conclusion::Decomposed {
                    destination: root,
                    subsumer: root,
                });
                if self.introduce_thing {
                    products.push(Conclusion::Composed {
                        destination: root,
                        subsumer: self.owl_thing,
                    });
                }
            }
            Conclusion::SubContextInitialization {
                destination,
                relation,
            } => self.on_subcontext(state, destination, relation, &mut products)?,
            Conclusion::Decomposed {
                destination,
                subsumer,
            } => self.on_decomposed(state, destination, subsumer, &mut products)?,
            Conclusion::Composed {
                destination,
                subsumer,
            } => self.on_composed(state, destination, subsumer, &mut products)?,
            Conclusion::ForwardLink {
                destination,
                chain,
                target,
            } => self.on_forward(state, destination, chain, target, &mut products)?,
            Conclusion::BackwardLink {
                destination,
                relation,
                source,
            } => self.on_backward(state, destination, relation, source, &mut products)?,
            Conclusion::Propagation {
                destination,
                relation,
                carry,
            } => {
                if let Some(sources) = state.backward_links.get(&relation) {
                    for &source in sources {
                        products.push(Conclusion::Composed {
                            destination: source,
                            subsumer: carry,
                        });
                    }
                }
                debug_assert_eq!(destination, state.root);
            }
            Conclusion::DisjointSubsumer {
                destination,
                group,
                position,
            } => {
                if state
                    .disjoint_positions
                    .get(&group)
                    .is_some_and(|positions| positions.iter().any(|&other| other != position))
                {
                    products.push(Conclusion::Inconsistency(destination));
                }
            }
            Conclusion::Inconsistency(destination) => {
                for sources in state.backward_links.values() {
                    for &source in sources {
                        products.push(Conclusion::Inconsistency(source));
                    }
                }
                debug_assert_eq!(destination, state.root);
            }
        }
        Ok(products)
    }

    fn on_subcontext(
        &self,
        state: &Context,
        destination: u32,
        relation: u32,
        products: &mut Vec<Conclusion>,
    ) -> CoreResult<()> {
        for &filler in &state.composed_subsumers {
            for &(existential, carry_property) in &self.existentials_by_filler[filler as usize] {
                let carry_chain = self.properties.singleton_chain(carry_property)?;
                if self
                    .properties
                    .sub_properties(carry_chain)
                    .contains(&relation)
                {
                    products.push(Conclusion::Propagation {
                        destination,
                        relation,
                        carry: existential,
                    });
                }
            }
        }
        Ok(())
    }

    fn on_decomposed(
        &self,
        state: &Context,
        destination: u32,
        subsumer: u32,
        products: &mut Vec<Conclusion>,
    ) -> CoreResult<()> {
        products.push(Conclusion::Composed {
            destination,
            subsumer,
        });
        for &definition in &self.definitions_by_class[subsumer as usize] {
            products.push(Conclusion::Decomposed {
                destination,
                subsumer: definition,
            });
        }
        let expression = &self.ontology.expressions[subsumer as usize];
        let occurrence = self.ontology.expression_occurrences[subsumer as usize];
        match expression.tag {
            ExpressionTag::ObjectIntersectionOf if occurrence.positive > 0 => {
                for &argument in &expression.arguments {
                    products.push(Conclusion::Decomposed {
                        destination,
                        subsumer: argument,
                    });
                }
            }
            ExpressionTag::ObjectSomeValuesFrom if occurrence.positive > 0 => {
                let relation = expression.arguments[0];
                let target = expression.arguments[1];
                let relation_chain = self.properties.singleton_chain(relation)?;
                products.push(Conclusion::BackwardLink {
                    destination: target,
                    relation,
                    source: destination,
                });
                if self
                    .properties
                    .compositions_for_right_chain(relation_chain)
                    .is_some()
                {
                    products.push(Conclusion::ForwardLink {
                        destination,
                        chain: relation_chain,
                        target,
                    });
                }
            }
            ExpressionTag::ObjectHasSelf if occurrence.positive > 0 => {
                let relation = expression.arguments[0];
                let relation_chain = self.properties.singleton_chain(relation)?;
                products.push(Conclusion::BackwardLink {
                    destination,
                    relation,
                    source: destination,
                });
                if self
                    .properties
                    .compositions_for_right_chain(relation_chain)
                    .is_some()
                {
                    products.push(Conclusion::ForwardLink {
                        destination,
                        chain: relation_chain,
                        target: destination,
                    });
                }
                for &range in self.properties.ranges(relation) {
                    products.push(Conclusion::Decomposed {
                        destination,
                        subsumer: range,
                    });
                }
            }
            ExpressionTag::ObjectComplementOf
                if occurrence.positive > 0
                    && self.positive_complements[subsumer as usize]
                        .iter()
                        .any(|negated| state.composed_subsumers.contains(negated)) =>
            {
                products.push(Conclusion::Inconsistency(destination));
            }
            _ => {}
        }
        if self.decompose_nothing && subsumer == self.owl_nothing {
            products.push(Conclusion::Inconsistency(destination));
        }
        Ok(())
    }

    fn on_composed(
        &self,
        state: &Context,
        destination: u32,
        subsumer: u32,
        products: &mut Vec<Conclusion>,
    ) -> CoreResult<()> {
        for &super_expression in &self.subclasses[subsumer as usize] {
            products.push(Conclusion::Decomposed {
                destination,
                subsumer: super_expression,
            });
        }
        for &defined_class in &self.classes_by_definition[subsumer as usize] {
            products.push(Conclusion::Composed {
                destination,
                subsumer: defined_class,
            });
        }
        for &first in &self.equivalent_first[subsumer as usize] {
            products.push(Conclusion::Decomposed {
                destination,
                subsumer: first,
            });
        }
        for &second in &self.equivalent_second[subsumer as usize] {
            products.push(Conclusion::Decomposed {
                destination,
                subsumer: second,
            });
        }
        for &(second, conjunction) in &self.intersections_by_first[subsumer as usize] {
            if state.composed_subsumers.contains(&second) {
                products.push(Conclusion::Composed {
                    destination,
                    subsumer: conjunction,
                });
            }
        }
        for &(first, conjunction) in &self.intersections_by_second[subsumer as usize] {
            if state.composed_subsumers.contains(&first) {
                products.push(Conclusion::Composed {
                    destination,
                    subsumer: conjunction,
                });
            }
        }
        for &(union, _position) in &self.unions_by_disjunct[subsumer as usize] {
            products.push(Conclusion::Composed {
                destination,
                subsumer: union,
            });
        }
        for &(existential, carry_property) in &self.existentials_by_filler[subsumer as usize] {
            let carry_chain = self.properties.singleton_chain(carry_property)?;
            let compatible = self.properties.sub_properties(carry_chain);
            for &relation in &state.initialized_subcontexts {
                if compatible.contains(&relation) {
                    products.push(Conclusion::Propagation {
                        destination,
                        relation,
                        carry: existential,
                    });
                }
            }
        }
        if self.complements_by_negated[subsumer as usize]
            .iter()
            .any(|complement| state.decomposed_subsumers.contains(complement))
        {
            products.push(Conclusion::Inconsistency(destination));
        }
        for &(group, position) in &self.disjoint_by_member[subsumer as usize] {
            products.push(Conclusion::DisjointSubsumer {
                destination,
                group,
                position,
            });
        }
        Ok(())
    }

    fn on_forward(
        &self,
        state: &Context,
        destination: u32,
        chain: u32,
        target: u32,
        products: &mut Vec<Conclusion>,
    ) -> CoreResult<()> {
        let record = self.properties.chains[chain as usize];
        if record.suffix_chain.is_some()
            && let Some(super_properties) = self.told_super_properties.get(&chain)
        {
            for &super_property in super_properties {
                products.push(Conclusion::BackwardLink {
                    destination: target,
                    relation: super_property,
                    source: destination,
                });
            }
        }
        if let Some(compositions) = self.properties.compositions_for_right_chain(chain) {
            for (&relation, result_chains) in compositions {
                if let Some(sources) = state.backward_links.get(&relation) {
                    for &source in sources {
                        for &result_chain in result_chains {
                            self.produce_composition(
                                relation,
                                source,
                                chain,
                                target,
                                result_chain,
                                products,
                            )?;
                        }
                    }
                }
            }
        }
        Ok(())
    }

    fn on_backward(
        &self,
        state: &Context,
        destination: u32,
        relation: u32,
        source: u32,
        products: &mut Vec<Conclusion>,
    ) -> CoreResult<()> {
        products.push(Conclusion::SubContextInitialization {
            destination,
            relation,
        });
        if let Some(carries) = state.propagations.get(&relation) {
            for &carry in carries {
                products.push(Conclusion::Composed {
                    destination: source,
                    subsumer: carry,
                });
            }
        }
        if state.inconsistent {
            products.push(Conclusion::Inconsistency(source));
        }
        for &range in self.properties.ranges(relation) {
            products.push(Conclusion::Decomposed {
                destination,
                subsumer: range,
            });
        }
        if let Some(compositions) = self.properties.compositions_for_left_property(relation) {
            for (&right_chain, result_chains) in compositions {
                if let Some(targets) = state.forward_links.get(&right_chain) {
                    for &target in targets {
                        for &result_chain in result_chains {
                            self.produce_composition(
                                relation,
                                source,
                                right_chain,
                                target,
                                result_chain,
                                products,
                            )?;
                        }
                    }
                }
            }
        }
        Ok(())
    }

    fn produce_composition(
        &self,
        _relation: u32,
        source: u32,
        _right_chain: u32,
        target: u32,
        result_chain: u32,
        products: &mut Vec<Conclusion>,
    ) -> CoreResult<()> {
        let result = self.properties.chains[result_chain as usize];
        if result.suffix_chain.is_none() {
            return Err(CoreError::internal(
                "property composition produced a singleton chain",
            ));
        }
        if self.properties.chain_is_extendable(result_chain) {
            products.push(Conclusion::ForwardLink {
                destination: source,
                chain: result_chain,
                target,
            });
        } else if let Some(super_properties) = self.told_super_properties.get(&result_chain) {
            for &super_property in super_properties {
                products.push(Conclusion::BackwardLink {
                    destination: target,
                    relation: super_property,
                    source,
                });
            }
        }
        Ok(())
    }
}

/// Immutable ontology-wide rule indexes reusable by independent saturation runs.
///
/// Classification deliberately saturates roots independently when range conclusions are
/// present.  Building these expression-sized indexes for every root is unnecessary, however:
/// they contain no mutable scheduling state and are safe to share across Rayon workers.
pub(crate) struct PreparedSaturation<'ontology> {
    dispatcher: RuleDispatcher<'ontology>,
}

impl<'ontology> PreparedSaturation<'ontology> {
    pub(crate) fn new(
        ontology: &'ontology Ontology,
        properties: &'ontology PropertyClosure,
    ) -> CoreResult<Self> {
        Ok(Self {
            dispatcher: RuleDispatcher::new(ontology, properties)?,
        })
    }

    pub(crate) fn saturate_roots(
        &self,
        roots: &[u32],
    ) -> CoreResult<(BTreeMap<u32, ContextSnapshot>, SaturationCounters)> {
        Saturator::new(&self.dispatcher).run(roots)
    }

    pub(crate) fn saturate_root(
        &self,
        root: u32,
    ) -> CoreResult<(ContextSnapshot, SaturationCounters)> {
        Saturator::new(&self.dispatcher).run_root(root)
    }
}

struct Saturator<'dispatcher, 'ontology> {
    dispatcher: &'dispatcher RuleDispatcher<'ontology>,
    contexts: BTreeMap<u32, Context>,
    // These sets are queried only for membership. Their iteration order cannot affect the
    // deterministic FIFO agenda, so hashing avoids logarithmic tree work without changing
    // inference order or snapshot ordering.
    seen: HashSet<Conclusion>,
    pending: HashSet<Conclusion>,
    agenda: VecDeque<Conclusion>,
    counters: SaturationCounters,
}

impl<'dispatcher, 'ontology> Saturator<'dispatcher, 'ontology> {
    fn new(dispatcher: &'dispatcher RuleDispatcher<'ontology>) -> Self {
        Self {
            dispatcher,
            contexts: BTreeMap::new(),
            seen: HashSet::new(),
            pending: HashSet::new(),
            agenda: VecDeque::new(),
            counters: SaturationCounters::default(),
        }
    }

    fn ensure_context(&mut self, root: u32) -> CoreResult<()> {
        if root as usize >= self.dispatcher.ontology.expressions.len() {
            return Err(CoreError::invalid(format!(
                "context root {root} is out of range"
            )));
        }
        if let std::collections::btree_map::Entry::Vacant(entry) = self.contexts.entry(root) {
            entry.insert(Context::new(root));
            self.counters.contexts_created += 1;
            self.enqueue_raw(Conclusion::ContextInitialization(root));
        }
        Ok(())
    }

    fn enqueue_raw(&mut self, conclusion: Conclusion) {
        self.counters.conclusion_candidates += 1;
        if self.seen.contains(&conclusion) || !self.pending.insert(conclusion.clone()) {
            self.counters.duplicate_candidates += 1;
            return;
        }
        self.agenda.push_back(conclusion);
    }

    fn enqueue(&mut self, conclusion: Conclusion) -> CoreResult<()> {
        self.ensure_context(conclusion.destination())?;
        self.enqueue_raw(conclusion);
        Ok(())
    }

    fn saturate(mut self, roots: &[u32]) -> CoreResult<Self> {
        let roots = roots.iter().copied().collect::<BTreeSet<_>>();
        for root in roots {
            self.ensure_context(root)?;
        }
        while let Some(premise) = self.agenda.pop_front() {
            self.pending.remove(&premise);
            if !self.seen.insert(premise.clone()) {
                self.counters.duplicate_candidates += 1;
                continue;
            }
            self.counters.conclusions_inserted += 1;
            let destination = premise.destination();
            let products = {
                let state = self
                    .contexts
                    .get_mut(&destination)
                    .ok_or_else(|| CoreError::internal("conclusion destination disappeared"))?;
                state.insert(&premise);
                self.dispatcher.dispatch(state, &premise)?
            };
            self.counters.rule_dispatches += 1;
            self.counters.product_candidates += products.len() as u64;
            for product in products {
                self.enqueue(product)?;
            }
        }
        Ok(self)
    }

    fn run(
        self,
        roots: &[u32],
    ) -> CoreResult<(BTreeMap<u32, ContextSnapshot>, SaturationCounters)> {
        let completed = self.saturate(roots)?;
        let counters = completed.counters;
        let contexts = completed
            .contexts
            .into_iter()
            .map(|(root, context)| (root, ContextSnapshot::from_context(context)))
            .collect();
        Ok((contexts, counters))
    }

    fn run_root(self, root: u32) -> CoreResult<(ContextSnapshot, SaturationCounters)> {
        let mut completed = self.saturate(&[root])?;
        let counters = completed.counters;
        let context = completed
            .contexts
            .remove(&root)
            .ok_or_else(|| CoreError::internal("saturation lost demanded root context"))?;
        Ok((ContextSnapshot::from_context(context), counters))
    }
}

/// Saturate one or more roots in one duplicate-suppressing engine.
pub fn saturate_roots(
    ontology: &Ontology,
    properties: &PropertyClosure,
    roots: &[u32],
) -> CoreResult<(BTreeMap<u32, ContextSnapshot>, SaturationCounters)> {
    PreparedSaturation::new(ontology, properties)?.saturate_roots(roots)
}

/// Saturate a root in isolation, which is safe to execute concurrently with other roots.
pub fn saturate_root(
    ontology: &Ontology,
    properties: &PropertyClosure,
    root: u32,
) -> CoreResult<(ContextSnapshot, SaturationCounters)> {
    PreparedSaturation::new(ontology, properties)?.saturate_root(root)
}
