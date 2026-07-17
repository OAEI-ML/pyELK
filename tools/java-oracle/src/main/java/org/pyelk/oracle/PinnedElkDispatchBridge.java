package org.pyelk.oracle;

import java.util.ArrayList;
import java.util.List;

import org.semanticweb.elk.owl.interfaces.ElkAxiom;
import org.semanticweb.elk.owl.interfaces.ElkDataRange;
import org.semanticweb.elk.owl.interfaces.ElkDatatype;
import org.semanticweb.elk.owl.interfaces.ElkDatatypeDefinitionAxiom;
import org.semanticweb.elk.owl.visitors.ElkAxiomVisitor;
import org.semanticweb.elk.owl.visitors.ElkDatatypeDefinitionAxiomVisitor;
import org.semanticweb.elk.owl.visitors.ElkObjectVisitor;

/**
 * Repairs one investigated visitor-dispatch defect in the pinned ELK object implementation.
 *
 * <p>At commit {@code b8ac5ce83db0704a7359d96aa382891e2f547863},
 * {@code ElkDatatypeDefinitionAxiomImpl.accept(ElkAxiomVisitor)} returns {@code null} instead
 * of calling the visitor. That prevents ELK's existing ontology and query converters from
 * recording the two corresponding {@code Feature} values. The wrapper below changes only
 * that broken overload and delegates the structural payload unchanged.</p>
 */
final class PinnedElkDispatchBridge {

    private PinnedElkDispatchBridge() {}

    static List<ElkAxiom> repair(List<ElkAxiom> axioms) {
        List<ElkAxiom> repaired = new ArrayList<>(axioms.size());
        for (ElkAxiom axiom : axioms) {
            if (axiom instanceof ElkDatatypeDefinitionAxiom) {
                repaired.add(new DatatypeDefinitionAxiom(
                    (ElkDatatypeDefinitionAxiom) axiom));
            } else {
                repaired.add(axiom);
            }
        }
        return repaired;
    }

    private static final class DatatypeDefinitionAxiom
            implements ElkDatatypeDefinitionAxiom {
        private final ElkDatatypeDefinitionAxiom delegate;

        DatatypeDefinitionAxiom(ElkDatatypeDefinitionAxiom delegate) {
            this.delegate = delegate;
        }

        @Override
        public ElkDatatype getDatatype() {
            return delegate.getDatatype();
        }

        @Override
        public ElkDataRange getDataRange() {
            return delegate.getDataRange();
        }

        @Override
        public <O> O accept(ElkAxiomVisitor<O> visitor) {
            return visitor.visit(this);
        }

        @Override
        public <O> O accept(ElkObjectVisitor<O> visitor) {
            return visitor.visit(this);
        }

        @Override
        public <O> O accept(ElkDatatypeDefinitionAxiomVisitor<O> visitor) {
            return visitor.visit(this);
        }
    }
}
