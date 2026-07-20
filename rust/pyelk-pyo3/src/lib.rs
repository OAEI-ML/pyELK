//! Thin private PyO3 boundary for the Python-free pyELK core.

#![forbid(unsafe_code)]

use std::any::Any;
use std::collections::{BTreeMap, BTreeSet};
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::process;
use std::sync::{Arc, Mutex};

use blake2::digest::consts::U32;
use blake2::{Blake2b, Digest};
use pyelk_core::encoded::{
    ByteSource, DESCRIPTOR_SHA256_V1, EncodedColumns, EncodedCompilationSegment, EncodedLimits,
    EncodedPostingMode, EncodedUnsupportedPolicy, compile_encoded_hierarchy_selected_with_policy,
    compile_encoded_hierarchy_with_policy, compile_encoded_overlay_delta_selected_with_policy,
    compile_encoded_overlay_delta_with_policy, compile_encoded_segments_with_policy,
    validate_columns,
};
use pyelk_core::wire::{
    encode_compiler_metadata, encode_query, encode_realization, encode_taxonomy,
};
use pyelk_core::{
    CoreError, CoreResult, DiagnosticValue, IMPLEMENTATION_VERSION, IR_MAJOR, IR_MINOR,
    NativeCoreSession, QueryKind,
};
use pyo3::create_exception;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{
    PyBytes, PyDict, PyInt, PyMapping, PyMemoryView, PyModule, PyTuple, PyTupleMethods,
};
use sha2::Sha256;

type Blake2b256 = Blake2b<U32>;

create_exception!(_native, NativeUnsupportedFeatureError, PyValueError);

const ENCODED_SCHEMA_NAME: &str = "pyowl-core/structural-columns";
const ENCODED_SCHEMA_VERSION: u64 = 1;
const ENCODED_MODEL_SCHEMA: u64 = 1;
const ENCODED_BUFFER_COUNT: usize = 11;
const SEGMENT_DIRECT: u64 = 1;
const SEGMENT_OVERLAY_BASE: u64 = 2;
const SEGMENT_OVERLAY_DELTA: u64 = 3;
const SEGMENT_COMPOSITE_MEMBER: u64 = 4;
const SEGMENT_COMPOSITE_BRIDGE: u64 = 5;
const POSTINGS_ALL: u64 = 0;
const POSTINGS_INCLUDE: u64 = 1;
const POSTINGS_EXCLUDE: u64 = 2;
const ENCODED_BUFFER_NAMES: [&str; ENCODED_BUFFER_COUNT] = [
    "root_kinds",
    "root_ids",
    "node_tags",
    "node_field_offsets",
    "field_kinds",
    "field_values",
    "field_lengths",
    "item_kinds",
    "item_values",
    "item_lengths",
    "scalar_bytes",
];
const COMPILER_SCHEMA_VERSION: u64 = 1;
const ELK_COMPATIBILITY_ID: &str = "elk-0.6.0:b8ac5ce83db0704a7359d96aa382891e2f547863";

#[derive(Clone, Copy)]
struct BorrowedPyBytes<'a, 'py> {
    view: &'a Bound<'py, PyAny>,
    bytes: Option<&'a [u8]>,
    len: usize,
}

impl ByteSource for BorrowedPyBytes<'_, '_> {
    fn len(self) -> usize {
        self.len
    }

    fn byte(self, index: usize) -> Option<u8> {
        self.bytes
            .and_then(|bytes| bytes.get(index).copied())
            .or_else(|| self.view.get_item(index).ok()?.extract().ok())
    }
}

#[derive(Clone)]
struct EncodedBufferBinding<'py> {
    view: Bound<'py, PyAny>,
    bytes_owner: Option<Bound<'py, PyBytes>>,
}

#[derive(Clone)]
struct EncodedBufferBindings<'py> {
    root_kinds: EncodedBufferBinding<'py>,
    root_ids: EncodedBufferBinding<'py>,
    node_tags: EncodedBufferBinding<'py>,
    node_field_offsets: EncodedBufferBinding<'py>,
    field_kinds: EncodedBufferBinding<'py>,
    field_values: EncodedBufferBinding<'py>,
    field_lengths: EncodedBufferBinding<'py>,
    item_kinds: EncodedBufferBinding<'py>,
    item_values: EncodedBufferBinding<'py>,
    item_lengths: EncodedBufferBinding<'py>,
    scalar_bytes: EncodedBufferBinding<'py>,
}

#[derive(Clone, Copy)]
struct EncodedIngestionMetrics {
    buffer_count: u64,
    buffer_bytes: u64,
    zero_copy_buffers: u64,
    compiler_gil_released: bool,
    segment_count: u64,
    referenced_view_count: u64,
    posting_bytes: u64,
}

struct EncodedPostingBinding<'py> {
    mode: EncodedPostingMode,
    root_ids: EncodedBufferBinding<'py>,
}

struct ValidatedEncodedInput<'py> {
    bindings: EncodedBufferBindings<'py>,
    delta_bindings: Option<EncodedBufferBindings<'py>>,
    source_parts: EncodedSourceParts,
    segment_count: u64,
    referenced_view_count: u64,
    posting: Option<EncodedPostingBinding<'py>>,
    composite_bindings: Option<Vec<EncodedCompilationTableBinding<'py>>>,
    composite_posting_bytes: usize,
}

#[derive(Clone, Copy)]
struct DetachedSimpleInput<'a> {
    columns: EncodedColumns<&'a [u8]>,
    delta_columns: Option<EncodedColumns<&'a [u8]>>,
    posting: Option<(EncodedPostingMode, &'a [u8])>,
}

#[derive(Clone)]
struct EncodedCompilationTableBinding<'py> {
    bindings: EncodedBufferBindings<'py>,
    view_id: usize,
    selection: EncodedRootPlan,
    scope_map: BTreeMap<[u8; 32], [u8; 32]>,
}

#[derive(Clone, Debug)]
enum EncodedRootPlan {
    All,
    Include(BTreeSet<u32>),
    Exclude(BTreeSet<u32>),
    Dropped,
}

impl EncodedRootPlan {
    fn mode(&self) -> Option<EncodedPostingMode> {
        match self {
            Self::All | Self::Dropped => None,
            Self::Include(_) => Some(EncodedPostingMode::Include),
            Self::Exclude(_) => Some(EncodedPostingMode::Exclude),
        }
    }

    fn posting_bytes(&self) -> CoreResult<Vec<u8>> {
        let postings = match self {
            Self::Include(postings) | Self::Exclude(postings) => postings,
            Self::All | Self::Dropped => return Ok(Vec::new()),
        };
        let capacity = postings
            .len()
            .checked_mul(4)
            .ok_or_else(|| CoreError::capacity("encoded resolved posting bytes overflow"))?;
        let mut encoded = Vec::new();
        encoded
            .try_reserve_exact(capacity)
            .map_err(|_| CoreError::capacity("encoded resolved posting allocation failed"))?;
        for posting in postings {
            encoded.extend_from_slice(&posting.to_le_bytes());
        }
        Ok(encoded)
    }

    fn apply(&mut self, mode: EncodedPostingMode, postings: &BTreeSet<u32>) {
        let next = match (&*self, mode) {
            (Self::Dropped, _) => Self::Dropped,
            (Self::All, EncodedPostingMode::Include) => Self::included(postings.clone()),
            (Self::All, EncodedPostingMode::Exclude) => Self::excluded(postings.clone()),
            (Self::Include(current), EncodedPostingMode::Include) => {
                Self::included(current.intersection(postings).copied().collect())
            }
            (Self::Include(current), EncodedPostingMode::Exclude) => {
                Self::included(current.difference(postings).copied().collect())
            }
            (Self::Exclude(current), EncodedPostingMode::Include) => {
                Self::included(postings.difference(current).copied().collect())
            }
            (Self::Exclude(current), EncodedPostingMode::Exclude) => {
                Self::excluded(current.union(postings).copied().collect())
            }
        };
        *self = next;
    }

    fn included(postings: BTreeSet<u32>) -> Self {
        if postings.is_empty() {
            Self::Dropped
        } else {
            Self::Include(postings)
        }
    }

    fn excluded(postings: BTreeSet<u32>) -> Self {
        if postings.is_empty() {
            Self::All
        } else {
            Self::Exclude(postings)
        }
    }
}

fn encoded_scope_map(scope_map: &BTreeMap<[u8; 32], [u8; 32]>) -> CoreResult<Vec<u8>> {
    let capacity = scope_map
        .len()
        .checked_mul(64)
        .ok_or_else(|| CoreError::capacity("encoded scope-map bytes overflow"))?;
    let mut encoded = Vec::new();
    encoded
        .try_reserve_exact(capacity)
        .map_err(|_| CoreError::capacity("encoded scope-map allocation failed"))?;
    for (source, target) in scope_map {
        encoded.extend_from_slice(source);
        encoded.extend_from_slice(target);
    }
    Ok(encoded)
}

#[derive(Clone)]
struct ResolvedEncodedView<'py> {
    tables: Vec<EncodedCompilationTableBinding<'py>>,
    local_root_count: usize,
}

struct CompositeResolver<'py> {
    model_schema: u64,
    active: BTreeSet<usize>,
    cache: BTreeMap<usize, ResolvedEncodedView<'py>>,
    referenced: BTreeSet<usize>,
    segment_count: u64,
    posting_bytes: usize,
}

impl<'py> CompositeResolver<'py> {
    fn new(model_schema: u64, top_view_id: usize) -> Self {
        Self {
            model_schema,
            active: BTreeSet::from([top_view_id]),
            cache: BTreeMap::new(),
            referenced: BTreeSet::new(),
            segment_count: 0,
            posting_bytes: 0,
        }
    }

    fn resolve(&mut self, view: &Bound<'py, PyAny>) -> CoreResult<ResolvedEncodedView<'py>> {
        let view_id = view.as_ptr() as usize;
        self.referenced.insert(view_id);
        if self.active.contains(&view_id) {
            return Err(CoreError::protocol(
                "encoded structural segment graph is cyclic",
            ));
        }
        if let Some(cached) = self.cache.get(&view_id) {
            return Ok(cached.clone());
        }
        if self.cache.len() + self.active.len() >= 256 {
            return Err(CoreError::capacity(
                "encoded structural view graph exceeds the consumer limit",
            ));
        }
        self.active.insert(view_id);
        let result = self.resolve_uncached(view, view_id);
        self.active.remove(&view_id);
        let resolved = result?;
        self.cache.insert(view_id, resolved.clone());
        Ok(resolved)
    }

    fn resolve_top(&mut self, view: &Bound<'py, PyAny>) -> CoreResult<ResolvedEncodedView<'py>> {
        let view_id = view.as_ptr() as usize;
        self.active.remove(&view_id);
        let resolved = self.resolve(view)?;
        self.referenced.remove(&view_id);
        Ok(resolved)
    }

    fn resolve_uncached(
        &mut self,
        view: &Bound<'py, PyAny>,
        view_id: usize,
    ) -> CoreResult<ResolvedEncodedView<'py>> {
        let (owner, model_schema) = validate_encoded_envelope(view)?;
        if model_schema != self.model_schema {
            return Err(CoreError::protocol(
                "referenced encoded view model schema differs from the top view",
            ));
        }
        let bindings = EncodedBufferBindings::from_view(view)?;
        let validated = validate_columns(bindings.columns()?, EncodedLimits::default())?;
        let raw_segments = required_attribute(view, "segments")?;
        let segments = raw_segments.cast::<PyTuple>().map_err(|_| {
            CoreError::protocol("encoded structural view segments must be an exact tuple")
        })?;
        if segments.is_empty() {
            return Err(CoreError::protocol(
                "encoded structural segment table must not be empty",
            ));
        }
        validate_structural_fingerprint(view, &bindings, segments)?;
        self.segment_count =
            self.segment_count
                .checked_add(u64::try_from(segments.len()).map_err(|_| {
                    CoreError::capacity("encoded structural segment count exceeds u64")
                })?)
                .ok_or_else(|| CoreError::capacity("encoded structural segment count overflow"))?;
        for index in 0..segments.len() {
            let segment = segments
                .get_item(index)
                .map_err(|_| CoreError::protocol("encoded structural segment is inaccessible"))?;
            let root_ids = EncodedBufferBinding::new(required_attribute(&segment, "root_ids")?);
            self.posting_bytes = self
                .posting_bytes
                .checked_add(root_ids.source("segment root_ids")?.len())
                .ok_or_else(|| CoreError::capacity("encoded posting byte count overflow"))?;
        }

        let first = segments
            .get_item(0)
            .map_err(|_| CoreError::protocol("encoded structural segment is inaccessible"))?;
        let first_role = exact_nonnegative_integer(
            &required_attribute(&first, "role")?,
            "encoded segment role",
        )?;
        match first_role {
            SEGMENT_DIRECT => {
                validate_direct_segment(view, &owner)?;
                Ok(ResolvedEncodedView {
                    tables: vec![EncodedCompilationTableBinding {
                        bindings,
                        view_id,
                        selection: EncodedRootPlan::All,
                        scope_map: BTreeMap::new(),
                    }],
                    local_root_count: validated.root_count,
                })
            }
            SEGMENT_OVERLAY_BASE => self.resolve_overlay(
                view,
                &owner,
                bindings,
                validated.root_count,
                segments,
                &first,
                view_id,
            ),
            SEGMENT_COMPOSITE_MEMBER => self.resolve_composite(
                view,
                &owner,
                bindings,
                validated.root_count,
                segments,
                view_id,
            ),
            _ => Err(CoreError::invalid(
                "encoded composite source has an unsupported segment family",
            )),
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn resolve_overlay(
        &mut self,
        _view: &Bound<'py, PyAny>,
        owner: &Bound<'py, PyAny>,
        bindings: EncodedBufferBindings<'py>,
        local_root_count: usize,
        segments: &Bound<'py, PyTuple>,
        base: &Bound<'py, PyAny>,
        view_id: usize,
    ) -> CoreResult<ResolvedEncodedView<'py>> {
        if !(1..=2).contains(&segments.len()) {
            return Err(CoreError::protocol(
                "encoded overlay segment family must contain a base and optional delta",
            ));
        }
        if !required_attribute(base, "member_token")?.is_none() {
            return Err(CoreError::protocol(
                "overlay base segment must not carry a member token",
            ));
        }
        let source = required_attribute(base, "source")?;
        if source.is_none() {
            return Err(CoreError::protocol(
                "overlay base segment must reference a source view",
            ));
        }
        let source_owner = required_attribute(&source, "owner")?;
        if !required_attribute(base, "owner")?.is(&source_owner) {
            return Err(CoreError::protocol(
                "overlay base segment owner differs from its source owner",
            ));
        }
        let mut resolved = self.resolve(&source)?;
        let scope_map = EncodedBufferBinding::new(required_attribute(base, "anonymous_scope_map")?);
        let scope_map = read_scope_map(scope_map.source("overlay anonymous_scope_map")?)?;
        apply_scope_map(&mut resolved.tables, &scope_map);
        let raw_mode = exact_nonnegative_integer(
            &required_attribute(base, "posting_mode")?,
            "encoded overlay posting_mode",
        )?;
        let root_ids = EncodedBufferBinding::new(required_attribute(base, "root_ids")?);
        let postings = root_ids.source("overlay base root_ids")?;
        match raw_mode {
            POSTINGS_ALL => {
                if !postings.is_empty() {
                    return Err(CoreError::protocol(
                        "ALL overlay base segment must not carry root postings",
                    ));
                }
            }
            POSTINGS_EXCLUDE => {
                let selected = read_root_postings(postings, resolved.local_root_count)?;
                apply_source_selection(
                    &mut resolved.tables,
                    source.as_ptr() as usize,
                    EncodedPostingMode::Exclude,
                    &selected,
                );
            }
            _ => {
                return Err(CoreError::protocol(
                    "overlay base segment posting mode is invalid",
                ));
            }
        }
        if segments.len() == 1 {
            if local_root_count != 0 {
                return Err(CoreError::protocol(
                    "single-segment overlay base must not carry local roots",
                ));
            }
        } else {
            let delta = segments.get_item(1).map_err(|_| {
                CoreError::protocol("encoded overlay delta segment is inaccessible")
            })?;
            validate_overlay_delta_segment(&delta, owner, local_root_count)?;
            resolved.tables.push(EncodedCompilationTableBinding {
                bindings,
                view_id,
                selection: EncodedRootPlan::All,
                scope_map: BTreeMap::new(),
            });
        }
        enforce_resolved_table_limit(&resolved.tables)?;
        Ok(ResolvedEncodedView {
            tables: resolved.tables,
            local_root_count,
        })
    }

    fn resolve_composite(
        &mut self,
        _view: &Bound<'py, PyAny>,
        owner: &Bound<'py, PyAny>,
        bindings: EncodedBufferBindings<'py>,
        local_root_count: usize,
        segments: &Bound<'py, PyTuple>,
        view_id: usize,
    ) -> CoreResult<ResolvedEncodedView<'py>> {
        let last = segments
            .get_item(segments.len() - 1)
            .map_err(|_| CoreError::protocol("encoded composite segment is inaccessible"))?;
        let has_bridge = exact_nonnegative_integer(
            &required_attribute(&last, "role")?,
            "encoded composite segment role",
        )? == SEGMENT_COMPOSITE_BRIDGE;
        let member_count = segments.len() - usize::from(has_bridge);
        if !(2..=255).contains(&member_count) {
            return Err(CoreError::capacity(
                "encoded composite member count is outside the consumer limit",
            ));
        }
        let mut tables = Vec::new();
        let mut previous_token: Option<[u8; 32]> = None;
        for index in 0..member_count {
            let segment = segments
                .get_item(index)
                .map_err(|_| CoreError::protocol("encoded composite member is inaccessible"))?;
            let role = exact_nonnegative_integer(
                &required_attribute(&segment, "role")?,
                "encoded composite member role",
            )?;
            if role != SEGMENT_COMPOSITE_MEMBER {
                return Err(CoreError::protocol(
                    "encoded composite member roles are not contiguous and canonical",
                ));
            }
            let token = exact_member_token(&segment)?;
            if previous_token.is_some_and(|previous| previous >= token) {
                return Err(CoreError::protocol(
                    "encoded composite member tokens must be sorted and unique",
                ));
            }
            previous_token = Some(token);
            let source = required_attribute(&segment, "source")?;
            if source.is_none() {
                return Err(CoreError::protocol(
                    "encoded composite member must reference a source view",
                ));
            }
            let source_owner = required_attribute(&source, "owner")?;
            if !required_attribute(&segment, "owner")?.is(&source_owner) {
                return Err(CoreError::protocol(
                    "encoded composite member owner differs from its source owner",
                ));
            }
            let mut resolved = self.resolve(&source)?;
            let scope_map =
                EncodedBufferBinding::new(required_attribute(&segment, "anonymous_scope_map")?);
            let scope_map = read_scope_map(scope_map.source("composite anonymous_scope_map")?)?;
            apply_scope_map(&mut resolved.tables, &scope_map);
            let raw_mode = exact_nonnegative_integer(
                &required_attribute(&segment, "posting_mode")?,
                "encoded composite member posting_mode",
            )?;
            let root_ids = EncodedBufferBinding::new(required_attribute(&segment, "root_ids")?);
            let postings = root_ids.source("composite member root_ids")?;
            match raw_mode {
                POSTINGS_ALL => {
                    if !postings.is_empty() {
                        return Err(CoreError::protocol(
                            "encoded composite ALL member must not carry root postings",
                        ));
                    }
                }
                POSTINGS_INCLUDE | POSTINGS_EXCLUDE => {
                    let selected = read_root_postings(postings, resolved.local_root_count)?;
                    let mode = if raw_mode == POSTINGS_INCLUDE {
                        EncodedPostingMode::Include
                    } else {
                        EncodedPostingMode::Exclude
                    };
                    apply_source_selection(
                        &mut resolved.tables,
                        source.as_ptr() as usize,
                        mode,
                        &selected,
                    );
                }
                _ => {
                    return Err(CoreError::protocol(
                        "encoded composite member posting mode is invalid",
                    ));
                }
            }
            tables.extend(resolved.tables);
            enforce_resolved_table_limit(&tables)?;
        }
        if has_bridge {
            validate_composite_bridge_segment(&last, owner, local_root_count)?;
            tables.push(EncodedCompilationTableBinding {
                bindings,
                view_id,
                selection: EncodedRootPlan::All,
                scope_map: BTreeMap::new(),
            });
        } else if local_root_count != 0 {
            return Err(CoreError::protocol(
                "encoded composite without a bridge must not carry local roots",
            ));
        }
        enforce_resolved_table_limit(&tables)?;
        Ok(ResolvedEncodedView {
            tables,
            local_root_count,
        })
    }
}

fn exact_member_token(segment: &Bound<'_, PyAny>) -> CoreResult<[u8; 32]> {
    let member_token = required_attribute(segment, "member_token")?;
    let member_token = member_token.cast::<PyBytes>().map_err(|_| {
        CoreError::protocol("encoded composite member token must be exact immutable bytes")
    })?;
    member_token
        .as_bytes()
        .try_into()
        .map_err(|_| CoreError::protocol("encoded composite member token must contain 32 bytes"))
}

fn apply_source_selection(
    tables: &mut [EncodedCompilationTableBinding<'_>],
    source_view_id: usize,
    mode: EncodedPostingMode,
    postings: &BTreeSet<u32>,
) {
    for table in tables {
        if table.view_id == source_view_id {
            table.selection.apply(mode, postings);
        } else if mode == EncodedPostingMode::Include {
            table.selection = EncodedRootPlan::Dropped;
        }
    }
}

fn enforce_resolved_table_limit(tables: &[EncodedCompilationTableBinding<'_>]) -> CoreResult<()> {
    if tables.len() > 256 {
        return Err(CoreError::capacity(
            "encoded resolved segment table count exceeds the consumer limit",
        ));
    }
    Ok(())
}

struct SessionState {
    session: Option<NativeCoreSession>,
    encoded_owner: Option<Py<PyAny>>,
    encoded_metrics: Option<EncodedIngestionMetrics>,
    failed: bool,
}

/// The only native handle exposed by the private extension.
#[pyclass(module = "pyelk._native")]
struct NativeSession {
    inner: Arc<Mutex<SessionState>>,
    creator_pid: u32,
}

impl NativeSession {
    fn ensure_creator_process(&self) -> PyResult<()> {
        let current = process::id();
        if current != self.creator_pid {
            return Err(PyRuntimeError::new_err(format!(
                "native session was created in process {} and cannot be used after fork in process {current}",
                self.creator_pid
            )));
        }
        Ok(())
    }

    fn detached<T, F>(&self, py: Python<'_>, stage: &'static str, operation: F) -> PyResult<T>
    where
        T: Send + 'static,
        F: FnOnce(&mut NativeCoreSession) -> CoreResult<T> + Send + 'static,
    {
        self.ensure_creator_process()?;
        let inner = Arc::clone(&self.inner);
        let outcome = py.detach(move || {
            let mut state = inner
                .lock()
                .map_err(|_| CoreError::Closed("native session lock is poisoned".to_owned()))?;
            if state.failed {
                return Err(CoreError::Closed(
                    "native session is permanently invalidated".to_owned(),
                ));
            }
            let session = state
                .session
                .as_mut()
                .ok_or_else(|| CoreError::Closed("native session is closed".to_owned()))?;
            match catch_unwind(AssertUnwindSafe(|| operation(session))) {
                Ok(Ok(value)) => Ok(value),
                Ok(Err(error)) => {
                    if matches!(error, CoreError::Internal(_)) {
                        state.failed = true;
                        state.session = None;
                        state.encoded_owner = None;
                    }
                    Err(error)
                }
                Err(payload) => {
                    state.failed = true;
                    state.session = None;
                    state.encoded_owner = None;
                    Err(CoreError::Internal(format!(
                        "panic during {stage}: {}",
                        panic_message(payload.as_ref())
                    )))
                }
            }
        });
        outcome.map_err(core_error)
    }

    fn packed<T, F, E>(
        &self,
        py: Python<'_>,
        stage: &'static str,
        operation: F,
        encoder: E,
    ) -> PyResult<Py<PyBytes>>
    where
        T: Send + 'static,
        F: FnOnce(&mut NativeCoreSession) -> CoreResult<T> + Send + 'static,
        E: FnOnce(&T) -> CoreResult<Vec<u8>>,
    {
        let value = self.detached(py, stage, operation)?;
        let encoded = encoder(&value).map_err(core_error)?;
        Ok(PyBytes::new(py, &encoded).unbind())
    }
}

#[pymethods]
impl NativeSession {
    fn compiler_metadata(&self, py: Python<'_>) -> PyResult<Py<PyBytes>> {
        let encoded = self.detached(py, "compiler_metadata", |session| {
            encode_compiler_metadata(session.ontology())
        })?;
        Ok(PyBytes::new(py, &encoded).unbind())
    }

    fn is_inconsistent(&self, py: Python<'_>) -> PyResult<bool> {
        self.detached(py, "is_inconsistent", NativeCoreSession::is_inconsistent)
    }

    fn class_taxonomy(&self, py: Python<'_>) -> PyResult<Py<PyBytes>> {
        self.packed(
            py,
            "class_taxonomy",
            NativeCoreSession::class_taxonomy,
            encode_taxonomy,
        )
    }

    fn object_property_taxonomy(&self, py: Python<'_>) -> PyResult<Py<PyBytes>> {
        self.packed(
            py,
            "object_property_taxonomy",
            NativeCoreSession::object_property_taxonomy,
            encode_taxonomy,
        )
    }

    fn realization(&self, py: Python<'_>) -> PyResult<Py<PyBytes>> {
        self.packed(
            py,
            "realization",
            NativeCoreSession::realization,
            encode_realization,
        )
    }

    #[pyo3(signature = (query_ir, kind, direct))]
    fn query_class_expression(
        &self,
        py: Python<'_>,
        query_ir: Option<&Bound<'_, PyBytes>>,
        kind: u8,
        direct: bool,
    ) -> PyResult<Py<PyBytes>> {
        let payload = query_ir.map(|value| value.as_bytes().to_vec());
        let query_kind = QueryKind::try_from(kind).map_err(core_error)?;
        self.packed(
            py,
            "query_class_expression",
            move |session| session.query_class_expression(payload.as_deref(), query_kind, direct),
            encode_query,
        )
    }

    fn entails(&self, py: Python<'_>, query_ir: Option<&Bound<'_, PyBytes>>) -> PyResult<bool> {
        let payload = query_ir.map(|value| value.as_bytes().to_vec());
        self.detached(py, "entails", move |session| {
            session.entails(payload.as_deref())
        })
    }

    #[pyo3(signature = (realize=false, limit=1_000_000))]
    fn debug_snapshot(&self, py: Python<'_>, realize: bool, limit: usize) -> PyResult<Py<PyBytes>> {
        self.packed(
            py,
            "debug_snapshot",
            move |session| session.debug_snapshot(realize, limit),
            |value| Ok(value.clone()),
        )
    }

    fn diagnostics(&self, py: Python<'_>) -> PyResult<Py<PyDict>> {
        let values = self.detached(py, "diagnostics", |session| Ok(session.diagnostics()))?;
        let encoded_metrics = self
            .inner
            .lock()
            .map_err(|_| PyRuntimeError::new_err("native session lock is poisoned"))?
            .encoded_metrics;
        let result = PyDict::new(py);
        for (key, value) in values {
            match value {
                DiagnosticValue::Integer(item) => result.set_item(key, item)?,
                DiagnosticValue::Boolean(item) => result.set_item(key, item)?,
                DiagnosticValue::Text(item) => result.set_item(key, item)?,
            }
        }
        if let Some(metrics) = encoded_metrics {
            result.set_item("encoded_buffer_count", metrics.buffer_count)?;
            result.set_item("encoded_buffer_bytes", metrics.buffer_bytes)?;
            result.set_item("encoded_zero_copy_buffers", metrics.zero_copy_buffers)?;
            result.set_item(
                "encoded_compiler_gil_released",
                metrics.compiler_gil_released,
            )?;
            result.set_item("encoded_segment_count", metrics.segment_count)?;
            result.set_item(
                "encoded_referenced_view_count",
                metrics.referenced_view_count,
            )?;
            result.set_item("encoded_posting_bytes", metrics.posting_bytes)?;
            result.set_item(
                "encoded_indexed_buffer_count",
                metrics.buffer_count - metrics.zero_copy_buffers,
            )?;
            result.set_item("encoded_staging_copy_bytes", 0)?;
            result.set_item("encoded_private_ir_bytes", 0)?;
        }
        Ok(result.unbind())
    }

    fn close(&self) -> PyResult<()> {
        self.ensure_creator_process()?;
        let mut state = self
            .inner
            .lock()
            .map_err(|_| PyRuntimeError::new_err("native session lock is poisoned"))?;
        state.session = None;
        state.encoded_owner = None;
        Ok(())
    }
}

impl Drop for NativeSession {
    fn drop(&mut self) {
        if process::id() == self.creator_pid {
            return;
        }
        // Rayon workers and inherited Python owners belong to the parent process.  Dropping either
        // after fork can join vanished threads or touch parent-interpreter state, so abandon the
        // inherited allocation in the child.  The operating system reclaims it when that child
        // exits; all child-side methods reject before locking this state.
        let inherited = std::mem::replace(
            &mut self.inner,
            Arc::new(Mutex::new(SessionState {
                session: None,
                encoded_owner: None,
                encoded_metrics: None,
                failed: true,
            })),
        );
        std::mem::forget(inherited);
    }
}

#[pyfunction]
fn implementation_version() -> &'static str {
    IMPLEMENTATION_VERSION
}

#[pyfunction]
fn ir_version() -> (u16, u16) {
    (IR_MAJOR, IR_MINOR)
}

#[pyfunction]
fn abi_version() -> &'static str {
    "abi3-py310"
}

#[pyfunction]
fn self_check() -> bool {
    IR_MAJOR == 1 && IR_MINOR == 0 && !IMPLEMENTATION_VERSION.is_empty()
}

/// Encoded structural schemas compiled into this extension.
///
/// The registration seam intentionally advertises nothing until the generated pyowl-core
/// schema ledger and the complete structural compiler are present.  Python therefore keeps
/// using scalar-wire ingestion rather than inferring support from this function's existence.
#[pyfunction]
fn encoded_view_schemas(py: Python<'_>) -> Py<PyDict> {
    PyDict::new(py).unbind()
}

/// Coarse encoded-view compiler entry point retained behind absent capability advertising.
///
/// This executable handoff accepts validated direct, recursively segmented overlay, and
/// composite sources, including anonymous-scope remapping, without flattening them.
/// Mmap-lifetime, exhaustive-constructor, and performance gates remain release blockers, so
/// `encoded_view_schemas()` intentionally stays empty.
#[pyfunction]
fn create_session_from_encoded(
    py: Python<'_>,
    encoded_view: &Bound<'_, PyAny>,
    workers: isize,
    unsupported: &str,
) -> PyResult<NativeSession> {
    if workers < 0 {
        return Err(PyValueError::new_err(
            "workers must be a nonnegative integer",
        ));
    }
    if !matches!(unsupported, "ignore" | "error") {
        return Err(PyValueError::new_err(
            "unsupported must be 'ignore' or 'error'",
        ));
    }
    let worker_count = usize::try_from(workers)
        .map_err(|_| PyValueError::new_err("workers does not fit usize"))?;
    let policy = match unsupported {
        "ignore" => EncodedUnsupportedPolicy::Ignore,
        "error" => EncodedUnsupportedPolicy::Error,
        _ => unreachable!("unsupported policy was validated above"),
    };
    let outcome = catch_unwind(AssertUnwindSafe(|| {
        let input = validate_encoded_input(encoded_view)?;
        let detached_simple = input.detached_simple()?;
        let (mut compilation, metrics) = if let Some(detached) = detached_simple {
            let metric_columns = [
                Some(input.bindings.columns()?),
                input
                    .delta_bindings
                    .as_ref()
                    .map(EncodedBufferBindings::columns)
                    .transpose()?,
            ]
            .into_iter()
            .flatten()
            .collect::<Vec<_>>();
            let metrics = encoded_ingestion_metrics(
                &metric_columns,
                input.segment_count,
                input.referenced_view_count,
                detached.posting.map_or(0, |(_mode, values)| values.len()),
            )?;
            let metrics = EncodedIngestionMetrics {
                compiler_gil_released: true,
                ..metrics
            };
            let compilation =
                py.detach(move || match (detached.delta_columns, detached.posting) {
                    (Some(delta), Some((mode, postings))) => {
                        compile_encoded_overlay_delta_selected_with_policy(
                            detached.columns,
                            delta,
                            EncodedLimits::default(),
                            policy,
                            mode,
                            postings,
                        )
                    }
                    (Some(delta), None) => compile_encoded_overlay_delta_with_policy(
                        detached.columns,
                        delta,
                        EncodedLimits::default(),
                        policy,
                    ),
                    (None, Some((mode, postings))) => {
                        compile_encoded_hierarchy_selected_with_policy(
                            detached.columns,
                            EncodedLimits::default(),
                            policy,
                            mode,
                            postings,
                        )
                    }
                    (None, None) => compile_encoded_hierarchy_with_policy(
                        detached.columns,
                        EncodedLimits::default(),
                        policy,
                    ),
                })?;
            (compilation, metrics)
        } else if let Some(composite) = &input.composite_bindings {
            let mut posting_storage = Vec::new();
            posting_storage
                .try_reserve_exact(composite.len())
                .map_err(|_| CoreError::capacity("encoded posting storage allocation failed"))?;
            for table in composite {
                posting_storage.push(table.selection.posting_bytes()?);
            }
            let mut scope_storage = Vec::new();
            scope_storage
                .try_reserve_exact(composite.len())
                .map_err(|_| CoreError::capacity("encoded scope storage allocation failed"))?;
            for table in composite {
                scope_storage.push(encoded_scope_map(&table.scope_map)?);
            }
            let mut metric_columns = Vec::new();
            metric_columns
                .try_reserve_exact(composite.len())
                .map_err(|_| CoreError::capacity("encoded composite metric allocation failed"))?;
            let mut metric_views = BTreeSet::new();
            for table in composite {
                let columns = table.bindings.columns()?;
                if metric_views.insert(table.view_id) {
                    metric_columns.push(columns);
                }
            }
            let mut metrics = encoded_ingestion_metrics(
                &metric_columns,
                input.segment_count,
                input.referenced_view_count,
                input.composite_posting_bytes,
            )?;
            let mut detached_columns = Vec::new();
            detached_columns
                .try_reserve_exact(composite.len())
                .map_err(|_| CoreError::capacity("encoded detached composite allocation failed"))?;
            let mut all_detached = true;
            for table in composite {
                let Some(columns) = table.bindings.detached_columns()? else {
                    all_detached = false;
                    break;
                };
                detached_columns.push(columns);
            }
            let compilation = if all_detached {
                let mut tables = Vec::new();
                tables.try_reserve_exact(composite.len()).map_err(|_| {
                    CoreError::capacity("encoded detached composite table allocation failed")
                })?;
                for (((table, columns), postings), scope_map) in composite
                    .iter()
                    .zip(&detached_columns)
                    .zip(&posting_storage)
                    .zip(&scope_storage)
                {
                    if matches!(table.selection, EncodedRootPlan::Dropped) {
                        continue;
                    }
                    tables.push(EncodedCompilationSegment {
                        columns: *columns,
                        posting_mode: table.selection.mode(),
                        postings: postings.as_slice(),
                        anonymous_scope_map: scope_map.as_slice(),
                    });
                }
                metrics.compiler_gil_released = true;
                py.detach(move || {
                    compile_encoded_segments_with_policy(&tables, EncodedLimits::default(), policy)
                })?
            } else {
                let mut tables = Vec::new();
                tables.try_reserve_exact(composite.len()).map_err(|_| {
                    CoreError::capacity("encoded composite table allocation failed")
                })?;
                for ((table, postings), scope_map) in
                    composite.iter().zip(&posting_storage).zip(&scope_storage)
                {
                    if matches!(table.selection, EncodedRootPlan::Dropped) {
                        continue;
                    }
                    tables.push(EncodedCompilationSegment {
                        columns: table.bindings.columns()?,
                        posting_mode: table.selection.mode(),
                        postings: postings.as_slice(),
                        anonymous_scope_map: scope_map.as_slice(),
                    });
                }
                compile_encoded_segments_with_policy(&tables, EncodedLimits::default(), policy)?
            };
            (compilation, metrics)
        } else {
            let columns = input.bindings.columns()?;
            let delta_columns = input
                .delta_bindings
                .as_ref()
                .map(EncodedBufferBindings::columns)
                .transpose()?;
            let posting = input
                .posting
                .as_ref()
                .map(|binding| binding.root_ids.source("segment root_ids"))
                .transpose()?;
            let metric_columns = [Some(columns), delta_columns]
                .into_iter()
                .flatten()
                .collect::<Vec<_>>();
            let metrics = encoded_ingestion_metrics(
                &metric_columns,
                input.segment_count,
                input.referenced_view_count,
                posting.map_or(0, ByteSource::len),
            )?;
            let compilation = match (delta_columns, &input.posting, posting) {
                (Some(delta), Some(binding), Some(root_ids)) => {
                    compile_encoded_overlay_delta_selected_with_policy(
                        columns,
                        delta,
                        EncodedLimits::default(),
                        policy,
                        binding.mode,
                        root_ids,
                    )?
                }
                (Some(delta), None, None) => compile_encoded_overlay_delta_with_policy(
                    columns,
                    delta,
                    EncodedLimits::default(),
                    policy,
                )?,
                (None, Some(binding), Some(root_ids)) => {
                    compile_encoded_hierarchy_selected_with_policy(
                        columns,
                        EncodedLimits::default(),
                        policy,
                        binding.mode,
                        root_ids,
                    )?
                }
                (None, None, None) => compile_encoded_hierarchy_with_policy(
                    columns,
                    EncodedLimits::default(),
                    policy,
                )?,
                _ => {
                    return Err(CoreError::internal(
                        "validated encoded posting and delta bindings diverged",
                    ));
                }
            };
            (compilation, metrics)
        };
        let compatibility_spelling =
            py.detach(|| compatibility_spelling_digest(&compilation.compatibility_observations))?;
        compilation.ontology.source_fingerprint = encoded_source_fingerprint(
            py,
            &input.source_parts.logical,
            &input.source_parts.signature,
            unsupported,
            input.source_parts.model_schema,
            &compatibility_spelling,
        )?;
        let ontology = compilation.ontology;
        let session =
            py.detach(move || NativeCoreSession::from_ontology(ontology, worker_count))?;
        Ok((session, metrics))
    }));
    let (session, metrics) = match outcome {
        Ok(value) => value.map_err(core_error)?,
        Err(payload) => {
            return Err(PyRuntimeError::new_err(format!(
                "panic during create_session_from_encoded: {}",
                panic_message(payload.as_ref())
            )));
        }
    };
    Ok(NativeSession {
        inner: Arc::new(Mutex::new(SessionState {
            session: Some(session),
            encoded_owner: Some(encoded_view.clone().unbind()),
            encoded_metrics: Some(metrics),
            failed: false,
        })),
        creator_pid: process::id(),
    })
}

impl<'py> EncodedBufferBindings<'py> {
    fn from_view(encoded_view: &Bound<'py, PyAny>) -> CoreResult<Self> {
        let raw_buffers = required_attribute(encoded_view, "buffers")?;
        let buffers = raw_buffers.cast::<PyMapping>().map_err(|_| {
            CoreError::protocol("encoded view buffers must implement collections.abc.Mapping")
        })?;
        if buffers
            .len()
            .map_err(|_| CoreError::protocol("encoded view buffers has invalid length"))?
            != ENCODED_BUFFER_COUNT
        {
            return Err(CoreError::protocol(
                "encoded view buffer set must contain exactly eleven schema columns",
            ));
        }
        let get = |name: &'static str| {
            buffers
                .get_item(name)
                .map_err(|_| CoreError::protocol(format!("encoded view is missing buffer {name}")))
        };
        Ok(Self {
            root_kinds: EncodedBufferBinding::new(get("root_kinds")?),
            root_ids: EncodedBufferBinding::new(get("root_ids")?),
            node_tags: EncodedBufferBinding::new(get("node_tags")?),
            node_field_offsets: EncodedBufferBinding::new(get("node_field_offsets")?),
            field_kinds: EncodedBufferBinding::new(get("field_kinds")?),
            field_values: EncodedBufferBinding::new(get("field_values")?),
            field_lengths: EncodedBufferBinding::new(get("field_lengths")?),
            item_kinds: EncodedBufferBinding::new(get("item_kinds")?),
            item_values: EncodedBufferBinding::new(get("item_values")?),
            item_lengths: EncodedBufferBinding::new(get("item_lengths")?),
            scalar_bytes: EncodedBufferBinding::new(get("scalar_bytes")?),
        })
    }

    fn columns(&self) -> CoreResult<EncodedColumns<BorrowedPyBytes<'_, 'py>>> {
        Ok(EncodedColumns {
            root_kinds: self.root_kinds.source("root_kinds")?,
            root_ids: self.root_ids.source("root_ids")?,
            node_tags: self.node_tags.source("node_tags")?,
            node_field_offsets: self.node_field_offsets.source("node_field_offsets")?,
            field_kinds: self.field_kinds.source("field_kinds")?,
            field_values: self.field_values.source("field_values")?,
            field_lengths: self.field_lengths.source("field_lengths")?,
            item_kinds: self.item_kinds.source("item_kinds")?,
            item_values: self.item_values.source("item_values")?,
            item_lengths: self.item_lengths.source("item_lengths")?,
            scalar_bytes: self.scalar_bytes.source("scalar_bytes")?,
        })
    }

    fn detached_columns(&self) -> CoreResult<Option<EncodedColumns<&[u8]>>> {
        let columns = self.columns()?;
        let Some(root_kinds) = columns.root_kinds.bytes else {
            return Ok(None);
        };
        let Some(root_ids) = columns.root_ids.bytes else {
            return Ok(None);
        };
        let Some(node_tags) = columns.node_tags.bytes else {
            return Ok(None);
        };
        let Some(node_field_offsets) = columns.node_field_offsets.bytes else {
            return Ok(None);
        };
        let Some(field_kinds) = columns.field_kinds.bytes else {
            return Ok(None);
        };
        let Some(field_values) = columns.field_values.bytes else {
            return Ok(None);
        };
        let Some(field_lengths) = columns.field_lengths.bytes else {
            return Ok(None);
        };
        let Some(item_kinds) = columns.item_kinds.bytes else {
            return Ok(None);
        };
        let Some(item_values) = columns.item_values.bytes else {
            return Ok(None);
        };
        let Some(item_lengths) = columns.item_lengths.bytes else {
            return Ok(None);
        };
        let Some(scalar_bytes) = columns.scalar_bytes.bytes else {
            return Ok(None);
        };
        Ok(Some(EncodedColumns {
            root_kinds,
            root_ids,
            node_tags,
            node_field_offsets,
            field_kinds,
            field_values,
            field_lengths,
            item_kinds,
            item_values,
            item_lengths,
            scalar_bytes,
        }))
    }
}

impl<'py> EncodedBufferBinding<'py> {
    fn new(view: Bound<'py, PyAny>) -> Self {
        let bytes_owner = view
            .getattr("obj")
            .ok()
            .and_then(|owner| owner.cast_into::<PyBytes>().ok());
        Self { view, bytes_owner }
    }

    fn source(&self, name: &str) -> CoreResult<BorrowedPyBytes<'_, 'py>> {
        borrowed_py_bytes(&self.view, self.bytes_owner.as_ref(), name)
    }
}

impl ValidatedEncodedInput<'_> {
    fn detached_simple(&self) -> CoreResult<Option<DetachedSimpleInput<'_>>> {
        if self.composite_bindings.is_some() {
            return Ok(None);
        }
        let Some(columns) = self.bindings.detached_columns()? else {
            return Ok(None);
        };
        let delta_columns = match &self.delta_bindings {
            Some(bindings) => {
                let Some(columns) = bindings.detached_columns()? else {
                    return Ok(None);
                };
                Some(columns)
            }
            None => None,
        };
        let posting = match &self.posting {
            Some(binding) => {
                let source = binding.root_ids.source("segment root_ids")?;
                let Some(bytes) = source.bytes else {
                    return Ok(None);
                };
                Some((binding.mode, bytes))
            }
            None => None,
        };
        Ok(Some(DetachedSimpleInput {
            columns,
            delta_columns,
            posting,
        }))
    }
}

fn encoded_ingestion_metrics(
    column_tables: &[EncodedColumns<BorrowedPyBytes<'_, '_>>],
    segment_count: u64,
    referenced_view_count: u64,
    posting_bytes: usize,
) -> CoreResult<EncodedIngestionMetrics> {
    let mut buffer_bytes = 0_u64;
    let mut zero_copy_buffers = 0_u64;
    let mut buffer_count = 0_u64;
    for columns in column_tables.iter().copied() {
        let buffers = [
            columns.root_kinds,
            columns.root_ids,
            columns.node_tags,
            columns.node_field_offsets,
            columns.field_kinds,
            columns.field_values,
            columns.field_lengths,
            columns.item_kinds,
            columns.item_values,
            columns.item_lengths,
            columns.scalar_bytes,
        ];
        for buffer in buffers {
            buffer_bytes = buffer_bytes
                .checked_add(
                    u64::try_from(buffer.len())
                        .map_err(|_| CoreError::capacity("encoded buffer length exceeds u64"))?,
                )
                .ok_or_else(|| CoreError::capacity("encoded buffer byte total exceeds u64"))?;
            zero_copy_buffers += u64::from(buffer.bytes.is_some());
            buffer_count = buffer_count
                .checked_add(1)
                .ok_or_else(|| CoreError::capacity("encoded buffer count overflow"))?;
        }
    }
    Ok(EncodedIngestionMetrics {
        buffer_count,
        buffer_bytes,
        zero_copy_buffers,
        compiler_gil_released: false,
        segment_count,
        referenced_view_count,
        posting_bytes: u64::try_from(posting_bytes)
            .map_err(|_| CoreError::capacity("encoded posting byte count exceeds u64"))?,
    })
}

fn validate_encoded_input<'py>(
    encoded_view: &Bound<'py, PyAny>,
) -> CoreResult<ValidatedEncodedInput<'py>> {
    let (top_owner, model_schema) = validate_encoded_envelope(encoded_view)?;
    let logical = read_fingerprint(&top_owner, "logical_fingerprint")?;
    let signature = read_fingerprint(&top_owner, "signature_fingerprint")?;
    let source_parts = EncodedSourceParts {
        logical,
        signature,
        model_schema,
    };

    let mut current = encoded_view.clone();
    let mut seen = BTreeSet::new();
    let mut segment_count = 0_u64;
    let mut referenced_view_count = 0_u64;
    let mut posting = None;
    let mut delta_bindings = None;
    loop {
        if !seen.insert(current.as_ptr() as usize) {
            return Err(CoreError::protocol(
                "encoded structural segment graph is cyclic",
            ));
        }
        if seen.len() > 256 {
            return Err(CoreError::capacity(
                "encoded structural overlay depth exceeds the consumer limit",
            ));
        }
        let (owner, current_model_schema) = validate_encoded_envelope(&current)?;
        if current_model_schema != model_schema {
            return Err(CoreError::protocol(
                "referenced encoded view model schema differs from the top view",
            ));
        }
        let bindings = EncodedBufferBindings::from_view(&current)?;
        let validated = validate_columns(bindings.columns()?, EncodedLimits::default())?;
        let raw_segments = required_attribute(&current, "segments")?;
        let segments = raw_segments.cast::<PyTuple>().map_err(|_| {
            CoreError::protocol("encoded structural view segments must be an exact tuple")
        })?;
        validate_structural_fingerprint(&current, &bindings, segments)?;
        segment_count =
            segment_count
                .checked_add(u64::try_from(segments.len()).map_err(|_| {
                    CoreError::capacity("encoded structural segment count exceeds u64")
                })?)
                .ok_or_else(|| CoreError::capacity("encoded structural segment count overflow"))?;

        if segments.is_empty() {
            return Err(CoreError::protocol(
                "encoded structural segment table must not be empty",
            ));
        }
        let first_segment = segments
            .get_item(0)
            .map_err(|_| CoreError::protocol("encoded structural segment is inaccessible"))?;
        let first_role = exact_nonnegative_integer(
            &required_attribute(&first_segment, "role")?,
            "encoded segment role",
        )?;
        if first_role == SEGMENT_COMPOSITE_MEMBER {
            if seen.len() != 1 || posting.is_some() || delta_bindings.is_some() {
                return validate_recursive_encoded_input(encoded_view, source_parts, model_schema);
            }
            return validate_composite_input(
                &current,
                &owner,
                bindings,
                validated.root_count,
                segments,
                source_parts,
                segment_count,
                model_schema,
            );
        }
        if !(1..=2).contains(&segments.len()) {
            return Err(CoreError::invalid(
                "encoded compiler slice currently accepts direct or base-plus-delta overlay segments",
            ));
        }
        let has_delta = segments.len() == 2;
        let mut local_bindings = Some(bindings);
        if has_delta {
            if delta_bindings.is_some() {
                return validate_recursive_encoded_input(encoded_view, source_parts, model_schema);
            }
            let delta = segments.get_item(1).map_err(|_| {
                CoreError::protocol("encoded overlay delta segment is inaccessible")
            })?;
            validate_overlay_delta_segment(&delta, &owner, validated.root_count)?;
            delta_bindings = local_bindings.take();
        }
        let segment = first_segment;
        let role = exact_nonnegative_integer(
            &required_attribute(&segment, "role")?,
            "encoded segment role",
        )?;
        let posting_mode = exact_nonnegative_integer(
            &required_attribute(&segment, "posting_mode")?,
            "encoded segment posting_mode",
        )?;
        match (role, posting_mode) {
            (SEGMENT_DIRECT, POSTINGS_ALL) => {
                if has_delta {
                    return Err(CoreError::protocol(
                        "encoded overlay delta must follow an overlay base segment",
                    ));
                }
                validate_direct_segment(&current, &owner)?;
                return Ok(ValidatedEncodedInput {
                    bindings: local_bindings.ok_or_else(|| {
                        CoreError::internal("direct encoded bindings were unexpectedly moved")
                    })?,
                    delta_bindings,
                    source_parts,
                    segment_count,
                    referenced_view_count,
                    posting,
                    composite_bindings: None,
                    composite_posting_bytes: 0,
                });
            }
            (SEGMENT_OVERLAY_BASE, mode @ (POSTINGS_ALL | POSTINGS_EXCLUDE)) => {
                if posting.is_some() {
                    return validate_recursive_encoded_input(
                        encoded_view,
                        source_parts,
                        model_schema,
                    );
                }
                if !has_delta && validated.root_count != 0 {
                    return Err(CoreError::protocol(
                        "single-segment overlay base must not carry local roots",
                    ));
                }
                let scope_map =
                    EncodedBufferBinding::new(required_attribute(&segment, "anonymous_scope_map")?);
                if !scope_map.source("overlay anonymous_scope_map")?.is_empty() {
                    return validate_recursive_encoded_input(
                        encoded_view,
                        source_parts,
                        model_schema,
                    );
                }
                if !required_attribute(&segment, "member_token")?.is_none() {
                    return Err(CoreError::protocol(
                        "overlay base segment must not carry a member token",
                    ));
                }
                let source = required_attribute(&segment, "source")?;
                if source.is_none() {
                    return Err(CoreError::protocol(
                        "overlay base segment must reference a source view",
                    ));
                }
                let source_owner = required_attribute(&source, "owner")?;
                if !required_attribute(&segment, "owner")?.is(&source_owner) {
                    return Err(CoreError::protocol(
                        "overlay base segment owner differs from its source owner",
                    ));
                }
                referenced_view_count = referenced_view_count
                    .checked_add(1)
                    .ok_or_else(|| CoreError::capacity("encoded referenced-view count overflow"))?;
                let root_ids = required_attribute(&segment, "root_ids")?;
                let root_ids_binding = EncodedBufferBinding::new(root_ids);
                let root_ids_source = root_ids_binding.source("segment root_ids")?;
                if mode == POSTINGS_ALL {
                    if !root_ids_source.is_empty() {
                        return Err(CoreError::protocol(
                            "ALL overlay base segment must not carry root postings",
                        ));
                    }
                } else {
                    if root_ids_source.is_empty() {
                        return Err(CoreError::protocol(
                            "EXCLUDE overlay base segment requires root postings",
                        ));
                    }
                    posting = Some(EncodedPostingBinding {
                        mode: EncodedPostingMode::Exclude,
                        root_ids: root_ids_binding,
                    });
                }
                current = source;
            }
            _ => {
                return Err(CoreError::invalid(
                    "encoded compiler slice currently accepts direct or base-plus-delta overlay segments",
                ));
            }
        }
    }
}

fn validate_recursive_encoded_input<'py>(
    encoded_view: &Bound<'py, PyAny>,
    source_parts: EncodedSourceParts,
    model_schema: u64,
) -> CoreResult<ValidatedEncodedInput<'py>> {
    let top_bindings = EncodedBufferBindings::from_view(encoded_view)?;
    let mut resolver = CompositeResolver::new(model_schema, encoded_view.as_ptr() as usize);
    let resolved = resolver.resolve_top(encoded_view)?;
    enforce_resolved_table_limit(&resolved.tables)?;
    Ok(ValidatedEncodedInput {
        bindings: top_bindings,
        delta_bindings: None,
        source_parts,
        segment_count: resolver.segment_count,
        referenced_view_count: u64::try_from(resolver.referenced.len())
            .map_err(|_| CoreError::capacity("encoded reference count exceeds u64"))?,
        posting: None,
        composite_bindings: Some(resolved.tables),
        composite_posting_bytes: resolver.posting_bytes,
    })
}

#[allow(clippy::too_many_arguments)]
fn validate_composite_input<'py>(
    encoded_view: &Bound<'py, PyAny>,
    top_owner: &Bound<'py, PyAny>,
    top_bindings: EncodedBufferBindings<'py>,
    local_root_count: usize,
    segments: &Bound<'py, PyTuple>,
    source_parts: EncodedSourceParts,
    segment_count: u64,
    model_schema: u64,
) -> CoreResult<ValidatedEncodedInput<'py>> {
    let last = segments
        .get_item(segments.len() - 1)
        .map_err(|_| CoreError::protocol("encoded composite segment is inaccessible"))?;
    let has_bridge = exact_nonnegative_integer(
        &required_attribute(&last, "role")?,
        "encoded composite segment role",
    )? == SEGMENT_COMPOSITE_BRIDGE;
    let member_count = segments.len() - usize::from(has_bridge);
    if member_count < 2 {
        return Err(CoreError::protocol(
            "encoded composite requires at least two member segments",
        ));
    }
    if member_count > 255 {
        return Err(CoreError::capacity(
            "encoded composite member count exceeds the consumer limit",
        ));
    }

    let mut compiled = Vec::new();
    compiled
        .try_reserve_exact(member_count + usize::from(has_bridge))
        .map_err(|_| CoreError::capacity("encoded composite binding allocation failed"))?;
    let mut previous_token: Option<[u8; 32]> = None;
    let mut composite_posting_bytes = 0_usize;
    let mut resolver = CompositeResolver::new(model_schema, encoded_view.as_ptr() as usize);
    for index in 0..member_count {
        let segment = segments
            .get_item(index)
            .map_err(|_| CoreError::protocol("encoded composite member is inaccessible"))?;
        let role = exact_nonnegative_integer(
            &required_attribute(&segment, "role")?,
            "encoded composite member role",
        )?;
        if role != SEGMENT_COMPOSITE_MEMBER {
            return Err(CoreError::protocol(
                "encoded composite member roles are not contiguous and canonical",
            ));
        }
        let scope_map =
            EncodedBufferBinding::new(required_attribute(&segment, "anonymous_scope_map")?);
        let scope_map = read_scope_map(scope_map.source("composite anonymous_scope_map")?)?;
        let member_token = exact_member_token(&segment)?;
        if previous_token.is_some_and(|previous| previous >= member_token) {
            return Err(CoreError::protocol(
                "encoded composite member tokens must be sorted and unique",
            ));
        }
        previous_token = Some(member_token);

        let source = required_attribute(&segment, "source")?;
        if source.is_none() {
            return Err(CoreError::protocol(
                "encoded composite member must reference a source view",
            ));
        }
        let source_owner = required_attribute(&source, "owner")?;
        if !required_attribute(&segment, "owner")?.is(&source_owner) {
            return Err(CoreError::protocol(
                "encoded composite member owner differs from its source owner",
            ));
        }
        let mut resolved = resolver.resolve(&source)?;
        apply_scope_map(&mut resolved.tables, &scope_map);

        let raw_mode = exact_nonnegative_integer(
            &required_attribute(&segment, "posting_mode")?,
            "encoded composite member posting_mode",
        )?;
        let root_ids = EncodedBufferBinding::new(required_attribute(&segment, "root_ids")?);
        let postings = root_ids.source("composite member root_ids")?;
        composite_posting_bytes = composite_posting_bytes
            .checked_add(postings.len())
            .ok_or_else(|| CoreError::capacity("encoded composite posting byte count overflow"))?;
        match raw_mode {
            POSTINGS_ALL => {
                if !postings.is_empty() {
                    return Err(CoreError::protocol(
                        "encoded composite ALL member must not carry root postings",
                    ));
                }
            }
            POSTINGS_INCLUDE | POSTINGS_EXCLUDE => {
                let selected = read_root_postings(postings, resolved.local_root_count)?;
                let mode = if raw_mode == POSTINGS_INCLUDE {
                    EncodedPostingMode::Include
                } else {
                    EncodedPostingMode::Exclude
                };
                apply_source_selection(
                    &mut resolved.tables,
                    source.as_ptr() as usize,
                    mode,
                    &selected,
                );
            }
            _ => {
                return Err(CoreError::protocol(
                    "encoded composite member posting mode is invalid",
                ));
            }
        }
        compiled.extend(resolved.tables);
        enforce_resolved_table_limit(&compiled)?;
    }

    if has_bridge {
        validate_composite_bridge_segment(&last, top_owner, local_root_count)?;
        compiled.push(EncodedCompilationTableBinding {
            bindings: top_bindings.clone(),
            view_id: encoded_view.as_ptr() as usize,
            selection: EncodedRootPlan::All,
            scope_map: BTreeMap::new(),
        });
    } else if local_root_count != 0 {
        return Err(CoreError::protocol(
            "encoded composite without a bridge must not carry local roots",
        ));
    }
    enforce_resolved_table_limit(&compiled)?;
    let segment_count = segment_count
        .checked_add(resolver.segment_count)
        .ok_or_else(|| CoreError::capacity("encoded composite segment count overflow"))?;
    composite_posting_bytes = composite_posting_bytes
        .checked_add(resolver.posting_bytes)
        .ok_or_else(|| CoreError::capacity("encoded composite posting byte count overflow"))?;

    Ok(ValidatedEncodedInput {
        bindings: if has_bridge {
            EncodedBufferBindings::from_view(encoded_view)?
        } else {
            top_bindings
        },
        delta_bindings: None,
        source_parts,
        segment_count,
        referenced_view_count: u64::try_from(resolver.referenced.len())
            .map_err(|_| CoreError::capacity("encoded composite reference count exceeds u64"))?,
        posting: None,
        composite_bindings: Some(compiled),
        composite_posting_bytes,
    })
}

fn read_scope_map(scope_map: BorrowedPyBytes<'_, '_>) -> CoreResult<BTreeMap<[u8; 32], [u8; 32]>> {
    if scope_map.len() % 64 != 0 {
        return Err(CoreError::protocol(
            "encoded anonymous scope map must contain exact 64-byte rows",
        ));
    }
    let mut mappings = BTreeMap::new();
    let mut previous = None;
    for row in 0..scope_map.len() / 64 {
        let start = row
            .checked_mul(64)
            .ok_or_else(|| CoreError::capacity("encoded scope-map offset overflow"))?;
        let source = borrowed_fixed_bytes_32(scope_map, start, "scope-map source")?;
        let target = borrowed_fixed_bytes_32(
            scope_map,
            start
                .checked_add(32)
                .ok_or_else(|| CoreError::capacity("encoded scope-map target overflow"))?,
            "scope-map target",
        )?;
        if previous.is_some_and(|value| value >= source) || source == target {
            return Err(CoreError::protocol(
                "encoded scope-map sources must be sorted, unique, and nonidentity",
            ));
        }
        previous = Some(source);
        mappings.insert(source, target);
    }
    Ok(mappings)
}

fn borrowed_fixed_bytes_32(
    source: BorrowedPyBytes<'_, '_>,
    start: usize,
    name: &str,
) -> CoreResult<[u8; 32]> {
    let mut value = [0_u8; 32];
    for (offset, byte) in value.iter_mut().enumerate() {
        *byte = source
            .byte(
                start
                    .checked_add(offset)
                    .ok_or_else(|| CoreError::capacity(format!("encoded {name} overflow")))?,
            )
            .ok_or_else(|| CoreError::protocol(format!("encoded {name} is truncated")))?;
    }
    Ok(value)
}

fn apply_scope_map(
    tables: &mut [EncodedCompilationTableBinding<'_>],
    incoming: &BTreeMap<[u8; 32], [u8; 32]>,
) {
    if incoming.is_empty() {
        return;
    }
    for table in tables {
        let keys = table
            .scope_map
            .keys()
            .chain(incoming.keys())
            .copied()
            .collect::<BTreeSet<_>>();
        let mut composed = BTreeMap::new();
        for source in keys {
            let intermediate = table.scope_map.get(&source).copied().unwrap_or(source);
            let target = incoming.get(&intermediate).copied().unwrap_or(intermediate);
            if source != target {
                composed.insert(source, target);
            }
        }
        table.scope_map = composed;
    }
}

fn read_root_postings(
    postings: BorrowedPyBytes<'_, '_>,
    root_count: usize,
) -> CoreResult<BTreeSet<u32>> {
    if postings.is_empty() || postings.len() % 4 != 0 {
        return Err(CoreError::protocol(
            "encoded composite root postings must be nonempty u32 rows",
        ));
    }
    let mut previous = 0_usize;
    let mut selected = BTreeSet::new();
    for row in 0..postings.len() / 4 {
        let offset = row
            .checked_mul(4)
            .ok_or_else(|| CoreError::capacity("encoded composite posting offset overflow"))?;
        let bytes = [
            postings
                .byte(offset)
                .ok_or_else(|| CoreError::protocol("encoded composite posting is inaccessible"))?,
            postings
                .byte(offset + 1)
                .ok_or_else(|| CoreError::protocol("encoded composite posting is inaccessible"))?,
            postings
                .byte(offset + 2)
                .ok_or_else(|| CoreError::protocol("encoded composite posting is inaccessible"))?,
            postings
                .byte(offset + 3)
                .ok_or_else(|| CoreError::protocol("encoded composite posting is inaccessible"))?,
        ];
        let value = usize::try_from(u32::from_le_bytes(bytes))
            .map_err(|_| CoreError::capacity("encoded composite posting exceeds usize"))?;
        if value <= previous || value > root_count {
            return Err(CoreError::protocol(
                "encoded composite root postings must be sorted, unique, and in range",
            ));
        }
        previous = value;
        selected.insert(
            u32::try_from(value)
                .map_err(|_| CoreError::capacity("encoded composite posting exceeds u32"))?,
        );
    }
    Ok(selected)
}

fn validate_composite_bridge_segment(
    segment: &Bound<'_, PyAny>,
    top_owner: &Bound<'_, PyAny>,
    local_root_count: usize,
) -> CoreResult<()> {
    let role = exact_nonnegative_integer(
        &required_attribute(segment, "role")?,
        "encoded composite bridge role",
    )?;
    let posting_mode = exact_nonnegative_integer(
        &required_attribute(segment, "posting_mode")?,
        "encoded composite bridge posting_mode",
    )?;
    if role != SEGMENT_COMPOSITE_BRIDGE || posting_mode != POSTINGS_ALL {
        return Err(CoreError::protocol(
            "encoded composite bridge role or posting mode is invalid",
        ));
    }
    if !required_attribute(segment, "owner")?.is(top_owner)
        || !required_attribute(segment, "source")?.is_none()
        || !required_attribute(segment, "member_token")?.is_none()
    {
        return Err(CoreError::protocol(
            "encoded composite bridge ownership metadata is invalid",
        ));
    }
    validate_empty_segment_bytes(segment, "root_ids")?;
    validate_empty_segment_bytes(segment, "anonymous_scope_map")?;
    if local_root_count == 0 {
        return Err(CoreError::protocol(
            "encoded composite bridge must carry local structural roots",
        ));
    }
    Ok(())
}

fn validate_structural_fingerprint(
    encoded_view: &Bound<'_, PyAny>,
    bindings: &EncodedBufferBindings<'_>,
    segments: &Bound<'_, PyTuple>,
) -> CoreResult<()> {
    let expected = read_fingerprint(encoded_view, "structural_fingerprint")?;
    if expected.schema != 1 {
        return Err(CoreError::protocol(
            "encoded structural fingerprint schema must be one",
        ));
    }
    let descriptor = required_attribute(encoded_view, "descriptor")?;
    let descriptor = descriptor.cast::<PyBytes>().map_err(|_| {
        CoreError::protocol("encoded view descriptor must be exact immutable bytes")
    })?;
    let columns = bindings.columns()?;
    let sources = [
        columns.root_kinds,
        columns.root_ids,
        columns.node_tags,
        columns.node_field_offsets,
        columns.field_kinds,
        columns.field_values,
        columns.field_lengths,
        columns.item_kinds,
        columns.item_values,
        columns.item_lengths,
        columns.scalar_bytes,
    ];
    let mut digest = Sha256::new();
    digest.update(b"pyowl-core:encoded-structural-view:v1\0");
    update_varint_frame(&mut digest, descriptor.as_bytes())?;
    for (name, source) in ENCODED_BUFFER_NAMES.into_iter().zip(sources) {
        update_varint_frame(&mut digest, name.as_bytes())?;
        update_source_length(&mut digest, source)?;
        update_byte_source(&mut digest, source)?;
    }
    digest.update(
        u64::try_from(segments.len())
            .map_err(|_| CoreError::capacity("encoded segment count exceeds u64"))?
            .to_le_bytes(),
    );
    for index in 0..segments.len() {
        let segment = segments
            .get_item(index)
            .map_err(|_| CoreError::protocol("encoded structural segment is inaccessible"))?;
        let role = exact_u8_attribute(&segment, "role", "encoded segment role")?;
        let posting_mode =
            exact_u8_attribute(&segment, "posting_mode", "encoded segment posting_mode")?;
        digest.update([role, posting_mode]);

        let source = required_attribute(&segment, "source")?;
        if source.is_none() {
            digest.update([0]);
        } else {
            let source_fingerprint = read_fingerprint(&source, "structural_fingerprint")?;
            let source_schema = u32::try_from(source_fingerprint.schema).map_err(|_| {
                CoreError::protocol("referenced structural fingerprint schema exceeds u32")
            })?;
            digest.update([1]);
            digest.update(source_schema.to_le_bytes());
            digest.update(source_fingerprint.digest);
        }

        let member_token = required_attribute(&segment, "member_token")?;
        if member_token.is_none() {
            digest.update([0]);
        } else {
            let member_token = member_token.cast::<PyBytes>().map_err(|_| {
                CoreError::protocol("encoded segment member token must be exact immutable bytes")
            })?;
            if member_token.as_bytes().len() != 32 {
                return Err(CoreError::protocol(
                    "encoded segment member token must contain 32 bytes",
                ));
            }
            digest.update([1]);
            digest.update(member_token.as_bytes());
        }

        for name in ["root_ids", "anonymous_scope_map"] {
            let value = required_attribute(&segment, name)?;
            let owner = value
                .getattr("obj")
                .ok()
                .and_then(|candidate| candidate.cast_into::<PyBytes>().ok());
            let source = borrowed_py_bytes(&value, owner.as_ref(), name)?;
            update_source_length(&mut digest, source)?;
            update_byte_source(&mut digest, source)?;
        }
    }
    let actual: [u8; 32] = digest.finalize().into();
    if actual != expected.digest {
        return Err(CoreError::protocol(
            "encoded structural fingerprint does not cover its buffers and segments",
        ));
    }
    Ok(())
}

fn exact_u8_attribute(owner: &Bound<'_, PyAny>, attribute: &str, name: &str) -> CoreResult<u8> {
    u8::try_from(exact_nonnegative_integer(
        &required_attribute(owner, attribute)?,
        name,
    )?)
    .map_err(|_| CoreError::protocol(format!("{name} must fit u8")))
}

fn update_varint_frame(digest: &mut Sha256, value: &[u8]) -> CoreResult<()> {
    let mut length = u64::try_from(value.len())
        .map_err(|_| CoreError::capacity("encoded fingerprint frame length exceeds u64"))?;
    loop {
        let low = u8::try_from(length & 0x7f)
            .map_err(|_| CoreError::internal("varint low byte exceeds u8"))?;
        length >>= 7;
        digest.update([low | if length == 0 { 0 } else { 0x80 }]);
        if length == 0 {
            break;
        }
    }
    digest.update(value);
    Ok(())
}

fn update_source_length(digest: &mut Sha256, source: BorrowedPyBytes<'_, '_>) -> CoreResult<()> {
    digest.update(
        u64::try_from(source.len())
            .map_err(|_| CoreError::capacity("encoded fingerprint source length exceeds u64"))?
            .to_le_bytes(),
    );
    Ok(())
}

fn update_byte_source(digest: &mut Sha256, source: BorrowedPyBytes<'_, '_>) -> CoreResult<()> {
    if let Some(bytes) = source.bytes {
        digest.update(bytes);
        return Ok(());
    }
    for index in 0..source.len() {
        digest.update([source.byte(index).ok_or_else(|| {
            CoreError::protocol("encoded fingerprint source became inaccessible")
        })?]);
    }
    Ok(())
}

fn validate_encoded_envelope<'py>(
    encoded_view: &Bound<'py, PyAny>,
) -> CoreResult<(Bound<'py, PyAny>, u64)> {
    let schema_name = required_attribute(encoded_view, "schema_name")?
        .extract::<String>()
        .map_err(|_| CoreError::protocol("encoded view schema_name must be text"))?;
    if schema_name != ENCODED_SCHEMA_NAME {
        return Err(CoreError::protocol(
            "encoded view schema name is unsupported",
        ));
    }
    let schema_version = exact_nonnegative_integer(
        &required_attribute(encoded_view, "schema_version")?,
        "encoded view schema_version",
    )?;
    if schema_version != ENCODED_SCHEMA_VERSION {
        return Err(CoreError::protocol(
            "encoded view schema version is unsupported",
        ));
    }
    let model_schema = exact_nonnegative_integer(
        &required_attribute(encoded_view, "model_schema")?,
        "encoded view model_schema",
    )?;
    if model_schema != ENCODED_MODEL_SCHEMA {
        return Err(CoreError::protocol(
            "encoded view model schema is unsupported",
        ));
    }
    let scope = scope_value(&required_attribute(encoded_view, "scope")?)?;
    if scope != "closure" {
        return Err(CoreError::protocol(
            "encoded native reasoning requires closure scope",
        ));
    }
    if !required_attribute(encoded_view, "document_key")?.is_none() {
        return Err(CoreError::protocol(
            "closure-scoped encoded view must not select a document",
        ));
    }

    let descriptor = required_attribute(encoded_view, "descriptor")?;
    let descriptor = descriptor.cast::<PyBytes>().map_err(|_| {
        CoreError::protocol("encoded view descriptor must be exact immutable bytes")
    })?;
    if Sha256::digest(descriptor.as_bytes()).as_slice() != DESCRIPTOR_SHA256_V1 {
        return Err(CoreError::protocol(
            "encoded view descriptor does not match structural-columns v1",
        ));
    }

    let _structural = read_fingerprint(encoded_view, "structural_fingerprint")?;
    let owner = required_attribute(encoded_view, "owner")?;
    validate_owner_capabilities(&owner, model_schema)?;
    Ok((owner, model_schema))
}

fn validate_owner_capabilities(owner: &Bound<'_, PyAny>, model_schema: u64) -> CoreResult<()> {
    let capabilities = required_attribute(owner, "capabilities")?;
    let owner_model = exact_nonnegative_integer(
        &required_attribute(&capabilities, "model_schema")?,
        "encoded owner model_schema",
    )?;
    if owner_model != model_schema {
        return Err(CoreError::protocol(
            "encoded view model schema differs from its owner",
        ));
    }
    Ok(())
}

fn validate_overlay_delta_segment(
    segment: &Bound<'_, PyAny>,
    top_owner: &Bound<'_, PyAny>,
    local_root_count: usize,
) -> CoreResult<()> {
    let role = exact_nonnegative_integer(
        &required_attribute(segment, "role")?,
        "encoded delta segment role",
    )?;
    let posting_mode = exact_nonnegative_integer(
        &required_attribute(segment, "posting_mode")?,
        "encoded delta segment posting_mode",
    )?;
    if role != SEGMENT_OVERLAY_DELTA || posting_mode != POSTINGS_ALL {
        return Err(CoreError::protocol(
            "encoded overlay delta role or posting mode is invalid",
        ));
    }
    if !required_attribute(segment, "owner")?.is(top_owner) {
        return Err(CoreError::protocol(
            "encoded overlay delta does not retain the top owner",
        ));
    }
    if !required_attribute(segment, "source")?.is_none() {
        return Err(CoreError::protocol(
            "encoded overlay delta must not reference a source view",
        ));
    }
    if !required_attribute(segment, "member_token")?.is_none() {
        return Err(CoreError::protocol(
            "encoded overlay delta must not carry a member token",
        ));
    }
    validate_empty_segment_bytes(segment, "root_ids")?;
    validate_empty_segment_bytes(segment, "anonymous_scope_map")?;
    if local_root_count == 0 {
        return Err(CoreError::protocol(
            "encoded overlay delta must carry local structural roots",
        ));
    }
    Ok(())
}

fn validate_direct_segment(
    encoded_view: &Bound<'_, PyAny>,
    owner: &Bound<'_, PyAny>,
) -> CoreResult<()> {
    let raw_segments = required_attribute(encoded_view, "segments")?;
    let segments = raw_segments
        .cast::<PyTuple>()
        .map_err(|_| CoreError::protocol("encoded direct view segments must be an exact tuple"))?;
    if segments.len() != 1 {
        return Err(CoreError::invalid(
            "encoded compiler slice currently accepts exactly one direct segment",
        ));
    }
    let segment = segments
        .get_item(0)
        .map_err(|_| CoreError::protocol("encoded direct segment is inaccessible"))?;
    let role = exact_nonnegative_integer(
        &required_attribute(&segment, "role")?,
        "encoded segment role",
    )?;
    let posting_mode = exact_nonnegative_integer(
        &required_attribute(&segment, "posting_mode")?,
        "encoded segment posting_mode",
    )?;
    if role != SEGMENT_DIRECT || posting_mode != POSTINGS_ALL {
        return Err(CoreError::invalid(
            "encoded compiler slice currently accepts only direct all-postings segments",
        ));
    }
    if !required_attribute(&segment, "owner")?.is(owner) {
        return Err(CoreError::protocol(
            "encoded direct segment owner differs from view owner",
        ));
    }
    if !required_attribute(&segment, "source")?.is_none() {
        return Err(CoreError::protocol(
            "encoded direct segment must not reference a source view",
        ));
    }
    if !required_attribute(&segment, "member_token")?.is_none() {
        return Err(CoreError::protocol(
            "encoded direct segment must not carry a member token",
        ));
    }
    for name in ["root_ids", "anonymous_scope_map"] {
        let value = required_attribute(&segment, name)?;
        if !borrowed_py_bytes(&value, None, name)?.is_empty() {
            return Err(CoreError::protocol(format!(
                "encoded direct segment {name} must be empty"
            )));
        }
    }
    Ok(())
}

fn validate_empty_segment_bytes(segment: &Bound<'_, PyAny>, name: &str) -> CoreResult<()> {
    let value = required_attribute(segment, name)?;
    if !borrowed_py_bytes(&value, None, name)?.is_empty() {
        return Err(CoreError::protocol(format!(
            "encoded segment {name} must be empty"
        )));
    }
    Ok(())
}

#[derive(Clone)]
struct FingerprintParts {
    algorithm: String,
    schema: u64,
    digest: [u8; 32],
}

struct EncodedSourceParts {
    logical: FingerprintParts,
    signature: FingerprintParts,
    model_schema: u64,
}

fn read_fingerprint(owner: &Bound<'_, PyAny>, name: &str) -> CoreResult<FingerprintParts> {
    let fingerprint = required_attribute(owner, name)?;
    let algorithm = required_attribute(&fingerprint, "algorithm")?
        .extract::<String>()
        .map_err(|_| CoreError::protocol(format!("{name} algorithm must be text")))?;
    if algorithm != "sha256" {
        return Err(CoreError::protocol(format!(
            "{name} algorithm must be sha256"
        )));
    }
    let schema = exact_nonnegative_integer(
        &required_attribute(&fingerprint, "schema")?,
        &format!("{name} schema"),
    )?;
    if schema == 0 {
        return Err(CoreError::protocol(format!(
            "{name} schema must be positive"
        )));
    }
    let raw_digest = required_attribute(&fingerprint, "digest")?;
    let raw_digest = raw_digest
        .cast::<PyBytes>()
        .map_err(|_| CoreError::protocol(format!("{name} digest must be exact immutable bytes")))?;
    let digest: [u8; 32] = raw_digest
        .as_bytes()
        .try_into()
        .map_err(|_| CoreError::protocol(format!("{name} digest must contain 32 bytes")))?;
    Ok(FingerprintParts {
        algorithm,
        schema,
        digest,
    })
}

fn compatibility_spelling_digest(observations: &[Vec<u8>]) -> CoreResult<[u8; 32]> {
    let mut digest = Sha256::new();
    digest.update(b"pyelk:elk-literal-compatibility-inputs:v1\0");
    for value in observations {
        let length = u64::try_from(value.len()).map_err(|_| {
            CoreError::capacity("literal compatibility observation length exceeds u64")
        })?;
        digest.update(length.to_be_bytes());
        digest.update(value);
    }
    Ok(digest.finalize().into())
}

fn encoded_source_fingerprint(
    py: Python<'_>,
    logical: &FingerprintParts,
    signature: &FingerprintParts,
    unsupported: &str,
    model_schema: u64,
    compatibility_spelling: &[u8; 32],
) -> CoreResult<[u8; 32]> {
    let core = PyModule::import(py, "pyowl_core")
        .map_err(|_| CoreError::protocol("cannot import public pyowl_core version metadata"))?;
    let package_version = required_attribute(core.as_any(), "__version__")?
        .extract::<String>()
        .map_err(|_| CoreError::protocol("pyowl_core.__version__ must be text"))?;
    let api_version = exact_version_pair(
        &required_attribute(core.as_any(), "API_VERSION")?,
        "pyowl_core.API_VERSION",
    )?;
    let core_model_schema = exact_nonnegative_integer(
        &required_attribute(core.as_any(), "MODEL_SCHEMA_VERSION")?,
        "pyowl_core.MODEL_SCHEMA_VERSION",
    )?;
    let wire_version = exact_version_pair(
        &required_attribute(core.as_any(), "WIRE_FORMAT_VERSION")?,
        "pyowl_core.WIRE_FORMAT_VERSION",
    )?;
    let adapter_protocol = exact_nonnegative_integer(
        &required_attribute(core.as_any(), "ADAPTER_PROTOCOL_VERSION")?,
        "pyowl_core.ADAPTER_PROTOCOL_VERSION",
    )?;
    if core_model_schema != model_schema {
        return Err(CoreError::protocol(
            "encoded view model schema differs from public pyowl_core metadata",
        ));
    }

    let mut semantic_options = Sha256::new();
    semantic_options.update(b"pyelk:compiler-semantic-options:v1\0");
    semantic_options.update(unsupported.as_bytes());
    let semantic_options = semantic_options.finalize();
    let api_text = format!("{}.{}", api_version.0, api_version.1);
    let wire_text = format!("{}.{}", wire_version.0, wire_version.1);

    let mut digest = Blake2b256::new();
    digest.update(b"pyelk:compiled-ontology-source:v1\0");
    for value in [
        logical.algorithm.as_bytes(),
        &logical.schema.to_le_bytes(),
        &logical.digest,
        signature.algorithm.as_bytes(),
        &signature.schema.to_le_bytes(),
        &signature.digest,
        package_version.as_bytes(),
        api_text.as_bytes(),
        &core_model_schema.to_le_bytes(),
        wire_text.as_bytes(),
        &adapter_protocol.to_le_bytes(),
        &COMPILER_SCHEMA_VERSION.to_le_bytes(),
        ELK_COMPATIBILITY_ID.as_bytes(),
        semantic_options.as_slice(),
        compatibility_spelling.as_slice(),
    ] {
        update_framed(&mut digest, value)?;
    }
    let finalized = digest.finalize();
    let mut result = [0_u8; 32];
    result.copy_from_slice(finalized.as_slice());
    Ok(result)
}

fn update_framed(digest: &mut Blake2b256, value: &[u8]) -> CoreResult<()> {
    let length = u64::try_from(value.len())
        .map_err(|_| CoreError::capacity("fingerprint component length does not fit u64"))?;
    digest.update(length.to_le_bytes());
    digest.update(value);
    Ok(())
}

fn exact_version_pair(value: &Bound<'_, PyAny>, name: &str) -> CoreResult<(u64, u64)> {
    let pair = value
        .cast::<PyTuple>()
        .map_err(|_| CoreError::protocol(format!("{name} must be an exact tuple")))?;
    if pair.len() != 2 {
        return Err(CoreError::protocol(format!(
            "{name} must contain two integers"
        )));
    }
    Ok((
        exact_nonnegative_integer(
            &pair
                .get_item(0)
                .map_err(|_| CoreError::protocol(format!("{name} major is inaccessible")))?,
            name,
        )?,
        exact_nonnegative_integer(
            &pair
                .get_item(1)
                .map_err(|_| CoreError::protocol(format!("{name} minor is inaccessible")))?,
            name,
        )?,
    ))
}

fn exact_nonnegative_integer(value: &Bound<'_, PyAny>, name: &str) -> CoreResult<u64> {
    if !value.is_exact_instance_of::<PyInt>() {
        return Err(CoreError::protocol(format!(
            "{name} must be an exact nonnegative integer"
        )));
    }
    value
        .extract::<u64>()
        .map_err(|_| CoreError::protocol(format!("{name} must fit u64")))
}

fn scope_value(value: &Bound<'_, PyAny>) -> CoreResult<String> {
    value
        .extract::<String>()
        .or_else(|_| value.getattr("value")?.extract::<String>())
        .map_err(|_| CoreError::protocol("encoded view scope must be text-compatible"))
}

fn required_attribute<'py>(value: &Bound<'py, PyAny>, name: &str) -> CoreResult<Bound<'py, PyAny>> {
    value
        .getattr(name)
        .map_err(|_| CoreError::protocol(format!("encoded input is missing attribute {name}")))
}

fn borrowed_py_bytes<'a, 'py>(
    buffer: &'a Bound<'py, PyAny>,
    bytes_owner: Option<&'a Bound<'py, PyBytes>>,
    name: &str,
) -> CoreResult<BorrowedPyBytes<'a, 'py>> {
    let invalid = |message: &str| CoreError::protocol(format!("encoded buffer {name} {message}"));
    if !buffer.is_exact_instance_of::<PyMemoryView>() {
        return Err(invalid("is not an exact memoryview"));
    }
    let readonly = required_attribute(buffer, "readonly")?
        .extract::<bool>()
        .map_err(|_| invalid("has invalid readonly metadata"))?;
    if !readonly {
        return Err(invalid("is writable"));
    }
    let dimensions = required_attribute(buffer, "ndim")?
        .extract::<usize>()
        .map_err(|_| invalid("has invalid dimensional metadata"))?;
    let item_size = required_attribute(buffer, "itemsize")?
        .extract::<usize>()
        .map_err(|_| invalid("has invalid item-size metadata"))?;
    let contiguous = required_attribute(buffer, "c_contiguous")?
        .extract::<bool>()
        .map_err(|_| invalid("has invalid contiguity metadata"))?;
    let format = required_attribute(buffer, "format")?
        .extract::<String>()
        .map_err(|_| invalid("has invalid format metadata"))?;
    if dimensions != 1 || item_size != 1 || !contiguous || format != "B" {
        return Err(invalid(
            "is not a contiguous one-dimensional unsigned-byte memoryview",
        ));
    }
    let len = required_attribute(buffer, "nbytes")?
        .extract::<usize>()
        .map_err(|_| invalid("has invalid byte-length metadata"))?;
    if buffer.len().map_err(|_| invalid("has invalid length"))? != len {
        return Err(invalid("has inconsistent byte-length metadata"));
    }
    let bytes = bytes_owner
        .map(|owner| owner.as_bytes())
        .filter(|owner| owner.len() == len);
    Ok(BorrowedPyBytes {
        view: buffer,
        bytes,
        len,
    })
}

#[pyfunction]
fn create_session(
    py: Python<'_>,
    ir: &Bound<'_, PyBytes>,
    workers: isize,
) -> PyResult<NativeSession> {
    if workers < 0 {
        return Err(PyValueError::new_err(
            "workers must be a nonnegative integer",
        ));
    }
    let payload = ir.as_bytes().to_vec();
    let worker_count = usize::try_from(workers)
        .map_err(|_| PyValueError::new_err("workers does not fit usize"))?;
    let outcome = py.detach(move || {
        catch_unwind(AssertUnwindSafe(|| {
            NativeCoreSession::create(&payload, worker_count)
        }))
        .map_err(|panic| {
            CoreError::Internal(format!(
                "panic during create_session: {}",
                panic_message(panic.as_ref())
            ))
        })?
    });
    let session = outcome.map_err(core_error)?;
    Ok(NativeSession {
        inner: Arc::new(Mutex::new(SessionState {
            session: Some(session),
            encoded_owner: None,
            encoded_metrics: None,
            failed: false,
        })),
        creator_pid: process::id(),
    })
}

fn panic_message(payload: &(dyn Any + Send)) -> &str {
    payload
        .downcast_ref::<&str>()
        .copied()
        .or_else(|| payload.downcast_ref::<String>().map(String::as_str))
        .unwrap_or("non-text panic payload")
}

fn core_error(error: CoreError) -> PyErr {
    match error {
        CoreError::Protocol(message) | CoreError::InvalidInput(message) => {
            PyValueError::new_err(message)
        }
        CoreError::Unsupported(feature) => NativeUnsupportedFeatureError::new_err(feature),
        CoreError::Capacity(message)
        | CoreError::Closed(message)
        | CoreError::Internal(message) => PyRuntimeError::new_err(message),
    }
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add(
        "NativeUnsupportedFeatureError",
        module.py().get_type::<NativeUnsupportedFeatureError>(),
    )?;
    module.add_class::<NativeSession>()?;
    module.add_function(wrap_pyfunction!(implementation_version, module)?)?;
    module.add_function(wrap_pyfunction!(ir_version, module)?)?;
    module.add_function(wrap_pyfunction!(abi_version, module)?)?;
    module.add_function(wrap_pyfunction!(self_check, module)?)?;
    module.add_function(wrap_pyfunction!(encoded_view_schemas, module)?)?;
    module.add_function(wrap_pyfunction!(create_session_from_encoded, module)?)?;
    module.add_function(wrap_pyfunction!(create_session, module)?)?;
    Ok(())
}
