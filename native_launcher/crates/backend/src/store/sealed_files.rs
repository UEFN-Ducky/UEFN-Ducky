//! Files sealed for this PC's accounts (`backend/store/sealed_files.py`): each account's
//! file keypair, and chat attachments while their owner isn't signed in here.
//!
//! An account's public key is in `meta`, so anyone can seal for it; its private key is
//! sealed for the account, so it opens only while that account is signed in here. Chat
//! attachments follow who is signed in ([`Files::arrange_chat_files`]): the signed-in
//! account's are plain on disk (agents read them by path), every other account's are
//! sealed. The file format is [`super::sealed_file`].
//!
//! ponytail: an opened private key is not kept between calls as Python keeps it; each
//! call opens it once. Keep it (and forget it at sign-in) if that shows up in a trace.
//! Sealing ducky.db backups is `backups`.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Map, Value};
use x25519_dalek::StaticSecret;

use super::account::{self, LOCAL};
use super::duckies::Who;
use super::pyjson::sorted;
use super::sealed_file::{self as file, WRONG_ACCOUNT};
use super::{blocking, now, seal, Error, Result, Store};

const PUB_META: &str = "file_key_pub:";
const PRIV_META: &str = "file_key_priv:";
const ARRANGED_META: &str = "chat_files_for";
/// The keypairs live in ducky.db and in this file beside it: restoring an older backup
/// of ducky.db (one from before a keypair was made) must not lock that account's files
/// for good.
const KEYS_FILE: &str = "account_file_keys.json";

/// One pass over the chat attachments at a time.
static ARRANGING: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

/// (public key, private key sealed for the account), both as stored.
type Pair = (String, String);

fn meta(conn: &Connection, key: &str) -> rusqlite::Result<Option<String>> {
    conn.query_row("SELECT value FROM meta WHERE key=?1", [key], |r| r.get(0)).optional()
}

fn set_meta(conn: &Connection, key: &str, value: &str) -> rusqlite::Result<()> {
    conn.execute(
        "INSERT INTO meta(key, value, updated) VALUES (?1, ?2, ?3) \
         ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
        params![key, value, now()],
    )?;
    Ok(())
}

/// The keys file: account → `{"pub", "priv"}`. Missing or damaged reads as empty.
fn read_keys(root: &Path) -> Map<String, Value> {
    let doc = file::read_plain(&root.join(KEYS_FILE)).ok().and_then(|raw| serde_json::from_slice(&raw).ok());
    match doc {
        Some(Value::Object(keys)) => keys.into_iter().filter(|(_, pair)| pair.is_object()).collect(),
        _ => Map::new(),
    }
}

fn pair_of(pubkey: Option<&str>, private: Option<&str>) -> Option<Pair> {
    match (pubkey, private) {
        (Some(pubkey), Some(private)) if !pubkey.is_empty() && !private.is_empty() => Some((pubkey.to_string(), private.to_string())),
        _ => None,
    }
}

fn pair_in(keys: &Map<String, Value>, account: &str) -> Option<Pair> {
    let pair = keys.get(account)?;
    pair_of(pair["pub"].as_str(), pair["priv"].as_str())
}

fn pair_in_db(conn: &Connection, account: &str) -> rusqlite::Result<Option<Pair>> {
    let (pubkey, private) = (meta(conn, &format!("{PUB_META}{account}"))?, meta(conn, &format!("{PRIV_META}{account}"))?);
    Ok(pair_of(pubkey.as_deref(), private.as_deref()))
}

/// The account's pair from the file, else ducky.db.
fn stored_pair(conn: &Connection, root: &Path, account: &str) -> rusqlite::Result<Option<Pair>> {
    match pair_in(&read_keys(root), account) {
        Some(pair) => Ok(Some(pair)),
        None => pair_in_db(conn, account),
    }
}

/// The store and the app data folder the files live in.
pub struct Files<'a> {
    pub store: &'a Store,
    pub root: &'a Path,
}

impl Files<'_> {
    /// The account's file keypair, made the first time it is signed in here.
    pub async fn ensure_keypair(&self, who: &Who) -> Result<()> {
        let (root, account) = (self.root.to_path_buf(), who.id.clone());
        let have = self
            .store
            .readers
            .read(move |conn| {
                let Some((pubkey, _)) = pair_in(&read_keys(&root), &account) else { return Ok(false) };
                Ok(meta(conn, &format!("{PUB_META}{account}"))? == Some(pubkey))
            })
            .await?;
        if have {
            return Ok(());
        }
        let entropy = who.entropy.clone();
        let fresh: Pair = blocking(move || {
            let (private, public) = file::keypair();
            Ok((BASE64.encode(public), seal::seal_text(&BASE64.encode(private), &entropy)?))
        })
        .await?;
        let (root, account) = (self.root.to_path_buf(), who.id.clone());
        // One writer at a time across processes: the file and ducky.db always end up
        // holding the same pair, whichever was there first.
        self.store
            .writer
            .write("sealed_files::ensure_keypair", move |conn| {
                let mut keys = read_keys(&root);
                let pair = match pair_in(&keys, &account) {
                    Some(pair) => pair,
                    None => {
                        let pair = pair_in_db(conn, &account)?.unwrap_or(fresh);
                        keys.insert(account.clone(), json!({"pub": pair.0, "priv": pair.1}));
                        let text = serde_json::to_vec_pretty(&sorted(&Value::Object(keys))).unwrap_or_default();
                        file::write_whole(&root.join(KEYS_FILE), &text)?;
                        pair
                    }
                };
                set_meta(conn, &format!("{PUB_META}{account}"), &pair.0)?;
                set_meta(conn, &format!("{PRIV_META}{account}"), &pair.1)
            })
            .await
    }

    pub async fn public_key(&self, account: &str) -> Result<Option<[u8; 32]>> {
        let (root, account) = (self.root.to_path_buf(), account.to_string());
        let pair = self.store.readers.read(move |conn| stored_pair(conn, &root, &account)).await?;
        let Some((pubkey, _)) = pair else { return Ok(None) };
        let key = BASE64.decode(pubkey).ok().and_then(|raw| raw.try_into().ok());
        key.map(Some).ok_or_else(|| Error("this account's file key is damaged".into()))
    }

    /// The account's private key: only `who` itself can open it.
    async fn private_key(&self, who: &Who) -> Result<StaticSecret> {
        let (root, who) = (self.root.to_path_buf(), who.clone());
        self.store
            .readers
            .read(move |conn| {
                let Some((_, sealed)) = stored_pair(conn, &root, &who.id)? else { return Err(Error(WRONG_ACCOUNT.into()).into()) };
                let raw: Option<[u8; 32]> = BASE64.decode(seal::open_text(&sealed, &who.entropy)?).ok().and_then(|raw| raw.try_into().ok());
                Ok(raw.map(StaticSecret::from).ok_or_else(|| Error("this account's file key is damaged".into()))?)
            })
            .await
    }

    /// Seal `path` in place for `account`. False when it already is sealed (or the
    /// account has no keypair yet).
    pub async fn seal_file(&self, path: PathBuf, account: String) -> Result<bool> {
        let Some(public) = self.public_key(&account).await? else { return Ok(false) };
        blocking(move || file::seal_file(&path, &account, &public)).await
    }

    /// The signed-in account's key, for a sealed `path` that must be `account`'s:
    /// `None` when the file is plain. A file's key opens only while its account is
    /// signed in, so asking for another account's is refused.
    async fn key_for(&self, path: &Path, account: Option<&str>, who: &Who) -> Result<Option<StaticSecret>> {
        let sealed = {
            let path = path.to_path_buf();
            blocking(move || Ok(file::is_sealed_file(&path))).await?
        };
        if !sealed {
            return Ok(None);
        }
        if account.is_some_and(|account| account != who.id) {
            return Err(Error(WRONG_ACCOUNT.into()));
        }
        self.private_key(who).await.map(Some)
    }

    /// Open sealed `path` in place (`account`: the one it must be for). False when it
    /// was not sealed.
    pub async fn open_file(&self, path: PathBuf, account: Option<&str>, who: &Who) -> Result<bool> {
        let Some(private) = self.key_for(&path, account, who).await? else { return Ok(false) };
        let owner = who.id.clone();
        blocking(move || file::open_file(&path, &owner, &private)).await
    }

    /// Write the plaintext of sealed `path` to `dest` (a new file swapped in whole).
    pub async fn open_to(&self, path: PathBuf, dest: PathBuf, account: Option<&str>, who: &Who) -> Result<()> {
        let Some(private) = self.key_for(&path, account, who).await? else { return Err(Error("file is not sealed".into())) };
        let owner = who.id.clone();
        blocking(move || file::open_to(&path, &dest, &owner, &private)).await
    }

    /// A file's contents, opened if it is sealed.
    pub async fn read_bytes(&self, path: PathBuf, account: Option<&str>, who: &Who) -> Result<Vec<u8>> {
        let private = self.key_for(&path, account, who).await?;
        let owner = who.id.clone();
        blocking(move || match private {
            Some(private) => file::read_sealed(&path, &owner, &private),
            None => file::read_plain(&path),
        })
        .await
    }

    /// Open every file in `folder` sealed for `who`; one that fails stays sealed (the
    /// next read tries again).
    async fn open_in(&self, folder: PathBuf, who: &Who) -> Result<usize> {
        let Ok(private) = self.private_key(who).await else { return Ok(0) };
        let owner = who.id.clone();
        blocking(move || {
            let mine = |path: &PathBuf| file::sealed_for(path).as_deref() == Some(owner.as_str());
            Ok(file::files(&folder).iter().filter(|path| mine(path) && file::open_file(path, &owner, &private).unwrap_or(false)).count())
        })
        .await
    }

    /// Open every attachment sealed for `account` in one chat's attachments `folder`
    /// now (first read after a sign-in, before the background pass got to that chat).
    pub async fn open_chat_folder(&self, folder: PathBuf, account: &str, who: &Who) -> Result<usize> {
        if account != who.id {
            return Ok(0);
        }
        self.open_in(folder, who).await
    }

    /// Open every attachment in one chat's `folder` that is sealed for this PC's
    /// signed-out owner (`_local`), whoever is signed in: that key is this OS user's,
    /// no account's. For a chat made signed out that now belongs to the signed-in
    /// account (the caller checked that it does).
    pub async fn open_local_folder(&self, folder: PathBuf) -> Result<usize> {
        self.open_in(folder, &Who { id: LOCAL.to_string(), entropy: seal::account_entropy(None) }).await
    }

    async fn set_arranged(&self, account: Option<String>) -> Result<()> {
        self.store
            .writer
            .write("sealed_files::set_arranged", move |conn| match account {
                Some(account) => set_meta(conn, ARRANGED_META, &account),
                None => conn.execute("DELETE FROM meta WHERE key=?1", [ARRANGED_META]).map(|_| ()),
            })
            .await
    }

    /// Chat attachments on disk follow who is signed in: `who`'s are opened (it is
    /// signed in, its data key is here), every other account's chats' are sealed for
    /// their owner. Done once per sign-in, one pass at a time; a pass stops as soon as
    /// someone else signs in (that sign-in's own pass takes over), and a file in use is
    /// tried again by the next pass, since only a whole pass marks the account done.
    pub async fn arrange_chat_files(&self, who: &Who) -> Result<Map<String, Value>> {
        let signed_in = || async { Ok::<_, Error>(account::current(self.store).await?.id == who.id) };
        if !signed_in().await? {
            return Ok(Map::new());
        }
        let _one_pass = ARRANGING.lock().await;
        let (arranged, owners) = self
            .store
            .readers
            .read(|conn| {
                let owners: HashMap<String, String> = conn
                    .prepare("SELECT id, account_id FROM conversations")?
                    .query_map([], |r| Ok((r.get(0)?, r.get::<_, Option<String>>(1)?.unwrap_or_default())))?
                    .collect::<rusqlite::Result<_>>()?;
                Ok((meta(conn, ARRANGED_META)?, owners))
            })
            .await?;
        if arranged.as_deref() == Some(who.id.as_str()) {
            return Ok(Map::new());
        }
        self.set_arranged(None).await?; // a pass cut short must never look done for anyone
        let private = self.private_key(who).await.ok();
        let mut publics: HashMap<String, Option<[u8; 32]>> = HashMap::new();
        let (mut opened, mut sealed, mut left) = (0, 0, 0);
        let root = self.root.to_path_buf();
        for (conv_id, folder) in blocking(move || Ok(file::chat_folders(&root))).await? {
            // Not a saved chat (or one from before accounts that nobody claimed yet).
            let Some(owner) = owners.get(&conv_id).cloned() else { continue };
            if !signed_in().await? {
                left += 1;
                break;
            }
            let mine = owner == who.id;
            if !mine && !publics.contains_key(&owner) {
                publics.insert(owner.clone(), self.public_key(&owner).await?);
            }
            let (private, public) = (private.clone(), publics.get(&owner).copied().flatten());
            let did = blocking(move || {
                let (mut opened, mut sealed, mut left) = (0, 0, 0);
                for path in file::files(&folder) {
                    let done = match (mine, &private, &public) {
                        (true, _, _) if file::sealed_for(&path).as_deref() != Some(owner.as_str()) => Ok(false),
                        (true, Some(private), _) => file::open_file(&path, &owner, private),
                        // No file keys for its owner yet: sealed once they have some.
                        (true, None, _) | (false, _, None) => Err(Error(WRONG_ACCOUNT.into())),
                        (false, _, Some(public)) => file::seal_file(&path, &owner, public),
                    };
                    match done {
                        Ok(did) if mine => opened += did as usize,
                        Ok(did) => sealed += did as usize,
                        Err(_) => left += 1,
                    }
                }
                Ok((opened, sealed, left))
            })
            .await?;
            (opened, sealed, left) = (opened + did.0, sealed + did.1, left + did.2);
        }
        if left == 0 && signed_in().await? {
            self.set_arranged(Some(who.id.clone())).await?;
        }
        let counts = [("attachments_opened", opened), ("attachments_sealed", sealed), ("attachments_left", left)];
        Ok(counts.into_iter().filter(|(_, n)| *n > 0).map(|(name, n)| (name.to_string(), json!(n))).collect())
    }
}
