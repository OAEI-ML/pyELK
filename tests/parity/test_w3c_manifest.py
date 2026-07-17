from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "tests" / "data" / "w3c" / "manifest.toml"


def _field(block: str, name: str) -> str:
    match = re.search(rf"^{name} = \"([^\"]*)\"$", block, re.MULTILINE)
    assert match is not None, (name, block[:120])
    return match.group(1)


def test_w3c_manifest_is_the_complete_approved_direct_el_inventory() -> None:
    text = MANIFEST.read_text(encoding="utf-8")
    assert text.startswith('schema = "pyelk.w3c-el-manifest/1"')
    assert (
        'source_sha256 = "a703d36b774f55f14c0758cf20f2bdd635677045f7ba55053199660c10d6fefc"' in text
    )
    assert 'elk_commit = "b8ac5ce83db0704a7359d96aa382891e2f547863"' in text
    assert 'pyowl_core_commit = "6df155e3ef83588352dbfd11bc4b15bdc0fa9c4e"' in text
    assert "java_oracle_run = true" in text
    assert "case_count = 65" in text
    blocks = text.split("[[cases]]")[1:]
    assert len(blocks) == 65
    identifiers = [_field(block, "id") for block in blocks]
    assert len(set(identifiers)) == 65
    assert identifiers == sorted(identifiers, key=str.encode)


def test_every_w3c_case_has_immutable_input_expected_elk_and_scope_classification() -> None:
    text = MANIFEST.read_text(encoding="utf-8")
    blocks = text.split("[[cases]]")[1:]
    categories = Counter(_field(block, "classification") for block in blocks)
    assert sum(categories.values()) == 65
    assert categories == {
        "elk-complete": 15,
        "elk-incomplete-as-designed": 28,
        "outside-pyowl-core-input-scope": 22,
    }
    for block in blocks:
        assert _field(block, "status") == "Approved"
        assert _field(block, "semantics") == "DIRECT"
        assert _field(block, "profile") == "EL"
        assert len(_field(block, "input_sha256")) == 64
        assert _field(block, "source_url").startswith("http")
        assert "expected_direct = [" in block
        assert "elk_0_6_complete = " in block
        assert "elk_0_6_result_json = " in block
        assert "TODO" not in block and "unknown" not in block.lower()
