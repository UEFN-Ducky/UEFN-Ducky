//! `ducky.db`, the same file the Python backend uses (`ducky_app/backend/store`).
//!
//! Reads run on pooled read-only connections; every write goes through one writer thread
//! ([`writer`]), so no request ever waits on a lock inside an async worker. The Python
//! backend still owns the schema: nothing here creates or upgrades tables.

pub mod account;
pub mod chat_messages;
pub mod chats;
pub mod conn;
pub mod duckies;
pub mod events;
pub mod http;
pub mod ledger;
mod local_chats;
pub mod messages;
pub mod misc;
pub mod pyjson;
pub mod seal;
pub mod search;
pub mod sealed_file;
pub mod sealed_files;
pub mod secrets;
pub mod settings;
pub mod tokens;
pub mod writer;

use std::fmt;
use std::path::Path;
use std::sync::Arc;

/// A store failure, as text: one commit failure is the answer to every write of its batch.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Error(pub String);

pub type Result<T> = std::result::Result<T, Error>;

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for Error {}

impl From<rusqlite::Error> for Error {
    fn from(err: rusqlite::Error) -> Self {
        Error(err.to_string())
    }
}

impl From<std::io::Error> for Error {
    fn from(err: std::io::Error) -> Self {
        Error(err.to_string())
    }
}

/// Work that blocks (a file, a system call), off the async workers.
pub(crate) async fn blocking<T: Send + 'static>(work: impl FnOnce() -> Result<T> + Send + 'static) -> Result<T> {
    tokio::task::spawn_blocking(work).await.unwrap_or_else(|_| Err(Error("the call panicked".into())))
}

/// A store refusal raised inside a database closure (a row that would not open): it
/// comes back out of the read or write with its message.
impl From<Error> for rusqlite::Error {
    fn from(err: Error) -> Self {
        // The one always-built variant that carries a message and prints only it.
        rusqlite::Error::ToSqlConversionFailure(Box::new(err))
    }
}

/// Seconds since 1970, as Python's `time.time()` stamps rows.
pub(crate) fn now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0.0, |d| d.as_secs_f64())
}

/// One open database: its writer and its readers. Cheap to clone.
#[derive(Clone)]
pub struct Store {
    pub writer: writer::Writer,
    pub readers: Arc<conn::Readers>,
}

impl Store {
    pub fn open(path: &Path) -> Result<Store> {
        Store::open_with(path, writer::Options::default())
    }

    pub fn open_with(path: &Path, opts: writer::Options) -> Result<Store> {
        // The writer first: it creates the file and switches it to WAL, which a
        // read-only connection cannot do.
        let writer = writer::Writer::start(path, opts)?;
        Ok(Store { writer, readers: Arc::new(conn::Readers::new(path)) })
    }
}
