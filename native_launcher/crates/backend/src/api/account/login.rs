//! Signing this PC in (`frontend/duckyos_account.py`: `start_browser_login`,
//! `cancel_browser_login`, `fetch_agent_caps`): the site gives a code, the person approves
//! it in their browser, and the site's answer to the poll is this PC's device key.
//!
//! The device code and the device key are secrets: they are only ever sent to the site
//! and sealed into the login row, never put in an answer, an event or an error.
//! ponytail: one sign-in per process (statics), as Python's module-level lock.
//! ponytail: after a sign-in the presence beat, the remote mailbox and the tool catalog
//! are started by the Python backend only, so `duckyos_login` is not routed here before
//! they are this backend's.

use std::sync::atomic::{AtomicBool, Ordering::SeqCst};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};

use serde_json::{json, Map, Value};

use super::super::settings::{load, set_fields};
use super::super::site::{collect, normalize_base_url, resolve_base_url, text, Collect, Refused};
use super::{first, save_login, status, stored, stripped, Api, Fields};
use crate::store::pyjson::truthy;

/// How long the person has to approve the code.
const WAIT: Duration = Duration::from_secs(600);
/// What an agent may do when the account says nothing (`_CAP_DEFAULTS`).
const CAP_DEFAULTS: [(&str, bool); 4] = [("allow_settings_write", true), ("allow_agent_clicks", false), ("allow_see_uefn", true), ("allow_see_other_programs", true)];

static RUNNING: AtomicBool = AtomicBool::new(false);
static CANCEL: AtomicBool = AtomicBool::new(false);
/// The code the person types on the site, while a sign-in waits for it.
static CODE: Mutex<String> = Mutex::new(String::new());

fn code() -> MutexGuard<'static, String> {
    CODE.lock().unwrap_or_else(|e| e.into_inner())
}

/// The waiting sign-in's code, empty with none (`get_status` shows it).
pub(super) fn pending_code() -> String {
    code().clone()
}

/// The running sign-in: however it ends, its code goes and the next one may start.
struct Running;

impl Drop for Running {
    fn drop(&mut self) {
        code().clear();
        RUNNING.store(false, SeqCst);
    }
}

fn device_call<'a>(event: &'a str, error_code: &'a str) -> Collect<'a> {
    Collect { plugin: "uefn-ducky", event, unavailable_code: "auth_unavailable", error_code, allow_anonymous: true, timeout: Duration::from_secs(20), retries: 2 }
}

fn broke(error: String) -> Refused {
    Refused::new(error, "error")
}

fn cancelled() -> Refused {
    Refused::new("Browser login cancelled", "cancelled")
}

async fn status_now(api: &Api) -> Result<Value, Refused> {
    status(api, &stored(api).await.map_err(broke)?).await.map_err(broke)
}

async fn sign_in(api: &Arc<Api>, base_url: String) -> Result<Value, Refused> {
    let base = normalize_base_url(&if base_url.is_empty() { resolve_base_url(api).await? } else { base_url })?;
    if let Ok(settings) = load(api).await {
        if text(settings.get("duckyos_base_url")).trim().trim_end_matches('/') != base {
            let _ = set_fields(api, vec![("duckyos_base_url", base.clone().into())]).await;
        }
    }
    if RUNNING.swap(true, SeqCst) {
        // A link and the Account tab can both start one: the second shows the first's code.
        let mut status = status_now(api).await?;
        status["ok"] = true.into();
        status["browser_pending"] = true.into();
        return Ok(status);
    }
    let _running = Running;
    CANCEL.store(false, SeqCst);
    code().clear();

    let started = collect(api, device_call("desktop-device-start", "device_start_failed"), &json!({})).await?;
    let said = |name: &str| stripped(started.get(name).unwrap_or(&Value::Null));
    let (user_code, device_code) = (said("user_code"), said("device_code"));
    if user_code.is_empty() || device_code.is_empty() {
        return Err(Refused::new("Could not start login.", "device_start_failed"));
    }
    *code() = user_code;

    let deadline = Instant::now() + WAIT;
    let mut approved = Map::new();
    while Instant::now() < deadline {
        if CANCEL.load(SeqCst) {
            return Err(cancelled());
        }
        let answer = collect(api, device_call("desktop-device-poll", "device_poll_failed"), &json!({"device_code": device_code})).await?;
        if answer.get("status").map(text).unwrap_or_default() != "pending" {
            approved = answer;
            break;
        }
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
    if CANCEL.load(SeqCst) {
        return Err(cancelled());
    }
    let of = |name: &str| approved.get(name).unwrap_or(&Value::Null);
    let token = text(of("token"));
    if !token.starts_with("dky_v1_") {
        return Err(Refused::new("Timed out waiting for the code to be approved.", "browser_timeout"));
    }
    let login = json!({"base_url": base, "email": text(of("email")), "device_key": token, "device_key_id": first(&[of("keyId"), of("key_id")])});
    save_login(api, login).await.map_err(broke)?;
    // The website's card for this PC reads this: on before the first presence beat.
    let _ = set_fields(api, vec![("remote_access", true.into())]).await;
    // The account's agent limits can take ten seconds: the person is signed in already.
    let after = Arc::clone(api);
    tokio::spawn(async move { fetch_agent_caps(&after).await });
    let mut status = status_now(api).await?;
    status["ok"] = true.into();
    Ok(status)
}

/// Start a sign-in and wait for the person to approve its code (`duckyos_login`). The
/// page reads the code from `duckyos_get_status` meanwhile.
pub async fn duckyos_login(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let base_url = text(args.get("base_url").unwrap_or(&Value::Null));
    Ok(sign_in(&api, base_url).await.unwrap_or_else(|refused| json!({"ok": false, "error": refused.message, "code": refused.code, "logged_in": false})))
}

/// Tell a waiting sign-in to stop (`cancel_browser_login`).
pub async fn duckyos_cancel_login(api: Arc<Api>, _args: Fields) -> Result<Value, String> {
    CANCEL.store(true, SeqCst);
    code().clear();
    status(&api, &stored(&api).await?).await
}

/// `fetch_agent_caps`: what the account lets agents do on its PCs, kept in the login row
/// and in the settings. A PC whose limits were set here keeps its own.
pub async fn fetch_agent_caps(api: &Api) -> Result<(), String> {
    let mut login = stored(api).await?;
    if truthy(&login["agent_caps_local"]) || !(truthy(&login["device_key"]) || truthy(&login["session_value"])) {
        return Ok(());
    }
    let ask = Collect { plugin: "uefn-ducky", event: "agent-caps-get", unavailable_code: "auth_unavailable", error_code: "agent_caps_failed", allow_anonymous: false, timeout: Duration::from_secs(12), retries: 0 };
    let Ok(row) = collect(api, ask, &json!({})).await else {
        return Ok(());
    };
    if let Some(Value::Array(denied)) = row.get("denied") {
        login["agent_denied"] = denied.iter().filter(|name| name.as_str().is_some_and(|name| !name.trim().is_empty())).cloned().collect();
    }
    if let Some(Value::Object(settings)) = row.get("settings") {
        let caps = CAP_DEFAULTS.iter().map(|(key, default)| (key.to_string(), Value::Bool(settings.get(*key).map_or(*default, truthy))));
        login["agent_settings"] = Value::Object(caps.collect());
    }
    let kept = login["agent_settings"].clone();
    save_login(api, login).await?;
    if let Value::Object(kept) = kept {
        let caps = CAP_DEFAULTS.iter().filter_map(|(key, _)| kept.get(*key).map(|value| (*key, Value::Bool(truthy(value)))));
        set_fields(api, caps.collect()).await?;
    }
    Ok(())
}
