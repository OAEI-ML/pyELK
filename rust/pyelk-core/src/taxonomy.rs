//! Canonical taxonomy reduction and instance realization.

use std::collections::{BTreeMap, BTreeSet};

use crate::error::{CoreError, CoreResult};
use crate::ir::{
    EntityKind, ExpressionTag, OWL_BOTTOM_OBJECT_PROPERTY_IRI, OWL_NOTHING_IRI, OWL_THING_IRI,
    OWL_TOP_OBJECT_PROPERTY_IRI, Ontology,
};
use crate::properties::PropertyClosure;
use crate::reasoning::ContextSnapshot;
use crate::result::{RawRealization, RawTaxonomy};

/// Build the class quotient from independently saturated named roots.
pub fn class_taxonomy(
    ontology: &Ontology,
    contexts: &BTreeMap<u32, ContextSnapshot>,
    inconsistent: bool,
) -> CoreResult<RawTaxonomy> {
    let members = entity_ids(ontology, EntityKind::Class);
    let top = ontology.entity_id(EntityKind::Class, OWL_THING_IRI)?;
    let bottom = ontology.entity_id(EntityKind::Class, OWL_NOTHING_IRI)?;
    if inconsistent {
        return Ok(collapsed_taxonomy(members));
    }
    let expressions = named_expressions(ontology, ExpressionTag::Class);
    let expression_entities = expressions
        .iter()
        .map(|(&entity, &expression)| (expression, entity))
        .collect::<BTreeMap<_, _>>();
    let mut edges = BTreeSet::<(u32, u32)>::new();
    for &member in &members {
        edges.insert((bottom, member));
        edges.insert((member, top));
        let root = expressions[&member];
        let context = contexts.get(&root).ok_or_else(|| {
            CoreError::internal(format!("classification is missing class context {root}"))
        })?;
        for subsumer in context
            .composed_subsumers
            .iter()
            .chain(&context.decomposed_subsumers)
        {
            if let Some(&super_entity) = expression_entities.get(subsumer) {
                edges.insert((member, super_entity));
            }
        }
        if context.inconsistent || member == bottom {
            edges.insert((member, bottom));
        }
    }
    from_relation(&members, &edges, top, bottom)
}

/// Build the singleton object-property quotient from the property fixed point.
pub fn object_property_taxonomy(
    ontology: &Ontology,
    properties: &PropertyClosure,
    inconsistent: bool,
) -> CoreResult<RawTaxonomy> {
    let members = entity_ids(ontology, EntityKind::ObjectProperty);
    let top = ontology.entity_id(EntityKind::ObjectProperty, OWL_TOP_OBJECT_PROPERTY_IRI)?;
    let bottom = ontology.entity_id(EntityKind::ObjectProperty, OWL_BOTTOM_OBJECT_PROPERTY_IRI)?;
    if inconsistent {
        return Ok(collapsed_taxonomy(members));
    }
    let singleton_entities = properties
        .chains
        .iter()
        .enumerate()
        .filter_map(|(chain, record)| {
            record
                .suffix_chain
                .is_none()
                .then_some((chain as u32, record.first_property))
        })
        .collect::<BTreeMap<_, _>>();
    let mut edges = BTreeSet::<(u32, u32)>::new();
    for &member in &members {
        edges.insert((bottom, member));
        edges.insert((member, top));
        let singleton = properties.singleton_chain(member)?;
        for &super_chain in properties.super_chains(singleton) {
            if let Some(&super_property) = singleton_entities.get(&super_chain) {
                edges.insert((member, super_property));
            }
        }
    }
    from_relation(&members, &edges, top, bottom)
}

/// Build same-individual nodes and minimal direct named types.
pub fn realization(
    ontology: &Ontology,
    contexts: &BTreeMap<u32, ContextSnapshot>,
    taxonomy: &RawTaxonomy,
    inconsistent: bool,
) -> CoreResult<RawRealization> {
    let individuals = entity_ids(ontology, EntityKind::NamedIndividual);
    if inconsistent {
        let instance_nodes = if individuals.is_empty() {
            Vec::new()
        } else {
            vec![individuals]
        };
        let direct_types = if instance_nodes.is_empty() {
            Vec::new()
        } else {
            vec![(0, taxonomy.top)]
        };
        return Ok(RawRealization {
            class_taxonomy: taxonomy.clone(),
            instance_nodes,
            direct_types,
        });
    }
    let individual_expressions = named_expressions(ontology, ExpressionTag::Individual);
    let class_expressions = named_expressions(ontology, ExpressionTag::Class);
    let expression_to_class_node = taxonomy
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
    for individual in &individuals {
        let root = individual_expressions[individual];
        if !contexts.contains_key(&root) {
            return Err(CoreError::internal(format!(
                "realization is missing individual context {root}"
            )));
        }
    }

    let mut parent = individuals
        .iter()
        .copied()
        .map(|individual| (individual, individual))
        .collect::<BTreeMap<_, _>>();
    for (position, &first) in individuals.iter().enumerate() {
        let first_expression = individual_expressions[&first];
        let first_subsumers = contexts[&first_expression].subsumers();
        for &second in &individuals[position + 1..] {
            let second_expression = individual_expressions[&second];
            if first_subsumers.contains(&second_expression)
                && contexts[&second_expression]
                    .subsumers()
                    .contains(&first_expression)
            {
                union(&mut parent, first, second);
            }
        }
    }
    let mut grouped = BTreeMap::<u32, Vec<u32>>::new();
    for &individual in &individuals {
        let root = find(&mut parent, individual);
        grouped.entry(root).or_default().push(individual);
    }
    let mut instance_nodes = grouped.into_values().collect::<Vec<_>>();
    instance_nodes.sort();

    let strict_supers = strict_super_closure(taxonomy)?;
    let mut direct_types = Vec::new();
    for (instance_index, node) in instance_nodes.iter().enumerate() {
        let mut candidate_types = BTreeSet::from([taxonomy.top]);
        for individual in node {
            let root = individual_expressions[individual];
            for expression in contexts[&root].subsumers() {
                if let Some(&class_node) = expression_to_class_node.get(&expression) {
                    candidate_types.insert(class_node);
                }
            }
        }
        candidate_types.remove(&taxonomy.bottom);
        let mut direct = minimal_nodes(&candidate_types, &strict_supers);
        if direct.is_empty() {
            direct.push(taxonomy.top);
        }
        direct_types.extend(
            direct
                .into_iter()
                .map(|class_node| (instance_index as u32, class_node)),
        );
    }
    direct_types.sort_unstable();
    Ok(RawRealization {
        class_taxonomy: taxonomy.clone(),
        instance_nodes,
        direct_types,
    })
}

fn find(parent: &mut BTreeMap<u32, u32>, mut value: u32) -> u32 {
    while parent[&value] != value {
        let parent_value = parent[&value];
        let grandparent = parent[&parent_value];
        parent.insert(value, grandparent);
        value = grandparent;
    }
    value
}

fn union(parent: &mut BTreeMap<u32, u32>, first: u32, second: u32) {
    let mut first_root = find(parent, first);
    let mut second_root = find(parent, second);
    if first_root == second_root {
        return;
    }
    if first_root > second_root {
        std::mem::swap(&mut first_root, &mut second_root);
    }
    parent.insert(second_root, first_root);
}

fn collapsed_taxonomy(members: Vec<u32>) -> RawTaxonomy {
    RawTaxonomy {
        nodes: vec![members],
        direct_edges: Vec::new(),
        top: 0,
        bottom: 0,
    }
}

fn from_relation(
    members: &[u32],
    edges: &BTreeSet<(u32, u32)>,
    top: u32,
    bottom: u32,
) -> CoreResult<RawTaxonomy> {
    let (nodes, direct_edges) = quotient_and_reduce(members, edges)?;
    let top_node = taxonomy_node_index_in(&nodes, top)
        .ok_or_else(|| CoreError::internal("taxonomy reduction lost top"))?;
    let bottom_node = taxonomy_node_index_in(&nodes, bottom)
        .ok_or_else(|| CoreError::internal("taxonomy reduction lost bottom"))?;
    Ok(RawTaxonomy {
        nodes,
        direct_edges,
        top: top_node,
        bottom: bottom_node,
    })
}

type QuotientGraph = (Vec<Vec<u32>>, Vec<(u32, u32)>);

fn quotient_and_reduce(members: &[u32], edges: &BTreeSet<(u32, u32)>) -> CoreResult<QuotientGraph> {
    let dense = members
        .iter()
        .enumerate()
        .map(|(index, &member)| (member, index))
        .collect::<BTreeMap<_, _>>();
    let mut adjacency = vec![BTreeSet::<usize>::new(); members.len()];
    let mut reverse = vec![BTreeSet::<usize>::new(); members.len()];
    for &(sub_member, super_member) in edges {
        let (&sub, &super_node) = match (dense.get(&sub_member), dense.get(&super_member)) {
            (Some(sub), Some(super_node)) => (sub, super_node),
            _ => {
                return Err(CoreError::internal(
                    "taxonomy edge references unknown member",
                ));
            }
        };
        if sub != super_node {
            adjacency[sub].insert(super_node);
            reverse[super_node].insert(sub);
        }
    }
    let adjacency = adjacency
        .into_iter()
        .map(|values| values.into_iter().collect())
        .collect::<Vec<Vec<_>>>();
    let reverse = reverse
        .into_iter()
        .map(|values| values.into_iter().collect())
        .collect::<Vec<Vec<_>>>();
    let components = strong_components(&adjacency, &reverse);
    let component_members = components
        .iter()
        .map(|component| {
            let mut values = component
                .iter()
                .map(|&dense_id| members[dense_id])
                .collect::<Vec<_>>();
            values.sort_unstable();
            values
        })
        .collect::<Vec<_>>();
    let mut nodes = component_members.clone();
    nodes.sort();
    let node_by_members = nodes
        .iter()
        .enumerate()
        .map(|(index, node)| (node.clone(), index))
        .collect::<BTreeMap<_, _>>();
    let mut dense_component = vec![0_usize; members.len()];
    for (component, component_node_members) in components.iter().zip(component_members) {
        let node = node_by_members[&component_node_members];
        for &dense_id in component {
            dense_component[dense_id] = node;
        }
    }
    let mut component_edges = BTreeSet::<(u32, u32)>::new();
    for (sub, successors) in adjacency.iter().enumerate() {
        let sub_node = dense_component[sub];
        for &super_node_dense in successors {
            let super_node = dense_component[super_node_dense];
            if sub_node != super_node {
                component_edges.insert((sub_node as u32, super_node as u32));
            }
        }
    }
    let direct_edges = transitive_reduction(nodes.len(), &component_edges)?;
    Ok((nodes, direct_edges))
}

fn finishing_order(adjacency: &[Vec<usize>]) -> Vec<usize> {
    let mut visited = vec![false; adjacency.len()];
    let mut finished = Vec::new();
    for start in 0..adjacency.len() {
        if visited[start] {
            continue;
        }
        visited[start] = true;
        let mut stack = vec![(start, 0_usize)];
        while let Some((node, position)) = stack.last_mut() {
            if *position == adjacency[*node].len() {
                finished.push(*node);
                stack.pop();
                continue;
            }
            let successor = adjacency[*node][*position];
            *position += 1;
            if !visited[successor] {
                visited[successor] = true;
                stack.push((successor, 0));
            }
        }
    }
    finished
}

fn strong_components(adjacency: &[Vec<usize>], reverse: &[Vec<usize>]) -> Vec<Vec<usize>> {
    let finished = finishing_order(adjacency);
    let mut assigned = vec![false; adjacency.len()];
    let mut components = Vec::new();
    for &start in finished.iter().rev() {
        if assigned[start] {
            continue;
        }
        assigned[start] = true;
        let mut component = Vec::new();
        let mut pending = vec![start];
        while let Some(node) = pending.pop() {
            component.push(node);
            for &predecessor in reverse[node].iter().rev() {
                if !assigned[predecessor] {
                    assigned[predecessor] = true;
                    pending.push(predecessor);
                }
            }
        }
        component.sort_unstable();
        components.push(component);
    }
    components
}

/// Compute the unique transitive reduction of a finite DAG.
pub fn transitive_reduction(
    node_count: usize,
    edges: &BTreeSet<(u32, u32)>,
) -> CoreResult<Vec<(u32, u32)>> {
    let mut adjacency = vec![Vec::<usize>::new(); node_count];
    let mut indegrees = vec![0_usize; node_count];
    for &(sub, super_node) in edges {
        if sub as usize >= node_count || super_node as usize >= node_count || sub == super_node {
            return Err(CoreError::internal("invalid taxonomy DAG edge"));
        }
        adjacency[sub as usize].push(super_node as usize);
        indegrees[super_node as usize] += 1;
    }
    for row in &mut adjacency {
        row.sort_unstable();
        row.dedup();
    }
    let mut ready = BTreeSet::new();
    for (node, &indegree) in indegrees.iter().enumerate() {
        if indegree == 0 {
            ready.insert(node);
        }
    }
    let mut topological = Vec::with_capacity(node_count);
    while let Some(node) = ready.pop_first() {
        topological.push(node);
        for &successor in &adjacency[node] {
            indegrees[successor] -= 1;
            if indegrees[successor] == 0 {
                ready.insert(successor);
            }
        }
    }
    if topological.len() != node_count {
        return Err(CoreError::internal("taxonomy relation contains a cycle"));
    }
    let mut rank = vec![0_usize; node_count];
    for (position, &node) in topological.iter().enumerate() {
        rank[node] = position;
    }
    let mut direct = Vec::new();
    for (sub, successors) in adjacency.iter().enumerate() {
        if successors.len() <= 1 {
            direct.extend(
                successors
                    .iter()
                    .map(|&super_node| (sub as u32, super_node as u32)),
            );
            continue;
        }
        let mut ordered = successors.clone();
        ordered.sort_by_key(|&node| rank[node]);
        let mut covered = BTreeSet::new();
        for super_node in ordered {
            if covered.contains(&super_node) {
                continue;
            }
            direct.push((sub as u32, super_node as u32));
            let mut pending = vec![super_node];
            while let Some(reached) = pending.pop() {
                if covered.insert(reached) {
                    pending.extend(&adjacency[reached]);
                }
            }
        }
    }
    direct.sort_unstable();
    Ok(direct)
}

/// Strict superclass closure by canonical taxonomy node index.
pub fn strict_super_closure(taxonomy: &RawTaxonomy) -> CoreResult<Vec<BTreeSet<u32>>> {
    let mut outgoing = vec![Vec::<u32>::new(); taxonomy.nodes.len()];
    for &(sub, super_node) in &taxonomy.direct_edges {
        if sub as usize >= taxonomy.nodes.len() || super_node as usize >= taxonomy.nodes.len() {
            return Err(CoreError::internal("taxonomy edge index is out of range"));
        }
        outgoing[sub as usize].push(super_node);
    }
    let mut result = Vec::with_capacity(taxonomy.nodes.len());
    for start in 0..taxonomy.nodes.len() {
        let mut reached = BTreeSet::new();
        let mut pending = outgoing[start].clone();
        while let Some(node) = pending.pop() {
            if reached.insert(node) {
                pending.extend(&outgoing[node as usize]);
            }
        }
        result.push(reached);
    }
    Ok(result)
}

/// Strict direct/transitive node relatives in either taxonomy direction.
pub fn relative_indices(
    taxonomy: &RawTaxonomy,
    start: u32,
    supers: bool,
    direct: bool,
) -> CoreResult<Vec<u32>> {
    if start as usize >= taxonomy.nodes.len() {
        return Err(CoreError::invalid("taxonomy start node is out of range"));
    }
    let mut adjacency = vec![Vec::<u32>::new(); taxonomy.nodes.len()];
    for &(sub, super_node) in &taxonomy.direct_edges {
        let (source, target) = if supers {
            (sub, super_node)
        } else {
            (super_node, sub)
        };
        adjacency[source as usize].push(target);
    }
    let mut reached = adjacency[start as usize]
        .iter()
        .copied()
        .collect::<BTreeSet<_>>();
    if !direct {
        let mut pending = reached.iter().copied().collect::<Vec<_>>();
        while let Some(node) = pending.pop() {
            for &target in &adjacency[node as usize] {
                if reached.insert(target) {
                    pending.push(target);
                }
            }
        }
    }
    Ok(reached.into_iter().collect())
}

pub fn taxonomy_node_index(taxonomy: &RawTaxonomy, entity: u32) -> Option<u32> {
    taxonomy_node_index_in(&taxonomy.nodes, entity)
}

fn taxonomy_node_index_in(nodes: &[Vec<u32>], entity: u32) -> Option<u32> {
    nodes
        .iter()
        .position(|node| node.binary_search(&entity).is_ok())
        .map(|index| index as u32)
}

pub fn minimal_nodes(candidates: &BTreeSet<u32>, strict_supers: &[BTreeSet<u32>]) -> Vec<u32> {
    candidates
        .iter()
        .copied()
        .filter(|node| {
            !candidates
                .iter()
                .any(|other| other != node && strict_supers[*other as usize].contains(node))
        })
        .collect()
}

pub fn entity_ids(ontology: &Ontology, kind: EntityKind) -> Vec<u32> {
    ontology
        .entities
        .iter()
        .enumerate()
        .filter_map(|(index, entity)| (entity.kind == kind).then_some(index as u32))
        .collect()
}

pub fn named_expressions(ontology: &Ontology, tag: ExpressionTag) -> BTreeMap<u32, u32> {
    ontology
        .expressions
        .iter()
        .enumerate()
        .filter_map(|(index, expression)| {
            (expression.tag == tag).then_some((expression.arguments[0], index as u32))
        })
        .collect()
}

/// Return direct class-node indices for one realization node.
pub fn direct_type_indices(realization: &RawRealization, instance: u32) -> Vec<u32> {
    realization
        .direct_types
        .iter()
        .filter_map(|&(candidate, class_node)| (candidate == instance).then_some(class_node))
        .collect()
}
