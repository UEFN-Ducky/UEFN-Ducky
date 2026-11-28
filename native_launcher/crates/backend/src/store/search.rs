//! `search_docs` + `search_fts` + `search_vecs`: the skill and tool search index
//! (`backend/store/repos/search.py`).
//!
//! Skill, sub-skill, section and tool rows are derived data, so every write here is an
//! idempotent upsert keyed by `doc_key`. Plugin records have no other source: a rebuild
//! keeps them. A doc is a JSON object with the fields of Python's `Doc`.
//!
//! ponytail: `data_version` is not here (it is one connection's counter, and reads come
//! from a pool); `revision_token` says the same across processes.

use std::collections::HashMap;

use aes_gcm::aead::{Aead, KeyInit, Payload};
use aes_gcm::{Aes256Gcm, Nonce};
use blake2::digest::consts::U32;
use blake2::digest::Mac;
use blake2::Blake2bMac;
use rusqlite::types::Value as Sql;
use rusqlite::{params, params_from_iter, Connection, OptionalExtension, Row};
use serde_json::{json, Map, Value};
use unicode_properties::{GeneralCategoryGroup, UnicodeGeneralCategory};

use super::duckies::Who;
use super::pyjson::{dumps, pystr, sorted, truthy};
use super::{now, seal, tokens, Error, Result, Store};

pub const KINDS: [&str; 5] = ["pack", "subskill", "section", "tool", "record"];
/// bm25 column weights: title, heading path, body, summary.
const BM25: &str = "bm25(search_fts, 3.0, 2.0, 1.0, 2.5)";
const MAX_QUERY_TOKENS: usize = 16;
/// Words that match nearly every row; OR-ing them in only adds noise to the candidates.
const STOP: &str = "a an and are as at be by can do does for from how i in into is it its me my of on or our so that the \
                    their them then there these this to up we what when where which why will with you your";
/// How a sealed plugin record's `meta` starts (`backend/search/sealed_records.py`).
const SEALED_PREFIX: &str = "{\"sealed\": \"enc1:";
const VEC_MAGIC: &[u8] = b"UDV1";

/// A doc's text columns, in table order after `doc_key`.
const TEXTS: [&str; 13] = [
    "source_key", "kind", "origin", "plugin_id", "pack_id", "subskill_id", "section", "tool_name", "scope", "title", "path", "body", "summary",
];
const INSERT: &str = "INSERT INTO search_docs(doc_key, source_key, kind, origin, plugin_id, pack_id, subskill_id, section, tool_name, \
                      scope, title, path, body, summary, meta, content_hash, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)";
const UPDATE: &str = "UPDATE search_docs SET source_key=?, kind=?, origin=?, plugin_id=?, pack_id=?, subskill_id=?, section=?, \
                      tool_name=?, scope=?, title=?, path=?, body=?, summary=?, meta=?, content_hash=?, updated=? WHERE id=?";
const SET_VEC: &str = "INSERT INTO search_vecs(doc_id, model, vec) VALUES (?1, ?2, ?3) \
                       ON CONFLICT(doc_id) DO UPDATE SET model=excluded.model, vec=excluded.vec";

fn field(doc: &Value, name: &str) -> String {
    doc[name].as_str().unwrap_or_default().to_string()
}

/// The 17 columns of a doc's row.
fn row(doc: &Value) -> Vec<Sql> {
    let meta = if doc["meta"].is_object() { sorted(&doc["meta"]) } else { json!({}) };
    let mut out = vec![Sql::Text(field(doc, "doc_key"))];
    out.extend(TEXTS.iter().map(|name| Sql::Text(field(doc, name))));
    out.extend([Sql::Text(dumps(&meta)), Sql::Text(field(doc, "content_hash")), Sql::Real(now())]);
    out
}

fn insert(conn: &Connection, doc: &Value) -> rusqlite::Result<i64> {
    conn.prepare_cached(INSERT)?.execute(params_from_iter(row(doc)))?;
    Ok(conn.last_insert_rowid())
}

/// Rewrite a row whose text changed; its vector was made from the old text.
fn update(conn: &Connection, doc: &Value, id: i64) -> rusqlite::Result<()> {
    let mut args = row(doc);
    args.remove(0);
    args.push(Sql::Integer(id));
    conn.prepare_cached(UPDATE)?.execute(params_from_iter(args))?;
    conn.execute("DELETE FROM search_vecs WHERE doc_id=?1", [id]).map(|_| ())
}

fn marks(n: usize) -> String {
    vec!["?"; n].join(", ")
}

/// doc_key -> `[id, content_hash, has a vector for model]` for one source.
pub async fn existing(store: &Store, source_key: String, model: String) -> Result<Map<String, Value>> {
    store
        .readers
        .read(move |conn| {
            let sql = "SELECT d.id, d.doc_key, d.content_hash, (v.doc_id IS NOT NULL) AS has_vec FROM search_docs d \
                       LEFT JOIN search_vecs v ON v.doc_id = d.id AND v.model = ?1 WHERE d.source_key = ?2";
            conn.prepare(sql)?
                .query_map(params![model, source_key], |r| {
                    Ok((r.get::<_, String>(1)?, json!([r.get::<_, i64>(0)?, r.get::<_, String>(2)?, r.get::<_, bool>(3)?])))
                })?
                .collect()
        })
        .await
}

/// One source's rows to store ([`write_source`]).
pub struct Source {
    pub source_key: String,
    pub docs: Vec<Value>,
    /// doc_key -> float32 bytes.
    pub vectors: HashMap<String, Vec<u8>>,
    pub model: String,
    pub revision: String,
    /// Delete the source's rows that are not in `docs` or `keep`.
    pub replace: bool,
    pub keep: Vec<String>,
}

/// Upsert a source's docs and vectors in one transaction: `{added, changed, removed}`.
/// Unchanged rows are not rewritten.
pub async fn write_source(store: &Store, source: Source) -> Result<Value> {
    store
        .writer
        .write("search::write_source", move |conn| {
            let Source { source_key, docs, vectors, model, revision, replace, keep } = &source;
            let have: HashMap<String, (i64, String)> = conn
                .prepare_cached("SELECT id, doc_key, content_hash FROM search_docs WHERE source_key=?1")?
                .query_map([source_key], |r| Ok((r.get(1)?, (r.get(0)?, r.get(2)?))))?
                .collect::<rusqlite::Result<_>>()?;
            let (mut added, mut changed, mut removed) = (0, 0, 0);
            for doc in docs {
                let key = field(doc, "doc_key");
                let id = match have.get(&key) {
                    None => {
                        // A doc_key can move between sources (a pack moved from AppData to a plugin).
                        conn.execute("DELETE FROM search_docs WHERE doc_key=?1", [&key])?;
                        added += 1;
                        insert(conn, doc)?
                    }
                    Some((id, hash)) => {
                        if *hash != field(doc, "content_hash") {
                            update(conn, doc, *id)?;
                            changed += 1;
                        }
                        *id
                    }
                };
                if let Some(vec) = vectors.get(&key) {
                    conn.prepare_cached(SET_VEC)?.execute(params![id, model, vec])?;
                }
            }
            if *replace {
                let kept = |key: &String| keep.contains(key) || docs.iter().any(|doc| doc["doc_key"] == key.as_str());
                for (id, _) in have.iter().filter(|(key, _)| !kept(key)).map(|(_, row)| row) {
                    conn.execute("DELETE FROM search_docs WHERE id=?1", [id])?;
                    removed += 1;
                }
            }
            conn.execute(
                "INSERT INTO search_state(source_key, revision, indexed) VALUES (?1, ?2, ?3) \
                 ON CONFLICT(source_key) DO UPDATE SET revision=excluded.revision, indexed=excluded.indexed",
                params![source_key, revision, now()],
            )?;
            Ok(json!({"added": added, "changed": changed, "removed": removed}))
        })
        .await
}

fn parsed_meta(raw: &str) -> Value {
    serde_json::from_str(if raw.is_empty() { "{}" } else { raw }).unwrap_or_else(|_| json!({}))
}

/// Rows with no vector for `model` (a model change), as docs. Sealed plugin records are
/// left out: their text is sealed.
pub async fn missing_vectors(store: &Store, model: String, limit: i64) -> Result<Vec<Value>> {
    store
        .readers
        .read(move |conn| {
            let sql = format!(
                "SELECT d.doc_key, d.{}, d.meta, d.content_hash FROM search_docs d \
                 LEFT JOIN search_vecs v ON v.doc_id = d.id AND v.model = ?1 \
                 WHERE v.doc_id IS NULL AND NOT (d.kind = 'record' AND substr(d.meta, 1, {}) = ?2) LIMIT ?3",
                TEXTS.join(", d."),
                SEALED_PREFIX.len()
            );
            conn.prepare(&sql)?
                .query_map(params![model, SEALED_PREFIX, limit.max(1)], |r| {
                    let mut doc = Map::new();
                    for name in std::iter::once("doc_key").chain(TEXTS).chain(["content_hash"]) {
                        doc.insert(name.into(), json!(r.get::<_, String>(name)?));
                    }
                    doc.insert("meta".into(), parsed_meta(&r.get::<_, Option<String>>("meta")?.unwrap_or_default()));
                    Ok(Value::Object(doc))
                })?
                .collect()
        })
        .await
}

/// doc_key -> vector for rows that already exist: how many were stored.
///
/// With `hashes` (doc_key -> the content_hash the vector was made from) a row whose
/// text changed while the vector was being made is skipped: its writer stored its own
/// vector, or the next fill makes one.
pub async fn set_vectors(store: &Store, vectors: Vec<(String, Vec<u8>)>, model: String, hashes: Option<HashMap<String, String>>) -> Result<usize> {
    if vectors.is_empty() {
        return Ok(0);
    }
    store
        .writer
        .write("search::set_vectors", move |conn| {
            let mut n = 0;
            for (key, vec) in &vectors {
                let row: Option<(i64, String)> = conn
                    .prepare_cached("SELECT id, content_hash FROM search_docs WHERE doc_key=?1")?
                    .query_row([key], |r| Ok((r.get(0)?, r.get(1)?)))
                    .optional()?;
                let Some((id, hash)) = row else { continue };
                if hashes.as_ref().is_some_and(|hashes| hashes.get(key) != Some(&hash)) {
                    continue;
                }
                conn.prepare_cached(SET_VEC)?.execute(params![id, model, vec])?;
                n += 1;
            }
            Ok(n)
        })
        .await
}

/// doc_key -> owning desktop plugin id ('' for core and MCP tools) of one source.
pub async fn tool_owners(store: &Store, source_key: String) -> Result<Map<String, Value>> {
    store
        .readers
        .read(move |conn| {
            conn.prepare("SELECT doc_key, plugin_id FROM search_docs WHERE source_key=?1")?
                .query_map([source_key], |r| Ok((r.get::<_, String>(0)?, json!(r.get::<_, Option<String>>(1)?.unwrap_or_default()))))?
                .collect()
        })
        .await
}

const STATE: &str = "SELECT d.id, d.content_hash, (v.doc_id IS NOT NULL) FROM search_docs d \
                     LEFT JOIN search_vecs v ON v.doc_id = d.id AND v.model = ?1 WHERE d.doc_key = ?2";

/// `(content_hash, has a vector for model)` of one row, or None when it does not exist.
pub async fn record_state(store: &Store, doc_key: String, model: String) -> Result<Option<(String, bool)>> {
    store.readers.read(move |conn| conn.query_row(STATE, params![model, doc_key], |r| Ok((r.get(1)?, r.get(2)?))).optional()).await
}

/// Inside a write: plaintext plugin records (from before they were sealed) are about to
/// be replaced or deleted. Their old words stay in the file's free space and in the
/// keyword index until the one rebuild after sealing, so ask for it.
fn note_plain_records(conn: &Connection, filter: &str, args: &[Sql]) -> rusqlite::Result<()> {
    let sql = format!(
        "INSERT OR REPLACE INTO meta(key, value, updated) SELECT 'vacuum_wanted', '1', ? WHERE EXISTS (\
         SELECT 1 FROM search_docs WHERE ({filter}) AND kind='record' AND substr(meta, 1, {}) <> ?)",
        SEALED_PREFIX.len()
    );
    let args = std::iter::once(Sql::Real(now())).chain(args.iter().cloned()).chain([Sql::Text(SEALED_PREFIX.into())]);
    conn.execute(&sql, params_from_iter(args)).map(|_| ())
}

/// Delete the rows `filter` names: how many went.
fn delete_where(conn: &Connection, filter: &str, args: &[Sql]) -> rusqlite::Result<usize> {
    note_plain_records(conn, filter, args)?;
    conn.execute(&format!("DELETE FROM search_docs WHERE {filter}"), params_from_iter(args))
}

fn gone_key(plugin_id: &str) -> String {
    format!("gone:{plugin_id}")
}

/// One record row: insert or update in place, vector alongside. False when the row
/// already holds this text (and a vector for `model`). `max_in_source` caps a new row's
/// source inside the write, so writers in several processes cannot pass the cap together.
pub async fn upsert_one(store: &Store, doc: Value, vec: Option<Vec<u8>>, model: String, max_in_source: Option<i64>) -> Result<bool> {
    store
        .writer
        .write("search::upsert_one", move |conn| {
            let (plugin, hash) = (field(&doc, "plugin_id"), field(&doc, "content_hash"));
            // A record write from a plugin whose uninstall marked it gone.
            if doc["kind"] == "record" && !plugin.is_empty() && conn.prepare_cached("SELECT 1 FROM search_meta WHERE key=?1")?.exists([gone_key(&plugin)])? {
                return Err(Error(format!("plugin {plugin} was uninstalled; its search records are gone")).into());
            }
            let row: Option<(i64, String, bool)> =
                conn.query_row(STATE, params![model, field(&doc, "doc_key")], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?))).optional()?;
            let id = match row {
                None => {
                    if let Some(max) = max_in_source {
                        let n: i64 = conn.query_row("SELECT count(*) FROM search_docs WHERE source_key=?1", [field(&doc, "source_key")], |r| r.get(0))?;
                        if n >= max {
                            return Err(Error(format!("search holds at most {max} records per plugin; delete some first")).into());
                        }
                    }
                    insert(conn, &doc)?
                }
                Some((_, old, has_vec)) if old == hash && (has_vec || vec.is_none()) => return Ok(false),
                Some((id, old, _)) => {
                    if old != hash {
                        note_plain_records(conn, "id=?", &[Sql::Integer(id)])?;
                        update(conn, &doc, id)?;
                    }
                    id
                }
            };
            if let Some(vec) = &vec {
                conn.execute(SET_VEC, params![id, model, vec])?;
            }
            Ok(true)
        })
        .await
}

pub async fn find_doc(store: &Store, doc_key: String) -> Result<Option<i64>> {
    store.readers.read(move |conn| conn.query_row("SELECT id FROM search_docs WHERE doc_key=?1", [doc_key], |r| r.get(0)).optional()).await
}

pub async fn count_source(store: &Store, source_key: String) -> Result<i64> {
    store.readers.read(move |conn| conn.query_row("SELECT count(*) FROM search_docs WHERE source_key=?1", [source_key], |r| r.get(0))).await
}

pub async fn delete_docs(store: &Store, doc_keys: Vec<String>) -> Result<usize> {
    if doc_keys.is_empty() {
        return Ok(0);
    }
    store
        .writer
        .write("search::delete_docs", move |conn| doc_keys.iter().map(|key| delete_where(conn, "doc_key=?", &[Sql::Text(key.clone())])).sum())
        .await
}

/// Delete the sources `filter` names with their index state: how many rows went.
async fn drop_sources(store: &Store, caller: &'static str, filter: &'static str, arg: String, gone: Option<String>) -> Result<usize> {
    store
        .writer
        .write(caller, move |conn| {
            if let Some(gone) = &gone {
                conn.execute("INSERT INTO search_meta(key, value) VALUES (?1, 1) ON CONFLICT(key) DO UPDATE SET value=1", [gone])?;
            }
            let n = delete_where(conn, filter, &[Sql::Text(arg.clone())])?;
            conn.execute(&format!("DELETE FROM search_state WHERE {filter}"), [&arg])?;
            Ok(n)
        })
        .await
}

const LIKE: &str = "source_key LIKE ? ESCAPE '\\'";

/// A LIKE pattern for the keys that start with `prefix`.
fn starting(prefix: &str) -> String {
    prefix.replace('\\', "\\\\").replace('%', "\\%").replace('_', "\\_") + "%"
}

pub async fn drop_source(store: &Store, source_key: String) -> Result<usize> {
    drop_sources(store, "search::drop_source", "source_key=?", source_key, None).await
}

/// Delete every source whose key starts with `prefix` (a plugin's records on uninstall).
pub async fn drop_sources_like(store: &Store, prefix: String) -> Result<usize> {
    drop_sources(store, "search::drop_sources_like", LIKE, starting(&prefix), None).await
}

/// Uninstall: delete a plugin's records and mark it gone, in one transaction. A record
/// write checks the mark inside its own write, so a plugin thread that outlives the
/// uninstall cannot add rows back. Installing the plugin again clears the mark.
pub async fn forget_records(store: &Store, plugin_id: String) -> Result<usize> {
    let like = starting(&format!("record:{plugin_id}:"));
    drop_sources(store, "search::forget_records", LIKE, like, Some(gone_key(&plugin_id))).await
}

pub async fn clear_plugin_gone(store: &Store, plugin_id: String) -> Result<()> {
    store
        .writer
        .write("search::clear_plugin_gone", move |conn| conn.execute("DELETE FROM search_meta WHERE key=?1", [gone_key(&plugin_id)]).map(|_| ()))
        .await
}

/// Delete every plugin record of one account's data scope (a shared copy purged, a
/// plugin-test sandbox ended). A record row's `scope` is `<account>~<scope id>`.
pub async fn drop_scope_records(store: &Store, account: String, scope_id: String) -> Result<usize> {
    let scope = format!("{}~{scope_id}", if account.is_empty() { "_local" } else { &account });
    store.writer.write("search::drop_scope_records", move |conn| delete_where(conn, "kind='record' AND scope=?", &[Sql::Text(scope.clone())])).await
}

/// Delete plugin-test sandbox records not written for `older_than_s` seconds.
pub async fn drop_stale_sandbox_records(store: &Store, older_than_s: f64) -> Result<usize> {
    const STALE: &str = "kind='record' AND scope LIKE ? ESCAPE '\\' AND updated < ?";
    let args = [Sql::Text("%~\\_plugin\\_test\\_%".into()), Sql::Real(now() - older_than_s)];
    let check = args.clone();
    // The common case: no write.
    let any = store.readers.read(move |conn| conn.prepare(&format!("SELECT 1 FROM search_docs WHERE {STALE} LIMIT 1"))?.exists(params_from_iter(check)));
    if !any.await? {
        return Ok(0);
    }
    store.writer.write("search::drop_stale_sandbox_records", move |conn| delete_where(conn, STALE, &args)).await
}

async fn column<T: rusqlite::types::FromSql + Send + 'static>(store: &Store, sql: &'static str, arg: Option<String>) -> Result<Vec<T>> {
    store.readers.read(move |conn| conn.prepare(sql)?.query_map(params_from_iter(arg), |r| r.get(0))?.collect()).await
}

pub async fn source_keys(store: &Store) -> Result<Vec<String>> {
    column(store, "SELECT DISTINCT source_key FROM search_docs", None).await
}

/// Row ids of one source.
pub async fn source_ids(store: &Store, source_key: String) -> Result<Vec<i64>> {
    column(store, "SELECT id FROM search_docs WHERE source_key=?1", Some(source_key)).await
}

/// Every indexed tool name (the search boxes' allowed set; no row bodies read).
pub async fn tool_names(store: &Store) -> Result<Vec<String>> {
    column(store, "SELECT DISTINCT tool_name FROM search_docs WHERE kind='tool' AND tool_name != '' ORDER BY tool_name", None).await
}

pub async fn get_state(store: &Store, source_key: String) -> Result<Option<String>> {
    store.readers.read(move |conn| conn.query_row("SELECT revision FROM search_state WHERE source_key=?1", [source_key], |r| r.get(0)).optional()).await
}

/// source_key -> `[revision, indexed]`.
pub async fn states(store: &Store) -> Result<Map<String, Value>> {
    store
        .readers
        .read(|conn| {
            conn.prepare("SELECT source_key, revision, indexed FROM search_state")?
                .query_map([], |r| Ok((r.get::<_, String>(0)?, json!([r.get::<_, String>(1)?, r.get::<_, f64>(2)?]))))?
                .collect()
        })
        .await
}

/// Drop every derived row (Rebuild, the Database tab's clear) and rebuild the keyword
/// index. Plugin records have no source to rebuild them from, so they stay; their
/// vectors go and the fill pass re-embeds them.
pub async fn wipe(store: &Store) -> Result<()> {
    store
        .writer
        .write("search::wipe", |conn| {
            conn.execute_batch(
                "DELETE FROM search_vecs WHERE doc_id IN (SELECT id FROM search_docs WHERE kind='record'); \
                 DELETE FROM search_docs WHERE kind != 'record'; \
                 DELETE FROM search_state; \
                 INSERT INTO search_fts(search_fts) VALUES ('rebuild');",
            )
        })
        .await
}

// --------------------------------------------------------------------------- reads

/// Runs of letters and digits: Python's `[^\W_]+`.
/// ponytail: told by this build's Unicode tables, as `tokens::words`.
fn words(text: &str) -> impl Iterator<Item = &str> {
    text.split(|ch: char| !matches!(ch.general_category_group(), GeneralCategoryGroup::Letter | GeneralCategoryGroup::Number))
        .filter(|word| !word.is_empty())
}

fn is_stop(word: &str) -> bool {
    STOP.split_ascii_whitespace().any(|stop| stop == word)
}

/// Safe FTS5 MATCH text: quoted tokens OR'd together, the last one as a prefix
/// (`prefix` false: exact words only, for rows whose words are blinded tokens).
/// Quoting every token keeps FTS5 syntax in the person's words from being parsed.
pub fn fts_query(text: &str, prefix: bool) -> String {
    let all: Vec<String> = words(text).map(str::to_lowercase).collect();
    let mut tokens: Vec<&String> = all.iter().filter(|token| !is_stop(token)).collect();
    if tokens.is_empty() {
        tokens = all.iter().collect();
    }
    let mut seen = Vec::new();
    tokens.retain(|token| !seen.contains(token) && { seen.push(*token); true });
    tokens.truncate(MAX_QUERY_TOKENS);
    let Some(last) = tokens.pop() else { return String::new() };
    let mut parts: Vec<String> = tokens.iter().map(|token| format!("\"{token}\"")).collect();
    parts.push(if prefix && last.chars().count() >= 3 { format!("\"{last}\"*") } else { format!("\"{last}\"") });
    parts.join(" OR ")
}

/// The words of `text` without stop words, lower case: "run a workflow" -> "run workflow".
pub fn title_words(text: &str) -> String {
    words(&text.to_lowercase()).filter(|word| !is_stop(word)).collect::<Vec<_>>().join(" ")
}

/// `(doc id, bm25 score, lower is better)` for the best keyword matches.
///
/// `source_key` limits candidates to one source before the cut. `match_text` is a ready
/// FTS5 query in place of `text`'s (sealed records match on blinded words).
pub async fn keyword(store: &Store, text: String, kinds: Vec<String>, limit: i64, source_key: String, match_text: Option<String>) -> Result<Vec<(i64, f64)>> {
    let matching = match_text.unwrap_or_else(|| fts_query(&text, true));
    if matching.is_empty() {
        return Ok(Vec::new());
    }
    store
        .readers
        .read(move |conn| {
            let kinds: Vec<String> = kinds.into_iter().filter(|kind| KINDS.contains(&kind.as_str())).collect();
            let mut sql = format!("SELECT d.id, {BM25} AS score FROM search_fts JOIN search_docs d ON d.id = search_fts.rowid WHERE search_fts MATCH ?");
            let mut args = vec![Sql::Text(matching)];
            if !kinds.is_empty() {
                sql += &format!(" AND d.kind IN ({})", marks(kinds.len()));
                args.extend(kinds.into_iter().map(Sql::Text));
            }
            if !source_key.is_empty() {
                sql += " AND d.source_key = ?";
                args.push(Sql::Text(source_key));
            }
            sql += " ORDER BY score LIMIT ?";
            args.push(Sql::Integer(limit.max(1)));
            let rows = || -> rusqlite::Result<Vec<(i64, f64)>> {
                conn.prepare(&sql)?.query_map(params_from_iter(args), |r| Ok((r.get(0)?, r.get(1)?)))?.collect()
            };
            // A query FTS5 refuses matches nothing.
            Ok(rows().unwrap_or_default())
        })
        .await
}

/// The account a record's scope belongs to.
fn owner(scope: &str) -> &str {
    scope.split_once('~').map_or("", |(account, _)| account)
}

/// A sealed vector, opened with the account's chat key.
fn open_vec(raw: &[u8], key: &tokens::Key) -> Option<Vec<u8>> {
    let mut mac = Blake2bMac::<U32>::new_with_salt_and_personal(key.as_bytes(), &[], b"search-vec").ok()?;
    mac.update(b"");
    let key = mac.finalize().into_bytes();
    let (nonce, sealed) = raw.get(4..)?.split_at_checked(12)?;
    Aes256Gcm::new(&key).decrypt(Nonce::from_slice(nonce), Payload { msg: sealed, aad: VEC_MAGIC }).ok()
}

/// Every vector of `model`: `(doc ids, kinds, concatenated float32 bytes)` in id order.
/// Sealed record vectors are opened for `account` (the signed-in one) with its chat
/// `key`; other accounts' (and all of them without a key) are left out.
pub async fn load_vectors(store: &Store, model: String, account: String, key: Option<tokens::Key>) -> Result<(Vec<i64>, Vec<String>, Vec<u8>)> {
    store
        .readers
        .read(move |conn| {
            let sql = "SELECT v.doc_id, d.kind, d.scope, v.vec FROM search_vecs v JOIN search_docs d ON d.id = v.doc_id \
                       WHERE v.model = ?1 ORDER BY v.doc_id";
            let (mut ids, mut kinds, mut vecs) = (Vec::new(), Vec::new(), Vec::new());
            let mut rows = conn.prepare(sql)?;
            let mut rows = rows.query([model])?;
            while let Some(r) = rows.next()? {
                let mut vec: Vec<u8> = r.get(3)?;
                if vec.starts_with(VEC_MAGIC) {
                    let mine = !account.is_empty() && owner(&r.get::<_, Option<String>>(2)?.unwrap_or_default()) == account;
                    // Sealed with a key restored away: re-embedded on its next write.
                    let Some(opened) = key.as_ref().filter(|_| mine).and_then(|key| open_vec(&vec, key)) else { continue };
                    vec = opened;
                }
                ids.push(r.get(0)?);
                kinds.push(r.get(1)?);
                vecs.extend(vec);
            }
            Ok((ids, kinds, vecs))
        })
        .await
}

const DOC: [&str; 14] = [
    "doc_key", "source_key", "kind", "origin", "plugin_id", "pack_id", "subskill_id", "section", "tool_name", "scope", "title", "path", "body", "meta",
];

/// A sealed record's row with what its plugin wrote, when it is `me`'s and opens.
fn opened(row: &Map<String, Value>, me: Option<&Who>) -> Option<Map<String, Value>> {
    let me = me.filter(|me| owner(row["scope"].as_str().unwrap_or_default()) == me.id)?;
    let plain: Value = serde_json::from_str(&seal::open_text(row["meta"]["sealed"].as_str()?, &me.entropy).ok()?).ok()?;
    let plain = plain.as_object()?;
    let mut row = row.clone();
    for name in ["title", "path", "body"] {
        let text = plain.get(name).filter(|text| truthy(text)).map(pystr).unwrap_or_default();
        row.insert(name.into(), json!(text));
    }
    row.insert("meta".into(), plain.get("meta").filter(|meta| meta.is_object()).cloned().unwrap_or_else(|| json!({})));
    Some(row)
}

fn doc(r: &Row, me: Option<&Who>) -> rusqlite::Result<(String, Value)> {
    let id: i64 = r.get("id")?;
    let mut row = Map::new();
    row.insert("id".into(), json!(id));
    for name in DOC {
        row.insert(name.into(), json!(r.get::<_, Option<String>>(name)?));
    }
    let raw = row["meta"].as_str().unwrap_or_default().to_string();
    row.insert("meta".into(), parsed_meta(&raw));
    if row["kind"] == "record" && raw.starts_with(SEALED_PREFIX) && row["meta"]["sealed"].is_string() {
        row = opened(&row, me).unwrap_or_else(|| {
            // Another account's, or locked for now: shown empty, never as ciphertext.
            for name in ["title", "path", "body"] {
                row.insert(name.into(), json!(""));
            }
            row.insert("meta".into(), json!({}));
            row
        });
    }
    Ok((id.to_string(), Value::Object(row)))
}

/// Rows by id (as text). A sealed plugin record is opened when it is `me`'s (the
/// account signed in) and comes back empty otherwise.
pub async fn get_docs(store: &Store, ids: Vec<i64>, me: Option<Who>) -> Result<Map<String, Value>> {
    store
        .readers
        .read(move |conn| {
            let mut out = Map::new();
            for some in ids.chunks(500) {
                let sql = format!("SELECT id, {} FROM search_docs WHERE id IN ({})", DOC.join(", "), marks(some.len()));
                for row in conn.prepare(&sql)?.query_map(params_from_iter(some), |r| doc(r, me.as_ref()))? {
                    let (id, row) = row?;
                    out.insert(id, row);
                }
            }
            Ok(out)
        })
        .await
}

/// Exact lookups for pinning: a pack or sub-skill id, a tool name, or a title
/// (case-insensitive). An empty filter is not applied.
pub async fn find_ids(store: &Store, kind: String, pack_id: String, subskill_id: String, tool_name: String, title: String) -> Result<Vec<i64>> {
    store
        .readers
        .read(move |conn| {
            let mut sql = "SELECT id FROM search_docs WHERE kind=?".to_string();
            let mut args = vec![kind];
            let filters = [(" AND lower(title)=?", title.to_lowercase()), (" AND pack_id=?", pack_id), (" AND subskill_id=?", subskill_id), (" AND tool_name=?", tool_name)];
            for (filter, value) in filters.into_iter().filter(|(_, value)| !value.is_empty()) {
                sql += filter;
                args.push(value);
            }
            conn.prepare(&sql)?.query_map(params_from_iter(args), |r| r.get(0))?.collect()
        })
        .await
}

pub async fn has_docs(store: &Store) -> Result<bool> {
    store.readers.read(|conn| conn.prepare("SELECT 1 FROM search_docs LIMIT 1")?.exists([])).await
}

/// `{docs: {kind: n}, vectors: {model: n}, last_indexed, sources}`.
pub async fn counts(store: &Store) -> Result<Value> {
    store
        .readers
        .read(|conn| {
            let by = |sql: &str| -> rusqlite::Result<Map<String, Value>> {
                conn.prepare(sql)?.query_map([], |r| Ok((r.get::<_, String>(0)?, json!(r.get::<_, i64>(1)?))))?.collect()
            };
            let (last, sources): (Option<f64>, i64) = conn.query_row("SELECT max(indexed), count(*) FROM search_state", [], |r| Ok((r.get(0)?, r.get(1)?)))?;
            Ok(json!({
                "docs": by("SELECT kind, count(*) FROM search_docs GROUP BY kind")?,
                "vectors": by("SELECT model, count(*) FROM search_vecs GROUP BY model")?,
                "last_indexed": last.unwrap_or(0.0), "sources": sources,
            }))
        })
        .await
}

/// Changes on every insert, update or delete of an index row or vector, by any process
/// (triggers count them in search_meta). It keys a per-process vector-matrix cache.
pub async fn revision_token(store: &Store) -> Result<String> {
    store
        .readers
        .read(|conn| {
            let writes: Option<i64> = conn.query_row("SELECT value FROM search_meta WHERE key='writes'", [], |r| r.get(0)).optional()?;
            Ok(writes.unwrap_or(0).to_string())
        })
        .await
}
