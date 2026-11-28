//! `file_versions` + `blobs`: per-file version history (`backend/store/repos/history.py`).
//!
//! One row per version; the text lives once in `blobs` under the same hash the change
//! ledger uses, so an agent edit recorded by both costs one copy.

use rusqlite::types::Value as Sql;
use rusqlite::{params, params_from_iter, OptionalExtension};
use serde_json::{Map, Value};

use super::ledger::{put_blob, text_or};
use super::{record, Result, Store};

const ATTRIBUTION: [&str; 9] = ["source", "run_id", "conv_id", "profile_id", "ducky_name", "model", "tool", "group_id", "coding_agent"];
pub const MAX_PER_FILE: i64 = 100;

/// Which file's history.
#[derive(Clone)]
pub struct File {
    pub project_id: String,
    pub path: String,
}

pub async fn newest(store: &Store, file: File) -> Result<Option<Map<String, Value>>> {
    store
        .readers
        .read(move |conn| {
            conn.query_row("SELECT * FROM file_versions WHERE project_id=?1 AND path=?2 ORDER BY entry_id DESC LIMIT 1", [file.project_id, file.path], record)
                .optional()
        })
        .await
}

pub async fn exists(store: &Store, file: File, entry_id: String) -> Result<bool> {
    store
        .readers
        .read(move |conn| conn.prepare("SELECT 1 FROM file_versions WHERE project_id=?1 AND path=?2 AND entry_id=?3")?.exists([file.project_id, file.path, entry_id]))
        .await
}

/// One version to store ([`put`]).
pub struct Version {
    pub entry_id: String,
    pub content: String,
    pub preview: String,
    pub saved_at: i64,
    /// Who made it: the `ATTRIBUTION` fields, each optional.
    pub attribution: Value,
    pub schema_version: i64,
    pub content_hash: Option<String>,
}

/// Store a version and keep the file's newest [`MAX_PER_FILE`]: the content's blob name.
pub async fn put(store: &Store, file: File, version: Version) -> Result<String> {
    store
        .writer
        .write("history::put", move |conn| {
            let digest = put_blob(conn, &version.content, version.content_hash.as_deref())?;
            let mut args = vec![
                Sql::Text(file.project_id.clone()),
                Sql::Text(file.path.clone()),
                Sql::Text(version.entry_id.clone()),
                Sql::Integer(version.saved_at),
                Sql::Integer(version.content.len() as i64),
                Sql::Text(version.preview.clone()),
                Sql::Text(digest.clone()),
                Sql::Integer(version.schema_version),
            ];
            args.extend(ATTRIBUTION.iter().map(|name| Sql::Text(text_or(&version.attribution[*name], ""))));
            conn.execute(
                "INSERT INTO file_versions(project_id, path, entry_id, saved_at, bytes, preview, content_hash, \
                 schema_version, source, run_id, conv_id, profile_id, ducky_name, model, tool, group_id, coding_agent) \
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) \
                 ON CONFLICT(project_id, path, entry_id) DO UPDATE SET saved_at=excluded.saved_at, bytes=excluded.bytes, \
                 preview=excluded.preview, content_hash=excluded.content_hash, schema_version=excluded.schema_version, \
                 source=excluded.source, run_id=excluded.run_id, conv_id=excluded.conv_id, profile_id=excluded.profile_id, \
                 ducky_name=excluded.ducky_name, model=excluded.model, tool=excluded.tool, group_id=excluded.group_id, \
                 coding_agent=excluded.coding_agent",
                params_from_iter(args),
            )?;
            // Entry ids are millisecond timestamps: the highest are the newest.
            conn.execute(
                "DELETE FROM file_versions WHERE project_id=?1 AND path=?2 AND entry_id NOT IN \
                 (SELECT entry_id FROM file_versions WHERE project_id=?1 AND path=?2 ORDER BY entry_id DESC LIMIT ?3)",
                params![file.project_id, file.path, MAX_PER_FILE],
            )?;
            Ok(digest)
        })
        .await
}

/// A file's versions, newest first.
pub async fn list_rows(store: &Store, file: File) -> Result<Vec<Value>> {
    store
        .readers
        .read(move |conn| {
            conn.prepare("SELECT * FROM file_versions WHERE project_id=?1 AND path=?2 ORDER BY entry_id DESC")?
                .query_map([file.project_id, file.path], |r| record(r).map(Value::Object))?
                .collect()
        })
        .await
}

pub async fn content(store: &Store, file: File, entry_id: String) -> Result<Option<String>> {
    store
        .readers
        .read(move |conn| {
            conn.query_row(
                "SELECT b.text FROM file_versions v JOIN blobs b ON b.hash = v.content_hash WHERE v.project_id=?1 AND v.path=?2 AND v.entry_id=?3",
                [file.project_id, file.path, entry_id],
                |r| r.get(0),
            )
            .optional()
        })
        .await
}
