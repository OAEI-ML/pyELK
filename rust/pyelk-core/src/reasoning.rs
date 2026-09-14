//! Iterative occurrence-aware class saturation over one demanded root.

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet, VecDeque};
use std::ops::{Deref, Index, IndexMut};
use std::sync::Arc;

use crate::error::{CoreError, CoreResult};
use crate::ir::{Expression, ExpressionTag, OWL_NOTHING_IRI, OWL_THING_IRI, Occurrence, Ontology};
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
    /// Charge worst-case sparse B-tree node occupancy rather than only logical payload bytes.
    pub(crate) fn retained_bytes(&self) -> usize {
        let set_entries = self.composed_subsumers.len()
            + self.decomposed_subsumers.len()
            + self.initialized_subcontexts.len();
        let maps = [
            &self.forward_links,
            &self.backward_links,
            &self.propagations,
            &self.disjoint_positions,
        ];
        64 + 12 * std::mem::size_of::<(u32, Self)>()
            + set_entries * (64 + 12 * std::mem::size_of::<u32>())
            + maps
                .iter()
                .map(|map| {
                    map.len() * (64 + 12 * std::mem::size_of::<(u32, BTreeSet<u32>)>())
                        + map
                            .values()
                            .map(|set| set.len() * (64 + 12 * std::mem::size_of::<u32>()))
                            .sum::<usize>()
                })
                .sum::<usize>()
    }

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

/// Dense immutable base rows with copy-on-write changes only for touched query rows.
#[derive(Clone, Debug)]
struct RuleRows<T> {
    base: Arc<Vec<Vec<T>>>,
    changed: BTreeMap<usize, Vec<T>>,
    empty: Vec<T>,
}

impl<T: Clone + Ord> RuleRows<T> {
    fn new(count: usize) -> Self {
        Self {
            base: Arc::new(vec![Vec::new(); count]),
            changed: BTreeMap::new(),
            empty: Vec::new(),
        }
    }

    fn owned_bytes(&self) -> usize {
        self.changed
            .values()
            .map(|row| {
                64 + 12 * std::mem::size_of::<(usize, Vec<T>)>()
                    + row.capacity() * std::mem::size_of::<T>()
            })
            .sum()
    }

    fn sort_dedup(&mut self) {
        if let Some(base) = Arc::get_mut(&mut self.base) {
            for row in base {
                row.sort();
                row.dedup();
            }
        }
        for row in self.changed.values_mut() {
            row.sort();
            row.dedup();
        }
    }
}

impl<T> Index<usize> for RuleRows<T> {
    type Output = Vec<T>;
    fn index(&self, index: usize) -> &Self::Output {
        self.changed
            .get(&index)
            .or_else(|| self.base.get(index))
            .unwrap_or(&self.empty)
    }
}

impl<T: Clone> IndexMut<usize> for RuleRows<T> {
    fn index_mut(&mut self, index: usize) -> &mut Self::Output {
        if Arc::strong_count(&self.base) == 1 && index < self.base.len() {
            return &mut Arc::get_mut(&mut self.base).expect("exclusive rule rows")[index];
        }
        self.changed
            .entry(index)
            .or_insert_with(|| self.base.get(index).cloned().unwrap_or_default())
    }
}

/// Minimal immutable expression access used by the native scheduler.
pub(crate) trait ExpressionSource: Sync {
    fn expression(&self, id: usize) -> &Expression;
    fn occurrence(&self, id: usize) -> Occurrence;
    fn expression_count(&self) -> usize;
}

impl ExpressionSource for Ontology {
    fn expression(&self, id: usize) -> &Expression {
        &self.expressions[id]
    }
    fn occurrence(&self, id: usize) -> Occurrence {
        self.expression_occurrences[id]
    }
    fn expression_count(&self) -> usize {
        self.expressions.len()
    }
}

#[derive(Clone, Debug)]
pub(crate) struct RuleIndices {
    owl_thing: u32,
    owl_nothing: u32,
    introduce_thing: bool,
    decompose_nothing: bool,
    subclasses: RuleRows<u32>,
    definitions_by_class: RuleRows<u32>,
    classes_by_definition: RuleRows<u32>,
    equivalent_first: RuleRows<u32>,
    equivalent_second: RuleRows<u32>,
    intersections_by_first: RuleRows<(u32, u32)>,
    intersections_by_second: RuleRows<(u32, u32)>,
    unions_by_disjunct: RuleRows<(u32, u32)>,
    existentials_by_filler: RuleRows<(u32, u32)>,
    complements_by_negated: RuleRows<u32>,
    positive_complements: RuleRows<u32>,
    disjoint_by_member: RuleRows<(u32, u32)>,
    told_super_properties: Arc<BTreeMap<u32, Vec<u32>>>,
}

impl RuleIndices {
    pub(crate) fn new(ontology: &Ontology, properties: &PropertyClosure) -> CoreResult<Self> {
        let expression_count = ontology.expressions.len();
        let class_thing = ontology.entity_id(crate::ir::EntityKind::Class, OWL_THING_IRI)?;
        let class_nothing = ontology.entity_id(crate::ir::EntityKind::Class, OWL_NOTHING_IRI)?;
        let owl_thing = ontology.named_expression(ExpressionTag::Class, class_thing)?;
        let owl_nothing = ontology.named_expression(ExpressionTag::Class, class_nothing)?;
        let mut result = Self {
            owl_thing,
            owl_nothing,
            introduce_thing: ontology.expression_occurrences[owl_thing as usize].negative > 0,
            decompose_nothing: ontology.expression_occurrences[owl_nothing as usize].positive > 0,
            subclasses: RuleRows::new(expression_count),
            definitions_by_class: RuleRows::new(expression_count),
            classes_by_definition: RuleRows::new(expression_count),
            equivalent_first: RuleRows::new(expression_count),
            equivalent_second: RuleRows::new(expression_count),
            intersections_by_first: RuleRows::new(expression_count),
            intersections_by_second: RuleRows::new(expression_count),
            unions_by_disjunct: RuleRows::new(expression_count),
            existentials_by_filler: RuleRows::new(expression_count),
            complements_by_negated: RuleRows::new(expression_count),
            positive_complements: RuleRows::new(expression_count),
            disjoint_by_member: RuleRows::new(expression_count),
            told_super_properties: Arc::new(BTreeMap::new()),
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
            result.register_expression(
                expression_index as u32,
                expression,
                Occurrence::default(),
                ontology.expression_occurrences[expression_index],
            );
        }
        for (group, members) in ontology.disjoint_groups.iter().enumerate() {
            for (position, &member) in members.iter().enumerate() {
                result.disjoint_by_member[member as usize].push((group as u32, position as u32));
            }
        }
        for &(compiled_chain, super_property) in &ontology.subproperty_axioms {
            let local_chain = properties.compiled_chain(compiled_chain)?;
            Arc::get_mut(&mut result.told_super_properties)
                .expect("new rule map")
                .entry(local_chain)
                .or_default()
                .push(super_property);
        }
        result.sort_indices();
        Ok(result)
    }

    fn register_expression(
        &mut self,
        expression_id: u32,
        expression: &Expression,
        previous: Occurrence,
        occurrence: Occurrence,
    ) {
        match expression.tag {
            ExpressionTag::ObjectIntersectionOf
                if occurrence.negative > 0 && previous.negative == 0 =>
            {
                self.intersections_by_first[expression.arguments[0] as usize]
                    .push((expression.arguments[1], expression_id));
                self.intersections_by_second[expression.arguments[1] as usize]
                    .push((expression.arguments[0], expression_id));
            }
            ExpressionTag::ObjectUnionOf if occurrence.negative > 0 && previous.negative == 0 => {
                for (position, &argument) in expression.arguments.iter().enumerate() {
                    self.unions_by_disjunct[argument as usize]
                        .push((expression_id, position as u32));
                }
            }
            ExpressionTag::ObjectSomeValuesFrom
                if occurrence.negative > 0 && previous.negative == 0 =>
            {
                self.existentials_by_filler[expression.arguments[1] as usize]
                    .push((expression_id, expression.arguments[0]));
            }
            ExpressionTag::ObjectComplementOf
                if occurrence.positive > 0 && previous.positive == 0 =>
            {
                let negated = expression.arguments[0];
                self.complements_by_negated[negated as usize].push(expression_id);
                self.positive_complements[expression_id as usize].push(negated);
            }
            _ => {}
        }
    }

    pub(crate) fn query_owned_bytes(&self) -> usize {
        std::mem::size_of::<Self>()
            + self.subclasses.owned_bytes()
            + self.definitions_by_class.owned_bytes()
            + self.classes_by_definition.owned_bytes()
            + self.equivalent_first.owned_bytes()
            + self.equivalent_second.owned_bytes()
            + self.intersections_by_first.owned_bytes()
            + self.intersections_by_second.owned_bytes()
            + self.unions_by_disjunct.owned_bytes()
            + self.existentials_by_filler.owned_bytes()
            + self.complements_by_negated.owned_bytes()
            + self.positive_complements.owned_bytes()
            + self.disjoint_by_member.owned_bytes()
    }

    pub(crate) fn with_query(
        &self,
        expressions: &dyn ExpressionSource,
        changed: &BTreeMap<usize, Occurrence>,
    ) -> Self {
        let mut result = self.clone();
        for (&id, &previous) in changed {
            result.register_expression(
                id as u32,
                expressions.expression(id),
                previous,
                expressions.occurrence(id),
            );
        }
        result.introduce_thing = expressions.occurrence(self.owl_thing as usize).negative > 0;
        result.decompose_nothing = expressions.occurrence(self.owl_nothing as usize).positive > 0;
        result.sort_indices();
        result
    }

    fn sort_indices(&mut self) {
        self.subclasses.sort_dedup();
        self.definitions_by_class.sort_dedup();
        self.classes_by_definition.sort_dedup();
        self.equivalent_first.sort_dedup();
        self.equivalent_second.sort_dedup();
        self.intersections_by_first.sort_dedup();
        self.intersections_by_second.sort_dedup();
        self.unions_by_disjunct.sort_dedup();
        self.existentials_by_filler.sort_dedup();
        self.complements_by_negated.sort_dedup();
        self.positive_complements.sort_dedup();
        self.disjoint_by_member.sort_dedup();
        if let Some(rows) = Arc::get_mut(&mut self.told_super_properties) {
            for values in rows.values_mut() {
                values.sort_unstable();
                values.dedup();
            }
        }
    }
}

struct RuleDispatcher<'a> {
    ontology: &'a dyn ExpressionSource,
    properties: &'a PropertyClosure,
    indices: Arc<RuleIndices>,
}

impl Deref for RuleDispatcher<'_> {
    type Target = RuleIndices;

    fn deref(&self) -> &Self::Target {
        &self.indices
    }
}

impl RuleDispatcher<'_> {
    fn dispatch(
        &self,
        state: &Context,
        premise: &Conclusion,
        products: &mut Vec<Conclusion>,
    ) -> CoreResult<()> {
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
            } => self.on_subcontext(state, destination, relation, products)?,
            Conclusion::Decomposed {
                destination,
                subsumer,
            } => self.on_decomposed(state, destination, subsumer, products)?,
            Conclusion::Composed {
                destination,
                subsumer,
            } => self.on_composed(state, destination, subsumer, products)?,
            Conclusion::ForwardLink {
                destination,
                chain,
                target,
            } => self.on_forward(state, destination, chain, target, products)?,
            Conclusion::BackwardLink {
                destination,
                relation,
                source,
            } => self.on_backward(state, destination, relation, source, products)?,
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
        Ok(())
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
        let expression = self.ontology.expression(subsumer as usize);
        let occurrence = self.ontology.occurrence(subsumer as usize);
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
        let record = self.properties.chain(chain);
        if record.suffix_chain.is_some() {
            if let Some(super_properties) = self.told_super_properties.get(&chain) {
                for &super_property in super_properties {
                    products.push(Conclusion::BackwardLink {
                        destination: target,
                        relation: super_property,
                        source: destination,
                    });
                }
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
        let result = self.properties.chain(result_chain);
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
        Ok(Self::with_indices(
            ontology,
            properties,
            Arc::new(RuleIndices::new(ontology, properties)?),
        ))
    }

    pub(crate) fn with_indices(
        ontology: &'ontology dyn ExpressionSource,
        properties: &'ontology PropertyClosure,
        indices: Arc<RuleIndices>,
    ) -> Self {
        Self {
            dispatcher: RuleDispatcher {
                ontology,
                properties,
                indices,
            },
        }
    }

    pub(crate) fn saturate_roots(
        &self,
        roots: &[u32],
    ) -> CoreResult<(BTreeMap<u32, ContextSnapshot>, SaturationCounters)> {
        self.workspace().run(roots)
    }

    pub(crate) fn saturate_root(
        &self,
        root: u32,
    ) -> CoreResult<(ContextSnapshot, SaturationCounters)> {
        self.workspace().run_root(root)
    }

    /// Create one empty, reusable scheduler workspace.
    ///
    /// Reusing a workspace retains only allocation capacity. Every reasoning fact and counter is
    /// cleared before the next demanded root, so independent-root semantics are unchanged.
    pub(crate) fn workspace(&self) -> SaturationWorkspace<'_, 'ontology> {
        SaturationWorkspace::new(&self.dispatcher)
    }
}

/// Cleared worker-local storage for independent saturation runs.
pub(crate) struct SaturationWorkspace<'dispatcher, 'ontology> {
    dispatcher: &'dispatcher RuleDispatcher<'ontology>,
    // Context lookup is keyed only by numeric identity during saturation. Boundary conversion
    // below restores canonical BTreeMap order before any snapshot can escape the engine.
    contexts: HashMap<u32, Context>,
    // Membership is monotone from enqueue onward. This set is never iterated, so hashing cannot
    // affect the deterministic FIFO agenda or canonical snapshot ordering.
    accepted: HashSet<Conclusion>,
    agenda: VecDeque<Conclusion>,
    counters: SaturationCounters,
}

impl<'dispatcher, 'ontology> SaturationWorkspace<'dispatcher, 'ontology> {
    fn new(dispatcher: &'dispatcher RuleDispatcher<'ontology>) -> Self {
        Self {
            dispatcher,
            contexts: HashMap::new(),
            accepted: HashSet::new(),
            agenda: VecDeque::new(),
            counters: SaturationCounters::default(),
        }
    }

    fn ensure_context(&mut self, root: u32) -> CoreResult<()> {
        if root as usize >= self.dispatcher.ontology.expression_count() {
            return Err(CoreError::invalid(format!(
                "context root {root} is out of range"
            )));
        }
        if let std::collections::hash_map::Entry::Vacant(entry) = self.contexts.entry(root) {
            entry.insert(Context::new(root));
            self.counters.contexts_created += 1;
            self.enqueue_raw(Conclusion::ContextInitialization(root));
        }
        Ok(())
    }

    fn enqueue_raw(&mut self, conclusion: Conclusion) {
        self.counters.conclusion_candidates += 1;
        if !self.accepted.insert(conclusion.clone()) {
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

    fn reset(&mut self) {
        self.contexts.clear();
        self.accepted.clear();
        self.agenda.clear();
        self.counters = SaturationCounters::default();
    }

    fn saturate(&mut self, roots: &[u32]) -> CoreResult<()> {
        self.reset();
        let roots = roots.iter().copied().collect::<BTreeSet<_>>();
        for root in roots {
            self.ensure_context(root)?;
        }
        let mut products = Vec::new();
        while let Some(premise) = self.agenda.pop_front() {
            debug_assert!(self.accepted.contains(&premise));
            self.counters.conclusions_inserted += 1;
            let destination = premise.destination();
            products.clear();
            {
                let state = self
                    .contexts
                    .get_mut(&destination)
                    .ok_or_else(|| CoreError::internal("conclusion destination disappeared"))?;
                state.insert(&premise);
                self.dispatcher.dispatch(state, &premise, &mut products)?;
            }
            self.counters.rule_dispatches += 1;
            self.counters.product_candidates += products.len() as u64;
            for product in products.drain(..) {
                self.enqueue(product)?;
            }
        }
        Ok(())
    }

    pub(crate) fn run(
        &mut self,
        roots: &[u32],
    ) -> CoreResult<(BTreeMap<u32, ContextSnapshot>, SaturationCounters)> {
        self.saturate(roots)?;
        let counters = self.counters;
        let contexts = self
            .contexts
            .drain()
            .map(|(root, context)| (root, ContextSnapshot::from_context(context)))
            .collect();
        Ok((contexts, counters))
    }

    pub(crate) fn run_root(
        &mut self,
        root: u32,
    ) -> CoreResult<(ContextSnapshot, SaturationCounters)> {
        self.saturate(&[root])?;
        let counters = self.counters;
        let context = self
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
