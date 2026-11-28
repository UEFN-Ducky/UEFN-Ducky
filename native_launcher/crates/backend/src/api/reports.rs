//! Crash reports kept on this PC, and what the page reports about itself
//! (`frontend/boot_report.py`: `list_reports`, `send_report`, `delete_report`;
//! `frontend/error_log.py::record_error`; `panel_api_window.py`: `report_ui_error`,
//! `report_ui_crash`).
//!
//! A report is a file the app wrote when its last run ended badly: already scrubbed, and
//! shown to the person as the exact text that is sent. Everything the page reports goes
//! through [`scrub`] before it is stored.
//! ponytail: writing a report (the part of `boot_report.py` that runs while the app
//! starts) stays with the starting app; here reports are only listed, sent and deleted.
//! ponytail: the lines the UEFN listener appends to errors.jsonl, and the old file's
//! one-time import, are still folded into the rows by the Python backend's reader
//! (`error_log._ingest_listener_file`); they move with the error log's reader.

use std::sync::Arc;
use std::time::Duration;

use reqwest::header::{self, HeaderMap, HeaderValue};
use reqwest::Url;
use serde_json::{json, Map, Value};

use super::privacy::scrub;
use super::site::{app_version, send, text, USER_AGENT};
use super::usage::track;
use super::usage::words::{code_of, kind_of, where_of};
use super::Api;
use crate::store::pyjson::dumps_ascii;
use crate::store::{account, events, now};

type Fields = Map<String, Value>;

/// The site's feedback form.
const COLLECT_URL: &str = "https://uefnducky.org/api/v1/plugins/custom-forms/collect/submit";
const ORIGIN: &str = "https://uefnducky.org";
const SEND_TIMEOUT: Duration = Duration::from_secs(6);
/// The error log keeps a day, and at most this many lines (`error_log.MAX_ENTRIES`).
const ERRORS_KEPT: i64 = 2000;
const ERRORS_MAX_AGE_S: f64 = 24.0 * 3600.0;

/// `str(args.get(name) or "")`.
fn said(from: &Fields, name: &str) -> String {
    text(from.get(name).unwrap_or(&Value::Null))
}

/// The first `n` characters (`text[:n]`).
fn cut(text: &str, n: usize) -> String {
    text.chars().take(n).collect()
}

/// `str.strip()`: Python also strips the four separator controls.
fn strip(text: &str) -> &str {
    text.trim_matches(|ch: char| ch.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&ch))
}

async fn blocking<T: Send + 'static>(work: impl FnOnce() -> T + Send + 'static) -> Result<T, String> {
    tokio::task::spawn_blocking(work).await.map_err(|stopped| stopped.to_string())
}

/// The report files, in the app's crash folder.
mod disk {
    #![allow(clippy::disallowed_methods)] // every caller is on a blocking thread

    use std::fs;
    use std::path::{Path, PathBuf};
    use std::sync::LazyLock;
    use std::time::UNIX_EPOCH;

    use regex::Regex;
    use serde_json::{json, Map, Value};

    use super::said;

    /// sent = the person sent it; kept = not sent (they chose not to, or sending failed).
    /// As Python's `$`, the end may be before one last line break.
    static REPORT_ID: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^(?:sent|kept)-\d{8}-\d{6}(?:-\d+)?\n?\z").expect("a fixed pattern"));

    /// `boot_report._dir`.
    /// ponytail: with neither LOCALAPPDATA nor APPDATA set Python looks under the home
    /// folder itself; Windows always sets them.
    fn dir() -> PathBuf {
        crate::app_data_dir().join("crash")
    }

    /// The file's JSON object, empty for anything else.
    pub fn read(path: &Path) -> Map<String, Value> {
        match fs::read_to_string(path).ok().and_then(|text| serde_json::from_str(&text).ok()) {
            Some(Value::Object(body)) => body,
            _ => Map::new(),
        }
    }

    /// `_report_path`: the file of a report id, when it names one that is there.
    pub fn path_of(id: &str) -> Option<PathBuf> {
        let path = dir().join(format!("{id}.json"));
        (REPORT_ID.is_match(id) && path.is_file()).then_some(path)
    }

    /// `report_text`: exactly what the report sends, as the person sees it.
    fn report_text(body: &Map<String, Value>) -> String {
        let version = Some(said(body, "app_version")).filter(|version| !version.is_empty()).unwrap_or_else(|| "?".to_string());
        let parts = [said(body, "message"), format!("App {version}"), said(body, "error_log")];
        parts.into_iter().filter(|part| !part.is_empty()).collect::<Vec<_>>().join("\n\n")
    }

    /// A report's id from its file name: the name without `.json` (any case on Windows,
    /// as its file names are).
    fn id_of(name: &str) -> Option<&str> {
        let (id, suffix) = name.split_at_checked(name.len().checked_sub(5)?)?;
        (suffix == ".json" || (cfg!(windows) && suffix.eq_ignore_ascii_case(".json"))).then_some(id).filter(|id| REPORT_ID.is_match(id))
    }

    /// `list_reports`: every saved report, newest first by the date in its name.
    pub fn list() -> Vec<Value> {
        let named = |entry: fs::DirEntry| id_of(&entry.file_name().to_string_lossy()).map(|id| (id.to_string(), entry.path()));
        let mut found: Vec<(String, PathBuf)> = fs::read_dir(dir()).into_iter().flatten().flatten().filter_map(named).collect();
        let date = |id: &str| id.split_once('-').map_or(String::new(), |(_state, date)| date.to_string());
        found.sort_by_key(|(id, _path)| std::cmp::Reverse(date(id)));
        let row = |(id, path): (String, PathBuf)| {
            let body = read(&path);
            let written = fs::metadata(&path).and_then(|file| file.modified()).ok().and_then(|at| at.duration_since(UNIX_EPOCH).ok());
            // As Python makes `st_mtime`: the seconds plus the nanoseconds times 1e-9.
            let at = written.map_or(0.0, |at| at.as_secs() as f64 + 1e-9 * f64::from(at.subsec_nanos()));
            json!({"id": id, "sent": id.starts_with("sent-"), "at": at, "message": said(&body, "message"), "text": report_text(&body)})
        };
        found.into_iter().map(row).collect()
    }

    /// A kept report was sent: its file says so. One that cannot be renamed stays kept.
    pub fn mark_sent(path: &Path) {
        let name = path.file_name().unwrap_or_default().to_string_lossy().replacen("kept-", "sent-", 1);
        let _ = fs::rename(path, path.with_file_name(name));
    }

    pub fn delete(id: &str) -> bool {
        path_of(id).is_some_and(|path| fs::remove_file(path).is_ok())
    }
}

/// `crash_reports`: the reports kept on this PC, newest first, each with the exact text it sends.
pub async fn crash_reports(_api: Arc<Api>, _args: Fields) -> Result<Value, String> {
    Ok(json!({"reports": blocking(disk::list).await?}))
}

/// Where a report is sent. Under the tests nothing reaches the real site: a parity test's
/// fake site (the signed-in address, plain http on this PC) takes it, or nothing is sent.
async fn collect_url(api: &Api) -> Option<Url> {
    if std::env::var_os("DUCKY_TESTS_ISOLATED_APPDATA").is_none() {
        return Url::parse(COLLECT_URL).ok();
    }
    let login = account::login(api.repos.store().await.ok()?).await.ok()?;
    let base = text(&login["base_url"]);
    let path = COLLECT_URL.strip_prefix(ORIGIN)?;
    base.starts_with("http://127.0.0.1:").then(|| Url::parse(&format!("{}{path}", base.trim_end_matches('/'))).ok()).flatten()
}

/// `boot_report._send`: the report to the site's feedback form. No account goes with it.
async fn post(api: &Api, body: &Value) -> bool {
    let Some(url) = collect_url(api).await else {
        return false;
    };
    let mut headers = HeaderMap::new();
    headers.insert(header::CONTENT_TYPE, HeaderValue::from_static("application/json"));
    headers.insert(header::ORIGIN, HeaderValue::from_static(ORIGIN));
    if let Ok(agent) = HeaderValue::from_str(&USER_AGENT) {
        headers.insert(header::USER_AGENT, agent);
    }
    send(reqwest::Method::POST, url, headers, Some(dumps_ascii(body)), SEND_TIMEOUT).await.is_ok_and(|answer| (200..300).contains(&answer.status))
}

/// `crash_report_send`: send a kept report; its file then says it was sent.
pub async fn crash_report_send(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let id = said(&args, "report_id");
    let kept = blocking(move || disk::path_of(&id).filter(|_| id.starts_with("kept-")).map(|path| (Value::Object(disk::read(&path)), path))).await?;
    let sent = match kept {
        Some((body, path)) if post(&api, &body).await => blocking(move || disk::mark_sent(&path)).await.is_ok(),
        _ => false,
    };
    Ok(if sent { json!({"ok": true}) } else { json!({"ok": false, "error": "Couldn't send the report. Check your connection and try again."}) })
}

/// `crash_report_delete`.
pub async fn crash_report_delete(_api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let id = said(&args, "report_id");
    Ok(json!({"ok": blocking(move || disk::delete(&id)).await?}))
}

/// `error_log.record_error`: one scrubbed line in Settings → Errors, unless it repeats the
/// last one, and its three words in the usage counts. Never fails its caller.
pub async fn record_error(api: &Api, source: &str, message: &str) {
    let message = scrub(strip(message));
    if message.is_empty() {
        return;
    }
    let words = json!({"code": code_of(source), "where": where_of(source), "kind": kind_of(&message), "version": app_version(), "at": ""});
    track(api, "error.code", words.as_object().expect("an object")).await;
    let Ok(store) = api.repos.store().await else {
        return;
    };
    let kind = || "error".to_string();
    if events::last_message(store, kind()).await.ok().flatten().as_deref() == Some(message.as_str()) {
        return;
    }
    let at = now();
    let source = if source.is_empty() { "?" } else { source };
    if events::insert(store, kind(), at, cut(source, 64), cut(&message, 2000), Value::Null).await.is_ok() {
        let _ = events::trim(store, kind(), at - ERRORS_MAX_AGE_S, ERRORS_KEPT).await;
    }
}

/// `report_ui_error`: a problem the page only timed out on still leaves a line.
pub async fn report_ui_error(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let given = Some(said(&args, "source")).filter(|source| !source.is_empty()).unwrap_or_else(|| "ui".to_string());
    let tag: String = given.to_lowercase().chars().filter(|ch| matches!(ch, 'a'..='z' | '0'..='9' | '_' | '-')).take(32).collect();
    let message = cut(strip(&said(&args, "message")), 1000);
    if !message.is_empty() {
        record_error(&api, if tag.is_empty() { "ui" } else { &tag }, &message).await;
    }
    Ok(json!({"ok": !message.is_empty()}))
}

/// `report_ui_crash`: a crash the page caught, kept for support.
pub async fn report_ui_crash(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let none = Map::new();
    let data = args.get("payload").and_then(Value::as_object).unwrap_or(&none);
    let part = |name: &str, n: usize| cut(&said(data, name), n);
    let scrubbed = |name: &str, n: usize| cut(&scrub(&said(data, name)), n);
    let version = app_version();
    let at = now();
    let row = json!({
        "ts": at,
        "version": version,
        "label": said(data, "label"),
        "message": scrubbed("message", 2000),
        "stack": scrubbed("stack", 8000),
        "componentStack": scrubbed("componentStack", 8000),
        "appVersion": Some(said(data, "appVersion")).filter(|given| !given.is_empty()).unwrap_or_else(|| version.to_string()),
        "pluginId": part("pluginId", 128),
        "surface": part("surface", 128),
        "faultKind": part("faultKind", 32),
        "faultAction": part("faultAction", 32),
    });
    let first = |names: &[&str]| names.iter().map(|name| text(&row[*name])).find(|word| !word.is_empty());
    let code = first(&["faultKind", "surface", "label"]).unwrap_or_else(|| "boundary".to_string());
    track(&api, "ui.crash", json!({"code": code_of(&code)}).as_object().expect("an object")).await;
    let source = first(&["surface", "label"]).unwrap_or_default();
    let saved = match api.repos.store().await {
        Ok(store) => events::insert(store, "ui_crash".to_string(), at, source, text(&row["message"]), row).await,
        Err(failed) => Err(failed),
    };
    Ok(match saved {
        Ok(()) => json!({"ok": true, "path": "ducky.db:events/ui_crash"}),
        Err(failed) => json!({"ok": false, "error": failed.0}),
    })
}

/// The parity tests' way in (`/__rs/reports/<what>`): the text rules on their own.
pub fn check(what: &str, text: &str) -> Value {
    match what {
        "scrub" => scrub(text).into(),
        "kind" => kind_of(text).into(),
        "where" => where_of(text).into(),
        _ => code_of(text).into(),
    }
}
