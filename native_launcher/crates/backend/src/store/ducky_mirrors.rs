//! `ducky_mirror_chats` / `ducky_mirror_peers`: read-only copies of your other UEFN
//! Duckies' chats (`backend/store/repos/ducky_mirrors.py`). Copies only: a chat lives on
//! its home Ducky, so nothing here is ever sent back or run.
//!
//! A copy's title and rows are sealed for its account; copies an older build stored
//! plain still read.

use rusqlite::{params, OptionalExtension};
use serde_json::{json, Value};

use super::duckies::Who;
use super::messages::{opened, sealed};
use super::pyjson::dumps;
use super::{now, Result, Store};

/// One mirrored chat: whose, from which Ducky.
#[derive(Clone)]
pub struct Chat {
    pub ducky: String,
    pub conv_id: String,
}

const THIS: &str = "account_id=?1 AND ducky=?2 AND conv_id=?3";

/// Record a chat the home Ducky lists. True when its rows need pulling: never pulled,
/// or changed there since the rows here (a failed pull stays True).
pub async fn note_chat(store: &Store, who: Who, chat: Chat, title: String, updated: f64) -> Result<bool> {
    store
        .writer
        .write("ducky_mirrors::note_chat", move |conn| {
            let me = params![who.id, chat.ducky, chat.conv_id];
            let stored: Option<String> = conn.query_row(&format!("SELECT title FROM ducky_mirror_chats WHERE {THIS}"), me, |r| r.get(0)).optional()?;
            // Every round lists every chat: a title that didn't change isn't sealed again.
            let stored = match stored {
                Some(stored) if opened(&stored, &who)? == title => stored,
                _ => sealed(&title, &who)?,
            };
            conn.execute(
                "INSERT INTO ducky_mirror_chats(account_id, ducky, conv_id, title, updated, gone) VALUES (?1, ?2, ?3, ?4, ?5, 0) \
                 ON CONFLICT(account_id, ducky, conv_id) DO UPDATE SET title=excluded.title, updated=excluded.updated, gone=0",
                params![who.id, chat.ducky, chat.conv_id, stored, updated],
            )?;
            let rows_of: f64 = conn.query_row(&format!("SELECT rows_of FROM ducky_mirror_chats WHERE {THIS}"), me, |r| r.get(0))?;
            Ok(rows_of < 0.0 || updated > rows_of)
        })
        .await
}

/// The chat's newest rows, as of the home's updated time `of`.
pub async fn put_rows(store: &Store, who: Who, chat: Chat, rows: Vec<Value>, of: f64) -> Result<()> {
    store
        .writer
        .write("ducky_mirrors::put_rows", move |conn| {
            let text = sealed(&dumps(&Value::Array(rows.clone())), &who)?;
            conn.execute(
                &format!("UPDATE ducky_mirror_chats SET rows=?4, rows_of=?5, synced=?6 WHERE {THIS}"),
                params![who.id, chat.ducky, chat.conv_id, text, of, now()],
            )
            .map(|_| ())
        })
        .await
}

/// Chats the home Ducky no longer lists drop out of the lists; their copies stay.
pub async fn mark_gone(store: &Store, account: String, ducky: String, listed: Vec<String>) -> Result<usize> {
    store
        .writer
        .write("ducky_mirrors::mark_gone", move |conn| {
            let have: Vec<String> = conn
                .prepare("SELECT conv_id FROM ducky_mirror_chats WHERE account_id=?1 AND ducky=?2 AND gone=0")?
                .query_map([&account, &ducky], |r| r.get(0))?
                .collect::<rusqlite::Result<_>>()?;
            let gone: Vec<&String> = have.iter().filter(|conv| !listed.contains(conv)).collect();
            for conv in &gone {
                conn.execute(&format!("UPDATE ducky_mirror_chats SET gone=1 WHERE {THIS}"), [&account, &ducky, conv])?;
            }
            Ok(gone.len())
        })
        .await
}

pub async fn note_peer(store: &Store, account: String, ducky: String, name: String) -> Result<()> {
    store
        .writer
        .write("ducky_mirrors::note_peer", move |conn| {
            conn.execute(
                "INSERT INTO ducky_mirror_peers(account_id, ducky, name, synced) VALUES (?1, ?2, ?3, ?4) \
                 ON CONFLICT(account_id, ducky) DO UPDATE SET name=excluded.name, synced=excluded.synced",
                params![account, ducky, name, now()],
            )
            .map(|_| ())
        })
        .await
}

/// Drop one other Ducky and all its copies here: how many chats went.
pub async fn forget_peer(store: &Store, account: String, ducky: String) -> Result<usize> {
    store
        .writer
        .write("ducky_mirrors::forget_peer", move |conn| {
            let gone = conn.execute("DELETE FROM ducky_mirror_chats WHERE account_id=?1 AND ducky=?2", [&account, &ducky])?;
            conn.execute("DELETE FROM ducky_mirror_peers WHERE account_id=?1 AND ducky=?2", [&account, &ducky])?;
            Ok(gone)
        })
        .await
}

fn real(r: &rusqlite::Row, at: usize) -> rusqlite::Result<f64> {
    Ok(r.get::<_, Option<f64>>(at)?.unwrap_or(0.0))
}

/// Every other Ducky with copies here, its chats newest first (rows left out).
pub async fn peers(store: &Store, who: Who) -> Result<Vec<Value>> {
    store
        .readers
        .read(move |conn| {
            let peers: Vec<(String, String, f64)> = conn
                .prepare("SELECT ducky, name, synced FROM ducky_mirror_peers WHERE account_id=?1 ORDER BY name, ducky")?
                .query_map([&who.id], |r| Ok((r.get(0)?, r.get(1)?, real(r, 2)?)))?
                .collect::<rusqlite::Result<_>>()?;
            let mut chats = conn.prepare(
                "SELECT conv_id, title, updated, synced FROM ducky_mirror_chats WHERE account_id=?1 AND ducky=?2 AND gone=0 ORDER BY updated DESC",
            )?;
            let mut out = Vec::new();
            for (ducky, name, synced) in peers {
                let chats = chats
                    .query_map([&who.id, &ducky], |r| {
                        let title = opened(&r.get::<_, Option<String>>(1)?.unwrap_or_default(), &who)?;
                        Ok(json!({"conv_id": r.get::<_, String>(0)?, "title": title, "updated": real(r, 2)?, "synced": real(r, 3)?}))
                    })?
                    .collect::<rusqlite::Result<Vec<Value>>>()?;
                out.push(json!({"ducky": ducky, "name": name, "synced": synced, "chats": chats}));
            }
            Ok(out)
        })
        .await
}

/// One copy: `{title, updated, rows, synced}`, or None when it is not here.
pub async fn rows(store: &Store, who: Who, chat: Chat) -> Result<Option<Value>> {
    store
        .readers
        .read(move |conn| {
            conn.query_row(&format!("SELECT title, updated, rows, synced FROM ducky_mirror_chats WHERE {THIS}"), params![who.id, chat.ducky, chat.conv_id], |r| {
                let text = opened(&r.get::<_, Option<String>>(2)?.unwrap_or_default(), &who)?;
                // Rows that are not a JSON list read as none.
                let rows = serde_json::from_str::<Value>(&text).ok().filter(Value::is_array).unwrap_or_else(|| json!([]));
                let title = opened(&r.get::<_, Option<String>>(0)?.unwrap_or_default(), &who)?;
                Ok(json!({"title": title, "updated": real(r, 1)?, "rows": rows, "synced": real(r, 3)?}))
            })
            .optional()
        })
        .await
}
