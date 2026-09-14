//! Query-local expression registration over one immutable native program.

use std::collections::{BTreeMap, HashMap};
use std::sync::Arc;

use crate::error::{CoreError, CoreResult};
use crate::ir::{
    EntityKind, Expression, ExpressionTag, Occurrence, Ontology, QueryIr, U32_RESERVED,
};
use crate::properties::PropertyClosure;
use crate::reasoning::{ExpressionSource, RuleIndices};

/// Symbol and rule preparation shared by every class query in this session generation.
#[derive(Debug)]
pub(crate) struct QueryBase {
    pub ontology: Arc<Ontology>,
    pub properties: Arc<PropertyClosure>,
    pub rules: Arc<RuleIndices>,
    pub named_classes: Arc<BTreeMap<u32, u32>>,
    pub named_individuals: BTreeMap<u32, u32>,
    expressions: HashMap<Expression, u32>,
    property_start: usize,
}

impl QueryBase {
    pub fn new(
        ontology: Arc<Ontology>,
        properties: Arc<PropertyClosure>,
        rules: Arc<RuleIndices>,
        named_classes: Arc<BTreeMap<u32, u32>>,
    ) -> Self {
        let expressions = ontology
            .expressions
            .iter()
            .cloned()
            .enumerate()
            .map(|(id, expression)| (expression, id as u32))
            .collect();
        let property_start = ontology
            .entities
            .partition_point(|entity| entity.kind < EntityKind::ObjectProperty);
        let named_individuals =
            crate::taxonomy::named_expressions(&ontology, ExpressionTag::Individual);
        Self {
            ontology,
            properties,
            rules,
            named_classes,
            named_individuals,
            expressions,
            property_start,
        }
    }
}

/// Base numeric identities stay fixed. Only new expressions and occurrence changes are local.
#[derive(Debug)]
pub(crate) struct QueryProgram {
    pub base: Arc<QueryBase>,
    pub expression_ids: Vec<u32>,
    pub fresh_result_ids: BTreeMap<u32, u32>,
    pub properties: Arc<PropertyClosure>,
    pub rules: Arc<RuleIndices>,
    expressions: Vec<Expression>,
    occurrences: BTreeMap<usize, Occurrence>,
}

impl QueryProgram {
    /// Conservative retained allocation charge, excluding the single shared base.
    pub fn owned_bytes(&self) -> usize {
        std::mem::size_of::<Self>()
            + self.expression_ids.capacity() * 4
            + self.expressions.capacity() * std::mem::size_of::<Expression>()
            + self
                .expressions
                .iter()
                .map(|e| e.arguments.capacity() * 4 + e.payload.capacity())
                .sum::<usize>()
            + self.occurrences.len() * (64 + 12 * std::mem::size_of::<(usize, Occurrence)>())
            + self.fresh_result_ids.len() * (64 + 12 * std::mem::size_of::<(u32, u32)>())
            + self.rules.query_owned_bytes()
            + if Arc::ptr_eq(&self.properties, &self.base.properties) {
                0
            } else {
                self.properties.query_owned_bytes()
            }
    }

    pub fn new(base: Arc<QueryBase>, query: &QueryIr) -> CoreResult<Self> {
        let mut entity_ids = Vec::with_capacity(query.entities.len());
        let mut fresh_result_ids = BTreeMap::new();
        let mut fresh_properties = Vec::new();
        let mut property_occurrences = query.property_occurrences.iter();
        for (index, record) in query.entities.iter().enumerate() {
            let id = match record.ontology_id {
                Some(id) => id,
                None => {
                    let id = base
                        .ontology
                        .entities
                        .len()
                        .checked_add(fresh_result_ids.len())
                        .filter(|&id| id < U32_RESERVED as usize)
                        .ok_or_else(|| CoreError::capacity("fresh result namespace exhausted"))?
                        as u32;
                    fresh_result_ids.insert(index as u32, id);
                    if record.entity.kind == EntityKind::ObjectProperty {
                        fresh_properties.push(id);
                    }
                    id
                }
            };
            entity_ids.push(id);
            if record.entity.kind == EntityKind::ObjectProperty {
                let occurrence = property_occurrences
                    .next()
                    .ok_or_else(|| CoreError::internal("query property occurrence disappeared"))?;
                if let Some(id) = record.ontology_id {
                    add_occurrences(
                        base.ontology.property_occurrences[id as usize - base.property_start],
                        *occurrence,
                    )?;
                }
            }
        }
        let properties = if fresh_properties.is_empty() {
            Arc::clone(&base.properties)
        } else {
            Arc::new(base.properties.with_fresh_properties(&fresh_properties)?)
        };
        let mut result = Self {
            rules: Arc::clone(&base.rules),
            base,
            expression_ids: Vec::with_capacity(query.expressions.len()),
            fresh_result_ids,
            properties,
            expressions: Vec::new(),
            occurrences: BTreeMap::new(),
        };
        let mut local_ids = HashMap::new();
        let mut previous = BTreeMap::new();
        for (index, expression) in query.expressions.iter().enumerate() {
            let arguments = match expression.tag {
                ExpressionTag::ObjectSomeValuesFrom => vec![
                    entity_ids[expression.arguments[0] as usize],
                    result.expression_ids[expression.arguments[1] as usize],
                ],
                ExpressionTag::ObjectIntersectionOf
                | ExpressionTag::ObjectComplementOf
                | ExpressionTag::ObjectUnionOf => expression
                    .arguments
                    .iter()
                    .map(|&id| result.expression_ids[id as usize])
                    .collect(),
                _ => expression
                    .arguments
                    .iter()
                    .map(|&id| entity_ids[id as usize])
                    .collect(),
            };
            let mapped = Expression {
                tag: expression.tag,
                payload: expression.payload.clone(),
                arguments,
            };
            let id = match result
                .base
                .expressions
                .get(&mapped)
                .or_else(|| local_ids.get(&mapped))
            {
                Some(&id) => id,
                None => {
                    let id = result
                        .base
                        .ontology
                        .expressions
                        .len()
                        .checked_add(result.expressions.len())
                        .filter(|&id| id < U32_RESERVED as usize)
                        .ok_or_else(|| {
                            CoreError::capacity("query expression namespace exhausted")
                        })? as u32;
                    local_ids.insert(mapped.clone(), id);
                    result.expressions.push(mapped);
                    id
                }
            };
            let old = result.occurrence(id as usize);
            previous.entry(id as usize).or_insert(old);
            result.occurrences.insert(
                id as usize,
                add_occurrences(old, query.expression_occurrences[index])?,
            );
            result.expression_ids.push(id);
        }
        result.rules = Arc::new(result.base.rules.with_query(&result, &previous));
        Ok(result)
    }
}

impl ExpressionSource for QueryProgram {
    fn expression(&self, id: usize) -> &Expression {
        if id < self.base.ontology.expressions.len() {
            &self.base.ontology.expressions[id]
        } else {
            &self.expressions[id - self.base.ontology.expressions.len()]
        }
    }
    fn occurrence(&self, id: usize) -> Occurrence {
        self.occurrences.get(&id).copied().unwrap_or_else(|| {
            self.base
                .ontology
                .expression_occurrences
                .get(id)
                .copied()
                .unwrap_or_default()
        })
    }
    fn expression_count(&self) -> usize {
        self.base.ontology.expressions.len() + self.expressions.len()
    }
}

fn add_occurrences(previous: Occurrence, added: Occurrence) -> CoreResult<Occurrence> {
    Ok(Occurrence {
        negative: previous
            .negative
            .checked_add(added.negative)
            .ok_or_else(|| CoreError::capacity("negative occurrence count overflow"))?,
        positive: previous
            .positive
            .checked_add(added.positive)
            .ok_or_else(|| CoreError::capacity("positive occurrence count overflow"))?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ir::{
        Entity, OWL_BOTTOM_OBJECT_PROPERTY_IRI, OWL_NOTHING_IRI, OWL_THING_IRI,
        OWL_TOP_OBJECT_PROPERTY_IRI, QueryEntity, QueryIrKind,
    };

    fn base(extra_classes: usize) -> Arc<QueryBase> {
        let mut entities = vec![
            Entity {
                kind: EntityKind::Class,
                iri: OWL_NOTHING_IRI.into(),
            },
            Entity {
                kind: EntityKind::Class,
                iri: OWL_THING_IRI.into(),
            },
        ];
        entities.extend((0..extra_classes).map(|id| Entity {
            kind: EntityKind::Class,
            iri: format!("urn:unrelated:{id:08}"),
        }));
        let expressions = (0..entities.len())
            .map(|id| Expression {
                tag: ExpressionTag::Class,
                payload: vec![],
                arguments: vec![id as u32],
            })
            .collect::<Vec<_>>();
        entities.extend([
            Entity {
                kind: EntityKind::ObjectProperty,
                iri: OWL_BOTTOM_OBJECT_PROPERTY_IRI.into(),
            },
            Entity {
                kind: EntityKind::ObjectProperty,
                iri: OWL_TOP_OBJECT_PROPERTY_IRI.into(),
            },
        ]);
        let ontology = Arc::new(Ontology {
            expression_occurrences: vec![Occurrence::default(); expressions.len()],
            entities,
            expressions,
            property_occurrences: vec![Occurrence::default(); 2],
            property_chains: vec![],
            subclass_axioms: vec![],
            equivalent_class_axioms: vec![],
            disjoint_groups: vec![],
            subproperty_axioms: vec![],
            property_ranges: vec![],
            feature_counts: vec![0; 79],
            source_fingerprint: [0; 32],
        });
        let properties = Arc::new(PropertyClosure::build(&ontology).unwrap());
        let rules = Arc::new(RuleIndices::new(&ontology, &properties).unwrap());
        let classes = Arc::new(crate::taxonomy::named_expressions(
            &ontology,
            ExpressionTag::Class,
        ));
        Arc::new(QueryBase::new(ontology, properties, rules, classes))
    }

    #[test]
    fn query_owned_allocations_do_not_scale_with_unrelated_base_classes() {
        let query = QueryIr {
            kind: QueryIrKind::ClassExpression,
            entities: ["urn:fresh:A", "urn:fresh:B"]
                .into_iter()
                .map(|iri| QueryEntity {
                    entity: Entity {
                        kind: EntityKind::Class,
                        iri: iri.into(),
                    },
                    ontology_id: None,
                })
                .collect(),
            expressions: vec![
                Expression {
                    tag: ExpressionTag::Class,
                    payload: vec![],
                    arguments: vec![0],
                },
                Expression {
                    tag: ExpressionTag::Class,
                    payload: vec![],
                    arguments: vec![1],
                },
                Expression {
                    tag: ExpressionTag::ObjectIntersectionOf,
                    payload: vec![],
                    arguments: vec![0, 1],
                },
            ],
            expression_occurrences: vec![
                Occurrence {
                    negative: 1,
                    positive: 1
                };
                3
            ],
            property_occurrences: vec![],
            root_expression: Some(2),
            subsumption_obligations: vec![],
        };
        let small = base(2);
        let large = base(1000);
        let first = QueryProgram::new(Arc::clone(&small), &query).unwrap();
        let second = QueryProgram::new(Arc::clone(&large), &query).unwrap();
        assert_eq!(first.owned_bytes(), second.owned_bytes());
        assert!(Arc::ptr_eq(&second.base, &large));
        assert!(Arc::ptr_eq(&second.properties, &large.properties));
        assert_eq!(large.ontology.expressions.len(), 1002);
        assert_eq!(second.expressions.len(), 3);
        assert_eq!(second.occurrences.len(), 3);
    }
}
