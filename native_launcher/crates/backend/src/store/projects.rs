//! `projects`: one row per project (UEFN island or plain folder) Ducky has opened
//! (`backend/store/repos/projects.py`). `id` is the project slug every project-scoped
//! store uses.
//!
//! ponytail: the slug comes from the caller. Python makes it from the resolved path
//! (`chats.store.project_slug`); it is ported with the chat store that owns it.

use rusqlite::{params, OptionalExtension};

use super::{now, Result, Store};

pub const MAX_RECENT: i64 = 20;

pub async fn recent_paths(store: &Store, limit: i64) -> Result<Vec<String>> {
    store
        .readers
        .read(move |conn| conn.prepare("SELECT path FROM projects WHERE deleted=0 ORDER BY last_opened DESC, path LIMIT ?1")?.query_map([limit], |r| r.get(0))?.collect())
        .await
}

/// Every saved project (no recent cap), newest first: `(path, last_opened)`.
pub async fn opened_rows(store: &Store) -> Result<Vec<(String, f64)>> {
    store
        .readers
        .read(|conn| {
            conn.prepare("SELECT path, last_opened FROM projects WHERE deleted=0 ORDER BY last_opened DESC, path")?
                .query_map([], |r| Ok((r.get(0)?, r.get::<_, Option<f64>>(1)?.unwrap_or(0.0))))?
                .collect()
        })
        .await
}

/// Projects the person removed: discovery keeps them out of the project list.
pub async fn removed_paths(store: &Store) -> Result<Vec<String>> {
    store.readers.read(|conn| conn.prepare("SELECT path FROM projects WHERE deleted=1")?.query_map([], |r| r.get(0))?.collect()).await
}

/// One open of a project ([`touch`]).
pub struct Open {
    pub slug: String,
    pub path: String,
    pub name: String,
    pub when: Option<f64>,
    /// 'uefn' or 'folder'; anything else keeps the row's kind.
    pub kind: String,
}

/// Record an open. The kind is stored on the first open and may upgrade a folder to an
/// island later; an island never becomes a folder here.
pub async fn touch(store: &Store, open: Open) -> Result<()> {
    store
        .writer
        .write("projects::touch", move |conn| {
            let at = open.when.unwrap_or_else(now);
            let kind = if matches!(open.kind.as_str(), "uefn" | "folder") { open.kind.as_str() } else { "" };
            conn.execute(
                "INSERT INTO projects(id, path, name, last_opened, created, deleted, kind) \
                 VALUES (?1, ?2, ?3, ?4, ?4, 0, COALESCE(NULLIF(?5, ''), 'uefn')) \
                 ON CONFLICT(id) DO UPDATE SET path=excluded.path, last_opened=excluded.last_opened, \
                 deleted=0, name=CASE WHEN excluded.name='' THEN projects.name ELSE excluded.name END, \
                 kind=CASE WHEN projects.kind='uefn' OR ?5='' THEN projects.kind ELSE excluded.kind END",
                params![open.slug, open.path, open.name, at, kind],
            )
            .map(|_| ())
        })
        .await
}

/// The stored kind ('' when Ducky never recorded the project). Removed rows still answer.
pub async fn kind_for(store: &Store, slug: String) -> Result<String> {
    let kind: Option<Option<String>> = store.readers.read(move |conn| conn.query_row("SELECT kind FROM projects WHERE id=?1", [slug], |r| r.get(0)).optional()).await?;
    Ok(kind.flatten().unwrap_or_default())
}

/// Every project's stored kind by its slug, removed rows too ('' when none was recorded).
pub async fn kinds(store: &Store) -> Result<Vec<(String, String)>> {
    store
        .readers
        .read(|conn| conn.prepare("SELECT id, COALESCE(kind, '') FROM projects")?.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?.collect())
        .await
}

/// Drop from the recent list (the row stays, marked deleted, so history keeps its key).
/// A discovered island never opened gets a deleted row so discovery leaves it out.
pub async fn forget(store: &Store, slug: String, path: String) -> Result<()> {
    store
        .writer
        .write("projects::forget", move |conn| {
            conn.execute(
                "INSERT INTO projects(id, path, name, last_opened, created, deleted) VALUES (?1, ?2, '', 0, ?3, 1) \
                 ON CONFLICT(id) DO UPDATE SET deleted=1",
                params![slug, path, now()],
            )
            .map(|_| ())
        })
        .await
}

/// The importer's entry: an ordered list of `(slug, path)` becomes descending
/// last_opened stamps.
pub async fn replace_recent(store: &Store, projects: Vec<(String, String)>) -> Result<()> {
    store
        .writer
        .write("projects::replace_recent", move |conn| {
            let base = now();
            for (index, (slug, path)) in projects.iter().enumerate() {
                conn.execute(
                    "INSERT INTO projects(id, path, name, last_opened, created, deleted) VALUES (?1, ?2, '', ?3, ?3, 0) \
                     ON CONFLICT(id) DO UPDATE SET path=excluded.path, last_opened=excluded.last_opened, deleted=0",
                    params![slug, path, base - index as f64],
                )?;
            }
            Ok(())
        })
        .await
}

pub async fn has_rows(store: &Store) -> Result<bool> {
    store.readers.read(|conn| conn.prepare("SELECT 1 FROM projects LIMIT 1")?.exists([])).await
}
