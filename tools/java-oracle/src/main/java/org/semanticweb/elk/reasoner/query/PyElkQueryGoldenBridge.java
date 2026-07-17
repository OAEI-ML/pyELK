package org.semanticweb.elk.reasoner.query;

import java.io.IOException;
import java.io.InputStream;
import java.net.URL;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Comparator;
import java.util.List;

import org.semanticweb.elk.owl.interfaces.ElkClassExpression;
import org.semanticweb.elk.owl.printers.OwlFunctionalStylePrinter;
import org.semanticweb.elk.reasoner.Reasoner;
import org.semanticweb.elk.testing.TestManifestWithOutput;
import org.semanticweb.elk.testing.TestResultComparisonException;

/** Package-local adapter over ELK's exact frozen class-query test outputs. */
public final class PyElkQueryGoldenBridge {

    private PyElkQueryGoldenBridge() {}

    public static List<ElkClassExpression> loadQueries(Path golden, URL ontology)
            throws IOException {
        ElkExpectedTestOutputLoader loader;
        try (InputStream stream = Files.newInputStream(golden)) {
            loader = ElkExpectedTestOutputLoader.load(stream);
        }
        List<ElkClassExpression> result = new ArrayList<>();
        for (QueryTestManifest<ElkClassExpression, EmptyTestOutput> manifest
                : loader.getNoOutputManifests(golden.getFileName().toString(), ontology)) {
            result.add(manifest.getInput().getQuery());
        }
        result.sort(Comparator.comparing(
            value -> OwlFunctionalStylePrinter.toString(value, true)));
        return result;
    }

    public static int verify(Path golden, URL ontology, Reasoner reasoner)
            throws IOException, TestResultComparisonException,
                   org.semanticweb.elk.exceptions.ElkException {
        ElkExpectedTestOutputLoader loader;
        try (InputStream stream = Files.newInputStream(golden)) {
            loader = ElkExpectedTestOutputLoader.load(stream);
        }
        int checked = 0;
        for (TestManifestWithOutput<QueryTestInput<ElkClassExpression>, SatisfiabilityTestOutput>
                manifest : loader.getClassExpressionSatisfiabilityTestManifests(
                    golden.getFileName().toString(), ontology)) {
            ElkClassExpression query = manifest.getInput().getQuery();
            manifest.compare(new SatisfiabilityTestOutput(reasoner.isSatisfiableQuietly(query)));
            checked++;
        }
        for (TestManifestWithOutput<QueryTestInput<ElkClassExpression>, ElkEquivalentClassesTestOutput>
                manifest : loader.getEquivalentClassesManifests(
                    golden.getFileName().toString(), ontology)) {
            ElkClassExpression query = manifest.getInput().getQuery();
            manifest.compare(new ElkEquivalentClassesTestOutput(
                reasoner.getEquivalentClassesQuietly(query)));
            checked++;
        }
        for (TestManifestWithOutput<QueryTestInput<ElkClassExpression>, ElkDirectSuperClassesTestOutput>
                manifest : loader.getDirectSuperClassesManifests(
                    golden.getFileName().toString(), ontology)) {
            ElkClassExpression query = manifest.getInput().getQuery();
            manifest.compare(new ElkDirectSuperClassesTestOutput(
                query, reasoner.getSuperClassesQuietly(query, true)));
            checked++;
        }
        for (TestManifestWithOutput<QueryTestInput<ElkClassExpression>, ElkDirectSubClassesTestOutput>
                manifest : loader.getDirectSubClassesManifests(
                    golden.getFileName().toString(), ontology)) {
            ElkClassExpression query = manifest.getInput().getQuery();
            manifest.compare(new ElkDirectSubClassesTestOutput(
                query, reasoner.getSubClassesQuietly(query, true)));
            checked++;
        }
        for (TestManifestWithOutput<QueryTestInput<ElkClassExpression>, ElkDirectInstancesTestOutput>
                manifest : loader.getInstancesManifests(
                    golden.getFileName().toString(), ontology)) {
            ElkClassExpression query = manifest.getInput().getQuery();
            manifest.compare(new ElkDirectInstancesTestOutput(
                query, reasoner.getInstancesQuietly(query, true)));
            checked++;
        }
        return checked;
    }
}
