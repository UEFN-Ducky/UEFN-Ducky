//! What the app-version feed says, and what the What's new window shows of it
//! (`frontend/version_check.py`, `frontend/whats_new.py`, `net_access.explain`). No I/O.

use serde_json::{json, Map, Value};

use crate::store::pyjson::{pystr, truthy};

/// The What's new window lists at most this many versions, newest first.
const MAX_VERSIONS: usize = 5;

/// `str(value or "").strip()`.
pub fn stripped(value: Option<&Value>) -> String {
    value.filter(|value| truthy(value)).map(pystr).unwrap_or_default().trim().to_string()
}

/// `app_versions.parse_version`: strictly `X.Y.Z`.
/// ponytail: ASCII digits and numbers up to 2^64; Python also takes other scripts' digits.
pub fn parse_version(text: &str) -> Option<(u64, u64, u64)> {
    let number = |part: &str| (!part.is_empty() && part.bytes().all(|b| b.is_ascii_digit())).then(|| part.parse::<u64>().ok()).flatten();
    let parts: Vec<&str> = text.trim().split('.').collect();
    match parts.as_slice() {
        [a, b, c] => Some((number(a)?, number(b)?, number(c)?)),
        _ => None,
    }
}

/// `is_remote_newer`: false when either is not a version.
pub fn newer(local: &str, remote: &str) -> bool {
    matches!((parse_version(local), parse_version(remote)), (Some(local), Some(remote)) if remote > local)
}

/// `_extract_str`: a value that is there and not blank, as text.
fn said(payload: &Map<String, Value>, key: &str) -> Option<String> {
    let text = payload.get(key).filter(|value| !value.is_null()).map(pystr)?.trim().to_string();
    (!text.is_empty()).then_some(text)
}

/// The feed's answer as its app-version object (`unwrap_collect_payload`). No object
/// to use is an error; an empty one is "nothing", with no error, as Python reads it.
pub fn feed_payload(raw: &str) -> Result<Map<String, Value>, Option<String>> {
    let envelope = match serde_json::from_str::<Value>(raw) {
        Ok(Value::Object(envelope)) => envelope,
        Ok(_) => return Err(Some("Invalid JSON: expected object".to_string())),
        Err(error) => return Err(Some(format!("Invalid JSON: {error}"))),
    };
    let names = |body: &Map<String, Value>, keys: &[&str]| keys.iter().any(|key| body.contains_key(*key));
    let payload = envelope.get("payload").and_then(Value::as_object);
    let inner = payload.and_then(|payload| payload.get("payload")).and_then(Value::as_object);
    let picked = match (payload, inner) {
        (_, Some(inner)) if names(inner, &["currentVersion", "installerUrl"]) => inner,
        (Some(payload), _) if names(payload, &["currentVersion", "installerUrl", "version"]) => payload,
        _ if names(&envelope, &["currentVersion", "installerUrl", "version"]) => &envelope,
        (Some(payload), _) => payload,
        _ => &envelope,
    };
    if picked.is_empty() {
        return Err(None);
    }
    Ok(picked.clone())
}

/// `_short_items`: a version's one-line changes (`kind` new, improved or fix).
fn short_items(raw: Option<&Value>) -> Vec<Value> {
    let rows = raw.and_then(Value::as_array).map(Vec::as_slice).unwrap_or_default();
    let item = |item: &Value| {
        let (text, kind) = (stripped(item.get("text")), stripped(item.get("kind")));
        let kind = if ["new", "improved", "fix"].contains(&kind.as_str()) { kind } else { "improved".to_string() };
        (item.is_object() && !text.is_empty()).then(|| json!({"kind": kind, "text": text}))
    };
    rows.iter().filter_map(item).collect()
}

/// `extract_release_versions`: the feed's public patch notes, or its one current note.
pub fn release_versions(payload: &Map<String, Value>) -> Vec<Value> {
    let rows = payload.get("versions").and_then(Value::as_array).map(Vec::as_slice).unwrap_or_default();
    let note = |row: &Value| {
        let version = stripped(row.get("version"));
        if !row.is_object() || version.is_empty() {
            return None;
        }
        let created = stripped(row.get("created_at"));
        let changelog = [row.get("changelog"), row.get("notes")].into_iter().flatten().find(|text| truthy(text)).map(pystr).unwrap_or_default();
        let mut note = json!({"version": version, "changelog": changelog.trim(), "created_at": if created.is_empty() { Value::Null } else { created.into() }});
        let items = short_items(row.get("items"));
        if !items.is_empty() {
            note["items"] = items.into();
        }
        Some(note)
    };
    let out: Vec<Value> = rows.iter().filter_map(note).collect();
    if !out.is_empty() {
        return out;
    }
    let version = ["currentVersion", "current_version", "version"].into_iter().find_map(|key| said(payload, key));
    let notes = said(payload, "releaseNotes").or_else(|| said(payload, "notes")).unwrap_or_default();
    version.map(|version| vec![json!({"version": version, "changelog": notes, "created_at": null})]).unwrap_or_default()
}

/// `whats_new._items`: the short changes of a kept note.
fn kept_items(row: &Value) -> Vec<Value> {
    let rows = row["items"].as_array().map(Vec::as_slice).unwrap_or_default();
    let item = |item: &Value| {
        let text = stripped(item.get("text"));
        let kind = item.get("kind").filter(|kind| truthy(kind)).map_or_else(|| "improved".to_string(), pystr);
        (item.is_object() && !text.is_empty()).then(|| json!({"kind": kind, "text": pystr(&item["text"]).trim()}))
    };
    rows.iter().filter_map(item).collect()
}

/// `_with_items`: the versions up to `current` that have short changes, newest first.
fn with_items(rows: &[&Value], current: &str) -> Vec<Value> {
    let mut picked: Vec<Value> = Vec::new();
    for row in rows {
        let (version, items) = (stripped(row.get("version")), kept_items(row));
        if version.is_empty() || items.is_empty() || newer(current, &version) || picked.iter().any(|p| p["version"] == version.as_str()) {
            continue;
        }
        picked.push(json!({"version": version, "created_at": row["created_at"], "items": items}));
    }
    let rank = |row: &Value| parse_version(row["version"].as_str().unwrap_or_default()).unwrap_or_default();
    picked.sort_by_key(|row| std::cmp::Reverse(rank(row)));
    picked
}

/// `whats_new`: what the window shows. `mark` is the version to save as seen without
/// showing anything (a fresh install, or a running version with no short changes).
pub fn whats_new(rows: &[&Value], current: &str, last_seen: Option<&str>, fresh_install: bool) -> Value {
    let released = rows.iter().find(|row| stripped(row.get("version")) == current);
    let mut recent = with_items(rows, current);
    recent.truncate(MAX_VERSIONS);
    let mut result = json!({
        "current": current,
        "last_seen": last_seen,
        "released_at": released.map_or(Value::Null, |row| row["created_at"].clone()),
        "show": false,
        "versions": [],
        "recent": recent,
        "mark": null,
    });
    let seen = last_seen.filter(|seen| !seen.is_empty());
    if seen.is_some_and(|seen| !newer(seen, current)) {
        return result;
    }
    if last_seen.is_none() && fresh_install {
        result["mark"] = current.into();
        return result;
    }
    if released.is_none() {
        // Offline, or the feed doesn't list this build yet: ask again next start.
        return result;
    }
    let unseen: Vec<Value> = recent
        .iter()
        .filter(|row| {
            let version = row["version"].as_str().unwrap_or_default();
            last_seen.map_or(version == current, |seen| newer(seen, version))
        })
        .cloned()
        .collect();
    if unseen.is_empty() {
        result["mark"] = current.into();
        return result;
    }
    result["versions"] = unseen.into();
    result["show"] = true.into();
    result
}

/// Why the feed gave nothing to use: its own error status, or no answer at all.
pub enum Failure {
    Status(u16),
    Network(String),
}

impl Failure {
    /// `net_access.stage_of`: where the request died.
    pub fn stage(&self) -> &'static str {
        let text = match self {
            Failure::Status(407) => return "proxy",
            Failure::Status(_) => return "http",
            Failure::Network(text) => text.to_lowercase(),
        };
        let has = |words: &[&str]| words.iter().any(|word| text.contains(word));
        match () {
            _ if has(&["dns", "lookup", "name or service"]) => "dns",
            _ if has(&["proxy"]) => "proxy",
            _ if has(&["certificate", "ssl", "tls"]) => "tls",
            _ if has(&["timed out"]) => "timeout",
            _ => "connect",
        }
    }

    /// `update_network.retryable`: a busy server, a timeout or a dropped connection.
    /// Never a certificate failure, and a name that does not resolve stays unresolved.
    pub fn retryable(&self) -> bool {
        match self {
            Failure::Status(status) => [408, 429, 500, 502, 503, 504].contains(status),
            Failure::Network(_) => ["timeout", "connect"].contains(&self.stage()),
        }
    }

    /// `net_access.explain`: a sentence a person can act on, the raw error in brackets.
    pub fn explain(&self, host: &str) -> String {
        let plain = match self.stage() {
            "dns" => format!("UEFN Ducky can't look up {host} (DNS). Check the internet connection, or a VPN/DNS filter blocking it."),
            "connect" => format!("UEFN Ducky can't connect to {host}. If it opens in your browser, a firewall or antivirus is blocking UEFN Ducky: allow it, or press Test network."),
            "timeout" => format!("UEFN Ducky timed out reaching {host}. If it opens in your browser, a firewall, antivirus, VPN or proxy is blocking UEFN Ducky: press Test network."),
            "tls" => format!("UEFN Ducky reached {host} but the secure connection failed. Antivirus HTTPS scanning or a wrong PC clock usually causes this."),
            "proxy" => format!("The network's proxy refused UEFN Ducky's connection to {host}. Press Test network for details."),
            _ => format!("{host} answered with an error. Try again in a minute."),
        };
        let raw = match self {
            // The status line's words (`HTTPError.reason`), which the client does not keep:
            // the standard ones for that status.
            Failure::Status(status) => match reqwest::StatusCode::from_u16(*status).ok().and_then(|status| status.canonical_reason()) {
                Some(reason) => reason.to_string(),
                None => format!("HTTP Error {status}: "),
            },
            Failure::Network(text) => text.trim_start_matches("Network error: ").chars().take(120).collect(),
        };
        format!("{plain} ({raw})")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_version_is_three_numbers() {
        assert_eq!(parse_version(" 1.2.30 "), Some((1, 2, 30)));
        assert!(parse_version("1.2").is_none() && parse_version("1.2.3.4").is_none() && parse_version("v1.2.3").is_none() && parse_version("1..3").is_none());
        assert!(newer("1.2.9", "1.2.10") && !newer("1.2.10", "1.2.9") && !newer("1.2.3", "1.2.3") && !newer("", "1.2.3") && !newer("1.2.3", "next"));
    }

    #[test]
    fn the_feed_object_is_found_inside_its_envelopes() {
        let version = |raw: &str| feed_payload(raw).map(|payload| pystr(&payload["currentVersion"]));
        assert_eq!(version(r#"{"currentVersion": "1.0.0"}"#), Ok("1.0.0".to_string()));
        assert_eq!(version(r#"{"ok": true, "payload": {"currentVersion": "2.0.0"}}"#), Ok("2.0.0".to_string()));
        assert_eq!(version(r#"{"handled": true, "payload": {"payload": {"currentVersion": "3.0.0"}}}"#), Ok("3.0.0".to_string()));
        assert_eq!(feed_payload("{}"), Err(None));
        assert_eq!(feed_payload(r#"{"payload": {}}"#), Err(None));
        assert_eq!(feed_payload("[1]"), Err(Some("Invalid JSON: expected object".to_string())));
        assert!(feed_payload("<html>").unwrap_err().unwrap().starts_with("Invalid JSON: "));
    }

    #[test]
    fn notes_are_the_listed_versions_or_the_current_one() {
        let payload = |raw: Value| raw.as_object().cloned().unwrap();
        let listed = release_versions(&payload(json!({"versions": [
            {"version": " 1.2.0 ", "notes": " Faster. ", "created_at": "", "items": [{"kind": "odd", "text": " A "}, {"text": ""}, "x"]},
            {"version": "", "changelog": "skipped"},
            "not a row",
        ]})));
        assert_eq!(listed, vec![json!({"version": "1.2.0", "changelog": "Faster.", "created_at": null, "items": [{"kind": "improved", "text": "A"}]})]);
        let current = release_versions(&payload(json!({"versions": [], "current_version": 7, "notes": " Hi "})));
        assert_eq!(current, vec![json!({"version": "7", "changelog": "Hi", "created_at": null})]);
        assert!(release_versions(&payload(json!({"versions": "none"}))).is_empty());
    }

    #[test]
    fn whats_new_shows_only_what_was_not_seen() {
        let row = |version: &str| json!({"version": version, "created_at": format!("day-{version}"), "items": [{"kind": "new", "text": "Change"}]});
        let all = [row("1.0.2"), row("1.0.0"), row("1.0.1"), row("1.0.1"), row("1.0.3"), json!({"version": "0.9.0"})];
        let rows: Vec<&Value> = all.iter().collect();
        let shown = |result: &Value| result["versions"].as_array().unwrap().iter().map(|row| pystr(&row["version"])).collect::<Vec<_>>();

        let update = whats_new(&rows, "1.0.2", Some("1.0.0"), false);
        assert_eq!((shown(&update), &update["show"], &update["mark"], &update["released_at"]), (vec!["1.0.2".to_string(), "1.0.1".to_string()], &json!(true), &Value::Null, &json!("day-1.0.2")));
        assert_eq!(update["recent"].as_array().unwrap().len(), 3, "a newer version than the running one is not listed");
        // Seen already, a first start, a first look on an old install, and a build the feed does not list.
        assert_eq!(whats_new(&rows, "1.0.2", Some("1.0.2"), false)["show"], false);
        assert_eq!(whats_new(&rows, "1.0.2", None, true)["mark"], "1.0.2");
        assert_eq!(shown(&whats_new(&rows, "1.0.2", None, false)), ["1.0.2"]);
        let unlisted = whats_new(&rows, "1.0.9", Some("1.0.0"), false);
        assert_eq!((&unlisted["show"], &unlisted["mark"]), (&json!(false), &Value::Null));
        // Nothing short to show for the running version: seen without a window.
        assert_eq!(whats_new(&rows, "0.9.0", Some(""), false)["mark"], "0.9.0");
    }

    #[test]
    fn a_failed_check_says_where_it_died() {
        let busy = Failure::Status(503);
        assert!(busy.retryable() && !Failure::Status(404).retryable());
        assert_eq!(busy.explain("example.org"), "example.org answered with an error. Try again in a minute. (Service Unavailable)");
        assert_eq!(Failure::Status(407).stage(), "proxy");
        let stage = |text: &str| Failure::Network(format!("Network error: {text}")).stage();
        assert_eq!(stage("error sending request: dns error: failed to lookup address"), "dns");
        assert_eq!(stage("invalid peer certificate: UnknownIssuer"), "tls");
        assert_eq!(stage("timed out"), "timeout");
        assert_eq!(stage("connection refused"), "connect");
        assert!(Failure::Network("timed out".into()).retryable() && !Failure::Network("invalid peer certificate".into()).retryable());
    }
}
