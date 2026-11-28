//! Rows from before chats and duckies belonged to an account
//! (`backend/store/account_rows.py`, `repos/chat_seal.py`): they go whole to the first
//! account that opens them ([`claim`]) and are sealed in place, a batch at a time
//! ([`seal_table`], [`seal_stored_messages`]). Nothing is deleted. The space those
//! rewrites free is zeroed, so a value's old plaintext doesn't stay in the file.
//!
//! Sealing, search tokens and the chat key are in `seal`, `tokens` and `chats`.
//!
//! The pass that strings these together at sign-in is `seal_pending`.

use std::time::Duration;

use rusqlite::types::Value as Sql;
use rusqlite::{params, params_from_iter, Connection, OptionalExtension};
use serde_json::{json, Value};

use super::duckies::Who;
use super::messages::{self, Parts, SealedParts, FMT_PARTS, FMT_PLAIN, PART_MIN_CHARS};
use super::settings::LEGACY_DUCKY_KEYS;
use super::tokens::{blind_text, Key};
use super::{blocking, now, seal, search, Error, Result, Store};

/// Owner of an old row the claiming account already had a copy of (the same folder or
/// ducky): the account keeps its own, the old copy stays in the table under an owner
/// nobody reads.
pub const PARKED: &str = "_parked:";
/// The account the rows from before went to.
pub(super) const CLAIMED_BY: &str = "rows_claimed_by";
const SEALED: &str = "enc1:";
/// Columns holding what the person wrote, sealed in place by [`seal_table`] (messages
/// and their payloads: [`seal_stored_messages`]).
pub const SEALED_COLUMNS: [(&str, &[&str]); 5] = [
    ("conversations", &["title", "state"]),
    ("folders", &["name"]),
    ("duckies", &["data"]),
    ("duckies_history", &["data"]),
    ("ducky_mirror_chats", &["title", "rows"]),
];
pub(super) const HAS_PARKED: [&str; 2] = ["folders", "duckies"];
const PARKED_ROWS: &str = "substr(account_id, 1, 8) = '_parked:'";
const UNCLAIMED_ROWS: &str = "SELECT EXISTS(SELECT 1 FROM conversations WHERE account_id='') \
                              OR EXISTS(SELECT 1 FROM folders WHERE account_id='') \
                              OR EXISTS(SELECT 1 FROM duckies WHERE account_id='') \
                              OR EXISTS(SELECT 1 FROM duckies_history WHERE account_id='')";

/// `work` with the space it frees zeroed.
pub(super) fn zeroing<T>(conn: &Connection, work: impl FnOnce() -> rusqlite::Result<T>) -> rusqlite::Result<T> {
    let before: i64 = conn.query_row("PRAGMA secure_delete", [], |r| r.get(0))?;
    conn.execute_batch("PRAGMA secure_delete=ON")?;
    let out = work();
    conn.execute_batch(&format!("PRAGMA secure_delete={before}"))?;
    out
}

fn claimed_by(conn: &Connection) -> rusqlite::Result<String> {
    Ok(conn.query_row("SELECT value FROM meta WHERE key=?1", [CLAIMED_BY], |r| r.get(0)).optional()?.unwrap_or_default())
}

/// The account the rows from before went to (`''` while nobody claimed any).
pub async fn claimer(store: &Store) -> Result<String> {
    store.readers.read(claimed_by).await
}

/// Rows from before become `account`'s, all of them at once. Returns how many. If the
/// database already names the account they went to (a backup sealed for it, restored
/// while another account was signed in), they go to that account instead.
///
/// A message belongs to its chat; its own `account_id` is filled when it is sealed (a
/// batch at a time), not here: rewriting every message in one transaction would hold
/// the database for as long as copying it.
pub async fn claim(store: &Store, account: String) -> Result<usize> {
    if !store.readers.read(|conn| conn.query_row(UNCLAIMED_ROWS, [], |r| r.get::<_, bool>(0))).await? {
        return Ok(0);
    }
    store
        .writer
        .write("account_rows::claim", move |conn| {
            let named = claimed_by(conn)?;
            let account = if named.is_empty() { account } else { named };
            zeroing(conn, || {
                let mut moved = conn.execute("UPDATE conversations SET account_id=?1 WHERE account_id=''", [&account])?;
                for table in HAS_PARKED {
                    moved += conn.execute(&format!("UPDATE OR IGNORE {table} SET account_id=?1 WHERE account_id=''"), [&account])?;
                    conn.execute(&format!("UPDATE {table} SET account_id=?1 || rowid WHERE account_id=''"), [PARKED])?;
                }
                moved += conn.execute("UPDATE duckies_history SET account_id=?1 WHERE account_id=''", [&account])?;
                conn.execute("INSERT OR IGNORE INTO meta(key, value, updated) VALUES (?1, ?2, ?3)", params![CLAIMED_BY, account, now()])?;
                Ok(moved)
            })
        })
        .await
}

/// (chat ids, folder ids) of one account, for a sidebar dropping them on a switch.
pub async fn owned_ids(store: &Store, account: String) -> Result<(Vec<String>, Vec<String>)> {
    store
        .readers
        .read(move |conn| {
            let ids = |sql: &str| conn.prepare(sql)?.query_map([&account], |r| r.get(0))?.collect::<rusqlite::Result<Vec<String>>>();
            Ok((ids("SELECT id FROM conversations WHERE account_id=?1")?, ids("SELECT DISTINCT id FROM folders WHERE account_id=?1")?))
        })
        .await
}

fn columns(table: &str) -> Result<&'static [&'static str]> {
    SEALED_COLUMNS.iter().find(|(name, _)| *name == table).map(|(_, cols)| *cols).ok_or_else(|| Error(format!("{table} holds nothing to seal")))
}

/// `(col <> '' AND not sealed) OR ...`, one `?` per column for the seal's prefix.
fn pending(cols: &[&str]) -> String {
    cols.iter().map(|col| format!("({col} <> '' AND substr({col}, 1, 5) <> ?)")).collect::<Vec<_>>().join(" OR ")
}

type Values = Vec<Option<String>>;

/// Seal one table's plaintext values of the account in place, a batch per write; a
/// value changed meanwhile is left to the save that changed it (it wrote it sealed).
/// `parked`: the parked copies instead, sealed for this account (the one that claimed
/// them). Returns how many rows were sealed.
pub async fn seal_table(store: &Store, who: Who, table: String, parked: bool, batch: i64) -> Result<usize> {
    let cols = columns(&table)?;
    if parked && !HAS_PARKED.contains(&table.as_str()) {
        return Err(Error(format!("{table} has no parked rows")));
    }
    let owner = if parked { PARKED_ROWS } else { "account_id=?" };
    let select = format!("SELECT rowid, {} FROM {table} WHERE {owner} AND rowid>? AND ({}) ORDER BY rowid LIMIT ?", cols.join(", "), pending(cols));
    let sets = cols.iter().map(|col| format!("{col}=?")).collect::<Vec<_>>();
    let update = format!("UPDATE {table} SET {} WHERE rowid=? AND {}", sets.join(", "), sets.join(" AND "));
    let (mut sealed, mut cursor) = (0, 0i64);
    loop {
        let (select, account) = (select.clone(), who.id.clone());
        let rows: Vec<(i64, Values)> = store
            .readers
            .read(move |conn| {
                let mut args: Vec<Sql> = if parked { Vec::new() } else { vec![account.into()] };
                args.push(cursor.into());
                args.extend(cols.iter().map(|_| Sql::from(SEALED.to_string())));
                args.push(batch.into());
                conn.prepare(&select)?.query_map(params_from_iter(args), |r| Ok((r.get(0)?, (1..=cols.len()).map(|at| r.get(at)).collect::<rusqlite::Result<_>>()?)))?.collect()
            })
            .await?;
        let Some((last, _)) = rows.last() else { return Ok(sealed) };
        cursor = *last;
        let owner = who.clone();
        let todo: Vec<(i64, Values, Values)> = blocking(move || {
            let seal = |value: &Option<String>| match value.as_deref() {
                Some(text) if !text.is_empty() && !seal::is_sealed(text) => messages::sealed(text, &owner).map(Some),
                _ => Ok(value.clone()),
            };
            rows.into_iter().map(|(rowid, old)| Ok((rowid, old.iter().map(seal).collect::<Result<_>>()?, old))).collect()
        })
        .await?;
        let update = update.clone();
        sealed += store
            .writer
            .write("account_rows::seal_table", move |conn| {
                zeroing(conn, || {
                    let mut update = conn.prepare(&update)?;
                    let mut done = 0;
                    for (rowid, new, old) in &todo {
                        let args = new.iter().cloned().map(Sql::from).chain([Sql::from(*rowid)]).chain(old.iter().cloned().map(Sql::from));
                        done += update.execute(params_from_iter(args))?;
                    }
                    Ok(done)
                })
            })
            .await?;
    }
}

/// Anything of `account` ducky.db still holds in plaintext (with the parked copies and
/// the old duckies settings when it is the account that claimed the rows from before:
/// `claimer_pass`).
pub async fn plaintext_left(store: &Store, account: String, claimer_pass: bool) -> Result<bool> {
    store
        .readers
        .read(move |conn| {
            let any = |sql: &str, args: Vec<Sql>| conn.query_row(&format!("SELECT EXISTS({sql})"), params_from_iter(args), |r| r.get::<_, bool>(0));
            for (table, cols) in SEALED_COLUMNS {
                let parked = if claimer_pass && HAS_PARKED.contains(&table) { format!(" OR {PARKED_ROWS}") } else { String::new() };
                let args = [Sql::from(account.clone())].into_iter().chain(cols.iter().map(|_| Sql::from(SEALED.to_string()))).collect();
                if any(&format!("SELECT 1 FROM {table} WHERE (account_id=?{parked}) AND ({})", pending(cols)), args)? {
                    return Ok(true);
                }
            }
            let bodies = "SELECT 1 FROM messages m JOIN conversations c ON c.id = m.conv_id WHERE c.account_id=? AND m.body <> '' AND substr(m.body, 1, 5) <> ?";
            let parts = "SELECT 1 FROM message_parts p WHERE substr(p.data, 1, 4) <> ? AND EXISTS (\
                         SELECT 1 FROM message_part_refs r JOIN messages m ON m.id = r.message_id \
                         JOIN conversations c ON c.id = m.conv_id WHERE r.hash = p.hash AND c.account_id=?)";
            if any(bodies, vec![account.clone().into(), SEALED.to_string().into()])? || any(parts, vec![seal::FILE_MAGIC.to_vec().into(), account.clone().into()])? {
                return Ok(true);
            }
            if any(&format!("SELECT 1 FROM search_docs WHERE {}", search::plain_records()), vec![account.clone().into(), account.clone().into()])? {
                return Ok(true);
            }
            let marks = vec!["?"; LEGACY_DUCKY_KEYS.len()].join(", ");
            Ok(claimer_pass && any(&format!("SELECT 1 FROM settings WHERE key IN ({marks})"), LEGACY_DUCKY_KEYS.iter().map(|key| Sql::from(key.to_string())).collect())?)
        })
        .await
}

/// A plaintext message row, ready to be written sealed.
struct Row {
    id: i64,
    old: String,
    body: String,
    fmt: i64,
    blind: String,
    parts: Parts,
    sealed: SealedParts,
}

/// Seal the account's message rows still in plaintext (saved before accounts, or by an
/// older build), a few rows per write so chats keep saving meanwhile. A large row from
/// before message parts gets the compact form on the way. Then its payloads from before.
/// Returns `{sealed, compacted, scanned, parts}`.
///
/// Resumable: a sealed row is never looked at again. A row is replaced only when it
/// still holds what was read (a save meanwhile wrote it sealed); its hash stays the same.
pub async fn seal_stored_messages(store: &Store, who: Who, key: Key, batch: i64, pause: Duration) -> Result<Value> {
    let (mut sealed, mut compacted, mut scanned, mut cursor) = (0usize, 0usize, 0usize, 0i64);
    loop {
        let account = who.id.clone();
        let rows: Vec<(i64, String, i64, Option<String>)> = store
            .readers
            .read(move |conn| {
                let sql = "SELECT m.id, m.body, m.fmt, m.text FROM messages m JOIN conversations c ON c.id = m.conv_id \
                           WHERE c.account_id=?1 AND m.id>?2 AND substr(m.body, 1, 5)<>?3 ORDER BY m.id LIMIT ?4";
                conn.prepare(sql)?.query_map(params![account, cursor, SEALED, batch], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))?.collect()
            })
            .await?;
        let Some((last, ..)) = rows.last() else { break };
        (cursor, scanned) = (*last, scanned + rows.len());
        let (owner, tokens) = (who.clone(), key.clone());
        let todo: Vec<Row> = blocking(move || {
            let prepare = |(id, old, fmt, text): (i64, String, i64, Option<String>)| {
                let compact = if fmt == FMT_PLAIN && old.chars().count() >= PART_MIN_CHARS { messages::compact_stored(&old, &tokens) } else { None };
                let (stored, fmt, parts) = compact.map_or((old.clone(), fmt, Parts::new()), |(stored, parts)| (stored, FMT_PARTS, parts));
                let sealed = parts.iter().map(|(digest, text)| Ok((digest.clone(), messages::seal_part(text, &owner)?))).collect::<Result<_>>()?;
                Ok(Row { id, body: messages::sealed(&stored, &owner)?, old, fmt, blind: blind_text(&text.unwrap_or_default(), &tokens), parts, sealed })
            };
            rows.into_iter().map(prepare).collect()
        })
        .await?;
        let owner = who.clone();
        let (wrote, packed) = store
            .writer
            .write("account_rows::seal_stored_messages", move |conn| {
                zeroing(conn, || {
                    let (mut wrote, mut packed) = (0, 0);
                    for row in &todo {
                        let now: Option<String> = conn.query_row("SELECT body FROM messages WHERE id=?1", [row.id], |r| r.get(0)).optional()?;
                        if now.as_deref() != Some(row.old.as_str()) {
                            continue; // saved again since it was read; that save sealed it
                        }
                        if !row.parts.is_empty() {
                            messages::write_parts(conn, row.id, &row.parts, &owner, &row.sealed)?;
                            packed += 1;
                        }
                        conn.execute("UPDATE messages SET body=?1, fmt=?2, text=?3, account_id=?4 WHERE id=?5", params![row.body, row.fmt, row.blind, owner.id, row.id])?;
                        wrote += 1;
                    }
                    Ok((wrote, packed))
                })
            })
            .await?;
        (sealed, compacted) = (sealed + wrote, compacted + packed);
        tokio::time::sleep(pause).await;
    }
    if sealed > 0 {
        // The index still holds the old rows' words in segments it marked deleted; one
        // merge leaves only the tokens.
        store.writer.write("account_rows::optimize", |conn| zeroing(conn, || conn.execute("INSERT INTO message_fts(message_fts) VALUES('optimize')", []).map(|_| ()))).await?;
    }
    Ok(json!({"sealed": sealed, "compacted": compacted, "scanned": scanned, "parts": seal_stored_parts(store, &who, batch).await?}))
}

/// Seal in place the plaintext payloads the account's messages use (saved before accounts).
async fn seal_stored_parts(store: &Store, who: &Who, batch: i64) -> Result<usize> {
    let (mut sealed, mut cursor) = (0, String::new());
    loop {
        let (account, after) = (who.id.clone(), cursor.clone());
        let rows: Vec<(String, Vec<u8>)> = store
            .readers
            .read(move |conn| {
                let sql = "SELECT p.hash, p.data FROM message_parts p WHERE p.hash>?1 AND substr(p.data, 1, 4)<>?2 AND EXISTS (\
                           SELECT 1 FROM message_part_refs r JOIN messages m ON m.id = r.message_id \
                           JOIN conversations c ON c.id = m.conv_id WHERE r.hash = p.hash AND c.account_id=?3) ORDER BY p.hash LIMIT ?4";
                conn.prepare(sql)?.query_map(params![after, seal::FILE_MAGIC, account, batch], |r| Ok((r.get(0)?, r.get(1)?)))?.collect()
            })
            .await?;
        let Some((last, _)) = rows.last() else { return Ok(sealed) };
        cursor = last.clone();
        let entropy = who.entropy.clone();
        let todo: Vec<(String, Vec<u8>, Vec<u8>)> =
            blocking(move || rows.into_iter().map(|(digest, old)| Ok((digest, seal::seal_bytes(&old, &entropy)?, old))).collect()).await?;
        sealed += store
            .writer
            .write("account_rows::seal_stored_parts", move |conn| {
                zeroing(conn, || {
                    let mut update = conn.prepare("UPDATE message_parts SET data=?1 WHERE hash=?2 AND data=?3")?;
                    todo.iter().try_fold(0, |done, (digest, new, old)| Ok(done + update.execute(params![new, digest, old])?))
                })
            })
            .await?;
    }
}
