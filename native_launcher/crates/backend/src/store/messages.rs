//! Message rows and their stored payloads (the row half of `backend/store/repos/chats.py`).
//!
//! Messages are one row each, so an append or an in-flight checkpoint writes one row
//! instead of the whole transcript. A message field of [`PART_MIN_CHARS`] or more (a
//! tool result, tool arguments, a long reply) is stored once in `message_parts`,
//! compressed and sealed, and the row keeps a reference to it (`fmt` 1). Reads put the
//! fields back, so callers always see the message exactly as it was saved.
//!
//! ponytail: no opened-row or opened-part cache as Python keeps; every read opens its
//! rows. Add one when loading a long chat shows up in a trace.

use std::collections::{HashMap, HashSet};
use std::io::{Read, Write};

use flate2::read::ZlibDecoder;
use flate2::write::ZlibEncoder;
use flate2::Compression;
use rusqlite::{params, params_from_iter, Connection};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use super::duckies::Who;
use super::pyjson::{dumps, float, pystr, truthy};
use super::tokens::{blind_text, part_digest, Key};
use super::{now, seal, Result};

/// Message rows: the body is the message JSON, or an envelope whose large fields live
/// in `message_parts`.
pub const FMT_PLAIN: i64 = 0;
pub const FMT_PARTS: i64 = 1;
pub const PART_MIN_CHARS: usize = 8 * 1024;

/// Stored payloads to write: (keyed digest, the field's JSON), in the order met.
pub type Parts = Vec<(String, String)>;
/// Payloads sealed before the write lock: digest → (sealed blob, uncompressed bytes).
pub type SealedParts = HashMap<String, (Vec<u8>, i64)>;
/// A stored row at one seq: (hash, id).
type Stored = HashMap<i64, (String, i64)>;

/// A row's hash: of the whole message JSON, whichever way the row is stored.
pub fn hash(text: &str) -> String {
    Sha256::digest(text.as_bytes())[..8].iter().map(|byte| format!("{byte:02x}")).collect()
}

/// Python's `len(text)`: characters, not bytes.
fn chars(text: &str) -> usize {
    text.chars().count()
}

/// Plaintext of a stored value: sealed ones are opened, rows from before pass as they are.
pub(super) fn opened(stored: &str, who: &Who) -> Result<String> {
    if seal::is_sealed(stored) {
        seal::open_text(stored, &who.entropy)
    } else {
        Ok(stored.to_string())
    }
}

/// `text` sealed for the account; an empty value stays empty (nothing to hide).
pub(super) fn sealed(text: &str, who: &Who) -> Result<String> {
    if text.is_empty() {
        Ok(String::new())
    } else {
        seal::seal_text(text, &who.entropy)
    }
}

/// What full-text search indexes for one message.
pub fn search_text(message: &Map<String, Value>) -> String {
    let first = |from: &Map<String, Value>, keys: &[&str]| {
        keys.iter().filter_map(|key| from.get(*key)).find(|value| truthy(value)).map(pystr).unwrap_or_default()
    };
    if message.get("role").and_then(Value::as_str) != Some("assistant") {
        return first(message, &["text", "content"]);
    }
    let mut parts = vec![first(message, &["content"])];
    for block in message.get("blocks").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_object) {
        let name = block.get("name").map(pystr).unwrap_or_default();
        let arguments = block.get("arguments").filter(|value| truthy(value)).map_or("{}".to_string(), pystr);
        parts.push(format!("{name} {arguments}"));
        if let Some(result) = block.get("result").and_then(Value::as_object) {
            parts.push(first(result, &["text", "hint"]));
        }
    }
    parts.retain(|part| !part.is_empty());
    parts.join(" ")
}

// --------------------------------------------------------------------------- parts

/// The message with its large fields set to null, where they were, and the fields.
/// Large means [`PART_MIN_CHARS`] or more of JSON: a top-level field, or one field of
/// a block (`blocks[i].result`), so a checkpoint that adds a tool call stores only the
/// new result and the earlier ones are shared.
fn split_parts(message: &Map<String, Value>, key: &Key) -> (Map<String, Value>, Vec<Value>, Parts) {
    let mut refs: Vec<Value> = Vec::new();
    let mut parts: Parts = Vec::new();
    let mut take = |mut path: Vec<Value>, value: &Value| -> Value {
        if matches!(value, Value::Null | Value::Bool(_) | Value::Number(_)) {
            return value.clone();
        }
        let text = dumps(value);
        if text.len() < PART_MIN_CHARS || chars(&text) < PART_MIN_CHARS {
            return value.clone();
        }
        let digest = part_digest(&text, key);
        if !parts.iter().any(|(have, _)| *have == digest) {
            parts.push((digest.clone(), text));
        }
        path.push(json!(digest));
        refs.push(Value::Array(path));
        Value::Null
    };
    let mut skeleton = Map::new();
    for (name, value) in message {
        let kept = match value {
            Value::Array(blocks) if name == "blocks" => {
                let fields = |i: usize, block: &Value, take: &mut dyn FnMut(Vec<Value>, &Value) -> Value| match block {
                    Value::Object(block) => Value::Object(
                        block.iter().map(|(field, value)| (field.clone(), take(vec![json!("blocks"), json!(i), json!(field)], value))).collect(),
                    ),
                    other => take(vec![json!("blocks"), json!(i)], other),
                };
                Value::Array(blocks.iter().enumerate().map(|(i, block)| fields(i, block, &mut take)).collect())
            }
            other => take(vec![json!(name)], other),
        };
        skeleton.insert(name.clone(), kept);
    }
    (skeleton, refs, parts)
}

/// (stored body, fmt, parts) for a message whose JSON is `body`.
fn compact(message: &Value, body: &str, key: &Key) -> (String, i64, Parts) {
    let plain = || (body.to_string(), FMT_PLAIN, Vec::new());
    let Value::Object(message) = message else { return plain() };
    if body.len() < PART_MIN_CHARS || chars(body) < PART_MIN_CHARS {
        return plain();
    }
    let (skeleton, refs, parts) = split_parts(message, key);
    if refs.is_empty() {
        return plain();
    }
    (dumps(&json!({"m": skeleton, "x": refs})), FMT_PARTS, parts)
}

/// Put the stored fields back into a `fmt` 1 body; None when it is not a message envelope.
fn decode_parts(envelope: Value, part_text: &mut dyn FnMut(&str) -> rusqlite::Result<Option<String>>) -> rusqlite::Result<Option<Value>> {
    let Value::Object(mut envelope) = envelope else { return Ok(None) };
    let Some(Value::Object(message)) = envelope.remove("m") else { return Ok(None) };
    let mut message = Value::Object(message);
    for reference in envelope.get("x").and_then(Value::as_array).into_iter().flatten() {
        let Some((digest, path)) = reference.as_array().and_then(|steps| steps.split_last()) else { return Ok(None) };
        let Some((last, steps)) = path.split_last() else { return Ok(None) };
        let mut target = &mut message;
        for step in steps {
            let next = match (target, step) {
                (Value::Object(map), Value::String(name)) => map.get_mut(name),
                (Value::Array(items), Value::Number(at)) => at.as_u64().and_then(|at| items.get_mut(at as usize)),
                _ => None,
            };
            let Some(next) = next else { return Ok(None) };
            target = next;
        }
        // A missing part is never expected (a part goes only with its last ref): the
        // field reads as empty and the rest of the message is kept.
        let value = match part_text(&pystr(digest))? {
            Some(text) => match serde_json::from_str(&text) {
                Ok(value) => value,
                Err(_) => return Ok(None),
            },
            None => Value::Null,
        };
        match (target, last) {
            (Value::Object(map), Value::String(name)) => {
                map.insert(name.clone(), value);
            }
            (Value::Array(items), Value::Number(at)) => match at.as_u64().and_then(|at| items.get_mut(at as usize)) {
                Some(slot) => *slot = value,
                None => return Ok(None),
            },
            _ => return Ok(None),
        }
    }
    Ok(Some(message))
}

/// The compact form of a stored `fmt` 0 body, only if it reads back as the same JSON.
pub(super) fn compact_stored(body: &str, key: &Key) -> Option<(String, Parts)> {
    let message: Value = serde_json::from_str(body).ok()?;
    let (stored, fmt, parts) = compact(&message, body, key);
    if fmt != FMT_PARTS {
        return None;
    }
    let mut part = |digest: &str| Ok(parts.iter().find(|(have, _)| have == digest).map(|(_, text)| text.clone()));
    let again = decode_parts(serde_json::from_str(&stored).ok()?, &mut part).ok()??;
    (dumps(&again) == dumps(&message)).then_some((stored, parts))
}

pub(super) fn seal_part(text: &str, who: &Who) -> Result<(Vec<u8>, i64)> {
    let mut packer = ZlibEncoder::new(Vec::new(), Compression::new(6));
    // Writing into a Vec cannot fail.
    let packed = packer.write_all(text.as_bytes()).and_then(|()| packer.finish()).unwrap_or_default();
    Ok((seal::seal_bytes(&packed, &who.entropy)?, text.len() as i64))
}

/// A stored payload's JSON; None when it does not unpack (read as a missing part).
fn open_part(blob: &[u8], who: &Who) -> Result<Option<String>> {
    // Payloads from before sealing pass as they are.
    let packed = if blob.starts_with(seal::FILE_MAGIC) { seal::open_bytes(blob, &who.entropy)? } else { blob.to_vec() };
    let mut text = String::new();
    Ok(ZlibDecoder::new(packed.as_slice()).read_to_string(&mut text).ok().map(|_| text))
}

/// Before the write lock: the `parts` not stored yet, sealed, so the transaction only
/// inserts them. One stored meanwhile is skipped there; one this missed is sealed there.
pub fn seal_new_parts(conn: &Connection, parts: &Parts, who: &Who) -> rusqlite::Result<SealedParts> {
    let mut have = conn.prepare("SELECT 1 FROM message_parts WHERE hash=?1")?;
    let mut out = SealedParts::new();
    for (digest, text) in parts {
        if !out.contains_key(digest) && !have.exists([digest])? {
            out.insert(digest.clone(), seal_part(text, who)?);
        }
    }
    Ok(out)
}

/// Point the row at exactly `parts`: store new ones (sealed, from `sealed` when it has
/// them), drop refs it no longer uses.
pub(super) fn write_parts(conn: &Connection, message_id: i64, parts: &Parts, who: &Who, sealed: &SealedParts) -> rusqlite::Result<()> {
    let old: HashSet<String> = conn
        .prepare("SELECT hash FROM message_part_refs WHERE message_id=?1")?
        .query_map([message_id], |r| r.get(0))?
        .collect::<rusqlite::Result<_>>()?;
    for (digest, text) in parts.iter().filter(|(digest, _)| !old.contains(digest)) {
        if !conn.prepare("SELECT 1 FROM message_parts WHERE hash=?1")?.exists([digest])? {
            let fresh;
            let (blob, size) = match sealed.get(digest) {
                Some(ready) => ready,
                None => {
                    fresh = seal_part(text, who)?;
                    &fresh
                }
            };
            conn.execute("INSERT INTO message_parts(hash, data, size, created) VALUES (?1, ?2, ?3, ?4)", params![digest, blob, size, now()])?;
        }
        conn.execute("INSERT INTO message_part_refs(message_id, hash) VALUES (?1, ?2)", params![message_id, digest])?;
    }
    for stale in old.iter().filter(|digest| !parts.iter().any(|(kept, _)| kept == *digest)) {
        conn.execute("DELETE FROM message_part_refs WHERE message_id=?1 AND hash=?2", params![message_id, stale])?;
    }
    Ok(())
}

/// Part JSON by digest for one chat or one message: read in one query on the first
/// miss, opened on first use.
pub struct PartReader<'a> {
    conn: &'a Connection,
    who: &'a Who,
    sql: &'static str,
    of: rusqlite::types::Value,
    loaded: Option<HashMap<String, Vec<u8>>>,
}

impl<'a> PartReader<'a> {
    pub fn of_chat(conn: &'a Connection, who: &'a Who, conv_id: &str) -> Self {
        let sql = "SELECT hash, data FROM message_parts WHERE hash IN (SELECT r.hash FROM message_part_refs r \
                   JOIN messages m ON m.id = r.message_id WHERE m.conv_id=?1)";
        PartReader { conn, who, sql, of: conv_id.to_string().into(), loaded: None }
    }

    pub fn of_message(conn: &'a Connection, who: &'a Who, message_id: i64) -> Self {
        let sql = "SELECT p.hash, p.data FROM message_parts p JOIN message_part_refs r ON r.hash = p.hash WHERE r.message_id=?1";
        PartReader { conn, who, sql, of: message_id.into(), loaded: None }
    }

    fn text(&mut self, digest: &str) -> rusqlite::Result<Option<String>> {
        if self.loaded.is_none() {
            let blobs = self.conn.prepare(self.sql)?.query_map([&self.of], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<rusqlite::Result<_>>()?;
            self.loaded = Some(blobs);
        }
        match self.loaded.as_ref().and_then(|blobs| blobs.get(digest)) {
            Some(blob) => Ok(open_part(blob, self.who)?),
            None => Ok(None),
        }
    }
}

/// One stored row as the message it was saved as; None when it isn't one.
pub fn message(body: &str, fmt: i64, who: &Who, parts: &mut PartReader) -> rusqlite::Result<Option<Value>> {
    let Ok(value) = serde_json::from_str::<Value>(&opened(body, who)?) else { return Ok(None) };
    if fmt == FMT_PARTS {
        return decode_parts(value, &mut |digest| parts.text(digest));
    }
    Ok(Some(value))
}

/// The transcript from seq `start` (to `stop`, exclusive) as (seq, row hash, message).
/// A row that is not a message is left out.
pub fn messages(conn: &Connection, conv_id: &str, who: &Who, start: i64, stop: Option<i64>) -> rusqlite::Result<Vec<(i64, String, Value)>> {
    let mut stmt = conn.prepare("SELECT seq, hash, body, fmt FROM messages WHERE conv_id=?1 AND seq>=?2 AND seq<?3 ORDER BY seq")?;
    let mut rows = stmt.query(params![conv_id, start, stop.unwrap_or(1 << 62)])?;
    let mut parts = PartReader::of_chat(conn, who, conv_id);
    let mut out = Vec::new();
    while let Some(r) = rows.next()? {
        let (seq, hash, body, fmt): (i64, String, String, i64) = (r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?);
        if let Some(message) = message(&body, fmt, who, &mut parts)? {
            out.push((seq, hash, message));
        }
    }
    Ok(out)
}

// --------------------------------------------------------------------------- writing rows

/// Everything a row write needs that reads no database (big fields as parts, the seal,
/// search text), so it runs before the write lock.
pub struct Prep {
    pub hash: String,
    role: String,
    ts: f64,
    run_id: String,
    blind: String,
    body: String,
    fmt: i64,
    pub parts: Parts,
}

pub fn prep_row(source: &Value, body: &str, hash: String, who: &Who, key: &Key) -> Result<Prep> {
    let (stored, fmt, parts) = compact(source, body, key);
    let field = |name: &str| source.as_object().and_then(|message| message.get(name)).filter(|value| truthy(value));
    Ok(Prep {
        hash,
        role: field("role").map(pystr).unwrap_or_default(),
        ts: field("ts").and_then(float).unwrap_or(0.0),
        run_id: field("run_id").map(pystr).unwrap_or_default(),
        blind: source.as_object().map(|message| blind_text(&search_text(message), key)).unwrap_or_default(),
        body: sealed(&stored, who)?,
        fmt,
        parts,
    })
}

/// Write one message row; `row` is the existing (hash, id) at `seq`, or None to insert.
pub fn write_row(conn: &Connection, conv_id: &str, seq: i64, row: Option<&(String, i64)>, prep: &Prep, who: &Who, sealed: &SealedParts) -> rusqlite::Result<()> {
    let message_id = match row {
        Some((_, id)) => {
            conn.execute(
                "UPDATE messages SET hash=?1, role=?2, ts=?3, run_id=?4, text=?5, body=?6, fmt=?7 WHERE id=?8",
                params![prep.hash, prep.role, prep.ts, prep.run_id, prep.blind, prep.body, prep.fmt, id],
            )?;
            *id
        }
        None => {
            conn.execute(
                "INSERT INTO messages(conv_id, seq, hash, role, ts, run_id, text, body, fmt, account_id) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
                params![conv_id, seq, prep.hash, prep.role, prep.ts, prep.run_id, prep.blind, prep.body, prep.fmt, who.id],
            )?;
            conn.last_insert_rowid()
        }
    };
    if !prep.parts.is_empty() || row.is_some() {
        write_parts(conn, message_id, &prep.parts, who, sealed)?;
    }
    Ok(())
}

/// (hash, id) by seq for the listed `rows` and every row from `start` on.
fn stored_rows(conn: &Connection, conv_id: &str, rows: &[i64], start: i64) -> rusqlite::Result<Stored> {
    let listed = if rows.is_empty() { String::new() } else { format!(" OR seq IN ({})", vec!["?"; rows.len()].join(",")) };
    let sql = format!("SELECT seq, hash, id FROM messages WHERE conv_id=? AND (seq>=?{listed})");
    let args = [conv_id.to_string().into(), start.into()].into_iter().chain(rows.iter().map(|seq| rusqlite::types::Value::from(*seq)));
    conn.prepare(&sql)?.query_map(params_from_iter(args), |r| Ok((r.get(0)?, (r.get(1)?, r.get(2)?))))?.collect()
}

/// (listed rows below the tail, tail start) a save of `len` messages encodes.
pub fn dirty_span(len: usize, dirty_from: Option<i64>, dirty_rows: &[i64]) -> (Vec<i64>, i64) {
    let Some(dirty_from) = dirty_from else { return (Vec::new(), 0) };
    let tail = dirty_from.clamp(0, len as i64);
    let mut rows: Vec<i64> = dirty_rows.iter().copied().filter(|seq| (0..tail).contains(seq)).collect();
    rows.sort_unstable();
    rows.dedup();
    (rows, tail)
}

/// Rows encoded before the write lock: each one's (JSON characters, hash), the full row
/// write for those whose stored hash differed then, and their new parts, sealed.
#[derive(Default)]
pub struct Encoded {
    pre: HashMap<i64, (usize, String)>,
    prepped: HashMap<i64, Prep>,
    pub sealed: SealedParts,
}

/// What [`encode_rows`] and [`sync`] work for: the account, its chat key, the chat.
pub struct Chat<'a> {
    pub conv_id: &'a str,
    pub who: &'a Who,
    pub key: &'a Key,
    /// Each row's hash when this copy last read or saved it.
    pub base: Option<&'a [String]>,
}

fn seqs(rows: &[i64], from: i64, len: usize) -> impl Iterator<Item = i64> + '_ {
    rows.iter().copied().chain(from..len as i64)
}

/// Before the write lock: encode the listed `rows` and `messages[tail..]`. The
/// transaction re-reads the hashes; a row it needs that isn't here is encoded there.
pub fn encode_rows(conn: &Connection, chat: &Chat, messages: &[Value], rows: &[i64], tail: i64) -> rusqlite::Result<Encoded> {
    let mut enc = Encoded::default();
    let mut bodies = HashMap::new();
    for seq in seqs(rows, tail, messages.len()) {
        let body = dumps(&messages[seq as usize]);
        enc.pre.insert(seq, (chars(&body), hash(&body)));
        bodies.insert(seq, body);
    }
    if enc.pre.is_empty() {
        return Ok(enc);
    }
    let stored = stored_rows(conn, chat.conv_id, rows, tail)?;
    let mut parts: Parts = Vec::new();
    for seq in seqs(rows, tail, messages.len()) {
        let h = &enc.pre[&seq].1;
        let based = chat.base.and_then(|base| base.get(seq as usize)) == Some(h);
        if stored.get(&seq).is_some_and(|(have, _)| have == h || based) {
            continue; // unchanged, or another writer's (theirs): not written
        }
        let prep = prep_row(&messages[seq as usize], &bodies[&seq], h.clone(), chat.who, chat.key)?;
        parts.extend(prep.parts.iter().cloned());
        enc.prepped.insert(seq, prep);
    }
    enc.sealed = seal_new_parts(conn, &parts, chat.who)?;
    Ok(enc)
}

/// Write the listed `rows` (all below `start`) and `messages[start..]` where the stored
/// row's hash differs and (`truncate`) drop rows past the end; other rows below `start`
/// are not read. With a base, a row the copy didn't change (its hash is the base) that
/// another writer did is left as theirs (`theirs` lists those seqs). `hashes` is each
/// seq's stored row hash from `start` on, `dirty_hashes` the same for the listed rows.
pub fn sync(conn: &Connection, chat: &Chat, messages: &[Value], start: i64, truncate: bool, rows: &[i64], enc: &Encoded) -> rusqlite::Result<Map<String, Value>> {
    let existing = stored_rows(conn, chat.conv_id, rows, start)?;
    let (mut hashes, mut dirty_hashes, mut theirs) = (Vec::new(), Map::new(), Vec::new());
    let (mut inserted, mut updated, mut deleted, mut nbytes) = (0, 0, 0, 0);
    for seq in seqs(rows, start, messages.len()) {
        let source = &messages[seq as usize];
        let mut body = None;
        let (size, h) = match enc.pre.get(&seq) {
            Some((size, h)) => (*size, h.clone()),
            None => {
                let text = body.insert(dumps(source));
                (chars(text), hash(text))
            }
        };
        let row = existing.get(&seq);
        let known = chat.base.and_then(|base| base.get(seq as usize)).filter(|known| !known.is_empty());
        let mut got = h.clone();
        match row {
            // Unchanged here, changed by another writer since this copy read it: theirs is newer.
            Some((have, _)) if *have != h && known == Some(&h) => {
                got = have.clone();
                theirs.push(seq);
            }
            Some((have, _)) if *have == h => {}
            _ => {
                nbytes += size;
                let fresh;
                let prep = match enc.prepped.get(&seq) {
                    Some(prep) => prep,
                    None => {
                        let body = body.unwrap_or_else(|| dumps(source));
                        fresh = prep_row(source, &body, h, chat.who, chat.key)?;
                        &fresh
                    }
                };
                write_row(conn, chat.conv_id, seq, row, prep, chat.who, &enc.sealed)?;
                if row.is_some() {
                    updated += 1;
                } else {
                    inserted += 1;
                }
            }
        }
        if seq < start {
            dirty_hashes.insert(seq.to_string(), json!(got));
        } else {
            hashes.push(got);
        }
    }
    if truncate && existing.keys().any(|seq| *seq >= messages.len() as i64) {
        deleted = conn.execute("DELETE FROM messages WHERE conv_id=?1 AND seq>=?2", params![chat.conv_id, messages.len() as i64])?;
    }
    let stats = json!({
        "inserted": inserted, "updated": updated, "deleted": deleted, "bytes": nbytes, "hashes": hashes,
        "dirty_hashes": dirty_hashes, "theirs": theirs,
    });
    Ok(stats.as_object().cloned().unwrap_or_default())
}
