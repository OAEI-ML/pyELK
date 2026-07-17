from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pyelk

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = re.compile(
    r"<!-- pyelk-readme-example -->\s*```python\n(?P<source>.*?)\n```",
    re.DOTALL,
)


def test_readme_python_examples_run_verbatim_without_java(tmp_path: Path) -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    examples = [match.group("source") for match in EXAMPLE.finditer(readme)]
    assert len(examples) == 5
    environment = os.environ.copy()
    environment["PATH"] = str(Path(sys.executable).resolve().parent)
    if ROOT in Path(pyelk.__file__).resolve().parents:
        environment["PYTHONPATH"] = os.pathsep.join(
            (str(ROOT / "src"), str(ROOT.parent / "pyOWLCore" / "src"))
        )
    else:
        environment.pop("PYTHONPATH", None)
    for index, source in enumerate(examples, start=1):
        completed = subprocess.run(
            [sys.executable, "-c", source],
            cwd=tmp_path,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, (
            f"README example {index} failed\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
