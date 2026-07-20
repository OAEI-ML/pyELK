//! Thin private PyO3 boundary for the Python-free pyELK core.

#![forbid(unsafe_code)]

use std::any::Any;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::{Arc, Mutex};

use blake2::digest::consts::U32;
use blake2::{Blake2b, Digest};
use pyelk_core::encoded::{
    ByteSource, DESCRIPTOR_SHA256_V1, EncodedColumns, EncodedLimits, EncodedUnsupportedPolicy,
    compile_named_hierarchy_with_policy,
};
use pyelk_core::wire::{encode_query, encode_realization, encode_taxonomy};
use pyelk_core::{
    CoreError, CoreResult, DiagnosticValue, IMPLEMENTATION_VERSION, IR_MAJOR, IR_MINOR,
    NativeCoreSession, QueryKind,
};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{
    PyBytes, PyDict, PyInt, PyMapping, PyMemoryView, PyModule, PyTuple, PyTupleMethods,
};
use sha2::Sha256;

type Blake2b256 = Blake2b<U32>;

const ENCODED_SCHEMA_NAME: &str = "pyowl-core/structural-columns";
const ENCODED_SCHEMA_VERSION: u64 = 1;
const ENCODED_MODEL_SCHEMA: u64 = 1;
const ENCODED_BUFFER_COUNT: usize = 11;
const COMPILER_SCHEMA_VERSION: u64 = 1;
const ELK_COMPATIBILITY_ID: &str = "elk-0.6.0:b8ac5ce83db0704a7359d96aa382891e2f547863";

#[derive(Clone, Copy)]
struct BorrowedPyBytes<'a, 'py> {
    view: &'a Bound<'py, PyAny>,
    len: usize,
}

impl ByteSource for BorrowedPyBytes<'_, '_> {
    fn len(self) -> usize {
        self.len
    }

    fn byte(self, index: usize) -> Option<u8> {
        self.view.get_item(index).ok()?.extract().ok()
    }
}

struct EncodedBufferBindings<'py> {
    root_kinds: Bound<'py, PyAny>,
    root_ids: Bound<'py, PyAny>,
    node_tags: Bound<'py, PyAny>,
    node_field_offsets: Bound<'py, PyAny>,
    field_kinds: Bound<'py, PyAny>,
    field_values: Bound<'py, PyAny>,
    field_lengths: Bound<'py, PyAny>,
    item_kinds: Bound<'py, PyAny>,
    item_values: Bound<'py, PyAny>,
    item_lengths: Bound<'py, PyAny>,
    scalar_bytes: Bound<'py, PyAny>,
}

struct SessionState {
    session: Option<NativeCoreSession>,
    encoded_owner: Option<Py<PyAny>>,
    failed: bool,
}

/// The only native handle exposed by the private extension.
#[pyclass(module = "pyelk._native")]
struct NativeSession {
    inner: Arc<Mutex<SessionState>>,
}

impl NativeSession {
    fn detached<T, F>(&self, py: Python<'_>, stage: &'static str, operation: F) -> PyResult<T>
    where
        T: Send + 'static,
        F: FnOnce(&mut NativeCoreSession) -> CoreResult<T> + Send + 'static,
    {
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
        let result = PyDict::new(py);
        for (key, value) in values {
            match value {
                DiagnosticValue::Integer(item) => result.set_item(key, item)?,
                DiagnosticValue::Boolean(item) => result.set_item(key, item)?,
                DiagnosticValue::Text(item) => result.set_item(key, item)?,
            }
        }
        Ok(result.unbind())
    }

    fn close(&self) -> PyResult<()> {
        let mut state = self
            .inner
            .lock()
            .map_err(|_| PyRuntimeError::new_err("native session lock is poisoned"))?;
        state.session = None;
        state.encoded_owner = None;
        Ok(())
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
/// This first executable handoff accepts only a validated direct segment.  Overlay, composite,
/// mmap-lifetime, exhaustive-constructor, and performance gates remain release blockers, so
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
        let (bindings, source_fingerprint) =
            validate_direct_encoded_input(py, encoded_view, unsupported)?;
        let ontology = compile_named_hierarchy_with_policy(
            bindings.columns()?,
            EncodedLimits::default(),
            source_fingerprint,
            policy,
        )?;
        NativeCoreSession::from_ontology(ontology, worker_count)
    }));
    let session = match outcome {
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
            failed: false,
        })),
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
            root_kinds: get("root_kinds")?,
            root_ids: get("root_ids")?,
            node_tags: get("node_tags")?,
            node_field_offsets: get("node_field_offsets")?,
            field_kinds: get("field_kinds")?,
            field_values: get("field_values")?,
            field_lengths: get("field_lengths")?,
            item_kinds: get("item_kinds")?,
            item_values: get("item_values")?,
            item_lengths: get("item_lengths")?,
            scalar_bytes: get("scalar_bytes")?,
        })
    }

    fn columns(&self) -> CoreResult<EncodedColumns<BorrowedPyBytes<'_, 'py>>> {
        Ok(EncodedColumns {
            root_kinds: borrowed_py_bytes(&self.root_kinds, "root_kinds")?,
            root_ids: borrowed_py_bytes(&self.root_ids, "root_ids")?,
            node_tags: borrowed_py_bytes(&self.node_tags, "node_tags")?,
            node_field_offsets: borrowed_py_bytes(&self.node_field_offsets, "node_field_offsets")?,
            field_kinds: borrowed_py_bytes(&self.field_kinds, "field_kinds")?,
            field_values: borrowed_py_bytes(&self.field_values, "field_values")?,
            field_lengths: borrowed_py_bytes(&self.field_lengths, "field_lengths")?,
            item_kinds: borrowed_py_bytes(&self.item_kinds, "item_kinds")?,
            item_values: borrowed_py_bytes(&self.item_values, "item_values")?,
            item_lengths: borrowed_py_bytes(&self.item_lengths, "item_lengths")?,
            scalar_bytes: borrowed_py_bytes(&self.scalar_bytes, "scalar_bytes")?,
        })
    }
}

fn validate_direct_encoded_input<'py>(
    py: Python<'py>,
    encoded_view: &Bound<'py, PyAny>,
    unsupported: &str,
) -> CoreResult<(EncodedBufferBindings<'py>, [u8; 32])> {
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

    let structural = read_fingerprint(encoded_view, "structural_fingerprint")?;
    let owner = required_attribute(encoded_view, "owner")?;
    validate_owner_capabilities(&owner, model_schema)?;
    validate_direct_segment(encoded_view, &owner)?;
    let logical = read_fingerprint(&owner, "logical_fingerprint")?;
    let signature = read_fingerprint(&owner, "signature_fingerprint")?;
    let source_fingerprint =
        encoded_source_fingerprint(py, &logical, &signature, unsupported, model_schema)?;

    // The encoded structural fingerprint is a required owner/segment identity even though the
    // private semantic cache fingerprint intentionally follows the scalar logical/signature key.
    let _ = structural;
    Ok((
        EncodedBufferBindings::from_view(encoded_view)?,
        source_fingerprint,
    ))
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
    if role != 1 || posting_mode != 0 {
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
        if !borrowed_py_bytes(&value, name)?.is_empty() {
            return Err(CoreError::protocol(format!(
                "encoded direct segment {name} must be empty"
            )));
        }
    }
    Ok(())
}

#[derive(Clone)]
struct FingerprintParts {
    algorithm: String,
    schema: u64,
    digest: [u8; 32],
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

fn encoded_source_fingerprint(
    py: Python<'_>,
    logical: &FingerprintParts,
    signature: &FingerprintParts,
    unsupported: &str,
    model_schema: u64,
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
    let compatibility_spelling = Sha256::digest(b"pyelk:elk-literal-compatibility-inputs:v1\0");
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
    Ok(BorrowedPyBytes { view: buffer, len })
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
            failed: false,
        })),
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
        CoreError::Capacity(message)
        | CoreError::Closed(message)
        | CoreError::Internal(message) => PyRuntimeError::new_err(message),
    }
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
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
