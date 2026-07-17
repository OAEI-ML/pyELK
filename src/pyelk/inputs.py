"""Thin, identity-preserving ingestion over :mod:`pyowl_core`.

pyELK owns no parser, resolver, ontology model, or shared-model cache.  Acquisition is the
exact core operation; an existing view or provider crosses this module through one core
coercion call and is retained without materialising its axiom closure.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum

import pyowl_core as _core
from pyowl_core import (
    IRI,
    ImportManifest,
    ImportPolicy,
    ImportResolver,
    LoadOptions,
    OntologyComposite,
    OntologyInput,
    OntologyOverlay,
    OntologySnapshot,
    OntologyView,
)

from .core import CapturedOntology, capture_compatible_view

# These aliases intentionally preserve core identity, signatures, exceptions, and future
# compatible 0.1 implementation fixes.  In particular, load_snapshot continues to reject
# view/provider input rather than materialising it into a concrete snapshot.
load_snapshot = _core.load_snapshot
coerce_snapshot = _core.coerce_snapshot

_EMPTY_OPTIONS_FINGERPRINT = hashlib.sha256(
    b"pyelk:semantic-options:none:v1\x00"
).digest()


class OntologyViewKind(str, Enum):
    """Storage shape captured for diagnostics, never for OWL identity."""

    SNAPSHOT = "snapshot"
    OVERLAY = "overlay"
    COMPOSITE = "composite"
    ADAPTER = "adapter"


@dataclass(frozen=True, slots=True)
class ImportClosureMetadata:
    """Small policy record retaining core manifests by identity.

    The record deliberately does not copy document or edge tables.  ``requires_incomplete_imports``
    is the only pyELK policy observation needed before the facade attaches its policy feature.
    """

    is_complete: bool
    manifests: tuple[ImportManifest, ...]
    policies: tuple[ImportPolicy, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.is_complete, bool):
            raise TypeError("is_complete must be bool")
        if not all(isinstance(item, ImportManifest) for item in self.manifests):
            raise TypeError("manifests must contain pyowl_core.ImportManifest values")
        if not all(isinstance(item, ImportPolicy) for item in self.policies):
            raise TypeError("policies must contain pyowl_core.ImportPolicy values")
        if len(self.policies) != len(self.manifests):
            raise ValueError("one import policy is required for each retained manifest")

    @property
    def requires_incomplete_imports(self) -> bool:
        """Whether the facade must explicitly allow and report incomplete imports."""

        return not self.is_complete


@dataclass(frozen=True, slots=True)
class ViewRevision:
    """Exact effective-view fingerprints plus bounded storage-shape diagnostics."""

    kind: OntologyViewKind
    structural_fingerprint: _core.Fingerprint
    logical_fingerprint: _core.Fingerprint
    signature_fingerprint: _core.Fingerprint
    overlay_depth: int
    component_count: int
    delta_entries: int

    def __post_init__(self) -> None:
        if not isinstance(self.kind, OntologyViewKind):
            raise TypeError("kind must be OntologyViewKind")
        for name in (
            "structural_fingerprint",
            "logical_fingerprint",
            "signature_fingerprint",
        ):
            if not isinstance(getattr(self, name), _core.Fingerprint):
                raise TypeError(f"{name} must be pyowl_core.Fingerprint")
        for name in ("overlay_depth", "delta_entries"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if (
            isinstance(self.component_count, bool)
            or not isinstance(self.component_count, int)
            or self.component_count < 1
        ):
            raise ValueError("component_count must be a positive integer")


@dataclass(frozen=True, slots=True)
class SemanticCacheRecord:
    """Versioned inputs to a future pyELK compiler/session cache key.

    WP4 owns hashing this record into the private ``source_fingerprint``.  Keeping this as a
    record here prevents paths, syntax, Python hashes, or structural-only provenance from
    entering semantic cache identity accidentally.
    """

    logical_fingerprint: _core.Fingerprint
    signature_fingerprint: _core.Fingerprint
    core_package_version: str
    core_api_version: tuple[int, int]
    core_model_schema_version: int
    core_wire_format_version: tuple[int, int]
    core_adapter_protocol_version: int
    compiler_schema_version: int
    compatibility_id: str
    semantic_options_fingerprint: bytes

    def __post_init__(self) -> None:
        for name in ("logical_fingerprint", "signature_fingerprint"):
            if not isinstance(getattr(self, name), _core.Fingerprint):
                raise TypeError(f"{name} must be pyowl_core.Fingerprint")
        if not isinstance(self.core_package_version, str) or not self.core_package_version:
            raise ValueError("core_package_version must be a nonempty string")
        for name in ("core_api_version", "core_wire_format_version"):
            value = getattr(self, name)
            if (
                not isinstance(value, tuple)
                or len(value) != 2
                or not all(
                    isinstance(item, int) and not isinstance(item, bool) and item >= 0
                    for item in value
                )
            ):
                raise TypeError(f"{name} must be a pair of nonnegative integers")
        for name in (
            "core_model_schema_version",
            "core_adapter_protocol_version",
            "compiler_schema_version",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not isinstance(self.compatibility_id, str) or not self.compatibility_id:
            raise ValueError("compatibility_id must be a nonempty string")
        if (
            not isinstance(self.semantic_options_fingerprint, bytes)
            or len(self.semantic_options_fingerprint) != 32
        ):
            raise ValueError("semantic_options_fingerprint must be exactly 32 bytes")


@dataclass(frozen=True, slots=True)
class StructuralCacheRecord:
    """Structural/provenance cache identity kept separate from semantic reuse."""

    semantic: SemanticCacheRecord
    structural_fingerprint: _core.Fingerprint

    def __post_init__(self) -> None:
        if not isinstance(self.semantic, SemanticCacheRecord):
            raise TypeError("semantic must be SemanticCacheRecord")
        if not isinstance(self.structural_fingerprint, _core.Fingerprint):
            raise TypeError("structural_fingerprint must be pyowl_core.Fingerprint")


@dataclass(frozen=True, slots=True)
class InputCapture:
    """One captured view plus bounded policy/revision metadata."""

    ontology: CapturedOntology
    imports: ImportClosureMetadata
    revision: ViewRevision

    def __post_init__(self) -> None:
        if not isinstance(self.ontology, CapturedOntology):
            raise TypeError("ontology must be CapturedOntology")
        if not isinstance(self.imports, ImportClosureMetadata):
            raise TypeError("imports must be ImportClosureMetadata")
        if not isinstance(self.revision, ViewRevision):
            raise TypeError("revision must be ViewRevision")
        if self.revision.structural_fingerprint is not self.ontology.structural_fingerprint:
            raise ValueError("captured structural fingerprint identity changed")
        if self.revision.logical_fingerprint is not self.ontology.logical_fingerprint:
            raise ValueError("captured logical fingerprint identity changed")
        if self.revision.signature_fingerprint is not self.ontology.signature_fingerprint:
            raise ValueError("captured signature fingerprint identity changed")


def capture_input(
    source: OntologyInput,
    *,
    document_iri: IRI | str | None = None,
    options: LoadOptions | None = None,
    resolver: ImportResolver | None = None,
) -> InputCapture:
    """Coerce exactly once, validate, and retain a bounded input observation."""

    view = _core.coerce_snapshot(
        source,
        document_iri=document_iri,
        options=options,
        resolver=resolver,
    )
    ontology = capture_compatible_view(view)
    return InputCapture(
        ontology=ontology,
        imports=describe_import_closure(view),
        revision=describe_view_revision(ontology),
    )


def capture_ontology(
    source: OntologyInput,
    *,
    document_iri: IRI | str | None = None,
    options: LoadOptions | None = None,
    resolver: ImportResolver | None = None,
) -> CapturedOntology:
    """Return the frozen contract capture used by the future reasoner facade."""

    return capture_input(
        source,
        document_iri=document_iri,
        options=options,
        resolver=resolver,
    ).ontology


def describe_import_closure(view: OntologyView) -> ImportClosureMetadata:
    """Retain public leaf manifests without walking their document/import edge tables."""

    manifests = _leaf_import_manifests(view)
    return ImportClosureMetadata(
        is_complete=view.is_complete,
        manifests=manifests,
        policies=tuple(manifest.policy for manifest in manifests),
    )


def describe_view_revision(captured: CapturedOntology) -> ViewRevision:
    """Describe storage shape without materialising, compacting, or mutating the view."""

    if not isinstance(captured, CapturedOntology):
        raise TypeError("captured must be CapturedOntology")
    view = captured.view
    kind = OntologyViewKind.ADAPTER
    overlay_depth = 0
    component_count = 1
    delta_entries = 0
    if isinstance(view, OntologySnapshot):
        kind = OntologyViewKind.SNAPSHOT
    elif isinstance(view, OntologyOverlay):
        kind = OntologyViewKind.OVERLAY
        overlay_depth = view.depth
        delta_entries = view.delta.entry_count
    elif isinstance(view, OntologyComposite):
        kind = OntologyViewKind.COMPOSITE
        component_count = len(view.members)
        delta_entries = view.delta.entry_count
    return ViewRevision(
        kind=kind,
        structural_fingerprint=captured.structural_fingerprint,
        logical_fingerprint=captured.logical_fingerprint,
        signature_fingerprint=captured.signature_fingerprint,
        overlay_depth=overlay_depth,
        component_count=component_count,
        delta_entries=delta_entries,
    )


def semantic_cache_record(
    captured: CapturedOntology,
    *,
    compiler_schema_version: int,
    compatibility_id: str,
    semantic_options_fingerprint: bytes = _EMPTY_OPTIONS_FINGERPRINT,
) -> SemanticCacheRecord:
    """Create the complete backend-neutral input portion of a compiler cache key."""

    if not isinstance(captured, CapturedOntology):
        raise TypeError("captured must be CapturedOntology")
    return SemanticCacheRecord(
        logical_fingerprint=captured.logical_fingerprint,
        signature_fingerprint=captured.signature_fingerprint,
        core_package_version=captured.core_package_version,
        core_api_version=captured.core_api_version,
        core_model_schema_version=captured.core_model_schema_version,
        core_wire_format_version=captured.core_wire_format_version,
        core_adapter_protocol_version=captured.core_adapter_protocol_version,
        compiler_schema_version=compiler_schema_version,
        compatibility_id=compatibility_id,
        semantic_options_fingerprint=semantic_options_fingerprint,
    )


def structural_cache_record(
    captured: CapturedOntology,
    *,
    compiler_schema_version: int,
    compatibility_id: str,
    semantic_options_fingerprint: bytes = _EMPTY_OPTIONS_FINGERPRINT,
) -> StructuralCacheRecord:
    """Add structural provenance identity without changing semantic cache semantics."""

    return StructuralCacheRecord(
        semantic=semantic_cache_record(
            captured,
            compiler_schema_version=compiler_schema_version,
            compatibility_id=compatibility_id,
            semantic_options_fingerprint=semantic_options_fingerprint,
        ),
        structural_fingerprint=captured.structural_fingerprint,
    )


def _leaf_import_manifests(view: OntologyView) -> tuple[ImportManifest, ...]:
    pending: list[OntologyView] = [view]
    observed_views: set[int] = set()
    observed_manifests: set[int] = set()
    manifests: list[ImportManifest] = []
    while pending:
        current = pending.pop()
        token = id(current)
        if token in observed_views:
            continue
        observed_views.add(token)
        if isinstance(current, OntologySnapshot):
            manifest = current.import_manifest
            manifest_token = id(manifest)
            if manifest_token not in observed_manifests:
                observed_manifests.add(manifest_token)
                manifests.append(manifest)
        elif isinstance(current, OntologyOverlay):
            pending.append(current.base)
        elif isinstance(current, OntologyComposite):
            pending.extend(member.view for member in reversed(current.members))
    return tuple(manifests)


__all__ = [
    "ImportClosureMetadata",
    "InputCapture",
    "OntologyViewKind",
    "SemanticCacheRecord",
    "StructuralCacheRecord",
    "ViewRevision",
    "capture_input",
    "capture_ontology",
    "coerce_snapshot",
    "describe_import_closure",
    "describe_view_revision",
    "load_snapshot",
    "semantic_cache_record",
    "structural_cache_record",
]
