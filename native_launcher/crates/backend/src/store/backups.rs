//! Copies of ducky.db kept beside it (`backend/store/sealed_files.py`, `db.py`):
//! snapshots, files a restore replaced, corrupt ones. Those made before an account's
//! rows were all sealed hold its chats in plaintext, so they are sealed for it.
//!
//! Every function here blocks (file and SQLite work): callers run them on a blocking
//! thread, never on an async worker.
#![allow(clippy::disallowed_methods)]

use std::fs::{self, Metadata};
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use rusqlite::types::ValueRef;
use rusqlite::{Connection, OpenFlags};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use super::pyjson::{dumps, dumps_ascii};
use super::sealed_file::{is_sealed_file, is_tmp, keep_times, seal_file, write_whole};
use super::{migrate, now, Result};

const DB_NAME: &str = "ducky.db";
const SNAPSHOTS: &str = "snapshots";
const VACUUM_PENDING_NAME: &str = "ducky.db.vacuum-pending";
/// Caches, logs and bookkeeping change on their own all day; a change there alone is
/// no reason for another copy of the database.
const FINGERPRINT_SKIP: [&str; 11] = [
    "meta", "cache_docs", "events", "usage_calls", "verse_diagnostics", "digest_files", "digest_lines", "watch_index", "workspace_state", "captures",
    "scope_sync",
];

/// Every copy of ducky.db under `root`.
pub fn files(root: &Path) -> Vec<PathBuf> {
    let listed = |dir: PathBuf, keep: &dyn Fn(&str) -> bool| -> Vec<PathBuf> {
        let entries = fs::read_dir(dir).into_iter().flatten().flatten().map(|entry| entry.path());
        entries.filter(|path| path.is_file() && !is_tmp(path) && keep(&path.file_name().unwrap_or_default().to_string_lossy())).collect()
    };
    let mut out = listed(root.join(SNAPSHOTS), &|name| name.starts_with("ducky-") && name.ends_with(".db"));
    out.extend(listed(root.to_path_buf(), &|name| name.starts_with("ducky.db.replaced-") || (name.starts_with(DB_NAME) && name.ends_with(".corrupt"))));
    out
}

fn seconds(at: std::io::Result<SystemTime>) -> Option<f64> {
    Some(at.ok()?.duration_since(UNIX_EPOCH).ok()?.as_secs_f64())
}

/// When a file was made (a snapshot's copy starts then; sealing it keeps its modified
/// time). Off Windows that is the inode's change time: it is never earlier than the
/// file and, unlike the modified time, can't be set back.
/// ponytail: a rename or chmod moves the change time later; the birth time if that bites.
fn made(meta: &Metadata) -> Option<f64> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        Some(meta.ctime() as f64 + meta.ctime_nsec() as f64 / 1e9)
    }
    #[cfg(not(unix))]
    {
        seconds(meta.created())
    }
}

/// `(made, seconds since it was last written)` of a copy that is still plain.
fn plain(path: &Path, now: f64) -> Option<(f64, f64)> {
    let meta = fs::metadata(path).ok()?;
    let times = (made(&meta)?, now - seconds(meta.modified())?);
    (!is_sealed_file(path)).then_some(times)
}

/// A copy from before `made_before`, still plain, skipped only for being fresh.
pub fn waiting(root: &Path, made_before: f64, settle_s: f64) -> bool {
    let now = now();
    files(root).iter().filter_map(|path| plain(path, now)).any(|(made, idle)| made < made_before && idle < settle_s)
}

/// Seal for `account` every copy of ducky.db started before `made_before`; a snapshot
/// keeps only `account`'s plugin search records. One written in the last `settle_s`
/// seconds may still be being written: it is left for the next run. Returns how many
/// were sealed.
pub fn seal(root: &Path, account: &str, public: &[u8; 32], made_before: f64, settle_s: f64) -> usize {
    let now = now();
    let snapshots = fs::canonicalize(root.join(SNAPSHOTS)).ok();
    let mut sealed = 0;
    for path in files(root) {
        let Some((made, idle)) = plain(&path, now) else { continue };
        if made >= made_before || idle < settle_s {
            continue;
        }
        let snapshot = snapshots.is_some() && path.parent().and_then(|dir| fs::canonicalize(dir).ok()) == snapshots;
        let done = (|| {
            if snapshot {
                strip_foreign_records(&path, account)?;
            }
            seal_file(&path, account, public)
        })();
        // In use or unreadable: the next pass tries again.
        sealed += matches!(done, Ok(true)) as usize;
    }
    sealed
}

/// Before a snapshot from before sealing is sealed for `owner`: other accounts' plugin
/// search records go from it (each account's plugins write their own again), so `owner`
/// can't read them from it. Keeps the file's times. Returns how many went.
fn strip_foreign_records(path: &Path, owner: &str) -> Result<i64> {
    const THEIRS: &str = "kind='record' AND substr(scope, 1, length(?1) + 1) <> ?1 || '~'";
    let times = fs::metadata(path)?;
    let conn = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_WRITE)?;
    if !conn.query_row("SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_docs')", [], |r| r.get::<_, bool>(0))? {
        return Ok(0);
    }
    let theirs: i64 = conn.query_row(&format!("SELECT count(*) FROM search_docs WHERE {THEIRS}"), [owner], |r| r.get(0))?;
    if theirs == 0 {
        return Ok(0);
    }
    conn.execute_batch("PRAGMA secure_delete=ON; BEGIN IMMEDIATE")?;
    conn.execute(&format!("DELETE FROM search_vecs WHERE doc_id IN (SELECT id FROM search_docs WHERE {THEIRS})"), [owner])?;
    conn.execute(&format!("DELETE FROM search_docs WHERE {THEIRS}"), [owner])?;
    conn.execute_batch("COMMIT; INSERT INTO search_fts(search_fts) VALUES('optimize'); VACUUM")?;
    drop(conn);
    keep_times(path, &times);
    Ok(theirs)
}

/// Rebuild ducky.db at the next start: every value of an account is sealed now, and
/// only a rebuild drops the plaintext the file still holds in free pages and page
/// slack from before.
pub fn request_vacuum(root: &Path) -> Result<()> {
    let marker = root.join(VACUUM_PENDING_NAME);
    if !marker.is_file() {
        fs::write(marker, dumps(&json!({"waits": 0, "failures": 0})))?;
    }
    Ok(())
}

fn cell(value: ValueRef) -> Value {
    match value {
        ValueRef::Integer(n) => json!(n),
        ValueRef::Real(n) => json!(n),
        ValueRef::Text(text) => json!(String::from_utf8_lossy(text)),
        // ponytail: no table keeps a blob in `updated`; Python would print its bytes.
        ValueRef::Null | ValueRef::Blob(_) => Value::Null,
    }
}

/// Row count, highest rowid and newest `updated` of every table holding the person's
/// data, hashed (`db.data_fingerprint`). Equal fingerprints mean no row was added,
/// removed or stamped as updated in between; search indexes and caches are left out.
fn fingerprint(conn: &Connection) -> rusqlite::Result<String> {
    let tables: Vec<(String, String)> = conn
        .prepare("SELECT name, coalesce(sql, '') FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")?
        .query_map([], |r| Ok((r.get(0)?, r.get::<_, String>(1)?.to_uppercase())))?
        .collect::<rusqlite::Result<_>>()?;
    let is_virtual = |name: &str| tables.iter().any(|(table, sql)| sql.starts_with("CREATE VIRTUAL TABLE") && (name == table || name.starts_with(&format!("{table}_"))));
    let mut summary = Vec::new();
    for (name, sql) in &tables {
        if FINGERPRINT_SKIP.contains(&name.as_str()) || is_virtual(name) {
            continue;
        }
        let columns: Vec<String> = conn.prepare(&format!("PRAGMA table_info(\"{name}\")"))?.query_map([], |r| r.get(1))?.collect::<rusqlite::Result<_>>()?;
        let mut asked = vec!["count(*)"];
        if !sql.contains("WITHOUT ROWID") {
            asked.push("max(rowid)");
        }
        if columns.iter().any(|column| column == "updated") {
            asked.push("max(updated)");
        }
        let mut row = vec![json!(name)];
        conn.query_row(&format!("SELECT {} FROM \"{name}\"", asked.join(", ")), [], |r| {
            for at in 0..asked.len() {
                row.push(cell(r.get_ref(at)?));
            }
            Ok(())
        })?;
        summary.push(Value::Array(row));
    }
    Ok(Sha256::digest(dumps_ascii(&Value::Array(summary))).iter().map(|byte| format!("{byte:02x}")).collect())
}

/// A consistent copy of ducky.db under `snapshots/` (`db.snapshot`), with the
/// fingerprint the data had just before: `{"name", "fingerprint"}`, kept in `meta` so
/// the daily snapshot is skipped while nothing changed.
pub fn snapshot(root: &Path, label: &str) -> Result<Value> {
    let conn = Connection::open_with_flags(root.join(DB_NAME), OpenFlags::SQLITE_OPEN_READ_WRITE)?;
    conn.busy_timeout(Duration::from_secs(5))?;
    let fingerprint = fingerprint(&conn)?;
    let target = migrate::vacuum_into(&conn, root, &format!("-{label}"))?;
    Ok(json!({"name": target.file_name().unwrap_or_default().to_string_lossy(), "fingerprint": fingerprint}))
}

/// The old `panel_settings.json` (the copy from before settings were rows) drops `keys`.
pub fn strip_settings_file(root: &Path, keys: &[&str]) {
    let path = root.join("panel_settings.json");
    let Some(Value::Object(mut data)) = fs::read(&path).ok().and_then(|raw| serde_json::from_slice(&raw).ok()) else { return };
    if !keys.iter().any(|key| data.contains_key(*key)) {
        return;
    }
    data.retain(|key, _| !keys.contains(&key.as_str()));
    // ponytail: text outside ASCII is written as it is (Python escapes it); both read it the same.
    if let Ok(text) = serde_json::to_vec_pretty(&data) {
        let _ = write_whole(&path, &text);
    }
}
