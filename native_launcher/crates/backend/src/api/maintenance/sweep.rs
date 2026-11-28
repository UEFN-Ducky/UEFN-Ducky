//! The App Data sweep (`maintain_appdata`): check ducky.db and copy it once a day, age
//! out old backups, and remove the empty folders and leftovers of older versions.
//!
//! ponytail: three parts of Python's sweep are not here and answer 0, each until its
//! owner is answered by this backend:
//! - a database that fails its check is only reported: the Python backend's first open
//!   restores the newest snapshot (`db.py::open_checked`);
//! - `db_imported`: the pre-database files are imported by the Python backend
//!   (`store/importers`);
//! - `removed_installer_cache`: the updater's own sweep, which also stops the old
//!   install's programs (`updater.sweep_installer_cache`).
//!
//! `moved_backups` is always 0: only the file stores scattered `.bak` copies.
#![allow(clippy::disallowed_methods)] // the whole module runs inside spawn_blocking

use std::collections::{HashMap, HashSet};
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::time::{Duration, UNIX_EPOCH};

use rusqlite::types::ValueRef;
use rusqlite::{Connection, OptionalExtension};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use super::disk::{safe_rmtree, PROJECT_AREAS};
use crate::api::project_paths::text;
use crate::proc;
use crate::store::conn::{open_writer, BUSY_TIMEOUT};
use crate::store::migrate::{snapshots, vacuum_into, SNAPSHOT_DIR_NAME};
use crate::store::pyjson::{dumps, float_repr};
use crate::store::{now, Error, Result};

const DAY: f64 = 24.0 * 3600.0;
const MAX_BACKUPS: usize = 10;
const MAX_BACKUP_AGE: f64 = 30.0 * DAY;
/// A prompt file this old is no running turn's.
const PROMPT_TMP_MAX_AGE: f64 = DAY;
const SNAPSHOT_EVERY: Duration = Duration::from_secs(24 * 3600);
/// Unchanged data is copied again after a week anyway.
const SNAPSHOT_UNCHANGED_MAX: Duration = Duration::from_secs(7 * 24 * 3600);
/// The full check reads every page of the file: once a week, the quick one each sweep.
const FULL_CHECK_EVERY: f64 = 7.0 * DAY;
const FULL_CHECK_KEY: &str = "last_full_integrity";
const SNAPSHOT_FINGERPRINT_KEY: &str = "snapshot_fingerprint";
/// Caches, logs and bookkeeping change on their own all day: no reason for another copy.
const FINGERPRINT_SKIP: [&str; 11] =
    ["meta", "cache_docs", "events", "usage_calls", "verse_diagnostics", "digest_files", "digest_lines", "watch_index", "workspace_state", "captures", "scope_sync"];
/// `legacy/` goes once the store passed its check on this many sweeps.
const CLEAN_BOOTS: i64 = 3;
/// Empty trees of the pre-database layout.
const LEFTOVER_ROOTS: [&str; 13] = [
    "chats", "workspace", "file_history", "changesets", "memory", "plan_templates", "uefn_plugin_cache", "uefn_plugin_prefs", "perf",
    "verse_diagnostics", "backups", "mcp_plugins", "tasks",
];

fn mtime(path: &Path) -> Option<f64> {
    Some(fs::metadata(path).ok()?.modified().ok()?.duration_since(UNIX_EPOCH).ok()?.as_secs_f64())
}

fn name_of(path: &Path) -> String {
    path.file_name().map(|name| name.to_string_lossy().into_owned()).unwrap_or_default()
}

fn entries(dir: &Path) -> impl Iterator<Item = fs::DirEntry> {
    fs::read_dir(dir).into_iter().flatten().flatten()
}

fn real_dir(entry: &fs::DirEntry) -> bool {
    entry.file_type().is_ok_and(|kind| kind.is_dir())
}

/// Every file under `dir`; a link is never followed.
fn files_under(dir: &Path) -> Vec<PathBuf> {
    let (mut found, mut stack) = (Vec::new(), vec![dir.to_path_buf()]);
    while let Some(current) = stack.pop() {
        for entry in entries(&current) {
            match entry.file_type() {
                Ok(kind) if kind.is_dir() => stack.push(entry.path()),
                Ok(kind) if kind.is_file() => found.push(entry.path()),
                _ => {}
            }
        }
    }
    found
}

/// `prune_all_backups` and `_prune_backup_leftovers`: of each file's `.bak.` copies under
/// `backups/` the newest ten of the last 30 days stay; any other file there goes after
/// 30 days (the one-time copies made around the move to ducky.db).
fn prune_backups(root: &Path) -> i64 {
    let (now, mut removed) = (now(), 0);
    let mut groups: HashMap<(PathBuf, String), Vec<(f64, PathBuf)>> = HashMap::new();
    for file in files_under(&root.join("backups")) {
        let Some(written) = mtime(&file) else { continue };
        match name_of(&file).split_once(".bak.") {
            Some((source, _stamp)) => groups.entry((file.parent().unwrap_or(root).to_path_buf(), source.to_string())).or_default().push((written, file)),
            None if written < now - MAX_BACKUP_AGE => removed += fs::remove_file(&file).is_ok() as i64,
            None => {}
        }
    }
    for mut copies in groups.into_values() {
        copies.retain(|(written, file)| {
            if now - written > MAX_BACKUP_AGE && fs::remove_file(file).is_ok() {
                removed += 1;
            }
            file.exists()
        });
        copies.sort_by(|a, b| b.0.total_cmp(&a.0));
        removed += copies.iter().skip(MAX_BACKUPS).filter(|copy| fs::remove_file(&copy.1).is_ok()).count() as i64;
    }
    removed
}

/// `_dir_has_files`. A link counts as a file: what it points at is not looked into.
fn has_files(dir: &Path) -> bool {
    entries(dir).any(|entry| !real_dir(&entry) || has_files(&entry.path()))
}

fn folders(dir: &Path) -> Vec<PathBuf> {
    entries(dir).map(|entry| entry.path()).filter(|path| path.is_dir()).collect()
}

/// `prune_empty_project_dirs`: project folders with no file in them. A chats folder
/// also stays while any account has a chat in the project (`with_chats`).
fn prune_project_dirs(root: &Path, with_chats: &HashSet<String>) -> i64 {
    let mut removed = 0;
    for (index, (area, _label)) in PROJECT_AREAS.iter().enumerate() {
        for folder in folders(&root.join(area)) {
            let kept = has_files(&folder) || (index == 0 && with_chats.contains(&name_of(&folder)));
            removed += (!kept && safe_rmtree(&folder)) as i64;
        }
    }
    removed
}

/// Remove the empty folders of `dir`, deepest first, and `dir` itself when that empties it.
fn prune_empty(dir: &Path) -> i64 {
    let mut removed: i64 = entries(dir).filter(real_dir).map(|entry| prune_empty(&entry.path())).sum();
    if fs::read_dir(dir).is_ok_and(|mut left| left.next().is_none()) && fs::remove_dir(dir).is_ok() {
        removed += 1;
    }
    removed
}

/// `prune_empty_leftover_dirs`: build scratch, day-old prompt files a killed agent host
/// left (they hold the person's messages), and the empty folders of the old layout.
fn prune_leftovers(root: &Path) -> i64 {
    let work = root.join("pyinstaller-work");
    let mut removed = (work.is_dir() && safe_rmtree(&work)) as i64;
    let tmp = root.join("coding_agents").join("tmp");
    if tmp.is_dir() {
        let cutoff = now() - PROMPT_TMP_MAX_AGE;
        // A locked one stays.
        let old = entries(&tmp).map(|entry| entry.path()).filter(|path| path.is_file() && mtime(path).is_some_and(|written| written < cutoff));
        removed += old.filter(|path| fs::remove_file(path).is_ok()).count() as i64;
        removed += (fs::read_dir(&tmp).is_ok_and(|mut left| left.next().is_none()) && fs::remove_dir(&tmp).is_ok()) as i64;
    }
    removed + LEFTOVER_ROOTS.iter().map(|name| root.join(name)).filter(|top| top.is_dir()).map(|top| prune_empty(&top)).sum::<i64>()
}

fn copy_tree(from: &Path, to: &Path) -> io::Result<()> {
    fs::create_dir_all(to)?;
    for entry in fs::read_dir(from)? {
        let entry = entry?;
        if real_dir(&entry) {
            copy_tree(&entry.path(), &to.join(entry.file_name()))?;
        } else {
            fs::copy(entry.path(), to.join(entry.file_name()))?;
        }
    }
    Ok(())
}

/// `heal_plugin_runtime_homes`: a plugin runtime that leaked to the data folder's root
/// moves into its plugin's folder.
fn heal_runtime_homes(root: &Path) -> i64 {
    let from = root.join("unity_mcp");
    if !from.is_dir() {
        return 0;
    }
    let plugin = root.join("uefn_plugins").join("unity-mcp");
    let to = plugin.join("runtime");
    if to.exists() || !plugin.is_dir() {
        let _ = fs::remove_dir_all(&from);
        return 1;
    }
    if fs::rename(&from, &to).is_ok() {
        return 1;
    }
    let copied = copy_tree(&from, &to).is_ok();
    if copied {
        let _ = fs::remove_dir_all(&from);
    }
    copied as i64
}

/// `retire_cloudflare_tunnel`: Remote Ducky connects peer to peer now. End any copy of
/// the tunnel program still running from the data folder, then delete it and its log.
/// None when it was never there; the caller then drops its saved token.
fn retire_tunnel(root: &Path) -> Option<i64> {
    let exe = root.join("bin").join("cloudflared.exe");
    if !exe.exists() {
        return None;
    }
    if cfg!(windows) {
        let marker = text(&exe).to_lowercase();
        for (pid, running) in proc::table().into_iter().filter(|(_pid, running)| running.name.eq_ignore_ascii_case("cloudflared.exe")) {
            if proc::cmdline(pid).0.join(" ").to_lowercase().contains(&marker) {
                proc::terminate(pid, &running.name);
            }
        }
    }
    let mut removed = 0;
    for path in [exe, root.join("diagnostics").join("cloudflared.log")] {
        for _try in 0..10 {
            match fs::remove_file(&path) {
                Ok(()) => {
                    removed += 1;
                    break;
                }
                Err(gone) if gone.kind() == io::ErrorKind::NotFound => break,
                // The ended process still holds the file for a moment.
                Err(_) => std::thread::sleep(Duration::from_millis(500)),
            }
        }
    }
    Some(removed)
}

fn meta(conn: &Connection, key: &str) -> rusqlite::Result<Option<String>> {
    conn.query_row("SELECT value FROM meta WHERE key=?1", [key], |row| row.get(0)).optional()
}

fn set_meta(conn: &Connection, key: &str, value: &str) -> rusqlite::Result<()> {
    conn.execute("INSERT OR REPLACE INTO meta(key, value, updated) VALUES (?1, ?2, ?3)", (key, value, now())).map(drop)
}

/// `startup_check`: "ok" or what failed.
fn check(conn: &Connection) -> rusqlite::Result<String> {
    let ask = |pragma: &str| conn.query_row(pragma, [], |row| row.get::<_, String>(0));
    let quick = ask("PRAGMA quick_check")?;
    let due = meta(conn, FULL_CHECK_KEY).ok().flatten().and_then(|at| at.parse::<f64>().ok()).is_none_or(|at| now() - at >= FULL_CHECK_EVERY);
    if quick == "ok" && !due {
        return Ok(quick);
    }
    let full = ask("PRAGMA integrity_check")?;
    if full == "ok" {
        set_meta(conn, FULL_CHECK_KEY, &float_repr(now()))?;
    }
    Ok(full)
}

/// `data_fingerprint`: row count, highest rowid and newest `updated` of every table that
/// holds the person's data, hashed. The Python backend makes the same text, so either
/// one sees that the other's copy is still current.
/// ponytail: text in an `updated` column that is not ASCII prints as it is (Python
/// escapes it); every `updated` is a number.
pub fn fingerprint(conn: &Connection) -> rusqlite::Result<String> {
    let tables: Vec<(String, String)> = conn
        .prepare("SELECT name, coalesce(sql, '') FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")?
        .query_map([], |row| Ok((row.get(0)?, row.get::<_, String>(1)?.to_uppercase())))?
        .collect::<rusqlite::Result<_>>()?;
    let search: Vec<&str> = tables.iter().filter(|(_name, sql)| sql.starts_with("CREATE VIRTUAL TABLE")).map(|(name, _sql)| name.as_str()).collect();
    let mut summary = Vec::new();
    for (name, sql) in &tables {
        // A search index and the tables it keeps for itself.
        if FINGERPRINT_SKIP.contains(&name.as_str()) || search.iter().any(|index| name == index || name.starts_with(&format!("{index}_"))) {
            continue;
        }
        let mut asked = vec!["count(*)"];
        if !sql.contains("WITHOUT ROWID") {
            asked.push("max(rowid)");
        }
        if conn.prepare(&format!("SELECT 1 FROM pragma_table_info('{name}') WHERE name='updated'"))?.exists([])? {
            asked.push("max(updated)");
        }
        let mut row = vec![json!(name)];
        conn.query_row(&format!("SELECT {} FROM \"{name}\"", asked.join(", ")), [], |found| {
            for column in 0..asked.len() {
                row.push(match found.get_ref(column)? {
                    ValueRef::Integer(n) => json!(n),
                    ValueRef::Real(n) => json!(n),
                    ValueRef::Text(text) => json!(String::from_utf8_lossy(text)),
                    ValueRef::Null | ValueRef::Blob(_) => Value::Null,
                });
            }
            Ok(())
        })?;
        summary.push(Value::Array(row));
    }
    Ok(Sha256::digest(dumps(&Value::Array(summary)).as_bytes()).iter().map(|byte| format!("{byte:02x}")).collect())
}

/// `unchanged_since_snapshot`: `newest` is the copy the last fingerprint was saved for
/// and the data still matches it.
fn unchanged_since(conn: &Connection, newest: &Path) -> rusqlite::Result<bool> {
    let saved = meta(conn, SNAPSHOT_FINGERPRINT_KEY)?.and_then(|saved| serde_json::from_str::<Value>(&saved).ok()).unwrap_or(Value::Null);
    Ok(saved.is_object() && saved["name"] == json!(name_of(newest)) && saved["fingerprint"] == json!(fingerprint(conn)?))
}

/// `_retire_legacy_after_clean_boots`.
fn retire_legacy(conn: &Connection, root: &Path) -> rusqlite::Result<i64> {
    let legacy = root.join("legacy");
    if !legacy.is_dir() {
        return Ok(0);
    }
    let boots = meta(conn, "clean_boots")?.and_then(|boots| boots.trim().parse::<i64>().ok()).unwrap_or(0) + 1;
    set_meta(conn, "clean_boots", &boots.to_string())?;
    if boots < CLEAN_BOOTS {
        return Ok(0);
    }
    let _ = fs::remove_dir_all(&legacy);
    Ok(1)
}

fn maintain_store(root: &Path, out: &mut Map<String, Value>) -> Result<()> {
    let conn = open_writer(&root.join("ducky.db"), BUSY_TIMEOUT)?;
    let result = check(&conn).unwrap_or_else(|failed| format!("error: {failed}"));
    if result != "ok" {
        return Err(Error(format!("it failed its check ({result}); the app restores the newest snapshot when it next starts")));
    }
    out["db_checked"] = json!(1);
    set_meta(&conn, "last_integrity", &dumps(&json!({"result": "ok", "ts": now()})))?;
    out.insert("db_imported".into(), json!(0));
    out.insert("legacy_removed".into(), json!(retire_legacy(&conn, root)?));
    let newest = snapshots(&root.join(SNAPSHOT_DIR_NAME)).into_iter().next();
    let age = newest.as_ref().map(|(_copy, written)| written.elapsed().unwrap_or_default());
    if age.is_none_or(|age| age > SNAPSHOT_EVERY) {
        // While the data has not changed, a new copy would only push an older restore point out.
        let unchanged = match (&newest, age) {
            (Some((copy, _written)), Some(age)) if age < SNAPSHOT_UNCHANGED_MAX => unchanged_since(&conn, copy)?,
            _ => false,
        };
        if unchanged {
            out.insert("db_snapshot_skipped".into(), json!(1));
        } else {
            // The fingerprint first: a write landing in between only makes the next sweep see a change.
            let taken_at = fingerprint(&conn)?;
            let copy = vacuum_into(&conn, root, "-daily")?;
            set_meta(&conn, SNAPSHOT_FINGERPRINT_KEY, &dumps(&json!({"name": name_of(&copy), "fingerprint": taken_at})))?;
            out["db_snapshot"] = json!(1);
        }
    }
    Ok(())
}

/// The sweep's result, and whether the tunnel program was found (its saved token goes too).
pub fn run(root: &Path, with_chats: &HashSet<String>) -> (Value, bool) {
    let mut store = Map::new();
    store.insert("db_checked".into(), json!(0));
    store.insert("db_snapshot".into(), json!(0));
    // Never fails the sweep: the files are still tidied.
    if let Err(failed) = maintain_store(root, &mut store) {
        eprintln!("ducky.db maintenance failed: {failed}");
    }
    if store["db_checked"] == json!(1) {
        // Python's import step (`_prune_empty_legacy_parents`) takes the empty folders of
        // the old layout before anything is counted: the count is of what the sweep found.
        for name in LEFTOVER_ROOTS {
            prune_empty(&root.join(name));
        }
    }
    let pruned = prune_backups(root);
    let folders = prune_project_dirs(root, with_chats) + prune_leftovers(root) + heal_runtime_homes(root);
    let tunnel = retire_tunnel(root);
    let mut result = Map::new();
    for (name, n) in [("moved_backups", 0), ("pruned_backups", pruned), ("removed_project_dirs", folders), ("removed_installer_cache", 0), ("removed_cloudflare_tunnel", tunnel.unwrap_or(0))] {
        result.insert(name.into(), json!(n));
    }
    result.extend(store);
    (Value::Object(result), tunnel.is_some())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn old_copies_and_empty_folders_go_and_the_rest_stays() {
        let root = std::env::temp_dir().join(format!("ducky-sweep-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        crate::store::migrate::ensure(&root.join("ducky.db")).unwrap();
        let file = |rel: &str| {
            fs::create_dir_all(root.join(rel).parent().unwrap()).unwrap();
            fs::write(root.join(rel), "x").unwrap();
        };
        for n in 0..12 {
            file(&format!("backups/a/config.json.bak.{n:02}"));
        }
        for kept in ["backups/other.json.bak.1", "backups/copied/data.bin", "chats/projects/with_file/run.json", "file_history/used/v1.verse", "coding_agents/tmp/prompt.txt"] {
            file(kept);
        }
        for empty in ["chats/projects/empty", "chats/projects/has_chat", "workspace/projects/empty/deep", "perf/a/b", "pyinstaller-work/build", "tasks"] {
            fs::create_dir_all(root.join(empty)).unwrap();
        }
        let (result, tunnel) = run(&root, &HashSet::from(["has_chat".to_string()]));
        assert!(!tunnel && result["removed_cloudflare_tunnel"] == 0 && result["moved_backups"] == 0, "{result}");
        // Two of twelve copies of one file; a fresh database is checked and copied.
        assert_eq!((&result["pruned_backups"], &result["db_checked"], &result["db_snapshot"]), (&json!(2), &json!(1), &json!(1)), "{result}");
        // Only pyinstaller-work is counted: the empty folders of the old layout go before the count.
        assert_eq!(result["removed_project_dirs"], 1, "{result}");
        for gone in ["chats/projects/empty", "chats/projects/has_chat", "workspace", "perf", "tasks", "pyinstaller-work"] {
            assert!(!root.join(gone).exists(), "{gone}");
        }
        assert_eq!(files_under(&root.join("backups")).len(), 12);
        for kept in ["chats/projects/with_file/run.json", "file_history/used/v1.verse", "coding_agents/tmp/prompt.txt", "ducky.db"] {
            assert!(root.join(kept).exists(), "{kept}");
        }
        assert_eq!(snapshots(&root.join(SNAPSHOT_DIR_NAME)).len(), 1);
        // A second sweep has nothing to do: the copy is today's.
        let again = run(&root, &HashSet::from(["has_chat".to_string()])).0;
        assert_eq!((&again["pruned_backups"], &again["removed_project_dirs"], &again["db_snapshot"]), (&json!(0), &json!(0), &json!(0)), "{again}");
        fs::remove_dir_all(&root).unwrap();
    }
}
