//! The routing table against the Python js_api: every window name the launcher answers
//! natively must be a real method of `PanelApiWindowMixin`, and the shim must expose the
//! pywebview surface the page relies on.

use std::path::PathBuf;

use shim::{is_window_name, native_op, SHIM_JS, WINDOW_NAMES};

fn repo_file(rel: &str) -> String {
    let p = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..").join(rel);
    std::fs::read_to_string(&p).unwrap_or_else(|e| panic!("{}: {e}", p.display()))
}

fn python_methods(src: &str) -> Vec<String> {
    src.lines()
        .filter_map(|l| l.strip_prefix("    def "))
        .filter_map(|rest| rest.split('(').next())
        .filter(|n| !n.starts_with('_'))
        .map(str::to_string)
        .collect()
}

#[test]
fn window_names_are_js_api_methods() {
    let methods = python_methods(&repo_file("ducky_app/frontend/ui_web/panel_api_window.py"));
    assert!(methods.len() > 50, "parsed too few methods: {}", methods.len());
    for name in WINDOW_NAMES {
        assert!(methods.iter().any(|m| m == name), "{name} is not a js_api method in panel_api_window.py");
    }
}

#[test]
fn webview_app_still_passes_the_api_object() {
    // The page's api is the object handed to create_window(js_api=...); if that changes,
    // this routing table must be reviewed.
    assert!(repo_file("ducky_app/frontend/ui_web/webview_app.py").contains("js_api=api"));
}

#[test]
fn every_window_name_has_a_native_op() {
    for name in WINDOW_NAMES {
        assert!(is_window_name(name));
        let args = [serde_json::json!(1), serde_json::json!(2), serde_json::json!(3), serde_json::json!(4)];
        let args: &[serde_json::Value] = if *name == "begin_native_window_resize" { &[serde_json::json!("n")] } else { &args };
        assert!(matches!(native_op(name, args), Some(Ok(_))), "{name}");
    }
}

#[test]
fn backend_names_are_not_window_names() {
    for name in ["get_version", "exit_all", "pick_project_path", "open_external_url", "terminal_spawn", "open_focus_window"] {
        assert!(!is_window_name(name), "{name}");
        assert!(native_op(name, &[]).is_none());
    }
}

#[test]
fn cacheable_names_are_python_getters_in_the_inventory() {
    let inv = repo_file("ducky_app/frontend/native_host/inventory.py");
    let tokens: std::collections::HashSet<&str> = inv.split_whitespace().collect();
    for name in shim::cache::CACHEABLE {
        assert!(tokens.contains(name), "{name} is not in native_host/inventory.py");
        assert!(!is_window_name(name), "{name}");
        assert!(!inv.contains(&format!("\"{name}\": \"")), "{name} is a launcher handler");
    }
}

#[test]
fn every_cacheable_name_has_an_in_place_refresh() {
    let page = repo_file("ducky_app/frontend/ui_web/web/src/hooks/startupRefresh.ts");
    // Keys of PANEL_PUSH / AGENT_EVENT: `  name: [`.
    let mapped: Vec<&str> = page
        .lines()
        .filter_map(|l| l.strip_prefix("  ")?.split_once(": [").map(|(k, _)| k))
        .filter(|k| k.chars().all(|c| c.is_ascii_lowercase() || c == '_'))
        .collect();
    assert!(mapped.len() >= 5, "parsed too few refresh entries: {mapped:?}");
    for name in shim::cache::CACHEABLE {
        if shim::cache::NO_REFRESH_NEEDED.contains(name) {
            continue;
        }
        assert!(mapped.contains(name), "{name} is cached but startupRefresh.ts has no refresh for it");
    }
    for name in &mapped {
        assert!(shim::cache::CACHEABLE.contains(name), "{name} is mapped but never cached");
    }
}

#[test]
fn shim_js_never_reloads_the_page() {
    for needle in ["reload(", "location.href =", "location.assign", "location.replace"] {
        assert!(!SHIM_JS.contains(needle), "shim.js must refresh in place, found {needle}");
    }
    let page = repo_file("ducky_app/frontend/ui_web/web/src/hooks/startupRefresh.ts");
    assert!(page.contains("\"ducky-native-startup-refresh\""), "page no longer listens for startup-refresh");
}

#[test]
fn shim_js_exposes_pywebview_surface() {
    for needle in ["window.pywebview.api", "pywebviewready", "chrome.webview", "postMessage", "new Proxy", "\"js-\""] {
        assert!(SHIM_JS.contains(needle), "shim.js lacks {needle}");
    }
}

#[test]
fn shim_js_answers_every_sync_bridge_name_the_page_sends() {
    // Nov 6 2026: 1.2.472 shipped without _jsApiCallback, so the header could not drag the
    // window and the top edge could not resize it.
    let sent: Vec<String> = [
        "ducky_app/frontend/ui_web/web/src/utils/windowDrag.ts",
        "ducky_app/frontend/ui_web/web/src/utils/nativeWindowChrome.ts",
    ]
    .iter()
    .flat_map(|f| {
        repo_file(f)
            .split("_jsApiCallback(\"")
            .skip(1)
            .filter_map(|s| s.split('"').next().map(str::to_string))
            .collect::<Vec<_>>()
    })
    .collect();
    assert!(sent.len() >= 3, "parsed too few bridge calls: {sent:?}");
    assert!(SHIM_JS.contains("window.pywebview._jsApiCallback"));
    for name in &sent {
        assert!(SHIM_JS.contains(&format!("{name}:")), "shim.js does not map {name}");
    }
    assert!(matches!(native_op("uefnNativeWindowMenu", &[]), Some(Ok(_))));
    assert!(matches!(native_op("begin_native_window_resize", &[serde_json::json!("n"), serde_json::json!(true)]), Some(Ok(_))));
}
