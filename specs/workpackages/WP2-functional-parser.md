# WP2 — Functional-Syntax Parser, Printer, and Ontology Value

## Goal

Implement a streaming OWL 2 Functional-Style Syntax lexer/parser and canonical printer with
the same recognised grammar as ELK 0.6.0. Add the immutable public `Ontology` document value
and import-policy metadata. Do not implement other OWL serialisations or network imports.

## Read first

| Source | Sections / symbols |
|---|---|
| `specs/parsing.md` | all |
| `specs/contracts.md` | §§2, 6 |
| `specs/verification.md` | §§2–4, 8 |
| pinned `Owl2FunctionalStyleParser.jj` | complete grammar |
| pinned `Owl2ParserLoader`, parser/printer tests | streaming and expected syntax |

## Depends on

WP1 and WP3 (for the attributed upstream corpus).

## Owned paths

```text
src/pyelk/parsing/**
src/pyelk/ontology.py
tests/unit/parsing/**
tests/properties/test_parser_roundtrip.py
tests/fuzz/seeds/parser/**
```

## Forbidden paths

`owl/**`, compiled indexing/reasoning/backends, Rust, completeness/oracle generators,
upstream fixture contents, build/release files, final `Reasoner` facade.

## Deliverables

1. Incremental lexer with exact source spans, prefix/full IRI, literal, comment, numeric,
   keyword, and delimiter handling.
2. Iterative parser for document, axiom, and class-expression entry points.
3. Supported typed values and lossless generic unsupported/annotation nodes with exact
   feature classification.
4. `Ontology` immutable document value, `parse`, `functional_syntax`, and `with_axioms`.
5. Import declarations recorded but never fetched; no side-effecting I/O beyond the supplied
   path/stream.
6. Iterative canonical and stored-order Functional Syntax printer.
7. Upstream parser/printer tests and all 124 ontology-input round trips.
8. Chunk-boundary, malformed-input, deep-nesting, Unicode, and bounded-memory tests.

## Acceptance criteria

1. Every upstream `.owl` input parses; canonical print → parse → print is byte-identical.
2. Known unsupported constructors parse as generic nodes; an unknown keyword is a positioned
   `ParseError`.
3. `iter_axioms` can consume a generated 1,000,000-axiom stream without retaining the full
   document; measured memory is bounded by current axiom/token buffer when output is not
   materialised.
4. Deep valid nesting uses no Python recursion and malformed nesting fails in bounded time.
5. Prefix/literal escape semantics match pinned parser fixtures, including the v0.6.0
   single-literal `DataOneOf` regression.
6. No RDF/XML/Turtle library, Java, or network access is introduced.
7. Full checks pass and only owned paths changed.
