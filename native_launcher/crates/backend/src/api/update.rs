//! Is there a newer version (`frontend/version_check.py`: `get_app_update_status`;
//! `frontend/update_swap.py`: `status`, `check_and_stage`'s check; `frontend/updater.py`:
//! `linux_update_status`, `pick_asset`, `get_update_progress`), and the network test.
//!
//! ponytail: only the check is here. Getting a found version ready (the download),
//! switching to it and the timer that does both are still the Python backend's. Until
//! they move: no state here leaves `idle`, what a window reports about being in use is
//! not kept (only the switch reads it), and a copy past its start grace says its
//! background updater is not running, which offers Setup, as Python does for a copy
//! whose updater never started.

pub mod network;

use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, LazyLock, Mutex};
use std::time::{Duration, Instant};

use reqwest::Url;
use serde_json::{json, Map, Value};

use super::app_info::notes::{self, newer, parse_version, stripped};
use super::app_info::{feed, get_install_info, keep_notes, kept_notes, update_base};
use super::site::{app_version, resolve_base_url, DEFAULT_BASE_URL};
use super::usage::track;
use super::usage::words::code_of;
use super::Api;
use crate::store::pyjson::truthy;
use crate::store::{kv, now};

type Fields = Map<String, Value>;

/// The background updater gets this long to start before the update check calls it missing.
const NOT_RUNNING_AFTER: Duration = Duration::from_secs(5 * 60);
/// The new version shows "Updated to ..." this long after a switch.
const UPDATED_NOTE: Duration = Duration::from_secs(10 * 60);
const SWITCHED_FROM: &str = "UEFN_DUCKY_SWITCHED_FROM";
const META_RECOVERED: &str = "usage.update_check_recovered";

/// The update row: `off` until a check ran here, then `checking` and `idle`.
struct State {
    phase: &'static str,
    version: String,
    error: String,
}

static STATE: Mutex<State> = Mutex::new(State { phase: "off", version: String::new(), error: String::new() });
/// When this backend started (`serve` reads it first).
pub static STARTED: LazyLock<Instant> = LazyLock::new(Instant::now);
/// This session's first `update.check` was counted.
static COUNTED: AtomicBool = AtomicBool::new(false);
/// This session's checks: how many ended, and whether one failed since the last that worked.
static CHECKS: AtomicU32 = AtomicU32::new(0);
static CHECK_FAILED: AtomicBool = AtomicBool::new(false);
/// The (what, stage) failures to reach the site this session already counted.
static NET_FAILS: Mutex<Vec<(String, String)>> = Mutex::new(Vec::new());

fn set(phase: &'static str, version: Option<String>, error: String) {
    let mut state = STATE.lock().unwrap_or_else(|e| e.into_inner());
    state.phase = phase;
    state.error = error;
    if let Some(version) = version {
        state.version = version;
    }
}

/// `update_swap.status`: a start that came from a switch says so for a while.
fn shown(state: &State, switched_from: &str, up: Duration) -> Value {
    let mut out = json!({"phase": state.phase, "version": state.version, "done": 0, "total": 0, "error": state.error, "failures": 0});
    if !switched_from.is_empty() && ["off", "idle", "checking"].contains(&state.phase) && up < UPDATED_NOTE {
        out["phase"] = "updated".into();
        out["version"] = app_version().into();
        out["previous"] = switched_from.into();
    }
    out
}

fn status() -> Value {
    let switched_from = std::env::var(SWITCHED_FROM).unwrap_or_default();
    shown(&STATE.lock().unwrap_or_else(|e| e.into_inner()), switched_from.trim(), STARTED.elapsed())
}

pub async fn get_auto_update_status(_api: Arc<Api>, _args: Fields) -> Result<Value, String> {
    Ok(status())
}

/// `report_update_ui`: a window's word on whether Ducky is in use.
pub async fn report_update_ui(_api: Arc<Api>, _args: Fields) -> Result<Value, String> {
    Ok(Value::Null)
}

/// Settings' Check for updates: the state as it is, and a check started now.
pub async fn check_auto_update_now(api: Arc<Api>, _args: Fields) -> Result<Value, String> {
    let before = status();
    if allowed(&api).await {
        tokio::spawn(async move { check(&api).await });
    }
    Ok(before)
}

/// `get_update_progress`: the Setup download's progress.
/// ponytail: that download is the Python backend's, so nothing is ever in flight here.
pub async fn get_update_progress(_api: Arc<Api>, _args: Fields) -> Result<Value, String> {
    Ok(json!({"stage": "idle", "downloaded_bytes": 0, "total_bytes": 0, "error": null}))
}

/// `is_frozen_dev_exe`: the started program's name marks a dev build.
fn dev_exe() -> bool {
    let named = std::env::var_os("UEFN_DUCKY_LAUNCHER_EXE").map(std::path::PathBuf::from).or_else(|| std::env::current_exe().ok());
    named.and_then(|exe| exe.file_stem().map(|stem| stem.to_string_lossy().to_lowercase())).is_some_and(|stem| stem.starts_with("uefn-ducky-dev"))
}

/// `update_channel`: Store updates are for installed production copies only.
fn channel(install: &Value) -> &'static str {
    match () {
        _ if dev_exe() => "dev",
        _ if install["installed"] == true => "installed",
        _ => "portable",
    }
}

async fn install_info(api: &Arc<Api>) -> Value {
    get_install_info(Arc::clone(api), Map::new()).await.unwrap_or_else(|_| json!({}))
}

/// `_auto_update_allowed`: an installed copy, or a local rehearsal's feed on any copy
/// but a dev build.
async fn allowed(api: &Arc<Api>) -> bool {
    let channel = channel(&install_info(api).await);
    let loopback = || async {
        let host = update_base(api).await.ok().and_then(|base| Url::parse(&base).ok()).and_then(|url| url.host_str().map(str::to_lowercase));
        host.is_some_and(|host| ["127.0.0.1", "localhost", "[::1]"].contains(&host.as_str()))
    };
    channel == "installed" || (channel != "dev" && loopback().await)
}

/// `feed_is_paused`.
fn paused(payload: &Fields) -> bool {
    ["true", "1"].contains(&stripped(payload.get("downloadsPaused")).to_lowercase().as_str())
}

/// `extract_remote_version`.
fn remote_version(payload: &Fields) -> Option<String> {
    ["currentVersion", "current_version", "version"].into_iter().find_map(|key| notes::said(payload, key))
}

/// `feed_update`: the feed's update pack when it is for a version newer than `current`.
fn feed_update<'a>(payload: &'a Fields, current: &str) -> Option<&'a Fields> {
    let update = payload.get("update")?.as_object()?;
    let (remote, mine) = (parse_version(&stripped(update.get("version")))?, parse_version(current)?);
    let named = ["packUrl", "manifestUrl", "manifestSha256"].iter().all(|key| !stripped(update.get(*key)).is_empty());
    (remote > mine && named).then_some(update)
}

/// `a or b`.
fn either(a: Option<&Value>, b: Option<&Value>) -> Value {
    a.filter(|a| truthy(a)).or(b).cloned().unwrap_or(Value::Null)
}

fn os_name() -> &'static str {
    if cfg!(windows) { "windows" } else { std::env::consts::OS }
}

/// `pick_asset`: the feed's `assets` row for an OS, CPU and install kind. Windows falls
/// back to the old top-level installer fields (always the x64 Setup).
fn pick_asset(payload: &Fields, os: &str, machine: &str, kind: &str) -> Option<Fields> {
    let machine = machine.to_lowercase();
    let arch = match machine.as_str() {
        "x86_64" | "amd64" | "x64" => "x64",
        "aarch64" | "arm64" => "arm64",
        other => other,
    };
    let package = match kind {
        "installer" => "setup",
        "app" => "app-zip",
        "appimage" | "deb" | "rpm" | "flatpak" | "aur" => kind,
        _ => return None,
    };
    let rows = payload.get("assets").and_then(Value::as_array).map(Vec::as_slice).unwrap_or_default();
    let is = |row: &Fields, key: &str, word: &str| row.get(key).and_then(Value::as_str) == Some(word);
    if let Some(row) = rows.iter().filter_map(Value::as_object).find(|row| is(row, "os", os) && is(row, "arch", arch) && is(row, "package", package)) {
        return Some(row.clone());
    }
    let url = either(payload.get("installerUrl"), payload.get("installer_url"));
    let old = (os, package, arch) == ("windows", "setup", "x64") && truthy(&url);
    let sha256 = either(payload.get("installerSha256"), payload.get("sha256"));
    old.then(|| json!({"os": "windows", "arch": "x64", "package": "setup", "url": url, "sha256": sha256, "sig_url": null}).as_object().cloned().unwrap_or_default())
}

/// `native_machine`: the PC's own CPU, even from an x64 build running emulated on arm64.
fn native_machine() -> &'static str {
    #[cfg(windows)]
    {
        use windows_sys::Win32::System::Threading::{GetCurrentProcess, IsWow64Process2};
        let (mut process, mut native) = (0u16, 0u16);
        // SAFETY: both out values are whole u16s of this frame.
        if unsafe { IsWow64Process2(GetCurrentProcess(), &mut process, &mut native) } != 0 {
            match native {
                0xAA64 => return "ARM64",
                0x8664 => return "AMD64",
                _ => {}
            }
        }
    }
    std::env::consts::ARCH
}

/// `absolute_installer_url`: a feed link, made whole against the feed's own address.
fn absolute(link: Option<&Value>, base: &str) -> Value {
    let text = stripped(link);
    let lower = text.to_lowercase();
    match () {
        _ if text.is_empty() => Value::Null,
        _ if lower.starts_with("https://") || lower.starts_with("http://") => text.into(),
        _ => match Url::parse(&format!("{}/", base.trim_end_matches('/'))).and_then(|base| base.join(text.trim_start_matches('/'))) {
            Ok(whole) => whole.to_string().into(),
            Err(_) => Value::Null,
        },
    }
}

/// What this copy is, as the status reads it (a test names its own).
struct Copy {
    install: Value,
    channel: String,
    /// The background download keeps failing here, or is not running at all: Setup is offered.
    stuck: bool,
    machine: String,
}

async fn this_copy(api: &Arc<Api>, named: &Value) -> Copy {
    let install = if named["install"].is_object() { named["install"].clone() } else { install_info(api).await };
    Copy {
        channel: named["channel"].as_str().unwrap_or(channel(&install)).to_string(),
        install,
        stuck: named["stuck"].as_bool().unwrap_or_else(|| STARTED.elapsed() > NOT_RUNNING_AFTER),
        machine: named["machine"].as_str().unwrap_or(native_machine()).to_string(),
    }
}

async fn download_page(api: &Api) -> String {
    format!("{}/download", resolve_base_url(api).await.unwrap_or_else(|_| DEFAULT_BASE_URL.to_string()).trim_end_matches('/'))
}

/// `_app_update_status` (Windows and macOS): the feed against the running version, and
/// for an installed copy what to update with.
async fn app_status(api: &Api, copy: &Copy) -> Value {
    let mut result = json!({
        "local_version": app_version(),
        "remote_version": null,
        "update_available": false,
        "installed": copy.install["installed"],
        "channel": copy.channel,
        "install_location": copy.install["install_location"],
        "install_scope": copy.install["install_scope"],
        "installer_url": null,
        "installer_sha256": null,
        "release_notes": null,
        "versions": [],
        // The feed offers an update pack: installed copies update in the background.
        "update_pack": false,
        "download_url": download_page(api).await,
        // none | no_release | up_to_date | update_available | paused | error
        "feed_status": "none",
        "error": null,
    });
    // Someone is looking at the button: an answer in about 10 s.
    let fed = feed(api, Duration::from_secs(5), 2).await;
    if !fed.fallback.is_empty() {
        result["fallback"] = fed.fallback.into();
    }
    // An empty feed object is an answer with nothing in it, not a failure.
    let (payload, error) = match fed.payload {
        Ok(payload) => (Some(payload), None),
        Err(None) => (Some(Map::new()), None),
        Err(error) => (None, error),
    };
    match payload.as_ref().filter(|payload| !payload.is_empty()) {
        Some(payload) => {
            let versions = notes::release_versions(payload);
            keep_notes(api, &versions).await;
            result["versions"] = versions.into();
            result["release_notes"] = notes::said(payload, "releaseNotes").or_else(|| notes::said(payload, "notes")).into();
            result["remote_version"] = remote_version(payload).into();
        }
        None => {
            if let Some(kept) = kept_notes(api).await {
                result["versions"] = kept["versions"].clone();
            }
        }
    }
    if copy.channel != "installed" {
        return result;
    }
    let Some(payload) = payload else {
        result["feed_status"] = "error".into();
        result["error"] = error.unwrap_or_else(|| "Empty version-check response".to_string()).into();
        result["net_stage"] = if fed.stage.is_empty() { "other" } else { fed.stage }.into();
        return result;
    };
    let remote = result["remote_version"].as_str().unwrap_or_default().to_string();
    result["feed_status"] = match () {
        _ if paused(&payload) => "paused",
        _ if remote.is_empty() => "no_release",
        _ if newer(app_version(), &remote) => "update_available",
        _ => "up_to_date",
    }
    .into();
    if result["feed_status"] == "update_available" {
        let setup = pick_asset(&payload, "windows", &copy.machine, "installer").or_else(|| pick_asset(&payload, "windows", "AMD64", "installer")).unwrap_or_default();
        // ponytail: the feed's update pack is the x64 app folder; an arm64 copy (or an x64
        // one moving to arm64) takes the full Setup until the release publishes an arm64 pack.
        let x64_only = !cfg!(windows) || (setup.get("arch").is_none_or(|arch| arch == "x64") && cfg!(target_arch = "x86_64"));
        result["update_available"] = true.into();
        result["update_pack"] = (x64_only && feed_update(&payload, app_version()).is_some() && !copy.stuck).into();
        result["installer_url"] = absolute(setup.get("url"), &update_base(api).await.unwrap_or_default());
        result["installer_sha256"] = setup.get("sha256").cloned().unwrap_or(Value::Null);
    }
    result
}

/// `linux_update_status`: the same question for a Linux copy of install kind `kind`,
/// which also says what the update button does. `given` is a feed object already read.
async fn linux_status(api: &Api, kind: &str, given: Option<Fields>) -> Value {
    let action = match kind {
        "appimage" | "installer" | "app" => "restart",
        "deb" | "rpm" | "flatpak" => "install",
        "aur" => "manual",
        _ => "none",
    };
    let failed = api.repos.data_dir().join("versions").join("appimage_swap.failed");
    #[allow(clippy::disallowed_methods)] // inside spawn_blocking
    let last_failure = tokio::task::spawn_blocking(move || std::fs::read_to_string(failed).ok().and_then(|text| serde_json::from_str::<Value>(&text).ok())).await;
    let mut result = json!({
        "local_version": app_version(),
        "remote_version": null,
        "update_available": false,
        "installed": false,
        "channel": if kind == "source" { "portable" } else { "installed" },
        "install_kind": kind,
        "update_action": action,
        "asset": null,
        "installer_url": null,
        "installer_sha256": null,
        "release_notes": null,
        "download_url": download_page(api).await,
        "feed_status": "none",
        "error": null,
        // An AppImage update that did not start and was rolled back.
        "last_failure": last_failure.ok().flatten().filter(Value::is_object),
    });
    if kind == "source" {
        return result;
    }
    if dev_exe() {
        result["channel"] = "dev".into();
        return result;
    }
    let payload = match given {
        Some(payload) if payload.is_empty() => Err(None),
        Some(payload) => Ok(payload),
        None => feed(api, Duration::from_secs(5), 2).await.payload,
    };
    let payload = match payload {
        Ok(payload) => payload,
        Err(error) => {
            result["feed_status"] = "error".into();
            result["error"] = error.unwrap_or_else(|| "Empty version-check response".to_string()).into();
            return result;
        }
    };
    result["release_notes"] = either(payload.get("releaseNotes"), payload.get("notes"));
    if paused(&payload) {
        result["feed_status"] = "paused".into();
        return result;
    }
    let remote = remote_version(&payload).unwrap_or_default();
    result["remote_version"] = remote_version(&payload).into();
    result["feed_status"] = if remote.is_empty() { "no_release" } else { "up_to_date" }.into();
    if !newer(app_version(), &remote) {
        return result;
    }
    if let Some(mut asset) = pick_asset(&payload, os_name(), std::env::consts::ARCH, kind) {
        let base = update_base(api).await.unwrap_or_default();
        for link in ["url", "sig_url"] {
            asset.insert(link.to_string(), absolute(asset.get(link), &base));
        }
        result["installer_url"] = asset["url"].clone();
        result["installer_sha256"] = asset.get("sha256").cloned().unwrap_or(Value::Null);
        result["asset"] = asset.into();
    }
    result["feed_status"] = "update_available".into();
    result["update_available"] = true.into();
    result
}

/// One payload for the page and the updater: the remote version against this one, and
/// whether this copy is installed. Read-only.
async fn update_status(api: &Arc<Api>, named: &Value) -> Value {
    let copy = this_copy(api, named).await;
    let result = match copy.install["install_kind"].as_str() {
        Some(kind) if cfg!(target_os = "linux") => linux_status(api, kind, None).await,
        _ => app_status(api, &copy).await,
    };
    // The session's first check: why a PC stays on an old build (a portable copy, a
    // paused feed, a feed error, an update waiting).
    if !COUNTED.swap(true, Ordering::Relaxed) {
        let props = json!({"status": result["feed_status"], "channel": result["channel"], "pack": truthy(&result["update_pack"])});
        track(api, "update.check", props.as_object().expect("an object")).await;
    }
    result
}

pub async fn get_app_update_status(api: Arc<Api>, _args: Fields) -> Result<Value, String> {
    Ok(update_status(&api, &Value::Null).await)
}

/// Settings → App Info "Test network": DNS, IPv4, IPv6, proxy and HTTPS to the site, and
/// a secure connection to the storage host the update downloads come from.
pub async fn run_network_test(api: Arc<Api>, _args: Fields) -> Result<Value, String> {
    let base = update_base(&api).await.map_err(|refused| refused.message)?;
    let url = Url::parse(&format!("{base}/")).map_err(|error| error.to_string())?;
    Ok(network::diagnose(&url, &format!("{base}/api/v1/files/app-release/latest")).await)
}

/// `usage_stats.update_check_done`: one check ended. A check that works after a failed
/// one this session is counted, once per UTC day per PC (one failure after sleep is no
/// stuck PC).
async fn check_done(api: &Api, ok: bool) {
    CHECKS.fetch_add(1, Ordering::Relaxed);
    if !ok {
        CHECK_FAILED.store(true, Ordering::Relaxed);
        return;
    }
    let (true, Ok(store)) = (CHECK_FAILED.swap(false, Ordering::Relaxed), api.repos.store().await) else { return };
    let today = network::utc_day(now());
    if kv::meta_get(store, META_RECOVERED.to_string()).await.ok().flatten().as_deref() != Some(today.as_str()) {
        let _ = kv::meta_set(store, META_RECOVERED.to_string(), today).await;
        track(api, "update.check_recovered", json!({"v": app_version()}).as_object().expect("an object")).await;
    }
}

/// `usage_stats.net_fail`: the app couldn't reach the site (`code`: what it tried). Once
/// per UTC day per PC per code and stage: the day is kept in ducky.db, which every
/// process on the PC shares. `status` is the site's answer when it did answer.
pub async fn net_fail(api: &Api, code: &str, stage: &str, status: u16) {
    {
        let mut counted = NET_FAILS.lock().unwrap_or_else(|e| e.into_inner());
        let this = (code.to_string(), stage.to_string());
        if counted.contains(&this) {
            return;
        }
        counted.push(this);
    }
    let Ok(store) = api.repos.store().await else { return };
    let (key, today) = (format!("usage.net_fail.{}.{stage}", code_of(code)), network::utc_day(now()));
    if kv::meta_get(store, key.clone()).await.ok().flatten().as_deref() == Some(today.as_str()) {
        return;
    }
    let _ = kv::meta_set(store, key, today).await;
    let kind = match (stage, status) {
        ("timeout", _) => "timeout",
        ("dns" | "connect" | "proxy", _) => "offline",
        (_, 429) => "rate_limit",
        (_, 500..=599) => "server_error",
        _ => "other",
    };
    let mode = tokio::task::spawn_blocking(super::site::proxy::mode).await.unwrap_or("none");
    let mut props = json!({"code": code_of(code), "where": "store", "kind": kind, "version": app_version(), "net_stage": stage, "proxy": mode});
    if stage == "http" && (100..=599).contains(&status) {
        props["reason"] = format!("http_{status}").into();
    }
    if code == "update_check" {
        // The session's first check, or a recheck after one that ended.
        props["attempt"] = if CHECKS.load(Ordering::Relaxed) == 0 { "first" } else { "later" }.into();
    }
    track(api, "error.code", props.as_object().expect("an object")).await;
}

/// The check of `check_and_stage`: ask the feed, and answer its update pack when it is
/// for a newer version.
pub async fn check(api: &Api) -> Option<Fields> {
    set("checking", None, String::new());
    let fed = feed(api, Duration::from_secs(8), 5).await.payload;
    check_done(api, !matches!(fed, Err(Some(_)))).await;
    let payload = match fed {
        Ok(payload) if !paused(&payload) => payload,
        Ok(_) | Err(None) => Map::new(),
        Err(Some(error)) => {
            set("idle", None, error);
            return None;
        }
    };
    let update = feed_update(&payload, app_version()).cloned();
    set("idle", update.as_ref().map(|update| stripped(update.get("version"))), String::new());
    update
}

/// The parity tests' way in (`/__rs/update/<what>`): a check now, and the status of a
/// copy the test describes (installed, on some CPU, of some Linux install kind).
pub async fn probe(api: &Arc<Api>, what: &str, body: &Value) -> Value {
    match what {
        "check" => json!({"update": check(api).await, "status": status()}),
        "linux" => linux_status(api, body["kind"].as_str().unwrap_or_default(), body["payload"].as_object().cloned()).await,
        _ => update_status(api, body).await,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fields(value: Value) -> Fields {
        value.as_object().cloned().unwrap()
    }

    #[test]
    fn a_start_from_a_switch_says_updated_for_ten_minutes() {
        let state = |phase| State { phase, version: "9.9.9".to_string(), error: String::new() };
        let plain = json!({"phase": "idle", "version": "9.9.9", "done": 0, "total": 0, "error": "", "failures": 0});
        assert_eq!(shown(&state("idle"), "", Duration::ZERO), plain);
        assert_eq!(shown(&state("idle"), "1.0.0", UPDATED_NOTE), plain);
        let updated = shown(&state("off"), "1.0.0", Duration::from_secs(5));
        assert_eq!((&updated["phase"], &updated["version"], &updated["previous"]), (&json!("updated"), &json!(app_version()), &json!("1.0.0")));
    }

    #[test]
    fn the_feed_names_a_pack_only_for_a_newer_version_with_all_its_links() {
        let pack = |version: &str| json!({"update": {"version": version, "packUrl": "p", "manifestUrl": "m", "manifestSha256": "s"}});
        assert!(feed_update(&fields(pack(" 2.0.0 ")), "1.9.9").is_some());
        assert!(feed_update(&fields(pack("1.9.9")), "1.9.9").is_none() && feed_update(&fields(pack("next")), "1.9.9").is_none() && feed_update(&fields(pack("2.0.0")), "dev").is_none());
        assert!(feed_update(&fields(json!({"update": {"version": "2.0.0", "packUrl": "p", "manifestUrl": " ", "manifestSha256": "s"}})), "1.0.0").is_none());
        assert!(feed_update(&fields(json!({"update": "2.0.0"})), "1.0.0").is_none());
        for (value, is) in [(json!(true), true), (json!(" TRUE "), true), (json!(1), true), (json!("1"), true), (json!(false), false), (json!("yes"), false), (json!(null), false), (json!(1.0), false)] {
            assert_eq!(paused(&fields(json!({"downloadsPaused": value}))), is, "{value}");
        }
    }

    #[test]
    fn an_asset_is_picked_by_os_cpu_and_kind_and_a_link_is_made_whole() {
        let feed = fields(json!({
            "assets": [{"os": "windows", "arch": "arm64", "package": "setup", "url": "/arm.exe"}, {"os": "linux", "arch": "x64", "package": "appimage", "url": "a.AppImage"}, "not a row"],
            "installer_url": "old.exe",
            "installerSha256": "",
            "sha256": "abc",
        }));
        assert_eq!(pick_asset(&feed, "windows", "ARM64", "installer").unwrap()["url"], "/arm.exe");
        assert_eq!(pick_asset(&feed, "linux", "x86_64", "appimage").unwrap()["url"], "a.AppImage");
        let old = pick_asset(&feed, "windows", "AMD64", "installer").unwrap();
        assert_eq!((&old["url"], &old["sha256"], &old["arch"]), (&json!("old.exe"), &json!("abc"), &json!("x64")));
        assert!(pick_asset(&feed, "linux", "aarch64", "appimage").is_none() && pick_asset(&feed, "windows", "AMD64", "portable").is_none());
        assert!(pick_asset(&fields(json!({"assets": "none"})), "windows", "AMD64", "installer").is_none());

        let base = "http://127.0.0.1:9/";
        assert_eq!(absolute(Some(&json!(" files/setup.exe ")), base), "http://127.0.0.1:9/files/setup.exe");
        assert_eq!(absolute(Some(&json!("//files/x")), base), "http://127.0.0.1:9/files/x");
        assert_eq!(absolute(Some(&json!("HTTPS://cdn.example/x")), base), "HTTPS://cdn.example/x");
        assert!(absolute(Some(&json!("  ")), base).is_null() && absolute(None, base).is_null() && absolute(Some(&json!("x")), "").is_null());
    }
}
