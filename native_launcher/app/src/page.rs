//! The page side of the bridge, free of Win32: page `postMessage` calls in, backend lines
//! in, and the actions they cause out. The start-up cache (`shim::cache`) answers the
//! page's first read-only getters before hello and refreshes them live afterwards.

use serde_json::{json, Value};
use shim::cache::{Intercept, StartupCache};
use shim::{NativeOp, Route, ShimError};
use supervisor::protocol::{BackendMsg, Hello};

/// What one input asks the launcher to do, in order.
#[derive(Debug, Clone, PartialEq)]
pub enum Action {
    /// `PostWebMessageAsJson` this to the page.
    Page(String),
    /// A contract `call` to the backend (queued until hello by the supervisor thread).
    Backend { id: String, name: String, args: Vec<Value> },
    /// A window call the launcher answers itself, then `Page(result_message(id, ..))`.
    Native { id: String, op: NativeOp },
}

pub struct Bridge {
    cache: Option<StartupCache>,
}

impl Bridge {
    /// `cache`: the start-up cache for this app version, or None (every call waits).
    pub fn new(cache: Option<StartupCache>) -> Bridge {
        Bridge { cache }
    }

    /// One `postMessage` payload from `shim.js`.
    pub fn on_page_message(&mut self, text: &str) -> Vec<Action> {
        let call = match shim::parse_page_message(text) {
            Ok(c) => c,
            Err(_) => return Vec::new(),
        };
        if let Some(cache) = self.cache.as_mut() {
            if let Intercept::Answered { page_msg, refresh_line } = cache.intercept(&call) {
                let mut out = vec![Action::Page(page_msg)];
                out.extend(call_from_line(&refresh_line));
                return out;
            }
        }
        match shim::route(call.clone()) {
            Ok(Route::Native { id, op }) => vec![Action::Native { id, op }],
            Ok(Route::Backend { .. }) => vec![Action::Backend { id: call.id, name: call.name, args: call.args }],
            Err(ShimError::BadArgs { id, error }) => vec![Action::Page(shim::result_message(&id, Err(error)))],
            Err(ShimError::BadMessage(_)) => Vec::new(),
        }
    }

    /// The backend said hello (first start or after a restart): no more cached answers.
    pub fn on_hello(&mut self, hello: &Hello) {
        if let Some(cache) = self.cache.as_mut() {
            let line = json!({"t": "hello", "protocol": hello.protocol, "app_version": hello.app_version});
            cache.on_backend_line(&line.to_string());
        }
    }

    /// One backend result: the page's own answer, and the `startup-refresh` event once
    /// the cache's refresh calls are all back.
    pub fn on_result(&mut self, id: &str, result: &Result<Value, String>) -> Vec<Action> {
        let mut out = Vec::new();
        if id.starts_with(shim::PAGE_ID_PREFIX) {
            out.push(Action::Page(shim::result_message(id, result.clone())));
        }
        if let Some(cache) = self.cache.as_mut() {
            let line = match result {
                Ok(v) => json!({"t": "result", "id": id, "ok": true, "value": v}),
                Err(e) => json!({"t": "result", "id": id, "ok": false, "error": e}),
            };
            if let Some(event) = cache.on_backend_line(&line.to_string()) {
                out.push(Action::Page(event));
            }
        }
        out
    }

    /// Any backend message (results go through [`Self::on_result`]).
    pub fn on_backend(&mut self, msg: &BackendMsg) -> Vec<Action> {
        match msg {
            BackendMsg::Result { id, result } => self.on_result(id, result),
            BackendMsg::Hello(h) => {
                self.on_hello(h);
                Vec::new()
            }
            _ => Vec::new(),
        }
    }
}

/// The cache's refresh `call` line as a backend action.
fn call_from_line(line: &str) -> Option<Action> {
    let v: Value = serde_json::from_str(line).ok()?;
    Some(Action::Backend {
        id: v.get("id")?.as_str()?.to_string(),
        name: v.get("name")?.as_str()?.to_string(),
        args: v.get("args").and_then(Value::as_array).cloned().unwrap_or_default(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const VERSION: &str = "1.2.472";

    fn cache_file(name: &str, results: Value) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("ducky-page-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("startup_cache.json");
        std::fs::write(&path, json!({"app_version": VERSION, "results": results}).to_string()).unwrap();
        path
    }

    fn call(id: &str, name: &str) -> String {
        json!({"t": "call", "id": id, "name": name, "args": []}).to_string()
    }

    fn page_value(a: &Action) -> Value {
        match a {
            Action::Page(s) => serde_json::from_str(s).unwrap(),
            other => panic!("{other:?}"),
        }
    }

    fn hello() -> Hello {
        Hello {
            protocol: 1,
            app_version: VERSION.into(),
            port: 50001,
            origin: "http://127.0.0.1:50001".into(),
            profile_dir: String::new(),
            pid: 9,
            handoff: false,
        }
    }

    #[test]
    fn cached_getter_answered_before_hello_non_cacheable_waits() {
        let path = cache_file("answer", json!({"get_appearance[]": {"theme": "dark"}}));
        let mut b = Bridge::new(Some(StartupCache::load(&path, VERSION)));
        let out = b.on_page_message(&call("js-1", "get_appearance"));
        assert_eq!(page_value(&out[0]), json!({"t": "result", "id": "js-1", "ok": true, "value": {"theme": "dark"}}));
        // The same getter goes to the backend under a cache- id, to refresh it live.
        assert!(matches!(&out[1], Action::Backend { id, name, .. } if id.starts_with("cache-") && name == "get_appearance"));
        // Not cacheable (settings): it goes to the backend and waits for hello.
        let out = b.on_page_message(&call("js-2", "get_settings"));
        assert_eq!(out, [Action::Backend { id: "js-2".into(), name: "get_settings".into(), args: vec![] }]);
        // A cacheable name with no cached value waits too.
        let out = b.on_page_message(&call("js-3", "list_workflows"));
        assert!(matches!(&out[..], [Action::Backend { id, .. }] if id == "js-3"));
        let _ = std::fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn refresh_event_reaches_the_page_after_hello() {
        let path = cache_file("refresh", json!({"get_appearance[]": {"theme": "dark"}}));
        let mut b = Bridge::new(Some(StartupCache::load(&path, VERSION)));
        let out = b.on_page_message(&call("js-1", "get_appearance"));
        let Action::Backend { id: refresh_id, .. } = out[1].clone() else { panic!() };
        b.on_hello(&hello());
        // After hello the cache answers nothing.
        let out = b.on_page_message(&call("js-2", "get_appearance"));
        assert!(matches!(&out[..], [Action::Backend { id, .. }] if id == "js-2"));
        // The refresh result changed: the page gets no result for the cache- id, only the event.
        let out = b.on_backend(&BackendMsg::Result { id: refresh_id, result: Ok(json!({"theme": "light"})) });
        assert_eq!(out.len(), 1);
        let ev = page_value(&out[0]);
        assert_eq!(ev["t"], "event");
        assert_eq!(ev["name"], "startup-refresh");
        assert_eq!(ev["data"]["stale"], json!(["get_appearance"]));
        // A page result is still forwarded as usual.
        let out = b.on_result("js-2", &Ok(json!({"theme": "light"})));
        assert_eq!(page_value(&out[0])["id"], "js-2");
        let _ = std::fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn no_cache_and_window_calls_route_as_before() {
        let mut b = Bridge::new(None);
        let out = b.on_page_message(&call("js-1", "get_appearance"));
        assert!(matches!(&out[..], [Action::Backend { id, .. }] if id == "js-1"));
        let out = b.on_page_message(&call("js-2", "is_window_maximized"));
        assert_eq!(out, [Action::Native { id: "js-2".into(), op: NativeOp::IsMaximized }]);
        let bad = json!({"t": "call", "id": "js-3", "name": "set_window_bounds", "args": ["x"]}).to_string();
        assert_eq!(page_value(&b.on_page_message(&bad)[0])["ok"], false);
        assert!(b.on_page_message("not json").is_empty());
        // Results for launcher-only ids never reach the page.
        assert!(b.on_result("cache-7", &Ok(Value::Null)).is_empty());
    }
}
