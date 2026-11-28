//! Dropping files from an island's Lore changelist (`urc/lore_host.py`): unstage and
//! reset(purge), only for pending adds. It never checks in and never edits `.lore`
//! packs. Resetting a committed path or a pending delete would put files back on disk,
//! so neither is ever dropped.
//!
//! Every call here blocks (Lore's library, the disk): run it inside `spawn_blocking`.

use std::collections::HashSet;
use std::path::Path;

use serde_json::{json, Value};

use crate::store::pyjson::pystr;

/// What reads and changes the changelist: Lore's library ([`super::lore_ffi::Ffi`]),
/// or a test's stand-in.
pub trait Backend {
    /// `{ok, paths, staged, untracked, ...}`.
    fn status(&self, root: &str) -> Result<Value, String>;
    /// Unstage and reset(purge) each path: `{ok, dropped, ...}`.
    fn drop_paths(&self, root: &str, paths: &[String]) -> Result<Value, String>;
    /// Drop a local commit that was not pushed. Never a check-in.
    fn revert_local(&self, root: &str) -> Result<Value, String>;
}

pub fn has_lore(root: &str) -> bool {
    Path::new(root).join(".lore").is_dir()
}

/// `/Project/Characters/Foo` is `Content/Characters/Foo.uasset`.
pub fn content_rel_from_asset_path(asset_path: &str) -> String {
    let mut raw = asset_path.trim().replace('\\', "/");
    if raw.starts_with('/') {
        let parts: Vec<&str> = raw.split('/').filter(|part| !part.is_empty()).collect();
        if parts.len() >= 2 {
            raw = parts[1..].join("/");
        }
    }
    let low = raw.to_lowercase();
    match (low.starts_with("content/"), low.ends_with(".uasset")) {
        (true, true) => raw,
        (true, false) => format!("{raw}.uasset"),
        (false, true) => format!("Content/{raw}"),
        (false, false) => format!("Content/{raw}.uasset"),
    }
}

fn norm(path: &str) -> String {
    path.replace('\\', "/").trim_start_matches('/').to_string()
}

fn under(rel: &str, prefixes: &[String]) -> bool {
    prefixes.is_empty() || prefixes.iter().any(|prefix| rel == prefix || rel.strip_prefix(prefix.as_str()).is_some_and(|rest| rest.starts_with('/')))
}

fn skipped(extra: Value) -> Value {
    let mut out = serde_json::Map::new();
    out.insert("ok".into(), true.into());
    out.insert("skipped".into(), "no_lore".into());
    out.extend(extra.as_object().cloned().unwrap_or_default());
    Value::Object(out)
}

/// Unstage and reset(purge) each path. Nothing to do when the island has no `.lore`.
pub fn drop_paths(backend: &dyn Backend, root: &str, paths: &[String]) -> Result<Value, String> {
    if !has_lore(root) {
        return Ok(skipped(json!({"dropped": []})));
    }
    let cleaned: Vec<String> = paths.iter().filter(|path| !path.trim().is_empty()).map(|path| norm(path)).collect();
    if cleaned.is_empty() {
        return Ok(json!({"ok": true, "dropped": []}));
    }
    backend.drop_paths(root, &cleaned)
}

pub fn lore_status(backend: &dyn Backend, root: &str) -> Result<Value, String> {
    if !has_lore(root) {
        return Ok(skipped(json!({"paths": [], "staged": [], "untracked": []})));
    }
    backend.status(root)
}

/// The paths Lore lists as added or untracked, under `prefixes` when any is given.
/// Never a pending delete.
pub fn pending_adds(backend: &dyn Backend, root: &str, prefixes: &[String]) -> Result<Vec<String>, String> {
    let status = lore_status(backend, root)?;
    let prefixes: Vec<String> = prefixes.iter().map(|prefix| norm(prefix).trim_end_matches('/').to_string()).collect();
    let mut seen = HashSet::new();
    let listed = status["untracked"].as_array().into_iter().flatten().map(|raw| norm(&pystr(raw)));
    Ok(listed.filter(|rel| !rel.is_empty() && under(rel, &prefixes) && seen.insert(rel.clone())).collect())
}

/// Drop the pending adds under `prefixes`. Committed trees are never reset.
pub fn drop_pending_adds(backend: &dyn Backend, root: &str, prefixes: &[String]) -> Result<Value, String> {
    drop_some(backend, root, prefixes, |_| true)
}

/// Drop the pending adds whose files are gone. Pending deletes stay (the editor's own).
pub fn drop_missing_orphans(backend: &dyn Backend, root: &str, prefixes: &[String]) -> Result<Value, String> {
    drop_some(backend, root, prefixes, |rel| !Path::new(root).join(rel).exists())
}

fn drop_some(backend: &dyn Backend, root: &str, prefixes: &[String], wanted: impl Fn(&String) -> bool) -> Result<Value, String> {
    if !has_lore(root) {
        return Ok(skipped(json!({"dropped": []})));
    }
    let adds: Vec<String> = pending_adds(backend, root, prefixes)?.into_iter().filter(wanted).collect();
    if adds.is_empty() {
        return Ok(json!({"ok": true, "dropped": []}));
    }
    drop_paths(backend, root, &adds)
}

/// Drop a local commit that was not pushed (`revisionRevert`, no commit). Never a check-in.
pub fn revert_local_commit(backend: &dyn Backend, root: &str) -> Result<Value, String> {
    if !has_lore(root) {
        return Ok(skipped(Value::Null));
    }
    backend.revert_local(root)
}

/// An asset the editor already removed: its pending add is dropped, never a pending delete.
pub fn after_asset_gone(backend: &dyn Backend, root: &str, asset_path: &str) -> Result<Value, String> {
    let rel = content_rel_from_asset_path(asset_path);
    let folder = rel.rsplit_once('/').map_or("Content", |(folder, _)| folder).to_string();
    let adds: Vec<String> = pending_adds(backend, root, &[folder])?.into_iter().filter(|add| under(add, std::slice::from_ref(&rel))).collect();
    if adds.is_empty() {
        return Ok(json!({"ok": true, "dropped": [], "note": "no_pending_add"}));
    }
    drop_paths(backend, root, &adds)
}

/// A changelist a test made up: it answers `status` with what the test says, and
/// drops whatever it is asked to.
struct Scripted(Value);

impl Backend for Scripted {
    fn status(&self, _root: &str) -> Result<Value, String> {
        Ok(self.0.clone())
    }

    fn drop_paths(&self, _root: &str, paths: &[String]) -> Result<Value, String> {
        Ok(json!({"ok": true, "dropped": paths}))
    }

    fn revert_local(&self, _root: &str) -> Result<Value, String> {
        Ok(json!({"ok": true, "reverted": true}))
    }
}

/// One of the calls above on a made-up changelist, for the tests that compare it with
/// Python's.
pub fn probe(what: &str, body: &Value) -> Value {
    let backend = Scripted(body["status"].clone());
    let words = |key: &str| body[key].as_array().into_iter().flatten().map(pystr).collect::<Vec<String>>();
    let (root, asset) = (body["root"].as_str().unwrap_or_default(), body["asset_path"].as_str().unwrap_or_default());
    let told = match what {
        "content_rel" => Ok(json!(content_rel_from_asset_path(asset))),
        "drop_paths" => drop_paths(&backend, root, &words("paths")),
        "status" => lore_status(&backend, root),
        "pending_adds" => pending_adds(&backend, root, &words("prefixes")).map(Value::from),
        "drop_pending_adds" => drop_pending_adds(&backend, root, &words("prefixes")),
        "drop_missing_orphans" => drop_missing_orphans(&backend, root, &words("prefixes")),
        "revert_local_commit" => revert_local_commit(&backend, root),
        "after_asset_gone" => after_asset_gone(&backend, root, asset),
        _ => Err(format!("no such call: {what}")),
    };
    told.unwrap_or_else(|error| json!({"error": error}))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api::project_paths::tests::Temp;
    use crate::api::project_paths::text;

    #[test]
    fn an_asset_path_is_its_file_under_content() {
        let told: Vec<String> = ["/Isle/Characters/Foo", " Content\\Foo.uasset ", "content/Foo", "Foo.UASSET", "/Foo"].iter().map(|path| content_rel_from_asset_path(path)).collect();
        assert_eq!(told, ["Content/Characters/Foo.uasset", "Content/Foo.uasset", "content/Foo.uasset", "Content/Foo.UASSET", "Content//Foo.uasset"]);
    }

    #[test]
    fn only_pending_adds_are_dropped_and_only_in_a_lore_island() {
        let island = Temp::new("lore-host");
        let root = text(&island.0);
        let backend = Scripted(json!({"ok": true, "untracked": ["Content/New/A.uasset", "\\Content\\New\\A.uasset", "Content/Newer/B.uasset", "Content/Kept.uasset"]}));
        // No `.lore` folder: nothing is asked of Lore.
        assert_eq!(drop_pending_adds(&backend, &root, &[]).unwrap(), json!({"ok": true, "skipped": "no_lore", "dropped": []}));
        std::fs::create_dir_all(island.0.join(".lore")).unwrap();
        island.file("Content/Kept.uasset", "x");
        assert_eq!(pending_adds(&backend, &root, &["Content/New/".to_string()]).unwrap(), ["Content/New/A.uasset"]);
        let gone = drop_missing_orphans(&backend, &root, &[]).unwrap();
        assert_eq!(gone["dropped"], json!(["Content/New/A.uasset", "Content/Newer/B.uasset"]));
        let asset = after_asset_gone(&backend, &root, "/Isle/Newer/B").unwrap();
        assert_eq!(asset, json!({"ok": true, "dropped": ["Content/Newer/B.uasset"]}));
        assert_eq!(after_asset_gone(&backend, &root, "/Isle/Other/C").unwrap()["note"], "no_pending_add");
    }
}
