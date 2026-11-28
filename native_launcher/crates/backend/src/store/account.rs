//! Who is signed in on this PC, and the second secret their rows are sealed with
//! (`frontend/duckyos_account.py::account_key`, `backend/uefn_plugins/scopes.py::account_id_for`,
//! `backend/uefn_plugins/data_crypto.py`).
//!
//! The Python backend signs in, fetches the account data key from the server and caches
//! it on disk; this side only reads the login row and that cache. No cache yet means the
//! account's data is locked here: nothing of it is read or written.
//! ponytail: the login row is read on every call (Python memoizes it on
//! `meta.login_version`); do the same if it shows up in a profile.

use std::path::Path;

use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use serde_json::Value;
use sha2::{Digest, Sha256};

use super::pyjson::truthy;
use super::{seal, secrets, Error, Result, Store};

/// The account id of signed-out rows.
pub const LOCAL: &str = "_local";
/// What a caller is told while the account data key is not on this PC.
pub const LOCKED: &str = "Waiting for this account's data key. Your chats open once you're online.";
const KEY_CACHE_NAME: &str = "account_data_key.bin";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Account {
    /// `site|email`, empty signed out.
    pub key: String,
    /// The id rows and folders carry: `_local`, or `a_` + 16 hex digits.
    pub id: String,
    /// The login's device token; wraps the data key cache.
    pub device_token: String,
}

/// Python's `str(value or "")` for the login fields.
fn text(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Number(n) => n.to_string(),
        _ => String::new(),
    }
}

/// Who a login blob is (`duckyos_account._key_of`): empty unless it holds a device token
/// or a session.
pub fn key_of(login: &Value) -> String {
    if !(truthy(&login["device_key"]) || truthy(&login["session_value"])) {
        return String::new();
    }
    let who = if truthy(&login["email"]) { &login["email"] } else { &login["user_id"] };
    format!("{}|{}", text(&login["base_url"]).trim_end_matches('/'), text(who).trim().to_lowercase())
}

/// Row and folder id of an account key (`scopes.account_id_for`).
pub fn account_id_for(key: &str) -> String {
    if key.is_empty() {
        return LOCAL.to_string();
    }
    let digest = Sha256::digest(key.as_bytes());
    format!("a_{}", digest[..8].iter().map(|byte| format!("{byte:02x}")).collect::<String>())
}

/// The login row (`duckyos_account._load_blob`): an object, or null signed out.
pub async fn login(store: &Store) -> Result<Value> {
    let login = store
        .readers
        .read(|conn| {
            // A row that does not open (another Windows user's, damaged) is "signed out".
            let opened = secrets::blob(conn, secrets::LOGIN_NAME)?.and_then(|blob| seal::unprotect_secret(&blob).ok());
            Ok(opened.and_then(|text| serde_json::from_str::<Value>(&text).ok()).filter(Value::is_object))
        })
        .await?;
    Ok(login.unwrap_or(Value::Null))
}

/// The account signed in now, read from the login row.
pub async fn current(store: &Store) -> Result<Account> {
    let login = login(store).await?;
    let key = key_of(&login);
    Ok(Account { id: account_id_for(&key), key, device_token: text(&login["device_key"]) })
}

/// The account's data key from its disk cache (`data_crypto._read_cache`): wrapped for
/// this OS user and the login's device token. Blocks: a file read and an unseal.
pub fn cached_data_key(data_dir: &Path, account: &Account) -> Option<[u8; 32]> {
    #[allow(clippy::disallowed_methods)] // callers run this on a blocking thread
    let raw = std::fs::read(data_dir.join(KEY_CACHE_NAME)).ok()?;
    let doc: Value = serde_json::from_slice(&raw).ok()?;
    if account.device_token.is_empty() || doc["account"] != account.id.as_str() {
        return None;
    }
    let wrapped = BASE64.decode(doc["wrapped"].as_str()?).ok()?;
    let token = [b"device:", account.device_token.as_bytes()].concat();
    seal::unprotect(&wrapped, Some(&token)).ok()?.try_into().ok()
}

/// The signed-in account and the entropy its rows are sealed with; [`LOCKED`] while its
/// data key is not on this PC.
pub async fn entropy(store: &Store, data_dir: &Path) -> Result<(Account, Vec<u8>)> {
    let account = current(store).await?;
    if account.id == LOCAL {
        return Ok((account, seal::account_entropy(None)));
    }
    let (dir, who) = (data_dir.to_path_buf(), account.clone());
    let key = tokio::task::spawn_blocking(move || cached_data_key(&dir, &who)).await.ok().flatten();
    match key {
        Some(key) => Ok((account, seal::account_entropy(Some(&key)))),
        None => Err(Error(LOCKED.into())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_login_names_its_account_as_python_does() {
        let login = json!({"device_key": "tok", "email": " Alice@Example.COM ", "base_url": "https://uefnducky.org/"});
        assert_eq!(key_of(&login), "https://uefnducky.org|alice@example.com");
        // No token and no session is signed out, whatever else the row holds.
        assert_eq!(key_of(&json!({"email": "a@b.c", "base_url": "https://x"})), "");
        assert_eq!(key_of(&json!({"session_value": "s", "user_id": 42})), "|42");
        assert_eq!(key_of(&Value::Null), "");
        assert_eq!(account_id_for(""), "_local");
        // hashlib.sha256(b"https://uefnducky.org|alice@example.com").hexdigest()[:16]
        assert_eq!(account_id_for("https://uefnducky.org|alice@example.com"), "a_a0074e8954cdd7b4");
    }
}
