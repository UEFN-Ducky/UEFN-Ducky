//! Host data service tables (`backend/store/repos/plugin_data.py`): `plugin_data` docs +
//! asset metadata, `scope_sync` per-space sync state, `plugin_scopes` where each plugin's
//! data lives. Each row also says why a shared change hasn't gone out yet.
//!
//! Every call names its `(account, scope)`; nothing here reads across accounts.

use std::fs;
use std::path::{Path, PathBuf};

use rusqlite::types::Value as Sql;
use rusqlite::{params, params_from_iter, Connection, OptionalExtension, Params, ParamsFromIter};
use serde_json::{json, Map, Value};

use super::ledger::text_or;
use super::pyjson::{dumps, float, int, pystr, repr, truthy};
use super::{blocking, now, record, Error, Result, Store};

const COLS: &str = "plugin_id, kind, key, value, size, sha256, rev, dirty, deleted, sensitive, updated, sync_state, sync_error, attempts, next_try";
const WHERE: &str = "account_id=?1 AND scope_id=?2 AND plugin_id=?3 AND kind=?4 AND key=?5";
/// Why a dirty row isn't going out: `''` = queued as usual. Refused and blocked rows stay
/// dirty but leave the queue until a retry, a new write or a plan change re-queues them.
pub const SYNC_STATES: [&str; 5] = ["", "refused", "blocked", "conflict", "locked_out"];
/// Local push failures in a row before a row is blocked.
pub const BLOCK_AFTER: i64 = 5;
pub const REFUSED_REASON: &str = "The server didn't accept this change.";
const FRESH: &str = "sync_state='', sync_error='', attempts=0, next_try=0";

type Doc = Map<String, Value>;

/// One doc or file of a plugin, in one scope of one account.
#[derive(Clone, Debug, Default)]
pub struct Item {
    pub account: String,
    pub scope: String,
    pub plugin: String,
    pub kind: String,
    pub key: String,
}

/// The item's five keys, then `more`: the parameters of a statement on [`WHERE`].
fn with(item: &Item, more: impl IntoIterator<Item = Sql>) -> ParamsFromIter<Vec<Sql>> {
    let mut all: Vec<Sql> = [&item.account, &item.scope, &item.plugin, &item.kind, &item.key].into_iter().map(|text| Sql::from(text.clone())).collect();
    all.extend(more);
    params_from_iter(all)
}

fn list(conn: &Connection, sql: &str, params: impl Params) -> rusqlite::Result<Vec<Doc>> {
    conn.prepare(sql)?.query_map(params, record)?.collect()
}

fn strings(conn: &Connection, sql: &str, params: impl Params) -> rusqlite::Result<Vec<String>> {
    conn.prepare(sql)?.query_map(params, |r| r.get(0))?.collect()
}

fn io(err: std::io::Error) -> rusqlite::Error {
    Error::from(err).into()
}

/// `temp` takes `target`'s place. On the writer thread, inside the row's transaction.
fn replace(temp: &Path, target: &Path) -> rusqlite::Result<()> {
    if let Some(folder) = target.parent() {
        fs::create_dir_all(folder).map_err(io)?;
    }
    fs::rename(temp, target).map_err(io)
}

/// The file goes; one that is not there is fine. Only ever off the async workers.
#[allow(clippy::disallowed_methods)]
fn unlink(path: &Path) -> rusqlite::Result<()> {
    match fs::remove_file(path) {
        Err(err) if err.kind() != std::io::ErrorKind::NotFound => Err(io(err)),
        _ => Ok(()),
    }
}

/// The temp file is gone after a write, whether it landed or not.
async fn sweep(temp: Option<PathBuf>) {
    if let Some(temp) = temp {
        let _ = blocking(move || Ok(unlink(&temp)?)).await;
    }
}

/// The first 500 characters of a reason.
fn reason(text: &str) -> String {
    text.chars().take(500).collect()
}

/// Seconds a row waits after its `attempts`-th failed push: `min(2**attempts * 30 s, 1 h)`.
fn backoff(attempts: i64) -> f64 {
    (2f64.powi(attempts.clamp(0, 20) as i32) * 30.0).min(3600.0)
}

// --------------------------------------------------------------------------- docs + assets

pub async fn get(store: &Store, item: Item) -> Result<Option<Doc>> {
    store.readers.read(move |conn| current(conn, &item)).await
}

fn current(conn: &Connection, item: &Item) -> rusqlite::Result<Option<Doc>> {
    conn.query_row(&format!("SELECT {COLS} FROM plugin_data WHERE {WHERE}"), with(item, []), record).optional()
}

/// What a write stores. `rev: None` keeps the stored rev (a local write). `group_id`: the
/// write group (one tool call or panel call) the write was made in, `''` for none.
#[derive(Clone, Debug, Default)]
pub struct Write {
    pub value: Option<String>,
    pub size: i64,
    pub sha256: String,
    pub dirty: bool,
    pub rev: Option<i64>,
    pub sensitive: bool,
    pub group_id: String,
}

/// Upsert a live row.
pub async fn put(store: &Store, item: Item, write: Write) -> Result<()> {
    store.writer.write("plugin_data::put", move |conn| upsert_in(conn, &item, &write)).await
}

/// [`put`] inside a write (writes that must land together). Every write starts the row's
/// sync afresh: a new edit or a pulled copy is never refused or blocked, and it goes out
/// with the write group of its newest write.
pub fn upsert_in(conn: &Connection, item: &Item, write: &Write) -> rusqlite::Result<()> {
    let sql = format!(
        "INSERT INTO plugin_data(account_id, scope_id, plugin_id, kind, key, value, size, sha256, rev, dirty, \
         deleted, sensitive, updated, group_id) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, 0, ?11, ?12, ?13) \
         ON CONFLICT(account_id, scope_id, plugin_id, kind, key) DO UPDATE SET value=excluded.value, \
         size=excluded.size, sha256=excluded.sha256, rev=COALESCE(?14, plugin_data.rev), dirty=excluded.dirty, \
         deleted=0, sensitive=excluded.sensitive, updated=excluded.updated, group_id=excluded.group_id, {FRESH}"
    );
    let more = [
        write.value.clone().into(),
        write.size.into(),
        write.sha256.clone().into(),
        write.rev.unwrap_or(0).into(),
        i64::from(write.dirty).into(),
        i64::from(write.sensitive).into(),
        now().into(),
        write.group_id.clone().into(),
        write.rev.into(),
    ];
    conn.execute(&sql, with(item, more)).map(|_| ())
}

/// A local file write: `temp` (the sealed bytes, in the target's own folder) replaces
/// `target` inside the row's transaction, so the row and the file never disagree. The
/// temp file is gone afterwards either way. `item.kind` is the file kind (`asset`).
pub async fn put_file_row(store: &Store, item: Item, write: Write, temp: PathBuf, target: PathBuf) -> Result<()> {
    let from = temp.clone();
    let out = store
        .writer
        .write("plugin_data::put_file_row", move |conn| {
            upsert_in(conn, &item, &Write { value: None, rev: None, sensitive: false, ..write })?;
            replace(&from, &target)
        })
        .await;
    sweep(Some(temp)).await;
    out
}

/// Local delete: a tombstone waiting to push (it keeps the deleted content's sha256), or
/// the row just goes. A file's `target` goes in the same transaction, after the row: a
/// file held open fails and keeps both, and a pull can't land in between.
pub async fn remove(store: &Store, item: Item, tombstone: bool, target: Option<PathBuf>, group_id: String) -> Result<()> {
    store
        .writer
        .write("plugin_data::remove", move |conn| {
            if tombstone {
                let sql = format!("UPDATE plugin_data SET value=NULL, size=0, deleted=1, dirty=1, updated=?6, group_id=?7, {FRESH} WHERE {WHERE}");
                conn.execute(&sql, with(&item, [now().into(), group_id.into()]))?;
            } else {
                delete_row_in(conn, &item)?;
            }
            target.as_deref().map_or(Ok(()), unlink)
        })
        .await
}

/// This PC's own changes of a plugin written after `since` (dirty rows, deletes included;
/// never sensitive ones), oldest first: what a live group hears at once.
pub async fn written_since(store: &Store, account: String, scope: String, plugin: String, since: f64) -> Result<Vec<Doc>> {
    store
        .readers
        .read(move |conn| {
            let sql = format!(
                "SELECT {COLS} FROM plugin_data WHERE account_id=?1 AND scope_id=?2 AND plugin_id=?3 AND dirty=1 AND sensitive=0 AND updated>?4 ORDER BY updated"
            );
            list(conn, &sql, params![account, scope, plugin, since])
        })
        .await
}

/// A row goes (no tombstone), inside a write.
pub fn delete_row_in(conn: &Connection, item: &Item) -> rusqlite::Result<()> {
    conn.execute(&format!("DELETE FROM plugin_data WHERE {WHERE}"), with(item, [])).map(|_| ())
}

/// Plugin ids with live, non-sensitive rows in one scope.
pub async fn plugin_ids(store: &Store, account: String, scope: String) -> Result<Vec<String>> {
    let sql = "SELECT DISTINCT plugin_id FROM plugin_data WHERE account_id=?1 AND scope_id=?2 AND deleted=0 AND sensitive=0 ORDER BY plugin_id";
    store.readers.read(move |conn| strings(conn, sql, [account, scope])).await
}

/// Scopes (Local and shared spaces) holding live, non-sensitive rows of the account.
pub async fn scope_ids(store: &Store, account: String) -> Result<Vec<String>> {
    let sql = "SELECT DISTINCT scope_id FROM plugin_data WHERE account_id=?1 AND deleted=0 AND sensitive=0 ORDER BY scope_id";
    store.readers.read(move |conn| strings(conn, sql, [account])).await
}

/// Any row of `plugin` (tombstones and sensitive ones too) in `scope`, or in any scope
/// of the account.
pub async fn has_rows(store: &Store, account: String, plugin: String, scope: Option<String>) -> Result<bool> {
    store
        .readers
        .read(move |conn| {
            conn.prepare("SELECT 1 FROM plugin_data WHERE account_id=?1 AND plugin_id=?2 AND (?3 IS NULL OR scope_id=?3) LIMIT 1")?.exists(params![account, plugin, scope])
        })
        .await
}

/// Live rows of `under`'s kind whose key starts with exactly `under.key` (`_` is no
/// wildcard). Keys are ASCII, so the prefix + DEL bounds the range. `values: false`
/// leaves `value` null: listing keys never loads every sealed doc.
pub async fn rows(store: &Store, under: Item, values: bool) -> Result<Vec<Doc>> {
    store
        .readers
        .read(move |conn| {
            let cols = if values { COLS.to_string() } else { COLS.replacen("value,", "NULL AS value,", 1) };
            let sql = format!(
                "SELECT {cols} FROM plugin_data WHERE account_id=?1 AND scope_id=?2 AND plugin_id=?3 AND kind=?4 AND deleted=0 AND key>=?5 AND key<?6 ORDER BY key"
            );
            list(conn, &sql, with(&under, [format!("{}\x7f", under.key).into()]))
        })
        .await
}

/// Changes to push: fewest failed attempts first, then oldest, so a row that keeps
/// failing never holds up newer ones. Sensitive rows never leave the PC; refused and
/// blocked rows wait for a retry, and a row backing off waits for its `next_try`.
pub async fn dirty(store: &Store, account: String, scope: String, limit: i64) -> Result<Vec<Doc>> {
    store
        .readers
        .read(move |conn| {
            let sql = format!(
                "SELECT {COLS} FROM plugin_data WHERE account_id=?1 AND scope_id=?2 AND dirty=1 AND sensitive=0 \
                 AND sync_state NOT IN ('refused', 'blocked') AND next_try<=?3 ORDER BY attempts, updated LIMIT ?4"
            );
            list(conn, &sql, params![account, scope, now(), limit])
        })
        .await
}

/// Changes still to send (rows backing off included; refused and blocked rows are in
/// [`state_counts`]). `plugin`: that plugin's only.
pub async fn count_dirty(store: &Store, account: String, scope: String, plugin: Option<String>) -> Result<i64> {
    let sql = "SELECT COUNT(*) FROM plugin_data WHERE account_id=?1 AND scope_id=?2 AND dirty=1 AND sensitive=0 \
               AND sync_state NOT IN ('refused', 'blocked') AND (?3 IS NULL OR plugin_id=?3)";
    store.readers.read(move |conn| conn.query_row(sql, params![account, scope, plugin], |r| r.get(0))).await
}

/// `{scope: newest write}` of the account's changes still to send, per scope (the
/// background sync's one look per change; refused and blocked rows wait for a retry).
pub async fn unsent_spaces(store: &Store, account: String) -> Result<Doc> {
    let sql = "SELECT scope_id, MAX(updated) FROM plugin_data WHERE account_id=?1 AND dirty=1 AND sensitive=0 \
               AND sync_state NOT IN ('refused', 'blocked') GROUP BY scope_id";
    store
        .readers
        .read(move |conn| conn.prepare(sql)?.query_map([account], |r| Ok((r.get::<_, String>(0)?, json!(r.get::<_, Option<f64>>(1)?.unwrap_or(0.0)))))?.collect())
        .await
}

/// The server confirmed a push at `rev`.
///
/// A delete (`pushed_delete`, or an empty `sha256` from providers older than the flag):
/// the row goes while it is still a tombstone; a row re-created since keeps its change
/// and takes `rev` as its base. A content push (`sha256` of the pushed plaintext) clears
/// `dirty` only while the row still holds that content: an edit made meanwhile, or a
/// delete (tombstones keep the deleted sha), stays queued on the new base rev.
pub async fn mark_pushed(store: &Store, item: Item, rev: i64, sha256: String, pushed_delete: bool) -> Result<()> {
    store
        .writer
        .write("plugin_data::mark_pushed", move |conn| {
            if pushed_delete || sha256.is_empty() {
                if conn.execute(&format!("DELETE FROM plugin_data WHERE {WHERE} AND deleted=1"), with(&item, []))? == 0 {
                    conn.execute(&format!("UPDATE plugin_data SET rev=?6, {FRESH} WHERE {WHERE}"), with(&item, [rev.into()]))?;
                }
                return Ok(());
            }
            let sql = format!("UPDATE plugin_data SET rev=?6, dirty=CASE WHEN deleted=0 AND sha256=?7 THEN 0 ELSE dirty END, {FRESH} WHERE {WHERE}");
            conn.execute(&sql, with(&item, [rev.into(), sha256.into()])).map(|_| ())
        })
        .await
}

pub async fn set_rev(store: &Store, item: Item, rev: i64) -> Result<()> {
    store.writer.write("plugin_data::set_rev", move |conn| conn.execute(&format!("UPDATE plugin_data SET rev=?6 WHERE {WHERE}"), with(&item, [rev.into()])).map(|_| ())).await
}

/// Queue every live doc of one plugin to push on the next sync round, refused and blocked
/// ones too.
pub async fn mark_plugin_dirty(store: &Store, account: String, scope: String, plugin: String) -> Result<()> {
    let sql = "UPDATE plugin_data SET dirty=1, attempts=0, next_try=0, \
               sync_error=CASE WHEN sync_state IN ('refused', 'blocked') THEN '' ELSE sync_error END, \
               sync_state=CASE WHEN sync_state IN ('refused', 'blocked') THEN '' ELSE sync_state END \
               WHERE account_id=?1 AND scope_id=?2 AND plugin_id=?3 AND deleted=0";
    store.writer.write("plugin_data::mark_plugin_dirty", move |conn| conn.execute(sql, [account, scope, plugin]).map(|_| ())).await
}

/// Queue one live item to push again as it is (the server's copy needs replacing).
pub async fn mark_dirty(store: &Store, item: Item) -> Result<()> {
    store.writer.write("plugin_data::mark_dirty", move |conn| conn.execute(&format!("UPDATE plugin_data SET dirty=1 WHERE {WHERE} AND deleted=0"), with(&item, [])).map(|_| ())).await
}

/// Record why a row isn't syncing (`refused`, `blocked`, `conflict`, `locked_out`); `''`
/// puts it back in the queue with fresh attempts. `dirty` is kept. `expect_sha`: only
/// while the row still holds what was pushed (`""`: the push was a delete), so a newer
/// edit is never marked. False when no row matched.
pub async fn set_state(store: &Store, item: Item, state: String, why: String, expect_sha: Option<String>) -> Result<bool> {
    if !SYNC_STATES.contains(&state.as_str()) {
        return Err(Error(format!("unknown sync state: {}", repr(&json!(state)))));
    }
    store
        .writer
        .write("plugin_data::set_state", move |conn| {
            let held = match expect_sha.as_deref() {
                Some("") => " AND deleted=1",
                Some(_) => " AND sha256=?8 AND deleted=0",
                None => "",
            };
            let fresh = if state.is_empty() { ", attempts=0, next_try=0" } else { "" };
            let sql = format!("UPDATE plugin_data SET sync_state=?6, sync_error=?7{fresh} WHERE {WHERE}{held}");
            let sha = expect_sha.filter(|sha| !sha.is_empty()).map(Sql::from);
            Ok(conn.execute(&sql, with(&item, [state.into(), reason(&why).into()].into_iter().chain(sha)))? > 0)
        })
        .await
}

/// What providers older than [`set_state`] call for a push the server refused: the row is
/// kept as refused (still dirty, out of the queue) instead of looking synced.
pub async fn clear_dirty(store: &Store, item: Item) -> Result<()> {
    set_state(store, item, "refused".into(), REFUSED_REASON.into(), None).await.map(|_| ())
}

fn attempt(conn: &Connection, item: &Item) -> rusqlite::Result<Option<(i64, String)>> {
    conn.query_row(&format!("SELECT attempts, sync_state FROM plugin_data WHERE {WHERE}"), with(item, []), |r| Ok((r.get(0)?, r.get(1)?))).optional()
}

fn note(conn: &Connection, item: &Item, attempts: i64, next_try: f64, state: &str, error: &str) -> rusqlite::Result<()> {
    let sql = format!("UPDATE plugin_data SET attempts=?6, next_try=?7, sync_state=?8, sync_error=?9 WHERE {WHERE}");
    conn.execute(&sql, with(item, [attempts.into(), next_try.into(), state.to_string().into(), reason(error).into()])).map(|_| ())
}

/// A push of this row failed on this PC (file missing, unreadable, can't be sealed). It
/// waits `min(2**attempts * 30 s, 1 h)` and is `blocked` after [`BLOCK_AFTER`] failures.
/// Returns `{attempts, next_try, state}`; None when there is no such row.
pub async fn note_attempt(store: &Store, item: Item, error: String) -> Result<Option<Value>> {
    store
        .writer
        .write("plugin_data::note_attempt", move |conn| {
            let Some((attempts, state)) = attempt(conn, &item)? else { return Ok(None) };
            let attempts = attempts + 1;
            let next_try = now() + backoff(attempts);
            let state = if attempts >= BLOCK_AFTER { "blocked".to_string() } else { state };
            note(conn, &item, attempts, next_try, &state, &error)?;
            Ok(Some(json!({"attempts": attempts, "next_try": next_try, "state": state})))
        })
        .await
}

/// A changeset of these rows (`(kind, key)` of `of`'s plugin) failed as a whole (the
/// server took none of it and said why). They count one try together, as many as the
/// most-tried of them, so they wait alike, come back together and are `blocked` with the
/// reason together after [`BLOCK_AFTER`]. Returns `{attempts, next_try, state}`; None
/// when none of them is here any more.
pub async fn note_attempts(store: &Store, of: Item, items: Vec<(String, String)>, error: String) -> Result<Option<Value>> {
    store
        .writer
        .write("plugin_data::note_attempts", move |conn| {
            let mut found: Vec<(Item, i64, String)> = Vec::new();
            for (kind, key) in items {
                let item = Item { kind, key, ..of.clone() };
                if found.iter().any(|(seen, ..)| seen.kind == item.kind && seen.key == item.key) {
                    continue;
                }
                if let Some((attempts, state)) = attempt(conn, &item)? {
                    found.push((item, attempts, state));
                }
            }
            let Some(most) = found.iter().map(|(_, attempts, _)| *attempts).max() else { return Ok(None) };
            let attempts = most + 1;
            let next_try = now() + backoff(attempts);
            let blocked = attempts >= BLOCK_AFTER;
            for (item, _, state) in &found {
                note(conn, item, attempts, next_try, if blocked { "blocked" } else { state }, &error)?;
            }
            Ok(Some(json!({"attempts": attempts, "next_try": next_try, "state": if blocked { "blocked" } else { "" }})))
        })
        .await
}

/// Refused rows of a scope go back in the queue (the plan or the space's usage changed),
/// folder files plugins keep too. Returns how many.
///
/// ponytail: Python asks Ducky Account's engine for the folder files (0 without it); the
/// tables are the host's, so the engine's two statements are here.
pub async fn requeue_refused(store: &Store, account: String, scope: String) -> Result<usize> {
    store
        .writer
        .write("plugin_data::requeue_refused", move |conn| {
            let rows = conn.execute(&format!("UPDATE plugin_data SET {FRESH} WHERE account_id=?1 AND scope_id=?2 AND sync_state='refused'"), [&account, &scope])?;
            let files = conn.execute("UPDATE folder_files SET sync_state='', sync_error='' WHERE account_id=?1 AND space_id=?2 AND sync_state='refused'", [&account, &scope])?;
            Ok(rows + files)
        })
        .await
}

/// The person's choice after the space's data was reset on the server. It is for this
/// PC's whole copy of the space (every plugin's, workflows too): `keep` (Upload my copy)
/// queues every live row again as new (base rev 0) and drops tombstones (nothing is left
/// there to delete); otherwise (Clear my copy) every row goes, and the space's data comes
/// down instead. The space is writable again and pulls from the start, all in one
/// transaction: its cursor goes back to 0, every plugin's head does, and what this PC
/// kept of the old versions goes (changesets, merge bases, unit seqs, items set aside,
/// conflicts). Folder files plugins keep go up again as new too, or their rows go.
/// Returns how many rows and files were queued, or removed.
pub async fn settle_reset(store: &Store, account: String, scope: String, keep: bool) -> Result<usize> {
    store
        .writer
        .write("plugin_data::settle_reset", move |conn| {
            let of = [&account, &scope];
            for table in ["space_changesets", "space_conflicts", "space_incoming_blocked", "plugin_data_base", "space_units", "folder_merge_base"] {
                conn.execute(&format!("DELETE FROM {table} WHERE account_id=?1 AND space_id=?2"), of)?;
            }
            conn.execute("UPDATE space_heads SET seq=0, remote_seq=0, moves=0, attempts=0, next_try=0 WHERE account_id=?1 AND space_id=?2", of)?;
            let count = if keep {
                let queued =
                    conn.execute(&format!("UPDATE plugin_data SET rev=0, dirty=1, {FRESH} WHERE account_id=?1 AND scope_id=?2 AND deleted=0 AND sensitive=0"), of)?;
                conn.execute("DELETE FROM plugin_data WHERE account_id=?1 AND scope_id=?2 AND deleted=1", of)?;
                conn.execute("DELETE FROM folder_files WHERE account_id=?1 AND space_id=?2 AND (deleted=1 OR sync_state='refetch')", of)?;
                queued + conn.execute("UPDATE folder_files SET dirty=1, base_seq=0, base_sha='', sync_state='', sync_error='' WHERE account_id=?1 AND space_id=?2", of)?
            } else {
                conn.execute("DELETE FROM plugin_data WHERE account_id=?1 AND scope_id=?2", of)?
                    + conn.execute("DELETE FROM folder_files WHERE account_id=?1 AND space_id=?2", of)?
            };
            conn.execute("UPDATE scope_sync SET state='ok', error='', cursor_rev=0, rewind_rev=0, rewind_at=0 WHERE account_id=?1 AND space_id=?2", of)?;
            Ok(count)
        })
        .await
}

/// Rows that aren't syncing, per state: `{refused, blocked, conflict, locked_out}`.
pub async fn state_counts(store: &Store, account: String, scope: String, plugin: Option<String>) -> Result<Doc> {
    let sql = "SELECT sync_state, COUNT(*) FROM plugin_data WHERE account_id=?1 AND scope_id=?2 AND sync_state<>'' AND (?3 IS NULL OR plugin_id=?3) GROUP BY sync_state";
    store
        .readers
        .read(move |conn| {
            let mut out: Doc = SYNC_STATES[1..].iter().map(|state| (state.to_string(), json!(0))).collect();
            let mut counts = conn.prepare(sql)?;
            let mut counts = counts.query(params![account, scope, plugin])?;
            while let Some(r) = counts.next()? {
                out.insert(r.get(0)?, json!(r.get::<_, i64>(1)?));
            }
            Ok(out)
        })
        .await
}

/// The rows behind [`state_counts`], newest first: plugin_id, kind, key, deleted,
/// sync_state, sync_error (the reason), attempts, next_try.
pub async fn state_items(store: &Store, account: String, scope: String, plugin: Option<String>, limit: i64) -> Result<Vec<Doc>> {
    let sql = "SELECT plugin_id, kind, key, deleted, sync_state, sync_error, attempts, next_try FROM plugin_data \
               WHERE account_id=?1 AND scope_id=?2 AND sync_state<>'' AND (?3 IS NULL OR plugin_id=?3) ORDER BY updated DESC LIMIT ?4";
    store.readers.read(move |conn| list(conn, sql, params![account, scope, plugin, limit])).await
}

/// One plugin's changes not sent yet (dirty, never sensitive), oldest first, without
/// their content: kind, key, size, deleted, sync_state, sync_error, attempts, next_try.
pub async fn pending_items(store: &Store, account: String, scope: String, plugin: String, limit: i64) -> Result<Vec<Doc>> {
    let sql = "SELECT kind, key, size, deleted, sync_state, sync_error, attempts, next_try FROM plugin_data \
               WHERE account_id=?1 AND scope_id=?2 AND plugin_id=?3 AND dirty=1 AND sensitive=0 ORDER BY updated LIMIT ?4";
    store.readers.read(move |conn| list(conn, sql, params![account, scope, plugin, limit])).await
}

/// The row is still what the caller read before its download: absent both times, or
/// clean with the same content and deleted flag (no local write landed meanwhile).
fn unchanged(row: Option<&Doc>, expect: Option<&Doc>) -> bool {
    let (Some(row), Some(expect)) = (row, expect) else { return row.is_none() && expect.is_none() };
    let sha = expect.get("sha256").filter(|sha| truthy(sha)).map(pystr).unwrap_or_default();
    int(&row["dirty"]) == 0 && row["sha256"] == sha.as_str() && (int(&row["deleted"]) != 0) == expect.get("deleted").is_some_and(|deleted| int(deleted) != 0)
}

/// Land a pulled copy (`write.rev` is its rev) as a clean row, unless a local write won
/// the race. `expect` is the row read before the download (None: there was none). For a
/// file, `temp` (in the target's own folder) replaces `target` inside the same
/// transaction. False: nothing changed, the local write stands (its push or the stale
/// path settles it). The temp file is gone afterwards either way.
pub async fn apply_remote(store: &Store, item: Item, write: Write, expect: Option<Doc>, temp: Option<PathBuf>, target: Option<PathBuf>) -> Result<bool> {
    let from = temp.clone();
    let out = store
        .writer
        .write("plugin_data::apply_remote", move |conn| {
            if !unchanged(current(conn, &item)?.as_ref(), expect.as_ref()) {
                return Ok(false);
            }
            upsert_in(conn, &item, &Write { dirty: false, sensitive: false, group_id: String::new(), ..write })?;
            if let (Some(from), Some(target)) = (&from, &target) {
                replace(from, target)?;
            }
            Ok(true)
        })
        .await;
    sweep(temp).await;
    out
}

/// A pulled delete, on the terms of [`apply_remote`]. The row and `target` go in one
/// transaction (a file held open fails and keeps the row).
pub async fn remove_remote(store: &Store, item: Item, expect: Option<Doc>, target: Option<PathBuf>) -> Result<bool> {
    store
        .writer
        .write("plugin_data::remove_remote", move |conn| {
            let row = current(conn, &item)?;
            if !unchanged(row.as_ref(), expect.as_ref()) {
                return Ok(false);
            }
            if row.is_some() {
                delete_row_in(conn, &item)?;
                target.as_deref().map_or(Ok(()), unlink)?;
            }
            Ok(true)
        })
        .await
}

/// Drop my change: the row `expect` (read just before) goes, with `target`, in one
/// transaction, and only while it is still that change held back (dirty, refused or
/// blocked, same content, same deleted flag). A newer edit, or a change whose push is
/// under way, is never dropped. When the team has the item (rev > 0) it comes back: on
/// the older sync the space's cursor goes back to its rev (the pull cursor is inclusive)
/// and stays at most there until a round that started after the drop writes it, so a
/// round already running can't move it past again; on the new sync (the plugin's head is
/// past it) the item is set aside to be fetched at its current version on the next
/// round. False when the row changed meanwhile.
pub async fn drop_held(store: &Store, item: Item, expect: Doc, target: Option<PathBuf>) -> Result<bool> {
    store
        .writer
        .write("plugin_data::drop_held", move |conn| {
            let of = |name: &str| expect.get(name).cloned().unwrap_or_default();
            let rev = int(&of("rev"));
            let sql = format!("DELETE FROM plugin_data WHERE {WHERE} AND dirty=1 AND sync_state IN ('refused', 'blocked') AND sha256=?6 AND deleted=?7");
            if conn.execute(&sql, with(&item, [text_or(&of("sha256"), "").into(), i64::from(int(&of("deleted")) != 0).into()]))? == 0 {
                return Ok(false);
            }
            let engine: Option<String> = conn
                .query_row("SELECT engine FROM space_heads WHERE account_id=?1 AND space_id=?2 AND plugin_id=?3", [&item.account, &item.scope, &item.plugin], |r| r.get(0))
                .optional()?;
            if rev > 0 && engine.as_deref() == Some("v2") {
                conn.execute(
                    "INSERT INTO space_incoming_blocked(account_id, space_id, plugin_id, kind, key, unit, seq, error, \
                     attempts, next_try) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, 'Getting the team''s version.', 0, 0) \
                     ON CONFLICT(account_id, space_id, plugin_id, kind, key) \
                     DO UPDATE SET seq=MAX(space_incoming_blocked.seq, excluded.seq), next_try=0",
                    with(&item, [format!("{}:{}", item.kind, item.key).into(), rev.into()]),
                )?;
            } else if rev > 0 {
                conn.execute(
                    "UPDATE scope_sync SET cursor_rev=MIN(cursor_rev, ?1), \
                     rewind_rev=CASE WHEN rewind_at>0 THEN MIN(rewind_rev, ?1) ELSE ?1 END, rewind_at=?2 \
                     WHERE account_id=?3 AND space_id=?4",
                    params![rev, now(), item.account, item.scope],
                )?;
            }
            target.as_deref().map_or(Ok(()), unlink)?;
            Ok(true)
        })
        .await
}

/// Erase `plugin`'s rows in every scope of the account, and its link. The spaces' cursors
/// stay: their next rounds must not bring the erased copy back (a pull stores every
/// plugin's items). Linking it again pulls from the start. Returns the scopes it had
/// rows in.
pub async fn delete_plugin(store: &Store, account: String, plugin: String) -> Result<Vec<String>> {
    store
        .writer
        .write("plugin_data::delete_plugin", move |conn| {
            let of = [&account, &plugin];
            let held = strings(conn, "SELECT DISTINCT scope_id FROM plugin_data WHERE account_id=?1 AND plugin_id=?2", of)?;
            conn.execute("DELETE FROM plugin_data WHERE account_id=?1 AND plugin_id=?2", of)?;
            conn.execute("DELETE FROM plugin_scopes WHERE account_id=?1 AND plugin_id=?2", of)?;
            Ok(held)
        })
        .await
}

/// Every plugin id with rows in one scope, deleted and sensitive ones too.
pub async fn scope_plugin_ids(store: &Store, account: String, scope: String) -> Result<Vec<String>> {
    let sql = "SELECT DISTINCT plugin_id FROM plugin_data WHERE account_id=?1 AND scope_id=?2 ORDER BY plugin_id";
    store.readers.read(move |conn| strings(conn, sql, [account, scope])).await
}

pub async fn delete_scope_plugin(store: &Store, account: String, scope: String, plugin: String) -> Result<()> {
    let sql = "DELETE FROM plugin_data WHERE account_id=?1 AND scope_id=?2 AND plugin_id=?3";
    store.writer.write("plugin_data::delete_scope_plugin", move |conn| conn.execute(sql, [account, scope, plugin]).map(|_| ())).await
}

pub async fn delete_scope(store: &Store, account: String, scope: String) -> Result<()> {
    store
        .writer
        .write("plugin_data::delete_scope", move |conn| {
            let of = [&account, &scope];
            conn.execute("DELETE FROM plugin_data WHERE account_id=?1 AND scope_id=?2", of)?;
            conn.execute("DELETE FROM scope_sync WHERE account_id=?1 AND space_id=?2", of)?;
            conn.execute("DELETE FROM plugin_scopes WHERE account_id=?1 AND scope_id=?2", of).map(|_| ())
        })
        .await
}

// --------------------------------------------------------------------------- sync state

pub async fn sync_get(store: &Store, account: String, space: String) -> Result<Doc> {
    let sql = "SELECT label, members, cursor_rev, state, error, usage, synced_at, called_at FROM scope_sync WHERE account_id=?1 AND space_id=?2";
    let row = store.readers.read(move |conn| conn.query_row(sql, [account, space], record).optional()).await?;
    let Some(mut row) = row else {
        let none = json!({"label": "", "members": 0, "cursor_rev": 0, "state": "ok", "error": "", "usage": {}, "synced_at": 0.0, "called_at": 0.0});
        return Ok(none.as_object().cloned().unwrap_or_default());
    };
    let usage = row["usage"].as_str().filter(|text| !text.is_empty()).unwrap_or("{}");
    row["usage"] = serde_json::from_str(usage).unwrap_or_else(|_| json!({}));
    Ok(row)
}

/// A round reads the cursor a moment before it stamps `called_at`: one stamped this soon
/// after a drop ([`drop_held`]) may still pull from the cursor before it.
pub const REWIND_GRACE_S: f64 = 5.0;
/// While a drop's rewind holds, a cursor write goes no further than the dropped item's
/// rev; the first write by a round that started after the drop ends the hold.
const CURSOR_SET: &str = "cursor_rev=CASE WHEN scope_sync.rewind_at>0 AND scope_sync.called_at<=scope_sync.rewind_at+5.0 \
                          THEN MIN(excluded.cursor_rev, scope_sync.rewind_rev) ELSE excluded.cursor_rev END, \
                          rewind_at=CASE WHEN scope_sync.called_at>scope_sync.rewind_at+5.0 THEN 0 ELSE scope_sync.rewind_at END";

/// `json.dumps(value)`: as [`dumps`], every character past ASCII escaped.
fn dumps_ascii(value: &Value) -> String {
    let mut out = String::new();
    for ch in dumps(value).chars() {
        if ch.is_ascii() {
            out.push(ch);
        } else {
            out.extend(ch.encode_utf16(&mut [0; 2]).iter().map(|unit| format!("\\u{unit:04x}")));
        }
    }
    out
}

/// One of the sync row's columns, as Python converts it; None for any other name.
fn sync_field(name: &str, value: &Value) -> Option<Sql> {
    Some(match name {
        "label" | "state" | "error" => pystr(value).into(),
        "members" | "cursor_rev" => int(value).into(),
        "usage" if truthy(value) => dumps_ascii(value).into(),
        "usage" => "{}".to_string().into(),
        "synced_at" | "called_at" => float(value).unwrap_or(0.0).into(),
        _ => return None,
    })
}

/// Set `fields` on the space's sync row in one statement, so two writers (a round and a
/// picker refresh, in any process) never undo each other's columns. Other names are
/// ignored. A `cursor_rev` honours a dropped change's rewind ([`drop_held`]). A space
/// back to `ok` forgets what the person chose for unsent changes when access went: a
/// later loss asks again.
pub async fn sync_put(store: &Store, account: String, space: String, fields: Doc) -> Result<()> {
    let (cols, values): (Vec<&str>, Vec<Sql>) = fields.iter().filter_map(|(name, value)| Some((name.as_str(), sync_field(name, value)?))).unzip();
    if cols.is_empty() {
        return Ok(());
    }
    let sets: Vec<String> = cols.iter().map(|col| if *col == "cursor_rev" { CURSOR_SET.to_string() } else { format!("{col}=excluded.{col}") }).collect();
    let sql = format!(
        "INSERT INTO scope_sync(account_id, space_id, {}) VALUES (?, ?{}) ON CONFLICT(account_id, space_id) DO UPDATE SET {}",
        cols.join(", "),
        ", ?".repeat(cols.len()),
        sets.join(", ")
    );
    let ok = fields.get("state").is_some_and(|state| state == "ok");
    store
        .writer
        .write("plugin_data::sync_put", move |conn| {
            let of = [Sql::from(account.clone()), Sql::from(space.clone())];
            conn.execute(&sql, params_from_iter(of.into_iter().chain(values)))?;
            if ok {
                conn.execute("DELETE FROM space_lost_choice WHERE account_id=?1 AND space_id=?2", [&account, &space])?;
            }
            Ok(())
        })
        .await
}

// --------------------------------------------------------------------------- plugin links

/// Where this plugin's data lives for the account: `personal` (Local) or a space id.
pub async fn link_get(store: &Store, account: String, plugin: String) -> Result<String> {
    let scope: Option<String> =
        store.readers.read(move |conn| conn.query_row("SELECT scope_id FROM plugin_scopes WHERE account_id=?1 AND plugin_id=?2", [account, plugin], |r| r.get(0)).optional()).await?;
    Ok(scope.unwrap_or_else(|| "personal".into()))
}

pub async fn link_set(store: &Store, account: String, plugin: String, scope: String) -> Result<()> {
    store
        .writer
        .write("plugin_data::link_set", move |conn| {
            if scope == "personal" {
                return conn.execute("DELETE FROM plugin_scopes WHERE account_id=?1 AND plugin_id=?2", [account, plugin]).map(|_| ());
            }
            conn.execute("INSERT OR REPLACE INTO plugin_scopes(account_id, plugin_id, scope_id, updated) VALUES (?1, ?2, ?3, ?4)", params![account, plugin, scope, now()]).map(|_| ())
        })
        .await
}

/// Every plugin of the account whose data lives in a shared space: `{plugin_id: space_id}`.
pub async fn links(store: &Store, account: String) -> Result<Doc> {
    store
        .readers
        .read(move |conn| {
            conn.prepare("SELECT plugin_id, scope_id FROM plugin_scopes WHERE account_id=?1")?.query_map([account], |r| Ok((r.get::<_, String>(0)?, json!(r.get::<_, String>(1)?))))?.collect()
        })
        .await
}

/// Live docs and files of one plugin in one scope: counts and plaintext bytes.
pub async fn totals(store: &Store, account: String, scope: String, plugin: String) -> Result<Value> {
    let sql = "SELECT kind, COUNT(*), COALESCE(SUM(size), 0) FROM plugin_data WHERE account_id=?1 AND scope_id=?2 \
               AND plugin_id=?3 AND deleted=0 AND sensitive=0 GROUP BY kind";
    store
        .readers
        .read(move |conn| {
            let mut out = json!({"docs": 0, "files": 0, "docsBytes": 0, "filesBytes": 0});
            let mut kinds = conn.prepare(sql)?;
            let mut kinds = kinds.query([account, scope, plugin])?;
            while let Some(r) = kinds.next()? {
                let name = if r.get::<_, String>(0)? == "doc" { "docs" } else { "files" };
                out[name] = json!(r.get::<_, i64>(1)?);
                out[format!("{name}Bytes")] = json!(r.get::<_, i64>(2)?);
            }
            Ok(out)
        })
        .await
}
