//! An island's sync state, read through UEFN's `lore.exe` (`urc/lore_cli.py`): status,
//! history, revision diff, locks. It never syncs, pushes, commits or stages. Lore
//! prints text only, and paths relative to the folder it runs in, so every call runs
//! in the island's root.
//!
//! The state is measured against the remote `main` branch, not `lore status`'s "Remote
//! revision": after an editor save UEFN moves the island to a personal `main-<id>`
//! branch and pushes its autosave there, so status reads "in sync" while `main` still
//! lacks the change.
//! ponytail: on Linux `lore.exe` runs inside UEFN's Wine or Proton prefix, which this
//! backend does not look up yet: every call answers with that error there.

use std::collections::{HashMap, HashSet};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::{LazyLock, Mutex, PoisonError};
use std::time::{Duration, Instant};

use regex::Regex;
use serde_json::{json, Map, Value};

use super::urc::{self, Stopped};
use crate::api::project_paths::{posix, text};
use crate::store::pyjson::{pystr, truthy};

/// The shared branch every PC checks in to.
pub const REMOTE_BRANCH: &str = "main";
/// Revisions walked (first parent) to find where the local and remote histories meet.
const HISTORY_DEPTH: &str = "200";
/// The signed-in account rarely changes: one `auth info` per 10 minutes.
const ME_TTL: Duration = Duration::from_secs(600);
/// Per-user editor collections, never shared between PCs.
const EXCLUDED_PREFIX: &str = "content/developers/";
/// Lore's copies beside a text file it merged with conflicts: never pushed.
const SIDE_FILES: [&str; 3] = ["~base", "~mine", "~theirs"];
/// Text files a merge can leave conflict markers in, read up to `MARKER_MAX_BYTES` each.
const TEXT_EXTS: [&str; 7] = ["verse", "txt", "json", "ini", "uefnproject", "uplugin", "csv"];
const MARKER_MAX_BYTES: u64 = 512 * 1024;
const NO_REPO: &str = "not a Lore repository (no .lore folder)";
const NO_WINE: &str = "lore.exe runs inside UEFN's Wine or Proton prefix, which is not looked up here yet";

static BRANCH: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^On branch (\S+) revision (\d+) -> ([0-9a-f]+)").unwrap());
static FIELD: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^(Revision|Signature|Merge|Creator)\s*:\s*(.+)$").unwrap());
/// `lore lock query`: "<rel> by <name> on branch <id>"; `lore lock status`: "... on <date>".
static LOCK: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^(.+) by (.+?) on (?:branch \S+|\w{3}, .+)$").unwrap());
static AUTH: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^(ID|Name):\s*(.*)$").unwrap());
/// An account as `lore auth info` names it: `(id, name)`.
type Account = (String, String);
/// Who lore is signed in as, per island, and when it was asked.
static ME: LazyLock<Mutex<HashMap<String, (Instant, Account)>>> = LazyLock::new(Mutex::default);

/// A changed path: lore's code (`A`, `M`, `D`...) and the path under the island with `/`.
pub type Entry = (String, String);

fn norm(path: &str) -> String {
    path.trim().replace('\\', "/")
}

/// `A Content/Foo.uasset` is `("A", "Content/Foo.uasset")`. A folder keeps its `/`; a
/// move or copy is its new place (what the remote lacks).
pub fn parse_entry(line: &str) -> Option<Entry> {
    let mut rest = line.trim_end().chars();
    let code = rest.next().filter(char::is_ascii_uppercase)?;
    let path = rest.as_str().strip_prefix(' ').filter(|path| !path.is_empty())?;
    Some((code.to_string(), norm(path.split_once(" -> ").map_or(path, |(_, new)| new))))
}

/// What `lore status` says: the repository, the local branch and head, and the working
/// tree's changes.
#[derive(Default, Clone, Debug, PartialEq)]
pub struct Status {
    pub repository: String,
    pub local_branch: String,
    pub local_revision: Option<i64>,
    pub local_signature: String,
    /// Every change that is not under "Untracked files:".
    pub staged: Vec<Entry>,
    pub untracked: Vec<Entry>,
}

/// Its "Remote revision" and "Local branch is ..." lines follow the current (maybe
/// personal) branch, so they are not read.
pub fn parse_status(text: &str) -> Status {
    let mut out = Status::default();
    let mut untracked = None;
    for raw in text.lines() {
        let line = raw.trim();
        if let Some(repository) = line.strip_prefix("Repository ") {
            out.repository = repository.trim().to_string();
        } else if let Some(found) = BRANCH.captures(line) {
            out.local_branch = found[1].to_string();
            out.local_revision = found[2].parse().ok();
            out.local_signature = found[3].to_string();
        } else if line.ends_with(':') && parse_entry(line).is_none() {
            untracked = Some(line.to_lowercase().starts_with("untracked"));
        } else if let (Some(untracked), Some(entry)) = (untracked, parse_entry(raw)) {
            if untracked { &mut out.untracked } else { &mut out.staged }.push(entry);
        }
    }
    out
}

/// One revision of `lore history`. `merges` holds the other parent(s) a check-in merged
/// in (UEFN merges the personal autosave branch into `main`), `creator` the Epic display
/// name as lore prints it, `message` the first line of the check-in's description.
#[derive(Default, Clone, Debug, PartialEq)]
pub struct Revision {
    pub revision: Option<i64>,
    pub signature: String,
    pub merges: Vec<String>,
    pub creator: String,
    pub message: String,
}

/// `lore history` (full format), newest first, following first parents.
pub fn parse_history(text: &str) -> Vec<Revision> {
    let mut out: Vec<Revision> = Vec::new();
    for raw in text.lines() {
        let Some(found) = FIELD.captures(raw.trim()) else {
            if let Some(last) = out.last_mut().filter(|last| last.message.is_empty() && raw.starts_with(char::is_whitespace)) {
                last.message = raw.trim().to_string();
            }
            continue;
        };
        let (key, value) = (&found[1], found[2].trim());
        if key == "Revision" {
            let revision = Some(value).filter(|value| value.chars().all(|c| c.is_ascii_digit())).and_then(|value| value.parse().ok());
            out.push(Revision { revision, ..Revision::default() });
        } else if let Some(last) = out.last_mut() {
            match key {
                "Signature" => last.signature = value.to_string(),
                "Creator" => last.creator = value.to_string(),
                _ => last.merges.extend(value.split_whitespace().map(String::from)),
            }
        }
    }
    out
}

/// An Epic display name without its invisible padding (Hangul fillers and kin).
pub fn display_name(name: &str) -> String {
    let filler = |c: char| matches!(c, '\u{115f}' | '\u{1160}' | '\u{3164}' | '\u{ffa0}' | '\u{200b}'..='\u{200d}' | '\u{2800}');
    name.replace(filler, " ").split_whitespace().collect::<Vec<_>>().join(" ")
}

/// `lore lock query` / `lore lock status`: `(rel, owner)` per lock, the owner as lore
/// prints it. Header lines are skipped.
pub fn parse_locks(text: &str) -> Vec<(String, String)> {
    text.lines().filter_map(|raw| LOCK.captures(raw.trim())).map(|found| (norm(&found[1]), found[2].trim().to_string())).collect()
}

/// `lore auth info`: `(id, name)`, the name with its padding; empty when missing.
pub fn parse_auth_info(text: &str) -> Account {
    let mut out = (String::new(), String::new());
    for found in text.lines().filter_map(|raw| AUTH.captures(raw.trim())) {
        *(if &found[1] == "ID" { &mut out.0 } else { &mut out.1 }) = found[2].trim().to_string();
    }
    out
}

fn reachable(history: &[Revision]) -> HashSet<&str> {
    history.iter().flat_map(|rev| std::iter::once(&rev.signature).chain(&rev.merges)).map(String::as_str).filter(|sig| !sig.is_empty()).collect()
}

/// The local head against the remote `main` head.
#[derive(Debug, PartialEq)]
pub struct Compared {
    /// The newest local first-parent revision the remote reaches; empty when none in range.
    pub merge_base: String,
    /// The remote head is not in the local ancestry.
    pub behind: bool,
    /// The local head is not reachable from the remote.
    pub ahead: bool,
    pub in_sync: bool,
}

pub fn compare_histories(local: &[Revision], remote: &[Revision]) -> Compared {
    let head = |history: &[Revision]| history.first().map(|rev| rev.signature.clone()).unwrap_or_default();
    let (local_head, remote_head, on_remote) = (head(local), head(remote), reachable(remote));
    let merge_base = local.iter().map(|rev| &rev.signature).find(|sig| on_remote.contains(sig.as_str())).cloned().unwrap_or_default();
    let behind = !remote_head.is_empty() && !reachable(local).contains(remote_head.as_str());
    let ahead = !local_head.is_empty() && merge_base != local_head;
    Compared { merge_base, behind, ahead, in_sync: !behind && !ahead }
}

/// Remote main's first-parent revisions the local head lacks, newest first. `own`: the
/// check-in merges a revision the local history already has (this PC's own autosave
/// checked in to main), so it brings nothing made elsewhere.
pub fn incoming(local: &[Revision], remote: &[Revision]) -> Vec<Value> {
    let have = reachable(local);
    let row = |rev: &Revision| {
        let own = rev.merges.iter().any(|sig| have.contains(sig.as_str()));
        json!({"revision": rev.revision, "signature": rev.signature, "creator": display_name(&rev.creator), "message": rev.message, "own": own})
    };
    remote.iter().take_while(|rev| !have.contains(rev.signature.as_str())).map(row).collect()
}

/// `lore revision diff`'s lines. Folders are listed without a `/`.
pub fn parse_revision_diff(text: &str) -> Vec<Entry> {
    text.lines().filter_map(parse_entry).collect()
}

fn excluded(rel: &str) -> bool {
    let rel = rel.trim_end_matches('/').to_lowercase();
    format!("{rel}/").starts_with(EXCLUDED_PREFIX) || SIDE_FILES.iter().any(|side| rel.ends_with(side))
}

/// Every file under `dir`, in the order Python sorts paths (part by part; Windows
/// ignores case).
fn files_under(dir: &Path) -> Vec<PathBuf> {
    #[allow(clippy::disallowed_methods)] // reads folders: runs inside spawn_blocking
    fn walk(dir: &Path, out: &mut Vec<PathBuf>) {
        for entry in std::fs::read_dir(dir).into_iter().flatten().flatten() {
            let path = entry.path();
            if path.is_dir() {
                walk(&path, out);
            } else if path.is_file() {
                out.push(path);
            }
        }
    }
    let mut out = Vec::new();
    walk(dir, &mut out);
    let parts = |path: &PathBuf| path.components().map(|part| if cfg!(windows) { text(part.as_ref()).to_lowercase() } else { text(part.as_ref()) }).collect::<Vec<_>>();
    out.sort_by_cached_key(parts);
    out
}

/// The files that exist on disk, as absolute paths with `/`, each once, in order.
/// Deletes and anything missing are dropped: there is no file to send. An untracked
/// `dir/` is the files under it (lore lists only the folder); a bare folder line from
/// `revision diff` is skipped (it lists the folder's files too).
pub fn to_pending_files(root: &str, entries: &[Entry]) -> Vec<String> {
    let root = Path::new(root);
    let (mut seen, mut out) = (HashSet::new(), Vec::new());
    let mut add = |path: &Path| {
        let rel = path.strip_prefix(root).map(posix).unwrap_or_default();
        if !excluded(&rel) && seen.insert(posix(path)) {
            out.push(posix(path));
        }
    };
    for (code, rel) in entries {
        if code == "D" || rel.is_empty() || rel.starts_with("../") || excluded(rel) {
            continue;
        }
        let path = root.join(rel.trim_end_matches('/'));
        if rel.ends_with('/') {
            files_under(&path).iter().for_each(|file| add(file));
        } else if path.is_file() {
            add(&path);
        }
    }
    out
}

/// The entries that name files: `revision diff` lists a folder (no `/` at its end)
/// before its files, so a path another entry sits under, or a folder on disk, is
/// dropped. Untracked `dir/` lines stay (they stand for every file under them).
fn files_only(root: &str, entries: Vec<Entry>) -> Vec<Entry> {
    let mut parents = HashSet::new();
    for (_, rel) in &entries {
        let rel = rel.to_lowercase();
        let rel = rel.trim_end_matches('/');
        parents.extend(rel.match_indices('/').map(|(at, _)| rel[..at].to_string()));
    }
    let named = |(_, rel): &Entry| {
        let kept = !rel.is_empty() && !rel.starts_with("../") && !excluded(rel);
        kept && (rel.ends_with('/') || !(parents.contains(&rel.to_lowercase()) || Path::new(root).join(rel).is_dir()))
    };
    entries.into_iter().filter(named).collect()
}

async fn blocking<T: Send + 'static>(work: impl FnOnce() -> T + Send + 'static) -> Result<T, String> {
    tokio::task::spawn_blocking(work).await.map_err(|stopped| stopped.to_string())
}

pub(super) async fn is_repo(root: &str) -> bool {
    let folder = Path::new(root).join(".lore");
    blocking(move || folder.is_dir()).await.unwrap_or(false)
}

/// `lore.exe -P --repository <root> <args>`: what it printed, or why not.
async fn run_lore(root: &str, args: &[&str], limit: f64) -> Result<String, String> {
    if cfg!(target_os = "linux") {
        return Err(NO_WINE.into());
    }
    let found = urc::lore_program().await;
    let Some(program) = found.get("path").and_then(Value::as_str).filter(|_| found.get("available").is_some_and(truthy)) else {
        return Err(found.get("error").and_then(Value::as_str).filter(|error| !error.is_empty()).unwrap_or("lore.exe not found").to_string());
    };
    let full: Vec<String> = ["-P", "--repository", root].iter().chain(args).map(|arg| arg.to_string()).collect();
    match urc::run(program, &full, root, Duration::from_secs_f64(limit)).await {
        Err(Stopped::Late) => Err(format!("lore {} timed out after {limit}s", args[0])),
        Err(Stopped::Os(refused)) => Err(format!("lore {} failed: {refused}", args[0])),
        Ok(ran) if ran.code != 0 || ran.out.trim_start().starts_with("[Error]") => {
            let said = if ran.err.is_empty() { &ran.out } else { &ran.err };
            Err(said.trim().lines().next().map_or_else(|| format!("lore {} exited {}", args[0], ran.code), String::from))
        }
        Ok(ran) => Ok(ran.out),
    }
}

/// `{"ok": false, ...extra, "error": error}`.
fn failed(error: impl Into<String>, extra: Value) -> Value {
    let mut out: Map<String, Value> = Map::new();
    out.insert("ok".into(), false.into());
    out.extend(extra.as_object().cloned().unwrap_or_default());
    out.insert("error".into(), error.into().into());
    Value::Object(out)
}

fn pairs<'a>(entries: impl IntoIterator<Item = &'a Entry>) -> Value {
    entries.into_iter().map(|(code, rel)| json!([code, rel])).collect()
}

fn rels(entries: &[Entry]) -> Value {
    entries.iter().map(|(_, rel)| rel.as_str()).collect()
}

/// An island's status with both histories read.
struct State {
    status: Status,
    compared: Compared,
    remote_revision: Option<i64>,
    remote_signature: String,
    incoming: Vec<Value>,
}

impl State {
    /// The working tree's changes, staged then untracked, deletes included.
    fn changes(&self) -> Vec<Entry> {
        self.status.staged.iter().chain(&self.status.untracked).cloned().collect()
    }
}

/// `_state`: status, the local history and remote main's.
async fn state(root: &str, limit: f64) -> Result<State, String> {
    if !is_repo(root).await {
        return Err(NO_REPO.into());
    }
    let status = parse_status(&run_lore(root, &["status"], limit).await?);
    if status.local_signature.is_empty() {
        return Err("lore status shows no current revision".into());
    }
    let local = run_lore(root, &["history", HISTORY_DEPTH, "--revision", &status.local_signature], limit).await?;
    let remote = parse_history(&run_lore(root, &["--remote", "history", HISTORY_DEPTH, "--branch", REMOTE_BRANCH], limit).await?);
    let Some(head) = remote.first() else {
        return Err(format!("remote branch {REMOTE_BRANCH} has no revisions"));
    };
    let local = parse_history(&local);
    let compared = compare_histories(&local, &remote);
    if compared.merge_base.is_empty() {
        return Err(format!("no revision shared with remote {REMOTE_BRANCH} in the last {HISTORY_DEPTH}"));
    }
    Ok(State { status, compared, remote_revision: head.revision, remote_signature: head.signature.clone(), incoming: incoming(&local, &remote) })
}

async fn head(root: &str, args: &[&str], limit: f64) -> Value {
    if !is_repo(root).await {
        return failed(NO_REPO, Value::Null);
    }
    let out = match run_lore(root, args, limit).await {
        Ok(out) => out,
        Err(error) => return failed(error, Value::Null),
    };
    let mut changes = None;
    let (revision, signature) = if args == ["status"] {
        let status = parse_status(&out);
        changes = Some(pairs(status.staged.iter().chain(&status.untracked)));
        (status.local_revision, status.local_signature)
    } else {
        parse_history(&out).into_iter().next().map(|rev| (rev.revision, rev.signature)).unwrap_or_default()
    };
    if signature.is_empty() {
        return failed("lore shows no revision", Value::Null);
    }
    let mut answer = json!({"ok": true, "revision": revision, "signature": signature});
    if let Some(changes) = changes {
        answer["changes"] = changes;
    }
    answer["error"] = "".into();
    answer
}

/// `{ok, revision, signature, error}`: the head of remote `main`, one lore call.
pub async fn remote_head(root: &str, limit: f64) -> Value {
    head(root, &["--remote", "history", "1", "--branch", REMOTE_BRANCH], limit).await
}

/// `{ok, revision, signature, changes, error}`: the island's current revision and its
/// working tree's `[code, rel]` changes, one `lore status`.
pub async fn local_head(root: &str, limit: f64) -> Value {
    head(root, &["status"], limit).await
}

/// `{ok, available, repository, branch, local_revision, remote_revision, behind, ahead,
/// in_sync, staged, untracked, incoming, changes, ..., error}`. The remote and
/// behind/ahead/in_sync are against remote `main`. Not ok when lore is missing, the
/// folder has no `.lore`, or lore fails; `limit` is each of the three calls' own.
pub async fn repo_state(root: &str, limit: f64) -> Value {
    if !urc::lore_program().await.get("available").is_some_and(truthy) {
        return failed("lore.exe not found — install Fortnite/UEFN or set LORE_CLI_LOCATION", json!({"available": false}));
    }
    let state = match state(root, limit).await {
        Ok(state) => state,
        Err(error) => return failed(error, json!({"available": true})),
    };
    let (status, compared) = (&state.status, &state.compared);
    json!({
        "ok": true,
        "available": true,
        "repository": status.repository,
        "local_branch": status.local_branch,
        "local_revision": status.local_revision,
        "local_signature": status.local_signature,
        "staged": rels(&status.staged),
        "untracked": rels(&status.untracked),
        "merge_base": compared.merge_base,
        "behind": compared.behind,
        "ahead": compared.ahead,
        "in_sync": compared.in_sync,
        "changes": pairs(&state.changes()),
        "remote_branch": REMOTE_BRANCH,
        "remote_revision": state.remote_revision,
        "remote_signature": state.remote_signature,
        "incoming": state.incoming,
        "branch": status.local_branch,
        "error": "",
    })
}

/// The files this PC's commits changed since the merge base (autosaves main lacks):
/// one `revision diff` when the local head is ahead, none otherwise.
async fn committed(root: &str, ahead: bool, base: &str, head: &str, limit: f64) -> Result<Vec<Entry>, String> {
    if !ahead {
        return Ok(Vec::new());
    }
    Ok(parse_revision_diff(&run_lore(root, &["revision", "diff", base, "--target", head], limit).await?))
}

/// `{ok, paths, error}`: every file not yet on remote `main`, as absolute paths with
/// `/`: the working tree's changes, and the files changed between the merge base and
/// the local head. Only files that exist on disk; never Content/Developers or the
/// copies lore leaves beside a conflicted merge.
pub async fn pending_paths(root: &str, limit: f64) -> Value {
    let none = || json!({"paths": []});
    let state = match state(root, limit).await {
        Ok(state) => state,
        Err(error) => return failed(error, none()),
    };
    let mut entries = state.changes();
    match committed(root, state.compared.ahead, &state.compared.merge_base, &state.status.local_signature, limit).await {
        Ok(more) => entries.extend(more),
        Err(error) => return failed(error, none()),
    }
    let at = root.to_string();
    match blocking(move || to_pending_files(&at, &entries)).await {
        Ok(paths) => json!({"ok": true, "paths": paths, "error": ""}),
        Err(error) => failed(format!("listing pending files failed: {error}"), none()),
    }
}

/// The text files among `paths` that still hold merge conflict markers (a line that
/// starts `<<<<<<< ` and one that starts `>>>>>>> `, as lore writes them). A file that
/// can't be read is skipped.
#[allow(clippy::disallowed_methods)] // reads files: runs inside spawn_blocking
pub fn conflict_marked(paths: &[String]) -> Vec<String> {
    let marked = |path: &&String| {
        let ext = Path::new(path.as_str()).extension().map(|ext| ext.to_string_lossy().to_lowercase()).unwrap_or_default();
        let mut bytes = Vec::new();
        if !TEXT_EXTS.contains(&ext.as_str()) || std::fs::File::open(path.as_str()).and_then(|file| file.take(MARKER_MAX_BYTES).read_to_end(&mut bytes)).is_err() {
            return false;
        }
        let text = String::from_utf8_lossy(&bytes);
        let has = |mark: &str| text.starts_with(mark) || text.contains(&format!("\n{mark}"));
        has("<<<<<<< ") && has(">>>>>>> ")
    };
    paths.iter().filter(marked).cloned().collect()
}

/// `(id, name)`: the Epic account lore is signed in with on this PC (`lore auth info`),
/// asked once per `ME_TTL`. Empty when lore can't say.
pub async fn whoami(root: &str, limit: f64) -> Account {
    let cached = ME.lock().unwrap_or_else(PoisonError::into_inner).get(root).filter(|(asked, _)| asked.elapsed() < ME_TTL).map(|(_, me)| me.clone());
    if let Some(me) = cached {
        return me;
    }
    let asked = Instant::now();
    let me = run_lore(root, &["auth", "info"], limit).await.map(|out| parse_auth_info(&out)).unwrap_or_default();
    if !me.1.is_empty() {
        ME.lock().unwrap_or_else(PoisonError::into_inner).insert(root.to_string(), (asked, me.clone()));
    }
    me
}

fn same_owner(owner: &str, me: &str) -> bool {
    !me.is_empty() && (owner.trim() == me.trim() || display_name(owner) == display_name(me))
}

/// `{ok, locks: [{path, rel, owner, owner_raw, mine}], me, error}`: the files locked on
/// remote `main` (UEFN locks a file as soon as an actor is modified, until check-in or
/// revert). `mine`: the lock's owner is the account lore is signed in with here,
/// compared by display name (lore prints no owner id). When that account is unknown the
/// answer is not ok (the locks are still listed, none `mine`), so nobody mistakes this
/// PC's own locks for another's.
pub async fn locks(root: &str, limit: f64) -> Value {
    let none = |locks: Vec<Value>| json!({"locks": locks, "me": ""});
    if !is_repo(root).await {
        return failed(NO_REPO, none(Vec::new()));
    }
    let out = match run_lore(root, &["lock", "query", "--branch", REMOTE_BRANCH], limit).await {
        Ok(out) => out,
        Err(error) => return failed(error, none(Vec::new())),
    };
    let me = whoami(root, limit.min(30.0)).await.1;
    let row = |(rel, owner): (String, String)| {
        let path = posix(&Path::new(root).join(&rel));
        let mine = same_owner(&owner, &me);
        json!({"path": path, "rel": rel, "owner": display_name(&owner), "owner_raw": owner, "mine": mine})
    };
    let rows: Vec<Value> = parse_locks(&out).into_iter().map(row).collect();
    let me = display_name(&me);
    if me.is_empty() {
        return failed("lore auth info names no signed-in account", none(rows));
    }
    json!({"ok": true, "locks": rows, "me": me, "error": ""})
}

/// The state a caller already read, else a new [`repo_state`].
async fn known(root: &str, state: Option<Value>, limit: f64) -> Value {
    match state.filter(truthy) {
        Some(state) => state,
        None => repo_state(root, limit).await,
    }
}

/// `{ok, files: [{rel, path, code, deleted}], error}`: the files remote `main` changed
/// since the merge base (what a pull brings), deletions included. On top of `state` (a
/// [`repo_state`] answer, read when not given) one `revision diff`, none when the
/// island is not behind.
pub async fn incoming_paths(root: &str, state: Option<Value>, limit: f64) -> Value {
    let none = || json!({"files": []});
    let state = known(root, state, limit).await;
    if !truthy(&state["ok"]) {
        return failed(state.get("error").filter(|error| truthy(error)).map_or("could not read the repository".into(), pystr), none());
    }
    if !truthy(&state["behind"]) {
        return json!({"ok": true, "files": [], "error": ""});
    }
    let (Some(base), Some(target)) = (state["merge_base"].as_str(), state["remote_signature"].as_str()) else {
        return failed("listing incoming files failed: the state names no revisions", none());
    };
    let out = match run_lore(root, &["revision", "diff", base, "--target", target], limit).await {
        Ok(out) => out,
        Err(error) => return failed(error, none()),
    };
    let at = root.to_string();
    let entries = match blocking(move || files_only(&at, parse_revision_diff(&out))).await {
        Ok(entries) => entries,
        Err(error) => return failed(format!("listing incoming files failed: {error}"), none()),
    };
    let file = |(code, rel): &Entry| json!({"rel": rel, "path": posix(&Path::new(root).join(rel)), "code": code, "deleted": code == "D"});
    json!({"ok": true, "files": entries.iter().map(file).collect::<Vec<_>>(), "error": ""})
}

/// `{ok, files: [{rel, path, local, remote}], error}`: the files a pull would bring
/// that this PC changed too and main lacks: the working tree's changes (deletes and
/// untracked included) and this PC's commits since the merge base. `local` is "added",
/// "modified" or "deleted" ("changed" for another code), `remote` the diff's code. A
/// file deleted on both sides is not listed.
pub async fn overlap(root: &str, state: Option<Value>, limit: f64) -> Value {
    let none = || json!({"files": []});
    let state = known(root, state, limit).await;
    let coming = incoming_paths(root, Some(state.clone()), limit).await;
    let theirs = coming["files"].as_array().cloned().unwrap_or_default();
    if !truthy(&coming["ok"]) || theirs.is_empty() {
        return json!({"ok": coming["ok"], "files": [], "error": coming["error"]});
    }
    let word = |key: &str| state[key].as_str().unwrap_or_default();
    let mut entries = match committed(root, truthy(&state["ahead"]), word("merge_base"), word("local_signature"), limit).await {
        Ok(entries) => entries,
        Err(error) => return failed(error, none()),
    };
    // The working tree's lines last: they are the newest state of a file.
    let changes = state["changes"].as_array().into_iter().flatten();
    entries.extend(changes.filter_map(|pair| Some((pair.get(0)?.as_str()?.to_string(), pair.get(1)?.as_str()?.to_string()))));
    let at = root.to_string();
    let entries = match blocking(move || files_only(&at, entries)).await {
        Ok(entries) => entries,
        Err(error) => return failed(format!("comparing changes failed: {error}"), none()),
    };
    let (mut mine, mut folders) = (HashMap::new(), Vec::new());
    for (code, rel) in entries {
        if rel.ends_with('/') {
            folders.push((code, rel.to_lowercase()));
        } else {
            mine.insert(rel.to_lowercase(), code);
        }
    }
    let both = |file: &Value| {
        let low = file["rel"].as_str()?.to_lowercase();
        let code = mine.get(&low).or_else(|| folders.iter().find(|(_, folder)| low.starts_with(folder)).map(|(code, _)| code))?;
        let local = match code.as_str() {
            "D" if file["deleted"] == true => return None,
            "A" => "added",
            "M" => "modified",
            "D" => "deleted",
            _ => "changed",
        };
        Some(json!({"rel": file["rel"], "path": file["path"], "local": local, "remote": file["code"]}))
    };
    json!({"ok": true, "files": theirs.iter().filter_map(both).collect::<Vec<_>>(), "error": ""})
}

/// One of the calls above, for the tests that compare it with Python's.
pub async fn probe(what: &str, body: &Value) -> Value {
    if let Some(call) = what.strip_prefix("host_") {
        let (call, body) = (call.to_string(), body.clone());
        return blocking(move || super::lore_host::probe(&call, &body)).await.unwrap_or_default();
    }
    let root = body["root"].as_str().unwrap_or_default();
    let limit = |usual: f64| body["timeout"].as_f64().unwrap_or(usual);
    let state = body.get("state").cloned();
    match what {
        "detect" => json!({"urc": urc::urc_program().await, "lore": urc::lore_program().await}),
        "remote_head" => remote_head(root, limit(30.0)).await,
        "local_head" => local_head(root, limit(30.0)).await,
        "repo_state" => repo_state(root, limit(60.0)).await,
        "pending_paths" => pending_paths(root, limit(60.0)).await,
        "locks" => locks(root, limit(60.0)).await,
        "incoming_paths" => incoming_paths(root, state, limit(60.0)).await,
        "overlap" => overlap(root, state, limit(60.0)).await,
        "whoami" => {
            let (id, name) = whoami(root, limit(30.0)).await;
            json!({"id": id, "name": name})
        }
        "conflict_marked" => {
            let paths: Vec<String> = body["paths"].as_array().into_iter().flatten().map(pystr).collect();
            json!(blocking(move || conflict_marked(&paths)).await.unwrap_or_default())
        }
        _ => Value::Null,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api::project_paths::tests::Temp;

    const STATUS: &str = "Repository ee8b\nOn branch main revision 13 -> 86c6\nRemote revision 13 -> 86c6\nLocal branch in sync with remote\nChanges staged for commit:\nA Content/Python/init_unreal.py\nM Isle.uefnproject\nR Content/A.uasset -> Content\\B.uasset\nUntracked files:\nA Content/Developers/PC/\nA Content/NewFolder/\n";

    fn history(text: &str) -> Vec<Revision> {
        parse_history(text)
    }

    fn entry(code: &str, rel: &str) -> Entry {
        (code.to_string(), rel.to_string())
    }

    #[test]
    fn a_status_is_its_head_and_its_changes_by_section() {
        let status = parse_status(STATUS);
        assert_eq!((status.repository.as_str(), status.local_branch.as_str(), status.local_revision, status.local_signature.as_str()), ("ee8b", "main", Some(13), "86c6"));
        assert_eq!(status.staged, [entry("A", "Content/Python/init_unreal.py"), entry("M", "Isle.uefnproject"), entry("R", "Content/B.uasset")]);
        assert_eq!(status.untracked, [entry("A", "Content/Developers/PC/"), entry("A", "Content/NewFolder/")]);
        assert_eq!(parse_entry("Untracked files:"), None);
    }

    #[test]
    fn a_history_keeps_merges_names_and_first_message_lines() {
        let told = history("Revision  : 13\nSignature : s13\nMerge     : s11 s12\nBranch    : e726\nCreator   : Ann\u{3164}\u{3164}Lee\n    Moved the cube\n    second line\n\nRevision  : x\nSignature : s9\n");
        assert_eq!((told[0].revision, told[0].signature.as_str(), told[0].message.as_str()), (Some(13), "s13", "Moved the cube"));
        assert_eq!(told[0].merges, ["s11", "s12"]);
        assert_eq!((display_name(&told[0].creator), told[1].revision), ("Ann Lee".to_string(), None));
        assert_eq!(parse_auth_info("ID: 0197\nName: Ann\u{3164}\n"), ("0197".to_string(), "Ann\u{3164}".to_string()));
        let locks = parse_locks("Locks found:\nContent/My Folder/A by B.uasset by Ann\u{3164} on branch abc\nContent\\C.uasset by Lee on Mon, 26 Oct 2026 20:43:53 +0000\n");
        assert_eq!(locks, [("Content/My Folder/A by B.uasset".to_string(), "Ann\u{3164}".to_string()), ("Content/C.uasset".to_string(), "Lee".to_string())]);
    }

    #[test]
    fn histories_compare_against_remote_main() {
        let personal = history("Revision : 12\nSignature : s12\n\nRevision : 11\nSignature : s11\n");
        let main_11 = history("Revision : 11\nSignature : s11\n");
        let main_13 = history("Revision : 13\nSignature : s13\nMerge : s12\n\nRevision : 11\nSignature : s11\n");
        let main_15 = history("Revision : 15\nSignature : s15\nCreator : Lee\n\nRevision : 13\nSignature : s13\nMerge : s12\n");
        // An autosave on the personal branch is ahead of main; checked in, it is behind.
        assert_eq!(compare_histories(&personal, &main_11), Compared { merge_base: "s11".into(), behind: false, ahead: true, in_sync: false });
        assert_eq!(compare_histories(&personal, &main_13), Compared { merge_base: "s12".into(), behind: true, ahead: false, in_sync: false });
        assert!(compare_histories(&main_13, &main_13).in_sync);
        assert_eq!(incoming(&personal, &main_13), [json!({"revision": 13, "signature": "s13", "creator": "", "message": "", "own": true})]);
        let other = incoming(&main_13, &main_15);
        assert_eq!((other.len(), &other[0]["own"], &other[0]["creator"]), (1, &json!(false), &json!("Lee")));
    }

    #[test]
    fn pending_files_are_the_ones_on_disk_without_folders_deletes_and_side_copies() {
        let island = Temp::new("lore-pending");
        for rel in ["Content/NewFolder/b.uasset", "Content/NewFolder/sub/A.uasset", "Content/Developers/PC/x.collection", "Content/a.verse", "Content/a.verse~MINE", "Content/Gone.uasset"] {
            island.file(rel, "x");
        }
        let root = text(&island.0);
        let entries = [entry("A", "Content/NewFolder/"), entry("A", "Content/Developers/PC/"), entry("M", "Content/a.verse"), entry("A", "Content/a.verse~MINE"), entry("D", "Content/Gone.uasset"), entry("M", "Content/missing.verse"), entry("A", "Content/NewFolder"), entry("M", "Content/a.verse")];
        let at = |rel: &str| format!("{}/{rel}", posix(&island.0));
        assert_eq!(to_pending_files(&root, &entries), [at("Content/NewFolder/b.uasset"), at("Content/NewFolder/sub/A.uasset"), at("Content/a.verse")]);
        let diff = parse_revision_diff("A Content/NewFolder\nA Content/NewFolder/b.uasset\nA Content/Other\nA Content/Other/c.uasset\nA Content/Developers/PC\nD Content/Gone.uasset\n");
        assert_eq!(files_only(&root, diff), [entry("A", "Content/NewFolder/b.uasset"), entry("A", "Content/Other/c.uasset"), entry("D", "Content/Gone.uasset")]);
    }

    #[test]
    fn conflict_markers_are_found_at_line_starts_in_text_files() {
        let folder = Temp::new("lore-markers");
        let merged = "using { /Verse.org/Simulation }\n<<<<<<< ours\n# one\n=======\n# other\n>>>>>>> theirs\n";
        let paths: Vec<String> = [("merged.VERSE", merged), ("clean.verse", "# <<<<<<< not at a line start\n>>>>>>> x\n"), ("half.json", "<<<<<<< ours\n{}\n"), ("A.uasset", merged)].iter().map(|(name, text)| posix(&folder.file(name, text))).collect();
        let asked: Vec<String> = paths.iter().cloned().chain([format!("{}/gone.verse", posix(&folder.0))]).collect();
        assert_eq!(conflict_marked(&asked), paths[..1]);
    }
}
