//! A chat's document as the Python backend reads and writes it (`frontend/chat_store.py`:
//! `Conversation.from_dict(doc).to_dict()`), so a chat saved here is stored as there.

use std::collections::HashSet;
use std::path::Path;
use std::sync::LazyLock;

use serde_json::{json, Map, Value};

use crate::store::pyjson::{float, int, pystr, truthy};

/// The duckies in the build: `(ids, the default style)` (`frontend/ducky_assets.py`).
/// ponytail: only `bundled_duckies.json` is read; Python's fallback scan of the web
/// bundle's `duckies/` folder is for a checkout without that file.
pub(super) static BUNDLED: LazyLock<(HashSet<String>, String)> = LazyLock::new(|| {
    let file: Value = serde_json::from_str(include_str!("../../../../../ducky_app/frontend/bundled_duckies.json")).unwrap_or_default();
    let field = |item: &Value, name: &str| item.get(name).map(|v| pystr(v).trim().to_string()).unwrap_or_default();
    let listed = file["duckies"].as_array().into_iter().flatten().filter(|item| item.is_object());
    let ids: Vec<String> = listed.filter(|item| !field(item, "file").is_empty()).map(|item| field(item, "id")).filter(|id| !id.is_empty()).collect();
    let default = field(&file, "default_style");
    let default = if default.is_empty() { ids.first().cloned().unwrap_or_else(|| "classic".into()) } else { default };
    (ids.into_iter().collect(), default)
});

/// `str(value or "")`.
pub(super) fn text(value: &Value) -> String {
    if truthy(value) { pystr(value) } else { String::new() }
}

/// `float(raw) if raw is not None and raw != "" else 0.0`.
pub(super) fn sort_order(raw: &Value) -> f64 {
    match raw {
        Value::Null => 0.0,
        Value::String(text) if text.is_empty() => 0.0,
        Value::String(text) => text.trim().parse().unwrap_or(0.0),
        other => float(other).unwrap_or(0.0),
    }
}

/// `norm_file_path`.
pub(super) fn file_path(path: &str) -> String {
    path.trim().replace('\\', "/")
}

/// `ducky_assets.normalize_ducky_style`: a bundled id, a custom ducky that still has its
/// picture (`custom`, the slugs found in the data folder), else the default.
pub(super) fn ducky_style(raw: &str, custom: &HashSet<String>) -> String {
    let raw = raw.trim();
    if let Some(slug) = raw.strip_prefix("custom:") {
        let slug = slug.trim().to_lowercase();
        return if custom.contains(&slug) { raw.to_string() } else { BUNDLED.1.clone() };
    }
    let lowered = raw.to_lowercase();
    if BUNDLED.0.contains(&lowered) { lowered } else { BUNDLED.1.clone() }
}

/// The custom duckies' slugs: `<data folder>/duckies/<slug>.png`, the slug as
/// `ducky_assets._SLUG_RE` allows it.
pub(super) async fn custom_duckies(data_dir: &Path) -> HashSet<String> {
    let folder = data_dir.join("duckies");
    #[allow(clippy::disallowed_methods)] // runs in spawn_blocking, below
    let scan = move || {
        let mut slugs = HashSet::new();
        for entry in std::fs::read_dir(folder).into_iter().flatten().flatten() {
            let name = entry.file_name().to_string_lossy().to_lowercase();
            let Some(slug) = name.strip_suffix(".png") else { continue };
            let ok = |c: char| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' || c == '-';
            let first = slug.chars().next().is_some_and(|c| c.is_ascii_lowercase() || c.is_ascii_digit());
            if first && slug.len() <= 64 && slug.chars().all(ok) && entry.path().is_file() {
                slugs.insert(slug.to_string());
            }
        }
        slugs
    };
    tokio::task::spawn_blocking(scan).await.unwrap_or_default()
}

/// `is_placeholder_title`: a name the app gave ("New ducky 3", "Group"), never chosen.
pub(super) fn is_placeholder(title: &str) -> bool {
    let lowered = title.trim().to_lowercase();
    let name = lowered.trim_end_matches(|c: char| c.is_ascii_digit()).trim_end();
    let new = name.strip_prefix("new").map(str::trim_start);
    lowered.is_empty() || matches!(name, "ducky" | "chat" | "group" | "subagent" | "sub-agent") || matches!(new, Some("ducky" | "chat"))
}

/// `[str(x) for x in value]`, None when it is not set.
fn strings(value: &Value) -> Value {
    match value {
        Value::Array(items) => items.iter().map(pystr).collect(),
        Value::Object(map) => map.keys().cloned().collect(),
        Value::String(text) => text.chars().map(String::from).collect(),
        _ => Value::Null,
    }
}

/// The chat without its messages, every field coerced as `Conversation` coerces it and
/// in `to_dict`'s order. Fields the app does not know are dropped, as there.
pub(super) fn normalise(doc: &Value, custom: &HashSet<String>) -> Map<String, Value> {
    let get = |name: &str| doc.get(name).unwrap_or(&Value::Null);
    let str_of = |name: &str| text(get(name));
    let stripped = |name: &str| str_of(name).trim().to_string();
    let number = |name: &str| float(get(name)).unwrap_or(0.0);
    let dict = |name: &str| if get(name).is_object() { get(name).clone() } else { Value::Null };
    let kept = |name: &str| match get(name) {
        Value::Array(items) => items.iter().map(pystr).filter(|item| !item.trim().is_empty()).collect(),
        _ => Vec::<String>::new(),
    };
    let subskills = match get("enabled_subskills") {
        Value::Object(packs) => Value::Object(packs.iter().map(|(pack, names)| (pack.clone(), strings(names))).collect()),
        _ => Value::Null,
    };
    let members: Vec<Value> = get("group_members").as_array().into_iter().flatten().filter(|member| member.is_object()).cloned().collect();
    let agent = stripped("coding_agent");
    // A list, not one `json!`: fifty fields are past the macro's recursion limit.
    let fields: Vec<(&str, Value)> = vec![
        ("id", doc.get("id").map(pystr).unwrap_or_default().into()),
        ("folder_id", str_of("folder_id").into()),
        ("title", doc.get("title").map_or("New ducky".to_string(), pystr).into()),
        ("created", number("created").into()),
        ("updated", number("updated").into()),
        ("sort_order", sort_order(get("sort_order")).into()),
        ("provider", doc.get("provider").map_or("anthropic".to_string(), pystr).into()),
        ("model", str_of("model").into()),
        ("messages", json!([])),
        ("skill_snapshot", str_of("skill_snapshot").into()),
        ("skill_snapshot_rev", str_of("skill_snapshot_rev").into()),
        ("enabled_skills", strings(get("enabled_skills"))),
        ("enabled_packs", strings(get("enabled_packs"))),
        ("enabled_subskills", subskills),
        ("disabled_packs", strings(get("disabled_packs"))),
        ("ducky_style", ducky_style(&str_of("ducky_style"), custom).into()),
        ("ducky_name", stripped("ducky_name").into()),
        ("profile_id", stripped("profile_id").into()),
        ("ducky_personality", stripped("ducky_personality").into()),
        ("tts_voice", stripped("tts_voice").into()),
        ("tts_speed", number("tts_speed").into()),
        ("file_path", file_path(&str_of("file_path")).into()),
        ("context_omit", strings(get("context_omit"))),
        ("context_summary", str_of("context_summary").into()),
        ("context_summary_through", int(get("context_summary_through")).into()),
        ("context_summary_tokens", int(get("context_summary_tokens")).into()),
        ("mcp_plugins", strings(get("mcp_plugins"))),
        ("builtin_toolsets", strings(get("builtin_toolsets"))),
        ("uefn_plugins", strings(get("uefn_plugins"))),
        ("token_usage", dict("token_usage")),
        ("prompt_cache_snapshot", dict("prompt_cache_snapshot")),
        ("rules_app_version", stripped("rules_app_version").into()),
        ("coding_agent", if agent.is_empty() { "ducky".to_string() } else { agent }.into()),
        ("thinking_effort", stripped("thinking_effort").to_lowercase().into()),
        ("upstream_session_id", stripped("upstream_session_id").into()),
        ("coding_agent_stats", dict("coding_agent_stats")),
        ("context_limit_tokens", int(get("context_limit_tokens")).into()),
        ("terminal_session_id", stripped("terminal_session_id").into()),
        ("parent_conv_id", stripped("parent_conv_id").into()),
        ("is_group", truthy(get("is_group")).into()),
        ("muted", truthy(get("muted")).into()),
        ("leader_conv_id", stripped("leader_conv_id").into()),
        ("group_members", members.into()),
        ("tool_call_count", int(get("tool_call_count")).into()),
        ("file_count", int(get("file_count")).into()),
        ("web_access_allowed", truthy(get("web_access_allowed")).into()),
        ("web_access_denied", truthy(get("web_access_denied")).into()),
        ("agent_allow_rules", kept("agent_allow_rules").into()),
        ("web_fetch_urls", kept("web_fetch_urls").into()),
        ("settings_edited", number("settings_edited").into()),
    ];
    fields.into_iter().map(|(name, value)| (name.to_string(), value)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_ducky_style_is_a_bundled_one_a_custom_one_with_its_picture_or_the_default() {
        let (ids, default) = &*BUNDLED;
        assert!(ids.contains(default) && !ids.is_empty(), "{default}");
        let custom = HashSet::from(["mine".to_string()]);
        let known = ids.iter().next().unwrap();
        assert_eq!(ducky_style(&format!("  {} ", known.to_uppercase()), &custom), *known);
        assert_eq!(ducky_style("custom:Mine", &custom), "custom:Mine");
        for unknown in ["", "nope", "custom:gone", "custom:../x"] {
            assert_eq!(ducky_style(unknown, &custom), *default, "{unknown}");
        }
    }

    #[test]
    fn a_title_the_app_gave_is_a_placeholder() {
        for given in ["", "  ", "New ducky", "NewDucky12", "new  chat 3", "Ducky", "chat7", "GROUP 2", "Sub-agent", "subagent 4"] {
            assert!(is_placeholder(given), "{given}");
        }
        for chosen in ["My ducky", "New", "New group", "Group of 5", "7", "chat x", "sub agent"] {
            assert!(!is_placeholder(chosen), "{chosen}");
        }
    }

    #[test]
    fn a_chat_is_coerced_and_ordered_as_python_writes_it() {
        let chat = normalise(
            &json!({
                "id": 5, "title": null, "created": "3", "coding_agent": "  ", "thinking_effort": " High ", "file_path": " a\\b ",
                "enabled_skills": ["a", 2], "enabled_subskills": {"p": ["x", 1]}, "context_omit": null, "token_usage": [1],
                "group_members": [{"n": 1}, "x"], "agent_allow_rules": ["Bash:ls", " ", 3], "is_group": 1, "extra": {"dropped": true},
            }),
            &HashSet::new(),
        );
        assert_eq!(chat.keys().take(4).collect::<Vec<_>>(), ["id", "folder_id", "title", "created"]);
        assert_eq!(chat.len(), 50);
        assert_eq!((&chat["id"], &chat["title"], &chat["created"], &chat["provider"]), (&json!("5"), &json!("None"), &json!(3.0), &json!("anthropic")));
        assert_eq!((&chat["coding_agent"], &chat["thinking_effort"], &chat["file_path"]), (&json!("ducky"), &json!("high"), &json!("a/b")));
        assert_eq!((&chat["enabled_skills"], &chat["enabled_subskills"]), (&json!(["a", "2"]), &json!({"p": ["x", "1"]})));
        assert_eq!((&chat["context_omit"], &chat["token_usage"], &chat["mcp_plugins"]), (&Value::Null, &Value::Null, &Value::Null));
        assert_eq!((&chat["group_members"], &chat["agent_allow_rules"], &chat["is_group"]), (&json!([{"n": 1}]), &json!(["Bash:ls", "3"]), &json!(true)));
        assert!(!chat.contains_key("extra"));
    }
}
