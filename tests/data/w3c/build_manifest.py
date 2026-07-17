#!/usr/bin/env python3
"""Regenerate the approved Direct-Semantics OWL 2 EL compatibility manifest.

The input is the W3C test-suite export retained by the pinned HermiT reference tree. Bodies
are hashed and classified but are not copied because redistribution rights are unresolved.
When the pinned pyELK oracle is supplied, parseable cases also retain live ELK 0.6.0 values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pyowl_core as owl

from pyelk import Reasoner, ReasonerConfig

TEST = "http://www.w3.org/2007/OWL/testOntology#"
TEST_TAG = f"{{{TEST}}}"
RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
PINNED_SOURCE_SHA256 = "a703d36b774f55f14c0758cf20f2bdd635677045f7ba55053199660c10d6fefc"
PINNED_SOURCE_COMMIT = "37ec30aced32ac81ebecc5e33fad255ddefcb4c3"
ELK_COMMIT = "b8ac5ce83db0704a7359d96aa382891e2f547863"
CORE_COMMIT = "6df155e3ef83588352dbfd11bc4b15bdc0fa9c4e"
CONFIGURATION = {
    "allow_fresh_entities": True,
    "incremental": False,
    "unsupported": "ignore",
    "workers": 1,
}


class JavaOracle:
    def __init__(self, java: Path | None, jar: Path | None) -> None:
        self.process: subprocess.Popen[str] | None = None
        if java is not None and jar is not None:
            self.process = subprocess.Popen(
                [os.fspath(java), "-jar", os.fspath(jar)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

    def request(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if self.process is None:
            return None
        if self.process.stdin is None or self.process.stdout is None:
            raise RuntimeError("Java oracle protocol streams are unavailable")
        self.process.stdin.write(json.dumps(payload, sort_keys=True) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            stderr = "" if self.process.stderr is None else self.process.stderr.read()
            raise RuntimeError(f"Java oracle exited during W3C generation: {stderr}")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise RuntimeError("Java oracle returned a non-object response")
        return value

    def close(self) -> None:
        if self.process is None:
            return
        if self.process.stdin is not None:
            self.process.stdin.close()
        returncode = self.process.wait(timeout=30)
        if returncode:
            stderr = "" if self.process.stderr is None else self.process.stderr.read()
            raise RuntimeError(f"Java oracle failed with status {returncode}: {stderr}")


def _resources(case: ET.Element, name: str) -> list[str | None]:
    return [item.attrib.get(RDF + "resource") for item in case.findall(TEST_TAG + name)]


def _types(case: ET.Element) -> tuple[str, ...]:
    return tuple(
        sorted(
            (
                item.attrib.get(RDF + "resource", "").rsplit("#", 1)[-1]
                for item in case.findall(RDF + "type")
                if item.attrib.get(RDF + "resource", "").endswith("Test")
            ),
            key=str.encode,
        )
    )


def _bodies(case: ET.Element) -> dict[str, str]:
    return {
        item.tag.rsplit("}", 1)[-1]: item.text or ""
        for item in case
        if item.tag.rsplit("}", 1)[-1].endswith("Ontology")
    }


def _expected(types: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    if "ConsistencyTest" in types:
        result.append("consistent")
    if "InconsistencyTest" in types:
        result.append("inconsistent")
    if "PositiveEntailmentTest" in types:
        result.append("entailed")
    if "NegativeEntailmentTest" in types:
        result.append("not-entailed")
    return tuple(result)


def _body_key(bodies: dict[str, str], stem: str) -> str | None:
    functional = f"fs{stem}Ontology"
    rdf_xml = f"rdfXml{stem}Ontology"
    if functional in bodies:
        return functional
    return rdf_xml if rdf_xml in bodies else None


def _load(body: str, key: str, iri: str) -> owl.OntologySnapshot:
    format = owl.DocumentFormat.FUNCTIONAL if key.startswith("fs") else owl.DocumentFormat.RDF_XML
    return owl.load_snapshot(
        body.encode(),
        document_iri=owl.IRI(iri),
        options=owl.LoadOptions(
            format=format,
            imports=owl.ImportPolicy.IGNORE,
            backend=owl.BackendPreference.PYTHON,
        ),
    )


def _core_result(
    premise: owl.OntologySnapshot,
    conclusion: owl.OntologySnapshot | None,
    types: tuple[str, ...],
) -> tuple[dict[str, Any], bool, tuple[str, ...]]:
    payload: dict[str, Any] = {}
    complete = True
    features: set[str] = set()
    with Reasoner(
        premise,
        ReasonerConfig(backend="python", workers=1, allow_incomplete_imports=True),
    ) as reasoner:
        if "ConsistencyTest" in types or "InconsistencyTest" in types:
            result = reasoner.is_consistent()
            payload["consistent"] = result.value
            complete &= result.complete
            features.update(feature for issue in result.reasons for feature in issue.features)
        if conclusion is not None:
            rows: list[dict[str, Any]] = []
            for axiom in conclusion.iter_axioms():
                result = reasoner.is_entailed(axiom)
                rows.append(
                    {
                        "complete": result.complete,
                        "entailed": result.value,
                        "kind": type(axiom).__name__,
                    }
                )
                complete &= result.complete
                features.update(feature for issue in result.reasons for feature in issue.features)
            payload["queries"] = rows
    return payload, complete, tuple(sorted(features, key=str.encode))


def _write_functional(path: Path, snapshot: owl.OntologySnapshot) -> None:
    path.write_bytes(owl.render_document(snapshot.root, format=owl.DocumentFormat.FUNCTIONAL))


def _oracle_request(
    oracle: JavaOracle,
    *,
    case_id: str,
    operation: str,
    ontology: Path,
    arguments: dict[str, Any],
) -> dict[str, Any] | None:
    return oracle.request(
        {
            "schema": 1,
            "id": f"w3c/{case_id}/{operation}",
            "ontology_path": os.fspath(ontology),
            "operation": operation,
            "arguments": arguments,
            "configuration": CONFIGURATION,
        }
    )


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_strings(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _render(rows: list[dict[str, Any]], *, source_sha256: str, java_enabled: bool) -> str:
    lines = [
        'schema = "pyelk.w3c-el-manifest/1"',
        'generated_on = "2026-07-17"',
        f'source_commit = "{PINNED_SOURCE_COMMIT}"',
        (
            'source_url = "https://github.com/phillord/hermit-reasoner/blob/'
            f"{PINNED_SOURCE_COMMIT}/src/test/resources/org/semanticweb/HermiT/"
            'owl_wg_tests/ontologies/all.rdf"'
        ),
        f'source_sha256 = "{source_sha256}"',
        f'elk_commit = "{ELK_COMMIT}"',
        f'pyowl_core_commit = "{CORE_COMMIT}"',
        f"java_oracle_run = {'true' if java_enabled else 'false'}",
        'redistribution = "metadata and hashes only; embedded ontology bodies are not copied"',
        f"case_count = {len(rows)}",
        "",
    ]
    for row in rows:
        lines.extend(
            (
                "[[cases]]",
                f"id = {_toml_string(row['id'])}",
                f"source_url = {_toml_string(row['source_url'])}",
                'status = "Approved"',
                'semantics = "DIRECT"',
                'profile = "EL"',
                f"test_types = {_toml_strings(row['test_types'])}",
                f"input_syntax = {_toml_string(row['input_syntax'])}",
                f"input_sha256 = {_toml_string(row['input_sha256'])}",
                f"expected_direct = {_toml_strings(row['expected_direct'])}",
                f"elk_0_6_complete = {'true' if row['elk_0_6_complete'] else 'false'}",
                f"elk_0_6_result_json = {_toml_string(row['elk_0_6_result_json'])}",
                f"classification = {_toml_string(row['classification'])}",
                f"classification_features = {_toml_strings(row['classification_features'])}",
                f"classification_detail = {_toml_string(row['classification_detail'])}",
            )
        )
        if row["conclusion_sha256"] is not None:
            lines.append(f"conclusion_sha256 = {_toml_string(row['conclusion_sha256'])}")
        lines.append("")
    return "\n".join(lines)


def generate(source: Path, *, java: Path | None, jar: Path | None) -> str:
    source_bytes = source.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    if source_sha256 != PINNED_SOURCE_SHA256:
        raise ValueError(f"unexpected W3C source SHA-256: {source_sha256}")
    root = ET.fromstring(source_bytes)
    oracle = JavaOracle(java, jar)
    rows: list[dict[str, Any]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="pyelk-w3c-") as temporary:
            directory = Path(temporary)
            for index, case in enumerate(root.findall(TEST_TAG + "TestCase")):
                if (
                    TEST + "Approved" not in _resources(case, "status")
                    or TEST + "DIRECT" not in _resources(case, "semantics")
                    or TEST + "EL" not in _resources(case, "profile")
                ):
                    continue
                identifier = case.findtext(TEST_TAG + "identifier") or f"case-{index}"
                source_url = case.attrib.get(RDF + "about", "")
                types = _types(case)
                bodies = _bodies(case)
                premise_key = _body_key(bodies, "Premise")
                if premise_key is None:
                    raise ValueError(f"approved EL case {identifier!r} has no premise")
                conclusion_stem = (
                    "Conclusion"
                    if "PositiveEntailmentTest" in types
                    else "NonConclusion"
                    if "NegativeEntailmentTest" in types
                    else None
                )
                conclusion_key = (
                    None if conclusion_stem is None else _body_key(bodies, conclusion_stem)
                )
                premise: owl.OntologySnapshot | None = None
                conclusion: owl.OntologySnapshot | None = None
                error: Exception | None = None
                if "importedOntology" in bodies:
                    error = ValueError("embedded import resolver case")
                try:
                    premise = _load(bodies[premise_key], premise_key, source_url)
                    if conclusion_key is not None:
                        conclusion = _load(
                            bodies[conclusion_key],
                            conclusion_key,
                            source_url + "#conclusion",
                        )
                except Exception as caught:
                    error = caught

                core_payload: dict[str, Any] = {}
                core_complete = False
                features: tuple[str, ...] = ()
                if error is None and premise is not None:
                    core_payload, core_complete, features = _core_result(
                        premise,
                        conclusion,
                        types,
                    )

                java_payload: dict[str, Any] = {}
                if premise is not None:
                    premise_path = directory / f"{index:04d}.ofn"
                    if premise_key.startswith("fs"):
                        premise_path.write_text(bodies[premise_key], encoding="utf-8")
                    else:
                        _write_functional(premise_path, premise)
                    if "ConsistencyTest" in types or "InconsistencyTest" in types:
                        java_payload["consistency"] = _oracle_request(
                            oracle,
                            case_id=identifier,
                            operation="consistency",
                            ontology=premise_path,
                            arguments={},
                        )
                    if conclusion is not None and conclusion_key is not None:
                        suffix = (
                            ".entailed" if "PositiveEntailmentTest" in types else ".notentailed"
                        )
                        query_path = directory / f"{index:04d}{suffix}"
                        if conclusion_key.startswith("fs"):
                            query_path.write_text(bodies[conclusion_key], encoding="utf-8")
                        else:
                            _write_functional(query_path, conclusion)
                        java_payload["entailment"] = _oracle_request(
                            oracle,
                            case_id=identifier,
                            operation="entailment",
                            ontology=premise_path,
                            arguments={"query_paths": [os.fspath(query_path)]},
                        )

                if error is not None:
                    classification = "outside-pyowl-core-input-scope"
                    detail = f"{type(error).__name__}: {error}".replace("\n", " ")
                elif core_complete:
                    classification = "elk-complete"
                    detail = "pyELK result is complete under the pinned ELK monitors"
                else:
                    classification = "elk-incomplete-as-designed"
                    detail = "pinned ELK incompleteness monitor reported the listed features"
                elk_result = java_payload if java_payload else {"pyelk_public_result": core_payload}
                rows.append(
                    {
                        "id": identifier,
                        "source_url": source_url,
                        "test_types": types,
                        "input_syntax": "FUNCTIONAL" if premise_key.startswith("fs") else "RDFXML",
                        "input_sha256": hashlib.sha256(bodies[premise_key].encode()).hexdigest(),
                        "expected_direct": _expected(types),
                        "elk_0_6_complete": core_complete,
                        "elk_0_6_result_json": json.dumps(
                            elk_result,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                        "classification": classification,
                        "classification_features": features,
                        "classification_detail": detail[:500],
                        "conclusion_sha256": (
                            None
                            if conclusion_key is None
                            else hashlib.sha256(bodies[conclusion_key].encode()).hexdigest()
                        ),
                    }
                )
    finally:
        oracle.close()
    rows.sort(key=lambda row: str(row["id"]).encode())
    return _render(rows, source_sha256=source_sha256, java_enabled=java is not None)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--java", type=Path)
    parser.add_argument("--oracle-jar", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    if (arguments.java is None) != (arguments.oracle_jar is None):
        raise SystemExit("--java and --oracle-jar must be supplied together")
    rendered = generate(
        arguments.source,
        java=arguments.java,
        jar=arguments.oracle_jar,
    )
    if arguments.output is None:
        sys.stdout.write(rendered)
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
