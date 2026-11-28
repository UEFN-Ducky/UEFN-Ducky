//! `memory_entries` + `memory_fts`: project memory notes (`backend/store/repos/memory.py`).
//!
//! `name` is `entry` or `entry/sub`; `parent` is `''` or `entry`.

use rusqlite::{params, OptionalExtension};
use serde_json::{json, Map, Value};

use super::tokens::split;
use super::{record, Result, Store};

pub async fn get(store: &Store, project_id: String, name: String) -> Result<Option<Map<String, Value>>> {
    store
        .readers
        .read(move |conn| {
            conn.query_row("SELECT name, parent, description, author, updated, body FROM memory_entries WHERE project_id=?1 AND name=?2", [project_id, name], record)
                .optional()
        })
        .await
}

/// One note to store ([`put`]).
pub struct Entry {
    pub name: String,
    pub description: String,
    pub author: String,
    pub updated: String,
    pub body: String,
}

pub async fn put(store: &Store, project_id: String, entry: Entry) -> Result<()> {
    store
        .writer
        .write("memory::put", move |conn| {
            let parent = entry.name.split_once('/').map_or("", |(parent, _)| parent);
            conn.execute(
                "INSERT INTO memory_entries(project_id, name, parent, description, author, updated, body) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7) ON CONFLICT(project_id, name) DO UPDATE SET description=excluded.description, \
                 author=excluded.author, updated=excluded.updated, body=excluded.body",
                params![project_id, entry.name, parent, entry.description, entry.author, entry.updated, entry.body],
            )
            .map(|_| ())
        })
        .await
}

/// Delete a note, and with `with_subs` the notes under it: how many went.
pub async fn delete(store: &Store, project_id: String, name: String, with_subs: bool) -> Result<usize> {
    store
        .writer
        .write("memory::delete", move |conn| {
            let mut n = conn.execute("DELETE FROM memory_entries WHERE project_id=?1 AND name=?2", [&project_id, &name])?;
            if with_subs {
                n += conn.execute("DELETE FROM memory_entries WHERE project_id=?1 AND parent=?2", [&project_id, &name])?;
            }
            Ok(n)
        })
        .await
}

/// Top-level entries with their sub index, newest-updated first; no bodies.
pub async fn index(store: &Store, project_id: String) -> Result<Vec<Value>> {
    store
        .readers
        .read(move |conn| {
            let sql = "SELECT name, parent, description, author, updated, length(body) AS chars FROM memory_entries \
                       WHERE project_id=?1 ORDER BY updated DESC, name";
            let mut tops: Vec<Value> = Vec::new();
            let mut subs: Vec<(String, Value)> = Vec::new();
            let mut rows = conn.prepare(sql)?;
            let mut rows = rows.query([project_id])?;
            while let Some(r) = rows.next()? {
                let (name, parent, description, author): (String, String, String, String) = (r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?);
                if parent.is_empty() {
                    let (updated, chars): (String, Option<i64>) = (r.get(4)?, r.get(5)?);
                    tops.push(json!({"name": name, "description": description, "author": author, "updated": updated, "chars": chars.unwrap_or(0), "subs": []}));
                } else {
                    subs.push((parent, json!({"name": name, "description": description, "author": author})));
                }
            }
            // Each entry's subs by name; a sub whose entry is gone is left out.
            subs.sort_by(|(_, a), (_, b)| a["name"].as_str().cmp(&b["name"].as_str()));
            for (parent, sub) in subs {
                if let Some(under) = tops.iter_mut().find(|top| top["name"] == parent.as_str()).and_then(|top| top["subs"].as_array_mut()) {
                    under.push(sub);
                }
            }
            Ok(tops)
        })
        .await
}

pub async fn subs_of(store: &Store, project_id: String, entry: String) -> Result<Vec<Value>> {
    store
        .readers
        .read(move |conn| {
            conn.prepare("SELECT name, description FROM memory_entries WHERE project_id=?1 AND parent=?2 ORDER BY name")?
                .query_map([project_id, entry], |r| Ok(json!({"name": r.get::<_, String>(0)?, "description": r.get::<_, String>(1)?})))?
                .collect()
        })
        .await
}

/// The notes matching every word of `query`, best first: `{name, description, preview}`.
pub async fn search(store: &Store, project_id: String, query: String, limit: i64) -> Result<Vec<Value>> {
    let terms: Vec<String> = split(&query).map(|term| format!("\"{}\"", term.replace('"', "\"\""))).collect();
    if terms.is_empty() {
        return Ok(Vec::new());
    }
    store
        .readers
        .read(move |conn| {
            let sql = "SELECT m.name, m.description, snippet(memory_fts, 2, '', '', '…', 12) AS preview \
                       FROM memory_fts JOIN memory_entries m ON m.rowid = memory_fts.rowid \
                       WHERE memory_fts MATCH ?1 AND m.project_id=?2 ORDER BY bm25(memory_fts) LIMIT ?3";
            conn.prepare(sql)?.query_map(params![terms.join(" "), project_id, limit], |r| record(r).map(Value::Object))?.collect()
        })
        .await
}

pub async fn has_rows(store: &Store, project_id: String) -> Result<bool> {
    store.readers.read(move |conn| conn.prepare("SELECT 1 FROM memory_entries WHERE project_id=?1 LIMIT 1")?.exists([project_id])).await
}
