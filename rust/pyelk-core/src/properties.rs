//! Deterministic object-property hierarchy, range, and composition closure.

use std::collections::{BTreeMap, BTreeSet, VecDeque};

use crate::error::{CoreError, CoreResult};
use crate::ir::{EntityKind, ExpressionTag, OWL_THING_IRI, Ontology, U32_RESERVED};

/// Compact right-linked chain record used during class saturation.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct ChainRecord {
    pub first_property: u32,
    pub suffix_chain: Option<u32>,
}

/// Immutable property closure shared by every native saturation engine.
#[derive(Clone, Debug)]
pub struct PropertyClosure {
    pub chains: Vec<ChainRecord>,
    pub compiled_chain_ids: Vec<u32>,
    pub reflexive_properties: Vec<u32>,
    subchains_by_super: Vec<Vec<u32>>,
    superchains_by_sub: Vec<Vec<u32>>,
    ranges_by_property: BTreeMap<u32, Vec<u32>>,
    non_redundant_by_right: BTreeMap<u32, BTreeMap<u32, Vec<u32>>>,
    redundant_by_right: BTreeMap<u32, BTreeMap<u32, Vec<u32>>>,
    non_redundant_by_left: BTreeMap<u32, BTreeMap<u32, Vec<u32>>>,
    redundant_by_left: BTreeMap<u32, BTreeMap<u32, Vec<u32>>>,
    singleton_chains: BTreeMap<u32, u32>,
}

impl PropertyClosure {
    /// Compute the complete fixed point before any class rule is allowed to run.
    pub fn build(ontology: &Ontology) -> CoreResult<Self> {
        let universe = ChainUniverse::build(ontology)?;
        let chain_count = universe.records.len();
        let mut subchains_by_super = vec![BTreeSet::<u32>::new(); chain_count];
        let mut superchains_by_sub = vec![BTreeSet::<u32>::new(); chain_count];
        let mut conclusions = BTreeSet::<(u32, u32)>::new();
        let mut told_by_super = BTreeMap::<u32, BTreeSet<u32>>::new();
        let mut agenda = VecDeque::<(u32, u32)>::new();

        let add = |conclusion: (u32, u32),
                   conclusions: &mut BTreeSet<(u32, u32)>,
                   sub_by_super: &mut [BTreeSet<u32>],
                   super_by_sub: &mut [BTreeSet<u32>],
                   agenda: &mut VecDeque<(u32, u32)>| {
            if conclusions.insert(conclusion) {
                sub_by_super[conclusion.1 as usize].insert(conclusion.0);
                super_by_sub[conclusion.0 as usize].insert(conclusion.1);
                agenda.push_back(conclusion);
            }
        };
        for chain in 0..chain_count {
            let chain = u32::try_from(chain)
                .map_err(|_| CoreError::capacity("property-chain namespace exhausted"))?;
            add(
                (chain, chain),
                &mut conclusions,
                &mut subchains_by_super,
                &mut superchains_by_sub,
                &mut agenda,
            );
        }
        for &(compiled_sub, super_property) in &ontology.subproperty_axioms {
            let sub_chain = universe.compiled_ids[compiled_sub as usize];
            let super_chain = *universe.singleton_ids.get(&super_property).ok_or_else(|| {
                CoreError::internal("subproperty super entity has no singleton chain")
            })?;
            told_by_super
                .entry(super_chain)
                .or_default()
                .insert(sub_chain);
            add(
                (sub_chain, super_chain),
                &mut conclusions,
                &mut subchains_by_super,
                &mut superchains_by_sub,
                &mut agenda,
            );
        }
        while let Some((premise_sub, premise_super)) = agenda.pop_front() {
            if let Some(told_subchains) = told_by_super.get(&premise_sub) {
                for &told_sub in told_subchains {
                    add(
                        (told_sub, premise_super),
                        &mut conclusions,
                        &mut subchains_by_super,
                        &mut superchains_by_sub,
                        &mut agenda,
                    );
                }
            }
        }

        let ranges_by_property = inherit_ranges(ontology, &universe, &subchains_by_super);
        let (non_redundant, redundant) = compute_compositions(&universe, &subchains_by_super);
        let (non_redundant_by_right, non_redundant_by_left) = composition_indices(&non_redundant);
        let (redundant_by_right, redundant_by_left) = composition_indices(&redundant);
        let reflexive_properties = reflexive_properties(ontology)?;
        Ok(Self {
            chains: universe.records,
            compiled_chain_ids: universe.compiled_ids,
            reflexive_properties,
            subchains_by_super: subchains_by_super
                .into_iter()
                .map(|values| values.into_iter().collect())
                .collect(),
            superchains_by_sub: superchains_by_sub
                .into_iter()
                .map(|values| values.into_iter().collect())
                .collect(),
            ranges_by_property,
            non_redundant_by_right,
            redundant_by_right,
            non_redundant_by_left,
            redundant_by_left,
            singleton_chains: universe.singleton_ids,
        })
    }

    pub fn compiled_chain(&self, compiled_id: u32) -> CoreResult<u32> {
        self.compiled_chain_ids
            .get(compiled_id as usize)
            .copied()
            .ok_or_else(|| CoreError::internal("compiled chain ID is out of range"))
    }

    pub fn singleton_chain(&self, property: u32) -> CoreResult<u32> {
        self.singleton_chains
            .get(&property)
            .copied()
            .ok_or_else(|| {
                CoreError::internal(format!("entity {property} is not an object property"))
            })
    }

    pub fn sub_chains(&self, super_chain: u32) -> &[u32] {
        self.subchains_by_super
            .get(super_chain as usize)
            .map_or(&[], Vec::as_slice)
    }

    pub fn super_chains(&self, sub_chain: u32) -> &[u32] {
        self.superchains_by_sub
            .get(sub_chain as usize)
            .map_or(&[], Vec::as_slice)
    }

    pub fn sub_properties(&self, super_chain: u32) -> Vec<u32> {
        self.sub_chains(super_chain)
            .iter()
            .filter_map(|&chain| {
                let record = self.chains[chain as usize];
                record
                    .suffix_chain
                    .is_none()
                    .then_some(record.first_property)
            })
            .collect()
    }

    pub fn ranges(&self, property: u32) -> &[u32] {
        self.ranges_by_property
            .get(&property)
            .map_or(&[], Vec::as_slice)
    }

    /// Compositions indexed by a right-chain premise, then a left property.
    pub fn compositions_for_right_chain(
        &self,
        right_chain: u32,
    ) -> Option<&BTreeMap<u32, Vec<u32>>> {
        self.non_redundant_by_right.get(&right_chain)
    }

    /// Compositions indexed by a left-property premise, then a right chain.
    pub fn compositions_for_left_property(
        &self,
        left_property: u32,
    ) -> Option<&BTreeMap<u32, Vec<u32>>> {
        self.non_redundant_by_left.get(&left_property)
    }

    pub fn chain_is_extendable(&self, chain: u32) -> bool {
        self.non_redundant_by_right.contains_key(&chain)
            || self.redundant_by_right.contains_key(&chain)
    }

    pub fn redundant_for_right_chain(&self, right_chain: u32) -> Option<&BTreeMap<u32, Vec<u32>>> {
        self.redundant_by_right.get(&right_chain)
    }

    pub fn redundant_for_left_property(
        &self,
        left_property: u32,
    ) -> Option<&BTreeMap<u32, Vec<u32>>> {
        self.redundant_by_left.get(&left_property)
    }
}

#[derive(Debug)]
struct ChainUniverse {
    records: Vec<ChainRecord>,
    compiled_ids: Vec<u32>,
    singleton_ids: BTreeMap<u32, u32>,
}

impl ChainUniverse {
    fn build(ontology: &Ontology) -> CoreResult<Self> {
        let mut temporary_records = Vec::<(u32, Option<usize>)>::new();
        let mut temporary_depths = Vec::<usize>::new();
        let mut temporary_ids = BTreeMap::<(u32, Option<usize>), usize>::new();

        fn intern(
            values: &[u32],
            records: &mut Vec<(u32, Option<usize>)>,
            depths: &mut Vec<usize>,
            ids: &mut BTreeMap<(u32, Option<usize>), usize>,
        ) -> CoreResult<usize> {
            if values.is_empty() {
                return Err(CoreError::internal("cannot intern an empty property chain"));
            }
            let mut suffix = None;
            for &property in values.iter().rev() {
                let key = (property, suffix);
                let current = if let Some(&existing) = ids.get(&key) {
                    existing
                } else {
                    let next = records.len();
                    if next >= U32_RESERVED as usize {
                        return Err(CoreError::capacity(
                            "derived property-chain namespace exhausted",
                        ));
                    }
                    ids.insert(key, next);
                    records.push((property, suffix));
                    depths.push(suffix.map_or(1, |value| depths[value] + 1));
                    next
                };
                suffix = Some(current);
            }
            suffix.ok_or_else(|| CoreError::internal("nonempty chain produced no root"))
        }

        let object_properties = ontology
            .entities
            .iter()
            .enumerate()
            .filter_map(|(index, entity)| {
                (entity.kind == EntityKind::ObjectProperty).then_some(index as u32)
            })
            .collect::<Vec<_>>();
        let mut singleton_temporary = BTreeMap::new();
        for property in object_properties {
            let temporary = intern(
                &[property],
                &mut temporary_records,
                &mut temporary_depths,
                &mut temporary_ids,
            )?;
            singleton_temporary.insert(property, temporary);
        }
        let compiled_temporary = ontology
            .property_chains
            .iter()
            .map(|chain| {
                intern(
                    chain,
                    &mut temporary_records,
                    &mut temporary_depths,
                    &mut temporary_ids,
                )
            })
            .collect::<CoreResult<Vec<_>>>()?;

        let mut by_depth = BTreeMap::<usize, Vec<usize>>::new();
        for (temporary, &depth) in temporary_depths.iter().enumerate() {
            by_depth.entry(depth).or_default().push(temporary);
        }
        let mut final_ids = vec![0_u32; temporary_records.len()];
        let mut records = Vec::with_capacity(temporary_records.len());
        for temporary_values in by_depth.values_mut() {
            temporary_values.sort_by_key(|&temporary| {
                let (first, suffix) = temporary_records[temporary];
                (first, suffix.map_or(u32::MAX, |value| final_ids[value]))
            });
            for &temporary in temporary_values.iter() {
                let (first_property, temporary_suffix) = temporary_records[temporary];
                let suffix_chain = temporary_suffix.map(|value| final_ids[value]);
                let final_id = u32::try_from(records.len()).map_err(|_| {
                    CoreError::capacity("derived property-chain namespace exhausted")
                })?;
                final_ids[temporary] = final_id;
                records.push(ChainRecord {
                    first_property,
                    suffix_chain,
                });
            }
        }
        Ok(Self {
            records,
            compiled_ids: compiled_temporary
                .into_iter()
                .map(|temporary| final_ids[temporary])
                .collect(),
            singleton_ids: singleton_temporary
                .into_iter()
                .map(|(property, temporary)| (property, final_ids[temporary]))
                .collect(),
        })
    }
}

fn inherit_ranges(
    ontology: &Ontology,
    universe: &ChainUniverse,
    subchains_by_super: &[BTreeSet<u32>],
) -> BTreeMap<u32, Vec<u32>> {
    let mut ranges = BTreeMap::<u32, BTreeSet<u32>>::new();
    for &(super_property, range_expression) in &ontology.property_ranges {
        let super_chain = universe.singleton_ids[&super_property];
        for &sub_chain in &subchains_by_super[super_chain as usize] {
            let record = universe.records[sub_chain as usize];
            if record.suffix_chain.is_none() {
                ranges
                    .entry(record.first_property)
                    .or_default()
                    .insert(range_expression);
            }
        }
    }
    ranges
        .into_iter()
        .map(|(property, values)| (property, values.into_iter().collect()))
        .collect()
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
struct Composition {
    left_property: u32,
    right_chain: u32,
    result_chain: u32,
}

fn compute_compositions(
    universe: &ChainUniverse,
    subchains_by_super: &[BTreeSet<u32>],
) -> (BTreeSet<Composition>, BTreeSet<Composition>) {
    let mut named_cache = BTreeMap::<u32, BTreeSet<u32>>::new();
    let named_subproperties = |chain: u32, cache: &mut BTreeMap<u32, BTreeSet<u32>>| {
        cache
            .entry(chain)
            .or_insert_with(|| {
                subchains_by_super[chain as usize]
                    .iter()
                    .filter_map(|&sub_chain| {
                        let record = universe.records[sub_chain as usize];
                        record
                            .suffix_chain
                            .is_none()
                            .then_some(record.first_property)
                    })
                    .collect()
            })
            .clone()
    };
    let mut left_subcomposable_cache = BTreeMap::<u32, BTreeMap<u32, BTreeSet<u32>>>::new();
    let mut non_redundant = BTreeSet::new();
    let mut redundant = BTreeSet::new();

    for (result_index, &result_record) in universe.records.iter().enumerate() {
        let Some(suffix) = result_record.suffix_chain else {
            continue;
        };
        let result_chain = result_index as u32;
        let first_property = result_record.first_property;
        let first_chain = universe.singleton_ids[&first_property];
        let left_candidates = named_subproperties(first_chain, &mut named_cache);
        let right_candidates = subchains_by_super[suffix as usize].clone();
        for &right_chain in &right_candidates {
            if first_chain == suffix && right_chain == result_chain {
                continue;
            }
            let right_record = universe.records[right_chain as usize];
            let redundant_left = if right_record
                .suffix_chain
                .is_some_and(|right_suffix| right_candidates.contains(&right_suffix))
            {
                let cached = left_subcomposable_cache
                    .entry(first_property)
                    .or_insert_with(|| {
                        let property_chain = universe.singleton_ids[&first_property];
                        let property_subs = named_subproperties(property_chain, &mut named_cache);
                        let mut values = BTreeMap::<u32, BTreeSet<u32>>::new();
                        for &complex_sub_chain in &subchains_by_super[property_chain as usize] {
                            let complex = universe.records[complex_sub_chain as usize];
                            let Some(complex_suffix) = complex.suffix_chain else {
                                continue;
                            };
                            let shared_left = property_subs
                                .intersection(&named_subproperties(
                                    universe.singleton_ids[&complex.first_property],
                                    &mut named_cache,
                                ))
                                .copied()
                                .collect::<BTreeSet<_>>();
                            if shared_left.is_empty() {
                                continue;
                            }
                            for right_property in
                                named_subproperties(complex_suffix, &mut named_cache)
                            {
                                values
                                    .entry(right_property)
                                    .or_default()
                                    .extend(&shared_left);
                            }
                        }
                        values
                    });
                cached
                    .get(&right_record.first_property)
                    .cloned()
                    .unwrap_or_default()
            } else {
                BTreeSet::new()
            };
            for &left_property in &left_candidates {
                let composition = Composition {
                    left_property,
                    right_chain,
                    result_chain,
                };
                if redundant_left.contains(&left_property) {
                    redundant.insert(composition);
                } else {
                    non_redundant.insert(composition);
                }
            }
        }
    }
    non_redundant.retain(|composition| !redundant.contains(composition));
    (non_redundant, redundant)
}

type CompositionIndex = BTreeMap<u32, BTreeMap<u32, Vec<u32>>>;

fn composition_indices(
    compositions: &BTreeSet<Composition>,
) -> (CompositionIndex, CompositionIndex) {
    let mut by_right = CompositionIndex::new();
    let mut by_left = CompositionIndex::new();
    for composition in compositions {
        by_right
            .entry(composition.right_chain)
            .or_default()
            .entry(composition.left_property)
            .or_default()
            .push(composition.result_chain);
        by_left
            .entry(composition.left_property)
            .or_default()
            .entry(composition.right_chain)
            .or_default()
            .push(composition.result_chain);
    }
    (by_right, by_left)
}

fn reflexive_properties(ontology: &Ontology) -> CoreResult<Vec<u32>> {
    let thing_entity = ontology.entity_id(EntityKind::Class, OWL_THING_IRI)?;
    let thing_expression = ontology.named_expression(ExpressionTag::Class, thing_entity)?;
    let mut result = BTreeSet::new();
    for &(sub_expression, super_expression) in &ontology.subclass_axioms {
        if sub_expression != thing_expression {
            continue;
        }
        let expression = &ontology.expressions[super_expression as usize];
        if expression.tag == ExpressionTag::ObjectHasSelf {
            result.insert(expression.arguments[0]);
        }
    }
    Ok(result.into_iter().collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ir::{
        Entity, Expression, FEATURE_VECTOR_LENGTH, OWL_BOTTOM_OBJECT_PROPERTY_IRI, OWL_NOTHING_IRI,
        OWL_TOP_OBJECT_PROPERTY_IRI, Occurrence,
    };

    fn minimal_ontology() -> Ontology {
        let entities = vec![
            Entity {
                kind: EntityKind::Class,
                iri: OWL_NOTHING_IRI.to_owned(),
            },
            Entity {
                kind: EntityKind::Class,
                iri: OWL_THING_IRI.to_owned(),
            },
            Entity {
                kind: EntityKind::ObjectProperty,
                iri: OWL_BOTTOM_OBJECT_PROPERTY_IRI.to_owned(),
            },
            Entity {
                kind: EntityKind::ObjectProperty,
                iri: OWL_TOP_OBJECT_PROPERTY_IRI.to_owned(),
            },
        ];
        Ontology {
            entities,
            expressions: vec![
                Expression {
                    tag: ExpressionTag::Class,
                    arguments: vec![0],
                    payload: vec![],
                },
                Expression {
                    tag: ExpressionTag::Class,
                    arguments: vec![1],
                    payload: vec![],
                },
            ],
            expression_occurrences: vec![Occurrence::default(); 2],
            property_occurrences: vec![Occurrence::default(); 2],
            property_chains: vec![vec![2], vec![3]],
            subclass_axioms: vec![],
            equivalent_class_axioms: vec![],
            disjoint_groups: vec![],
            subproperty_axioms: vec![],
            property_ranges: vec![],
            feature_counts: vec![0; FEATURE_VECTOR_LENGTH],
            source_fingerprint: [0; 32],
        }
    }

    #[test]
    fn every_chain_has_its_tautology() {
        let closure = PropertyClosure::build(&minimal_ontology()).unwrap();
        for chain in 0..closure.chains.len() as u32 {
            assert!(closure.super_chains(chain).contains(&chain));
            assert!(closure.sub_chains(chain).contains(&chain));
        }
    }
}
