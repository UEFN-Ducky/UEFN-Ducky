//! `secrets` table: one sealed blob per name (`backend/store/repos/secrets.py`). The
//! caller owns the sealing ([`super::seal::protect_secret`]); plaintext never gets here.
//!
//! A write or delete of the login row bumps `meta.login_version` in the same
//! transaction, so every process can tell with one tiny read that the login changed.

use rusqlite::{params, Connection, OptionalExtension};

use super::{now, Result, Store};

/// The row holding the Ducky account login (`frontend.duckyos_account._CREDENTIALS_KEY`).
pub const LOGIN_NAME: &str = "duckyos_account";
const LOGIN_VERSION_KEY: &str = "login_version";

fn bump_login_version(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute(
        "INSERT INTO meta(key, value, updated) VALUES (?1, '1', ?2) ON CONFLICT(key) DO UPDATE SET \
         value=CAST(CAST(value AS INTEGER) + 1 AS TEXT), updated=excluded.updated",
        params![LOGIN_VERSION_KEY, now()],
    )?;
    Ok(())
}

pub(crate) fn blob(conn: &Connection, name: &str) -> rusqlite::Result<Option<Vec<u8>>> {
    conn.query_row("SELECT blob FROM secrets WHERE name=?1", [name], |row| row.get(0)).optional()
}

/// Changes whenever the login row is written or deleted; `"0"` before the first write.
pub async fn login_version(store: &Store) -> Result<String> {
    let found = store
        .readers
        .read(|conn| {
            conn.query_row("SELECT value FROM meta WHERE key=?1", [LOGIN_VERSION_KEY], |row| row.get(0)).optional()
        })
        .await?;
    Ok(found.unwrap_or_else(|| "0".to_string()))
}

pub async fn get_blob(store: &Store, name: String) -> Result<Option<Vec<u8>>> {
    store.readers.read(move |conn| blob(conn, &name)).await
}

pub async fn all_blobs(store: &Store) -> Result<Vec<(String, Vec<u8>)>> {
    store
        .readers
        .read(|conn| {
            conn.prepare("SELECT name, blob FROM secrets ORDER BY name")?
                .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))?
                .collect()
        })
        .await
}

pub async fn set_blob(store: &Store, name: String, blob: Vec<u8>) -> Result<()> {
    store
        .writer
        .write("secrets::set_blob", move |conn| {
            conn.execute(
                "INSERT INTO secrets(name, blob, updated) VALUES (?1, ?2, ?3) \
                 ON CONFLICT(name) DO UPDATE SET blob=excluded.blob, updated=excluded.updated",
                params![name, blob, now()],
            )?;
            if name == LOGIN_NAME {
                bump_login_version(conn)?;
            }
            Ok(())
        })
        .await
}

pub async fn delete(store: &Store, name: String) -> Result<()> {
    store
        .writer
        .write("secrets::delete", move |conn| {
            conn.execute("DELETE FROM secrets WHERE name=?1", [&name])?;
            if name == LOGIN_NAME {
                bump_login_version(conn)?;
            }
            Ok(())
        })
        .await
}

pub async fn replace_all(store: &Store, blobs: Vec<(String, Vec<u8>)>) -> Result<()> {
    store
        .writer
        .write("secrets::replace_all", move |conn| {
            let now = now();
            conn.execute("DELETE FROM secrets", [])?;
            for (name, blob) in &blobs {
                conn.execute("INSERT INTO secrets(name, blob, updated) VALUES (?1, ?2, ?3)", params![name, blob, now])?;
            }
            bump_login_version(conn) // an import or restore may change the login
        })
        .await
}

pub async fn has_rows(store: &Store) -> Result<bool> {
    store.readers.read(|conn| conn.prepare("SELECT 1 FROM secrets LIMIT 1")?.exists([])).await
}
