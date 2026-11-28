//! Something new arrived: tells the person (and agents) when another PC pushed to the
//! island (`urc/island_watch.py`). One task watches the island UEFN has open:
//!
//! - one `lore --remote history 1` a round reads the remote head; while the island is
//!   behind, `lore status` too, so a pull made anywhere clears the alert;
//! - only when one of them moved, [`lore::repo_state`] says which remote revisions the
//!   island lacks and who made them;
//! - only for news worth an alert, [`lore::overlap`] names the files changed on both
//!   PCs, and again each round while the island stays behind after that alert: a file
//!   newly changed on both gives one follow-up alert;
//! - each round that reached lore also reads the island's file locks. Locks never alert.
//!
//! Revisions made on another PC (a check-in of this PC's own autosave doesn't count)
//! give one alert per new remote head and an `island_updates` event. It never pulls.
//! What it found is kept in `workspace_state` ([`KEY`], [`LOCKS_KEY`]) for every process.
//!
//! Which island is open, whether the person is away and the alert itself come from the
//! [`Host`]. ponytail: the app's host (the listener's health answer kept while UEFN
//! runs, the idle time, the notification) is still Python's, so nothing starts
//! [`Watch::run`] yet.

use std::future::Future;
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, LazyLock};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};
use tokio::sync::{Mutex, Notify};

use super::lore;
use crate::api::project_paths::text;
use crate::api::Api;
use crate::store::kv;
use crate::store::pyjson::{pystr, truthy};

/// The person is at this PC: news shows in ~5 s on average.
pub const ACTIVE_S: f64 = 10.0;
/// Away from the keyboard: the toast waits for them anyway.
pub const IDLE_S: f64 = 60.0;
pub const NO_ISLAND_S: f64 = 15.0;
/// The open island has no `.lore` folder.
pub const NOT_LORE_S: f64 = 120.0;
pub const ERROR_MAX_S: f64 = 300.0;
pub const KEY: &str = "island_updates";
/// The open island's file locks.
pub const LOCKS_KEY: &str = "island_locks";
/// A locks document older than this many rounds (and the slack) means nothing.
const LOCKS_STALE_ROUNDS: f64 = 3.0;
const LOCKS_SLACK_S: f64 = 15.0;
/// Rounds in a row with no island (UEFN closed): the watch stops.
const STOP_AFTER_MISSES: u32 = 2;
const TABLE: &str = "workspace_state";

/// An alert's title, status and detail.
pub type Alert = [String; 3];

/// What the watch asks of the app around it.
pub trait Host: Send + Sync {
    /// The island UEFN has open, "" for none.
    fn open_island(&self) -> impl Future<Output = String> + Send;
    /// The person has been away from this PC for a while.
    fn away(&self) -> bool;
    fn alert(&self, title: &str, status: &str, detail: &str);
    /// The wait between two rounds.
    fn pause(&self, seconds: f64) -> impl Future<Output = ()> + Send {
        tokio::time::sleep(Duration::from_secs_f64(seconds))
    }
}

#[derive(Default)]
struct St {
    island: String,
    remote_sig: String,
    local_sig: String,
    behind: bool,
    /// The remote head the person was told of.
    told: String,
    fails: i32,
    /// Files changed on both PCs.
    both: Vec<String>,
    /// The last behind `repo_state`: the overlap's input.
    state: Option<Value>,
    /// Rounds in a row with no island.
    misses: u32,
}

#[derive(Default)]
pub struct Watch {
    st: Mutex<St>,
    wake: Notify,
    stop: AtomicBool,
    running: AtomicBool,
}

/// The one watch of this process.
pub static WATCH: LazyLock<Watch> = LazyLock::new(Watch::default);

fn now_s() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0.0, |since| since.as_secs_f64())
}

async fn read(api: &Api, key: &str) -> Option<Value> {
    kv::get_doc(api.repos.store().await.ok()?, TABLE, key.into()).await.ok()?.filter(Value::is_object)
}

/// A failed write is not an error: agents just don't see this round.
async fn write(api: &Api, key: &str, doc: Option<Value>) {
    let Ok(store) = api.repos.store().await else { return };
    let _ = match doc {
        Some(doc) => kv::set_doc(store, TABLE, key.into(), doc).await,
        None => kv::delete_doc(store, TABLE, key.into()).await,
    };
}

/// `str(value or "")`.
fn word(value: &Value) -> String {
    if truthy(value) { pystr(value) } else { String::new() }
}

fn plural(count: usize) -> &'static str {
    if count == 1 { "" } else { "s" }
}

fn name_of(island: &str) -> String {
    Path::new(island).file_name().map(|name| name.to_string_lossy().into_owned()).unwrap_or_default()
}

/// The first three, and how many more there are.
fn shown(rels: &[String]) -> String {
    let more = if rels.len() > 3 { format!(" (+{} more)", rels.len() - 3) } else { String::new() };
    rels[..rels.len().min(3)].join(", ") + &more
}

/// Who made `news`, each once, in order.
fn authors(news: &[Value]) -> Vec<Value> {
    let mut out: Vec<Value> = Vec::new();
    for creator in news.iter().map(|row| &row["creator"]).filter(|creator| truthy(creator)) {
        if !out.contains(creator) {
            out.push(creator.clone());
        }
    }
    out
}

/// "3 new changes on Isle from Mich ou: “moved the cube”".
pub fn news_text(name: &str, news: &[Value]) -> String {
    let mut out = format!("{} new change{} on {name}", news.len(), plural(news.len()));
    let authors: Vec<String> = authors(news).iter().map(pystr).collect();
    if !authors.is_empty() {
        out += &format!(" from {}", authors.join(", "));
    }
    if let Some(message) = news.first().map(|row| &row["message"]).filter(|message| truthy(message)) {
        out += &format!(": “{}”", pystr(message));
    }
    out
}

/// "1 file changed on both PCs: Content/x.verse — pull with care" ("" for none).
pub fn both_text(rels: &[String]) -> String {
    if rels.is_empty() {
        return String::new();
    }
    format!("{} file{} changed on both PCs: {} — pull with care", rels.len(), plural(rels.len()), shown(rels))
}

/// One spelling for a file path on both sides (this watch, the bridge's guard):
/// absolute, lower case on Windows, forward slashes. The disk is not read.
pub fn norm_path(path: &str) -> String {
    let mut out = PathBuf::new();
    for part in std::path::absolute(path).unwrap_or_else(|_| PathBuf::from(path)).components() {
        match part {
            Component::ParentDir => drop(out.pop()),
            Component::CurDir => {}
            part => out.push(part),
        }
    }
    let out = text(&out).replace('\\', "/");
    if cfg!(windows) { out.to_lowercase() } else { out }
}

/// One round on an open Lore island.
struct Round<'a> {
    api: &'a Api,
    st: &'a mut St,
    island: &'a str,
    away: bool,
    alerts: Vec<Alert>,
}

impl Round<'_> {
    fn interval(&self) -> f64 {
        if self.away { IDLE_S } else { ACTIVE_S }
    }

    fn failed(&mut self) -> f64 {
        self.st.fails = self.st.fails.saturating_add(1);
        (self.interval() * 2f64.powi(self.st.fails)).min(ERROR_MAX_S)
    }

    fn event(&self, name: &str, behind: bool, count: usize, detail: &str) {
        self.api.publish(vec![json!({"type": KEY, "island": self.island, "name": name, "behind": behind, "count": count, "detail": detail})]);
    }

    /// Files the remote changed that this PC changed too; none when lore can't say.
    async fn both_changed(&self, state: Value) -> Vec<String> {
        let answer = lore::overlap(self.island, Some(state), 60.0).await;
        let files = answer["files"].as_array().filter(|_| truthy(&answer["ok"]));
        files.into_iter().flatten().map(|file| pystr(&file["rel"])).collect()
    }

    async fn apply(&mut self, state: Value) {
        let name = name_of(self.island);
        let was_behind = self.st.behind;
        let news: Vec<Value> = state["incoming"].as_array().into_iter().flatten().filter(|row| !truthy(&row["own"])).cloned().collect();
        let behind = truthy(&state["behind"]);
        self.st.remote_sig = word(&state["remote_signature"]);
        self.st.local_sig = word(&state["local_signature"]);
        self.st.behind = behind;
        let said = if news.is_empty() { String::new() } else { news_text(&name, &news) };
        self.st.state = behind.then(|| state.clone());
        if !behind {
            self.st.both.clear();
        }
        let count = news.len();
        if behind && count > 0 && self.st.remote_sig != self.st.told {
            self.st.told = self.st.remote_sig.clone();
            self.st.both = self.both_changed(state.clone()).await;
            let detail = if self.st.both.is_empty() { said.clone() } else { format!("{said}; {}", both_text(&self.st.both)) };
            let more = "Pull when you're ready: ask a Ducky to run revision_pull (it never pulls over unsaved work).";
            self.alerts.push([name.clone(), format!("{count} new change{} to pull.", plural(count)), format!("{detail}. {more}")]);
            self.event(&name, true, count, &detail);
        } else if was_behind && !behind {
            self.event(&name, false, 0, &format!("{name} is up to date."));
        }
        let latest = news.first().filter(|_| behind).map(|row| json!({"revision": row["revision"], "by": row["creator"], "message": row["message"]}));
        let doc = json!({
            "island": self.island,
            "name": name,
            "behind": behind,
            "new_changes": if behind { count } else { 0 },
            "authors": if behind { authors(&news) } else { Vec::new() },
            "latest": latest,
            "text": if behind { said } else { String::new() },
            "both_changed": self.st.both,
            "local_revision": state["local_revision"],
            "remote_revision": state["remote_revision"],
            "told": self.st.told,
            "checked_at": now_s() as i64,
            "pid": std::process::id(),
        });
        write(self.api, KEY, Some(doc)).await;
    }

    /// Behind and already told: the overlap again with this round's working tree (one
    /// diff). Files newly changed on both PCs give one follow-up alert.
    async fn recheck_both(&mut self, local: &Value) {
        let Some(mut state) = self.st.state.clone().filter(|_| !self.st.told.is_empty()) else { return };
        if let Some(changes) = local.get("changes") {
            state["changes"] = changes.clone();
        }
        let both = self.both_changed(state).await;
        if both == self.st.both {
            return;
        }
        let new: Vec<String> = both.iter().filter(|rel| !self.st.both.contains(rel)).cloned().collect();
        self.st.both = both;
        if !new.is_empty() {
            let name = name_of(self.island);
            let detail = format!("{} {} also changed on this PC — pull with care", shown(&new), if new.len() == 1 { "was" } else { "were" });
            self.alerts.push([name.clone(), "Changed on both PCs.".into(), format!("{detail}. Ask a Ducky to run revision_pull when you're ready.")]);
            self.event(&name, true, new.len(), &detail);
        }
        if let Some(mut doc) = read(self.api, KEY).await.filter(|doc| doc["island"].as_str() == Some(self.island)) {
            doc["both_changed"] = json!(self.st.both);
            doc["checked_at"] = json!(now_s() as i64);
            write(self.api, KEY, Some(doc)).await;
        }
    }

    /// One `lore lock query`; the document is replaced only when it worked (else it
    /// goes stale).
    async fn refresh_locks(&self, every: f64) {
        let answer = lore::locks(self.island, 60.0).await;
        if !truthy(&answer["ok"]) {
            return;
        }
        let row = |lock: &Value| json!({"path": lock["path"], "rel": lock["rel"], "owner": lock["owner"], "mine": lock["mine"]});
        let rows: Vec<Value> = answer["locks"].as_array().into_iter().flatten().map(row).collect();
        let others: Vec<Value> = rows.iter().filter(|lock| !truthy(&lock["mine"])).map(|lock| lock["rel"].clone()).collect();
        let doc = json!({
            "island": self.island,
            "me": word(&answer["me"]),
            "locks": rows,
            "others": others,
            "checked_at": now_s() as i64,
            "every_s": every,
            "pid": std::process::id(),
        });
        write(self.api, LOCKS_KEY, Some(doc)).await;
    }

    /// The remote head (and what's new) part of a round: the next delay.
    async fn updates(&mut self) -> f64 {
        let head = lore::remote_head(self.island, 30.0).await;
        if !truthy(&head["ok"]) {
            return self.failed();
        }
        let mut quiet = head["signature"].as_str() == Some(self.st.remote_sig.as_str());
        if self.st.behind {
            let local = lore::local_head(self.island, 30.0).await;
            if !truthy(&local["ok"]) {
                return self.failed();
            }
            quiet = quiet && local["signature"].as_str() == Some(self.st.local_sig.as_str());
            if quiet {
                self.recheck_both(&local).await;
            }
        }
        if !quiet {
            let state = lore::repo_state(self.island, 60.0).await;
            if !truthy(&state["ok"]) {
                return self.failed();
            }
            self.apply(state).await;
        }
        self.st.fails = 0;
        self.interval()
    }
}

impl Watch {
    /// One round on `island` (what UEFN has open, "" for none): the seconds until the
    /// next one, and what to tell the person.
    pub async fn tick(&self, api: &Api, island: &str, away: bool) -> (f64, Vec<Alert>) {
        let mut st = self.st.lock().await;
        st.misses = if island.is_empty() { st.misses + 1 } else { 0 };
        if island != st.island {
            *st = St { island: island.into(), misses: st.misses, ..St::default() };
            // A restart doesn't tell the same news twice.
            if let Some(doc) = read(api, KEY).await.filter(|doc| !island.is_empty() && doc["island"].as_str() == Some(island)) {
                st.told = word(&doc["told"]);
                st.both = doc["both_changed"].as_array().into_iter().flatten().map(pystr).collect();
            }
            write(api, LOCKS_KEY, None).await;
            if island.is_empty() {
                write(api, KEY, None).await;
            }
        }
        if island.is_empty() {
            return (NO_ISLAND_S, Vec::new());
        }
        if !lore::is_repo(island).await {
            return (NOT_LORE_S, Vec::new());
        }
        let mut round = Round { api, st: &mut st, island, away, alerts: Vec::new() };
        let delay = round.updates().await;
        if round.st.fails == 0 {
            round.refresh_locks(delay).await; // lore answered this round: the locks too
        }
        (delay, round.alerts)
    }

    async fn done(&self) -> bool {
        self.stop.load(Ordering::SeqCst) || self.st.lock().await.misses >= STOP_AFTER_MISSES
    }

    /// The watch's rounds, until [`Watch::stop`] or two rounds in a row with no island
    /// (so a closed UEFN costs nothing). A second call while it runs does nothing.
    pub async fn run(&self, api: Arc<Api>, host: impl Host) {
        if self.running.swap(true, Ordering::SeqCst) {
            return;
        }
        self.stop.store(false, Ordering::SeqCst);
        self.st.lock().await.misses = 0;
        while !self.done().await {
            let island = host.open_island().await;
            let (delay, alerts) = self.tick(&api, &island, host.away()).await;
            for [title, status, detail] in &alerts {
                host.alert(title, status, detail);
            }
            if self.done().await {
                break;
            }
            tokio::select! {
                _ = host.pause(delay) => {}
                _ = self.wake.notified() => {}
            }
        }
        self.running.store(false, Ordering::SeqCst);
    }

    pub fn running(&self) -> bool {
        self.running.load(Ordering::SeqCst)
    }

    /// Check again now (after a pull or push made from this process).
    pub fn wake(&self) {
        self.wake.notify_one();
    }

    pub fn stop(&self) {
        self.stop.store(true, Ordering::SeqCst);
        self.wake.notify_one();
    }
}

/// What `ducky_get_status` shows: the open island's news, or null when nothing watches
/// it (the app is closed, no Lore island open).
pub async fn for_agents(api: &Api) -> Value {
    let Some(doc) = read(api, KEY).await.filter(|doc| truthy(&doc["island"])) else { return Value::Null };
    let pid = doc["pid"].as_i64().unwrap_or(0);
    if pid != 0 && pid != i64::from(std::process::id()) {
        // ponytail: the whole process table for one pid; a single-process check when this shows in a trace.
        let gone = tokio::task::spawn_blocking(move || !crate::proc::table().contains_key(&pid)).await.unwrap_or(false);
        if gone {
            return Value::Null; // the app that wrote it is gone
        }
    }
    let keys = ["island", "name", "behind", "new_changes", "authors", "latest", "both_changed", "local_revision", "remote_revision", "checked_at"];
    let mut out: Value = keys.iter().map(|key| (key.to_string(), doc[*key].clone())).collect::<serde_json::Map<_, _>>().into();
    if truthy(&doc["behind"]) {
        let mut hint = String::from("Another PC pushed changes to this island. Tell the person; pull only when they agree (revision_pull never pulls over unsaved work).");
        if truthy(&doc["both_changed"]) {
            hint += " Files in both_changed were changed on both PCs: ask the person which version to keep (this PC's, the other PC's, or merge by hand) before pulling.";
        }
        out["hint"] = hint.into();
    }
    out
}

/// The locks document when it is recent (and for `island` when given).
async fn fresh_locks(api: &Api, island: &str) -> Option<Value> {
    let doc = read(api, LOCKS_KEY).await.filter(|doc| truthy(&doc["island"]))?;
    if !island.is_empty() && norm_path(&pystr(&doc["island"])) != norm_path(island) {
        return None;
    }
    let every = doc["every_s"].as_f64().filter(|every| *every != 0.0).unwrap_or(ACTIVE_S);
    let age = now_s() - doc["checked_at"].as_f64().unwrap_or(0.0);
    (-LOCKS_SLACK_S..=LOCKS_STALE_ROUNDS * every + LOCKS_SLACK_S).contains(&age).then_some(doc)
}

fn of_others(doc: &Value) -> impl Iterator<Item = &Value> {
    doc["locks"].as_array().into_iter().flatten().filter(|lock| !truthy(&lock["mine"]))
}

/// `{norm_path(file): owner}`: the files of `island` locked by another PC's account, as
/// of the latest round (any process). Empty when the document is stale (the watch
/// stopped, lore failed for a few rounds), for another island, or nothing is locked.
pub async fn others_locks(api: &Api, island: &str) -> Value {
    let doc = if island.is_empty() { None } else { fresh_locks(api, island).await };
    let locked = |lock: &Value| truthy(&lock["path"]).then(|| (norm_path(&pystr(&lock["path"])), Value::from(word(&lock["owner"]))));
    doc.iter().flat_map(of_others).filter_map(locked).collect::<serde_json::Map<_, _>>().into()
}

/// What `ducky_get_status` shows: the open island's files locked by another PC, or null
/// when there are none or the list is stale.
pub async fn locks_for_agents(api: &Api, limit: usize) -> Value {
    let Some(doc) = fresh_locks(api, "").await else { return Value::Null };
    let others: Vec<&Value> = of_others(&doc).collect();
    if others.is_empty() {
        return Value::Null;
    }
    json!({
        "island": doc["island"],
        "count": others.len(),
        "locked_by_others": others.iter().take(limit).map(|lock| json!({"file": lock["rel"], "by": lock["owner"]})).collect::<Vec<_>>(),
        "hint": "Another PC is editing these files (Unreal Revision Control locks): don't modify them until it checks in; tell the person.",
    })
}

/// A round on the island a test names, and what agents read, for the tests that compare
/// them with Python's.
pub async fn probe(api: &Api, what: &str, body: &Value) -> Value {
    let island = body["island"].as_str().unwrap_or_default();
    match what {
        "tick" => {
            let (delay, alerts) = WATCH.tick(api, island, truthy(&body["away"])).await;
            json!({"delay": delay, "alerts": alerts})
        }
        "reset" => {
            *WATCH.st.lock().await = St::default();
            Value::Null
        }
        "for_agents" => for_agents(api).await,
        "others_locks" => others_locks(api, island).await,
        "locks_for_agents" => locks_for_agents(api, 20).await,
        "norm_path" => norm_path(island).into(),
        _ => Value::Null,
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Mutex as Lock;

    use super::*;
    use crate::store::http::Repos;

    #[test]
    fn news_and_files_changed_on_both_pcs_are_one_line_each() {
        let news = [json!({"creator": "A", "message": "m2"}), json!({"creator": "B", "message": "m1"}), json!({"creator": "A", "message": ""})];
        assert_eq!(news_text("isle", &news), "3 new changes on isle from A, B: “m2”");
        assert_eq!(news_text("isle", &[json!({"creator": "", "message": ""})]), "1 new change on isle");
        assert_eq!(both_text(&[]), "");
        let rels: Vec<String> = ["a", "b", "c", "d", "e"].map(String::from).into();
        assert_eq!(both_text(&rels), "5 files changed on both PCs: a, b, c (+2 more) — pull with care");
    }

    #[test]
    fn a_path_has_one_spelling() {
        let messy = std::env::temp_dir().join("Isle").join("Content").join("..").join("Content").join("A.uasset");
        let path = norm_path(&text(&messy));
        assert!(!path.contains('\\') && !path.contains("..") && path == norm_path(&path));
        assert!(path.ends_with(if cfg!(windows) { "/content/a.uasset" } else { "/Content/A.uasset" }));
    }

    /// No island is open, and every wait is over at once.
    #[derive(Default)]
    struct Closed(Lock<Vec<f64>>);

    impl Host for &Closed {
        async fn open_island(&self) -> String {
            String::new()
        }
        fn away(&self) -> bool {
            false
        }
        fn alert(&self, _: &str, _: &str, _: &str) {}
        async fn pause(&self, seconds: f64) {
            self.0.lock().unwrap().push(seconds);
        }
    }

    #[tokio::test]
    async fn the_watch_stops_after_two_rounds_without_an_island() {
        let api = Api::new(Arc::new(Repos::new(std::env::temp_dir())), crate::api::methods());
        let (watch, host) = (Watch::default(), Closed::default());
        watch.run(api, &host).await;
        assert_eq!(*host.0.lock().unwrap(), [NO_ISLAND_S]);
        assert!(!watch.running());
    }
}
