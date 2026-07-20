//! Bounded, stage-oriented errors returned by the native core.

use std::error::Error;
use std::fmt::{Display, Formatter};

/// Error category used without Python exception types in the core crate.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CoreError {
    /// A packed ontology, query, or result violated the frozen protocol.
    Protocol(String),
    /// A caller supplied an invalid scalar or requested an invalid operation.
    InvalidInput(String),
    /// Strict compilation encountered one named unsupported ELK feature.
    Unsupported(String),
    /// An arithmetic or namespace limit was exceeded.
    Capacity(String),
    /// A session was closed or permanently invalidated.
    Closed(String),
    /// A native reasoning invariant failed.
    Internal(String),
}

impl CoreError {
    /// Construct a protocol error without exposing unbounded input bytes.
    pub fn protocol(message: impl Into<String>) -> Self {
        Self::Protocol(message.into())
    }

    /// Construct an invalid-input error.
    pub fn invalid(message: impl Into<String>) -> Self {
        Self::InvalidInput(message.into())
    }

    /// Construct an unsupported-feature error with a stable feature identifier.
    pub fn unsupported(feature: impl Into<String>) -> Self {
        Self::Unsupported(feature.into())
    }

    /// Construct a capacity error.
    pub fn capacity(message: impl Into<String>) -> Self {
        Self::Capacity(message.into())
    }

    /// Construct an internal invariant error.
    pub fn internal(message: impl Into<String>) -> Self {
        Self::Internal(message.into())
    }
}

impl Display for CoreError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Protocol(message) => write!(formatter, "protocol error: {message}"),
            Self::InvalidInput(message) => write!(formatter, "invalid input: {message}"),
            Self::Unsupported(feature) => write!(formatter, "unsupported ELK feature: {feature}"),
            Self::Capacity(message) => write!(formatter, "capacity error: {message}"),
            Self::Closed(message) => write!(formatter, "closed session: {message}"),
            Self::Internal(message) => write!(formatter, "internal reasoner error: {message}"),
        }
    }
}

impl Error for CoreError {}

/// Result alias used by every fallible core operation.
pub type CoreResult<T> = Result<T, CoreError>;
