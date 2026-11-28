//! Chats made on this PC while signed out, as the signed-in account sees them
//! (`backend/store/repos/local_chats.py`).
//!
//! The signed-in account can see them ([`Chats::waiting`]), take them ([`Chats::adopt`])
//! or delete them ([`Chats::remove`]). Taking a chat moves its rows: opened with this
//! PC's key, sealed again for the account. The chat keeps its id. What belongs together
//! moves together: a chat with its sub-agent chats, a group with every chat and folder
//! in it. A chat keeps its folders too: the account gets the folder if it has none by
//! that id, and a folder left empty behind is dropped.
//!
//! Signed out these chats are simply the list; every function here then does nothing.

use std::collections::{HashMap, HashSet};

use rusqlite::{params, params_from_iter, Connection, OptionalExtension, Row};
use serde_json::{json, Value};

use super::account::LOCAL;
use super::chats::{mark_deleted, Chats, DELETED_CHAT};
use super::duckies::{self, Who};
use super::messages::{self, opened, sealed, PartReader, Prep, SealedParts};
use super::pyjson::{dumps, truthy};
use super::tokens::Key;
use super::{now, seal, Error, Result};

const ARCHIVE: &str = "archive";

/// (project id, folder id): folder ids repeat across projects.
type Folder = (String, String);

/// This PC's own rows: what a chat made signed out is sealed with.
fn local() -> Who {
    Who { id: LOCAL.to_string(), entropy: seal::account_entropy(None) }
}

fn col(r: &Row, at: usize) -> rusqlite::Result<String> {
    Ok(r.get::<_, Option<String>>(at)?.unwrap_or_default())
}

/// The folders made signed out, in stored order: → (parent id, group hub id).
#[derive(Default)]
struct Folders {
    order: Vec<Folder>,
    at: HashMap<Folder, (String, String)>,
}

impl Folders {
    fn read(conn: &Connection) -> rusqlite::Result<Folders> {
        let mut out = Folders::default();
        let mut stmt = conn.prepare("SELECT project_id, id, parent_id, group_hub_id FROM folders WHERE account_id=?1")?;
        let mut rows = stmt.query([LOCAL])?;
        while let Some(r) = rows.next()? {
            let key = (col(r, 0)?, col(r, 1)?);
            if out.at.insert(key.clone(), (col(r, 2)?, col(r, 3)?)).is_none() {
                out.order.push(key);
            }
        }
        Ok(out)
    }

    /// The folder and the folders it sits in, innermost first (Archive is every account's own).
    fn chain(&self, project: &str, folder_id: &str) -> Vec<Folder> {
        let mut out: Vec<Folder> = Vec::new();
        let mut key = (project.to_string(), folder_id.to_string());
        while !key.1.is_empty() && key.1 != ARCHIVE && !out.contains(&key) {
            let Some((parent, _)) = self.at.get(&key) else { break };
            let above = (key.0.clone(), parent.clone());
            out.push(std::mem::replace(&mut key, above));
        }
        out
    }

    /// The outermost group folder the folder sits in ("" when it is in no group).
    fn group_of(&self, project: &str, folder_id: &str) -> String {
        self.chain(project, folder_id).into_iter().rfind(|key| !self.at[key].1.is_empty()).map(|key| key.1).unwrap_or_default()
    }
}

/// Chats made signed out that move together.
struct Unit {
    /// The chat that stands for it.
    head: String,
    chats: Vec<String>,
    /// A group's own folders.
    folders: Vec<Folder>,
    group: bool,
    archived: bool,
    updated: f64,
    /// Stored, still sealed.
    title: String,
}

struct ChatRow {
    project: String,
    folder: String,
    parent: String,
    title: String,
    updated: f64,
}

fn units(conn: &Connection) -> rusqlite::Result<Vec<Unit>> {
    let (mut order, mut rows) = (Vec::new(), HashMap::new());
    let mut stmt =
        conn.prepare("SELECT id, project_id, folder_id, parent_conv_id, title, updated FROM conversations WHERE account_id=?1")?;
    let mut found = stmt.query([LOCAL])?;
    while let Some(r) = found.next()? {
        let row = ChatRow { project: col(r, 1)?, folder: col(r, 2)?, parent: col(r, 3)?, title: col(r, 4)?, updated: r.get::<_, Option<f64>>(5)?.unwrap_or(0.0) };
        order.push(col(r, 0)?);
        rows.insert(col(r, 0)?, row);
    }
    if rows.is_empty() {
        return Ok(Vec::new());
    }
    let folders = Folders::read(conn)?;
    // (is a group, project or chat id, group folder id)
    let unit_key = |conv_id: &str| -> (bool, String, String) {
        let (mut at, mut seen) = (conv_id.to_string(), HashSet::new());
        loop {
            let row = &rows[&at];
            let group = folders.group_of(&row.project, &row.folder);
            if !group.is_empty() {
                return (true, row.project.clone(), group);
            }
            seen.insert(at.clone());
            if row.parent.is_empty() || !rows.contains_key(&row.parent) || seen.contains(&row.parent) {
                return (false, at, String::new());
            }
            at = row.parent.clone();
        }
    };
    let (mut keys, mut by_key): (Vec<_>, HashMap<_, Vec<String>>) = (Vec::new(), HashMap::new());
    for conv_id in order {
        let key = unit_key(&conv_id);
        if !by_key.contains_key(&key) {
            keys.push(key.clone());
        }
        by_key.entry(key).or_default().push(conv_id);
    }
    let mut out = Vec::new();
    for key in keys {
        let ids = by_key.remove(&key).unwrap_or_default();
        let (group, a, b) = key;
        let (mut head, mut own) = (a.clone(), Vec::new());
        if group {
            let at = (a, b);
            let hub = &folders.at[&at].1;
            // The first of the newest, as Python's max().
            let newest = ids.iter().fold(&ids[0], |best, id| if rows[id].updated > rows[best].updated { id } else { best });
            head = if ids.contains(hub) { hub.clone() } else { newest.clone() };
            own = folders.order.iter().filter(|key| key.0 == at.0 && folders.chain(&at.0, &key.1).contains(&at)).cloned().collect();
        }
        out.push(Unit {
            archived: rows[&head].folder == ARCHIVE,
            updated: ids.iter().map(|id| rows[id].updated).fold(f64::MIN, f64::max),
            title: rows[&head].title.clone(),
            head,
            chats: ids,
            folders: own,
            group,
        });
    }
    Ok(out)
}

fn chosen(units: Vec<Unit>, conv_ids: Option<&[String]>) -> Vec<Unit> {
    match conv_ids {
        None => units,
        Some(wanted) => units.into_iter().filter(|unit| unit.chats.iter().any(|id| wanted.contains(id))).collect(),
    }
}

/// Everything one chat's move writes, read and sealed for the account.
struct Move {
    id: String,
    rev: i64,
    project: String,
    folder: String,
    profile: String,
    title: String,
    state: String,
    /// (seq, the stored (hash, id), the row sealed again)
    messages: Vec<(i64, (String, i64), Prep)>,
    /// (body sealed again, row id) of rows no read could make a message of.
    unread: Vec<(String, i64)>,
}

fn prepare(conn: &Connection, conv_id: &str, who: &Who, key: &Key) -> rusqlite::Result<Move> {
    let local = local();
    let row = conn
        .query_row(
            "SELECT title, state, msg_rev, project_id, folder_id, profile_id FROM conversations WHERE id=?1 AND account_id=?2",
            [conv_id, LOCAL],
            |r| Ok((col(r, 0)?, col(r, 1)?, r.get::<_, Option<i64>>(2)?.unwrap_or(0), col(r, 3)?, col(r, 4)?, col(r, 5)?)),
        )
        .optional()?;
    let Some((title, state, rev, project, folder, profile)) = row else {
        return Err(Error("the chat is no longer one made signed out".into()).into());
    };
    let again = |stored: &str| -> rusqlite::Result<String> { Ok(sealed(&opened(stored, &local)?, who)?) };
    let mut reader = PartReader::of_chat(conn, &local, conv_id);
    let (mut rows, mut unread) = (Vec::new(), Vec::new());
    let mut stmt = conn.prepare("SELECT id, seq, hash, body, fmt FROM messages WHERE conv_id=?1 ORDER BY seq")?;
    let mut found = stmt.query([conv_id])?;
    while let Some(r) = found.next()? {
        let (id, seq, hash, body, fmt): (i64, i64, String, String, i64) = (r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?);
        match messages::message(&body, fmt, &local, &mut reader)? {
            Some(message) => {
                let prep = messages::prep_row(&message, &dumps(&message), hash.clone(), who, key)?;
                rows.push((seq, (hash, id), prep));
            }
            // No read could make a message of this row before the move either (reads
            // skip it). It goes along as it is, sealed for its new owner: left sealed
            // for this PC, opening it with the account's key would fail the chat's load.
            None => unread.push((again(&body)?, id)),
        }
    }
    let state = if state.is_empty() { "{}" } else { &state };
    Ok(Move { id: conv_id.to_string(), rev, project, folder, profile, title: again(&title)?, state: again(state)?, messages: rows, unread })
}

fn write(conn: &Connection, moved: &Move, who: &Who) -> rusqlite::Result<()> {
    let now = conn
        .query_row("SELECT account_id, msg_rev FROM conversations WHERE id=?1", [&moved.id], |r| {
            Ok((col(r, 0)?, r.get::<_, Option<i64>>(1)?.unwrap_or(0)))
        })
        .optional()?;
    if now != Some((LOCAL.to_string(), moved.rev)) {
        return Err(Error("the chat changed while it was being moved".into()).into());
    }
    // ponytail: payloads are sealed inside the transaction; seal them first
    // (`seal_new_parts`) if moving a long chat ever shows up as a slow write.
    let none = SealedParts::new();
    for (seq, row, prep) in &moved.messages {
        messages::write_row(conn, &moved.id, *seq, Some(row), prep, who, &none)?;
    }
    for (body, id) in &moved.unread {
        conn.execute("UPDATE messages SET body=?1 WHERE id=?2", params![body, id])?;
    }
    conn.execute("UPDATE messages SET account_id=?1 WHERE conv_id=?2", [&who.id, &moved.id])?;
    conn.execute(
        "UPDATE conversations SET account_id=?1, title=?2, state=?3 WHERE id=?4 AND account_id=?5",
        params![who.id, moved.title, moved.state, moved.id, LOCAL],
    )?;
    Ok(())
}

/// Folders the move left with nothing made signed out in them go (the account has its
/// own row of each now); one that still holds a chat or a folder stays for both.
fn drop_emptied(conn: &Connection, folders: &[Folder]) -> rusqlite::Result<()> {
    let mut holds_chat = conn.prepare("SELECT 1 FROM conversations WHERE account_id=?1 AND project_id=?2 AND folder_id=?3 LIMIT 1")?;
    let mut holds_folder = conn.prepare("SELECT 1 FROM folders WHERE account_id=?1 AND project_id=?2 AND parent_id=?3 LIMIT 1")?;
    let mut left: Vec<&Folder> = folders.iter().collect();
    loop {
        let mut gone = Vec::new();
        for key in &left {
            let at = [LOCAL, &key.0, &key.1];
            if !holds_chat.exists(at)? && !holds_folder.exists(at)? {
                gone.push(*key);
            }
        }
        if gone.is_empty() {
            return Ok(());
        }
        for (project, folder_id) in &gone {
            conn.execute("DELETE FROM folders WHERE account_id=?1 AND project_id=?2 AND id=?3", [LOCAL, project, folder_id])?;
        }
        left.retain(|key| !gone.contains(key));
    }
}

impl Chats {
    /// What the signed-in account can take, newest first: `{id, title, updated, chats,
    /// group}`, one row per chat or per group (with everything in it). Archived ones are
    /// left out. Signed out, or nothing made signed out: empty.
    pub async fn waiting(&self, account: String) -> Result<Vec<Value>> {
        if account == LOCAL {
            return Ok(Vec::new());
        }
        self.store
            .readers
            .read(|conn| {
                let local = local();
                let mut units: Vec<Unit> = units(conn)?.into_iter().filter(|unit| !unit.archived).collect();
                units.sort_by(|a, b| b.updated.total_cmp(&a.updated).then_with(|| a.head.cmp(&b.head)));
                let row = |unit: Unit| {
                    // A title that won't open still lists (and can be taken or deleted).
                    let title = opened(&unit.title, &local).unwrap_or_default();
                    json!({"id": unit.head, "title": title, "updated": unit.updated, "chats": unit.chats.len(), "group": unit.group})
                };
                Ok(units.into_iter().map(row).collect())
            })
            .await
    }

    /// Move chats made signed out into the signed-in account: the ones named (each with
    /// what belongs to it), or every one with `None` (archived ones too, into the
    /// account's Archive). One set at a time, each whole or not at all.
    ///
    /// Answers `{"moved": [chat ids], "failed": [the ids that stood for a set that stayed]}`.
    pub async fn adopt(&self, who: Who, conv_ids: Option<Vec<String>>) -> Result<Value> {
        let (mut moved, mut failed) = (Vec::new(), Vec::new());
        if who.id != LOCAL {
            let key = self.chat_key(&who).await?;
            for unit in chosen(self.store.readers.read(units).await?, conv_ids.as_deref()) {
                match self.adopt_unit(&unit, &who, &key).await {
                    Ok(()) => moved.extend(unit.chats),
                    // That set stays as it was, whole; the others still move.
                    Err(err) => {
                        eprintln!("a chat made signed out could not be moved into the account: {}", err.0);
                        failed.push(unit.head);
                    }
                }
            }
        }
        Ok(json!({"moved": moved, "failed": failed}))
    }

    async fn adopt_unit(&self, unit: &Unit, who: &Who, key: &Key) -> Result<()> {
        // Opening and sealing happen before the write lock; the transaction only writes.
        let (ids, own, account, chat_key) = (unit.chats.clone(), unit.folders.clone(), who.clone(), key.clone());
        let (moves, needed, copies) = self
            .store
            .readers
            .read(move |conn| {
                let moves = ids.iter().map(|id| prepare(conn, id, &account, &chat_key)).collect::<rusqlite::Result<Vec<_>>>()?;
                let folders = Folders::read(conn)?;
                let mut needed: Vec<Folder> = Vec::new();
                for key in own.into_iter().chain(moves.iter().flat_map(|moved| folders.chain(&moved.project, &moved.folder))) {
                    if !needed.contains(&key) {
                        needed.push(key);
                    }
                }
                let local = local();
                let mut copies = Vec::new();
                for (project, folder_id) in &needed {
                    let row = conn
                        .query_row(
                            "SELECT name, parent_id, sort_order, group_hub_id FROM folders WHERE account_id=?1 AND project_id=?2 AND id=?3",
                            [LOCAL, project, folder_id],
                            |r| Ok((col(r, 0)?, col(r, 1)?, r.get::<_, Option<f64>>(2)?.unwrap_or(0.0), col(r, 3)?)),
                        )
                        .optional()?;
                    if let Some((name, parent, sort_order, hub)) = row {
                        copies.push((project.clone(), folder_id.clone(), sealed(&opened(&name, &local)?, &account)?, parent, sort_order, hub));
                    }
                }
                Ok((moves, needed, copies))
            })
            .await?;
        let profiles: Vec<String> = moves.iter().map(|moved| moved.profile.clone()).filter(|profile| !profile.is_empty()).collect();
        let account = who.clone();
        self.store
            .writer
            .write("local_chats::adopt", move |conn| {
                for moved in &moves {
                    write(conn, moved, &account)?;
                }
                for (project, folder_id, name, parent, sort_order, hub) in &copies {
                    conn.execute(
                        "INSERT OR IGNORE INTO folders(account_id, project_id, id, name, parent_id, sort_order, group_hub_id) \
                         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                        params![account.id, project, folder_id, name, parent, sort_order, hub],
                    )?;
                }
                drop_emptied(conn, &needed)
            })
            .await?;
        for profile in profiles {
            // The chat moved; without its ducky it opens with the default one.
            if let Err(err) = self.bring_ducky(profile, who).await {
                eprintln!("a moved chat's ducky could not be copied to the account: {}", err.0);
            }
        }
        Ok(())
    }

    /// A chat's own ducky (one the person made signed out) comes with it as a copy,
    /// unless the account already has a ducky by that id. Shipped duckies are everyone's.
    async fn bring_ducky(&self, profile: String, who: &Who) -> Result<()> {
        let Some(theirs) = duckies::get(&self.store, local(), profile.clone()).await? else { return Ok(()) };
        if theirs["kind"] != "custom" || truthy(&theirs["deleted"]) || !truthy(&theirs["data"]) {
            return Ok(());
        }
        if duckies::get(&self.store, who.clone(), profile.clone()).await?.is_none() {
            duckies::save(&self.store, who.clone(), profile, "custom".into(), theirs["data"].clone()).await?;
        }
        Ok(())
    }

    /// Delete the named chats made signed out, each with what belongs to it, for good.
    /// Answers `(chat id, project id)` of every chat deleted, for the caller to remove
    /// their folders on disk. Signed out: nothing (the list's own delete does it).
    pub async fn remove(&self, account: String, conv_ids: Vec<String>) -> Result<Vec<(String, String)>> {
        if account == LOCAL || conv_ids.is_empty() {
            return Ok(Vec::new());
        }
        let mut deleted = Vec::new();
        for unit in chosen(self.store.readers.read(units).await?, Some(&conv_ids)) {
            let rows = self
                .store
                .writer
                .write("local_chats::remove", move |conn| {
                    let sql = format!(
                        "SELECT id, project_id FROM conversations WHERE account_id=? AND id IN ({})",
                        vec!["?"; unit.chats.len()].join(",")
                    );
                    let args = std::iter::once(LOCAL).chain(unit.chats.iter().map(String::as_str));
                    let rows: Vec<(String, String)> =
                        conn.prepare(&sql)?.query_map(params_from_iter(args), |r| Ok((col(r, 0)?, col(r, 1)?)))?.collect::<rusqlite::Result<_>>()?;
                    for (conv_id, _) in &rows {
                        conn.execute("DELETE FROM messages WHERE conv_id=?1", [conv_id])?;
                        conn.execute("DELETE FROM conversations WHERE id=?1 AND account_id=?2", [conv_id.as_str(), LOCAL])?;
                    }
                    let ids: Vec<String> = rows.iter().map(|(conv_id, _)| conv_id.clone()).collect();
                    mark_deleted(conn, DELETED_CHAT, &ids, now())?;
                    drop_emptied(conn, &unit.folders)?;
                    Ok(rows)
                })
                .await?;
            deleted.extend(rows);
        }
        Ok(deleted)
    }
}
