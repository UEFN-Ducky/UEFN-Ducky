//! `window.pywebview.api` inside the launcher's WebView2.
//!
//! The page keeps calling `window.pywebview.api.<name>(...)` exactly as it does under
//! pywebview. [`SHIM_JS`] (injected before page scripts) turns each call into a
//! `chrome.webview.postMessage` of `{"t":"call","id","name","args"}`. The launcher hands
//! that text to [`route_message`]:
//! - a window name ([`WINDOW_NAMES`]) is answered by the launcher itself ([`NativeOp`]);
//! - any other name becomes a backend contract `call` (one JSON line on the backend's stdin).
//!
//! Answers go back to the page with `PostWebMessageAsJson` of [`result_message`]; the
//! backend's own `result` lines are forwarded with [`page_message_from_backend`].

use serde_json::{json, Map, Value};

pub mod cache;

/// Injected with `AddScriptToExecuteOnDocumentCreated`.
pub const SHIM_JS: &str = include_str!("shim.js");

/// Call ids minted by `shim.js`. Backend results with this prefix belong to the page.
pub const PAGE_ID_PREFIX: &str = "js-";

/// js_api names (from `frontend/ui_web/panel_api_window.py`) that act on the native window,
/// so the launcher answers them without a round trip to the backend.
pub const WINDOW_NAMES: &[&str] = &[
    "get_window_bounds",
    "set_window_bounds",
    "uses_native_window_chrome",
    "begin_native_window_move",
    "begin_native_window_resize",
    "is_window_maximized",
    "toggle_maximize",
    "minimize_window",
    "hide_window",
];

/// A window call the launcher runs itself.
#[derive(Debug, Clone, PartialEq)]
pub enum NativeOp {
    GetWindowBounds,
    SetWindowBounds { x: i32, y: i32, width: i32, height: i32 },
    UsesNativeWindowChrome,
    BeginMove,
    BeginResize { edge: String, double_click: bool },
    /// The caption's system menu (right-click on the page's title bar).
    ShowMenu,
    IsMaximized,
    ToggleMaximize,
    Minimize,
    Hide,
}

/// A page call, as posted by `shim.js`.
#[derive(Debug, Clone, PartialEq)]
pub struct PageCall {
    pub id: String,
    pub name: String,
    pub args: Vec<Value>,
}

/// Where a page call goes.
#[derive(Debug, Clone, PartialEq)]
pub enum Route {
    /// Answer locally, then post `result_message(id, ..)` back to the page.
    Native { id: String, op: NativeOp },
    /// Write `line` (one JSON object, no newline) to the backend's stdin.
    Backend { id: String, line: String },
}

#[derive(Debug, Clone, PartialEq)]
pub enum ShimError {
    /// Not JSON, or not a `{"t":"call",...}` object with a string id and name.
    BadMessage(String),
    /// A window call whose args do not fit; `id` is known so the page can be rejected.
    BadArgs { id: String, error: String },
}

pub fn is_window_name(name: &str) -> bool {
    WINDOW_NAMES.contains(&name)
}

/// Parse one `postMessage` payload from `shim.js`.
pub fn parse_page_message(text: &str) -> Result<PageCall, ShimError> {
    let v: Value = serde_json::from_str(text).map_err(|e| ShimError::BadMessage(e.to_string()))?;
    // WebView2 may hand over a JSON string that itself holds the object.
    let v = match v {
        Value::String(inner) => serde_json::from_str(&inner).map_err(|e| ShimError::BadMessage(e.to_string()))?,
        other => other,
    };
    let obj = v.as_object().ok_or_else(|| ShimError::BadMessage("not an object".into()))?;
    if obj.get("t").and_then(Value::as_str) != Some("call") {
        return Err(ShimError::BadMessage("t is not \"call\"".into()));
    }
    let id = obj.get("id").and_then(Value::as_str).filter(|s| !s.is_empty());
    let name = obj.get("name").and_then(Value::as_str).filter(|s| !s.is_empty());
    let (Some(id), Some(name)) = (id, name) else {
        return Err(ShimError::BadMessage("id and name must be non-empty strings".into()));
    };
    let args = match obj.get("args") {
        None | Some(Value::Null) => Vec::new(),
        Some(Value::Array(a)) => a.clone(),
        Some(_) => return Err(ShimError::BadMessage("args must be an array".into())),
    };
    Ok(PageCall { id: id.to_string(), name: name.to_string(), args })
}

fn arg_i32(args: &[Value], i: usize, what: &str) -> Result<i32, String> {
    let v = args.get(i).ok_or_else(|| format!("missing {what}"))?;
    v.as_f64()
        .filter(|f| f.is_finite())
        .map(|f| f.round().clamp(i32::MIN as f64, i32::MAX as f64) as i32)
        .ok_or_else(|| format!("{what} must be a number"))
}

/// The native op for a window name, or `None` when the name is not a window name.
pub fn native_op(name: &str, args: &[Value]) -> Option<Result<NativeOp, String>> {
    let op = match name {
        "get_window_bounds" => Ok(NativeOp::GetWindowBounds),
        "set_window_bounds" => (|| {
            Ok(NativeOp::SetWindowBounds {
                x: arg_i32(args, 0, "x")?,
                y: arg_i32(args, 1, "y")?,
                width: arg_i32(args, 2, "width")?,
                height: arg_i32(args, 3, "height")?,
            })
        })(),
        "uses_native_window_chrome" => Ok(NativeOp::UsesNativeWindowChrome),
        "begin_native_window_move" => Ok(NativeOp::BeginMove),
        "begin_native_window_resize" => match args.first().and_then(Value::as_str) {
            Some(edge) => Ok(NativeOp::BeginResize {
                edge: edge.to_string(),
                double_click: args.get(1).and_then(Value::as_bool) == Some(true),
            }),
            None => Err("edge must be a string".to_string()),
        },
        // pywebview's sync bridge only (`window.pywebview._jsApiCallback`), not a js_api method.
        "uefnNativeWindowMenu" => Ok(NativeOp::ShowMenu),
        "is_window_maximized" => Ok(NativeOp::IsMaximized),
        "toggle_maximize" => Ok(NativeOp::ToggleMaximize),
        "minimize_window" => Ok(NativeOp::Minimize),
        "hide_window" => Ok(NativeOp::Hide),
        _ => return None,
    };
    Some(op)
}

/// The contract `call` line for the backend (no trailing newline).
pub fn backend_call_line(call: &PageCall) -> String {
    json!({"t": "call", "id": call.id, "name": call.name, "args": call.args}).to_string()
}

/// Route one page call.
pub fn route(call: PageCall) -> Result<Route, ShimError> {
    match native_op(&call.name, &call.args) {
        Some(Ok(op)) => Ok(Route::Native { id: call.id, op }),
        Some(Err(error)) => Err(ShimError::BadArgs { id: call.id, error }),
        None => {
            let line = backend_call_line(&call);
            Ok(Route::Backend { id: call.id, line })
        }
    }
}

/// Parse and route one `postMessage` payload.
pub fn route_message(text: &str) -> Result<Route, ShimError> {
    route(parse_page_message(text)?)
}

/// Every window's shim mints the same `js-N` ids, so a pop-out window's call goes on
/// under `js-@<wid>@<id>` (still a page id) and its answer comes back to that window.
pub fn tag_id(wid: &str, id: &str) -> String {
    format!("{PAGE_ID_PREFIX}@{wid}@{id}")
}

/// `(Some(wid), id)` for a [`tag_id`] id, else `(None, id)` (the main window's own).
pub fn untag_id(id: &str) -> (Option<&str>, &str) {
    id.strip_prefix(PAGE_ID_PREFIX)
        .and_then(|rest| rest.strip_prefix('@'))
        .and_then(|rest| rest.split_once('@'))
        .map_or((None, id), |(wid, orig)| (Some(wid), orig))
}

/// The JSON posted back to the page for a finished call (`PostWebMessageAsJson`).
pub fn result_message(id: &str, outcome: Result<Value, String>) -> String {
    match outcome {
        Ok(value) => json!({"t": "result", "id": id, "ok": true, "value": value}).to_string(),
        Err(error) => json!({"t": "result", "id": id, "ok": false, "error": error}).to_string(),
    }
}

/// A backend stdout line that answers a page call, re-shaped for the page.
/// `None` for anything else (hello, events, results for the launcher's own calls).
pub fn page_message_from_backend(line: &str) -> Option<String> {
    let v: Value = serde_json::from_str(line.trim()).ok()?;
    let obj = v.as_object()?;
    if obj.get("t").and_then(Value::as_str) != Some("result") {
        return None;
    }
    let id = obj.get("id").and_then(Value::as_str)?;
    if !id.starts_with(PAGE_ID_PREFIX) {
        return None;
    }
    let ok = obj.get("ok").and_then(Value::as_bool).unwrap_or(false);
    let outcome = if ok {
        Ok(obj.get("value").cloned().unwrap_or(Value::Null))
    } else {
        Err(error_text(obj))
    };
    Some(result_message(id, outcome))
}

fn error_text(obj: &Map<String, Value>) -> String {
    match obj.get("error") {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Null) | None => "call failed".to_string(),
        Some(other) => other.to_string(),
    }
}

/// The page message that turns `window.pywebview.api` into a fixed name list
/// (unknown names read as `undefined`, like pywebview).
pub fn names_message(names: &[&str]) -> String {
    json!({"t": "names", "names": names}).to_string()
}

/// A backend event forwarded to the page as a `ducky-native-<name>` DOM event.
pub fn event_message(name: &str, data: &Value) -> String {
    json!({"t": "event", "name": name, "data": data}).to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn window_names_route_native() {
        let r = route_message(r#"{"t":"call","id":"js-1","name":"toggle_maximize","args":[]}"#).unwrap();
        assert_eq!(r, Route::Native { id: "js-1".into(), op: NativeOp::ToggleMaximize });
    }

    #[test]
    fn other_names_become_contract_calls() {
        let r = route_message(r#"{"t":"call","id":"js-2","name":"get_version","args":[1,{"a":"b"}]}"#).unwrap();
        let Route::Backend { id, line } = r else { panic!("not backend") };
        assert_eq!(id, "js-2");
        let v: Value = serde_json::from_str(&line).unwrap();
        assert_eq!(v, json!({"t":"call","id":"js-2","name":"get_version","args":[1,{"a":"b"}]}));
        assert!(!line.contains('\n'));
    }

    #[test]
    fn set_bounds_args_and_errors() {
        let r = route_message(r#"{"t":"call","id":"js-3","name":"set_window_bounds","args":[10,20.6,900,640]}"#).unwrap();
        assert_eq!(
            r,
            Route::Native { id: "js-3".into(), op: NativeOp::SetWindowBounds { x: 10, y: 21, width: 900, height: 640 } }
        );
        let e = route_message(r#"{"t":"call","id":"js-4","name":"set_window_bounds","args":[1]}"#).unwrap_err();
        assert!(matches!(e, ShimError::BadArgs { ref id, .. } if id == "js-4"));
    }

    #[test]
    fn resize_edge() {
        let r = route_message(r#"{"t":"call","id":"js-5","name":"begin_native_window_resize","args":["se"]}"#).unwrap();
        assert_eq!(r, Route::Native { id: "js-5".into(), op: NativeOp::BeginResize { edge: "se".into(), double_click: false } });
        let r = route_message(r#"{"t":"call","id":"js-10","name":"begin_native_window_resize","args":["n",true]}"#).unwrap();
        assert_eq!(r, Route::Native { id: "js-10".into(), op: NativeOp::BeginResize { edge: "n".into(), double_click: true } });
    }

    #[test]
    fn caption_menu_is_answered_natively() {
        let r = route_message(r#"{"t":"call","id":"js-11","name":"uefnNativeWindowMenu","args":[]}"#).unwrap();
        assert_eq!(r, Route::Native { id: "js-11".into(), op: NativeOp::ShowMenu });
    }

    #[test]
    fn bad_messages() {
        for bad in ["nope", "[]", r#"{"t":"x","id":"a","name":"b"}"#, r#"{"t":"call","id":"","name":"b"}"#, r#"{"t":"call","id":"a","name":"b","args":5}"#] {
            assert!(matches!(route_message(bad), Err(ShimError::BadMessage(_))), "{bad}");
        }
    }

    #[test]
    fn string_wrapped_message_and_missing_args() {
        let inner = r#"{"t":"call","id":"js-6","name":"hide_window"}"#;
        let wrapped = serde_json::to_string(inner).unwrap();
        assert_eq!(route_message(&wrapped).unwrap(), Route::Native { id: "js-6".into(), op: NativeOp::Hide });
    }

    #[test]
    fn backend_results_forwarded_for_page_ids_only() {
        let ok = page_message_from_backend(r#"{"t":"result","id":"js-7","ok":true,"value":{"v":1},"extra":1}"#).unwrap();
        assert_eq!(serde_json::from_str::<Value>(&ok).unwrap(), json!({"t":"result","id":"js-7","ok":true,"value":{"v":1}}));
        let err = page_message_from_backend(r#"{"t":"result","id":"js-8","ok":false,"error":"boom"}"#).unwrap();
        assert_eq!(serde_json::from_str::<Value>(&err).unwrap(), json!({"t":"result","id":"js-8","ok":false,"error":"boom"}));
        let none = page_message_from_backend(r#"{"t":"result","id":"js-9","ok":true}"#).unwrap();
        assert_eq!(serde_json::from_str::<Value>(&none).unwrap()["value"], Value::Null);
        assert!(page_message_from_backend(r#"{"t":"result","id":"launcher-1","ok":true}"#).is_none());
        assert!(page_message_from_backend(r#"{"t":"hello","protocol":1}"#).is_none());
        assert!(page_message_from_backend("garbage").is_none());
    }

    #[test]
    fn key_order_survives_both_ways() {
        // The page compares what it sent with what comes back as text; sorted keys never matched.
        let r = route_message(r##"{"t":"call","id":"js-12","name":"push_appearance_live","args":[{"foundation":{"text":"#fff","bg":"#000"},"active_profile_id":"p"}]}"##).unwrap();
        let Route::Backend { line, .. } = r else { panic!("not backend") };
        assert!(line.contains(r##"[{"foundation":{"text":"#fff","bg":"#000"},"active_profile_id":"p"}]"##), "{line}");
        let back = page_message_from_backend(r#"{"t":"result","id":"js-13","ok":true,"value":{"z":1,"a":2}}"#).unwrap();
        assert!(back.contains(r#""value":{"z":1,"a":2}"#), "{back}");
    }
}
