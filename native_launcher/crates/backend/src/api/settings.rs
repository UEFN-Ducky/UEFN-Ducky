//! Settings and appearance methods (`panel_api_settings.py`, `frontend/settings.py`).
//!
//! A field with no row has its default from `settings_defaults.json`, the contract the
//! Python backend checks its `PanelSettings` against.

use std::sync::{Arc, LazyLock};

use serde_json::{json, Map, Value};

use super::router::Api;
use super::uuid4;
use crate::store::kv;
use crate::store::pyjson::{int, py_eq, pystr, repr, truthy};
use crate::store::settings::Fields;

static DEFAULTS: LazyLock<Fields> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../../../../../ducky_app/frontend/settings_defaults.json")).expect("settings_defaults.json")
});

const DEFAULT_PROFILE: &str = "__default__";
/// Built in once, Store plugins now: a saved pick of one falls back to Default.
const LEGACY_PROFILES: [&str; 2] = ["__light__", "__hacker__"];
const MAKERS: [&str; 4] = ["default_image_provider", "default_mesh_provider", "default_video_provider", "default_music_provider"];

/// The built-in appearance profiles, cleaned as `appearance_builtin_profiles.py` cleans them.
static BUILT_IN: LazyLock<Vec<Value>> = LazyLock::new(|| {
    let file: Value = serde_json::from_str(include_str!("../../../../../ducky_app/frontend/appearance_builtin_profiles.json")).unwrap_or_default();
    let profiles: Vec<Value> = file["profiles"].as_array().into_iter().flatten().filter_map(|item| profile(item, true)).collect();
    match profiles.is_empty() {
        true => vec![json!({"id": DEFAULT_PROFILE, "name": "Default", "foundation": {}, "overrides": {}, "status_overrides": {}})],
        false => profiles,
    }
});

/// One load of the settings: every field (its row, else its default) and the snapshot
/// the rows came from, so a save writes only what this caller changed.
pub struct Loaded {
    pub fields: Fields,
    origin: u64,
}

impl Loaded {
    pub fn get(&self, key: &str) -> &Value {
        self.fields.get(key).unwrap_or(&Value::Null)
    }

    fn set(&mut self, key: &str, value: impl Into<Value>) {
        self.fields.insert(key.to_string(), value.into());
    }

    /// `str(field or "").strip()`.
    fn text(&self, key: &str) -> String {
        text(self.get(key))
    }
}

/// `str(value or "").strip()`.
fn text(value: &Value) -> String {
    if truthy(value) { pystr(value).trim().to_string() } else { String::new() }
}

/// `dict(value or {})`.
fn object(value: &Value) -> Value {
    Value::Object(value.as_object().cloned().unwrap_or_default())
}

/// `{str(k): str(v) for k, v in value.items() if v}`; anything but an object is empty.
fn str_map(value: &Value) -> Value {
    let rows = value.as_object().into_iter().flatten();
    Value::Object(rows.filter(|(_, v)| truthy(v)).map(|(k, v)| (k.clone(), Value::String(pystr(v)))).collect())
}

/// Per-status colors; `keep_empty` keeps a status left with no color.
fn status_map(value: &Value, keep_empty: bool) -> Value {
    let rows = value.as_object().into_iter().flatten().filter(|(_, fields)| fields.is_object());
    let rows = rows.map(|(status, fields)| (status.clone(), str_map(fields)));
    Value::Object(rows.filter(|(_, row)| keep_empty || truthy(row)).collect())
}

/// One profile as it is kept: id, name and its three color maps. None without an id or a
/// name, or (for a person's own profile) with a built-in's id.
fn profile(item: &Value, built_in: bool) -> Option<Value> {
    let (id, name) = (text(&item["id"]), text(&item["name"]));
    if !item.is_object() || id.is_empty() || name.is_empty() || (!built_in && is_built_in(&id)) {
        return None;
    }
    Some(json!({
        "id": id,
        "name": name,
        "foundation": str_map(&item["foundation"]),
        "overrides": str_map(&item["overrides"]),
        "status_overrides": status_map(&item["status_overrides"], true),
    }))
}

fn clean_profiles(raw: &Value) -> Value {
    Value::Array(raw.as_array().into_iter().flatten().filter_map(|item| profile(item, false)).collect())
}

fn is_built_in(id: &str) -> bool {
    BUILT_IN.iter().any(|profile| profile["id"] == id.trim())
}

/// Put a built-in profile's colors on the settings; false for an unknown id.
fn apply_built_in(s: &mut Loaded, id: &str) -> bool {
    let Some(profile) = BUILT_IN.iter().find(|profile| profile["id"] == id.trim()) else {
        return false;
    };
    // Default keeps the maps empty: the page merges its CSS defaults.
    let colors = |key: &str| if profile["id"] == DEFAULT_PROFILE { json!({}) } else { profile[key].clone() };
    s.set("appearance_foundation", colors("foundation"));
    s.set("appearance_overrides", colors("overrides"));
    s.set("appearance_status_overrides", colors("status_overrides"));
    s.set("appearance_active_profile_id", profile["id"].clone());
    true
}

/// `float(value)`; None where Python raises.
fn py_float(value: &Value) -> Option<f64> {
    match value {
        Value::Bool(b) => Some(f64::from(u8::from(*b))),
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

/// `store.switch.use_db` says no: this store is kept in files, the Python backend's to read.
pub(super) fn files_backend(store: &str) -> bool {
    let env = |name: String| std::env::var(name).unwrap_or_default().trim().to_lowercase();
    let one = env(format!("DUCKY_STORE_BACKEND_{}", store.to_uppercase()));
    if one.is_empty() { env("DUCKY_STORE_BACKEND".into()) == "files" } else { one == "files" }
}

pub(crate) fn failed(err: crate::store::Error) -> String {
    err.0
}

/// Refuse while the Python backend has not moved `store`'s old files (`legacy`, under
/// the data folder) into ducky.db: it imports them once, on its first read.
pub(super) async fn imported(api: &Api, store: &str, legacy: &str) -> Result<(), String> {
    if files_backend(store) {
        return Err(format!("{store} are kept in files here (DUCKY_STORE_BACKEND)"));
    }
    let db = api.repos.store().await.map_err(failed)?;
    if kv::meta_get(db, format!("imported:{store}")).await.map_err(failed)?.is_none() {
        let old = api.repos.data_dir().join(legacy);
        if tokio::task::spawn_blocking(move || old.exists()).await.unwrap_or(true) {
            return Err(format!("{store} are not imported yet"));
        }
    }
    Ok(())
}

/// `PanelSettings._finish_load`: the rows over the defaults, with the load-time fixups.
fn finish_load(rows: Fields) -> Fields {
    let mut out = DEFAULTS.clone();
    for (key, value) in rows {
        // A null row is no row, and an API key is never a settings field.
        let key_field = key == "api_key" || key.ends_with("_api_key");
        if !value.is_null() && !key_field && DEFAULTS.contains_key(&key) {
            out.insert(key, value);
        }
    }
    let list = |value: &Value| value.as_array().cloned().unwrap_or_default();
    // builtin_uefn is gone: its tools are Store plugins.
    let toolsets = list(&out["disabled_builtin_toolsets"]).into_iter().filter(|g| pystr(g).trim() != "builtin_uefn");
    out["disabled_builtin_toolsets"] = toolsets.collect();
    out["hidden_bundled_agent_profile_ids"] = list(&out["hidden_bundled_agent_profile_ids"]).into();
    // The old default was on, so every install had saved it: turned off once.
    if !truthy(&out["follow_code_off_migrated"]) {
        out["follow_code_enabled"] = false.into();
    }
    out["follow_code_off_migrated"] = true.into();
    // The old default (2,500) held about 6 entries: a saved 2,500 counts as unset.
    if py_eq(&out["memory_index_max_chars"], &json!(2500)) {
        out["memory_index_max_chars"] = DEFAULTS["memory_index_max_chars"].clone();
    }
    out
}

/// `PanelSettings.load()`.
pub async fn load(api: &Api) -> Result<Loaded, String> {
    imported(api, "settings", "panel_settings.json").await?;
    let (rows, origin) = api.repos.settings().await.map_err(failed)?.load_fields_versioned().await.map_err(failed)?;
    Ok(Loaded { fields: finish_load(rows), origin })
}

/// `PanelSettings.save()`: the fields that differ from the rows `s` was loaded from.
/// ponytail: Python also normalizes every other field on a save (`validate()`: clamps,
/// trimmed ids); here a save writes what its method changed. Port a rule with the method
/// that edits its field.
async fn save(api: &Api, s: &Loaded) -> Result<(), String> {
    let repo = api.repos.settings().await.map_err(failed)?;
    repo.save_fields(s.fields.clone(), DEFAULTS.clone(), Some(s.origin)).await.map(drop).map_err(failed)
}

/// Save each of `fields` that is not already what is kept.
pub(super) async fn set_fields(api: &Api, fields: Vec<(&str, Value)>) -> Result<(), String> {
    let mut s = load(api).await?;
    let changed: Vec<_> = fields.into_iter().filter(|(key, value)| s.get(key) != value).collect();
    if changed.is_empty() {
        return Ok(());
    }
    for (key, value) in changed {
        s.set(key, value);
    }
    save(api, &s).await
}

/// `coding_agents_dict`: each saved agent's row with its defaults filled in.
/// ponytail: an installed gateway's own defaults (its `register()`) are known to the
/// plugin host only, so `get_settings` is not routed here until the host can be asked.
fn coding_agents(raw: &Value) -> Value {
    let empty = Map::new();
    let raw = raw.as_object().unwrap_or(&empty);
    let ids = raw.iter().filter(|(_, entry)| entry.is_object()).map(|(key, _)| key.trim().to_lowercase().replace('-', "_"));
    let mut out = Map::new();
    for id in ids.filter(|id| !id.is_empty()) {
        let mut row = json!({"enabled": true, "cli_path": "", "default_args": ""});
        let saved = raw.get(&id).and_then(Value::as_object).into_iter().flatten();
        for (key, value) in saved.filter(|(_, value)| !value.is_null()) {
            row[key] = value.clone();
        }
        row["enabled"] = truthy(&row["enabled"]).into();
        for key in ["cli_path", "default_args"] {
            row[key] = if truthy(&row[key]) { pystr(&row[key]) } else { String::new() }.into();
        }
        out.insert(id, row);
    }
    Value::Object(out)
}

pub async fn get_settings(api: Arc<Api>, _args: Fields) -> Result<Value, String> {
    let s = load(&api).await?;
    let raw = |key: &str| s.get(key).clone();
    let flag = |key: &str| Value::Bool(truthy(s.get(key)));
    let or = |key: &str, default: Value| if truthy(s.get(key)) { raw(key) } else { default };
    let whole = |key: &str, default: i64| json!(int(&or(key, json!(default))));
    let one_of = |key: &str, allowed: &[&str], default: &str| match s.get(key).as_str() {
        Some(pick) if allowed.contains(&pick) => json!(pick),
        _ => json!(default),
    };
    let number = |value: Value| match py_float(&value) {
        Some(n) => Ok(json!(n)),
        None => Err(format!("could not convert string to float: {}", repr(&value))),
    };
    let done: Vec<(String, Value)> =
        s.get("walkthrough_completed").as_object().into_iter().flatten().filter(|(_, v)| truthy(v)).map(|(k, _)| (k.clone(), true.into())).collect();
    let mut out: Vec<(&str, Value)> = vec![
        ("agent_provider", raw("agent_provider")),
        ("agent_model", raw("agent_model")),
        ("default_model", raw("default_model")),
        ("uefn_project_root", raw("uefn_project_root")),
        ("antigravity_config_path", raw("antigravity_config_path")),
        ("verse_diagnostics_cache_enabled", raw("verse_diagnostics_cache_enabled")),
        ("verse_diagnostics_auto_check", raw("verse_diagnostics_auto_check")),
        ("ai_ignore_patterns", raw("ai_ignore_patterns")),
        ("ai_ignore_strict", raw("ai_ignore_strict")),
        ("allow_settings_write", flag("allow_settings_write")),
        ("allow_agent_clicks", flag("allow_agent_clicks")),
        ("allow_everything_all_chats", flag("allow_everything_all_chats")),
        ("allow_see_uefn", flag("allow_see_uefn")),
        ("allow_see_other_programs", flag("allow_see_other_programs")),
        ("show_hidden_project_files", raw("show_hidden_project_files")),
        ("terminals_enabled", raw("terminals_enabled")),
        ("prompt_caching_enabled", raw("prompt_caching_enabled")),
        ("freeze_prompt_prefix", raw("freeze_prompt_prefix")),
        ("anthropic_extended_cache_ttl", raw("anthropic_extended_cache_ttl")),
        ("default_coding_agent", or("default_coding_agent", json!("ducky"))),
        ("coding_agents", coding_agents(s.get("coding_agents"))),
        ("duckyos_base_url", or("duckyos_base_url", json!(""))),
        ("remote_access", flag("remote_access")),
        ("usage_stats", flag("usage_stats")),
        ("write_lanes_mode", json!(pystr(&or("write_lanes_mode", json!(""))))),
        ("voice_enabled", flag("voice_enabled")),
        ("voice_spoken_style", or("voice_spoken_style", json!("summary"))),
        ("voice_summary_model", or("voice_summary_model", json!(""))),
        ("voice_default_voice", or("voice_default_voice", json!(""))),
        ("voice_default_speed", number(or("voice_default_speed", json!(1.0)))?),
        ("voice_live_auto_send", flag("voice_live_auto_send")),
        ("voice_process_talk", number(or("voice_process_talk", json!(0.0)))?),
        ("voice_stt_provider", json!(s.text("voice_stt_provider"))),
        ("mic_permission", one_of("mic_permission", &["ask", "allow", "block"], "ask")),
        ("mic_device_id", or("mic_device_id", json!(""))),
        ("output_device_id", or("output_device_id", json!(""))),
        ("tts_volume", number(raw("tts_volume"))?),
        ("audio_muted", flag("audio_muted")),
        ("memory_auto_compress", flag("memory_auto_compress")),
        ("prompt_dedupe_exact_blocks", flag("prompt_dedupe_exact_blocks")),
        ("memory_keep_last_messages", whole("memory_keep_last_messages", 20)),
        ("memory_compress_messages", whole("memory_compress_messages", 40)),
        ("memory_compress_tokens", whole("memory_compress_tokens", 80_000)),
        ("memory_index_max_chars", whole("memory_index_max_chars", 8_000)),
        ("memory_summary_model", or("memory_summary_model", json!(""))),
        ("chat_auto_title", flag("chat_auto_title")),
        ("chat_mentions_enabled", flag("chat_mentions_enabled")),
        ("chat_slash_references_enabled", flag("chat_slash_references_enabled")),
        ("chat_title_model", or("chat_title_model", json!(""))),
    ];
    out.extend(MAKERS.map(|key| (key, or(key, json!("")))));
    out.extend([
        ("web_access", one_of("web_access", &["off", "ask", "on"], "ask")),
        ("team_send_delay_s", json!(int(s.get("team_send_delay_s")))),
        ("team_poll_s", json!(int(s.get("team_poll_s")))),
        ("follow_code_enabled", flag("follow_code_enabled")),
        ("follow_code_speed", one_of("follow_code_speed", &["slow", "normal", "fast", "instant"], "normal")),
        ("follow_code_split_beside_chat", flag("follow_code_split_beside_chat")),
        ("walkthrough_completed", Value::Object(done.into_iter().collect())),
        ("first_open_tours", flag("first_open_tours")),
        ("show_me_autoplay", flag("show_me_autoplay")),
        ("start_with_windows", flag("start_with_windows")),
        ("pc_memory_warnings", flag("pc_memory_warnings")),
    ]);
    Ok(Value::Object(out.into_iter().map(|(key, value)| (key.to_string(), value)).collect()))
}

/// The look as the page gets it; a saved pick of a profile that moved to the Store is
/// set back to Default first.
async fn appearance(api: &Api) -> Result<Value, String> {
    let mut s = load(api).await?;
    let active = s.text("appearance_active_profile_id");
    if LEGACY_PROFILES.contains(&active.as_str()) {
        apply_built_in(&mut s, DEFAULT_PROFILE);
        if active == "__hacker__" && s.text("appearance_effect_id") == "matrix" {
            s.set("appearance_effect_id", "");
            s.set("appearance_effects_enabled", false);
        }
        save(api, &s).await?;
    }
    let active = s.text("appearance_active_profile_id");
    Ok(json!({
        "foundation": object(s.get("appearance_foundation")),
        "overrides": object(s.get("appearance_overrides")),
        "status_overrides": object(s.get("appearance_status_overrides")),
        "profiles": clean_profiles(s.get("appearance_profiles")),
        "active_profile_id": if active.is_empty() { DEFAULT_PROFILE.to_string() } else { active },
        "effect_id": if truthy(s.get("appearance_effect_id")) { pystr(s.get("appearance_effect_id")) } else { String::new() },
        "effects_enabled": truthy(s.get("appearance_effects_enabled")),
        "skin_id": if truthy(s.get("appearance_skin_id")) { pystr(s.get("appearance_skin_id")) } else { String::new() },
        "profile_patches": object(s.get("appearance_profile_patches")),
        "sounds": object(s.get("appearance_sounds")),
    }))
}

/// Tell every window the look changed.
async fn push_changed(api: &Api) -> Result<(), String> {
    api.publish(vec![json!({"type": "appearance_changed", "appearance": appearance(api).await?})]);
    Ok(())
}

pub async fn get_appearance(api: Arc<Api>, _args: Fields) -> Result<Value, String> {
    appearance(&api).await
}

/// `_coerce_mapping`: the patch as an object (given as one, or as its JSON text).
fn mapping(value: Option<&Value>) -> Result<Map<String, Value>, String> {
    match value {
        None | Some(Value::Null) => Ok(Map::new()),
        Some(Value::Object(map)) => Ok(map.clone()),
        Some(Value::String(text)) if text.trim().is_empty() => Ok(Map::new()),
        Some(Value::String(text)) => match serde_json::from_str(text.trim()) {
            Ok(Value::Object(map)) => Ok(map),
            Ok(_) => Err("appearance patch must be a JSON object".to_string()),
            Err(err) => Err(err.to_string()),
        },
        Some(_) => Err("appearance patch must be an object".to_string()),
    }
}

pub async fn save_appearance(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let data = mapping(args.get("patch"))?;
    let given = |key: &str| data.get(key).unwrap_or(&Value::Null);
    let mut s = load(&api).await?;
    if given("foundation").is_object() {
        s.set("appearance_foundation", str_map(given("foundation")));
    }
    if given("overrides").is_object() {
        s.set("appearance_overrides", str_map(given("overrides")));
    }
    if given("status_overrides").is_object() {
        s.set("appearance_status_overrides", status_map(given("status_overrides"), false));
    }
    if given("profiles").is_array() {
        s.set("appearance_profiles", clean_profiles(given("profiles")));
    }
    if data.contains_key("active_profile_id") {
        let id = given("active_profile_id");
        s.set("appearance_active_profile_id", if truthy(id) { pystr(id) } else { String::new() });
    }
    for key in ["effect_id", "skin_id"] {
        if data.contains_key(key) {
            s.set(&format!("appearance_{key}"), text(given(key)));
        }
    }
    if data.contains_key("effects_enabled") {
        s.set("appearance_effects_enabled", truthy(given("effects_enabled")));
    }
    if let Some(patches) = given("profile_patches").as_object() {
        let rows = patches.iter().filter(|(_, row)| row.is_object()).map(|(id, row)| (id.clone(), row.clone()));
        s.set("appearance_profile_patches", Value::Object(rows.collect()));
    }
    if let Some(sounds) = given("sounds").as_object() {
        let sound = |key: &str| sounds.get(key).unwrap_or(&Value::Null);
        let mapping = sound("mapping").as_object().into_iter().flatten().filter(|(_, sound)| sound.is_string());
        let mapping: Map<String, Value> = mapping.map(|(hook, sound)| (hook.clone(), sound.clone())).collect();
        let volume = if sound("volume").is_null() { Some(0.5) } else { py_float(sound("volume")) };
        // min() and max() as Python orders them: not-a-number comes out as 1.
        let volume = volume.map_or(0.5, |v| if v < 1.0 { v.max(0.0) } else { 1.0 });
        s.set("appearance_sounds", json!({"enabled": truthy(sound("enabled")), "volume": volume, "mapping": mapping}));
    }
    save(&api, &s).await?;
    push_changed(&api).await?;
    Ok(json!("Appearance saved"))
}

/// `push_appearance_live`: show a look in every window without saving it.
pub async fn push_appearance_live(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    if let Some(look @ Value::Object(_)) = args.get("data") {
        api.publish(vec![json!({"type": "appearance_changed", "appearance": look})]);
    }
    Ok(Value::Null)
}

fn profiles(s: &Loaded) -> Vec<Value> {
    s.get("appearance_profiles").as_array().cloned().unwrap_or_default()
}

/// The argument as Python prints it in `Profile not found: ...`.
fn not_found(args: &Fields) -> String {
    format!("Profile not found: {}", pystr(args.get("profile_id").unwrap_or(&Value::Null)))
}

pub async fn save_appearance_profile(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let mut s = load(&api).await?;
    let name = text(args.get("name").unwrap_or(&Value::Null));
    let profile = json!({
        "id": uuid4(),
        "name": if name.is_empty() { "Untitled".to_string() } else { name },
        "foundation": object(s.get("appearance_foundation")),
        "overrides": object(s.get("appearance_overrides")),
        "status_overrides": object(s.get("appearance_status_overrides")),
    });
    let mut all = profiles(&s);
    all.push(profile.clone());
    s.set("appearance_profiles", all);
    s.set("appearance_active_profile_id", profile["id"].clone());
    save(&api, &s).await?;
    Ok(profile)
}

pub async fn load_appearance_profile(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let mut s = load(&api).await?;
    let id = text(args.get("profile_id").unwrap_or(&Value::Null));
    if is_built_in(&id) {
        apply_built_in(&mut s, &id);
    } else {
        let all = profiles(&s);
        let profile = all.iter().find(|profile| profile["id"] == id.as_str()).ok_or_else(|| not_found(&args))?;
        s.set("appearance_foundation", object(&profile["foundation"]));
        s.set("appearance_overrides", object(&profile["overrides"]));
        s.set("appearance_status_overrides", object(&profile["status_overrides"]));
        s.set("appearance_active_profile_id", id);
    }
    save(&api, &s).await?;
    push_changed(&api).await?;
    appearance(&api).await
}

pub async fn rename_appearance_profile(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let mut s = load(&api).await?;
    let id = text(args.get("profile_id").unwrap_or(&Value::Null));
    if is_built_in(&id) {
        return Err("Built-in appearance profiles cannot be renamed".to_string());
    }
    let name = text(args.get("name").unwrap_or(&Value::Null));
    if name.is_empty() {
        return Err("Profile name is required".to_string());
    }
    let mut all = profiles(&s);
    let profile = all.iter_mut().find(|profile| profile["id"] == id.as_str() && profile.is_object()).ok_or_else(|| not_found(&args))?;
    profile["name"] = name.into();
    let renamed = profile.clone();
    s.set("appearance_profiles", all);
    save(&api, &s).await?;
    Ok(renamed)
}

pub async fn delete_appearance_profile(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let mut s = load(&api).await?;
    let id = text(args.get("profile_id").unwrap_or(&Value::Null));
    if is_built_in(&id) {
        return Err("Built-in appearance profiles cannot be deleted".to_string());
    }
    let all = profiles(&s);
    let kept: Vec<Value> = all.iter().filter(|profile| profile["id"] != id.as_str()).cloned().collect();
    if kept.len() == all.len() {
        return Err(not_found(&args));
    }
    s.set("appearance_profiles", kept);
    let was_active = s.text("appearance_active_profile_id") == id;
    if was_active {
        // The look in use is gone: back to Default, with no effect and no skin.
        apply_built_in(&mut s, DEFAULT_PROFILE);
        s.set("appearance_effect_id", "");
        s.set("appearance_effects_enabled", false);
        s.set("appearance_skin_id", "");
    }
    save(&api, &s).await?;
    if was_active {
        push_changed(&api).await?;
    }
    Ok(json!("Profile deleted"))
}

/// `update_active_appearance_profile`: the profile in use takes the colors on screen.
pub async fn update_active_appearance_profile(api: Arc<Api>, _args: Fields) -> Result<Value, String> {
    let mut s = load(&api).await?;
    let id = s.text("appearance_active_profile_id");
    let mut all = profiles(&s);
    let found = all.iter_mut().find(|profile| profile["id"] == id.as_str() && profile.is_object());
    let Some(profile) = found.filter(|_| !id.is_empty() && !is_built_in(&id)) else {
        return Ok(Value::Null);
    };
    profile["foundation"] = object(s.get("appearance_foundation"));
    profile["overrides"] = object(s.get("appearance_overrides"));
    profile["status_overrides"] = object(s.get("appearance_status_overrides"));
    let updated = profile.clone();
    s.set("appearance_profiles", all);
    save(&api, &s).await?;
    Ok(updated)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rows(value: Value) -> Fields {
        value.as_object().cloned().unwrap_or_default()
    }

    #[test]
    fn a_load_is_the_rows_over_the_defaults_with_pythons_fixups() {
        let fresh = finish_load(Fields::new());
        assert_eq!(fresh.len(), DEFAULTS.len());
        assert!(!fresh.contains_key("port") && fresh["terminals_enabled"] == true && fresh["web_access"] == "ask");
        assert_eq!((&fresh["follow_code_off_migrated"], &fresh["hidden_bundled_agent_profile_ids"]), (&json!(true), &json!([])));

        let loaded = finish_load(rows(json!({
            "web_access": "on",
            "terminals_enabled": null,
            "openai_api_key": "sk-1",
            "no_such_field": 1,
            "follow_code_enabled": true,
            "memory_index_max_chars": 2500,
            "disabled_builtin_toolsets": ["builtin_uefn", " builtin_uefn ", "builtin_ducky"],
            "hidden_bundled_agent_profile_ids": ["a"],
        })));
        assert_eq!(loaded.len(), DEFAULTS.len(), "unknown and key fields are dropped");
        assert_eq!((&loaded["web_access"], &loaded["terminals_enabled"]), (&json!("on"), &json!(true)));
        assert_eq!((&loaded["follow_code_enabled"], &loaded["memory_index_max_chars"]), (&json!(false), &json!(8000)));
        assert_eq!(loaded["disabled_builtin_toolsets"], json!(["builtin_ducky"]));
        assert_eq!(loaded["hidden_bundled_agent_profile_ids"], json!(["a"]));
        let kept = finish_load(rows(json!({"follow_code_enabled": true, "follow_code_off_migrated": true, "memory_index_max_chars": 3000})));
        assert_eq!((&kept["follow_code_enabled"], &kept["memory_index_max_chars"]), (&json!(true), &json!(3000)));
    }

    #[test]
    fn profiles_and_color_maps_are_cleaned_as_python_cleans_them() {
        assert_eq!(str_map(&json!({"a": "#fff", "b": "", "n": 3, "z": null})), json!({"a": "#fff", "n": "3"}));
        assert_eq!(status_map(&json!({"error": {"bg": "red", "fg": ""}, "ok": {}, "bad": 1}), true), json!({"error": {"bg": "red"}, "ok": {}}));
        assert_eq!(status_map(&json!({"error": {"bg": "red"}, "ok": {"x": ""}}), false), json!({"error": {"bg": "red"}}));
        let raw = json!([{"id": " p1 ", "name": "Mine", "foundation": {"bg": "#000"}}, {"id": "__default__", "name": "x"}, {"id": "p2"}, "junk"]);
        let clean = json!([{"id": "p1", "name": "Mine", "foundation": {"bg": "#000"}, "overrides": {}, "status_overrides": {}}]);
        assert_eq!(clean_profiles(&raw), clean);
        assert!(is_built_in(" __default__ ") && !is_built_in("__light__") && !is_built_in(""));
    }

    #[test]
    fn a_patch_is_an_object_or_its_json_text_and_an_id_is_a_uuid4() {
        assert_eq!(mapping(Some(&json!(" {\"skin_id\": \"x\"} "))), Ok(rows(json!({"skin_id": "x"}))));
        assert_eq!((mapping(None), mapping(Some(&json!("  ")))), (Ok(Map::new()), Ok(Map::new())));
        assert_eq!(mapping(Some(&json!("[1]"))), Err("appearance patch must be a JSON object".to_string()));
        assert!(mapping(Some(&json!(5))).is_err() && mapping(Some(&json!("{nope"))).is_err());
        let (id, other) = (uuid4(), uuid4());
        let dashes: Vec<usize> = id.match_indices('-').map(|(at, _)| at).collect();
        assert_eq!((id.len(), dashes, &id[14..15]), (36, vec![8, 13, 18, 23], "4"));
        assert!("89ab".contains(&id[19..20]) && id != other);
    }

    #[test]
    fn saved_coding_agents_get_their_defaults() {
        let raw = json!({"Claude-Code": {"cli_path": "x"}, "codex": {"enabled": 0, "default_args": null, "permission_mode": "ask"}, "bad": 1});
        // The row is looked up by the id it is listed under, as Python does: a key that
        // is not already in that form keeps only its defaults.
        assert_eq!(
            coding_agents(&raw),
            json!({
                "claude_code": {"enabled": true, "cli_path": "", "default_args": ""},
                "codex": {"enabled": false, "cli_path": "", "default_args": "", "permission_mode": "ask"},
            })
        );
        assert_eq!(coding_agents(&json!(null)), json!({}));
    }
}
