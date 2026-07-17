//! Criterion stages for the Python-free reasoner core.

use std::hint::black_box;

use criterion::{BenchmarkId, Criterion, criterion_group, criterion_main};
use pyelk_core::ir::{
    Entity, EntityKind, Expression, ExpressionTag, FEATURE_VECTOR_LENGTH,
    OWL_BOTTOM_OBJECT_PROPERTY_IRI, OWL_NOTHING_IRI, OWL_THING_IRI, OWL_TOP_OBJECT_PROPERTY_IRI,
    Occurrence, Ontology,
};
use pyelk_core::properties::PropertyClosure;
use pyelk_core::reasoning::saturate_roots;
use pyelk_core::taxonomy::class_taxonomy;

fn hierarchy(size: usize) -> Ontology {
    let mut entities = vec![
        Entity {
            kind: EntityKind::Class,
            iri: OWL_NOTHING_IRI.to_owned(),
        },
        Entity {
            kind: EntityKind::Class,
            iri: OWL_THING_IRI.to_owned(),
        },
    ];
    entities.extend((0..size).map(|index| Entity {
        kind: EntityKind::Class,
        iri: format!("urn:pyelk:criterion:C{index:08}"),
    }));
    entities.extend([
        Entity {
            kind: EntityKind::ObjectProperty,
            iri: OWL_BOTTOM_OBJECT_PROPERTY_IRI.to_owned(),
        },
        Entity {
            kind: EntityKind::ObjectProperty,
            iri: OWL_TOP_OBJECT_PROPERTY_IRI.to_owned(),
        },
    ]);
    let class_count = size + 2;
    let expressions = (0..class_count)
        .map(|entity| Expression {
            tag: ExpressionTag::Class,
            payload: Vec::new(),
            arguments: vec![entity as u32],
        })
        .collect::<Vec<_>>();
    let subclass_axioms = (2..size + 1)
        .map(|index| (index as u32, index as u32 + 1))
        .collect::<Vec<_>>();
    let mut expression_occurrences = vec![Occurrence::default(); class_count];
    for &(sub, super_expression) in &subclass_axioms {
        expression_occurrences[sub as usize].negative += 1;
        expression_occurrences[super_expression as usize].positive += 1;
    }
    Ontology {
        entities,
        expressions,
        expression_occurrences,
        property_occurrences: vec![Occurrence::default(); 2],
        property_chains: vec![vec![class_count as u32], vec![class_count as u32 + 1]],
        subclass_axioms,
        equivalent_class_axioms: Vec::new(),
        disjoint_groups: Vec::new(),
        subproperty_axioms: Vec::new(),
        property_ranges: Vec::new(),
        feature_counts: vec![0; FEATURE_VECTOR_LENGTH],
        source_fingerprint: [0; 32],
    }
}

fn stages(criterion: &mut Criterion) {
    let mut group = criterion.benchmark_group("native-core-stages");
    for size in [64_usize, 256] {
        let ontology = hierarchy(size);
        let roots = (0..size + 2).map(|value| value as u32).collect::<Vec<_>>();
        group.bench_with_input(BenchmarkId::new("properties", size), &size, |bench, _| {
            bench.iter(|| PropertyClosure::build(black_box(&ontology)).unwrap());
        });
        let properties = PropertyClosure::build(&ontology).unwrap();
        group.bench_with_input(BenchmarkId::new("saturation", size), &size, |bench, _| {
            bench.iter(|| {
                saturate_roots(
                    black_box(&ontology),
                    black_box(&properties),
                    black_box(&roots),
                )
                .unwrap()
            });
        });
        let (contexts, _) = saturate_roots(&ontology, &properties, &roots).unwrap();
        group.bench_with_input(BenchmarkId::new("taxonomy", size), &size, |bench, _| {
            bench.iter(|| {
                class_taxonomy(black_box(&ontology), black_box(&contexts), black_box(false))
                    .unwrap()
            });
        });
        group.bench_with_input(BenchmarkId::new("end-to-end", size), &size, |bench, _| {
            bench.iter(|| {
                let closure = PropertyClosure::build(black_box(&ontology)).unwrap();
                let (contexts, _) =
                    saturate_roots(black_box(&ontology), &closure, black_box(&roots)).unwrap();
                class_taxonomy(black_box(&ontology), &contexts, false).unwrap()
            });
        });
    }
    group.finish();
}

criterion_group!(benches, stages);
criterion_main!(benches);
