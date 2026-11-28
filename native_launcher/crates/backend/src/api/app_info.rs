//! What a window asks about the app itself (`panel_api_window.py`): its version, its
//! patch notes, the What's new window and whether this copy is installed.
//!
//! ponytail: the feed has one extra try, DNS over HTTPS. Python's other one (its proxy
//! opener with the Windows certificate store) is how every request here already goes.

mod install;
pub(super) mod notes;

use std::sync::Arc;
use std::time::Duration;

use reqwest::header::{HeaderMap, HeaderValue};
use reqwest::{Method, Url};
use serde_json::{json, Map, Value};

use super::plugin_store::installed;
use super::settings::{load, set_fields};
use super::site::{app_version, resolve_base_url, send, Refused, USER_AGENT};
use super::update::{net_fail, network};
use super::Api;
use crate::store::kv;
use crate::store::pyjson::{py_eq, pystr, truthy};

type Fields = Map<String, Value>;

const FEED_PATH: &str = "/api/v1/plugins/uefn-ducky-store/collect/app-version";
const NOTES_KEY: &str = "patch_notes:app";
/// The last app version whose What's new was shown (or skipped) here, in `meta`.
const LAST_SEEN: &str = "whats_new.last_seen";
/// The plugins a fresh install may already have: they make no PC a used one.
const STARTER_GATEWAYS: [&str; 4] = ["anthropic", "openai", "google", "cursor"];

pub async fn get_version(_api: Arc<Api>, _args: Fields) -> Result<Value, String> {
    Ok(app_version().into())
}

/// What asking the feed gave.
pub(super) struct Fed {
    /// The feed's app-version object, or why there is none (no reason for an empty one).
    pub payload: Result<Fields, Option<String>>,
    /// `doh` when the PC's own DNS could not find the site and a public resolver did.
    pub fallback: &'static str,
    /// Where a failed request died (`Failure::stage`); empty otherwise.
    pub stage: &'static str,
}

/// `update_base_url`: the site the feed is on (`DUCKY_UPDATE_BASE_URL`: a local update
/// rehearsal's).
pub(super) async fn update_base(api: &Api) -> Result<String, Refused> {
    let rehearsal = std::env::var("DUCKY_UPDATE_BASE_URL").ok().map(|url| url.trim().trim_end_matches('/').to_string()).filter(|url| !url.is_empty());
    match rehearsal {
        Some(base) => Ok(base),
        None => resolve_base_url(api).await,
    }
}

/// `fetch_remote_payload`. A busy server, a timeout or a dropped connection is asked
/// again, waiting a little longer each time.
pub(super) async fn feed(api: &Api, timeout: Duration, tries: u64) -> Fed {
    asked(api, timeout, tries).await.unwrap_or_else(|error| Fed { payload: Err(error), fallback: "", stage: "" })
}

async fn asked(api: &Api, mut timeout: Duration, tries: u64) -> Result<Fed, Option<String>> {
    let base = update_base(api).await.map_err(|refused| Some(refused.message))?;
    let unusable = |error: String| Some(format!("Can't reach the update server. {error}"));
    let url = Url::parse(&format!("{base}{FEED_PATH}")).map_err(|error| unusable(error.to_string()))?;
    let host = url.host_str().unwrap_or(&base).to_lowercase();
    let mut headers = HeaderMap::new();
    // The collect endpoints want an Origin that is the site's own host.
    for (name, value) in [("accept", "application/json"), ("content-type", "application/json"), ("origin", base.as_str()), ("user-agent", USER_AGENT.as_str())] {
        headers.insert(name, HeaderValue::from_str(value).map_err(|error| unusable(error.to_string()))?);
    }
    let answered = |raw: &str, fallback| Ok(Fed { payload: notes::feed_payload(raw), fallback, stage: "" });
    for attempt in 1..=tries {
        let failure = match send(Method::POST, url.clone(), headers.clone(), Some("{}".to_string()), timeout).await {
            Ok(answer) if (200..300).contains(&answer.status) => return answered(&answer.raw, ""),
            Ok(answer) => notes::Failure::Status(answer.status),
            Err(refused) => notes::Failure::Network(refused.message),
        };
        let stage = failure.stage();
        if stage == "dns" {
            // The PC's DNS (and its proxy) can't find the site: look it up over HTTPS.
            if let Some(address) = network::doh_resolve(&host).await {
                let again = network::send_pinned(address, Method::POST, url.clone(), headers.clone(), Some("{}".to_string()), timeout).await;
                if let Some(answer) = again.ok().filter(|answer| (200..300).contains(&answer.status)) {
                    return answered(&answer.raw, "doh");
                }
            }
        }
        if let (true, notes::Failure::Network(text)) = (failure.cert(), &failure) {
            let error = network::clock_skew_message(&base).await.unwrap_or_else(|| text.trim_start_matches("Network error: ").to_string());
            return Ok(Fed { payload: Err(Some(error)), fallback: "", stage: "" });
        }
        if attempt == tries || !failure.retryable() {
            let status = if let notes::Failure::Status(status) = failure { status } else { 0 };
            net_fail(api, "update_check", stage, status).await;
            return Ok(Fed { payload: Err(unusable(failure.explain(&host))), fallback: "", stage });
        }
        if stage == "timeout" {
            timeout = timeout.max(Duration::from_secs(15)); // a slow link: the next try waits longer
        }
        tokio::time::sleep(Duration::from_millis(500 * attempt)).await;
    }
    Err(None)
}

/// The patch notes kept here, when they are a list. The copy is a cache: a store that
/// can't be read or written only means asking again.
pub(super) async fn kept_notes(api: &Api) -> Option<Value> {
    let store = api.repos.store().await.ok()?;
    kv::get_doc(store, "cache_docs", NOTES_KEY.to_string()).await.ok().flatten().filter(|doc| doc["versions"].is_array())
}

/// Keep `versions` as the running build's notes (written only when they changed).
pub(super) async fn keep_notes(api: &Api, versions: &[Value]) {
    let doc = json!({"version": app_version(), "versions": versions});
    let Ok(store) = api.repos.store().await else { return };
    let kept = kv::get_doc(store, "cache_docs", NOTES_KEY.to_string()).await.ok().flatten();
    if !kept.is_some_and(|kept| py_eq(&kept, &doc)) {
        let _ = kv::set_doc(store, "cache_docs", NOTES_KEY.to_string(), doc).await;
    }
}

/// `get_app_patch_notes`: the Store's notes for the running build, from the copy kept
/// here. The site is asked only after a version bump; offline answers the old copy.
async fn patch_notes(api: &Api) -> Value {
    let kept = kept_notes(api).await;
    let cached = kept.as_ref();
    if let Some(doc) = cached.filter(|doc| notes::stripped(doc.get("version")) == app_version()) {
        return json!({"versions": doc["versions"], "cached": true, "error": null});
    }
    match feed(api, Duration::from_secs(8), 5).await.payload {
        Ok(payload) => {
            let versions = notes::release_versions(&payload);
            keep_notes(api, &versions).await;
            json!({"versions": versions, "cached": false, "error": null})
        }
        Err(error) => match cached {
            Some(doc) => json!({"versions": doc["versions"], "cached": true, "error": error}),
            None => json!({"versions": [], "cached": false, "error": error}),
        },
    }
}

pub async fn get_app_patch_notes(api: Arc<Api>, _args: Fields) -> Result<Value, String> {
    Ok(patch_notes(&api).await)
}

/// `starter_llm_onboard_pending`: true before a fresh install's first setup finished.
/// A PC that already used Ducky (a finished walkthrough, a plugin beyond the starter
/// gateways) is not fresh.
/// A used PC's answer is saved (`starter_llm_gateways_seeded`), so it is worked out once.
async fn fresh_install(api: &Api) -> bool {
    let Ok(settings) = load(api).await else { return false };
    if truthy(settings.get("starter_llm_gateways_seeded")) {
        return false;
    }
    let enabled = settings.get("enabled_uefn_plugins").as_array().map(Vec::as_slice).unwrap_or_default();
    let mut used: Vec<String> = enabled.iter().map(|id| pystr(id).trim().to_lowercase()).filter(|id| !id.is_empty()).collect();
    let root = api.repos.data_dir().join("uefn_plugins");
    used.extend(tokio::task::spawn_blocking(move || installed::on_disk(&root)).await.unwrap_or_default());
    let fresh = !truthy(settings.get("walkthrough_completed")) && used.iter().all(|id| STARTER_GATEWAYS.contains(&id.as_str()));
    if !fresh {
        let _ = set_fields(api, vec![("starter_llm_gateways_seeded", true.into())]).await;
    }
    fresh
}

/// The panel's start-up question: show the What's new window, and with which versions.
pub async fn get_whats_new(api: Arc<Api>, _args: Fields) -> Result<Value, String> {
    let notes = patch_notes(&api).await;
    let rows: Vec<&Value> = notes["versions"].as_array().map(|rows| rows.iter().filter(|row| row.is_object()).collect()).unwrap_or_default();
    let store = api.repos.store().await.ok();
    let last_seen = match store {
        Some(store) => kv::meta_get(store, LAST_SEEN.to_string()).await.ok().flatten(),
        None => None,
    };
    let mut result = notes::whats_new(&rows, app_version(), last_seen.as_deref(), fresh_install(&api).await);
    if let (Some(store), Some(mark)) = (store, result["mark"].as_str()) {
        let _ = kv::meta_set(store, LAST_SEEN.to_string(), mark.to_string()).await;
    }
    result["error"] = notes["error"].clone();
    Ok(result)
}

/// Save `version` as seen, never moving back.
pub async fn mark_whats_new_seen(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let version = Some(notes::stripped(args.get("version"))).filter(|version| !version.is_empty()).unwrap_or_else(|| app_version().to_string());
    if let Ok(store) = api.repos.store().await {
        let last = kv::meta_get(store, LAST_SEEN.to_string()).await.ok().flatten().unwrap_or_default();
        if last.is_empty() || notes::newer(&last, &version) {
            let _ = kv::meta_set(store, LAST_SEEN.to_string(), version).await;
        }
    }
    Ok(json!({"ok": true}))
}

pub async fn get_install_info(api: Arc<Api>, _args: Fields) -> Result<Value, String> {
    let versions = api.repos.data_dir().join("versions");
    tokio::task::spawn_blocking(move || install::info(&versions)).await.map_err(|stopped| stopped.to_string())
}
