//! `runs` / `run_entries` / `run_seen` / `path_index` / `blobs` / `watch_index`: what
//! the change journal stores (`backend/store/repos/ledger.py`). `project_id` is the
//! ledger folder name the journal derives (the project slug).
//!
//! ponytail: Python's panel process still sweeps the runs an earlier start left open on
//! its first ledger use; this backend does it only when asked (`close_stale_runs`),
//! until it is the one that starts runs.

use std::sync::LazyLock;

use rusqlite::types::Value as Sql;
use rusqlite::{params, params_from_iter, Connection, OptionalExtension};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use super::pyjson::{dumps, float, float_repr, int, py_eq, pystr, truthy};
use super::{now, Error, Result, Store};

/// When this process started: a run older than it was left `running` by an earlier one.
pub static STARTED: LazyLock<f64> = LazyLock::new(now);

/// The first 16 hex characters of a SHA-256: an entry's change hash and a blob's name.
fn hash(text: &str) -> String {
    Sha256::digest(text.as_bytes()).iter().take(8).map(|byte| format!("{byte:02x}")).collect()
}

/// Python's `str(value or default)`.
fn text_or(value: &Value, default: &str) -> String {
    if truthy(value) { pystr(value) } else { default.to_string() }
}

/// A JSON value as sqlite3 binds it.
fn sql(value: &Value) -> Sql {
    match value {
        Value::Null => Sql::Null,
        Value::Bool(b) => Sql::Integer(i64::from(*b)),
        Value::Number(n) => n.as_i64().map_or_else(|| Sql::Real(n.as_f64().unwrap_or(0.0)), Sql::Integer),
        Value::String(s) => Sql::Text(s.clone()),
        other => Sql::Text(dumps(other)),
    }
}

fn marks(n: usize) -> String {
    vec!["?"; n].join(",")
}

fn parse(text: &str) -> rusqlite::Result<Value> {
    serde_json::from_str(text).map_err(|err| Error(format!("a ledger row is not JSON: {err}")).into())
}

// --------------------------------------------------------------------------- runs

/// Runs still `running` that nobody will finish become `interrupted` with `ended = at`:
/// those started before `started_before` (default: this process's start), or exactly
/// `run_ids` (a dead agent host's runs). Answers how many.
pub async fn close_stale_runs(store: &Store, at: Option<f64>, started_before: Option<f64>, run_ids: Option<Vec<String>>) -> Result<usize> {
    if run_ids.as_ref().is_some_and(Vec::is_empty) {
        return Ok(0);
    }
    let at = at.unwrap_or_else(now);
    let mut args = vec![Sql::Real(at), Sql::Real(at)];
    let which = match run_ids {
        None => {
            args.push(Sql::Real(started_before.unwrap_or(*STARTED)));
            "started < ?".to_string()
        }
        Some(ids) => {
            let which = format!("run_id IN ({})", marks(ids.len()));
            args.extend(ids.into_iter().map(Sql::Text));
            which
        }
    };
    let sql = format!(
        "UPDATE runs SET status='interrupted', ended=?, doc=json_set(doc, '$.status', 'interrupted', '$.ended', ?) \
         WHERE status='running' AND {which}"
    );
    store.writer.write("ledger::close_stale_runs", move |conn| conn.execute(&sql, params_from_iter(args))).await
}

/// A run as the journal knows it: its document, entries in order and seen map.
fn assemble(conn: &Connection, run_id: &str, doc: &str) -> rusqlite::Result<Value> {
    let Value::Object(mut run) = parse(doc)? else {
        return Err(Error("a run document is not an object".into()).into());
    };
    run.insert("run_id".into(), json!(run_id));
    let entries = conn
        .prepare_cached("SELECT doc FROM run_entries WHERE run_id=?1 ORDER BY seq")?
        .query_map([run_id], |r| r.get::<_, String>(0))?
        .map(|doc| parse(&doc?))
        .collect::<rusqlite::Result<Vec<Value>>>()?;
    run.insert("entries".into(), Value::Array(entries));
    let mut seen = Map::new();
    let mut rows = conn.prepare_cached("SELECT path, doc FROM run_seen WHERE run_id=?1")?;
    for row in rows.query_map([run_id], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))? {
        let (path, doc) = row?;
        seen.insert(path, parse(&doc)?);
    }
    run.insert("seen".into(), Value::Object(seen));
    Ok(Value::Object(run))
}

pub async fn run_get(store: &Store, project_id: String, run_id: String) -> Result<Option<Value>> {
    store
        .readers
        .read(move |conn| {
            let doc: Option<String> =
                conn.query_row("SELECT doc FROM runs WHERE run_id=?1 AND project_id=?2", params![run_id, project_id], |r| r.get(0)).optional()?;
            doc.map(|doc| assemble(conn, &run_id, &doc)).transpose()
        })
        .await
}

/// `{run_id, conv_id}` of the runs in `statuses`, without loading their entries.
pub async fn runs_with_status(store: &Store, project_id: String, statuses: Vec<String>) -> Result<Vec<Value>> {
    if statuses.is_empty() {
        return Ok(Vec::new());
    }
    store
        .readers
        .read(move |conn| {
            let sql = format!(
                "SELECT run_id, conv_id FROM runs WHERE project_id=? AND status IN ({}) ORDER BY started DESC",
                marks(statuses.len())
            );
            let args = std::iter::once(project_id).chain(statuses);
            conn.prepare(&sql)?
                .query_map(params_from_iter(args), |r| Ok(json!({"run_id": r.get::<_, String>(0)?, "conv_id": r.get::<_, String>(1)?})))?
                .collect()
        })
        .await
}

/// Running runs in every project with the files they changed since `since`:
/// `{run_id, project_id, conv_id, group_id, ducky_name, files, last_ts}`, newest first.
/// One query; what lets a chat see what the others are changing right now, and where.
pub async fn active_work(store: &Store, since: f64, max_files: usize) -> Result<Vec<Value>> {
    store
        .readers
        .read(move |conn| {
            let sql = "SELECT r.run_id, r.project_id, r.conv_id, r.group_id, json_extract(r.doc, '$.ducky_name') AS ducky, \
                       e.path, MAX(e.ts) AS ts FROM runs r JOIN run_entries e ON e.run_id = r.run_id \
                       WHERE r.status='running' AND e.outcome='ok' AND e.ts>=?1 \
                       GROUP BY r.run_id, e.path ORDER BY ts DESC";
            let mut out: Vec<Value> = Vec::new();
            let mut rows = conn.prepare(sql)?;
            let mut rows = rows.query([since])?;
            while let Some(r) = rows.next()? {
                let run_id: String = r.get(0)?;
                let at = match out.iter().position(|run| run["run_id"] == run_id.as_str()) {
                    Some(at) => at,
                    None => {
                        // Python's `str(name or "")`.
                        let ducky = match r.get::<_, Sql>(4)? {
                            Sql::Text(name) => name,
                            Sql::Integer(n) if n != 0 => n.to_string(),
                            Sql::Real(n) if n != 0.0 => float_repr(n),
                            _ => String::new(),
                        };
                        out.push(json!({
                            "run_id": run_id, "project_id": r.get::<_, String>(1)?, "conv_id": r.get::<_, String>(2)?,
                            "group_id": r.get::<_, String>(3)?, "ducky_name": ducky, "files": [], "last_ts": r.get::<_, f64>(6)?,
                        }));
                        out.len() - 1
                    }
                };
                if let Some(files) = out[at]["files"].as_array_mut().filter(|files| files.len() < max_files) {
                    files.push(json!(r.get::<_, String>(5)?));
                }
            }
            Ok(out)
        })
        .await
}

/// Every run document in a project, newest first.
pub async fn runs_docs(store: &Store, project_id: String) -> Result<Vec<Value>> {
    store
        .readers
        .read(move |conn| {
            let runs: Vec<(String, String)> = conn
                .prepare("SELECT run_id, doc FROM runs WHERE project_id=?1 ORDER BY started DESC")?
                .query_map([project_id], |r| Ok((r.get(0)?, r.get(1)?)))?
                .collect::<rusqlite::Result<_>>()?;
            runs.iter().map(|(run_id, doc)| assemble(conn, run_id, doc)).collect()
        })
        .await
}

/// `{run_id, started, status, archived}` per run, newest first: what retention needs.
pub async fn runs_brief(store: &Store, project_id: String) -> Result<Vec<Value>> {
    store
        .readers
        .read(move |conn| {
            conn.prepare("SELECT run_id, started, status, archived FROM runs WHERE project_id=?1 ORDER BY started DESC")?
                .query_map([project_id], |r| {
                    Ok(json!({
                        "run_id": r.get::<_, String>(0)?, "started": r.get::<_, Option<f64>>(1)?.unwrap_or(0.0),
                        "status": r.get::<_, String>(2)?, "archived": r.get::<_, Option<i64>>(3)?.unwrap_or(0) != 0,
                    }))
                })?
                .collect()
        })
        .await
}

/// Upsert the run row; sync entries by content hash; replace the seen map.
pub async fn run_put(store: &Store, project_id: String, run: Value) -> Result<()> {
    let Value::String(run_id) = &run["run_id"] else {
        return Err(Error("a run needs its run_id".into()));
    };
    let run_id = run_id.clone();
    store
        .writer
        .write("ledger::run_put", move |conn| {
            let owner: Option<String> = conn.query_row("SELECT project_id FROM runs WHERE run_id=?1", [&run_id], |r| r.get(0)).optional()?;
            if owner.is_some_and(|owner| owner != project_id) {
                return Err(Error("Changeset run belongs to another project.".into()).into());
            }
            let doc: Map<String, Value> = run
                .as_object()
                .into_iter()
                .flatten()
                .filter(|(key, _)| !matches!(key.as_str(), "entries" | "seen"))
                .map(|(key, value)| (key.clone(), value.clone()))
                .collect();
            conn.execute(
                "INSERT INTO runs(run_id, project_id, conv_id, group_id, started, ended, status, archived, doc) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9) ON CONFLICT(run_id) DO UPDATE SET project_id=excluded.project_id, \
                 conv_id=excluded.conv_id, group_id=excluded.group_id, started=excluded.started, ended=excluded.ended, \
                 status=excluded.status, archived=excluded.archived, doc=excluded.doc",
                params![
                    run_id,
                    project_id,
                    text_or(&run["conv_id"], ""),
                    text_or(&run["group_id"], ""),
                    float(&run["started"]).unwrap_or(0.0),
                    sql(&run["ended"]),
                    text_or(&run["status"], "running"),
                    i64::from(truthy(&run["archived"])),
                    dumps(&Value::Object(doc)),
                ],
            )?;
            let existing: Vec<(i64, String)> = conn
                .prepare_cached("SELECT seq, hash FROM run_entries WHERE run_id=?1")?
                .query_map([&run_id], |r| Ok((r.get(0)?, r.get(1)?)))?
                .collect::<rusqlite::Result<_>>()?;
            let mut wanted = Vec::new();
            for entry in run["entries"].as_array().into_iter().flatten() {
                let seq = int(&entry["seq"]);
                wanted.push(seq);
                let body = dumps(entry);
                let h = hash(&body);
                if existing.iter().any(|(at, old)| *at == seq && *old == h) {
                    continue;
                }
                conn.prepare_cached(
                    "INSERT INTO run_entries(run_id, seq, path, ts, outcome, before_blob, after_blob, hash, doc) \
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9) ON CONFLICT(run_id, seq) DO UPDATE SET path=excluded.path, \
                     ts=excluded.ts, outcome=excluded.outcome, before_blob=excluded.before_blob, \
                     after_blob=excluded.after_blob, hash=excluded.hash, doc=excluded.doc",
                )?
                .execute(params![
                    run_id,
                    seq,
                    text_or(&entry["path"], ""),
                    float(&entry["ts"]).unwrap_or(0.0),
                    text_or(&entry["outcome"], "ok"),
                    text_or(&entry["before_blob"], ""),
                    text_or(&entry["after_blob"], ""),
                    h,
                    body,
                ])?;
            }
            for (seq, _) in existing.iter().filter(|(seq, _)| !wanted.contains(seq)) {
                conn.execute("DELETE FROM run_entries WHERE run_id=?1 AND seq=?2", params![run_id, seq])?;
            }
            conn.execute("DELETE FROM run_seen WHERE run_id=?1", [&run_id])?;
            for (path, stamp) in run["seen"].as_object().into_iter().flatten() {
                conn.prepare_cached("INSERT INTO run_seen(run_id, path, doc) VALUES (?1, ?2, ?3)")?.execute(params![run_id, path, dumps(stamp)])?;
            }
            Ok(())
        })
        .await
}

pub async fn run_delete(store: &Store, project_id: String, run_id: String) -> Result<bool> {
    store
        .writer
        .write("ledger::run_delete", move |conn| {
            conn.execute("DELETE FROM run_entries WHERE run_id=?1", [&run_id])?;
            conn.execute("DELETE FROM run_seen WHERE run_id=?1", [&run_id])?;
            Ok(conn.execute("DELETE FROM runs WHERE run_id=?1 AND project_id=?2", params![run_id, project_id])? > 0)
        })
        .await
}

/// run_id -> `{conv_id, group_id, started, archived}` of a project's runs.
pub async fn catalog(store: &Store, project_id: String) -> Result<Map<String, Value>> {
    store
        .readers
        .read(move |conn| {
            conn.prepare("SELECT run_id, conv_id, group_id, started, archived FROM runs WHERE project_id=?1")?
                .query_map([project_id], |r| {
                    let run = json!({
                        "conv_id": r.get::<_, String>(1)?, "group_id": r.get::<_, String>(2)?,
                        "started": r.get::<_, f64>(3)?, "archived": r.get::<_, Option<i64>>(4)?.unwrap_or(0) != 0,
                    });
                    Ok((r.get::<_, String>(0)?, run))
                })?
                .collect()
        })
        .await
}

/// Newest entry touching any of `paths` since `since`.
pub async fn latest_entry_for_path(store: &Store, project_id: String, paths: Vec<String>, since: f64) -> Result<Option<Value>> {
    if paths.is_empty() {
        return Ok(None);
    }
    store
        .readers
        .read(move |conn| {
            let sql = format!(
                "SELECT e.doc FROM run_entries e JOIN runs r ON r.run_id = e.run_id \
                 WHERE r.project_id=? AND e.path IN ({}) AND e.ts>=? ORDER BY e.ts DESC LIMIT 1",
                marks(paths.len())
            );
            let args = std::iter::once(Sql::Text(project_id)).chain(paths.into_iter().map(Sql::Text)).chain([Sql::Real(since)]);
            let doc: Option<String> = conn.query_row(&sql, params_from_iter(args), |r| r.get(0)).optional()?;
            doc.map(|doc| parse(&doc)).transpose()
        })
        .await
}

// --------------------------------------------------------------------------- path index

fn index_rows(conn: &Connection, project_id: &str) -> rusqlite::Result<Map<String, Value>> {
    let mut out = Map::new();
    let mut rows = conn.prepare_cached("SELECT path, doc FROM path_index WHERE project_id=?1")?;
    for row in rows.query_map([project_id], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))? {
        let (path, doc) = row?;
        out.insert(path, parse(&doc)?);
    }
    Ok(out)
}

pub async fn index_get(store: &Store, project_id: String) -> Result<Map<String, Value>> {
    store.readers.read(move |conn| index_rows(conn, &project_id)).await
}

/// Set the index to exactly `index`, touching only rows that changed.
pub async fn index_replace(store: &Store, project_id: String, index: Map<String, Value>) -> Result<()> {
    store
        .writer
        .write("ledger::index_replace", move |conn| {
            let current = index_rows(conn, &project_id)?;
            for path in current.keys().filter(|path| !index.contains_key(*path)) {
                conn.execute("DELETE FROM path_index WHERE project_id=?1 AND path=?2", params![project_id, path])?;
            }
            for (path, stamp) in index.iter().filter(|(path, stamp)| !current.get(*path).is_some_and(|old| py_eq(old, stamp))) {
                conn.prepare_cached(
                    "INSERT INTO path_index(project_id, path, doc) VALUES (?1, ?2, ?3) \
                     ON CONFLICT(project_id, path) DO UPDATE SET doc=excluded.doc",
                )?
                .execute(params![project_id, path, dumps(stamp)])?;
            }
            Ok(())
        })
        .await
}

// --------------------------------------------------------------------------- blobs

/// Store a text under its content hash (or `digest`): the name it is read back by.
pub async fn blob_put(store: &Store, text: String, digest: Option<String>) -> Result<String> {
    let name = digest.filter(|digest| !digest.is_empty()).unwrap_or_else(|| hash(&text));
    let stored = name.clone();
    store
        .writer
        .write("ledger::blob_put", move |conn| {
            conn.execute("INSERT OR IGNORE INTO blobs(hash, text, size, created) VALUES (?1, ?2, ?3, ?4)", params![stored, text, text.len() as i64, now()])
        })
        .await?;
    Ok(name)
}

pub async fn blob_get(store: &Store, digest: String) -> Result<Option<String>> {
    if digest.is_empty() {
        return Ok(None);
    }
    store.readers.read(move |conn| conn.query_row("SELECT text FROM blobs WHERE hash=?1", [digest], |r| r.get(0)).optional()).await
}

pub async fn blob_exists(store: &Store, digest: String) -> Result<bool> {
    store.readers.read(move |conn| conn.prepare_cached("SELECT 1 FROM blobs WHERE hash=?1")?.exists([digest])).await
}

/// Drop blobs no run entry or file version references.
pub async fn sweep_blobs(store: &Store) -> Result<usize> {
    // ponytail: one statement; slice it as events::trim does when a `db_write` perf row
    // names this caller.
    store
        .writer
        .write("ledger::sweep_blobs", |conn| {
            conn.execute(
                "DELETE FROM blobs WHERE hash NOT IN (SELECT before_blob FROM run_entries WHERE before_blob<>'') \
                 AND hash NOT IN (SELECT after_blob FROM run_entries WHERE after_blob<>'') \
                 AND hash NOT IN (SELECT content_hash FROM file_versions)",
                [],
            )
        })
        .await
}

// --------------------------------------------------------------------------- watch index

fn watch_rows(conn: &Connection, project_id: &str) -> rusqlite::Result<Map<String, Value>> {
    conn.prepare_cached("SELECT path, hash FROM watch_index WHERE project_id=?1")?
        .query_map([project_id], |r| Ok((r.get::<_, String>(0)?, Value::String(r.get(1)?))))?
        .collect()
}

pub async fn watch_get(store: &Store, project_id: String) -> Result<Map<String, Value>> {
    store.readers.read(move |conn| watch_rows(conn, &project_id)).await
}

/// Set the watch index to exactly `seen` (path -> hash), touching only rows that changed.
pub async fn watch_replace(store: &Store, project_id: String, seen: Map<String, Value>) -> Result<()> {
    store
        .writer
        .write("ledger::watch_replace", move |conn| {
            let current = watch_rows(conn, &project_id)?;
            for path in current.keys().filter(|path| !seen.contains_key(*path)) {
                conn.execute("DELETE FROM watch_index WHERE project_id=?1 AND path=?2", params![project_id, path])?;
            }
            for (path, digest) in seen.iter().filter(|(path, digest)| current.get(*path) != Some(*digest)) {
                conn.prepare_cached(
                    "INSERT INTO watch_index(project_id, path, hash) VALUES (?1, ?2, ?3) \
                     ON CONFLICT(project_id, path) DO UPDATE SET hash=excluded.hash",
                )?
                .execute(params![project_id, path, sql(digest)])?;
            }
            Ok(())
        })
        .await
}
