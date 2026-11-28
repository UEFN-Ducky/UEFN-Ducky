//! The one thread that writes to `ducky.db`.
//!
//! Callers hand it a closure and await the answer, so a queue of writes never holds an
//! async worker. Writes waiting together share one `BEGIN IMMEDIATE` ... `COMMIT`, each
//! inside its own SAVEPOINT: one that fails or panics rolls back alone. An answer is sent
//! only after its COMMIT.

use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{self, Receiver, SyncSender};
use std::sync::Arc;
use std::time::{Duration, Instant};

use rusqlite::{Connection, OpenFlags};
use tokio::sync::oneshot;

use super::{conn, Error, Result};

/// Writes taken into one transaction at most.
const BATCH: usize = 256;
/// `BEGIN IMMEDIATE` attempts, each waiting the busy timeout, while the Python backend
/// (or another process) holds the write lock: a minute at the default timeout.
const BEGIN_TRIES: u32 = 12;
/// A commit landed: copy the WAL back at most this often (`db.py::CHECKPOINT_EVERY_S`).
const CHECKPOINT_EVERY: Duration = Duration::from_secs(1);

/// A write that took long, waits included: the Python backend's `db_write` perf event.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SlowWrite {
    /// Who asked for the write.
    pub caller: &'static str,
    /// Queued behind this process's other writes.
    pub wait_local_ms: u64,
    /// `BEGIN IMMEDIATE` waited on another process.
    pub wait_sqlite_ms: u64,
    /// Its own statements plus its transaction's COMMIT.
    pub held_ms: u64,
}

pub struct Options {
    pub busy: Duration,
    /// A write this slow, waits included, is reported to `on_slow`.
    pub trace_limit: Duration,
    pub on_slow: Box<dyn Fn(&SlowWrite) + Send>,
}

impl Default for Options {
    fn default() -> Self {
        Options {
            busy: conn::BUSY_TIMEOUT,
            trace_limit: Duration::from_millis(500),
            // The Python backend's `db_write` perf event, field for field.
            on_slow: Box::new(|w| {
                let total = (w.wait_local_ms + w.wait_sqlite_ms + w.held_ms) as f64;
                let meta = serde_json::json!({
                    "wait_local_ms": w.wait_local_ms, "wait_sqlite_ms": w.wait_sqlite_ms, "held_ms": w.held_ms, "thread": "rs-writer",
                });
                crate::perf::trace("db_write", w.caller, total, meta);
            }),
        }
    }
}

/// Sends a write's answer once the outcome of its COMMIT is known.
type Done = Box<dyn FnOnce(Result<()>) + Send>;
/// Runs a write on the connection, or takes the reason it never ran.
type Run = Box<dyn FnOnce(Result<&Connection>) -> Done + Send>;

struct Job {
    caller: &'static str,
    queued: Instant,
    run: Run,
}

#[derive(Clone)]
pub struct Writer {
    jobs: mpsc::Sender<Job>,
    commits: Arc<AtomicU64>,
}

impl Writer {
    /// Opens (and if new, creates) the database and starts the writer thread. It ends
    /// when the last clone is dropped, after the writes already queued.
    pub fn start(path: &Path, opts: Options) -> Result<Writer> {
        let conn = conn::open_writer(path, opts.busy)?;
        let (jobs, queue) = mpsc::channel();
        let commits = Arc::new(AtomicU64::new(0));
        let (counted, checkpoint) = (Arc::clone(&commits), checkpointer(path)?);
        std::thread::Builder::new()
            .name("rs-writer".into())
            .spawn(move || run(&conn, &queue, &opts, &counted, &checkpoint))
            .map_err(|e| Error(format!("the store writer could not start: {e}")))?;
        Ok(Writer { jobs, commits })
    }

    /// Run `f` in the writer's transaction and answer once it is committed. An `Err`
    /// from `f` undoes what `f` did, and nothing else.
    pub async fn write<T, F>(&self, caller: &'static str, f: F) -> Result<T>
    where
        T: Send + 'static,
        F: FnOnce(&Connection) -> rusqlite::Result<T> + Send + 'static,
    {
        let (answer, answered) = oneshot::channel();
        let run: Run = Box::new(move |conn: Result<&Connection>| -> Done {
            let out = conn.and_then(|conn| in_savepoint(conn, f));
            Box::new(move |commit: Result<()>| {
                let _ = answer.send(commit.and(out));
            })
        });
        self.jobs
            .send(Job { caller, queued: Instant::now(), run })
            .map_err(|_| Error("the store writer has stopped".into()))?;
        answered.await.unwrap_or_else(|_| Err(Error("the store writer dropped the write".into())))
    }

    /// Transactions committed so far.
    pub fn commits(&self) -> u64 {
        self.commits.load(Ordering::Relaxed)
    }
}

fn in_savepoint<T>(conn: &Connection, f: impl FnOnce(&Connection) -> rusqlite::Result<T>) -> Result<T> {
    conn.execute_batch("SAVEPOINT job")?;
    match catch_unwind(AssertUnwindSafe(|| f(conn))) {
        Ok(Ok(value)) => {
            conn.execute_batch("RELEASE job")?;
            Ok(value)
        }
        failed => {
            conn.execute_batch("ROLLBACK TO job; RELEASE job")?;
            Err(match failed {
                Ok(Err(err)) => err.into(),
                _ => Error("the write panicked".into()),
            })
        }
    }
}

fn begin(conn: &Connection) -> Result<()> {
    let mut tries = 0;
    loop {
        tries += 1;
        match conn.execute_batch("BEGIN IMMEDIATE") {
            Ok(()) => return Ok(()),
            // SQLite already waited the busy timeout: another process is mid-write.
            Err(err) if conn::is_busy(&err) && tries < BEGIN_TRIES => {}
            Err(err) => return Err(err.into()),
        }
    }
}

fn run(conn: &Connection, queue: &Receiver<Job>, opts: &Options, commits: &AtomicU64, checkpoint: &SyncSender<()>) {
    let ms = |d: Duration| d.as_millis() as u64;
    while let Ok(first) = queue.recv() {
        let mut batch = vec![first];
        while batch.len() < BATCH {
            match queue.try_recv() {
                Ok(job) => batch.push(job),
                Err(_) => break,
            }
        }
        let asked = Instant::now();
        if let Err(err) = begin(conn) {
            for job in batch {
                (job.run)(Err(err.clone()))(Ok(()));
            }
            continue;
        }
        let wait_sqlite = asked.elapsed();
        let mut ran = Vec::with_capacity(batch.len());
        for job in batch {
            let started = Instant::now();
            let done = (job.run)(Ok(conn));
            ran.push((job.caller, started.duration_since(job.queued), started.elapsed(), done));
        }
        let committing = Instant::now();
        let commit = conn.execute_batch("COMMIT").map_err(Error::from);
        if commit.is_err() {
            let _ = conn.execute_batch("ROLLBACK");
        } else {
            commits.fetch_add(1, Ordering::Relaxed);
            let _ = checkpoint.try_send(()); // full = one is already due
        }
        let commit_took = committing.elapsed();
        for (caller, waited, took, done) in ran {
            // Reported after the lock is released and before the caller hears back.
            if waited + took + commit_took >= opts.trace_limit {
                (opts.on_slow)(&SlowWrite {
                    caller,
                    wait_local_ms: ms(waited.saturating_sub(wait_sqlite)),
                    wait_sqlite_ms: ms(wait_sqlite),
                    held_ms: ms(took + commit_took),
                });
            }
            done(commit.clone());
        }
    }
}

/// The checkpoint thread: `wal_checkpoint(PASSIVE)` on its own connection, so the WAL is
/// copied back without the write lock and never inside a COMMIT. Ends with the writer.
fn checkpointer(path: &Path) -> Result<SyncSender<()>> {
    let (due, wake) = mpsc::sync_channel::<()>(1);
    let path = path.to_path_buf();
    std::thread::Builder::new()
        .name("rs-wal-checkpoint".into())
        .spawn(move || {
            let mut kept: Option<Connection> = None;
            while wake.recv().is_ok() {
                // READ_WRITE without CREATE: never brings back a database that was deleted.
                let flags = OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX;
                if let Some(conn) = kept.take().or_else(|| Connection::open_with_flags(&path, flags).ok()) {
                    let done = conn.query_row("PRAGMA wal_checkpoint(PASSIVE)", [], |_| Ok(()));
                    kept = done.is_ok().then_some(conn); // reopened after an error
                }
                #[allow(clippy::disallowed_methods)] // this thread's own pacing, no async worker
                std::thread::sleep(CHECKPOINT_EVERY);
            }
        })
        .map_err(|e| Error(format!("the checkpoint thread could not start: {e}")))?;
    Ok(due)
}
