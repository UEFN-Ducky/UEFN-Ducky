//! MCP servers, capture index, Verse diagnostics cache, Verse digest index
//! (`backend/store/repos/misc.py`).

use rusqlite::types::Value as Sql;
use rusqlite::{params, params_from_iter, Connection};
use serde_json::{json, Map, Value};

use super::pyjson::{dumps, int, py_eq, truthy};
use super::{now, Result, Store};

// --------------------------------------------------------------------------- mcp servers

fn mcp_servers(conn: &Connection) -> rusqlite::Result<Map<String, Value>> {
    let mut stmt = conn.prepare("SELECT id, block FROM mcp_servers ORDER BY id")?;
    let rows = stmt.query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)))?;
    let mut out = Map::new();
    for row in rows {
        let (id, block) = row?;
        if let Ok(block) = serde_json::from_str(&block) {
            out.insert(id, block);
        }
    }
    Ok(out)
}

pub async fn mcp_servers_get(store: &Store) -> Result<Map<String, Value>> {
    store.readers.read(mcp_servers).await
}

pub async fn mcp_servers_replace(store: &Store, servers: Map<String, Value>) -> Result<()> {
    store
        .writer
        .write("misc::mcp_servers_replace", move |conn| {
            let (current, now) = (mcp_servers(conn)?, now());
            // "A config exists" even when it holds zero servers; without this every boot
            // would seed the catalog again.
            conn.execute(
                "INSERT INTO meta(key, value, updated) VALUES ('mcp_servers_seeded', '1', ?1) ON CONFLICT(key) DO NOTHING",
                [now],
            )?;
            for id in current.keys().filter(|id| !servers.contains_key(*id)) {
                conn.execute("DELETE FROM mcp_servers WHERE id=?1", [id])?;
            }
            for (id, block) in &servers {
                if current.get(id).is_some_and(|have| py_eq(have, block)) {
                    continue;
                }
                conn.execute(
                    "INSERT INTO mcp_servers(id, block, updated) VALUES (?1, ?2, ?3) \
                     ON CONFLICT(id) DO UPDATE SET block=excluded.block, updated=excluded.updated",
                    params![id, dumps(block), now],
                )?;
            }
            Ok(())
        })
        .await
}

pub async fn mcp_servers_present(store: &Store) -> Result<bool> {
    store
        .readers
        .read(|conn| {
            Ok(conn.prepare("SELECT 1 FROM mcp_servers LIMIT 1")?.exists([])?
                || conn.prepare("SELECT 1 FROM meta WHERE key='mcp_servers_seeded'")?.exists([])?)
        })
        .await
}

// --------------------------------------------------------------------------- captures

pub async fn capture_add(store: &Store, filename: String, prefix: String, nbytes: i64) -> Result<()> {
    store
        .writer
        .write("misc::capture_add", move |conn| {
            conn.execute(
                "INSERT INTO captures(filename, prefix, bytes, created) VALUES (?1, ?2, ?3, ?4) \
                 ON CONFLICT(filename) DO UPDATE SET prefix=excluded.prefix, bytes=excluded.bytes, created=excluded.created",
                params![filename, prefix, nbytes, now()],
            )?;
            Ok(())
        })
        .await
}

/// Drop rows beyond the newest `keep`; returns the filenames dropped.
pub async fn capture_prune(store: &Store, keep: i64) -> Result<Vec<String>> {
    store
        .writer
        .write("misc::capture_prune", move |conn| {
            let stale: Vec<String> = conn
                .prepare(
                    "SELECT filename FROM captures WHERE filename NOT IN \
                     (SELECT filename FROM captures ORDER BY created DESC LIMIT ?1)",
                )?
                .query_map([keep], |row| row.get(0))?
                .collect::<rusqlite::Result<_>>()?;
            for filename in &stale {
                conn.execute("DELETE FROM captures WHERE filename=?1", [filename])?;
            }
            Ok(stale)
        })
        .await
}

pub async fn capture_names(store: &Store) -> Result<Vec<String>> {
    store.readers.read(|conn| conn.prepare("SELECT filename FROM captures")?.query_map([], |row| row.get(0))?.collect()).await
}

// --------------------------------------------------------------------------- verse diagnostics

fn diagnostics(conn: &Connection, project_id: &str) -> rusqlite::Result<Map<String, Value>> {
    let mut stmt = conn
        .prepare("SELECT path, mtime_ns, size, errors, warnings, items FROM verse_diagnostics WHERE project_id=?1")?;
    let mut rows = stmt.query([project_id])?;
    let mut out = Map::new();
    while let Some(row) = rows.next()? {
        let items: Value = serde_json::from_str(&row.get::<_, String>(5)?).unwrap_or_else(|_| json!([]));
        out.insert(
            row.get(0)?,
            json!({
                "mtime_ns": row.get::<_, i64>(1)?,
                "size": row.get::<_, i64>(2)?,
                "errors": row.get::<_, i64>(3)?,
                "warnings": row.get::<_, i64>(4)?,
                "items": items,
            }),
        );
    }
    Ok(out)
}

pub async fn diag_get(store: &Store, project_id: String) -> Result<Map<String, Value>> {
    store.readers.read(move |conn| diagnostics(conn, &project_id)).await
}

pub async fn diag_replace(store: &Store, project_id: String, files: Map<String, Value>) -> Result<()> {
    store
        .writer
        .write("misc::diag_replace", move |conn| {
            let (current, now) = (diagnostics(conn, &project_id)?, now());
            for path in current.keys().filter(|path| !files.contains_key(*path)) {
                conn.execute("DELETE FROM verse_diagnostics WHERE project_id=?1 AND path=?2", [&project_id, path])?;
            }
            for (path, entry) in &files {
                if !entry.is_object() {
                    continue;
                }
                let items = if truthy(&entry["items"]) { entry["items"].clone() } else { json!([]) };
                let slim = json!({
                    "mtime_ns": int(&entry["mtime_ns"]),
                    "size": int(&entry["size"]),
                    "errors": int(&entry["errors"]),
                    "warnings": int(&entry["warnings"]),
                    "items": items,
                });
                if current.get(path).is_some_and(|have| py_eq(have, &slim)) {
                    continue;
                }
                conn.execute(
                    "INSERT INTO verse_diagnostics(project_id, path, mtime_ns, size, errors, warnings, items, updated) \
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8) ON CONFLICT(project_id, path) DO UPDATE SET \
                     mtime_ns=excluded.mtime_ns, size=excluded.size, errors=excluded.errors, \
                     warnings=excluded.warnings, items=excluded.items, updated=excluded.updated",
                    params![
                        project_id,
                        path,
                        slim["mtime_ns"].as_i64(),
                        slim["size"].as_i64(),
                        slim["errors"].as_i64(),
                        slim["warnings"].as_i64(),
                        dumps(&slim["items"]),
                        now
                    ],
                )?;
            }
            Ok(())
        })
        .await
}

/// Changes whenever any process rewrote this project's rows: (newest write in µs, rows).
pub async fn diag_stamp(store: &Store, project_id: String) -> Result<Option<(i64, i64)>> {
    let (rows, newest): (i64, f64) = store
        .readers
        .read(move |conn| {
            conn.query_row(
                "SELECT count(*), coalesce(max(updated), 0) FROM verse_diagnostics WHERE project_id=?1",
                [project_id],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
        })
        .await?;
    Ok((rows != 0).then_some(((newest * 1_000_000.0) as i64, rows)))
}

// --------------------------------------------------------------------------- verse digests

pub async fn digest_indexed_mtime(store: &Store, path: String) -> Result<Option<f64>> {
    store
        .readers
        .read(move |conn| {
            let mut stmt = conn.prepare("SELECT mtime FROM digest_files WHERE path=?1")?;
            let mut rows = stmt.query([path])?;
            rows.next()?.map(|row| row.get(0)).transpose()
        })
        .await
}

/// (Re)index one digest: replace its rows when the file changed.
pub async fn digest_index(store: &Store, path: String, mtime: f64, lines: Vec<String>) -> Result<()> {
    store
        .writer
        .write("misc::digest_index", move |conn| {
            conn.execute("DELETE FROM digest_lines WHERE path=?1", [&path])?;
            conn.execute(
                "INSERT INTO digest_files(path, mtime, lines, indexed) VALUES (?1, ?2, ?3, ?4) \
                 ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime, lines=excluded.lines, indexed=excluded.indexed",
                params![path, mtime, lines.len() as i64, now()],
            )?;
            let mut insert = conn.prepare("INSERT INTO digest_lines(path, line, text) VALUES (?1, ?2, ?3)")?;
            for (i, line) in lines.iter().enumerate().filter(|(_, line)| !line.trim().is_empty()) {
                insert.execute(params![path, i as i64 + 1, line.trim_end_matches(['\r', '\n'])])?;
            }
            Ok(())
        })
        .await
}

/// Substring (trigram) search over indexed digest lines restricted to `paths`.
pub async fn digest_search(store: &Store, query: String, paths: Vec<String>, limit: i64) -> Result<Vec<(String, i64, String)>> {
    let q = query.trim().to_string();
    if q.chars().count() < 3 || paths.is_empty() {
        return Ok(Vec::new());
    }
    store
        .readers
        .read(move |conn| {
            let marks = vec!["?"; paths.len()].join(",");
            let sql = format!(
                "SELECT l.path, l.line, l.text FROM digest_fts f JOIN digest_lines l ON l.id = f.rowid \
                 WHERE digest_fts MATCH ? AND l.path IN ({marks}) LIMIT ?"
            );
            let phrase = Sql::Text(format!("\"{}\"", q.replace('"', "\"\"")));
            let args = std::iter::once(phrase).chain(paths.into_iter().map(Sql::Text)).chain([Sql::Integer(limit)]);
            let mut stmt = conn.prepare(&sql)?;
            let rows = stmt.query_map(params_from_iter(args), |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)))?;
            rows.collect()
        })
        .await
}
