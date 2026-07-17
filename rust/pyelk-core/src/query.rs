//! Query mini-IR installation, class selection, and entailment support.

use std::cmp::Reverse;
use std::collections::{BTreeMap, BTreeSet, BinaryHeap};

use crate::error::{CoreError, CoreResult};
use crate::ir::{
    Entity, EntityKind, Expression, ExpressionTag, Occurrence, Ontology, QueryIr, QueryIrKind,
    U32_RESERVED,
};
use crate::properties::PropertyClosure;
use crate::reasoning::{ContextSnapshot, saturate_root};
use crate::result::{QueryKind, RawQueryResult, RawRealization, RawTaxonomy};
use crate::taxonomy::{
    direct_type_indices, relative_indices, strict_super_closure, taxonomy_node_index,
};

/// Private canonical overlay and both source-to-overlay expression maps.
#[derive(Clone, Debug)]
pub struct InstalledQuery {
    pub overlay: Ontology,
    pub ontology_expression_ids: Vec<u32>,
    pub query_expression_ids: Vec<u32>,
    pub fresh_result_ids: BTreeMap<u32, u32>,
}

/// Install query occurrences into a private overlay without changing base enumeration.
pub fn install_query(base: &Ontology, query: &QueryIr) -> CoreResult<InstalledQuery> {
    let base_lookup = base
        .entities
        .iter()
        .enumerate()
        .map(|(index, entity)| (entity.clone(), index as u32))
        .collect::<BTreeMap<_, _>>();
    for record in &query.entities {
        let actual = base_lookup.get(&record.entity).copied();
        match record.ontology_id {
            None if actual.is_some() => {
                return Err(CoreError::invalid(
                    "query marks an existing ontology entity as fresh",
                ));
            }
            Some(expected) if actual != Some(expected) => {
                return Err(CoreError::invalid(
                    "query ontology entity reference does not match session table",
                ));
            }
            _ => {}
        }
    }

    let mut entities = base.entities.iter().cloned().collect::<BTreeSet<_>>();
    entities.extend(query.entities.iter().map(|record| record.entity.clone()));
    let entities = entities.into_iter().collect::<Vec<_>>();
    let entity_ids = entities
        .iter()
        .enumerate()
        .map(|(index, entity)| (entity.clone(), index as u32))
        .collect::<BTreeMap<_, _>>();
    let base_entity_ids = base
        .entities
        .iter()
        .map(|entity| entity_ids[entity])
        .collect::<Vec<_>>();
    let query_entity_ids = query
        .entities
        .iter()
        .map(|record| entity_ids[&record.entity])
        .collect::<Vec<_>>();

    let mut temporary = Vec::<Expression>::new();
    let mut temporary_ids = BTreeMap::<Expression, usize>::new();
    let mut temporary_occurrences = Vec::<Occurrence>::new();
    let base_handles = load_expressions(
        &base.entities,
        &base_entity_ids,
        &base.expressions,
        &base.expression_occurrences,
        &mut temporary,
        &mut temporary_ids,
        &mut temporary_occurrences,
    )?;
    let query_entities = query
        .entities
        .iter()
        .map(|record| record.entity.clone())
        .collect::<Vec<_>>();
    let query_handles = load_expressions(
        &query_entities,
        &query_entity_ids,
        &query.expressions,
        &query.expression_occurrences,
        &mut temporary,
        &mut temporary_ids,
        &mut temporary_occurrences,
    )?;
    let (expressions, expression_occurrences, final_ids) =
        canonicalize_expressions(&temporary, &temporary_occurrences)?;
    let ontology_expression_ids = base_handles
        .into_iter()
        .map(|handle| final_ids[handle])
        .collect::<Vec<_>>();
    let query_expression_ids = query_handles
        .into_iter()
        .map(|handle| final_ids[handle])
        .collect::<Vec<_>>();

    let mut property_occurrences = BTreeMap::<u32, Occurrence>::new();
    add_property_occurrences(
        base,
        &base_entity_ids,
        &base.property_occurrences,
        &mut property_occurrences,
    )?;
    add_query_property_occurrences(
        &query_entities,
        &query_entity_ids,
        &query.property_occurrences,
        &mut property_occurrences,
    )?;
    let property_occurrences = entities
        .iter()
        .enumerate()
        .filter_map(|(index, entity)| {
            (entity.kind == EntityKind::ObjectProperty).then_some(
                property_occurrences
                    .get(&(index as u32))
                    .copied()
                    .unwrap_or_default(),
            )
        })
        .collect::<Vec<_>>();

    let mut property_chains = base
        .property_chains
        .iter()
        .map(|chain| {
            chain
                .iter()
                .map(|&property| base_entity_ids[property as usize])
                .collect::<Vec<_>>()
        })
        .collect::<Vec<_>>();
    property_chains.sort();
    property_chains.dedup();
    let chain_ids = property_chains
        .iter()
        .enumerate()
        .map(|(index, chain)| (chain.clone(), index as u32))
        .collect::<BTreeMap<_, _>>();
    let mut subclass_axioms = remap_pairs(&base.subclass_axioms, &ontology_expression_ids);
    subclass_axioms.sort_unstable();
    subclass_axioms.dedup();
    let mut equivalent_class_axioms =
        remap_pairs(&base.equivalent_class_axioms, &ontology_expression_ids);
    equivalent_class_axioms.sort_unstable();
    equivalent_class_axioms.dedup();
    let mut disjoint_groups = base
        .disjoint_groups
        .iter()
        .map(|group| {
            group
                .iter()
                .map(|&expression| ontology_expression_ids[expression as usize])
                .collect::<Vec<_>>()
        })
        .collect::<Vec<_>>();
    disjoint_groups.sort();
    disjoint_groups.dedup();
    let mut subproperty_axioms = base
        .subproperty_axioms
        .iter()
        .map(|&(old_chain, super_property)| {
            let remapped_chain = base.property_chains[old_chain as usize]
                .iter()
                .map(|&property| base_entity_ids[property as usize])
                .collect::<Vec<_>>();
            (
                chain_ids[&remapped_chain],
                base_entity_ids[super_property as usize],
            )
        })
        .collect::<Vec<_>>();
    subproperty_axioms.sort_unstable();
    subproperty_axioms.dedup();
    let mut property_ranges = base
        .property_ranges
        .iter()
        .map(|&(property, range)| {
            (
                base_entity_ids[property as usize],
                ontology_expression_ids[range as usize],
            )
        })
        .collect::<Vec<_>>();
    property_ranges.sort_unstable();
    property_ranges.dedup();

    let mut fresh_result_ids = BTreeMap::new();
    let mut fresh_rank = 0_u32;
    let base_count = u32::try_from(base.entities.len())
        .map_err(|_| CoreError::capacity("ontology entity namespace exhausted"))?;
    for (query_entity, record) in query.entities.iter().enumerate() {
        if record.ontology_id.is_none() {
            let result_id = base_count
                .checked_add(fresh_rank)
                .ok_or_else(|| CoreError::capacity("fresh result namespace overflow"))?;
            if result_id == U32_RESERVED {
                return Err(CoreError::capacity("fresh result namespace exhausted"));
            }
            fresh_result_ids.insert(query_entity as u32, result_id);
            fresh_rank += 1;
        }
    }
    Ok(InstalledQuery {
        overlay: Ontology {
            entities,
            expressions,
            expression_occurrences,
            property_occurrences,
            property_chains,
            subclass_axioms,
            equivalent_class_axioms,
            disjoint_groups,
            subproperty_axioms,
            property_ranges,
            feature_counts: base.feature_counts.clone(),
            source_fingerprint: base.source_fingerprint,
        },
        ontology_expression_ids,
        query_expression_ids,
        fresh_result_ids,
    })
}

fn load_expressions(
    source_entities: &[Entity],
    mapped_entities: &[u32],
    expressions: &[Expression],
    occurrences: &[Occurrence],
    temporary: &mut Vec<Expression>,
    temporary_ids: &mut BTreeMap<Expression, usize>,
    temporary_occurrences: &mut Vec<Occurrence>,
) -> CoreResult<Vec<usize>> {
    let mut handles = Vec::with_capacity(expressions.len());
    for (index, expression) in expressions.iter().enumerate() {
        let arguments = match expression.tag {
            ExpressionTag::ObjectSomeValuesFrom => vec![
                mapped_entities[expression.arguments[0] as usize],
                u32::try_from(handles[expression.arguments[1] as usize])
                    .map_err(|_| CoreError::capacity("temporary expression namespace exhausted"))?,
            ],
            ExpressionTag::ObjectIntersectionOf
            | ExpressionTag::ObjectComplementOf
            | ExpressionTag::ObjectUnionOf => expression
                .arguments
                .iter()
                .map(|&dependency| {
                    u32::try_from(handles[dependency as usize]).map_err(|_| {
                        CoreError::capacity("temporary expression namespace exhausted")
                    })
                })
                .collect::<CoreResult<Vec<_>>>()?,
            _ => expression
                .arguments
                .iter()
                .map(|&entity| {
                    let source = source_entities.get(entity as usize).ok_or_else(|| {
                        CoreError::internal("expression entity reference is out of range")
                    })?;
                    let source_index = source_entities
                        .iter()
                        .position(|candidate| candidate == source)
                        .ok_or_else(|| CoreError::internal("expression entity disappeared"))?;
                    Ok(mapped_entities[source_index])
                })
                .collect::<CoreResult<Vec<_>>>()?,
        };
        let remapped = Expression {
            tag: expression.tag,
            payload: expression.payload.clone(),
            arguments,
        };
        let handle = if let Some(&handle) = temporary_ids.get(&remapped) {
            handle
        } else {
            let handle = temporary.len();
            temporary_ids.insert(remapped.clone(), handle);
            temporary.push(remapped);
            temporary_occurrences.push(Occurrence::default());
            handle
        };
        add_occurrence(&mut temporary_occurrences[handle], occurrences[index])?;
        handles.push(handle);
    }
    Ok(handles)
}

fn canonicalize_expressions(
    temporary: &[Expression],
    occurrences: &[Occurrence],
) -> CoreResult<(Vec<Expression>, Vec<Occurrence>, Vec<u32>)> {
    let mut dependents = vec![Vec::<usize>::new(); temporary.len()];
    let mut remaining = vec![0_usize; temporary.len()];
    let mut final_ids = vec![U32_RESERVED; temporary.len()];
    let mut available = BinaryHeap::<Reverse<(Expression, usize)>>::new();
    for (handle, expression) in temporary.iter().enumerate() {
        let dependencies = temporary_dependencies(expression);
        remaining[handle] = dependencies.len();
        if dependencies.is_empty() {
            available.push(Reverse((expression.clone(), handle)));
        }
        for dependency in dependencies {
            dependents[dependency].push(handle);
        }
    }
    let mut expressions = Vec::with_capacity(temporary.len());
    let mut final_occurrences = Vec::with_capacity(temporary.len());
    while let Some(Reverse((expression, handle))) = available.pop() {
        let final_id = u32::try_from(expressions.len())
            .map_err(|_| CoreError::capacity("overlay expression namespace exhausted"))?;
        final_ids[handle] = final_id;
        expressions.push(expression);
        final_occurrences.push(occurrences[handle]);
        for &dependent in &dependents[handle] {
            remaining[dependent] -= 1;
            if remaining[dependent] == 0 {
                let mapped = remap_temporary_expression(&temporary[dependent], &final_ids)?;
                available.push(Reverse((mapped, dependent)));
            }
        }
    }
    if expressions.len() != temporary.len() {
        return Err(CoreError::internal("overlay expression graph is cyclic"));
    }
    Ok((expressions, final_occurrences, final_ids))
}

fn temporary_dependencies(expression: &Expression) -> BTreeSet<usize> {
    match expression.tag {
        ExpressionTag::ObjectSomeValuesFrom => BTreeSet::from([expression.arguments[1] as usize]),
        ExpressionTag::ObjectIntersectionOf
        | ExpressionTag::ObjectComplementOf
        | ExpressionTag::ObjectUnionOf => expression
            .arguments
            .iter()
            .map(|&value| value as usize)
            .collect(),
        _ => BTreeSet::new(),
    }
}

fn remap_temporary_expression(
    expression: &Expression,
    final_ids: &[u32],
) -> CoreResult<Expression> {
    let arguments = match expression.tag {
        ExpressionTag::ObjectSomeValuesFrom => vec![
            expression.arguments[0],
            final_dependency(final_ids, expression.arguments[1])?,
        ],
        ExpressionTag::ObjectIntersectionOf
        | ExpressionTag::ObjectComplementOf
        | ExpressionTag::ObjectUnionOf => expression
            .arguments
            .iter()
            .map(|&dependency| final_dependency(final_ids, dependency))
            .collect::<CoreResult<Vec<_>>>()?,
        _ => expression.arguments.clone(),
    };
    Ok(Expression {
        tag: expression.tag,
        payload: expression.payload.clone(),
        arguments,
    })
}

fn final_dependency(final_ids: &[u32], dependency: u32) -> CoreResult<u32> {
    let value = final_ids
        .get(dependency as usize)
        .copied()
        .ok_or_else(|| CoreError::internal("temporary dependency is out of range"))?;
    if value == U32_RESERVED {
        return Err(CoreError::internal(
            "temporary dependency was not assigned before its parent",
        ));
    }
    Ok(value)
}

fn remap_pairs(values: &[(u32, u32)], ids: &[u32]) -> Vec<(u32, u32)> {
    values
        .iter()
        .map(|&(first, second)| (ids[first as usize], ids[second as usize]))
        .collect()
}

fn add_property_occurrences(
    ontology: &Ontology,
    mapped_entities: &[u32],
    occurrences: &[Occurrence],
    result: &mut BTreeMap<u32, Occurrence>,
) -> CoreResult<()> {
    let properties = ontology
        .entities
        .iter()
        .enumerate()
        .filter_map(|(index, entity)| {
            (entity.kind == EntityKind::ObjectProperty).then_some(mapped_entities[index])
        })
        .collect::<Vec<_>>();
    for (&property, &occurrence) in properties.iter().zip(occurrences) {
        add_occurrence(result.entry(property).or_default(), occurrence)?;
    }
    Ok(())
}

fn add_query_property_occurrences(
    entities: &[Entity],
    mapped_entities: &[u32],
    occurrences: &[Occurrence],
    result: &mut BTreeMap<u32, Occurrence>,
) -> CoreResult<()> {
    let properties = entities
        .iter()
        .enumerate()
        .filter_map(|(index, entity)| {
            (entity.kind == EntityKind::ObjectProperty).then_some(mapped_entities[index])
        })
        .collect::<Vec<_>>();
    for (&property, &occurrence) in properties.iter().zip(occurrences) {
        add_occurrence(result.entry(property).or_default(), occurrence)?;
    }
    Ok(())
}

fn add_occurrence(target: &mut Occurrence, value: Occurrence) -> CoreResult<()> {
    target.negative = target
        .negative
        .checked_add(value.negative)
        .ok_or_else(|| CoreError::capacity("negative occurrence count overflow"))?;
    target.positive = target
        .positive
        .checked_add(value.positive)
        .ok_or_else(|| CoreError::capacity("positive occurrence count overflow"))?;
    Ok(())
}

/// Cached class-query evaluation over one private overlay.
#[derive(Debug)]
pub struct QueryEvaluation {
    query: QueryIr,
    installed: InstalledQuery,
    root: u32,
    properties: PropertyClosure,
    contexts: BTreeMap<u32, ContextSnapshot>,
}

impl QueryEvaluation {
    pub fn new(base: &Ontology, query: QueryIr) -> CoreResult<Self> {
        if query.kind != QueryIrKind::ClassExpression {
            return Err(CoreError::invalid(
                "class query requires CLASS_EXPRESSION mini-IR",
            ));
        }
        let installed = install_query(base, &query)?;
        let local_root = query
            .root_expression
            .ok_or_else(|| CoreError::internal("class query lost its root"))?;
        let root = installed.query_expression_ids[local_root as usize];
        let properties = PropertyClosure::build(&installed.overlay)?;
        Ok(Self {
            query,
            installed,
            root,
            properties,
            contexts: BTreeMap::new(),
        })
    }

    fn ensure_contexts<I>(&mut self, roots: I) -> CoreResult<()>
    where
        I: IntoIterator<Item = u32>,
    {
        let requested = roots
            .into_iter()
            .filter(|root| !self.contexts.contains_key(root))
            .collect::<BTreeSet<_>>();
        for root in requested {
            let (context, _counters) =
                saturate_root(&self.installed.overlay, &self.properties, root)?;
            self.contexts.insert(root, context);
        }
        Ok(())
    }

    pub fn select(
        &mut self,
        base: &Ontology,
        taxonomy: &RawTaxonomy,
        realized: &RawRealization,
        kind: QueryKind,
        direct: bool,
    ) -> CoreResult<RawQueryResult> {
        self.ensure_contexts([self.root])?;
        let root_context = self.contexts[&self.root].clone();
        if kind == QueryKind::Satisfiable {
            return Ok(RawQueryResult::boolean(kind, !root_context.inconsistent));
        }
        let class_expressions = base
            .expressions
            .iter()
            .enumerate()
            .filter_map(|(index, expression)| {
                (expression.tag == ExpressionTag::Class).then_some((
                    expression.arguments[0],
                    self.installed.ontology_expression_ids[index],
                ))
            })
            .collect::<BTreeMap<_, _>>();
        let expression_to_node = taxonomy
            .nodes
            .iter()
            .enumerate()
            .flat_map(|(node, members)| {
                let class_expressions = &class_expressions;
                members.iter().filter_map(move |entity| {
                    class_expressions
                        .get(entity)
                        .copied()
                        .map(|expression| (expression, node as u32))
                })
            })
            .collect::<BTreeMap<_, _>>();
        let root_subsumers = root_context.subsumers();
        let strict_supers = strict_super_closure(taxonomy)?;
        let fresh_classes = self.fresh_class_candidates();
        let mut possible_equivalents = taxonomy
            .nodes
            .iter()
            .filter_map(|node| {
                let expression = class_expressions[&node[0]];
                root_subsumers.contains(&expression).then_some(expression)
            })
            .collect::<BTreeSet<_>>();
        possible_equivalents.extend(fresh_classes.iter().filter_map(|candidate| {
            root_subsumers
                .contains(&candidate.expression)
                .then_some(candidate.expression)
        }));
        self.ensure_contexts(possible_equivalents)?;

        let mut equivalent_index = root_context.inconsistent.then_some(taxonomy.bottom);
        if equivalent_index.is_none() {
            for (node_index, node) in taxonomy.nodes.iter().enumerate() {
                let representative = class_expressions[&node[0]];
                if root_subsumers.contains(&representative)
                    && self.contexts[&representative]
                        .subsumers()
                        .contains(&self.root)
                {
                    equivalent_index = Some(node_index as u32);
                    break;
                }
            }
        }
        let fresh_equivalent = fresh_classes.iter().find(|candidate| {
            root_subsumers.contains(&candidate.expression)
                && self.contexts[&candidate.expression]
                    .subsumers()
                    .contains(&self.root)
        });
        if kind == QueryKind::EquivalentClasses {
            let nodes = if let Some(index) = equivalent_index {
                vec![taxonomy.nodes[index as usize].clone()]
            } else if let Some(candidate) = fresh_equivalent {
                vec![candidate.members.clone()]
            } else {
                Vec::new()
            };
            return Ok(RawQueryResult::nodes(kind, nodes));
        }

        if matches!(kind, QueryKind::Subclasses | QueryKind::Superclasses) {
            let supers = kind == QueryKind::Superclasses;
            if !supers {
                self.ensure_contexts(
                    class_expressions
                        .values()
                        .copied()
                        .chain(fresh_classes.iter().map(|candidate| candidate.expression)),
                )?;
            }
            let mut ontology_candidates = if let Some(index) = equivalent_index {
                relative_indices(taxonomy, index, supers, false)?
                    .into_iter()
                    .collect::<BTreeSet<_>>()
            } else if supers {
                let mut values = BTreeSet::from([taxonomy.top]);
                values.extend(
                    root_subsumers
                        .iter()
                        .filter_map(|expression| expression_to_node.get(expression).copied()),
                );
                values
            } else {
                let mut values = BTreeSet::from([taxonomy.bottom]);
                values.extend(
                    taxonomy
                        .nodes
                        .iter()
                        .enumerate()
                        .filter_map(|(index, node)| {
                            self.contexts[&class_expressions[&node[0]]]
                                .subsumers()
                                .contains(&self.root)
                                .then_some(index as u32)
                        }),
                );
                values
            };
            let mut candidates = ontology_candidates
                .iter()
                .map(|&index| NodeCandidate {
                    members: taxonomy.nodes[index as usize].clone(),
                    expression: class_expressions[&taxonomy.nodes[index as usize][0]],
                    taxonomy_index: Some(index),
                })
                .collect::<Vec<_>>();
            candidates.extend(fresh_classes.iter().filter_map(|candidate| {
                (candidate.expression != self.root
                    && if supers {
                        root_subsumers.contains(&candidate.expression)
                    } else {
                        self.contexts[&candidate.expression]
                            .subsumers()
                            .contains(&self.root)
                    })
                .then_some(candidate.clone())
            }));
            if direct {
                self.ensure_contexts(candidates.iter().map(|candidate| candidate.expression))?;
                candidates = direct_candidates(
                    &candidates,
                    supers,
                    &self.contexts,
                    taxonomy,
                    &strict_supers,
                );
            }
            let mut nodes = candidates
                .into_iter()
                .map(|candidate| candidate.members)
                .collect::<Vec<_>>();
            nodes.sort();
            nodes.dedup();
            ontology_candidates.clear();
            return Ok(RawQueryResult::nodes(kind, nodes));
        }

        if kind != QueryKind::Instances {
            return Err(CoreError::internal("unhandled query kind"));
        }
        let selected = if let Some(equivalent) = equivalent_index {
            let mut matching_class_nodes = BTreeSet::from([equivalent]);
            matching_class_nodes.extend(
                (0..taxonomy.nodes.len() as u32)
                    .filter(|node| strict_supers[*node as usize].contains(&equivalent)),
            );
            (0..realized.instance_nodes.len() as u32)
                .filter(|instance| {
                    direct_type_indices(realized, *instance)
                        .iter()
                        .any(|direct_type| {
                            if direct {
                                *direct_type == equivalent
                            } else {
                                matching_class_nodes.contains(direct_type)
                            }
                        })
                })
                .collect::<BTreeSet<_>>()
        } else {
            let individual_expressions = base
                .expressions
                .iter()
                .enumerate()
                .filter_map(|(index, expression)| {
                    (expression.tag == ExpressionTag::Individual).then_some((
                        expression.arguments[0],
                        self.installed.ontology_expression_ids[index],
                    ))
                })
                .collect::<BTreeMap<_, _>>();
            self.ensure_contexts(individual_expressions.values().copied())?;
            let mut selected = realized
                .instance_nodes
                .iter()
                .enumerate()
                .filter_map(|(index, node)| {
                    self.contexts[&individual_expressions[&node[0]]]
                        .subsumers()
                        .contains(&self.root)
                        .then_some(index as u32)
                })
                .collect::<BTreeSet<_>>();
            if direct && !selected.is_empty() {
                let selected_direct_types = selected
                    .iter()
                    .flat_map(|instance| direct_type_indices(realized, *instance))
                    .collect::<BTreeSet<_>>();
                self.ensure_contexts(selected_direct_types.iter().map(|class_node| {
                    class_expressions[&taxonomy.nodes[*class_node as usize][0]]
                }))?;
                let mut strict_subclasses = BTreeSet::from([taxonomy.bottom]);
                strict_subclasses.extend(selected_direct_types.iter().filter_map(|class_node| {
                    self.contexts[&class_expressions[&taxonomy.nodes[*class_node as usize][0]]]
                        .subsumers()
                        .contains(&self.root)
                        .then_some(*class_node)
                }));
                selected.retain(|instance| {
                    !direct_type_indices(realized, *instance)
                        .iter()
                        .any(|class_node| strict_subclasses.contains(class_node))
                });
            }
            selected
        };
        let nodes = selected
            .into_iter()
            .map(|index| realized.instance_nodes[index as usize].clone())
            .collect();
        Ok(RawQueryResult::nodes(kind, nodes))
    }

    fn fresh_class_candidates(&self) -> Vec<NodeCandidate> {
        self.query
            .expressions
            .iter()
            .enumerate()
            .filter_map(|(index, expression)| {
                if expression.tag != ExpressionTag::Class {
                    return None;
                }
                self.installed
                    .fresh_result_ids
                    .get(&expression.arguments[0])
                    .copied()
                    .map(|result_id| NodeCandidate {
                        members: vec![result_id],
                        expression: self.installed.query_expression_ids[index],
                        taxonomy_index: None,
                    })
            })
            .collect()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct NodeCandidate {
    members: Vec<u32>,
    expression: u32,
    taxonomy_index: Option<u32>,
}

fn direct_candidates(
    candidates: &[NodeCandidate],
    supers: bool,
    contexts: &BTreeMap<u32, ContextSnapshot>,
    taxonomy: &RawTaxonomy,
    strict_supers: &[BTreeSet<u32>],
) -> Vec<NodeCandidate> {
    candidates
        .iter()
        .filter(|candidate| {
            !candidates.iter().any(|other| {
                if other == *candidate {
                    return false;
                }
                let (sub, super_candidate) = if supers {
                    (other, *candidate)
                } else {
                    (*candidate, other)
                };
                candidate_subsumes(sub, super_candidate, contexts, taxonomy, strict_supers)
            })
        })
        .cloned()
        .collect()
}

fn candidate_subsumes(
    sub: &NodeCandidate,
    super_candidate: &NodeCandidate,
    contexts: &BTreeMap<u32, ContextSnapshot>,
    taxonomy: &RawTaxonomy,
    strict_supers: &[BTreeSet<u32>],
) -> bool {
    if sub.expression == super_candidate.expression {
        return false;
    }
    if let (Some(sub_index), Some(super_index)) =
        (sub.taxonomy_index, super_candidate.taxonomy_index)
    {
        return strict_supers[sub_index as usize].contains(&super_index);
    }
    if sub.taxonomy_index == Some(taxonomy.bottom)
        || super_candidate.taxonomy_index == Some(taxonomy.top)
    {
        return true;
    }
    let context = &contexts[&sub.expression];
    context.inconsistent || context.subsumers().contains(&super_candidate.expression)
}

/// Pinned unindexed-query fallback.
pub fn unindexed_result(kind: QueryKind, direct: bool, taxonomy: &RawTaxonomy) -> RawQueryResult {
    match (kind, direct) {
        (QueryKind::Satisfiable, _) => RawQueryResult::boolean(kind, true),
        (QueryKind::Subclasses, true) => {
            RawQueryResult::nodes(kind, vec![taxonomy.nodes[taxonomy.bottom as usize].clone()])
        }
        (QueryKind::Superclasses, true) => {
            RawQueryResult::nodes(kind, vec![taxonomy.nodes[taxonomy.top as usize].clone()])
        }
        _ => RawQueryResult::nodes(kind, Vec::new()),
    }
}

/// Pinned quiet inconsistent-query fallback.
pub fn inconsistent_result(
    kind: QueryKind,
    taxonomy: &RawTaxonomy,
    realized: &RawRealization,
) -> RawQueryResult {
    match kind {
        QueryKind::Satisfiable => RawQueryResult::boolean(kind, false),
        QueryKind::EquivalentClasses => {
            RawQueryResult::nodes(kind, vec![taxonomy.nodes[taxonomy.top as usize].clone()])
        }
        QueryKind::Instances => RawQueryResult::nodes(kind, realized.instance_nodes.clone()),
        _ => RawQueryResult::nodes(kind, Vec::new()),
    }
}

/// Decide all normalized subsumption obligations in an entailment query.
pub fn decide_entailment(base: &Ontology, query: &QueryIr) -> CoreResult<bool> {
    if query.kind != QueryIrKind::Entailment {
        return Err(CoreError::invalid("entailment requires ENTAILMENT mini-IR"));
    }
    let mut installed = install_query(base, query)?;
    installed.overlay.property_ranges.clear();
    let properties = PropertyClosure::build(&installed.overlay)?;
    for &(sub, super_expression) in &query.subsumption_obligations {
        let sub = installed.query_expression_ids[sub as usize];
        let super_expression = installed.query_expression_ids[super_expression as usize];
        let (context, _counters) = saturate_root(&installed.overlay, &properties, sub)?;
        if !context.inconsistent && !context.subsumers().contains(&super_expression) {
            return Ok(false);
        }
    }
    Ok(true)
}

/// Select named taxonomy relatives; useful to native internal tests.
pub fn named_taxonomy_query(
    taxonomy: &RawTaxonomy,
    entity: u32,
    kind: QueryKind,
    direct: bool,
) -> CoreResult<RawQueryResult> {
    let node = taxonomy_node_index(taxonomy, entity)
        .ok_or_else(|| CoreError::invalid(format!("unknown taxonomy entity {entity}")))?;
    let nodes = match kind {
        QueryKind::EquivalentClasses => vec![taxonomy.nodes[node as usize].clone()],
        QueryKind::Subclasses | QueryKind::Superclasses => {
            relative_indices(taxonomy, node, kind == QueryKind::Superclasses, direct)?
                .into_iter()
                .map(|index| taxonomy.nodes[index as usize].clone())
                .collect()
        }
        _ => {
            return Err(CoreError::invalid(
                "named taxonomy query kind is unsupported",
            ));
        }
    };
    Ok(RawQueryResult::nodes(kind, nodes))
}
