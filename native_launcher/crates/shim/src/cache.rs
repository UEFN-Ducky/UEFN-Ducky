//! Start-up cache: the page's first read-only calls answered from the last good results
//! while the backend has not said hello yet, then refreshed live.
//!
//! The launcher feeds every page call to [`StartupCache::intercept`] and every backend
//! stdout line to [`StartupCache::on_backend_line`]:
//! - before hello, a cached getter is answered at once (`Intercept::Answered`) and the
//!   same call goes to the backend under a `cache-` id, so the page never sees its result;
//! - once every refresh call is back, the file is rewritten from the live results and, when
//!   any value changed, a `startup-refresh` event goes to the page (shim.js reloads the page
//!   once unless a listener calls `preventDefault`; after hello nothing comes from the cache).
//!
//! Only pure getters are in [`CACHEABLE`]; a missing, broken or other-version file means
//! no cache, so calls wait for Python exactly as without it.

use std::collections::{BTreeMap, HashMap};
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use crate::{event_message, result_message, PageCall};

/// Ids of the launcher's refresh calls; `page_message_from_backend` ignores them.
pub const REFRESH_ID_PREFIX: &str = "cache-";

/// Read-only js_api getters (names from `frontend/native_host/inventory.py`) the page
/// makes before it paints. Never anything that writes, consumes or acts.
// ponytail: fixed list; widen it from a measured start-up call trace when one exists.
pub const CACHEABLE: &[&str] = &[
    "get_settings",
    "get_appearance",
    "get_version",
    "get_header_visibility",
    "get_workspace_dock",
    "get_editor_workspace",
    "get_restore_tabs",
    "get_models",
    "get_models_catalog",
    "get_key_status",
    "has_any_api_key",
    "list_conversations",
    "list_all_conversations",
    "list_folders",
    "list_agent_profiles",
    "list_ducky_catalog",
    "list_recent_projects",
    "list_known_projects",
    "list_uefn_plugins",
    "list_workflows",
];

pub fn is_cacheable(name: &str) -> bool {
    CACHEABLE.contains(&name)
}

/// `%LOCALAPPDATA%/UEFN-Ducky/launcher/startup_cache.json`.
pub fn default_path() -> Option<PathBuf> {
    let base = std::env::var_os("LOCALAPPDATA")?;
    Some(PathBuf::from(base).join("UEFN-Ducky").join("launcher").join("startup_cache.json"))
}

fn key(name: &str, args: &[Value]) -> String {
    // serde_json's Map is a BTreeMap, so equal args give equal text.
    format!("{name}{}", Value::Array(args.to_vec()))
}

/// What to do with one page call.
#[derive(Debug, Clone, PartialEq)]
pub enum Intercept {
    /// Post `page_msg` to the page now and write `refresh_line` to the backend's stdin.
    Answered { page_msg: String, refresh_line: String },
    /// Route it as usual (`crate::route`).
    Pass,
}

#[derive(Debug)]
pub struct StartupCache {
    path: PathBuf,
    app_version: String,
    cached: BTreeMap<String, Value>,
    live: BTreeMap<String, Value>,
    /// Backend call id -> cache key, for calls whose live result we want.
    pending: HashMap<String, String>,
    stale: Vec<String>,
    hello: bool,
    seq: u64,
}

impl StartupCache {
    /// Load the cache for `app_version` (the launcher's own version). Missing, broken or
    /// other-version files load empty.
    pub fn load(path: impl Into<PathBuf>, app_version: &str) -> Self {
        let path = path.into();
        let cached = std::fs::read(&path)
            .ok()
            .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
            .filter(|v| v.get("app_version").and_then(Value::as_str) == Some(app_version))
            .and_then(|v| v.get("results").and_then(Value::as_object).cloned())
            .map(|m| m.into_iter().collect())
            .unwrap_or_default();
        Self {
            path,
            app_version: app_version.to_string(),
            cached,
            live: BTreeMap::new(),
            pending: HashMap::new(),
            stale: Vec::new(),
            hello: false,
            seq: 0,
        }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn hello_seen(&self) -> bool {
        self.hello
    }

    /// Call before routing a page call (window calls simply `Pass`).
    pub fn intercept(&mut self, call: &PageCall) -> Intercept {
        if !is_cacheable(&call.name) {
            return Intercept::Pass;
        }
        let k = key(&call.name, &call.args);
        if !self.hello {
            if let Some(value) = self.cached.get(&k) {
                self.seq += 1;
                let id = format!("{REFRESH_ID_PREFIX}{}", self.seq);
                let refresh_line = json!({"t": "call", "id": id, "name": call.name, "args": call.args}).to_string();
                self.pending.insert(id, k);
                return Intercept::Answered { page_msg: result_message(&call.id, Ok(value.clone())), refresh_line };
            }
        }
        // Not cached (or hello came): the page waits for the live answer; keep it for next start.
        self.pending.insert(call.id.clone(), k);
        Intercept::Pass
    }

    /// Feed every backend stdout line. Returns a page message to post (the
    /// `startup-refresh` event) once all refresh calls are back and something changed.
    pub fn on_backend_line(&mut self, line: &str) -> Option<String> {
        let v: Value = serde_json::from_str(line.trim()).ok()?;
        match v.get("t").and_then(Value::as_str)? {
            "hello" => {
                self.hello = true;
                if let Some(ver) = v.get("app_version").and_then(Value::as_str) {
                    self.app_version = ver.to_string();
                }
                None
            }
            "result" => {
                let id = v.get("id").and_then(Value::as_str)?;
                let k = self.pending.remove(id)?;
                if v.get("ok").and_then(Value::as_bool) == Some(true) {
                    let value = v.get("value").cloned().unwrap_or(Value::Null);
                    if id.starts_with(REFRESH_ID_PREFIX) && self.cached.get(&k) != Some(&value) {
                        let name = k.split('[').next().unwrap_or(&k).to_string();
                        if !self.stale.contains(&name) {
                            self.stale.push(name);
                        }
                    }
                    self.live.insert(k, value);
                }
                if self.pending.keys().any(|p| p.starts_with(REFRESH_ID_PREFIX)) {
                    return None;
                }
                let _ = self.save();
                if self.stale.is_empty() || !id.starts_with(REFRESH_ID_PREFIX) {
                    return None;
                }
                let stale = std::mem::take(&mut self.stale);
                Some(event_message("startup-refresh", &json!({ "stale": stale })))
            }
            _ => None,
        }
    }

    /// Write the cached results merged with the live ones (atomic replace).
    pub fn save(&self) -> std::io::Result<()> {
        if self.live.is_empty() {
            return Ok(());
        }
        let mut results = self.cached.clone();
        results.extend(self.live.iter().map(|(k, v)| (k.clone(), v.clone())));
        let text = json!({"app_version": self.app_version, "results": results}).to_string();
        if let Some(dir) = self.path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let tmp = self.path.with_extension("json.tmp");
        std::fs::write(&tmp, text)?;
        std::fs::rename(&tmp, &self.path)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("ducky-shim-cache-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir.join("startup_cache.json")
    }

    fn call(id: &str, name: &str) -> PageCall {
        PageCall { id: id.into(), name: name.into(), args: vec![] }
    }

    fn write(path: &Path, version: &str, results: Value) {
        std::fs::write(path, json!({"app_version": version, "results": results}).to_string()).unwrap();
    }

    #[test]
    fn cached_getter_answers_before_hello_then_refreshes() {
        let p = tmp("hit");
        write(&p, "1.2.9", json!({"get_settings[]": {"theme": "dark"}}));
        let mut c = StartupCache::load(&p, "1.2.9");
        let Intercept::Answered { page_msg, refresh_line } = c.intercept(&call("js-1", "get_settings")) else {
            panic!("not answered from cache")
        };
        let msg: Value = serde_json::from_str(&page_msg).unwrap();
        assert_eq!(msg, json!({"t":"result","id":"js-1","ok":true,"value":{"theme":"dark"}}));
        let line: Value = serde_json::from_str(&refresh_line).unwrap();
        assert_eq!(line["name"], "get_settings");
        let rid = line["id"].as_str().unwrap().to_string();
        assert!(rid.starts_with(REFRESH_ID_PREFIX));
        assert!(crate::page_message_from_backend(&format!(r#"{{"t":"result","id":"{rid}","ok":true}}"#)).is_none());

        assert!(c.on_backend_line(r#"{"t":"hello","protocol":1,"app_version":"1.2.9"}"#).is_none());
        let ev = c
            .on_backend_line(&json!({"t":"result","id":rid,"ok":true,"value":{"theme":"light"}}).to_string())
            .expect("stale value -> refresh event");
        let ev: Value = serde_json::from_str(&ev).unwrap();
        assert_eq!(ev, json!({"t":"event","name":"startup-refresh","data":{"stale":["get_settings"]}}));
        // Saved from the live result; after hello nothing comes from the cache.
        let again = StartupCache::load(&p, "1.2.9");
        assert_eq!(again.cached["get_settings[]"], json!({"theme":"light"}));
        assert_eq!(c.intercept(&call("js-2", "get_settings")), Intercept::Pass);
    }

    #[test]
    fn unchanged_refresh_sends_no_event() {
        let p = tmp("same");
        write(&p, "1", json!({"get_version[]": "1"}));
        let mut c = StartupCache::load(&p, "1");
        let Intercept::Answered { refresh_line, .. } = c.intercept(&call("js-1", "get_version")) else { panic!() };
        let rid = serde_json::from_str::<Value>(&refresh_line).unwrap()["id"].as_str().unwrap().to_string();
        assert!(c.on_backend_line(&json!({"t":"result","id":rid,"ok":true,"value":"1"}).to_string()).is_none());
    }

    #[test]
    fn write_calls_never_come_from_the_cache() {
        let p = tmp("write");
        write(&p, "1", json!({"save_settings[]": true, "send_message[]": true, "exit_all[]": true}));
        let mut c = StartupCache::load(&p, "1");
        for name in ["save_settings", "send_message", "exit_all"] {
            assert_eq!(c.intercept(&call("js-1", name)), Intercept::Pass, "{name}");
        }
        // Their live results are not kept either.
        assert!(c.on_backend_line(r#"{"t":"result","id":"js-1","ok":true,"value":1}"#).is_none());
        assert!(c.live.is_empty());
    }

    #[test]
    fn version_change_ignores_the_cache() {
        let p = tmp("ver");
        write(&p, "1.2.8", json!({"get_settings[]": {"a": 1}}));
        let mut c = StartupCache::load(&p, "1.2.9");
        assert_eq!(c.intercept(&call("js-1", "get_settings")), Intercept::Pass);
        // The live answer is written under the new version.
        c.on_backend_line(r#"{"t":"hello","protocol":1,"app_version":"1.2.9"}"#);
        assert!(c.on_backend_line(r#"{"t":"result","id":"js-1","ok":true,"value":{"a":2}}"#).is_none());
        assert_eq!(StartupCache::load(&p, "1.2.9").cached["get_settings[]"], json!({"a":2}));
    }

    #[test]
    fn broken_or_missing_file_waits_for_python() {
        let p = tmp("broken");
        std::fs::write(&p, "{not json").unwrap();
        let mut c = StartupCache::load(&p, "1");
        assert_eq!(c.intercept(&call("js-1", "get_settings")), Intercept::Pass);
        let mut m = StartupCache::load(p.with_file_name("missing.json"), "1");
        assert_eq!(m.intercept(&call("js-1", "get_settings")), Intercept::Pass);
    }

    #[test]
    fn args_are_part_of_the_key() {
        let p = tmp("args");
        write(&p, "1", json!({"list_conversations[\"a\"]": [1]}));
        let mut c = StartupCache::load(&p, "1");
        let mut other = call("js-1", "list_conversations");
        other.args = vec![json!("b")];
        assert_eq!(c.intercept(&other), Intercept::Pass);
        other.args = vec![json!("a")];
        assert!(matches!(c.intercept(&other), Intercept::Answered { .. }));
    }

    #[test]
    fn only_getters_are_cacheable() {
        for n in CACHEABLE {
            assert!(n.starts_with("get_") || n.starts_with("list_") || n.starts_with("has_"), "{n}");
        }
    }
}
