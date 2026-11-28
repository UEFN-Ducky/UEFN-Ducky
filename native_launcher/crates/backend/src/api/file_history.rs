//! A file's saved versions (`frontend/ui_web/verse_editor/file_history.py`), as the
//! editor's history list reads and adds them.
//! ponytail: the text cache the agent diff starts from (`verse_editor/io.py`) is the
//! Python backend's until the agent sync moves; `record_external_file_change` only keeps
//! the version here.

use std::sync::Arc;

use serde_json::{json, Map, Value};

use super::project_files::given;
use super::project_paths::project_slug;
use super::settings::{failed, imported, load};
use super::Api;
use crate::store::history::{self, File, Version};
use crate::store::ledger::hash;
use crate::store::now;
use crate::store::pyjson::{int, pystr, truthy};

type Fields = Map<String, Value>;

/// Who wrote a version, as the list names it.
const ATTRIBUTION: [&str; 8] = ["run_id", "conv_id", "profile_id", "ducky_name", "model", "tool", "group_id", "coding_agent"];

/// `str(value or "")`.
fn text(value: Option<&Value>) -> String {
    value.filter(|value| truthy(value)).map(pystr).unwrap_or_default()
}

/// A text argument as it was sent.
fn sent(args: &Fields, name: &str) -> String {
    args.get(name).and_then(Value::as_str).unwrap_or_default().to_string()
}

/// The history of the call's `relative_path` in the open project.
async fn file(api: &Api, args: &Fields, path_required: bool) -> Result<File, String> {
    let path = given(args, "relative_path").trim().replace('\\', "/");
    if path.is_empty() && path_required {
        return Err("relative_path is required".into());
    }
    // Old versions are files the Python backend moves into ducky.db once.
    imported(api, "ledger", "changesets").await?;
    imported(api, "ledger", "file_history").await?;
    let root = text(Some(load(api).await?.get("uefn_project_root"))).trim().to_string();
    if root.is_empty() {
        return Err("No project selected.".into());
    }
    let project_id = tokio::task::spawn_blocking(move || project_slug(&root)).await.map_err(|stopped| stopped.to_string())?;
    Ok(File { project_id, path })
}

/// `_preview_line`: the first line that is not blank, cut at 120 characters.
fn preview(content: &str) -> String {
    // What Python's `splitlines` breaks at and `strip` drops.
    let breaks = |c: char| matches!(c, '\n' | '\r' | '\x0b' | '\x0c' | '\x1c'..='\x1e') || [0x85, 0x2028, 0x2029].contains(&(c as u32));
    let blank = |c: char| c.is_whitespace() || ('\x1c'..='\x1f').contains(&c);
    content.split(breaks).map(|line| line.trim_matches(blank)).find(|line| !line.is_empty()).map(|line| line.chars().take(120).collect()).unwrap_or_default()
}

/// `_write_snapshot_rows`: keep `content` as a version of the file and answer its id.
/// Content the newest version already holds adds none (browsing the history round-trips
/// through the editor's autosave).
async fn snapshot(api: &Api, file: File, content: String) -> Result<String, String> {
    let store = api.repos.store().await.map_err(failed)?;
    let digest = hash(&content);
    if let Some(newest) = history::newest(store, file.clone()).await.map_err(failed)? {
        if text(newest.get("content_hash")) == digest {
            return Ok(text(newest.get("entry_id")));
        }
    }
    // An id is the time in milliseconds; two versions in the same one must not share it.
    let mut id = (now() * 1000.0) as i64;
    while history::exists(store, file.clone(), id.to_string()).await.map_err(failed)? {
        id += 1;
    }
    let version = Version {
        entry_id: id.to_string(),
        preview: preview(&content),
        saved_at: now() as i64,
        attribution: Value::Null,
        schema_version: 2,
        content_hash: Some(digest),
        content,
    };
    history::put(store, file, version).await.map_err(failed)?;
    Ok(id.to_string())
}

/// A file's versions, newest first, without their text.
pub async fn list_file_history(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let file = file(&api, &args, true).await?;
    let path = file.path.clone();
    let rows = history::list_rows(api.repos.store().await.map_err(failed)?, file).await.map_err(failed)?;
    let listed = |row: &Value| {
        let version = if truthy(&row["schema_version"]) { int(&row["schema_version"]) } else { 1 };
        let mut out = Map::new();
        out.insert("schema_version".into(), json!(version));
        out.insert("id".into(), json!(text(row.get("entry_id"))));
        out.insert("path".into(), json!(path));
        out.insert("saved_at".into(), json!(int(&row["saved_at"])));
        out.insert("bytes".into(), json!(int(&row["bytes"])));
        for name in ["preview", "content_hash", "source"].into_iter().chain(ATTRIBUTION) {
            out.insert(name.into(), json!(text(row.get(name))));
        }
        Value::Object(out)
    };
    Ok(rows.iter().map(listed).collect())
}

pub async fn read_file_history_entry(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let id = given(&args, "entry_id").trim().to_string();
    if id.is_empty() {
        return Err("entry_id is required".into());
    }
    let file = file(&api, &args, false).await?;
    let path = file.path.clone();
    match history::content(api.repos.store().await.map_err(failed)?, file, id.clone()).await.map_err(failed)? {
        Some(content) => Ok(json!({"content": content, "path": path, "id": id})),
        None => Err(format!("History entry not found: {id}")),
    }
}

/// The editor's text, kept so the person can come back to it after restoring an older version.
pub async fn snapshot_file_history(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let file = file(&api, &args, true).await?;
    let path = file.path.clone();
    Ok(json!({"id": snapshot(&api, file, sent(&args, "content")).await?, "path": path}))
}

/// A file changed on disk outside Ducky: keep what the editor had as a version.
pub async fn record_external_file_change(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let (before, after) = (sent(&args, "previous_content"), sent(&args, "new_content"));
    let changed = before != after;
    if changed {
        let file = file(&api, &args, true).await?;
        snapshot(&api, file, before).await?;
    }
    Ok(json!({"ok": true, "snapshotted": changed}))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_preview_is_the_first_line_with_text() {
        assert_eq!(preview("\r\n \t\n  using { /Verse.org }  \nnext"), "using { /Verse.org }");
        assert_eq!(preview(" \n\x0c\n"), "");
        assert_eq!(preview(&"é".repeat(130)).chars().count(), 120);
    }
}
