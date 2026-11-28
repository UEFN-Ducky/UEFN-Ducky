//! Anonymous usage stats (`frontend/usage_stats.py`, `frontend/usage_events.py`): counts of
//! what the app does and what fails.
//!
//! An event is a name and props from `usage_events.json` (enum words and bucket labels
//! only; anything else is dropped before it is queued), kept in ducky.db (`events`, kind
//! `usage_queue`) and sent in batches to the Store's `usage` route with a random install
//! id. A batch never carries the account: no key, no cookie ([`Call::anonymous`]).
//! The `usage_stats` setting is read for every event and every batch: off queues nothing
//! and sends nothing.
//!
//! ponytail: the events the Python backend makes itself (app open and close, heartbeat,
//! turns, tool calls, errors) are still queued by it, into the same queue; each moves
//! here with its caller.
//! ponytail: Setup's install id and the switch's copy in the registry
//! (`HKCU\Software\UEFN Ducky`) are read and kept by the Python backend only, and an Intel
//! build under Rosetta says `macos-x64`; both move with the app-open event.

pub mod words;

use std::collections::HashMap;
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{Arc, LazyLock};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{json, Map, Value};
use tokio::sync::Notify;

use super::settings::load;
use super::site::{app_version, request, text, Call};
use super::{uuid4, Api};
use crate::store::pyjson::{dumps, sorted, truthy};
use crate::store::{account, events, kv, Store};

type Fields = Map<String, Value>;

/// One batch an hour, one a minute after start, and again a minute after a batch that
/// left rows behind (or found the Store away).
const FLUSH_EVERY: Duration = Duration::from_secs(3600);
const FIRST_FLUSH: Duration = Duration::from_secs(60);
/// A queue this long is sent without waiting for the hour.
const FLUSH_AT_ROWS: i64 = 1000;
/// Distinct (name, props) rows per batch: the Store's limit.
const BATCH_MAX: usize = 200;
const QUEUE_MAX_ROWS: i64 = 5000;
const QUEUE_MAX_AGE_S: f64 = 7.0 * 86400.0;
/// A batch the Store refuses this many times in a row is dropped, so one row it can
/// never take does not hold the queue.
const REFUSED_DROP_AFTER: u32 = 3;
const KIND: &str = "usage_queue";
const META_INSTALL: &str = "usage.install_id";
const META_INSTALL_AT: &str = "usage.install_created";
const ROUTE: &str = "/api/v1/plugins/uefn-ducky-store/collect/usage";
/// Refusals that mean the batch was too big, not bad: each half is sent once.
const TOO_LARGE: [&str; 4] = ["too_big", "too_large", "http_413", "payload_too_large"];

/// `{"ui": [names the page may report], "events": {name: {prop: rule}}}`.
static LIST: LazyLock<Value> =
    LazyLock::new(|| serde_json::from_str(include_str!("../../../../../ducky_app/frontend/usage_events.json")).expect("usage_events.json"));
static WAKE: Notify = Notify::const_new();
static REFUSED: AtomicU32 = AtomicU32::new(0);

fn now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|since| since.as_secs_f64()).unwrap_or_default()
}

/// The label of the first step `value` fits in (`usage_events.bucket`).
/// ponytail: Python's `float()` also reads digits grouped with `_` in a text ("1_000");
/// every caller sends a number.
fn bucket(value: &Value, steps: &[Value]) -> Option<String> {
    let n: f64 = match value {
        Value::Bool(flag) => u8::from(*flag).into(),
        Value::Number(n) => n.as_f64()?,
        Value::String(text) => text.trim().parse().ok()?,
        _ => return None,
    };
    let step = steps.iter().find(|step| step[0].as_f64().is_none_or(|bound| n <= bound)).filter(|_| !n.is_nan())?;
    step[1].as_str().map(str::to_string)
}

fn yes_no(value: &Value) -> &'static str {
    let yes = match value {
        Value::String(text) => ["yes", "true", "1", "ok"].contains(&text.trim().to_lowercase().as_str()),
        other => truthy(other),
    };
    if yes { "yes" } else { "no" }
}

/// A catalog identifier: `^[a-z0-9][a-z0-9_.-]{0,63}$`.
fn is_slug(word: &str) -> bool {
    let plain = |b: u8| b.is_ascii_lowercase() || b.is_ascii_digit();
    (1..=64).contains(&word.len()) && plain(word.as_bytes()[0]) && word.bytes().all(|b| plain(b) || b"_.-".contains(&b))
}

/// `1.2.345`.
fn is_version(word: &str) -> bool {
    let parts: Vec<&str> = word.split('.').collect();
    parts.len() == 3 && parts.iter().all(|part| !part.is_empty() && part.bytes().all(|b| b.is_ascii_digit()))
}

/// The cleaned props of event `name`, or none for a name that is not on the list
/// (`usage_events.validate`). A prop that is not listed, or whose value is outside its
/// enum or buckets, is left out; a bucket prop takes the raw number under its source name
/// or the label itself.
pub fn validate(name: &str, raw: &Fields) -> Option<Fields> {
    let spec = LIST["events"].get(name)?.as_object()?;
    let lower = |value: &Value| text(value).trim().to_lowercase();
    let mut out = Map::new();
    for (prop, rule) in spec {
        let given = raw.get(prop);
        let rule = rule.as_array().map(Vec::as_slice).unwrap_or_default();
        let kept = match rule.first().and_then(Value::as_str).unwrap_or_default() {
            "yes_no" => given.map(|value| yes_no(value).to_string()),
            "bucket" => {
                let steps = rule[2].as_array().map(Vec::as_slice).unwrap_or_default();
                match raw.get(rule[1].as_str().unwrap_or_default()) {
                    Some(number) => bucket(number, steps),
                    None => given.and_then(Value::as_str).filter(|label| steps.iter().any(|step| step[1] == *label)).map(str::to_string),
                }
            }
            "slug" => given.map(lower).filter(|slug| is_slug(slug)),
            "version" => given.map(|value| text(value).trim().to_string()).filter(|version| is_version(version)),
            _ => given.map(lower).filter(|word| rule.iter().skip(1).any(|allowed| allowed == word)),
        };
        if let Some(kept) = kept {
            out.insert(prop.clone(), kept.into());
        }
    }
    Some(out)
}

async fn enabled(api: &Api) -> bool {
    load(api).await.is_ok_and(|settings| truthy(settings.get("usage_stats")))
}

/// Queue one event. False when stats are off or the event is not on the list.
pub async fn track(api: &Api, name: &str, props: &Fields) -> bool {
    if !enabled(api).await {
        return false;
    }
    let (Some(props), Ok(store)) = (validate(name, props), api.repos.store().await) else {
        return false;
    };
    let ts = now();
    let row = json!({"name": name, "props": props, "ts": ts});
    if events::insert(store, KIND.to_string(), ts, name.to_string(), String::new(), row).await.is_err() {
        return false;
    }
    // ponytail: one count per event; count per write batch if events ever come in bursts.
    let queued = events::count(store, KIND.to_string()).await.unwrap_or(0);
    if queued > QUEUE_MAX_ROWS {
        let _ = events::trim(store, KIND.to_string(), ts - QUEUE_MAX_AGE_S, QUEUE_MAX_ROWS).await;
    }
    if queued == FLUSH_AT_ROWS {
        WAKE.notify_one();
    }
    true
}

/// `usage_track`: the page reports a tab opened or a setup step; any other name is refused.
pub async fn usage_track(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let name = text(args.get("name").unwrap_or(&Value::Null));
    let ui = LIST["ui"].as_array().is_some_and(|names| names.iter().any(|listed| *listed == name));
    let none = Map::new();
    let props = args.get("props").and_then(Value::as_object).unwrap_or(&none);
    Ok((ui && track(&api, &name, props).await).into())
}

/// The random install id, made on first use. Never derived from the machine.
async fn install_id(store: &Store) -> String {
    if let Ok(Some(id)) = kv::meta_get(store, META_INSTALL.to_string()).await {
        if id.len() == 32 {
            return id;
        }
    }
    let fresh = uuid4().replace('-', "");
    let _ = kv::meta_set(store, META_INSTALL.to_string(), fresh.clone()).await;
    let _ = kv::meta_set(store, META_INSTALL_AT.to_string(), now().to_string()).await;
    fresh
}

/// One of `usage_events.PLATFORMS`: the Store splits every batch's counts by it.
fn platform() -> &'static str {
    match (std::env::consts::OS, std::env::consts::ARCH) {
        ("windows", "x86_64") => "windows-x64",
        ("windows", "aarch64") => "windows-arm64",
        ("linux", "x86_64") => "linux-x64",
        ("linux", "aarch64") => "linux-arm64",
        ("macos", "aarch64") => "macos-arm64",
        ("macos", "x86_64") => "macos-x64",
        _ => "other",
    }
}

/// Runs of anything but `a-z0-9` as one `_`, none at the ends, at most 24 characters.
fn word(text: &str) -> String {
    let mut out = String::new();
    for ch in text.chars() {
        match ch {
            'a'..='z' | '0'..='9' => out.push(ch),
            _ if !out.ends_with('_') => out.push('_'),
            _ => {}
        }
    }
    let cut: String = out.trim_matches('_').chars().take(24).collect();
    cut.trim_matches('_').to_string()
}

/// The Store's error as one short word (`usage_stats.reason_token`): a fixed class when it
/// is one, else the text before its first `:` (never the detail after it), else the status.
fn reason_token(error: &str, status: u16) -> String {
    if status == 413 {
        return "too_big".to_string();
    }
    if status >= 500 {
        return "server_5xx".to_string();
    }
    let low = error.to_lowercase();
    let has = |words: &[&str]| words.iter().any(|word| low.contains(word));
    // "install id", "install_id", "installid".
    let install_id = low.match_indices("install").any(|(at, found)| {
        let rest = &low[at + found.len()..];
        let next = rest.chars().next().filter(|ch| *ch != '\n').map_or(0, char::len_utf8);
        rest.starts_with("id") || rest[next..].starts_with("id")
    });
    let class = [
        ("install_id", install_id),
        ("too_big", has(&["too large", "too big", "payload", "max_events", "too many"])),
        ("rate", has(&["too soon", "rate", "429"])),
        ("origin", has(&["origin", "cors"])),
    ];
    if let Some((name, _)) = class.iter().find(|(_, found)| *found) {
        return name.to_string();
    }
    let head = word(low.split(':').next().unwrap_or_default());
    match (head.is_empty(), status) {
        (false, _) => head,
        (true, 0) => "unknown".to_string(),
        (true, status) => format!("http_{status}"),
    }
}

/// A sandbox copy or a test's process never sends: its rows stay in its own queue, so the
/// Store only hears from real installs (`usage_stats._test_copy`).
async fn test_copy(api: &Api) -> bool {
    let set = |name: &str| std::env::var_os(name).is_some();
    if !(std::env::var_os("DUCKY_SANDBOX").is_some_and(|value| !value.is_empty()) || set("PYTEST_CURRENT_TEST") || set("PYTEST_VERSION")) {
        return false;
    }
    // Only a parity test's fake site is sent to: plain http on this PC, which no saved
    // account address can be.
    let login = match api.repos.store().await {
        Ok(store) => account::login(store).await.unwrap_or_default(),
        Err(_) => Value::Null,
    };
    !(set("DUCKY_TESTS_ISOLATED_APPDATA") && text(&login["base_url"]).starts_with("http://127.0.0.1:"))
}

/// `ok`, `refused` with the Store's reason (it said no to this batch), or `failed` (no
/// answer: send it again later).
async fn send(api: &Api, body: &Value) -> (&'static str, String) {
    let failed = ("failed", String::new());
    if test_copy(api).await {
        return failed;
    }
    let call = Call { method: "POST", path: ROUTE, body: Some(body), allow_anonymous: true, anonymous: true, timeout: Duration::from_secs(20) };
    let Ok(answer) = request(api, call).await else {
        return failed;
    };
    let said = |body: &Fields| {
        let inner = body.get("payload").and_then(Value::as_object).and_then(|payload| payload.get("error")).filter(|error| truthy(error));
        inner.or(body.get("error")).map(text).unwrap_or_default()
    };
    let error = answer.parsed.as_ref().map(said).unwrap_or_default();
    // 404: the site's worker mid-swap; 408, 429 and 5xx: busy.
    if [404, 408, 429].contains(&answer.status) || answer.status >= 500 {
        return failed;
    }
    if !(200..300).contains(&answer.status) {
        return ("refused", reason_token(&error, answer.status));
    }
    // The Store answers a refused batch with 200 and an error.
    if error.is_empty() {
        return ("ok", String::new());
    }
    // "too soon" (its rate limit) and "try again" (its own read or write failed) are no
    // refusal of the batch.
    if error.contains("too soon") || error.contains("try again") {
        return failed;
    }
    ("refused", reason_token(&error, 0))
}

/// Identical (name, props) rows as one wire row with `n`, oldest first, each with its
/// rows' ids: `ts` is its last time and `t0` its first.
fn coalesce(rows: &[Value]) -> Vec<(Value, Vec<i64>)> {
    let mut at: HashMap<String, usize> = HashMap::new();
    let mut groups: Vec<(Value, Vec<i64>)> = Vec::new();
    for row in rows {
        let payload = &row["payload"];
        let Some(name) = payload["name"].as_str().filter(|name| !name.is_empty()) else {
            continue;
        };
        let props = if truthy(&payload["props"]) { payload["props"].clone() } else { json!({}) };
        let ts = Some(&payload["ts"]).filter(|ts| truthy(ts)).unwrap_or(&row["ts"]).as_f64().unwrap_or_default();
        let id = row["id"].as_i64().unwrap_or_default();
        let key = format!("{name}{}", dumps(&sorted(&props)));
        match at.get(&key) {
            Some(&group) => {
                let (wire, ids) = &mut groups[group];
                wire["n"] = (wire["n"].as_i64().unwrap_or_default() + 1).into();
                wire["ts"] = wire["ts"].as_f64().unwrap_or_default().max(ts).into();
                wire["t0"] = wire["t0"].as_f64().unwrap_or_default().min(ts).into();
                ids.push(id);
            }
            None => {
                at.insert(key, groups.len());
                groups.push((json!({"name": name, "props": props, "ts": ts, "t0": ts, "n": 1}), vec![id]));
            }
        }
    }
    groups
}

async fn report_refused(api: &Api, reason: &str) {
    let reason = if reason.is_empty() { "unknown" } else { reason };
    let props = json!({"code": "usage_batch_refused", "where": "store", "kind": "bad_request", "version": app_version(), "reason": reason});
    track(api, "error.code", props.as_object().expect("an object")).await;
}

/// Send one batch: the queue merged into at most [`BATCH_MAX`] counted rows. True when
/// the queue is empty after it (the Store takes one batch per PC per 30 s, so there is
/// no second one now).
pub async fn flush(api: &Api) -> bool {
    if !enabled(api).await {
        return false;
    }
    let Ok(store) = api.repos.store().await else {
        return true;
    };
    let groups = coalesce(&events::oldest(store, KIND.to_string(), QUEUE_MAX_ROWS).await.unwrap_or_default());
    if groups.is_empty() {
        return true;
    }
    let batch = &groups[..groups.len().min(BATCH_MAX)];
    let wire = |part: &[(Value, Vec<i64>)]| Value::Array(part.iter().map(|(row, _)| row.clone()).collect());
    let ids = |part: &[(Value, Vec<i64>)]| part.iter().flat_map(|(_, ids)| ids.clone()).collect::<Vec<i64>>();
    let mut body = json!({"installId": install_id(store).await, "appVersion": app_version(), "platform": platform(), "events": wire(batch)});
    let (sent, reason) = send(api, &body).await;
    if sent == "refused" && TOO_LARGE.contains(&reason.as_str()) && batch.len() > 1 {
        REFUSED.store(0, Ordering::Relaxed);
        // Each half once: one that lands or is refused goes, one with no answer stays queued.
        let mut refused = None;
        for half in [&batch[..batch.len() / 2], &batch[batch.len() / 2..]] {
            body["events"] = wire(half);
            let (got, why) = send(api, &body).await;
            if got != "failed" {
                let _ = events::delete_ids(store, ids(half)).await;
            }
            if got == "refused" {
                refused.get_or_insert(why);
            }
        }
        if let Some(why) = refused {
            report_refused(api, &why).await;
        }
        return false;
    }
    match sent {
        "ok" => {}
        "refused" if REFUSED.fetch_add(1, Ordering::Relaxed) + 1 >= REFUSED_DROP_AFTER => {}
        _ => return false,
    }
    let _ = events::delete_ids(store, ids(batch)).await;
    REFUSED.store(0, Ordering::Relaxed);
    if sent == "refused" {
        // The report itself goes in the next batch.
        report_refused(api, &reason).await;
        return false;
    }
    groups.len() <= BATCH_MAX
}

/// The sender: a batch a minute after start, then one an hour, sooner once the queue is
/// long. Runs until dropped.
pub async fn run(api: Arc<Api>) {
    let mut wait = FIRST_FLUSH;
    loop {
        let _ = tokio::time::timeout(wait, WAKE.notified()).await;
        wait = if flush(&api).await { FLUSH_EVERY } else { FIRST_FLUSH };
    }
}

/// The parity tests' way in (`/__rs/usage/<what>`): any event, not only the page's.
pub async fn probe(api: &Api, what: &str, body: &Value) -> Value {
    let none = Map::new();
    let (name, props) = (text(&body["name"]), body["props"].as_object().unwrap_or(&none));
    match what {
        "validate" => validate(&name, props).map_or(Value::Null, Value::Object),
        "track" => track(api, &name, props).await.into(),
        "reason" => reason_token(&text(&body["error"]), body["status"].as_u64().unwrap_or_default() as u16).into(),
        _ => flush(api).await.into(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cleaned(name: &str, props: Value) -> Value {
        validate(name, props.as_object().unwrap()).map_or(Value::Null, Value::Object)
    }

    #[test]
    fn only_listed_words_and_bucket_labels_are_kept() {
        assert_eq!(cleaned("no.such", json!({})), Value::Null);
        let turn = json!({"provider": " Claude ", "outcome": "exploded", "duration_s": 61, "tool_calls": "1-5", "reason": "C:\\Users\\me", "v": "1.2.3", "note": "hi"});
        assert_eq!(cleaned("chat.turn", turn), json!({"provider": "claude", "duration": "5m", "tool_calls": "1-5", "v": "1.2.3"}));
        assert_eq!(cleaned("tool.call", json!({"tool": "My_Tool", "ok": "TRUE", "duration_s": "nan"})), json!({"tool": "my_tool", "ok": "yes"}));
        assert_eq!(cleaned("app.heartbeat", json!({"uefn_up": 0})), json!({"uefn_up": "no"}));
        assert_eq!(cleaned("app.close", json!({"uptime_s": true, "uptime": "8h"})), json!({"uptime": "5m"}));
        assert_eq!(cleaned("update.installed", json!({"from": "1.2", "to": " 1.2.10 "})), json!({"to": "1.2.10"}));
        assert!(is_slug("a") && is_slug(&"a".repeat(64)) && !is_slug(&"a".repeat(65)) && !is_slug("_a") && !is_slug(""));
    }

    #[test]
    fn a_refusal_is_one_word() {
        let reasons = [
            ("anything", 413, "too_big"),
            ("", 502, "server_5xx"),
            ("bad Install-ID: 9f", 400, "install_id"),
            ("installid", 0, "install_id"),
            ("install wizard", 0, "install_wizard"),
            ("Too many events", 0, "too_big"),
            ("wrong origin", 403, "origin"),
            ("Unknown event: C:\\Users\\me", 0, "unknown_event"),
            ("a very long reason that goes on and on: x", 0, "a_very_long_reason_that"),
            ("", 403, "http_403"),
            ("", 0, "unknown"),
        ];
        for (error, status, word) in reasons {
            assert_eq!(reason_token(error, status), word, "{error} {status}");
        }
    }

    #[test]
    fn identical_rows_go_as_one_with_a_count() {
        let row = |id: i64, name: &str, props: Value, ts: f64| json!({"id": id, "ts": 1.0, "payload": {"name": name, "props": props, "ts": ts}});
        let rows = [
            row(1, "feature.open", json!({"tab": "chat"}), 30.0),
            row(2, "app.heartbeat", json!({}), 20.0),
            row(3, "feature.open", json!({"tab": "chat"}), 10.0),
            json!({"id": 4, "ts": 5.0, "payload": {"props": {}}}),
            row(5, "feature.open", json!({"tab": "plan"}), 40.0),
        ];
        let groups = coalesce(&rows);
        assert_eq!(groups[0], (json!({"name": "feature.open", "props": {"tab": "chat"}, "ts": 30.0, "t0": 10.0, "n": 2}), vec![1, 3]));
        assert_eq!(groups[1], (json!({"name": "app.heartbeat", "props": {}, "ts": 20.0, "t0": 20.0, "n": 1}), vec![2]));
        assert_eq!((groups.len(), &groups[2].1), (3, &vec![5]));
    }
}
