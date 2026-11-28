//! The schema of `ducky.db`: numbered SQL steps applied forward, one transaction each,
//! with `PRAGMA user_version` naming the last one applied (`backend/store/db.py::migrate`).
//! The steps are the files under `ducky_app/backend/store/migrations`, built in by
//! `build.rs`. A file written by a newer app is refused, never downgraded.
#![allow(clippy::disallowed_methods)] // runs while the store opens (off the async workers) or in the one-shot `migrate` command

use std::fs::{self, File, TryLockError};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime};

use rusqlite::Connection;
use serde_json::json;

use super::{conn, now, Error, Result};

/// `(number, SQL)` of every step, 1..N without gaps.
pub const MIGRATIONS: &[(i64, &str)] = include!(concat!(env!("OUT_DIR"), "/migrations.rs"));

const OPEN_LOCK_NAME: &str = "ducky.db.open-lock";
const RESTORE_PENDING_NAME: &str = "ducky.db.restore-pending";
pub(crate) const SNAPSHOT_DIR_NAME: &str = "snapshots";
const KEEP_SNAPSHOTS: usize = 3;
/// A first open may copy a large database before it upgrades it.
pub const OPEN_LOCK_WAIT: Duration = Duration::from_secs(120);
/// Meta row: no backup could be made before this upgrade.
pub const BACKUP_FAILED_KEY: &str = "pre_migrate_backup_failed";

/// The schema version this build brings a database to.
pub fn head() -> i64 {
    MIGRATIONS.len() as i64
}

pub fn user_version(conn: &Connection) -> rusqlite::Result<i64> {
    conn.query_row("PRAGMA user_version", [], |row| row.get(0))
}

/// Apply the steps of `steps` the file does not have yet; the version it ends at.
pub fn migrate(conn: &Connection, steps: &[(i64, &str)]) -> Result<i64> {
    let from = user_version(conn)?;
    let head = steps.last().map_or(0, |step| step.0);
    if from > head {
        return Err(Error(format!(
            "ducky.db is at schema {from} but this app knows up to {head}; it was written by a newer UEFN-Ducky. \
             Update the app instead of downgrading."
        )));
    }
    if from == head {
        return Ok(from);
    }
    if from > 0 {
        // The upgrade still runs (a full disk must not keep the app from starting), but
        // never silently: stderr and a meta row the Database tab can show.
        if let Err(err) = pre_migrate_copy(conn, from) {
            eprintln!("pre-migration backup failed: {err}");
            record_backup_failure(conn, from, head, &err);
        }
    }
    for &(number, sql) in steps.iter().filter(|step| step.0 > from) {
        step(conn, number, sql)?;
    }
    Ok(head)
}

fn step(conn: &Connection, number: i64, sql: &str) -> Result<()> {
    conn.execute_batch("BEGIN IMMEDIATE")?;
    let applied = (|| {
        // Another process opening the same file may have applied this step while this
        // one waited for the write lock; running it again would fail (ADD COLUMN).
        if user_version(conn)? < number {
            // A Windows checkout holds CRLF: the schema text SQLite keeps must not.
            conn.execute_batch(&sql.replace("\r\n", "\n"))?;
            conn.pragma_update(None, "user_version", number)?;
        }
        Ok::<(), rusqlite::Error>(())
    })();
    match applied {
        Ok(()) => Ok(conn.execute_batch("COMMIT")?),
        Err(err) => {
            let _ = conn.execute_batch("ROLLBACK");
            Err(err.into())
        }
    }
}

fn folder_of(conn: &Connection) -> Result<PathBuf> {
    let folder = conn.path().and_then(|path| Path::new(path).parent());
    folder.map(Path::to_path_buf).ok_or_else(|| Error("the database has no folder".into()))
}

/// The copies under `dir` (`ducky-*.db`), newest first.
pub(crate) fn snapshots(dir: &Path) -> Vec<(PathBuf, SystemTime)> {
    let Ok(listing) = fs::read_dir(dir) else { return Vec::new() };
    let mut found: Vec<(PathBuf, SystemTime)> = listing
        .flatten()
        .filter(|entry| entry.file_name().to_str().is_some_and(|name| name.starts_with("ducky-") && name.ends_with(".db")))
        .filter_map(|entry| Some((entry.path(), entry.metadata().ok()?.modified().ok()?)))
        .collect();
    found.sort_by_key(|copy| std::cmp::Reverse(copy.1));
    found
}

/// One copy of the database before an upgrade from schema `current`. A first open that
/// fails is retried and the next process to open the file upgrades too: a recent copy
/// for this schema, made by any of them, is the same data and is reused.
fn pre_migrate_copy(conn: &Connection, current: i64) -> Result<()> {
    let folder = folder_of(conn)?;
    let label = format!("-pre-migrate-v{current}");
    let recent = snapshots(&folder.join(SNAPSHOT_DIR_NAME)).into_iter().any(|(path, modified)| {
        let name = path.file_name().unwrap_or_default().to_string_lossy().into_owned();
        // A journal beside it: a copy its process never finished.
        name.contains(&label) && modified.elapsed().unwrap_or_default() < 2 * OPEN_LOCK_WAIT && !path.with_file_name(format!("{name}-journal")).exists()
    });
    if !recent {
        vacuum_into(conn, &folder, &label)?;
    }
    Ok(())
}

pub(crate) fn vacuum_into(conn: &Connection, folder: &Path, suffix: &str) -> Result<PathBuf> {
    let dir = folder.join(SNAPSHOT_DIR_NAME);
    fs::create_dir_all(&dir)?;
    // SQLite knows the PC's local time; the name is the one Python gives its copies.
    let stamp: String = conn.query_row("SELECT strftime('%Y%m%d-%H%M%S', 'now', 'localtime')", [], |row| row.get(0))?;
    let mut target = dir.join(format!("ducky-{stamp}{suffix}.db"));
    let mut n = 1;
    while target.exists() {
        n += 1;
        target = dir.join(format!("ducky-{stamp}{suffix}-{n}.db"));
    }
    conn.execute("VACUUM INTO ?1", [target.to_string_lossy().as_ref()])?;
    for (old, _) in snapshots(&dir).into_iter().skip(KEEP_SNAPSHOTS) {
        let _ = fs::remove_file(old);
    }
    Ok(target)
}

fn record_backup_failure(conn: &Connection, from: i64, head: i64, err: &Error) {
    let note = json!({"from": from, "to": head, "error": err.0, "ts": now()});
    let saved = conn.execute("INSERT OR REPLACE INTO meta(key, value, updated) VALUES (?1, ?2, ?3)", (BACKUP_FAILED_KEY, note.to_string(), now()));
    if let Err(err) = saved {
        eprintln!("could not record the failed pre-migration backup: {err}");
    }
}

/// Hold `ducky.db.open-lock` (an OS file lock, gone if the process dies) until the file
/// is dropped. Every process holds it while it opens ducky.db for the first time, so two
/// of them never switch to WAL, upgrade or copy the file at once.
pub fn open_lock(folder: &Path, wait: Duration) -> Result<File> {
    fs::create_dir_all(folder)?;
    let file = fs::OpenOptions::new().read(true).write(true).create(true).truncate(false).open(folder.join(OPEN_LOCK_NAME))?;
    let deadline = Instant::now() + wait;
    loop {
        match file.try_lock() {
            Ok(()) => return Ok(file),
            Err(TryLockError::Error(err)) => return Err(err.into()),
            Err(TryLockError::WouldBlock) if Instant::now() > deadline => {
                return Err(Error("database is locked: another UEFN Ducky process is still opening ducky.db".into()));
            }
            Err(TryLockError::WouldBlock) => std::thread::sleep(Duration::from_millis(20)),
        }
    }
}

/// A debug build must not upgrade the person's real ducky.db past the installed app's
/// schema: the installed app then refuses the file. `DUCKY_DEV_MIGRATE_REAL_DB=1` allows it.
fn refuse_dev_upgrade(path: &Path, from: i64, to: i64) -> Result<()> {
    if !cfg!(debug_assertions) || from == 0 || from >= to || std::env::var_os("DUCKY_DEV_MIGRATE_REAL_DB").is_some_and(|v| v == "1") {
        return Ok(());
    }
    let home = PathBuf::from(std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME")).unwrap_or_default());
    let data = if cfg!(windows) {
        home.join("AppData").join("Local")
    } else if cfg!(target_os = "macos") {
        home.join("Library").join("Application Support")
    } else {
        home.join(".local").join("share")
    };
    let same = |a: &Path, b: &Path| matches!((a.canonicalize(), b.canonicalize()), (Ok(a), Ok(b)) if a == b);
    if path.parent().is_some_and(|folder| same(folder, &data.join("UEFN-Ducky"))) {
        return Err(Error(format!(
            "refusing to upgrade the real ducky.db from schema {from} to {to} from a debug build: the installed UEFN Ducky \
             could no longer open it. Point LOCALAPPDATA at a copy, or set DUCKY_DEV_MIGRATE_REAL_DB=1 to do it on purpose."
        )));
    }
    Ok(())
}

/// Bring the database at `path` to schema `to`, creating the file when it is not there.
/// The caller holds the open lock.
pub fn upgrade(path: &Path, to: i64) -> Result<i64> {
    if !(0..=head()).contains(&to) {
        return Err(Error(format!("this Ducky-Backend knows schema up to {}, not {to}", head())));
    }
    let conn = conn::open_writer(path, conn::BUSY_TIMEOUT)?;
    refuse_dev_upgrade(path, user_version(&conn)?, to)?;
    migrate(&conn, &MIGRATIONS[..to as usize])
}

/// What the backend does before it opens its store: under the open lock, bring
/// `path` to this build's schema.
pub fn ensure(path: &Path) -> Result<i64> {
    let folder = path.parent().ok_or_else(|| Error("the database has no folder".into()))?;
    let _lock = open_lock(folder, OPEN_LOCK_WAIT)?;
    // ponytail: a staged restore is still swapped in by the Python backend's first open
    // (db.py::_apply_pending_restore); opening the file here would hold the one it replaces.
    if folder.join(RESTORE_PENDING_NAME).is_file() {
        return Err(Error("a restore of ducky.db is waiting for the app to apply it".into()));
    }
    upgrade(path, head())
}

/// `Ducky-Backend migrate <ducky.db> [--to N]`: whether it worked, and the one JSON line
/// to print. Run by a process that holds the open lock (the Python backend's first open).
pub fn cli(args: &[String]) -> (bool, String) {
    let asked = match args {
        [path] => Ok((path, head())),
        [path, flag, to] if flag == "--to" => to.parse().map(|to| (path, to)).map_err(|_| Error(format!("--to {to}: not a schema number"))),
        _ => Err(Error("usage: Ducky-Backend migrate <ducky.db> [--to N]".into())),
    };
    match asked.and_then(|(path, to)| upgrade(Path::new(path), to)) {
        Ok(version) => (true, json!({"ok": true, "version": version}).to_string()),
        Err(err) => (false, json!({"ok": false, "error": err.0}).to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A folder of its own per test, removed when the test ends.
    struct Dir(PathBuf);

    impl Dir {
        fn new(name: &str) -> Dir {
            let dir = std::env::temp_dir().join(format!("ducky-rs-migrate-{}-{name}", std::process::id()));
            let _ = fs::remove_dir_all(&dir);
            fs::create_dir_all(&dir).unwrap();
            Dir(dir)
        }

        fn db(&self) -> PathBuf {
            self.0.join("ducky.db")
        }

        fn copies(&self) -> Vec<String> {
            let found = snapshots(&self.0.join(SNAPSHOT_DIR_NAME));
            found.into_iter().map(|(path, _)| path.file_name().unwrap().to_string_lossy().into_owned()).collect()
        }
    }

    impl Drop for Dir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn tables(conn: &Connection) -> i64 {
        conn.query_row("SELECT count(*) FROM sqlite_master WHERE type='table'", [], |row| row.get(0)).unwrap()
    }

    #[test]
    fn a_new_file_gets_every_step_and_a_second_run_changes_nothing() {
        let dir = Dir::new("new");
        assert!(head() >= 28);
        assert_eq!(ensure(&dir.db()), Ok(head()));
        let conn = Connection::open(dir.db()).unwrap();
        let made = tables(&conn);
        assert!(made > 20 && user_version(&conn) == Ok(head()));
        let schema = |conn: &Connection| -> String { conn.query_row("SELECT group_concat(sql, ';') FROM sqlite_master", [], |row| row.get(0)).unwrap() };
        assert!(!schema(&conn).contains('\r'));
        assert_eq!(ensure(&dir.db()), Ok(head()));
        assert_eq!(tables(&conn), made);
        // Nothing to lose yet: no copy was made.
        assert!(dir.copies().is_empty());
    }

    #[test]
    fn steps_end_at_the_asked_version_and_an_upgrade_copies_the_file_first() {
        let dir = Dir::new("to");
        assert_eq!(upgrade(&dir.db(), 3), Ok(3));
        assert!(dir.copies().is_empty());
        assert_eq!(upgrade(&dir.db(), head()), Ok(head()));
        let copies = dir.copies();
        assert!(copies.len() == 1 && copies[0].ends_with("-pre-migrate-v3.db"), "{copies:?}");
        // The copy is the database as it was.
        let copy = Connection::open(dir.0.join(SNAPSHOT_DIR_NAME).join(&copies[0])).unwrap();
        assert_eq!(user_version(&copy), Ok(3));
        assert!(upgrade(&dir.db(), head() + 1).unwrap_err().0.contains("knows schema up to"));
        assert!(upgrade(&dir.db(), 3).unwrap_err().0.contains("newer UEFN-Ducky"));
    }

    #[test]
    fn a_file_from_a_newer_app_is_refused() {
        let dir = Dir::new("newer");
        ensure(&dir.db()).unwrap();
        let conn = Connection::open(dir.db()).unwrap();
        conn.pragma_update(None, "user_version", head() + 1).unwrap();
        let err = ensure(&dir.db()).unwrap_err();
        assert!(err.0.contains(&format!("is at schema {} but this app knows up to {}", head() + 1, head())), "{err}");
    }

    #[test]
    fn retries_take_one_copy_and_only_the_newest_three_are_kept() {
        let dir = Dir::new("copies");
        upgrade(&dir.db(), 2).unwrap();
        let conn = Connection::open(dir.db()).unwrap();
        pre_migrate_copy(&conn, 7).unwrap();
        pre_migrate_copy(&conn, 7).unwrap(); // another process, or a retry
        assert_eq!(dir.copies().len(), 1);
        // A copy its process never finished is not one to rely on.
        let unfinished = dir.0.join(SNAPSHOT_DIR_NAME).join(format!("{}-journal", dir.copies()[0]));
        fs::write(&unfinished, b"").unwrap();
        pre_migrate_copy(&conn, 7).unwrap();
        assert_eq!(dir.copies().len(), 2);
        for version in 8..12 {
            pre_migrate_copy(&conn, version).unwrap();
        }
        let kept = dir.copies();
        assert!(kept.len() == KEEP_SNAPSHOTS && kept.iter().any(|name| name.contains("-pre-migrate-v11")), "{kept:?}");
    }

    #[test]
    fn a_copy_that_fails_is_recorded_and_the_upgrade_still_runs() {
        let dir = Dir::new("full-disk");
        upgrade(&dir.db(), 5).unwrap();
        fs::write(dir.0.join(SNAPSHOT_DIR_NAME), b"in the way").unwrap();
        assert_eq!(upgrade(&dir.db(), head()), Ok(head()));
        let conn = Connection::open(dir.db()).unwrap();
        let note: String = conn.query_row("SELECT value FROM meta WHERE key=?1", [BACKUP_FAILED_KEY], |row| row.get(0)).unwrap();
        let note: serde_json::Value = serde_json::from_str(&note).unwrap();
        assert!(note["from"] == 5 && note["to"] == head() && note["error"].as_str().is_some_and(|e| !e.is_empty()), "{note}");
    }

    #[test]
    fn a_step_that_fails_rolls_back_alone_and_an_applied_one_is_not_run_again() {
        let dir = Dir::new("steps");
        let conn = Connection::open(dir.db()).unwrap();
        let steps = [(1, "CREATE TABLE a(x);"), (2, "CREATE TABLE b(x);\nINSERT INTO nowhere VALUES (1);"), (3, "CREATE TABLE c(x);")];
        assert!(migrate(&conn, &steps).is_err());
        assert!(user_version(&conn) == Ok(1) && tables(&conn) == 1 && conn.is_autocommit());
        // Another process applied step 1 while this one waited: it is skipped, not repeated.
        step(&conn, 1, "CREATE TABLE a(x);").unwrap();
        assert_eq!(migrate(&conn, &steps[..1]), Ok(1));
    }

    #[test]
    fn a_second_opener_waits_for_the_first() {
        let dir = Dir::new("lock");
        let held = open_lock(&dir.0, Duration::ZERO).unwrap();
        let err = open_lock(&dir.0, Duration::from_millis(60)).unwrap_err();
        assert!(err.0.contains("still opening ducky.db"), "{err}");
        drop(held);
        open_lock(&dir.0, Duration::ZERO).unwrap();
    }

    #[test]
    fn a_staged_restore_is_left_to_the_app() {
        let dir = Dir::new("restore");
        fs::write(dir.0.join(RESTORE_PENDING_NAME), b"").unwrap();
        assert!(ensure(&dir.db()).unwrap_err().0.contains("restore"));
        assert!(!dir.db().exists());
    }

    #[test]
    fn the_command_answers_one_json_line() {
        let dir = Dir::new("cli");
        let path = dir.db().to_string_lossy().into_owned();
        assert_eq!(cli(&[path.clone(), "--to".into(), "2".into()]), (true, r#"{"ok":true,"version":2}"#.to_string()));
        assert_eq!(cli(std::slice::from_ref(&path)), (true, format!(r#"{{"ok":true,"version":{}}}"#, head())));
        assert_eq!(cli(&[path, "--to".into(), "x".into()]), (false, r#"{"ok":false,"error":"--to x: not a schema number"}"#.to_string()));
        assert!(!cli(&[]).0);
    }
}
