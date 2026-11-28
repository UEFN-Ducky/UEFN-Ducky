//! The sidebar's folders and chats (`frontend/ui_web/panel_chats/folders.py` on top of
//! `frontend/ui_web/chats/`): every ducky, group and folder is in one list.

use std::cmp::Ordering;
use std::collections::HashSet;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering::Relaxed};
use std::sync::{Arc, LazyLock};

use serde_json::{json, Map, Value};

use super::router::Api;
use super::settings::{failed, imported};
use super::uuid4;
use crate::store::chats::Chats;
use crate::store::duckies::Who;
use crate::store::pyjson::{float, int, pystr, truthy};

type Fields = Map<String, Value>;

/// Where every chat and folder is stored (`chats.store.ONE_LIST_SLUG`).
const ONE_LIST: &str = "_no_project";
const ARCHIVE: &str = "archive";
const ARCHIVE_SORT_ORDER: f64 = 1_000_000.0;

/// The duckies in the build: `(ids, the default style)` (`frontend/ducky_assets.py`).
/// ponytail: only `bundled_duckies.json` is read; Python's fallback scan of the web
/// bundle's `duckies/` folder is for a checkout without that file.
static BUNDLED: LazyLock<(HashSet<String>, String)> = LazyLock::new(|| {
    let file: Value = serde_json::from_str(include_str!("../../../../../ducky_app/frontend/bundled_duckies.json")).unwrap_or_default();
    let field = |item: &Value, name: &str| item.get(name).map(|v| pystr(v).trim().to_string()).unwrap_or_default();
    let listed = file["duckies"].as_array().into_iter().flatten().filter(|item| item.is_object());
    let ids: Vec<String> = listed.filter(|item| !field(item, "file").is_empty()).map(|item| field(item, "id")).filter(|id| !id.is_empty()).collect();
    let default = field(&file, "default_style");
    let default = if default.is_empty() { ids.first().cloned().unwrap_or_else(|| "classic".into()) } else { default };
    (ids.into_iter().collect(), default)
});

/// `str(value or "")`.
fn text(value: &Value) -> String {
    if truthy(value) { pystr(value) } else { String::new() }
}

fn is_archive(folder_id: &str) -> bool {
    folder_id.trim() == ARCHIVE
}

/// `float(raw) if raw is not None and raw != "" else 0.0`.
fn sort_order(raw: &Value) -> f64 {
    match raw {
        Value::Null => 0.0,
        Value::String(text) if text.is_empty() => 0.0,
        Value::String(text) => text.trim().parse().unwrap_or(0.0),
        other => float(other).unwrap_or(0.0),
    }
}

/// `ducky_assets.normalize_ducky_style`: a bundled id, a custom ducky that still has its
/// picture (`custom`, the slugs found in the data folder), else the default.
fn ducky_style(raw: &str, custom: &HashSet<String>) -> String {
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
async fn custom_duckies(data_dir: &Path) -> HashSet<String> {
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

// --------------------------------------------------------------------------- folders

/// `ChatFolder`: one sidebar folder; with `group_hub_id` it is a group.
#[derive(Clone, Debug, PartialEq)]
struct Folder {
    id: String,
    name: String,
    parent_id: String,
    sort_order: f64,
    group_hub_id: String,
}

impl Folder {
    /// `ChatFolder.from_dict`.
    fn from(row: &Value) -> Folder {
        Folder {
            id: row.get("id").map(pystr).unwrap_or_default(),
            name: row.get("name").map_or("Folder".to_string(), pystr),
            parent_id: text(&row["parent_id"]),
            sort_order: sort_order(&row["sort_order"]),
            group_hub_id: text(&row["group_hub_id"]).trim().to_string(),
        }
    }

    /// `ChatFolder.to_dict`, which is the sidebar's row too.
    fn row(&self) -> Value {
        json!({"id": self.id, "name": self.name, "parent_id": self.parent_id, "sort_order": self.sort_order, "group_hub_id": self.group_hub_id})
    }
}

/// `ensure_archive_folder`: Archive is there, at the top level, named and last. True
/// when something changed.
fn ensure_archive(folders: &mut Vec<Folder>) -> bool {
    let Some(archive) = folders.iter_mut().find(|folder| folder.id == ARCHIVE) else {
        folders.push(Folder { id: ARCHIVE.into(), name: "Archive".into(), parent_id: String::new(), sort_order: ARCHIVE_SORT_ORDER, group_hub_id: String::new() });
        return true;
    };
    let fixed = Folder { name: "Archive".into(), parent_id: String::new(), sort_order: archive.sort_order.max(ARCHIVE_SORT_ORDER), ..archive.clone() };
    let changed = *archive != fixed;
    *archive = fixed;
    changed
}

/// `_migrate_folder_sort_orders`: siblings that are all still at 0 get their index.
fn number_unsorted_siblings(folders: &mut [Folder]) -> bool {
    let mut parents: Vec<&str> = folders.iter().map(|folder| folder.parent_id.as_str()).collect();
    parents.dedup();
    let parents: HashSet<String> = parents.into_iter().map(str::to_string).collect();
    let mut changed = false;
    for parent in parents {
        let siblings = || folders.iter().filter(|folder| folder.parent_id == parent);
        if siblings().count() > 1 && siblings().all(|folder| folder.sort_order == 0.0) {
            for (index, folder) in folders.iter_mut().filter(|folder| folder.parent_id == parent).enumerate() {
                folder.sort_order = index as f64;
            }
            changed = true;
        }
    }
    changed
}

fn by_order(a: f64, b: f64) -> Ordering {
    a.partial_cmp(&b).unwrap_or(Ordering::Equal)
}

/// The account's chats, once the Python backend's one-time moves are done.
struct Sidebar<'a> {
    chats: &'a Chats,
    who: Who,
}

async fn sidebar(api: &Api) -> Result<Sidebar<'_>, String> {
    imported(api, "chats", "chats").await?;
    Ok(Sidebar { chats: api.repos.chats().await.map_err(failed)?, who: api.repos.who().await.map_err(failed)? })
}

impl Sidebar<'_> {
    /// `chats.folders.load_folders`: the folders in sidebar order, Archive among them.
    async fn folders(&self) -> Result<Vec<Folder>, String> {
        let rows = self.chats.folders_get(self.who.clone(), ONE_LIST.into()).await.map_err(failed)?;
        let mut folders: Vec<Folder> = rows.iter().map(Folder::from).collect();
        // As Python's `or`: a load that put Archive right numbers nothing, the next one does.
        let added = ensure_archive(&mut folders);
        if added || number_unsorted_siblings(&mut folders) {
            self.save(&folders).await?;
        }
        folders.sort_by(|a, b| (&a.parent_id, &a.id).cmp(&(&b.parent_id, &b.id)));
        folders.sort_by(|a, b| a.parent_id.cmp(&b.parent_id).then(by_order(a.sort_order, b.sort_order)));
        Ok(folders)
    }

    async fn save(&self, folders: &[Folder]) -> Result<(), String> {
        let rows = folders.iter().map(Folder::row).collect();
        self.chats.folders_replace(self.who.clone(), ONE_LIST.into(), rows).await.map_err(failed)
    }

    /// `merge_into_one_list`: rows an older version stored per project move into the
    /// one list first.
    /// ponytail: the move (rows and each chat's attachment folder) is the Python
    /// backend's; port it with the chat saves, until then such a list is left to Python.
    async fn one_list(&self) -> Result<(), String> {
        match self.chats.rows_outside(self.who.id.clone(), ONE_LIST.into()).await.map_err(failed)? {
            true => Err("chats are not merged into one list yet".to_string()),
            false => Ok(()),
        }
    }

    /// The chats, without their messages, in stored order.
    async fn chats(&self) -> Result<Vec<Value>, String> {
        self.chats.conv_list(self.who.clone(), ONE_LIST.into(), false).await.map_err(failed)
    }
}

/// `ensure_group_folder_hubs`: a group from before group folders has no folder yet.
/// ponytail: making that folder saves the chat, which the Python backend still does;
/// port it with the chat saves.
fn groups_have_folders(folders: &[Folder], chats: &[Value]) -> Result<(), String> {
    let linked: HashSet<&str> = folders.iter().map(|folder| folder.group_hub_id.as_str()).collect();
    let unlinked = |chat: &&Value| truthy(&chat["is_group"]) && !linked.contains(pystr(&chat["id"]).as_str()) && !is_archive(&text(&chat["folder_id"]));
    match chats.iter().find(unlinked) {
        Some(_) => Err("a group has no folder yet".to_string()),
        None => Ok(()),
    }
}

/// Checked once per process, as `_maybe_migrate_conversation_sort_orders` is.
static ORDER_CHECKED: AtomicBool = AtomicBool::new(false);

/// `_migrate_conversation_sort_orders`: chats in one folder that are all still at 0 get
/// numbered by age.
/// ponytail: numbering saves each chat, which the Python backend still does; port it
/// with the chat saves.
fn chats_are_ordered(chats: &[Value]) -> Result<(), String> {
    if ORDER_CHECKED.load(Relaxed) {
        return Ok(());
    }
    let folders: HashSet<String> = chats.iter().map(|chat| text(&chat["folder_id"])).collect();
    for folder in folders {
        let inside = || chats.iter().filter(|chat| text(&chat["folder_id"]) == folder);
        if inside().count() > 1 && inside().all(|chat| sort_order(&chat["sort_order"]) == 0.0) {
            return Err("chats are not numbered yet".to_string());
        }
    }
    ORDER_CHECKED.store(true, Relaxed);
    Ok(())
}

// --------------------------------------------------------------------------- chat rows

/// `resolve_context_window_tokens`: the stored last-step size when it can be trusted,
/// else an estimate from the last call's token buckets.
fn context_window(stored: i64, input: i64, cache_read: i64, cache_write: i64, turns: i64) -> i64 {
    let [stored, input, cache_read, cache_write, turns] = [stored, input, cache_read, cache_write, turns].map(|n| n.max(0));
    let naive = input + cache_read + cache_write;
    let (larger, smaller) = (input.max(cache_read), input.min(cache_read));
    let estimated = if naive <= 0 {
        0
    } else if turns > 1 {
        ((naive + turns - 1) / turns).max(1)
    } else if smaller > 0 && smaller * 5 >= larger * 4 {
        // A run's totals often have input ≈ cache read: the larger one is the window.
        larger + cache_write
    } else {
        naive
    };
    if stored > 0 && naive > 0 && stored < naive {
        return stored;
    }
    if naive > 0 && (cache_read > 0 || cache_write > 0) && estimated > 0 && (stored == 0 || stored >= naive) {
        return estimated;
    }
    if stored > 0 { stored } else { estimated }
}

/// `_sidebar_context_tokens`.
fn context_tokens(chat: &Value) -> i64 {
    let count = |from: &Value, name: &str| from.get(name).filter(|n| truthy(n)).map_or(0, int);
    let stats = chat.get("coding_agent_stats").filter(|stats| stats.is_object()).unwrap_or(&Value::Null);
    let (stored, turns) = (count(stats, "context_tokens"), count(stats, "num_turns"));
    let usage = chat.get("token_usage").filter(|usage| usage.is_object()).unwrap_or(&Value::Null);
    match usage["calls"].as_array().and_then(|calls| calls.last()).filter(|last| last.is_object()) {
        Some(last) => context_window(stored, count(last, "input_tokens"), count(last, "cache_read_tokens"), count(last, "cache_write_tokens"), turns),
        None => stored.max(0),
    }
}

/// `_conversation_sidebar_row` of `Conversation.from_dict(chat)`.
fn chat_row(chat: &Value, custom: &HashSet<String>) -> Value {
    let field = |name: &str| text(&chat[name]);
    let trimmed = |name: &str| field(name).trim().to_string();
    let members: Vec<&Value> = chat["group_members"].as_array().into_iter().flatten().filter(|member| member.is_object()).collect();
    let coding_agent = field("coding_agent");
    json!({
        "id": chat.get("id").map(pystr).unwrap_or_default(),
        "title": chat.get("title").map_or("New ducky".to_string(), pystr),
        "sort_order": sort_order(&chat["sort_order"]),
        "updated": float(&chat["updated"]).unwrap_or(0.0),
        "ducky_style": ducky_style(&field("ducky_style"), custom),
        "ducky_name": field("ducky_name"),
        "profile_id": trimmed("profile_id"),
        "ducky_personality": field("ducky_personality"),
        "tts_voice": trimmed("tts_voice"),
        "tts_speed": float(&chat["tts_speed"]).unwrap_or(0.0),
        "file_path": file_path(&field("file_path")),
        "model": field("model"),
        "provider": chat.get("provider").map_or("anthropic".to_string(), pystr),
        "coding_agent": if coding_agent.is_empty() { "ducky".to_string() } else { coding_agent },
        "thinking_effort": trimmed("thinking_effort").to_lowercase(),
        "terminal_session_id": field("terminal_session_id"),
        "folder_id": field("folder_id"),
        "parent_conv_id": trimmed("parent_conv_id"),
        "is_group": truthy(&chat["is_group"]),
        "muted": truthy(&chat["muted"]),
        // Sub-agent seats are retired: a group's members are filed under its folder.
        "is_subagent": false,
        "leader_conv_id": trimmed("leader_conv_id"),
        "group_members": members,
        "tool_call_count": int(&chat["tool_call_count"]),
        "file_count": int(&chat["file_count"]),
        "context_tokens": context_tokens(chat),
    })
}

/// `norm_file_path`.
fn file_path(path: &str) -> String {
    path.trim().replace('\\', "/")
}

/// The chats `keep` picks, as sidebar rows in `_sort_conversations` order.
async fn rows(api: &Api, mut chats: Vec<Value>, keep: impl Fn(&Value) -> bool) -> Value {
    let custom = custom_duckies(api.repos.data_dir()).await;
    chats.retain(keep);
    let mut rows: Vec<Value> = chats.iter().map(|chat| chat_row(chat, &custom)).collect();
    let key = |row: &Value| (row["sort_order"].as_f64().unwrap_or(0.0), -row["updated"].as_f64().unwrap_or(0.0), pystr(&row["id"]));
    rows.sort_by(|a, b| {
        let (a, b) = (key(a), key(b));
        by_order(a.0, b.0).then(by_order(a.1, b.1)).then(a.2.cmp(&b.2))
    });
    Value::Array(rows)
}

// --------------------------------------------------------------------------- methods

pub async fn list_folders(api: Arc<Api>, _args: Fields) -> Result<Value, String> {
    let sidebar = sidebar(&api).await?;
    sidebar.one_list().await?;
    groups_have_folders(&sidebar.folders().await?, &sidebar.chats().await?)?;
    // Loaded again, as Python does after its group check: the load after the one that
    // added Archive numbers the folders.
    Ok(sidebar.folders().await?.iter().map(Folder::row).collect())
}

pub async fn list_all_conversations(api: Arc<Api>, _args: Fields) -> Result<Value, String> {
    let sidebar = sidebar(&api).await?;
    sidebar.one_list().await?;
    let chats = sidebar.chats().await?;
    chats_are_ordered(&chats)?;
    Ok(rows(&api, chats, |_| true).await)
}

pub async fn list_conversations(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let chats = sidebar(&api).await?.chats().await?;
    chats_are_ordered(&chats)?;
    // None lists every chat; a folder id that is no text matches none.
    let folder = args.get("folder_id").cloned().unwrap_or_default();
    Ok(rows(&api, chats, |chat| folder.is_null() || folder.as_str() == Some(text(&chat["folder_id"]).as_str())).await)
}

pub async fn list_conversations_for_file(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let target = file_path(args.get("file_path").and_then(Value::as_str).unwrap_or_default());
    if target.is_empty() {
        return Ok(json!([]));
    }
    let chats = sidebar(&api).await?.chats().await?;
    Ok(rows(&api, chats, |chat| file_path(&text(&chat["file_path"])) == target).await)
}

pub async fn create_folder(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let name = args.get("name").and_then(Value::as_str).ok_or("a folder needs a name")?.trim();
    let parent_id = text(args.get("parent_id").unwrap_or(&Value::Null));
    if is_archive(&parent_id) {
        return Err("Cannot create folders inside Archive".to_string());
    }
    let sidebar = sidebar(&api).await?;
    let mut folders = sidebar.folders().await?;
    let last = folders.iter().filter(|folder| folder.parent_id == parent_id).map(|folder| folder.sort_order).fold(-1.0, f64::max);
    let name = if name.is_empty() { "Folder" } else { name }.to_string();
    let folder = Folder { id: uuid4(), name, parent_id, sort_order: last + 1.0, group_hub_id: String::new() };
    folders.push(folder.clone());
    sidebar.save(&folders).await?;
    Ok(json!({"id": folder.id, "name": folder.name}))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn folder(id: &str, parent: &str, sort_order: f64) -> Folder {
        Folder { id: id.into(), name: id.to_uppercase(), parent_id: parent.into(), sort_order, group_hub_id: String::new() }
    }

    #[test]
    fn a_folder_row_is_read_and_written_as_python_does() {
        let read = Folder::from(&json!({"id": 7, "parent_id": null, "sort_order": "2.5", "group_hub_id": " hub "}));
        assert_eq!(read, Folder { id: "7".into(), name: "Folder".into(), parent_id: "".into(), sort_order: 2.5, group_hub_id: "hub".into() });
        assert_eq!(Folder::from(&json!({"name": null, "sort_order": ""})).name, "None");
        assert_eq!(read.row(), json!({"id": "7", "name": "Folder", "parent_id": "", "sort_order": 2.5, "group_hub_id": "hub"}));
    }

    #[test]
    fn archive_is_added_or_put_right_and_unsorted_siblings_are_numbered() {
        let mut folders = vec![folder("a", "", 0.0), folder("b", "", 0.0), folder("c", "a", 0.0)];
        assert!(ensure_archive(&mut folders) && !ensure_archive(&mut folders));
        assert_eq!((folders[3].id.as_str(), folders[3].name.as_str(), folders[3].sort_order), ("archive", "Archive", 1_000_000.0));
        // Archive is a sibling at the top and is not at 0: nothing is numbered.
        assert!(!number_unsorted_siblings(&mut folders));
        folders.truncate(3);
        folders.push(folder("d", "a", 0.0));
        assert!(number_unsorted_siblings(&mut folders) && !number_unsorted_siblings(&mut folders));
        assert_eq!(folders.iter().map(|f| f.sort_order).collect::<Vec<_>>(), [0.0, 1.0, 0.0, 1.0]);

        let mut moved = vec![Folder { name: "Old".into(), ..folder("archive", "a", 3.0) }];
        assert!(ensure_archive(&mut moved));
        assert_eq!(moved[0], Folder { name: "Archive".into(), ..folder("archive", "", 1_000_000.0) });
    }

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
    fn the_context_size_is_the_stored_one_or_an_estimate() {
        assert_eq!(context_window(0, 0, 0, 0, 0), 0);
        assert_eq!(context_window(50, 0, 0, 0, 0), 50);
        assert_eq!(context_window(50, 100, 100, 10, 0), 50, "a real last step under the totals");
        assert_eq!(context_window(0, 100, 90, 10, 0), 110, "input about the cache read: the larger");
        assert_eq!(context_window(0, 100, 10, 10, 0), 120);
        assert_eq!(context_window(500, 100, 10, 10, 3), 40, "totals over three turns");
        assert_eq!(context_window(500, 100, 0, 0, 0), 500);
        let chat = json!({"coding_agent_stats": {"context_tokens": "70", "num_turns": null}, "token_usage": {"calls": [{}, {"input_tokens": 100, "cache_read_tokens": 100}]}});
        assert_eq!(context_tokens(&chat), 70);
        assert_eq!(context_tokens(&json!({"coding_agent_stats": {"context_tokens": -5}, "token_usage": {"calls": ["x"]}})), 0);
    }

    #[test]
    fn a_sidebar_row_has_pythons_defaults() {
        let row = chat_row(&json!({"id": "c", "title": "T", "coding_agent": "", "thinking_effort": " HIGH ", "file_path": " a\\b ", "group_members": [{"n": 1}, 2]}), &HashSet::new());
        assert_eq!(row["coding_agent"], "ducky");
        assert_eq!((&row["thinking_effort"], &row["file_path"], &row["group_members"]), (&json!("high"), &json!("a/b"), &json!([{"n": 1}])));
        assert_eq!((&row["provider"], &row["is_subagent"], &row["ducky_style"]), (&json!("anthropic"), &json!(false), &json!(BUNDLED.1)));
        assert_eq!(row.as_object().unwrap().len(), 26);
    }
}
