//! The Problems list's cache (`verse_editor/lsp/diagnostics_cache.py`): per Verse file
//! of a project, the errors and warnings verse-lsp last gave it and the file's modified
//! time and size then. A file changed since, or one that had problems, is stale: it is
//! checked again. A file gone from disk goes from the cache.
//!
//! The rows are `verse_diagnostics` in ducky.db (`store::misc::diag_*`); here is what
//! is worked out from them and the disk. Every function that takes a path blocks.
//! ponytail: no copy kept in memory (Python keeps one per project and drops it when the
//! rows change under it): each call reads the rows. Keep one if a Problems read shows up
//! as slow.
#![allow(clippy::disallowed_methods)]

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

use serde_json::{json, Map, Value};

use crate::store::pyjson::{int, truthy};

/// Cache key (a path under the project, lower case, `/`) to its entry.
pub type Files = Map<String, Value>;

/// One spelling for a file's key.
/// ponytail: Rust's lower case; Python's differs on a few letters (a final sigma).
pub fn norm_key(path: &str) -> String {
    path.trim().replace('\\', "/").trim_start_matches('/').to_lowercase()
}

/// As `os.walk` goes: a folder's files as the disk lists them, then each of its folders.
fn walk(dir: &Path, root: &Path, out: &mut Vec<(String, PathBuf)>) {
    let mut folders = Vec::new();
    for entry in fs::read_dir(dir).into_iter().flatten().flatten() {
        let (path, name) = (entry.path(), entry.file_name().to_string_lossy().into_owned());
        if path.is_dir() {
            // Never into __ExternalActors__ / __ExternalObjects__ (one .uasset per actor,
            // no Verse), nor through a link.
            let link = entry.file_type().is_ok_and(|kind| kind.is_symlink());
            if !link && !name.starts_with("__External") {
                folders.push(path);
            }
        } else if name.to_lowercase().ends_with(".verse") {
            if let Ok(rel) = path.strip_prefix(root) {
                out.push((norm_key(&rel.to_string_lossy()), path));
            }
        }
    }
    for folder in folders {
        walk(&folder, root, out);
    }
}

/// Every Verse file under the project's `Content/` with its key, in the disk's order.
fn disk_list(root: &Path) -> Vec<(String, PathBuf)> {
    let mut out = Vec::new();
    let content = root.join("Content");
    if content.is_dir() {
        walk(&content, root, &mut out);
    }
    out
}

/// Every Verse file under the project's `Content/`, by key.
pub fn disk_files(root: &Path) -> BTreeMap<String, PathBuf> {
    disk_list(root).into_iter().collect()
}

/// `(modified time in ns, size)`: a file with the same pair was not edited.
pub fn fingerprint(path: &Path) -> Option<(i64, i64)> {
    let meta = fs::metadata(path).ok()?;
    let modified = meta.modified().ok()?.duration_since(UNIX_EPOCH).ok()?;
    Some((i64::try_from(modified.as_nanos()).ok()?, i64::try_from(meta.len()).ok()?))
}

fn count(entry: &Value, name: &str) -> i64 {
    if truthy(&entry[name]) { int(&entry[name]) } else { 0 }
}

fn items(entry: &Value) -> Value {
    entry["items"].as_array().map_or_else(|| json!([]), |items| json!(items))
}

/// A file needs a check when it is new or edited, or had problems (a wrong diagnosis
/// would otherwise stay until the file is edited).
fn is_stale(key: &str, path: &Path, files: &Files) -> bool {
    let Some(entry) = files.get(key).filter(|entry| entry.is_object()) else { return true };
    let fresh = fingerprint(path).is_some_and(|(mtime_ns, size)| entry["mtime_ns"].as_i64() == Some(mtime_ns) && entry["size"].as_i64() == Some(size));
    !fresh || count(entry, "errors") > 0 || count(entry, "warnings") > 0
}

/// `stale_keys`: the files that need a check, in the disk's order.
pub fn stale_keys(root: &Path, files: &Files) -> Vec<String> {
    disk_list(root).into_iter().filter(|(key, path)| is_stale(key, path, files)).map(|(key, _)| key).collect()
}

/// `_cached_file_result`: a file's kept result as the Problems list takes it.
pub fn result(key: &str, entry: &Value) -> Value {
    json!({"path": key, "errors": count(entry, "errors"), "warnings": count(entry, "warnings"), "items": items(entry)})
}

/// The entry a file's result is kept as.
pub fn entry((mtime_ns, size): (i64, i64), result: &Value) -> Value {
    json!({"mtime_ns": mtime_ns, "size": size, "errors": count(result, "errors"), "warnings": count(result, "warnings"), "items": items(result)})
}

/// Drop what is no longer on disk.
pub fn prune(root: &Path, files: &mut Files) {
    let disk = disk_files(root);
    files.retain(|key, _| disk.contains_key(key));
}

/// What the Problems list shows: the cache without deleted files, by path, and how many
/// files need a check.
pub fn for_ui(root: &Path, mut files: Files) -> Value {
    let disk = disk_files(root);
    files.retain(|key, _| disk.contains_key(key));
    let mut keys: Vec<&String> = files.iter().filter(|(_, entry)| entry.is_object()).map(|(key, _)| key).collect();
    keys.sort();
    let rows: Vec<Value> = keys.into_iter().map(|key| result(key, &files[key])).collect();
    let stale = disk.iter().filter(|(key, path)| is_stale(key, path, &files)).count();
    json!({"files": rows, "stale_count": stale, "from_cache": true})
}
