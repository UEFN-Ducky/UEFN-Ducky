//! The desktop plugins installed on this PC, read from their folders
//! (`backend/uefn_plugins/store.py::list_uefn_plugins`, `plugin_version.py`).
#![allow(clippy::disallowed_methods)] // the whole module runs inside spawn_blocking

use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value};

use crate::store::pyjson::{pystr, truthy};

const MANIFEST: &str = "plugin.json";

/// `normalize_plugin_id`, or none for a name that is not a plugin id.
pub fn plugin_id(raw: &str) -> Option<String> {
    let id = raw.trim().to_lowercase().replace(' ', "_");
    let mut bytes = id.bytes();
    let ok = bytes.next().is_some_and(|first| first.is_ascii_lowercase()) && id.len() <= 64 && bytes.all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_' || b == b'-');
    ok.then_some(id)
}

/// `parse_plugin_version`: a bare number `7` is `0.0.7`, so `1.0.8` is newer.
/// ponytail: ASCII digits and numbers up to 2^64; Python also takes other scripts' digits.
pub fn version_parts(raw: &Value) -> (u64, u64, u64) {
    let number = |text: &str| (!text.is_empty() && text.bytes().all(|b| b.is_ascii_digit())).then(|| text.parse::<u64>().ok()).flatten();
    match raw {
        Value::Number(n) => (0, 0, n.as_u64().unwrap_or_else(|| n.as_f64().filter(|f| *f > 0.0).map_or(0, |f| f as u64))),
        Value::String(text) => {
            let text = text.trim();
            if let Some(n) = number(text) {
                return (0, 0, n);
            }
            let base = text.split('+').next().unwrap_or_default().split('-').next().unwrap_or_default();
            let parts: Option<Vec<u64>> = base.split('.').take(3).map(number).collect();
            match parts.as_deref() {
                Some([a]) => (*a, 0, 0),
                Some([a, b]) => (*a, *b, 0),
                Some([a, b, c]) => (*a, *b, *c),
                _ => (0, 0, 0),
            }
        }
        _ => (0, 0, 0),
    }
}

/// `format_plugin_version`: a semver as it is written, an old plain number as a number.
pub fn format_version(raw: &Value) -> String {
    if let Value::String(text) = raw {
        let text = text.trim();
        if !text.bytes().all(|b| b.is_ascii_digit()) && version_parts(raw) != (0, 0, 0) {
            return text.split('+').next().unwrap_or_default().split('-').next().unwrap_or_default().to_string();
        }
    }
    match version_parts(raw) {
        (0, 0, c) => c.to_string(),
        (a, b, c) => format!("{a}.{b}.{c}"),
    }
}

/// A folder's `plugin.json` when it is an object.
/// ponytail: Python's parser also takes `NaN` and `Infinity`; a manifest with one is
/// skipped here.
fn manifest(folder: &Path) -> Option<Map<String, Value>> {
    match serde_json::from_slice(&std::fs::read(folder.join(MANIFEST)).ok()?) {
        Ok(Value::Object(manifest)) => Some(manifest),
        _ => None,
    }
}

/// The folders of `root` in the order Python sorts paths: by name, ignoring case on Windows.
fn folders(root: &Path) -> Vec<PathBuf> {
    let Ok(entries) = std::fs::read_dir(root) else { return Vec::new() };
    let mut found: Vec<PathBuf> = entries.flatten().map(|entry| entry.path()).filter(|path| path.is_dir()).collect();
    let key = |path: &PathBuf| {
        let name = path.file_name().unwrap_or_default().to_string_lossy().into_owned();
        if cfg!(windows) {
            name.to_lowercase()
        } else {
            name
        }
    };
    found.sort_by_key(key);
    found
}

/// The ids of the folders that hold a plugin, read or not (`_installed_plugin_ids`).
pub fn on_disk(root: &Path) -> Vec<String> {
    let id = |folder: PathBuf| folder.join(MANIFEST).is_file().then(|| plugin_id(&folder.file_name()?.to_string_lossy())).flatten();
    folders(root).into_iter().filter_map(id).collect()
}

/// `value or fallback`, as text.
fn text_or(value: Option<&Value>, fallback: &str) -> String {
    value.filter(|value| truthy(value)).map_or_else(|| fallback.to_string(), pystr)
}

/// The ids in a saved list (`enabled_uefn_plugins`, `trusted_local_uefn_plugins`).
fn ids(saved: &Value) -> Vec<String> {
    saved.as_array().map(|list| list.iter().filter_map(|id| plugin_id(&pystr(id))).collect()).unwrap_or_default()
}

/// One row per installed plugin. `enabled`: the saved list, or null for "never chosen":
/// then every plugin that does not say `default_enabled: false` is on.
pub fn list(root: &Path, enabled: &Value, trusted: &Value) -> Vec<Value> {
    let found: Vec<(PathBuf, Map<String, Value>, String)> = folders(root)
        .into_iter()
        .filter_map(|folder| {
            let manifest = manifest(&folder)?;
            let name = folder.file_name().unwrap_or_default().to_string_lossy().into_owned();
            let id = plugin_id(&text_or(manifest.get("id"), &name))?;
            Some((folder, manifest, id))
        })
        .collect();
    let on_by_default = |manifest: &Map<String, Value>| manifest.get("default_enabled").is_none_or(truthy);
    let enabled = match enabled.is_null() {
        true => found.iter().filter(|(_, manifest, _)| on_by_default(manifest)).map(|(_, _, id)| id.clone()).collect(),
        false => ids(enabled),
    };
    let trusted = ids(trusted);
    let row = |(folder, manifest, id): &(PathBuf, Map<String, Value>, String)| {
        json!({
            "id": id,
            "label": text_or(manifest.get("label"), id),
            "description": text_or(manifest.get("description"), ""),
            "version": format_version(manifest.get("version").unwrap_or(&Value::Null)),
            "kind": text_or(manifest.get("kind"), "plugin"),
            "source": text_or(manifest.get("source"), "local"),
            "enabled": enabled.contains(id),
            "default_enabled": on_by_default(manifest),
            "local_trusted": trusted.contains(id),
            "contributes": manifest.get("contributes").filter(|value| value.is_object()).cloned().unwrap_or_else(|| json!({})),
            "secret_keys": manifest.get("secret_keys").filter(|value| value.is_array()).cloned().unwrap_or_else(|| json!([])),
            "path": folder.to_string_lossy(),
        })
    };
    found.iter().map(row).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api::project_paths::tests::Temp;

    #[test]
    fn a_version_is_a_semver_or_an_old_plain_number() {
        let rank = |(a, b, c): (u64, u64, u64)| a * 1_000_000 + b * 1_000 + c;
        let shown = |raw: Value| (format_version(&raw), rank(version_parts(&raw)));
        assert_eq!(shown(json!("1.0.8")), ("1.0.8".to_string(), 1_000_008));
        assert_eq!(shown(json!(" 2.3.4-beta+build ")), ("2.3.4".to_string(), 2_003_004));
        assert_eq!(shown(json!("1.2")), ("1.2".to_string(), 1_002_000));
        assert_eq!(shown(json!(7)), ("7".to_string(), 7));
        assert_eq!(shown(json!("007")), ("7".to_string(), 7));
        assert_eq!(shown(json!(3.9)), ("3".to_string(), 3));
        assert_eq!(shown(json!(-4)), ("0".to_string(), 0));
        for junk in [json!("v1.0"), json!("1.x.0"), json!(""), json!(null), json!(true), json!([1])] {
            assert_eq!(shown(junk.clone()), ("0".to_string(), 0), "{junk}");
        }
        assert_eq!(format_version(&json!("0.0.5")), "0.0.5");
    }

    #[test]
    fn a_plugin_id_is_lowercase_and_starts_with_a_letter() {
        assert_eq!(plugin_id(" My Plugin "), Some("my_plugin".to_string()));
        assert_eq!(plugin_id("a-b_9"), Some("a-b_9".to_string()));
        for bad in ["", "9lives", "_x", "a.b", "é", &"a".repeat(65)] {
            assert_eq!(plugin_id(bad), None, "{bad}");
        }
    }

    #[test]
    fn installed_plugins_are_listed_from_their_manifests() {
        let dir = Temp::new("plugins");
        let put = |name: &str, body: &str| {
            std::fs::create_dir_all(dir.0.join(name)).unwrap();
            std::fs::write(dir.0.join(name).join(MANIFEST), body).unwrap();
        };
        put("beta", r#"{"id": "Beta", "label": "B", "version": 7, "default_enabled": false, "contributes": {"a": 1}, "secret_keys": ["K"]}"#);
        put("Alpha", r#"{"source": "store", "version": "1.2.0"}"#);
        put("broken", "{nope");
        put("a list", "[1]");
        std::fs::write(dir.0.join("file.txt"), "x").unwrap();
        let rows = list(&dir.0, &Value::Null, &json!(["BETA", 5]));
        let brief = |row: &Value| (row["id"].clone(), row["version"].clone(), row["enabled"].clone(), row["local_trusted"].clone());
        assert_eq!(rows.iter().map(brief).collect::<Vec<_>>(), [(json!("alpha"), json!("1.2.0"), json!(true), json!(false)), (json!("beta"), json!("7"), json!(false), json!(true))]);
        assert_eq!((&rows[0]["label"], &rows[0]["source"], &rows[0]["contributes"], &rows[1]["source"]), (&json!("alpha"), &json!("store"), &json!({}), &json!("local")));
        assert_eq!(rows[1]["path"], dir.0.join("beta").to_string_lossy().as_ref());
        assert_eq!(list(&dir.0, &json!(["beta"]), &Value::Null)[1]["enabled"], true);
        assert!(list(&dir.0.join("missing"), &Value::Null, &Value::Null).is_empty());
    }
}
