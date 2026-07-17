package org.pyelk.oracle;

import java.io.IOException;
import java.io.InputStream;
import java.lang.reflect.Field;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.concurrent.TimeUnit;

import com.fasterxml.jackson.databind.JsonNode;

import org.semanticweb.elk.loading.TestChangesLoader;
import org.semanticweb.elk.owl.interfaces.ElkAxiom;
import org.semanticweb.elk.owl.interfaces.ElkClass;
import org.semanticweb.elk.owl.interfaces.ElkClassExpression;
import org.semanticweb.elk.owl.interfaces.ElkEntity;
import org.semanticweb.elk.owl.interfaces.ElkNamedIndividual;
import org.semanticweb.elk.owl.interfaces.ElkObject;
import org.semanticweb.elk.owl.interfaces.ElkObjectProperty;
import org.semanticweb.elk.owl.managers.ElkObjectEntityRecyclingFactory;
import org.semanticweb.elk.owl.parsing.javacc.Owl2FunctionalStyleParserFactory;
import org.semanticweb.elk.owl.printers.OwlFunctionalStylePrinter;
import org.semanticweb.elk.reasoner.ElkClassTaxonomyTestOutput;
import org.semanticweb.elk.reasoner.ElkObjectPropertyTaxonomyTestOutput;
import org.semanticweb.elk.reasoner.InstanceTaxonomyTestOutput;
import org.semanticweb.elk.reasoner.Reasoner;
import org.semanticweb.elk.reasoner.TestReasonerUtils;
import org.semanticweb.elk.reasoner.completeness.IncompleteResult;
import org.semanticweb.elk.reasoner.completeness.Incompleteness;
import org.semanticweb.elk.reasoner.config.ReasonerConfiguration;
import org.semanticweb.elk.reasoner.query.PyElkQueryGoldenBridge;
import org.semanticweb.elk.reasoner.query.VerifiableQueryResult;
import org.semanticweb.elk.reasoner.saturation.SaturationState;
import org.semanticweb.elk.reasoner.taxonomy.MockObjectPropertyTaxonomyLoader;
import org.semanticweb.elk.reasoner.taxonomy.MockTaxonomyLoader;
import org.semanticweb.elk.reasoner.taxonomy.model.InstanceNode;
import org.semanticweb.elk.reasoner.taxonomy.model.InstanceTaxonomy;
import org.semanticweb.elk.reasoner.taxonomy.model.Node;
import org.semanticweb.elk.reasoner.taxonomy.model.Taxonomy;
import org.semanticweb.elk.reasoner.taxonomy.model.TaxonomyNode;

/** Implements the pinned, deterministic reference operations. */
final class Oracle {

    static final String ELK_COMMIT = "b8ac5ce83db0704a7359d96aa382891e2f547863";
    static final String ELK_TREE = "9becd9e41eac6434a1e247c2a9b19644cdd9d27a";

    private Oracle() {}

    static Result execute(JsonNode request) throws Exception {
        validateRequest(request);
        String operation = requiredText(request, "operation");
        if (operation.equals("identity")) {
            Map<String, Object> identity = new TreeMap<>();
            identity.put("elk_commit", ELK_COMMIT);
            identity.put("elk_tree", ELK_TREE);
            identity.put("elk_version", "0.6.0");
            identity.put("feature_names", List.of(FeatureBridge.featureNames()));
            identity.put("incremental", false);
            identity.put("java_runtime", System.getProperty("java.runtime.version"));
            identity.put("owlapi_version", "5.1.20");
            identity.put("workers", 1);
            return new Result(identity, true, Map.of(), List.of());
        }
        Path ontology = requiredPath(request, "ontology_path");
        JsonNode arguments = request.path("arguments");
        if (operation.equals("structural_load")) {
            List<ElkAxiom> axioms;
            try (InputStream stream = Files.newInputStream(ontology)) {
                axioms = TestReasonerUtils.loadAxioms(stream);
            }
            Map<String, Object> value = new TreeMap<>();
            value.put("axiom_count", axioms.size());
            return new Result(value, true, Map.of(), List.of());
        }
        if (operation.equals("query_feature_counts")) {
            return queryFeatureCounts(ontology);
        }

        try (LoadedReasoner loaded = LoadedReasoner.fromPath(ontology)) {
            Reasoner reasoner = loaded.reasoner;
            switch (operation) {
                case "consistency":
                    return consistency(reasoner);
                case "feature_counts":
                    Incompleteness.getValue(reasoner.isInconsistent());
                    return new Result(
                        Map.of("feature_count", FeatureBridge.featureNames().length),
                        true, FeatureBridge.ontologyCounts(reasoner), List.of());
                case "class_taxonomy":
                    return classTaxonomy(reasoner, optionalPath(arguments, "golden_path"));
                case "object_property_taxonomy":
                    return objectPropertyTaxonomy(
                        reasoner, optionalPath(arguments, "golden_path"));
                case "realization":
                    return realization(reasoner, optionalPath(arguments, "golden_path"));
                case "class_queries":
                    return classQueries(
                        reasoner, ontology, requiredPath(arguments, "golden_path"));
                case "entailment":
                    return entailment(reasoner, requiredPaths(arguments, "query_paths"));
                case "saturation_counts":
                    return saturationCounts(reasoner);
                default:
                    throw new OracleException("unsupported_operation");
            }
        }
    }

    private static Result consistency(Reasoner reasoner) throws Exception {
        IncompleteResult<Boolean> result = reasoner.isInconsistent();
        boolean inconsistent = Incompleteness.getValue(result);
        Map<String, Object> value = new TreeMap<>();
        value.put("consistent", !inconsistent);
        value.put("inconsistent", inconsistent);
        return result(value, result, reasoner);
    }

    private static Result classTaxonomy(Reasoner reasoner, Path golden) throws Exception {
        IncompleteResult<? extends Taxonomy<ElkClass>> incomplete =
            reasoner.getTaxonomyQuietly();
        Taxonomy<ElkClass> taxonomy = Incompleteness.getValue(incomplete);
        Map<String, Object> value = canonicalTaxonomy(taxonomy);
        if (golden != null) {
            ElkObject.Factory factory = new ElkObjectEntityRecyclingFactory();
            Taxonomy<ElkClass> expected;
            try (InputStream stream = Files.newInputStream(golden)) {
                expected = MockTaxonomyLoader.load(factory,
                    new Owl2FunctionalStyleParserFactory(factory).getParser(stream));
            }
            ElkClassTaxonomyTestOutput actualOutput =
                new ElkClassTaxonomyTestOutput(incomplete);
            ElkClassTaxonomyTestOutput expectedOutput =
                new ElkClassTaxonomyTestOutput(expected);
            value.put("upstream_golden_match",
                actualOutput.containsAllElementsOf(expectedOutput)
                && expectedOutput.containsAllElementsOf(actualOutput));
            value.put("upstream_goldens_checked", 1);
        }
        return result(value, incomplete, reasoner);
    }

    private static Result objectPropertyTaxonomy(Reasoner reasoner, Path golden)
            throws Exception {
        IncompleteResult<? extends Taxonomy<ElkObjectProperty>> incomplete =
            reasoner.getObjectPropertyTaxonomyQuietly();
        Taxonomy<ElkObjectProperty> taxonomy = Incompleteness.getValue(incomplete);
        Map<String, Object> value = canonicalTaxonomy(taxonomy);
        if (golden != null) {
            ElkObject.Factory factory = new ElkObjectEntityRecyclingFactory();
            Taxonomy<ElkObjectProperty> expected;
            try (InputStream stream = Files.newInputStream(golden)) {
                expected = MockObjectPropertyTaxonomyLoader.load(factory,
                    new Owl2FunctionalStyleParserFactory(factory).getParser(stream));
            }
            ElkObjectPropertyTaxonomyTestOutput actualOutput =
                new ElkObjectPropertyTaxonomyTestOutput(incomplete);
            ElkObjectPropertyTaxonomyTestOutput expectedOutput =
                new ElkObjectPropertyTaxonomyTestOutput(expected);
            value.put("upstream_golden_match",
                actualOutput.containsAllElementsOf(expectedOutput)
                && expectedOutput.containsAllElementsOf(actualOutput));
            value.put("upstream_goldens_checked", 1);
        }
        return result(value, incomplete, reasoner);
    }

    private static Result realization(Reasoner reasoner, Path golden) throws Exception {
        IncompleteResult<? extends InstanceTaxonomy<ElkClass, ElkNamedIndividual>> incomplete =
            reasoner.getInstanceTaxonomyQuietly();
        InstanceTaxonomy<ElkClass, ElkNamedIndividual> taxonomy =
            Incompleteness.getValue(incomplete);
        Map<String, Object> value = canonicalInstanceTaxonomy(taxonomy);
        if (golden != null) {
            ElkObject.Factory factory = new ElkObjectEntityRecyclingFactory();
            InstanceTaxonomy<ElkClass, ElkNamedIndividual> expected;
            try (InputStream stream = Files.newInputStream(golden)) {
                expected = MockTaxonomyLoader.load(factory,
                    new Owl2FunctionalStyleParserFactory(factory).getParser(stream));
            }
            InstanceTaxonomyTestOutput actualOutput =
                new InstanceTaxonomyTestOutput(incomplete);
            InstanceTaxonomyTestOutput expectedOutput =
                new InstanceTaxonomyTestOutput(expected);
            value.put("upstream_golden_match",
                actualOutput.containsAllElementsOf(expectedOutput)
                && expectedOutput.containsAllElementsOf(actualOutput));
            value.put("upstream_goldens_checked", 1);
        }
        return result(value, incomplete, reasoner);
    }

    private static Result classQueries(Reasoner reasoner, Path ontology, Path golden)
            throws Exception {
        List<ElkClassExpression> queries = PyElkQueryGoldenBridge.loadQueries(
            golden, ontology.toUri().toURL());
        queries.sort(Comparator.comparing(
            query -> OwlFunctionalStylePrinter.toString(query, true),
            Oracle::compareUtf8));
        List<Map<String, Object>> values = new ArrayList<>();
        boolean complete = true;
        for (ElkClassExpression query : queries) {
            Map<String, Object> item = new TreeMap<>();
            item.put("expression", OwlFunctionalStylePrinter.toString(query, true));

            IncompleteResult<? extends Boolean> satisfiable =
                reasoner.isSatisfiableQuietly(query);
            item.put("satisfiable", operationValue(
                Incompleteness.getValue(satisfiable), satisfiable));
            complete &= isComplete(satisfiable);

            IncompleteResult<? extends Node<ElkClass>> equivalent =
                reasoner.getEquivalentClassesQuietly(query);
            item.put("equivalent_classes", operationValue(
                canonicalNode(Incompleteness.getValue(equivalent)), equivalent));
            complete &= isComplete(equivalent);

            IncompleteResult<? extends Collection<? extends Node<ElkClass>>> supers =
                reasoner.getSuperClassesQuietly(query, true);
            item.put("direct_superclasses", operationValue(
                canonicalNodes(Incompleteness.getValue(supers)), supers));
            complete &= isComplete(supers);

            IncompleteResult<? extends Collection<? extends Node<ElkClass>>> subs =
                reasoner.getSubClassesQuietly(query, true);
            item.put("direct_subclasses", operationValue(
                canonicalNodes(Incompleteness.getValue(subs)), subs));
            complete &= isComplete(subs);

            IncompleteResult<? extends Collection<? extends Node<ElkNamedIndividual>>> instances =
                reasoner.getInstancesQuietly(query, true);
            item.put("direct_instances", operationValue(
                canonicalNodes(Incompleteness.getValue(instances)), instances));
            complete &= isComplete(instances);
            item.put("query_features", FeatureBridge.classQueryCounts(reasoner, query));
            values.add(item);
        }
        int checked = PyElkQueryGoldenBridge.verify(
            golden, ontology.toUri().toURL(), reasoner);
        Map<String, Object> value = new TreeMap<>();
        value.put("queries", values);
        value.put("upstream_golden_match", true);
        value.put("upstream_goldens_checked", 1);
        value.put("upstream_operations_checked", checked);
        return new Result(value, complete, FeatureBridge.ontologyCounts(reasoner), List.of());
    }

    private static Result entailment(Reasoner reasoner, List<Path> queryPaths)
            throws Exception {
        List<QueryExpectation> expectations = new ArrayList<>();
        for (Path path : queryPaths) {
            boolean expected;
            if (path.getFileName().toString().endsWith(".entailed")) {
                expected = true;
            } else if (path.getFileName().toString().endsWith(".notentailed")) {
                expected = false;
            } else {
                throw new OracleException("invalid_query_golden");
            }
            try (InputStream stream = Files.newInputStream(path)) {
                for (ElkAxiom axiom : PinnedElkDispatchBridge.repair(
                        TestReasonerUtils.loadAxioms(stream))) {
                    expectations.add(new QueryExpectation(axiom, expected, path));
                }
            }
        }
        expectations.sort(Comparator.comparing(
            item -> OwlFunctionalStylePrinter.toString(item.axiom, true),
            Oracle::compareUtf8));
        List<ElkAxiom> axioms = expectations.stream().map(item -> item.axiom).toList();
        Map<ElkAxiom, VerifiableQueryResult> results = reasoner.checkEntailment(axioms);
        List<Map<String, Object>> values = new ArrayList<>();
        boolean complete = true;
        boolean match = true;
        try {
            for (QueryExpectation expectation : expectations) {
                VerifiableQueryResult queryResult = results.get(expectation.axiom);
                if (queryResult == null) {
                    throw new IllegalStateException("ELK omitted a registered entailment query");
                }
                boolean entailed = queryResult.entailmentProved();
                boolean itemComplete = !queryResult.getIncompletenessMonitor()
                    .isIncompletenessDetected();
                Map<String, Object> item = new TreeMap<>();
                item.put("axiom", OwlFunctionalStylePrinter.toString(expectation.axiom, true));
                item.put("complete", itemComplete);
                item.put("entailed", entailed);
                item.put("query_features", FeatureBridge.queryCounts(queryResult));
                item.put("upstream_expected", expectation.expected);
                item.put("upstream_source", expectation.path.getFileName().toString());
                values.add(item);
                complete &= itemComplete;
                // ELK's own IncompleteEntailmentTestOutput leaves an incomplete
                // unproved query undecided, so it is compatible with either
                // upstream polarity. A proved query remains decisive.
                match &= entailed == expectation.expected || (!itemComplete && !entailed);
            }
        } finally {
            for (VerifiableQueryResult queryResult : results.values()) {
                queryResult.unlock();
            }
        }
        Map<String, Object> value = new TreeMap<>();
        value.put("queries", values);
        value.put("upstream_golden_match", match);
        value.put("upstream_goldens_checked", queryPaths.size());
        return new Result(value, complete, FeatureBridge.ontologyCounts(reasoner), List.of());
    }

    private static Result queryFeatureCounts(Path queryPath) throws Exception {
        List<ElkAxiom> axioms;
        try (InputStream stream = Files.newInputStream(queryPath)) {
            axioms = PinnedElkDispatchBridge.repair(
                TestReasonerUtils.loadAxioms(stream));
        }
        try (LoadedReasoner loaded = LoadedReasoner.empty()) {
            Map<ElkAxiom, VerifiableQueryResult> results =
                loaded.reasoner.checkEntailment(axioms);
            Map<String, Integer> counts = Map.of();
            try {
                for (VerifiableQueryResult result : results.values()) {
                    result.entailmentProved();
                    counts = FeatureBridge.addCounts(counts, FeatureBridge.queryCounts(result));
                }
            } finally {
                for (VerifiableQueryResult result : results.values()) {
                    result.unlock();
                }
            }
            return new Result(
                Map.of("query_axiom_count", axioms.size()),
                counts.isEmpty(), counts, List.of());
        }
    }

    private static Result saturationCounts(Reasoner reasoner) throws Exception {
        Incompleteness.getValue(reasoner.getTaxonomyQuietly());
        Field field = Class.forName("org.semanticweb.elk.reasoner.stages.AbstractReasonerState")
            .getDeclaredField("saturationState");
        field.setAccessible(true);
        Object state = field.get(reasoner);
        if (!(state instanceof SaturationState<?>)) {
            throw new IllegalStateException("saturation state has unexpected type");
        }
        SaturationState<?> saturation = (SaturationState<?>) state;
        Map<String, Object> value = new TreeMap<>();
        value.put("classes", reasoner.getAllClasses().size());
        value.put("contexts", saturation.getContexts().size());
        value.put("individuals", reasoner.getAllNamedIndividuals().size());
        value.put("not_saturated_contexts", saturation.getNotSaturatedContexts().size());
        value.put("object_properties", reasoner.getAllObjectProperties().size());
        return new Result(value, true, FeatureBridge.ontologyCounts(reasoner), List.of());
    }

    private static <T> Map<String, Object> operationValue(
            T value, IncompleteResult<?> result) {
        Map<String, Object> payload = new TreeMap<>();
        payload.put("complete", isComplete(result));
        payload.put("value", value);
        return payload;
    }

    private static Result result(
            Object value, IncompleteResult<?> incomplete, Reasoner reasoner) {
        return new Result(value, isComplete(incomplete),
            FeatureBridge.ontologyCounts(reasoner), List.of());
    }

    private static boolean isComplete(IncompleteResult<?> result) {
        return !result.getIncompletenessMonitor().isIncompletenessDetected();
    }

    private static <T extends ElkEntity> Map<String, Object> canonicalTaxonomy(
            Taxonomy<T> taxonomy) {
        List<TaxonomyNode<T>> nodes = new ArrayList<>(taxonomy.getNodes());
        nodes.sort((left, right) -> compareStringLists(
            canonicalNode(left), canonicalNode(right)));
        Map<String, Integer> indexes = new LinkedHashMap<>();
        List<List<String>> members = new ArrayList<>();
        for (int index = 0; index < nodes.size(); index++) {
            List<String> node = canonicalNode(nodes.get(index));
            members.add(node);
            indexes.put(String.join("\u0000", node), index);
        }
        List<List<Integer>> edges = new ArrayList<>();
        for (TaxonomyNode<T> sub : nodes) {
            int subIndex = indexes.get(nodeKey(sub));
            for (TaxonomyNode<T> sup : sub.getDirectSuperNodes()) {
                edges.add(List.of(subIndex, indexes.get(nodeKey(sup))));
            }
        }
        edges.sort(Comparator
            .comparingInt((List<Integer> edge) -> edge.get(0))
            .thenComparingInt(edge -> edge.get(1)));
        Map<String, Object> value = new TreeMap<>();
        value.put("bottom", indexes.get(nodeKey(taxonomy.getBottomNode())));
        value.put("direct_edges", edges);
        value.put("nodes", members);
        value.put("top", indexes.get(nodeKey(taxonomy.getTopNode())));
        return value;
    }

    private static Map<String, Object> canonicalInstanceTaxonomy(
            InstanceTaxonomy<ElkClass, ElkNamedIndividual> taxonomy) {
        Map<String, Object> value = canonicalTaxonomy(taxonomy);
        @SuppressWarnings("unchecked")
        List<List<String>> typeMembers = (List<List<String>>) value.get("nodes");
        Map<String, Integer> typeIndexes = new LinkedHashMap<>();
        for (int index = 0; index < typeMembers.size(); index++) {
            typeIndexes.put(String.join("\u0000", typeMembers.get(index)), index);
        }
        List<InstanceNode<ElkClass, ElkNamedIndividual>> instances =
            new ArrayList<>(taxonomy.getInstanceNodes());
        instances.sort((left, right) -> compareStringLists(
            canonicalNode(left), canonicalNode(right)));
        List<List<String>> instanceMembers = new ArrayList<>();
        Map<String, Integer> instanceIndexes = new LinkedHashMap<>();
        for (int index = 0; index < instances.size(); index++) {
            List<String> node = canonicalNode(instances.get(index));
            instanceMembers.add(node);
            instanceIndexes.put(String.join("\u0000", node), index);
        }
        List<List<Integer>> directTypes = new ArrayList<>();
        for (InstanceNode<ElkClass, ElkNamedIndividual> instance : instances) {
            for (TaxonomyNode<ElkClass> type : instance.getDirectTypeNodes()) {
                directTypes.add(List.of(
                    instanceIndexes.get(nodeKey(instance)),
                    typeIndexes.get(nodeKey(type))));
            }
        }
        directTypes.sort(Comparator
            .comparingInt((List<Integer> edge) -> edge.get(0))
            .thenComparingInt(edge -> edge.get(1)));
        value.put("direct_types", directTypes);
        value.put("instance_nodes", instanceMembers);
        return value;
    }

    private static <T extends ElkEntity> List<List<String>> canonicalNodes(
            Collection<? extends Node<T>> nodes) {
        List<List<String>> result = new ArrayList<>();
        for (Node<T> node : nodes) {
            result.add(canonicalNode(node));
        }
        result.sort(Oracle::compareStringLists);
        return result;
    }

    private static <T extends ElkEntity> List<String> canonicalNode(Node<T> node) {
        List<String> members = new ArrayList<>();
        for (T member : node) {
            members.add(member.getIri().getFullIriAsString());
        }
        members.sort(Oracle::compareUtf8);
        return members;
    }

    private static <T extends ElkEntity> String nodeKey(Node<T> node) {
        return String.join("\u0000", canonicalNode(node));
    }

    private static int compareStringLists(List<String> left, List<String> right) {
        int length = Math.min(left.size(), right.size());
        for (int index = 0; index < length; index++) {
            int compared = compareUtf8(left.get(index), right.get(index));
            if (compared != 0) {
                return compared;
            }
        }
        return Integer.compare(left.size(), right.size());
    }

    private static int compareUtf8(String left, String right) {
        byte[] leftBytes = left.getBytes(StandardCharsets.UTF_8);
        byte[] rightBytes = right.getBytes(StandardCharsets.UTF_8);
        int length = Math.min(leftBytes.length, rightBytes.length);
        for (int index = 0; index < length; index++) {
            int compared = Integer.compare(
                Byte.toUnsignedInt(leftBytes[index]),
                Byte.toUnsignedInt(rightBytes[index]));
            if (compared != 0) {
                return compared;
            }
        }
        return Integer.compare(leftBytes.length, rightBytes.length);
    }

    private static void validateRequest(JsonNode request) throws OracleException {
        String operation = requiredText(request, "operation");
        Set<String> required = operation.equals("identity")
            ? Set.of("arguments", "configuration", "id", "operation", "schema")
            : Set.of(
                "arguments", "configuration", "id", "ontology_path", "operation", "schema");
        if (request.size() != required.size()
                || !required.stream().allMatch(request::has)) {
            throw new OracleException("invalid_request");
        }
        JsonNode arguments = request.get("arguments");
        JsonNode configuration = request.get("configuration");
        if (arguments == null || !arguments.isObject()
                || configuration == null || !configuration.isObject()
                || configuration.size() != 4
                || configuration.path("allow_fresh_entities").asBoolean(false) != true
                || configuration.path("incremental").asBoolean(true) != false
                || !"ignore".equals(configuration.path("unsupported").asText())
                || configuration.path("workers").asInt(-1) != 1) {
            throw new OracleException("invalid_configuration");
        }
    }

    private static String requiredText(JsonNode node, String field) throws OracleException {
        JsonNode value = node.get(field);
        if (value == null || !value.isTextual() || value.textValue().isEmpty()) {
            throw new OracleException("invalid_request");
        }
        return value.textValue();
    }

    private static Path requiredPath(JsonNode node, String field) throws OracleException {
        Path path = Path.of(requiredText(node, field)).toAbsolutePath().normalize();
        if (!Files.isRegularFile(path)) {
            throw new OracleException("input_not_found");
        }
        return path;
    }

    private static Path optionalPath(JsonNode node, String field) throws OracleException {
        JsonNode value = node.get(field);
        if (value == null || value.isNull()) {
            return null;
        }
        return requiredPath(node, field);
    }

    private static List<Path> requiredPaths(JsonNode node, String field)
            throws OracleException {
        JsonNode values = node.get(field);
        if (values == null || !values.isArray() || values.isEmpty()) {
            throw new OracleException("invalid_request");
        }
        List<Path> result = new ArrayList<>();
        for (JsonNode value : values) {
            if (!value.isTextual()) {
                throw new OracleException("invalid_request");
            }
            Path path = Path.of(value.textValue()).toAbsolutePath().normalize();
            if (!Files.isRegularFile(path)) {
                throw new OracleException("input_not_found");
            }
            result.add(path);
        }
        return result;
    }

    static final class OracleException extends Exception {
        final String category;

        OracleException(String category) {
            super(category);
            this.category = category;
        }
    }

    static final class Result {
        final Object value;
        final boolean complete;
        final Map<String, Integer> features;
        final List<Map<String, Object>> diagnostics;

        Result(Object value, boolean complete, Map<String, Integer> features,
                List<Map<String, Object>> diagnostics) {
            this.value = value;
            this.complete = complete;
            this.features = features;
            this.diagnostics = diagnostics;
        }
    }

    private static final class QueryExpectation {
        final ElkAxiom axiom;
        final boolean expected;
        final Path path;

        QueryExpectation(ElkAxiom axiom, boolean expected, Path path) {
            this.axiom = axiom;
            this.expected = expected;
            this.path = path;
        }
    }

    private static final class LoadedReasoner implements AutoCloseable {
        final Reasoner reasoner;

        private LoadedReasoner(Reasoner reasoner) {
            this.reasoner = reasoner;
        }

        static LoadedReasoner fromPath(Path ontology) throws IOException {
            List<ElkAxiom> axioms;
            try (InputStream stream = Files.newInputStream(ontology)) {
                axioms = PinnedElkDispatchBridge.repair(
                    TestReasonerUtils.loadAxioms(stream));
            } catch (org.semanticweb.elk.owl.parsing.Owl2ParseException error) {
                throw new IOException("cannot parse ontology", error);
            }
            return fromAxioms(axioms);
        }

        static LoadedReasoner empty() {
            return fromAxioms(List.of());
        }

        private static LoadedReasoner fromAxioms(List<ElkAxiom> axioms) {
            ReasonerConfiguration config = ReasonerConfiguration.getConfiguration();
            config.setParameter(ReasonerConfiguration.NUM_OF_WORKING_THREADS, "1");
            config.setParameter(ReasonerConfiguration.INCREMENTAL_MODE_ALLOWED, "false");
            config.setParameter(
                ReasonerConfiguration.CLASS_EXPRESSION_QUERY_EVICTOR,
                "NQEvictor(0, 0.75)");
            config.setParameter(
                ReasonerConfiguration.ENTAILMENT_QUERY_EVICTOR,
                "NQEvictor(0, 0.75)");
            TestChangesLoader loader = new TestChangesLoader();
            for (ElkAxiom axiom : axioms) {
                loader.add(axiom);
            }
            Reasoner reasoner = TestReasonerUtils.createTestReasoner(loader, config);
            reasoner.setAllowFreshEntities(true);
            return new LoadedReasoner(reasoner);
        }

        @Override
        public void close() throws InterruptedException {
            reasoner.shutdown(30, TimeUnit.SECONDS);
        }
    }
}
