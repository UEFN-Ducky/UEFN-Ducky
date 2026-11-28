//! `settings` table: one row per settings field, absence = default
//! (`backend/store/repos/settings.py`).
//!
//! A save writes only the keys that differ from the rows its caller loaded, so two
//! editors of different fields never overwrite each other.

use std::collections::BTreeMap;
use std::sync::{Mutex, MutexGuard};

use rusqlite::{params, Connection};
use serde_json::{Map, Value};

use super::pyjson::{dumps, py_eq};
use super::{now, Result, Store};

pub type Fields = Map<String, Value>;

/// Duckies lived in these fields before they got their own table. They are never saved
/// again: a stale copy in memory must not write them back.
const LEGACY_DUCKY_KEYS: [&str; 5] = [
    "agent_profiles",
    "agent_profile_overrides",
    "archived_agent_profile_ids",
    "hidden_bundled_agent_profile_ids",
    "agent_profile_visibility_explicit",
];
const HISTORY_KEEP: usize = 8;

/// The rows as they were at each of the last few loads, by snapshot id (ids never repeat).
#[derive(Default)]
struct Seen {
    last: u64,
    history: BTreeMap<u64, Fields>,
}

pub struct Settings {
    store: Store,
    seen: Mutex<Seen>,
}

fn read_rows(conn: &Connection) -> rusqlite::Result<Fields> {
    let mut stmt = conn.prepare("SELECT key, value FROM settings")?;
    let rows = stmt.query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)))?;
    let mut out = Fields::new();
    for row in rows {
        let (key, value) = row?;
        if let Ok(value) = serde_json::from_str(&value) {
            out.insert(key, value);
        }
    }
    Ok(out)
}

impl Settings {
    pub fn new(store: Store) -> Settings {
        Settings { store, seen: Mutex::default() }
    }

    fn seen(&self) -> MutexGuard<'_, Seen> {
        self.seen.lock().unwrap_or_else(|e| e.into_inner())
    }

    fn remember(&self, rows: Fields) -> u64 {
        let mut seen = self.seen();
        seen.last += 1;
        let id = seen.last;
        seen.history.insert(id, rows);
        while seen.history.len() > HISTORY_KEEP {
            seen.history.pop_first();
        }
        id
    }

    /// All persisted fields plus the id of the snapshot they came from: a caller that
    /// keeps the id saves against exactly the rows it saw.
    /// ponytail: reads the table every time (Python skips it when `data_version` has not
    /// moved); cache on `data_version` if settings loads ever show up in a profile.
    pub async fn load_fields_versioned(&self) -> Result<(Fields, u64)> {
        let rows = self.store.readers.read(read_rows).await?;
        let id = self.remember(rows.clone());
        Ok((rows, id))
    }

    pub async fn load_fields(&self) -> Result<Fields> {
        Ok(self.load_fields_versioned().await?.0)
    }

    /// Write only the keys whose value differs from the rows the caller loaded (`origin`,
    /// else the latest snapshot). A value equal to its default deletes the row. Returns
    /// the keys written.
    pub async fn save_fields(&self, new_values: Fields, defaults: Fields, origin: Option<u64>) -> Result<Vec<String>> {
        let seen = {
            let seen = self.seen();
            let rows = origin.and_then(|id| seen.history.get(&id)).or_else(|| seen.history.values().next_back());
            rows.cloned().unwrap_or_default()
        };
        let changed: Vec<String> = new_values
            .iter()
            .filter(|(name, value)| {
                let before = seen.get(*name).or_else(|| defaults.get(*name)).unwrap_or(&Value::Null);
                !LEGACY_DUCKY_KEYS.contains(&name.as_str()) && !py_eq(value, before)
            })
            .map(|(name, _)| name.clone())
            .collect();
        if changed.is_empty() {
            return Ok(changed);
        }
        let names = changed.clone();
        let rows = self
            .store
            .writer
            .write("settings::save_fields", move |conn| {
                let now = now();
                for name in &names {
                    let value = &new_values[name];
                    if defaults.get(name).is_some_and(|default| py_eq(value, default)) {
                        conn.execute("DELETE FROM settings WHERE key=?1", [name])?;
                    } else {
                        conn.execute(
                            "INSERT INTO settings(key, value, updated) VALUES (?1, ?2, ?3) \
                             ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
                            params![name, dumps(value), now],
                        )?;
                    }
                }
                read_rows(conn)
            })
            .await?;
        self.remember(rows);
        Ok(changed)
    }

    /// Importer entry: set the table to exactly `values` (non-defaults only).
    pub async fn replace_all(&self, values: Fields, defaults: Fields) -> Result<()> {
        self.store
            .writer
            .write("settings::replace_all", move |conn| {
                let now = now();
                conn.execute("DELETE FROM settings", [])?;
                for (name, value) in &values {
                    if defaults.get(name).is_some_and(|default| py_eq(value, default)) {
                        continue;
                    }
                    conn.execute(
                        "INSERT INTO settings(key, value, updated) VALUES (?1, ?2, ?3)",
                        params![name, dumps(value), now],
                    )?;
                }
                Ok(())
            })
            .await?;
        self.forget_snapshot();
        Ok(())
    }

    /// Rows changed outside [`Settings::save_fields`].
    pub fn forget_snapshot(&self) {
        self.seen().history.clear();
    }

    pub async fn has_rows(&self) -> Result<bool> {
        self.store.readers.read(|conn| conn.prepare("SELECT 1 FROM settings LIMIT 1")?.exists([])).await
    }
}
