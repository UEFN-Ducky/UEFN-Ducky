//! The store's connections and its single writer (`backend::store`).
#![allow(clippy::disallowed_methods)] // tests sleep and clean up on their own threads

use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use backend::store::writer::{Options, SlowWrite};
use backend::store::{Error, Store};
use rusqlite::Connection;

/// A database of its own per test, in a folder removed when the test ends.
struct Db(PathBuf);

impl Db {
    fn new(name: &str) -> Db {
        let dir = std::env::temp_dir().join(format!("ducky-rs-store-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        Db(dir.join("ducky.db"))
    }
}

impl Drop for Db {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(self.0.parent().unwrap());
    }
}

type Seen = Arc<Mutex<Vec<SlowWrite>>>;

/// A store that reports writes over 50 ms into the returned list.
fn traced(db: &Db, busy: Duration) -> (Store, Seen) {
    let seen = Seen::default();
    let sink = Arc::clone(&seen);
    let on_slow = Box::new(move |w: &SlowWrite| sink.lock().unwrap().push(w.clone()));
    let store = Store::open_with(&db.0, Options { busy, trace_limit: Duration::from_millis(50), on_slow }).unwrap();
    (store, seen)
}

fn sleep(ms: u64) {
    std::thread::sleep(Duration::from_millis(ms));
}

/// A write that only takes time.
fn slow(ms: u64) -> rusqlite::Result<()> {
    sleep(ms);
    Ok(())
}

#[tokio::test(flavor = "multi_thread")]
async fn the_writer_connection_has_the_python_backends_pragmas() {
    let db = Db::new("pragmas");
    let store = Store::open(&db.0).unwrap();
    let pragmas = store
        .writer
        .write("test", |conn| {
            let text = |name: &str| conn.query_row(&format!("PRAGMA {name}"), [], |r| r.get::<_, String>(0));
            let int = |name: &str| conn.query_row(&format!("PRAGMA {name}"), [], |r| r.get::<_, i64>(0));
            Ok((
                text("journal_mode")?,
                int("busy_timeout")?,
                int("wal_autocheckpoint")?,
                int("synchronous")?,
                int("foreign_keys")?,
                int("temp_store")?,
                int("journal_size_limit")?,
                int("cache_size")?,
            ))
        })
        .await
        .unwrap();
    assert_eq!(pragmas, ("wal".to_string(), 5000, 0, 1, 1, 2, 64 * 1024 * 1024, -32768));
    // FTS5 and JSON, which chat and memory search need.
    store
        .writer
        .write("test", |conn| conn.execute_batch("CREATE VIRTUAL TABLE f USING fts5(body); INSERT INTO f VALUES ('hello duck')"))
        .await
        .unwrap();
    let hit: (i64, i64) = store
        .readers
        .read(|conn| {
            conn.query_row("SELECT count(*), json_extract('{\"a\":7}', '$.a') FROM f WHERE f MATCH 'duck'", [], |r| {
                Ok((r.get(0)?, r.get(1)?))
            })
        })
        .await
        .unwrap();
    assert_eq!(hit, (1, 7));
}

#[tokio::test(flavor = "multi_thread")]
async fn a_read_connection_cannot_write() {
    let db = Db::new("readonly");
    let store = Store::open(&db.0).unwrap();
    let err = store.readers.read(|conn| conn.execute_batch("CREATE TABLE t(x)")).await.unwrap_err();
    assert!(err.0.contains("readonly"), "{err}");
}

#[tokio::test(flavor = "multi_thread")]
async fn a_hundred_concurrent_writes_all_commit_in_a_few_transactions() {
    let db = Db::new("hundred");
    let store = Store::open(&db.0).unwrap();
    store.writer.write("test", |conn| conn.execute_batch("CREATE TABLE t(x INTEGER)")).await.unwrap();
    // One slow write holds the writer, so the hundred queue up behind it.
    let holder = tokio::spawn({
        let writer = store.writer.clone();
        async move { writer.write("holder", |_| slow(150)).await }
    });
    tokio::time::sleep(Duration::from_millis(30)).await;
    let before = store.writer.commits();
    let mut writes = tokio::task::JoinSet::new();
    for x in 0..100i64 {
        let writer = store.writer.clone();
        writes.spawn(async move { writer.write("one", move |conn| conn.execute("INSERT INTO t VALUES (?1)", [x])).await });
    }
    while let Some(done) = writes.join_next().await {
        assert_eq!(done.unwrap(), Ok(1));
    }
    holder.await.unwrap().unwrap();
    let transactions = store.writer.commits() - before;
    assert!(transactions <= 3, "100 queued writes took {transactions} transactions");
    let (rows, sum): (i64, i64) =
        store.readers.read(|conn| conn.query_row("SELECT count(*), sum(x) FROM t", [], |r| Ok((r.get(0)?, r.get(1)?)))).await.unwrap();
    assert_eq!((rows, sum), (100, 4950));
}

#[tokio::test(flavor = "multi_thread")]
async fn a_write_that_fails_or_panics_rolls_back_alone() {
    let db = Db::new("alone");
    let store = Store::open(&db.0).unwrap();
    store.writer.write("test", |conn| conn.execute_batch("CREATE TABLE t(x INTEGER PRIMARY KEY)")).await.unwrap();
    let writer = &store.writer;
    // Queued together behind a slow write: one transaction for all four.
    let (_, good, bad, panicked, after) = tokio::join!(
        writer.write("holder", |_| slow(100)),
        writer.write("good", |conn| conn.execute("INSERT INTO t VALUES (1)", [])),
        writer.write("bad", |conn| {
            conn.execute("INSERT INTO t VALUES (2)", [])?;
            conn.execute("INSERT INTO t VALUES (2)", []) // primary key: fails, and takes the first 2 with it
        }),
        writer.write("panics", |conn| {
            conn.execute("INSERT INTO t VALUES (3)", [])?;
            if conn.is_autocommit() {
                return Ok(0);
            }
            panic!("boom")
        }),
        writer.write("after", |conn| conn.execute("INSERT INTO t VALUES (4)", [])),
    );
    assert_eq!((good, after), (Ok(1), Ok(1)));
    assert!(bad.unwrap_err().0.contains("UNIQUE"));
    assert_eq!(panicked, Err(Error("the write panicked".into())));
    let rows: Vec<i64> = store
        .readers
        .read(|conn| conn.prepare("SELECT x FROM t ORDER BY x")?.query_map([], |r| r.get(0))?.collect())
        .await
        .unwrap();
    assert_eq!(rows, [1, 4]);
}

#[tokio::test(flavor = "multi_thread")]
async fn a_slow_write_is_traced_with_its_caller_and_a_fast_one_is_not() {
    let db = Db::new("slow");
    let (store, seen) = traced(&db, Duration::from_secs(5));
    store.writer.write("fast.rs:1", |conn| conn.execute_batch("CREATE TABLE quick(x)")).await.unwrap();
    assert_eq!(*seen.lock().unwrap(), []);
    store.writer.write("slow.rs:7", |_| slow(120)).await.unwrap();
    let seen = seen.lock().unwrap();
    assert_eq!(seen.len(), 1, "{seen:?}");
    let w = &seen[0];
    assert_eq!(w.caller, "slow.rs:7");
    assert!(w.held_ms >= 100 && w.wait_local_ms < 50 && w.wait_sqlite_ms < 50, "{w:?}");
}

#[tokio::test(flavor = "multi_thread")]
async fn a_write_that_rolls_back_is_still_traced() {
    let db = Db::new("rollback");
    let (store, seen) = traced(&db, Duration::from_secs(5));
    let out: Result<(), Error> = store
        .writer
        .write("nope.rs:3", |_| {
            sleep(120);
            Err(rusqlite::Error::InvalidQuery)
        })
        .await;
    assert!(out.is_err());
    let seen = seen.lock().unwrap();
    assert!(seen.len() == 1 && seen[0].held_ms >= 100, "{seen:?}");
}

#[tokio::test(flavor = "multi_thread")]
async fn a_write_queued_behind_another_reports_the_wait() {
    let db = Db::new("queued");
    let (store, seen) = traced(&db, Duration::from_secs(5));
    let holder = tokio::spawn({
        let writer = store.writer.clone();
        async move { writer.write("holder", |_| slow(150)).await }
    });
    tokio::time::sleep(Duration::from_millis(30)).await; // the holder is running, not batched with us
    store.writer.write("waiter", |_| Ok(())).await.unwrap();
    holder.await.unwrap().unwrap();
    let seen = seen.lock().unwrap();
    let of = |caller: &str| seen.iter().filter(|w| w.caller == caller).cloned().collect::<Vec<_>>();
    let (held, waited) = (of("holder"), of("waiter"));
    assert!(held.len() == 1 && held[0].held_ms >= 100, "{seen:?}");
    assert!(waited.len() == 1 && waited[0].wait_local_ms >= 100 && waited[0].held_ms < 50, "{seen:?}");
}

/// The Python backend mid-write is another connection holding the write lock for longer
/// than the busy timeout: the Rust write waits its turn and commits.
#[tokio::test(flavor = "multi_thread")]
async fn another_writer_holding_the_lock_delays_a_write_and_does_not_fail_it() {
    let db = Db::new("busy");
    let (store, seen) = traced(&db, Duration::from_millis(100));
    store.writer.write("test", |conn| conn.execute_batch("CREATE TABLE t(x)")).await.unwrap();
    let path = db.0.clone();
    let (holding, held) = std::sync::mpsc::channel();
    let other = std::thread::spawn(move || {
        let conn = Connection::open(path).unwrap();
        conn.execute_batch("BEGIN IMMEDIATE; INSERT INTO t VALUES (1)").unwrap();
        holding.send(()).unwrap();
        sleep(600);
        conn.execute_batch("COMMIT").unwrap();
    });
    held.recv().unwrap();
    let asked = Instant::now();
    assert_eq!(store.writer.write("ours", |conn| conn.execute("INSERT INTO t VALUES (2)", [])).await, Ok(1));
    assert!(asked.elapsed() >= Duration::from_millis(400), "did not wait: {:?}", asked.elapsed());
    other.join().unwrap();
    let rows: i64 = store.readers.read(|conn| conn.query_row("SELECT count(*) FROM t", [], |r| r.get(0))).await.unwrap();
    assert_eq!(rows, 2);
    let seen = seen.lock().unwrap();
    assert!(seen.len() == 1 && seen[0].wait_sqlite_ms >= 400 && seen[0].held_ms < 100, "{seen:?}");
}

#[tokio::test(flavor = "multi_thread")]
async fn commits_are_checkpointed_into_the_database_file() {
    let db = Db::new("checkpoint");
    let store = Store::open(&db.0).unwrap();
    store
        .writer
        .write("test", |conn| conn.execute_batch("CREATE TABLE t(x); INSERT INTO t VALUES (zeroblob(200000))"))
        .await
        .unwrap();
    // Auto-checkpoint is off, so only the checkpoint thread moves pages out of the WAL.
    let deadline = Instant::now() + Duration::from_secs(5);
    while std::fs::metadata(&db.0).unwrap().len() < 200_000 {
        assert!(Instant::now() < deadline, "the WAL was never copied back");
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
}
