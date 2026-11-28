//! Connections to `ducky.db`, configured as `backend/store/db.py::_configure` does, so
//! both backends can hold the same file open.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use rusqlite::{Connection, ErrorCode, OpenFlags};

use super::{Error, Result};

/// How long SQLite waits on another connection's lock before it answers "busy".
pub const BUSY_TIMEOUT: Duration = Duration::from_millis(5000);
const WAL_SIZE_LIMIT: i64 = 64 * 1024 * 1024;
const WRITER_CACHE_KIB: i64 = 32768;
const READER_CACHE_KIB: i64 = 2048;
/// Read connections kept open between calls.
const IDLE_READERS: usize = 8;

pub(crate) fn is_busy(err: &rusqlite::Error) -> bool {
    matches!(err.sqlite_error_code(), Some(ErrorCode::DatabaseBusy | ErrorCode::DatabaseLocked))
}

/// The read-write connection: WAL, checkpoints off (the checkpoint thread runs them
/// outside any commit), same pragmas as the Python backend's main connection.
pub fn open_writer(path: &Path, busy: Duration) -> Result<Connection> {
    let conn = Connection::open(path)?;
    conn.busy_timeout(busy)?;
    switch_to_wal(&conn, busy)?;
    conn.execute_batch(&format!(
        "PRAGMA journal_size_limit={WAL_SIZE_LIMIT}; PRAGMA wal_autocheckpoint=0; \
         PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON; \
         PRAGMA temp_store=MEMORY; PRAGMA cache_size=-{WRITER_CACHE_KIB};"
    ))?;
    assert_capabilities(&conn)?;
    Ok(conn)
}

/// SQLite never runs the busy handler for the switch to WAL: it fails at once while
/// another connection holds the file. The file stays WAL once set, so only a file that
/// is not is switched, and the switch is retried.
fn switch_to_wal(conn: &Connection, busy: Duration) -> Result<()> {
    let deadline = Instant::now() + busy;
    let mode = |sql: &str| conn.query_row(sql, [], |row| row.get::<_, String>(0)).map(|m| m.to_lowercase());
    while mode("PRAGMA journal_mode")? != "wal" {
        match mode("PRAGMA journal_mode=WAL") {
            Ok(now) if now == "wal" => break,
            Err(err) if !is_busy(&err) => return Err(err.into()),
            _ => {}
        }
        if Instant::now() > deadline {
            return Err(Error("database is locked: ducky.db could not be switched to WAL".into()));
        }
        #[allow(clippy::disallowed_methods)] // only ever runs on the thread opening the store
        std::thread::sleep(Duration::from_millis(20));
    }
    Ok(())
}

fn assert_capabilities(conn: &Connection) -> Result<()> {
    let fts5: bool = conn.query_row("SELECT sqlite_compileoption_used('ENABLE_FTS5')", [], |row| row.get(0))?;
    if !fts5 {
        return Err(Error("this SQLite build lacks FTS5; chat and memory search need it".into()));
    }
    conn.query_row("SELECT json_extract('{\"a\":1}', '$.a')", [], |row| row.get::<_, i64>(0))
        .map_err(|_| Error("this SQLite build lacks JSON functions".into()))?;
    Ok(())
}

fn open_reader(path: &Path) -> Result<Connection> {
    let conn = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX)?;
    conn.busy_timeout(BUSY_TIMEOUT)?;
    conn.execute_batch(&format!("PRAGMA temp_store=MEMORY; PRAGMA cache_size=-{READER_CACHE_KIB};"))?;
    Ok(conn)
}

/// Read-only connections, one per read in flight; in WAL they never wait for the writer.
pub struct Readers {
    path: PathBuf,
    idle: Mutex<Vec<Connection>>,
}

impl Readers {
    pub fn new(path: &Path) -> Readers {
        Readers { path: path.to_path_buf(), idle: Mutex::new(Vec::new()) }
    }

    /// Run `f` on a read connection, off the async workers.
    pub async fn read<T, F>(self: &Arc<Self>, f: F) -> Result<T>
    where
        T: Send + 'static,
        F: FnOnce(&Connection) -> rusqlite::Result<T> + Send + 'static,
    {
        let pool = Arc::clone(self);
        tokio::task::spawn_blocking(move || {
            let kept = pool.idle.lock().unwrap_or_else(|e| e.into_inner()).pop();
            let conn = match kept {
                Some(conn) => conn,
                None => open_reader(&pool.path)?,
            };
            let out = f(&conn)?;
            let mut idle = pool.idle.lock().unwrap_or_else(|e| e.into_inner());
            if idle.len() < IDLE_READERS {
                idle.push(conn);
            }
            Ok(out)
        })
        .await
        .unwrap_or_else(|_| Err(Error("the read panicked".into())))
    }
}
