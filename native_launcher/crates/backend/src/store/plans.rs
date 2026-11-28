//! `plans` (project plans keyed by chat id; templates keyed by template id) and `tasks`
//! (`backend/store/repos/plans.py`).

use rusqlite::{params, OptionalExtension};
use serde_json::Value;

use super::ledger::text_or;
use super::pyjson::{dumps, float, truthy};
use super::{Error, Result, Store};

pub const TEMPLATE_PROJECT: &str = "";
/// A chat's plan lives with its ducky, in the one list, not in a project (duckies work
/// on any project, so switching the selected project must not hide a ducky's plan).
pub const ONE_LIST_PROJECT: &str = "_no_project";

fn parse(text: &str) -> rusqlite::Result<Value> {
    serde_json::from_str(text).map_err(|err| Error(format!("a plan row is not JSON: {err}")).into())
}

/// Move chat plans saved under a project into the one list: how many moved. A chat with
/// plans in several places keeps the newest there; the others stay where they are.
/// Nothing is deleted.
///
/// ponytail: Python remembers per process that it ran; here the caller calls it once at
/// start (a second run finds nothing to move).
pub async fn merge_into_one_list(store: &Store) -> Result<usize> {
    store
        .writer
        .write("plans::merge_into_one_list", |conn| {
            let plans: Vec<(String, String, Option<f64>)> = conn
                .prepare("SELECT project_id, key, updated_at FROM plans WHERE kind='project' AND project_id NOT IN (?1, ?2) ORDER BY updated_at DESC")?
                .query_map([ONE_LIST_PROJECT, TEMPLATE_PROJECT], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?
                .collect::<rusqlite::Result<_>>()?;
            let mut moved = 0;
            let mut to = conn.prepare("UPDATE plans SET project_id=?1 WHERE project_id=?2 AND key=?3")?;
            for (project_id, key, updated) in &plans {
                let have: Option<Option<f64>> =
                    conn.query_row("SELECT updated_at FROM plans WHERE project_id=?1 AND key=?2", [ONE_LIST_PROJECT, key], |r| r.get(0)).optional()?;
                match have {
                    Some(have) if have.unwrap_or(0.0) >= updated.unwrap_or(0.0) => continue,
                    // The project's plan is newer: the two swap places, both kept.
                    Some(_) => {
                        to.execute(["\0swap", ONE_LIST_PROJECT, key])?;
                        to.execute([ONE_LIST_PROJECT, project_id, key])?;
                        to.execute([project_id.as_str(), "\0swap", key])?;
                    }
                    None => {
                        to.execute([ONE_LIST_PROJECT, project_id, key])?;
                    }
                }
                moved += 1;
            }
            Ok(moved)
        })
        .await
}

pub async fn plan_get(store: &Store, project_id: String, key: String) -> Result<Option<Value>> {
    store
        .readers
        .read(move |conn| {
            let doc: Option<String> = conn.query_row("SELECT doc FROM plans WHERE project_id=?1 AND key=?2", [project_id, key], |r| r.get(0)).optional()?;
            doc.map(|doc| parse(&doc)).transpose()
        })
        .await
}

pub async fn plan_put(store: &Store, project_id: String, key: String, kind: String, doc: Value) -> Result<()> {
    store
        .writer
        .write("plans::plan_put", move |conn| {
            let updated = if truthy(&doc["updated_at"]) { &doc["updated_at"] } else { &doc["created_at"] };
            conn.execute(
                "INSERT INTO plans(project_id, key, kind, plan_id, title, status, updated_at, doc) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8) \
                 ON CONFLICT(project_id, key) DO UPDATE SET kind=excluded.kind, plan_id=excluded.plan_id, title=excluded.title, \
                 status=excluded.status, updated_at=excluded.updated_at, doc=excluded.doc",
                params![
                    project_id,
                    key,
                    kind,
                    text_or(&doc["id"], ""),
                    text_or(&doc["title"], ""),
                    text_or(&doc["status"], ""),
                    float(updated).unwrap_or(0.0),
                    dumps(&doc),
                ],
            )
            .map(|_| ())
        })
        .await
}

pub async fn plan_delete(store: &Store, project_id: String, key: String) -> Result<bool> {
    store.writer.write("plans::plan_delete", move |conn| Ok(conn.execute("DELETE FROM plans WHERE project_id=?1 AND key=?2", [project_id, key])? > 0)).await
}

/// `(key, plan)` of a project's plans of one kind, by key.
pub async fn plan_docs(store: &Store, project_id: String, kind: String) -> Result<Vec<(String, Value)>> {
    store
        .readers
        .read(move |conn| {
            conn.prepare("SELECT key, doc FROM plans WHERE project_id=?1 AND kind=?2 ORDER BY key")?
                .query_map([project_id, kind], |r| Ok((r.get(0)?, parse(&r.get::<_, String>(1)?)?)))?
                .collect()
        })
        .await
}

// --------------------------------------------------------------------------- tasks

pub async fn task_get(store: &Store, project_id: String, task_id: String) -> Result<Option<Value>> {
    store
        .readers
        .read(move |conn| {
            let doc: Option<String> = conn.query_row("SELECT doc FROM tasks WHERE project_id=?1 AND id=?2", [project_id, task_id], |r| r.get(0)).optional()?;
            doc.map(|doc| parse(&doc)).transpose()
        })
        .await
}

pub async fn task_put(store: &Store, project_id: String, doc: Value) -> Result<()> {
    if doc.get("id").is_none() {
        return Err(Error("a task needs its id".into()));
    }
    store
        .writer
        .write("plans::task_put", move |conn| {
            conn.execute(
                "INSERT INTO tasks(project_id, id, updated, doc) VALUES (?1, ?2, ?3, ?4) \
                 ON CONFLICT(project_id, id) DO UPDATE SET updated=excluded.updated, doc=excluded.doc",
                params![project_id, super::pyjson::pystr(&doc["id"]), float(&doc["updated"]).unwrap_or(0.0), dumps(&doc)],
            )
            .map(|_| ())
        })
        .await
}

/// A project's tasks, newest first.
pub async fn task_docs(store: &Store, project_id: String) -> Result<Vec<Value>> {
    store
        .readers
        .read(move |conn| {
            conn.prepare("SELECT doc FROM tasks WHERE project_id=?1 ORDER BY updated DESC, id")?.query_map([project_id], |r| parse(&r.get::<_, String>(0)?))?.collect()
        })
        .await
}
