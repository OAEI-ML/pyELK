package org.pyelk.oracle;

import java.lang.reflect.Field;
import java.util.LinkedHashMap;
import java.util.Map;

import org.semanticweb.elk.owl.interfaces.ElkClassExpression;
import org.semanticweb.elk.reasoner.Reasoner;
import org.semanticweb.elk.reasoner.completeness.Feature;
import org.semanticweb.elk.reasoner.completeness.OccurrenceCounter;
import org.semanticweb.elk.reasoner.completeness.OccurrenceManager;
import org.semanticweb.elk.reasoner.query.VerifiableQueryResult;

/** Fail-closed access to ELK's pinned occurrence registries. */
final class FeatureBridge {

    private static final String[] PINNED_FEATURES = {
        "ANONYMOUS_INDIVIDUAL", "ASYMMETRIC_OBJECT_PROPERTY",
        "BOTTOM_OBJECT_PROPERTY_POSITIVE", "DATA_ALL_VALUES_FROM",
        "DATA_EXACT_CARDINALITY", "DATA_HAS_VALUE", "DATA_MAX_CARDINALITY",
        "DATA_MIN_CARDINALITY", "DATA_PROPERTY", "DATA_PROPERTY_ASSERTION",
        "DATA_PROPERTY_DOMAIN", "DATA_PROPERTY_RANGE", "DATA_SOME_VALUES_FROM",
        "DATATYPE", "DATATYPE_DEFINITION", "DIFFERENT_INDIVIDUALS",
        "DISJOINT_CLASSES", "DISJOINT_DATA_PROPERTIES", "DISJOINT_OBJECT_PROPERTIES",
        "DISJOINT_UNION", "EQUIVALENT_DATA_PROPERTIES", "FUNCTIONAL_DATA_PROPERTY",
        "FUNCTIONAL_OBJECT_PROPERTY", "HAS_KEY", "INVERSE_FUNCTIONAL_OBJECT_PROPERTY",
        "INVERSE_OBJECT_PROPERTIES", "IRREFLEXIVE_OBJECT_PROPERTY",
        "NEGATIVE_DATA_PROPERTY_ASSERTION", "NEGATIVE_OBJECT_PROPERTY_ASSERTION",
        "OBJECT_ALL_VALUES_FROM", "OBJECT_COMPLEMENT_OF_NEGATIVE",
        "OBJECT_COMPLEMENT_OF_POSITIVE", "OBJECT_EXACT_CARDINALITY",
        "OBJECT_HAS_SELF_NEGATIVE", "OBJECT_HAS_VALUE_POSITIVE", "OBJECT_INVERSE_OF",
        "OBJECT_MAX_CARDINALITY", "OBJECT_MIN_CARDINALITY", "OBJECT_ONE_OF",
        "OBJECT_PROPERTY_ASSERTION", "OBJECT_PROPERTY_CHAIN", "OBJECT_PROPERTY_RANGE",
        "OBJECT_UNION_OF_POSITIVE", "OWL_NOTHING_POSITIVE", "REFLEXIVE_OBJECT_PROPERTY",
        "SUB_DATA_PROPERTY_OF", "SWRL_RULE", "SYMMETRIC_OBJECT_PROPERTY",
        "TOP_OBJECT_PROPERTY_NEGATIVE", "QUERY_ANNOTATION_ASSERTION_AXIOM",
        "QUERY_ANNOTATION_PROPERTY_DOMAIN_AXIOM", "QUERY_ANNOTATION_PROPERTY_RANGE_AXIOM",
        "QUERY_SUB_ANNOTATION_PROPERTY_OF_AXIOM", "QUERY_DATA_PROPERTY_ASSERTION_AXIOM",
        "QUERY_NEGATIVE_DATA_PROPERTY_ASSERTION_AXIOM",
        "QUERY_NEGATIVE_OBJECT_PROPERTY_ASSERTION_AXIOM", "QUERY_DISJOINT_UNION_AXIOM",
        "QUERY_DATA_PROPERTY_DOMAIN_AXIOM", "QUERY_DATA_PROPERTY_RANGE_AXIOM",
        "QUERY_DISJOINT_DATA_PROPERTIES_AXIOM", "QUERY_EQUIVALENT_DATA_PROPERTIES_AXIOM",
        "QUERY_FUNCTIONAL_DATA_PROPERTY_AXIOM", "QUERY_SUB_DATA_PROPERTY_OF_AXIOM",
        "QUERY_DATATYPE_DEFINITION_AXIOM", "QUERY_DECLARATION_AXIOM",
        "QUERY_HAS_KEY_AXIOM", "QUERY_ASYMMETRIC_OBJECT_PROPERTY_AXIOM",
        "QUERY_DISJOINT_OBJECT_PROPERTIES_AXIOM",
        "QUERY_EQUIVALENT_OBJECT_PROPERTIES_AXIOM",
        "QUERY_FUNCTIONAL_OBJECT_PROPERTY_AXIOM",
        "QUERY_INVERSE_FUNCTIONAL_OBJECT_PROPERTY_AXIOM",
        "QUERY_INVERSE_OBJECT_PROPERTIES_AXIOM", "QUERY_IRREFLEXIVE_OBJECT_PROPERTY_AXIOM",
        "QUERY_OBJECT_PROPERTY_RANGE_AXIOM", "QUERY_REFLEXIVE_OBJECT_PROPERTY_AXIOM",
        "QUERY_SUB_OBJECT_PROPERTY_OF_AXIOM", "QUERY_SYMMETRIC_OBJECT_PROPERTY_AXIOM",
        "QUERY_TRANSITIVE_OBJECT_PROPERTY_AXIOM", "QUERY_SWRL_RULE"
    };

    private static final Field ONTOLOGY_OCCURRENCES;
    private static final Field QUERY_OCCURRENCES;
    private static final Field CLASS_QUERY_STATE;
    private static final Field CLASS_QUERIED;
    private static final Field CLASS_QUERY_OCCURRENCES;

    static {
        Feature[] actual = Feature.values();
        if (actual.length != PINNED_FEATURES.length) {
            throw new ExceptionInInitializerError("ELK Feature width differs from pinned manifest");
        }
        for (int index = 0; index < actual.length; index++) {
            if (!actual[index].name().equals(PINNED_FEATURES[index])) {
                throw new ExceptionInInitializerError(
                    "ELK Feature order differs at ordinal " + index);
            }
        }
        try {
            Class<?> state = Class.forName("org.semanticweb.elk.reasoner.stages.AbstractReasonerState");
            ONTOLOGY_OCCURRENCES = state.getDeclaredField("occurrencesInOntology_");
            ONTOLOGY_OCCURRENCES.setAccessible(true);
            CLASS_QUERY_STATE = state.getDeclaredField("classExpressionQueryState");
            CLASS_QUERY_STATE.setAccessible(true);
            Class<?> classQueryState = Class.forName(
                "org.semanticweb.elk.reasoner.stages.ClassExpressionQueryState");
            CLASS_QUERIED = classQueryState.getDeclaredField("queried_");
            CLASS_QUERIED.setAccessible(true);
            Class<?> classQuery = Class.forName(
                "org.semanticweb.elk.reasoner.stages.ClassExpressionQueryState$QueryState");
            CLASS_QUERY_OCCURRENCES = classQuery.getDeclaredField("occurrences");
            CLASS_QUERY_OCCURRENCES.setAccessible(true);
            Class<?> queryState = Class.forName(
                "org.semanticweb.elk.reasoner.stages.EntailmentQueryState$QueryState");
            QUERY_OCCURRENCES = queryState.getDeclaredField("occurrences");
            QUERY_OCCURRENCES.setAccessible(true);
        } catch (ReflectiveOperationException error) {
            throw new ExceptionInInitializerError(
                "Pinned ELK occurrence registry fields are unavailable");
        }
    }

    private FeatureBridge() {}

    static Map<String, Integer> ontologyCounts(Reasoner reasoner) {
        try {
            Object value = ONTOLOGY_OCCURRENCES.get(reasoner);
            if (!(value instanceof OccurrenceManager)) {
                throw new IllegalStateException("ontology occurrence registry has unexpected type");
            }
            return nonZeroCounts((OccurrenceCounter) value);
        } catch (IllegalAccessException error) {
            throw new IllegalStateException("cannot access ontology occurrence registry", error);
        }
    }

    static Map<String, Integer> queryCounts(VerifiableQueryResult result) {
        try {
            Object value = QUERY_OCCURRENCES.get(result);
            if (!(value instanceof OccurrenceCounter)) {
                throw new IllegalStateException("query occurrence registry has unexpected type");
            }
            return nonZeroCounts((OccurrenceCounter) value);
        } catch (IllegalAccessException | IllegalArgumentException error) {
            throw new IllegalStateException("cannot access query occurrence registry", error);
        }
    }

    static Map<String, Integer> classQueryCounts(
            Reasoner reasoner, ElkClassExpression expression) {
        try {
            Object state = CLASS_QUERY_STATE.get(reasoner);
            Object queriedValue = CLASS_QUERIED.get(state);
            if (!(queriedValue instanceof Map<?, ?>)) {
                throw new IllegalStateException("class-query registry has unexpected type");
            }
            Object query = ((Map<?, ?>) queriedValue).get(expression);
            if (query == null) {
                throw new IllegalStateException("class query is absent from pinned registry");
            }
            Object occurrences = CLASS_QUERY_OCCURRENCES.get(query);
            if (!(occurrences instanceof OccurrenceCounter)) {
                throw new IllegalStateException("class-query occurrences have unexpected type");
            }
            return nonZeroCounts((OccurrenceCounter) occurrences);
        } catch (IllegalAccessException error) {
            throw new IllegalStateException("cannot access class-query occurrence registry", error);
        }
    }

    static Map<String, Integer> addCounts(
            Map<String, Integer> left, Map<String, Integer> right) {
        Map<String, Integer> result = new LinkedHashMap<>();
        for (String name : PINNED_FEATURES) {
            int count = left.getOrDefault(name, 0) + right.getOrDefault(name, 0);
            if (count != 0) {
                result.put(name, count);
            }
        }
        return result;
    }

    static String[] featureNames() {
        return PINNED_FEATURES.clone();
    }

    private static Map<String, Integer> nonZeroCounts(OccurrenceCounter counter) {
        Map<String, Integer> result = new LinkedHashMap<>();
        for (Feature feature : Feature.values()) {
            int count = counter.getOccurrenceCount(feature);
            if (count < 0) {
                throw new IllegalStateException("negative ELK feature occurrence count");
            }
            if (count != 0) {
                result.put(feature.name(), count);
            }
        }
        return result;
    }
}
