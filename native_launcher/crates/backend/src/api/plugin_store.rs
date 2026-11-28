//! The Store tab's reads (`panel_api_store.py`): the plugins installed on this PC and a
//! Store item's version notes.
//!
//! ponytail: the catalog (`duckyos_store_catalog`) is still the Python backend's: it
//! merges in the skill packs and what a signed Store plugin says about private items,
//! which live with the plugin host. Install, update and remove stay there with it.

mod installed;

use std::sync::Arc;
use std::time::Duration;

use serde_json::{json, Map, Value};

use super::settings::load;
use super::site::{collect, Collect};
use super::Api;
use crate::store::kv;
use crate::store::pyjson::{py_eq, pystr, truthy};

type Fields = Map<String, Value>;

/// `str(value or "").strip()`.
fn stripped(value: Option<&Value>) -> String {
    value.filter(|value| truthy(value)).map(pystr).unwrap_or_default().trim().to_string()
}

/// The installed desktop plugins. A workflow pack's nodes and templates are counted, not
/// listed: they were most of the answer and the Store tab never reads them.
pub async fn list_uefn_plugins(api: Arc<Api>, _args: Fields) -> Result<Value, String> {
    let settings = match load(&api).await {
        Ok(settings) => settings,
        Err(error) => return Ok(json!({"ok": false, "error": error, "plugins": []})),
    };
    let (enabled, trusted) = (settings.get("enabled_uefn_plugins").clone(), settings.get("trusted_local_uefn_plugins").clone());
    let root = api.repos.data_dir().join("uefn_plugins");
    let mut plugins = tokio::task::spawn_blocking(move || installed::list(&root, &enabled, &trusted)).await.map_err(|stopped| stopped.to_string())?;
    for row in &mut plugins {
        if let Some(Value::Object(packs)) = row["contributes"].get_mut("automations") {
            for rows in packs.values_mut() {
                match rows {
                    Value::Array(list) => *rows = list.len().into(),
                    Value::Object(named) => *rows = named.len().into(),
                    _ => {}
                }
            }
        }
    }
    Ok(json!({"ok": true, "plugins": plugins}))
}

/// A Store item's public version notes (`store_item_versions`). The site is asked only
/// when there is no copy here or `latest_version` is not the copy's; offline answers the copy.
pub async fn duckyos_store_versions(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let (slug, want) = (stripped(args.get("slug")), stripped(args.get("latest_version")));
    if slug.is_empty() {
        return Ok(json!({"ok": false, "error": "slug required", "code": "bad_request", "versions": []}));
    }
    // The copy is a cache: a store that can't be read or written only means asking again.
    let (store, key) = (api.repos.store().await.ok(), format!("patch_notes:item:{slug}"));
    let kept = match store {
        Some(store) => kv::get_doc(store, "cache_docs", key.clone()).await.ok().flatten(),
        None => None,
    };
    let cached = kept.as_ref().filter(|doc| doc["versions"].is_array()).map(|doc| (stripped_version(doc), &doc["versions"]));
    if let Some((version, versions)) = &cached {
        if want.is_empty() || *version == want {
            return Ok(json!({"ok": true, "slug": slug, "versions": versions, "cached": true}));
        }
    }
    let ask = Collect {
        plugin: "uefn-ducky-store",
        event: "item-versions",
        unavailable_code: "store_unavailable",
        error_code: "store_error",
        allow_anonymous: true,
        timeout: Duration::from_secs(20),
    };
    let payload = match collect(&api, ask, &json!({"slug": slug})).await {
        Ok(payload) => payload,
        Err(refused) => {
            return Ok(match cached {
                Some((_, versions)) => json!({"ok": true, "slug": slug, "versions": versions, "cached": true, "error": refused.message, "code": refused.code}),
                None => json!({"ok": false, "error": refused.message, "code": refused.code, "versions": []}),
            })
        }
    };
    let rows = payload.get("versions").and_then(Value::as_array).map(Vec::as_slice).unwrap_or_default();
    let note = |row: &Value| {
        let (version, created) = (stripped(row.get("version")), stripped(row.get("created_at")));
        let created = if created.is_empty() { Value::Null } else { created.into() };
        (row.is_object() && !version.is_empty()).then(|| json!({"version": version, "changelog": stripped(row.get("changelog")), "created_at": created}))
    };
    let versions: Vec<Value> = rows.iter().filter_map(note).collect();
    let newest = versions.first().map(|row| pystr(&row["version"])).unwrap_or_default();
    let doc = json!({"version": if want.is_empty() { newest } else { want }, "versions": versions});
    // The check runs every 10 minutes per window and nearly always gets the same notes.
    if let Some(store) = store.filter(|_| !kept.as_ref().is_some_and(|kept| py_eq(kept, &doc))) {
        let _ = kv::set_doc(store, "cache_docs", key, doc.clone()).await;
    }
    Ok(json!({"ok": true, "slug": slug, "versions": doc["versions"]}))
}

/// `str(doc.get("version") or "")` of a kept copy.
fn stripped_version(doc: &Value) -> String {
    if truthy(&doc["version"]) {
        pystr(&doc["version"])
    } else {
        String::new()
    }
}
