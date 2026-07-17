"""Java-free ELK-compatible OWL reasoning over shared pyowl-core values."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyowl_core import (
    API_VERSION,
    Fingerprint,
    ImportResolver,
    LoadOptions,
    OntologyComposite,
    OntologyDelta,
    OntologyDocument,
    OntologyOverlay,
    OntologySnapshot,
    OntologyView,
    SnapshotProvider,
)

from pyelk.config import ReasonerConfig
from pyelk.inputs import load_snapshot
from pyelk.owl import *  # noqa: F403
from pyelk.owl import __all__ as _OWL_ALL
from pyelk.result import (
    CompletenessIssue,
    EntityNode,
    InstanceTaxonomy,
    PolicyFeature,
    ReasoningResult,
    Taxonomy,
)

if TYPE_CHECKING:
    from pyelk.api import Reasoner
    from pyelk.backends import backend_report

__all__ = [
    "API_VERSION",
    "CompletenessIssue",
    "EntityNode",
    "Fingerprint",
    "ImportResolver",
    "InstanceTaxonomy",
    "LoadOptions",
    "OntologyComposite",
    "OntologyDelta",
    "OntologyDocument",
    "OntologyOverlay",
    "OntologySnapshot",
    "OntologyView",
    "PolicyFeature",
    "Reasoner",
    "ReasonerConfig",
    "ReasoningResult",
    "SnapshotProvider",
    "Taxonomy",
    "backend_report",
    "load_snapshot",
    *_OWL_ALL,
]


def __getattr__(name: str) -> object:
    """Load facade/dispatcher code only when those public names are requested."""

    if name == "Reasoner":
        from pyelk.api import Reasoner

        globals()[name] = Reasoner
        return Reasoner
    if name == "backend_report":
        from pyelk.backends import backend_report

        globals()[name] = backend_report
        return backend_report
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()).union(__all__))
