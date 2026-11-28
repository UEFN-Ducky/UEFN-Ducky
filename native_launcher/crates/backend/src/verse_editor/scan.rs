//! The project scan (`verse_editor/lsp/diagnostics_scan.py`): every Verse file that
//! needs a check is opened in a verse-lsp of the scan's own, its diagnostics become the
//! Problems list's rows, and the windows hear each step.
//!
//! One scan runs at a time; a later request waits for it, then checks what is still
//! stale. Only a stop (the Stop button, another island) ends a running scan.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use super::diagnostics::{self, Files};
use super::stdio::{file_uri, Session};
use crate::api::project_paths::verse_workspace;
use crate::api::settings::failed;
use crate::api::Api;
use crate::store::misc;
use crate::store::pyjson::{int, pystr, truthy};

#[derive(Default)]
struct Scans {
    /// The running scan: its number, its island and its verse-lsp.
    active: Option<(u64, String, Arc<Session>)>,
    /// Goes up with every stop: a scan that waited through one does not run.
    stops: u64,
    /// The last id given to a scan; the windows drop events of an older one.
    ids: i64,
    made: u64,
}

static SCANS: Mutex<Scans> = Mutex::new(Scans { active: None, stops: 0, ids: 0, made: 0 });
static TURN: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

fn scans() -> MutexGuard<'static, Scans> {
    SCANS.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
}

/// `allocate_scan_id`.
pub fn next_id() -> i64 {
    let mut scans = scans();
    scans.ids += 1;
    scans.ids
}

/// `is_scan_active`: a scan of this island is running.
pub fn is_active(root: &str) -> bool {
    scans().active.as_ref().is_some_and(|(_, active, _)| active == root)
}

/// `abort_active_scan`, but for the last step: the running scan is no longer the
/// active one and queued ones will not run; the caller stops the session it gets.
pub fn unhook() -> Option<Arc<Session>> {
    let mut scans = scans();
    scans.stops += 1;
    scans.active.take().map(|(_, _, session)| session)
}

fn stopped(number: u64) -> bool {
    scans().active.as_ref().is_none_or(|(active, _, _)| *active != number)
}

/// verse-lsp disagrees with UEFN's compiler here. Nov 2 2026: 26 "Invalid access of
/// internal function" errors on `.Await()` that the build accepted; an agent spent a
/// whole turn rewriting good code.
fn false_positive(diag: &Value) -> bool {
    let low = Some(&diag["message"]).filter(|message| truthy(message)).map(pystr).unwrap_or_default().to_lowercase();
    low.contains("invalid access of internal function") && (low.contains("await") || low.contains("listenable"))
}

/// `_diagnostics_to_items`: the Problems rows of a file. verse-lsp often says one
/// unknown identifier twice on a line (the type and its constructor): the leftmost stays.
/// ponytail: an entry that is no object is skipped; Python fails the scan on one.
pub fn items(diags: &[Value]) -> Vec<Value> {
    let mut best: Vec<((&str, i64, String), i64)> = Vec::new();
    for diag in diags.iter().filter(|diag| diag.is_object() && !false_positive(diag)) {
        let severity = diag["severity"].as_f64();
        if severity == Some(3.0) || severity == Some(4.0) {
            continue;
        }
        let kind = if severity == Some(2.0) { "warning" } else { "error" };
        let start = &diag["range"]["start"];
        let (line, column) = (int(&start["line"]) + 1, int(&start["character"]) + 1);
        let key = (kind, line, Some(&diag["message"]).filter(|message| truthy(message)).map(pystr).unwrap_or_default());
        match best.iter_mut().find(|(known, _)| *known == key) {
            Some((_, kept)) => *kept = column.min(*kept),
            None => best.push((key, column)),
        }
    }
    best.sort_by_key(|((_, line, _), column)| (*line, *column));
    best.into_iter().map(|((kind, line, message), column)| json!({"line": line, "column": column, "message": message, "severity": kind})).collect()
}

fn counts(items: &[Value]) -> (usize, usize) {
    let of = |kind: &str| items.iter().filter(|item| item["severity"] == kind).count();
    (of("error"), of("warning"))
}

/// A Verse file to check.
struct Source {
    /// Its path under the island, as the disk spells it, with `/`.
    rel: String,
    key: String,
    text: String,
    /// Its modified time in seconds.
    modified: f64,
}

/// `Content/**/*.verse` in `sorted(Path)` order: part by part, small letters on Windows.
#[allow(clippy::disallowed_methods)] // reads folders: runs inside spawn_blocking
fn verse_paths(root: &Path) -> Vec<PathBuf> {
    let mut found = Vec::new();
    let mut pending = vec![root.join("Content")];
    while let Some(folder) = pending.pop() {
        for entry in std::fs::read_dir(&folder).into_iter().flatten().flatten() {
            let (path, name) = (entry.path(), entry.file_name().to_string_lossy().into_owned());
            let name = if cfg!(windows) { name.to_lowercase() } else { name };
            if path.is_dir() {
                // ponytail: a linked folder is not entered.
                if !entry.file_type().is_ok_and(|kind| kind.is_symlink()) {
                    pending.push(path);
                }
            } else if name.ends_with(".verse") && path.is_file() {
                found.push(path);
            }
        }
    }
    let parts = |path: &PathBuf| -> Vec<String> {
        let part = |part: std::path::Component| part.as_os_str().to_string_lossy().into_owned();
        path.components().map(part).map(|part| if cfg!(windows) { part.to_lowercase() } else { part }).collect()
    };
    found.sort_by_cached_key(parts);
    found
}

fn rel(root: &Path, path: &Path) -> String {
    path.strip_prefix(root).unwrap_or(path).to_string_lossy().replace('\\', "/")
}

/// `list_verse_scan_paths(full=True)`: the key of every Verse file a full scan checks.
pub fn all_keys(root: &Path) -> Vec<String> {
    verse_paths(root).iter().map(|path| diagnostics::norm_key(&rel(root, path))).collect()
}

/// `_collect_verse_files`, with each file's text as Python reads it (`\n` line ends).
/// ponytail: bytes that are no UTF-8 are replaced; Python fails the scan on them.
#[allow(clippy::disallowed_methods)] // reads files: runs inside spawn_blocking
fn sources(root: &Path) -> Vec<Source> {
    let source = |path: &PathBuf| {
        let rel = rel(root, path);
        let text = std::fs::read(path).map(|bytes| String::from_utf8_lossy(&bytes).replace("\r\n", "\n").replace('\r', "\n")).unwrap_or_default();
        let modified = diagnostics::fingerprint(path).map_or(0.0, |(mtime_ns, _)| mtime_ns as f64 / 1e9);
        Source { key: diagnostics::norm_key(&rel), rel, text, modified }
    };
    verse_paths(root).iter().map(source).collect()
}

async fn blocking<T: Send + 'static>(work: impl FnOnce() -> T + Send + 'static) -> Result<T, String> {
    tokio::task::spawn_blocking(work).await.map_err(|stopped| stopped.to_string())
}

/// What to scan, and for whom.
pub struct Ask {
    pub root: String,
    /// The id the island's rows are kept under.
    pub slug: String,
    /// Every file, not only the stale ones.
    pub full: bool,
    /// The caller already told the windows it started.
    pub announced: bool,
    pub scan_id: i64,
}

// The scan the Problems list asks for (Python's `fast`): bigger batches, shorter waits.
const OPEN_BATCH: usize = 16;
const PULL_LIMIT: Duration = Duration::from_secs(3);
const BATCH_SETTLE: Duration = Duration::from_millis(1200);
const REFRESH_SETTLE: Duration = Duration::from_millis(180);

struct Scan<'a> {
    api: &'a Api,
    ask: &'a Ask,
}

impl Scan<'_> {
    /// One progress event: `fields` under the scan's id, for its island.
    fn say(&self, fields: Value) {
        let mut event = json!({"type": "verse_diagnostics_progress", "scan_id": self.ask.scan_id});
        for (name, value) in fields.as_object().into_iter().flatten() {
            event[name] = value.clone();
        }
        event["project_root"] = json!(self.ask.root);
        self.api.publish(vec![event]);
    }

    /// Keep a checked file's result at once: a scan stopped in the middle of a batch
    /// has still answered for it, and the Problems list shows it.
    /// The rows are read first, never a copy from the scan's start: the editor's own
    /// check of a file may have written since.
    /// ponytail: every row is compared again per file; one row's own write when an
    /// island has thousands of Verse files.
    async fn keep(&self, key: String, entry: Value) {
        let Ok(store) = self.api.repos.store().await else { return };
        let mut files = misc::diag_get(store, self.ask.slug.clone()).await.unwrap_or_default();
        files.insert(key, entry);
        let _ = misc::diag_replace(store, self.ask.slug.clone(), files).await;
    }

    /// Drop the rows of files gone from disk.
    async fn prune(&self) {
        let Ok(store) = self.api.repos.store().await else { return };
        let mut files = misc::diag_get(store, self.ask.slug.clone()).await.unwrap_or_default();
        let root = PathBuf::from(&self.ask.root);
        let pruned = move || {
            diagnostics::prune(&root, &mut files);
            files
        };
        if let Ok(files) = blocking(pruned).await {
            let _ = misc::diag_replace(store, self.ask.slug.clone(), files).await;
        }
    }
}

/// `scan_project_verse_diagnostics`: wait for the scan's turn, then run it. An error is
/// what the windows are told ended it.
pub async fn scan(api: &Api, ask: &Ask) -> Result<(), String> {
    let other = scans().active.as_ref().is_some_and(|(_, root, _)| *root != ask.root);
    if other {
        // Another island was opened: the old one's scan answers nothing anyone needs.
        if let Some(session) = unhook() {
            session.stop();
        }
    }
    let stops = scans().stops;
    let _turn = TURN.lock().await;
    if scans().stops != stops {
        return Ok(()); // stopped while it waited
    }
    let scan = Scan { api, ask };
    let root = PathBuf::from(&ask.root);
    let store = api.repos.store().await.map_err(failed)?;
    let cache = misc::diag_get(store, ask.slug.clone()).await.unwrap_or_default();
    let at = root.clone();
    let read = move || {
        let mut cache = cache;
        diagnostics::prune(&at, &mut cache);
        (sources(&at), diagnostics::stale_keys(&at, &cache), cache)
    };
    let (files, stale, cache) = blocking(read).await?;
    let stale: HashSet<String> = stale.into_iter().collect();
    if !ask.full && stale.is_empty() {
        return Ok(()); // nothing to do: no spinner for it
    }
    let mut keys: Vec<String> = if ask.full { files.iter().map(|file| file.key.clone()).collect() } else { stale.iter().cloned().collect() };
    if !ask.full {
        keys.sort();
    }
    // Newest first: the file an agent just wrote is in the first batch, so a scan
    // stopped early has still answered for it.
    let by_key: HashMap<&str, &Source> = files.iter().map(|file| (file.key.as_str(), file)).collect();
    let modified = |key: &String| by_key.get(key.as_str()).map_or(0.0, |file| file.modified);
    keys.sort_by(|one, other| modified(other).total_cmp(&modified(one)));
    let mut seen = HashSet::new();
    keys.retain(|key| seen.insert(key.clone()));
    let to_scan: Vec<&Source> = keys.iter().filter_map(|key| by_key.get(key.as_str()).copied()).collect();
    if to_scan.is_empty() && !ask.full {
        return Ok(());
    }
    if !ask.announced {
        scan.say(json!({"phase": "started", "total": keys.len(), "paths": keys, "index": 0, "path": ""}));
    }
    if files.is_empty() {
        scan.say(json!({"phase": "done", "total": 0, "index": 0, "path": ""}));
        return Ok(());
    }
    // What is still fresh is in the result, with no progress of its own.
    let mut results = 0;
    if !ask.full {
        results = cache.iter().filter(|(key, entry)| !stale.contains(*key) && entry.is_object()).count();
    }
    let (at, data_dir) = (root.clone(), api.repos.data_dir().to_path_buf());
    let workspace = blocking(move || verse_workspace(&at, &data_dir, true)).await?;
    let session = Arc::new(Session::start(&ask.root, api.repos.data_dir()).await?);
    let number = {
        let mut scans = scans();
        if scans.stops != stops {
            return Ok(());
        }
        scans.made += 1;
        scans.active = Some((scans.made, ask.root.clone(), Arc::clone(&session)));
        scans.made
    };
    let checked = scan.check(&session, number, &workspace_folders(&workspace.folders), &workspace.watch_files, &to_scan, &cache).await;
    session.stop();
    {
        let mut scans = scans();
        if scans.active.as_ref().is_some_and(|(active, _, _)| *active == number) {
            scans.active = None;
        }
    }
    results += checked?;
    scan.prune().await;
    scan.say(json!({"phase": "done", "index": results, "total": results, "path": ""}));
    Ok(())
}

fn workspace_folders(folders: &[crate::api::project_paths::Folder]) -> Vec<Value> {
    folders.iter().map(|folder| json!({"uri": file_uri(&folder.path), "name": folder.name})).collect()
}

impl Scan<'_> {
    /// Open the files in batches and read what verse-lsp says of each. The number of
    /// files that got a result.
    async fn check(&self, session: &Session, number: u64, folders: &[Value], watch_files: &[String], to_scan: &[&Source], cache: &Files) -> Result<usize, String> {
        let capabilities = json!({
            "workspace": {"workspaceFolders": true, "diagnostics": {"workspaceDiagnostics": true}},
            "textDocument": {"publishDiagnostics": {"relatedInformation": true}},
        });
        let root_uri = folders.first().map_or_else(|| json!(session.uri("")), |folder| folder["uri"].clone());
        let hello = json!({"processId": null, "rootUri": root_uri, "capabilities": capabilities, "workspaceFolders": folders});
        session.request("initialize", hello, Duration::from_secs(20)).await?;
        session.notify("initialized", json!({})).await?;
        if !watch_files.is_empty() {
            let changes: Vec<Value> = watch_files.iter().map(|file| json!({"uri": file_uri(file), "type": 1})).collect();
            session.notify("workspace/didChangeWatchedFiles", json!({"changes": changes})).await?;
        }
        let (total, mut index, mut results) = (to_scan.len(), 0, 0);
        for (batch, start) in to_scan.chunks(OPEN_BATCH).zip((0..).step_by(OPEN_BATCH)) {
            if stopped(number) || !session.is_alive() {
                break;
            }
            let mut opened: Vec<(String, &Source)> = Vec::new();
            for file in batch {
                if stopped(number) {
                    break;
                }
                self.say(json!({"phase": "opening", "index": start + opened.len() + 1, "total": total, "path": file.key}));
                let uri = session.uri(&file.rel);
                let document = json!({"uri": uri, "languageId": "verse", "version": 1, "text": file.text});
                session.notify("textDocument/didOpen", json!({"textDocument": document})).await?;
                opened.push((uri, file));
            }
            if opened.is_empty() {
                continue;
            }
            let ready = session.published_count();
            let upto = (start + opened.len()).min(total);
            self.say(json!({"phase": "analyzing", "index": upto, "total": total, "path": "", "message": format!("Analyzing {upto}/{total}…")}));
            settle(session, ready + opened.len()).await;
            for (uri, file) in &opened {
                if stopped(number) {
                    break;
                }
                index += 1;
                self.say(json!({"phase": "checking", "index": index, "total": total, "path": file.key}));
                // A batch of opens can publish a false empty set for its later files:
                // each is changed (to the same text) before it is read, as Epic's
                // VS Code extension does.
                let mut diags = refresh(session, uri, &file.text).await?;
                if diags.is_none() {
                    diags = pull(session, uri).await;
                }
                let Some(diags) = diags else {
                    // The check FAILED: what is known of the file stays, never a false
                    // "0 errors".
                    match cache.get(&file.key).filter(|entry| entry.is_object()) {
                        Some(entry) => {
                            results += 1;
                            self.say(json!({"phase": "file", "index": index, "total": total, "path": file.key, "file": diagnostics::result(&file.key, entry)}));
                        }
                        None => self.say(json!({"phase": "file", "index": index, "total": total, "path": file.key})),
                    }
                    continue;
                };
                let mut found = items(&diags);
                let (mut errors, mut warnings) = counts(&found);
                // verse-lsp now and then reports what UEFN's compiler accepts; a second
                // pass on the same text usually comes back clean.
                if (errors > 0 || warnings > 0) && session.is_alive() {
                    if let Ok(Some(again)) = refresh(session, uri, &file.text).await {
                        if items(&again).is_empty() {
                            (found, errors, warnings) = (Vec::new(), 0, 0);
                        }
                    }
                }
                if errors == 0 {
                    // An empty read can be a false all-clear: a syntax error published
                    // for this same text during the scan stands.
                    let sticky = session.syntax_errors(uri);
                    if !sticky.is_empty() {
                        found = items(&sticky);
                        (errors, warnings) = counts(&found);
                    }
                }
                let result = json!({"path": file.key, "errors": errors, "warnings": warnings, "items": found});
                let path = Path::new(&self.ask.root).join(&file.rel);
                if let Some(print) = blocking(move || diagnostics::fingerprint(&path)).await? {
                    self.keep(file.key.clone(), diagnostics::entry(print, &result)).await;
                }
                results += 1;
                self.say(json!({"phase": "file", "index": index, "total": total, "path": file.key, "file": result}));
            }
            self.prune().await;
        }
        Ok(results)
    }
}

/// `_wait_for_diagnostics`: until `wanted` documents have a publish and that number
/// stood still for 0.35 s, at most the batch's settle time.
async fn settle(session: &Session, wanted: usize) {
    let end = Instant::now() + BATCH_SETTLE;
    let (mut last, mut still) = (usize::MAX, None::<Instant>);
    while Instant::now() < end {
        let count = session.published_count();
        if count >= wanted && count == last {
            if still.get_or_insert_with(Instant::now).elapsed() >= Duration::from_millis(350) {
                return;
            }
        } else {
            still = None;
        }
        last = count;
        tokio::time::sleep(Duration::from_millis(80)).await;
    }
}

/// `_refresh_document_diagnostics`: change the document to the same text and read what
/// verse-lsp then published for it; None when it never published.
async fn refresh(session: &Session, uri: &str, text: &str) -> Result<Option<Vec<Value>>, String> {
    session.notify("textDocument/didChange", json!({"textDocument": {"uri": uri, "version": 2}, "contentChanges": [{"text": text}]})).await?;
    tokio::time::sleep(REFRESH_SETTLE).await;
    Ok(session.published(uri))
}

/// `_pull_file_diagnostics`: ask for a file's diagnostics; None when the check failed.
async fn pull(session: &Session, uri: &str) -> Option<Vec<Value>> {
    let pushed = session.diagnostics_for(uri);
    let Ok(answer) = session.request("textDocument/diagnostic", json!({"textDocument": {"uri": uri}}), PULL_LIMIT).await else {
        return Some(pushed).filter(|pushed| !pushed.is_empty());
    };
    let Some(reports) = answer.as_object().and_then(|answer| answer.get("items")).and_then(Value::as_array) else { return Some(pushed) };
    let mut flat = Vec::new();
    for report in reports.iter().filter(|report| report.is_object()) {
        match report.get("diagnostics").unwrap_or(report) {
            Value::Array(diags) => flat.extend(diags.iter().filter(|diag| diag.is_object()).cloned()),
            diag @ Value::Object(_) => flat.push(diag.clone()),
            _ => {}
        }
    }
    Some(if flat.is_empty() { pushed } else { flat })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_files_rows_are_its_errors_and_warnings_once_each_leftmost_first_by_line() {
        let at = |line: i64, character: i64| json!({"start": {"line": line, "character": character}});
        let diags = [
            json!({"range": at(4, 9), "message": "Unknown identifier `x`"}),
            json!({"range": at(4, 2), "message": "Unknown identifier `x`", "severity": 1}),
            json!({"range": at(0, 0), "message": "unused", "severity": 2}),
            json!({"range": at(0, 0), "message": "hint", "severity": 4}),
            json!({"range": at(1, 0), "message": "Invalid access of internal function (/Verse.org:)Await"}),
            json!({"message": "no place"}),
            json!("junk"),
        ];
        let rows = items(&diags);
        assert_eq!(rows, [
            json!({"line": 1, "column": 1, "message": "unused", "severity": "warning"}),
            json!({"line": 1, "column": 1, "message": "no place", "severity": "error"}),
            json!({"line": 5, "column": 3, "message": "Unknown identifier `x`", "severity": "error"}),
        ]);
        assert_eq!(counts(&rows), (2, 1));
    }

    #[test]
    fn scan_ids_go_up_and_a_stop_leaves_nothing_active() {
        let first = next_id();
        assert_eq!(next_id(), first + 1);
        assert!(!is_active("C:/Isle") && unhook().is_none() && stopped(7));
    }
}
