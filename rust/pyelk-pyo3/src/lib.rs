//! Thin private PyO3 boundary for the Python-free pyELK core.

#![forbid(unsafe_code)]

use std::any::Any;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::{Arc, Mutex};

use pyelk_core::wire::{encode_query, encode_realization, encode_taxonomy};
use pyelk_core::{
    CoreError, CoreResult, DiagnosticValue, IMPLEMENTATION_VERSION, IR_MAJOR, IR_MINOR,
    NativeCoreSession, QueryKind,
};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyModule};

struct SessionState {
    session: Option<NativeCoreSession>,
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
                    }
                    Err(error)
                }
                Err(payload) => {
                    state.failed = true;
                    state.session = None;
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

/// Fail-closed placeholder for the future coarse encoded-view compiler entry point.
#[pyfunction]
fn create_session_from_encoded(
    _encoded_view: &Bound<'_, PyAny>,
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
    Err(PyValueError::new_err(
        "this extension advertises no encoded structural schema",
    ))
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
