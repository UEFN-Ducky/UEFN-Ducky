//! The pass that seals what the signed-in account still has in plaintext
//! (`backend/store/account_rows.py::seal_pending`): rows from before accounts, copies
//! an older build wrote, its plugin search records; for the account that claimed the
//! rows from before, also their parked copies and the old duckies settings. Once
//! nothing of it is left in plaintext, ducky.db is rebuilt at the next start and its
//! backups from before are sealed. Chat attachments are arranged for the account.
//!
//! Resumable at every step: a value is replaced only by its sealed copy, in the write
//! that checks it is still the one that was read, and what marks a stage done is
//! written after the stage. While the account's data key is not here only the files
//! are done; the next sign-in or start tries again.
//!
//! ponytail: one pass at a time in this process, whoever it is for (Python: one per
//! account); a pass asked for meanwhile answers empty, as Python's does.
//! ponytail: duckies still only in the settings (a database no build since they got
//! their table has opened) are copied over by the Python backend; until it has, their
//! settings rows stay as they are.

use std::path::{Path, PathBuf};
use std::time::Duration;

use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use rusqlite::{params, params_from_iter};
use serde_json::{json, Map, Value};

use super::account_rows::{self, zeroing, CLAIMED_BY, HAS_PARKED, SEALED_COLUMNS};
use super::chats::Chats;
use super::duckies::Who;
use super::plugin_kv::{self, Owner};
use super::pyjson::{dumps, float_repr};
use super::sealed_files::Files;
use super::settings::{Settings, LEGACY_DUCKY_KEYS};
use super::{account, backups, blocking, kv, now, seal, sealed_file, search, Error, Result, Store};

/// When nothing of the claimed rows was left in plaintext.
const SEALED_AT: &str = "rows_sealed_at";
/// A pass rewrote plaintext: rebuild once it's done.
const VACUUM_WANTED: &str = "vacuum_wanted";
/// `<account>`: nothing of it was left in plaintext (the scans are skipped after).
const DONE: &str = "rows_sealed:";
const LEGACY_SETTINGS: &str = "legacy_duckies_settings:";
const SNAPSHOT_FINGERPRINT_KEY: &str = "snapshot_fingerprint";
const LEFTOVER_AGE_S: f64 = 600.0;
/// A backup this fresh may still be being written.
pub const SETTLE_S: f64 = 60.0;

static PASS: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

/// What a pass did, by name; only what is not zero.
type Counts = Map<String, Value>;

pub struct Pass<'a> {
    pub store: &'a Store,
    pub chats: &'a Chats,
    pub settings: &'a Settings,
    pub root: &'a Path,
    /// Rows per write, so chats keep saving meanwhile.
    pub batch: i64,
    pub pause: Duration,
    pub settle_s: f64,
}

fn one(name: &str) -> Counts {
    Counts::from_iter([(name.to_string(), json!(1))])
}

impl Pass<'_> {
    pub async fn run(&self) -> Result<Counts> {
        let Ok(_one_pass) = PASS.try_lock() else { return Ok(Counts::new()) };
        let mut out = Counts::new();
        let root = self.root.to_path_buf();
        if let Ok(removed) = blocking(move || Ok(sealed_file::sweep_leftovers(&root, LEFTOVER_AGE_S))).await {
            out.insert("leftovers_removed".into(), json!(removed));
        }
        let who = match account::entropy(self.store, self.root).await {
            Ok((account, entropy)) => {
                let who = Who { id: account.id, entropy };
                self.rows(&who, &mut out).await?;
                who
            }
            // Locked: the other accounts' attachments are still sealed below, and no
            // file of this one opens with no key.
            Err(err) if err.0 == account::LOCKED => Who { id: account::current(self.store).await?.id, entropy: Vec::new() },
            Err(err) => return Err(err),
        };
        if let Ok(sealed) = seal_backups(self.store.clone(), self.root.to_path_buf(), self.settle_s).await {
            out.insert("backups".into(), json!(sealed));
        }
        // A folder in use: the next sign-in or start does it.
        if let Ok(arranged) = (Files { store: self.store, root: self.root }).arrange_chat_files(&who).await {
            out.extend(arranged);
        }
        out.retain(|_, count| count.as_i64() != Some(0));
        Ok(out)
    }

    async fn left(&self, who: &Who, claimer_pass: bool) -> Result<bool> {
        account_rows::plaintext_left(self.store, who.id.clone(), claimer_pass).await
    }

    /// The account's plugin cache and prefs rows from before they were sealed
    /// (`data_crypto.ensure_migrated`): sealed in place and checked.
    async fn plugin_rows(&self, who: &Who) -> Result<()> {
        for row in plugin_kv::unsealed_rows(self.store, who.id.clone(), seal::PREFIX.into()).await? {
            let [plugin_id, scope, key, value] = [0, 1, 2, 3].map(|at| row[at].as_str().unwrap_or_default().to_string());
            let (encrypted, entropy, old) = (row[4] != 0, who.entropy.clone(), value.clone());
            let sealed = blocking(move || {
                let text = if encrypted { BASE64.decode(&value).ok().and_then(|blob| seal::unprotect_secret(&blob).ok()) } else { Some(value) };
                // A row that does not read is left alone, never guessed at.
                let Some(text) = text.filter(|text| serde_json::from_str::<Value>(text).is_ok()) else { return Ok(None) };
                let sealed = seal::seal_text(&text, &entropy)?;
                if seal::open_text(&sealed, &entropy)? != text {
                    return Err(Error("plugin data migration failed its check".into()));
                }
                Ok(Some(sealed))
            })
            .await?;
            if let Some(sealed) = sealed {
                plugin_kv::replace_value(self.store, Owner { account: who.id.clone(), scope, plugin_id }, key, sealed, old).await?;
            }
        }
        Ok(())
    }

    /// The rows, in the order Python seals them.
    async fn rows(&self, who: &Who, out: &mut Counts) -> Result<()> {
        let store = self.store;
        self.plugin_rows(who).await?;
        (Files { store, root: self.root }).ensure_keypair(who).await?;
        let key = self.chats.chat_key(who).await?;
        let claimer = account_rows::claimer(store).await?;
        let claimer_pass = claimer.is_empty() || claimer == who.id;
        let done = format!("{DONE}{}", who.id);
        // Once nothing of the account is left in plaintext, every writer seals what it
        // writes (an older build can't open this schema): later passes skip the scans.
        if kv::meta_get(store, done.clone()).await?.is_some() {
            out.extend(self.rebuild_if_wanted().await?); // a plugin's old records went meanwhile
            return Ok(());
        }
        // Asked before rewriting anything, so a pass cut short still rebuilds in the end.
        if self.left(who, claimer_pass).await? {
            kv::meta_set(store, VACUUM_WANTED.into(), "1".into()).await?;
        }
        for (table, _) in SEALED_COLUMNS {
            let mut sealed = account_rows::seal_table(store, who.clone(), table.to_string(), false, self.batch).await?;
            if claimer_pass && HAS_PARKED.contains(&table) {
                sealed += account_rows::seal_table(store, who.clone(), table.to_string(), true, self.batch).await?;
            }
            out.insert(table.into(), json!(sealed));
        }
        if claimer_pass {
            out.insert("settings".into(), json!(self.move_legacy_settings(who).await?));
        }
        let messages = account_rows::seal_stored_messages(store, who.clone(), key.clone(), self.batch, self.pause).await?;
        out.extend(messages.as_object().cloned().unwrap_or_default());
        out.insert("search_records".into(), json!(search::seal_existing(store, who.clone(), key, self.batch).await?));
        if self.left(who, claimer_pass).await? {
            return Ok(());
        }
        out.extend(self.rebuild_if_wanted().await?);
        kv::meta_set(store, done, float_repr(now())).await?;
        if claimer_pass && kv::meta_get(store, SEALED_AT.into()).await?.is_none() {
            let account = who.id.clone();
            store
                .writer
                .write("seal_pending::sealed_at", move |conn| {
                    conn.execute("INSERT OR IGNORE INTO meta(key, value, updated) VALUES (?1, ?2, ?3)", params![CLAIMED_BY, account, now()])?;
                    conn.execute("INSERT OR REPLACE INTO meta(key, value, updated) VALUES (?1, ?2, ?3)", params![SEALED_AT, float_repr(now()), now()]).map(|_| ())
                })
                .await?;
            out.extend(self.keep_a_plain_snapshot().await);
        }
        Ok(())
    }

    /// The duckies settings kept from before duckies had their own table (copied into
    /// it then): kept sealed for the account in `meta`, the plaintext rows go. Also out
    /// of the old `panel_settings.json` copy. Returns how many rows moved.
    async fn move_legacy_settings(&self, who: &Who) -> Result<usize> {
        if kv::meta_get(self.store, "imported:duckies".into()).await?.is_none() {
            return Ok(0);
        }
        let marks = vec!["?"; LEGACY_DUCKY_KEYS.len()].join(", ");
        let select = format!("SELECT key, value FROM settings WHERE key IN ({marks})");
        let rows: Counts = self
            .store
            .readers
            .read(move |conn| {
                let row = |r: &rusqlite::Row| Ok((r.get::<_, String>(0)?, json!(r.get::<_, Option<String>>(1)?.unwrap_or_default())));
                conn.prepare(&select)?.query_map(params_from_iter(LEGACY_DUCKY_KEYS), row)?.collect()
            })
            .await?;
        let mut moved = 0;
        if !rows.is_empty() {
            let entropy = who.entropy.clone();
            let sealed = blocking(move || seal::seal_text(&dumps(&Value::Object(rows)), &entropy)).await?;
            let name = format!("{LEGACY_SETTINGS}{}", who.id);
            moved = self
                .store
                .writer
                .write("seal_pending::legacy_settings", move |conn| {
                    zeroing(conn, || {
                        let kept: bool = conn.query_row("SELECT EXISTS(SELECT 1 FROM meta WHERE key=?1)", [&name], |r| r.get(0))?;
                        // Moved before; this copy is kept beside it.
                        let name = if kept { format!("{name}:{}", now() as i64) } else { name };
                        conn.execute("INSERT INTO meta(key, value, updated) VALUES (?1, ?2, ?3)", params![name, sealed, now()])?;
                        conn.execute(&format!("DELETE FROM settings WHERE key IN ({marks})"), params_from_iter(LEGACY_DUCKY_KEYS))
                    })
                })
                .await?;
            self.settings.forget_snapshot();
        }
        let root = self.root.to_path_buf();
        blocking(move || {
            backups::strip_settings_file(&root, &LEGACY_DUCKY_KEYS);
            Ok(())
        })
        .await?;
        Ok(moved)
    }

    /// Plaintext was rewritten or deleted: the search indexes drop the old words (their
    /// segments are rows a rebuild would copy), then ducky.db is rebuilt at the next start.
    async fn rebuild_if_wanted(&self) -> Result<Counts> {
        if kv::meta_get(self.store, VACUUM_WANTED.into()).await?.is_none() {
            return Ok(Counts::new());
        }
        for fts in ["message_fts", "search_fts"] {
            let optimize = move |conn: &rusqlite::Connection| zeroing(conn, || conn.execute(&format!("INSERT INTO {fts}({fts}) VALUES('optimize')"), []).map(|_| ()));
            self.store.writer.write("seal_pending::optimize", optimize).await?;
        }
        let root = self.root.to_path_buf();
        blocking(move || backups::request_vacuum(&root)).await?;
        self.store.writer.write("seal_pending::rebuild_asked", |conn| conn.execute("DELETE FROM meta WHERE key=?1", [VACUUM_WANTED]).map(|_| ())).await?;
        Ok(one("rebuild_next_start"))
    }

    /// A snapshot made now holds every claimed row sealed and no old fragments, so it
    /// stays plain: a corrupt ducky.db is always restored from it, never needing a
    /// sealed one.
    async fn keep_a_plain_snapshot(&self) -> Counts {
        let root = self.root.to_path_buf();
        // Nothing from before to seal: nothing to keep beside it.
        let made = blocking(move || if backups::files(&root).is_empty() { Ok(None) } else { backups::snapshot(&root, "sealed").map(Some) }).await;
        // A failed copy: the daily snapshot makes one later.
        let Ok(Some(named)) = made else { return Counts::new() };
        let _ = kv::meta_set(self.store, SNAPSHOT_FINGERPRINT_KEY.into(), dumps(&named)).await;
        one("plain_snapshot")
    }
}

/// ducky.db backups made before the claimed rows were all sealed, sealed for the
/// account they went to (with its public key: it needn't be the one signed in).
async fn seal_backups(store: Store, root: PathBuf, settle_s: f64) -> Result<usize> {
    let owner = account_rows::claimer(&store).await?;
    let sealed_at = kv::meta_get(&store, SEALED_AT.into()).await?.and_then(|at| at.parse::<f64>().ok());
    let (Some(sealed_at), false) = (sealed_at, owner.is_empty()) else { return Ok(0) };
    let Some(public) = (Files { store: &store, root: &root }).public_key(&owner).await? else { return Ok(0) };
    let dir = root.clone();
    // Only copies started before then: one started after holds sealed rows only.
    let (sealed, waiting) = blocking(move || Ok((backups::seal(&dir, &owner, &public, sealed_at, settle_s), backups::waiting(&dir, sealed_at, settle_s)))).await?;
    if waiting {
        seal_backups_later(store, root, settle_s);
    }
    Ok(sealed)
}

/// One written moments ago (the copy made before the upgrade) is sealed once it has
/// settled, not only at the next start.
fn seal_backups_later(store: Store, root: PathBuf, settle_s: f64) {
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_secs_f64(settle_s + 1.0)).await;
        let _ = seal_backups(store, root, settle_s).await;
    });
}
