//! The sidebar's folders and chats (`frontend/ui_web/panel_chats/folders.py` on top of
//! `frontend/ui_web/chats/`): every ducky, group and folder is in one list.

use std::cmp::Ordering;
use std::collections::HashSet;
use std::sync::atomic::{AtomicBool, Ordering::Relaxed};
use std::sync::Arc;

use serde_json::{json, Map, Value};

use super::chat_doc::{custom_duckies, ducky_style, file_path, is_placeholder, normalise, sort_order, text};
use super::router::Api;
use super::settings::{failed, imported};
use super::uuid4;
use crate::store::chat_messages::Save;
use crate::store::chats::Chats;
use crate::store::duckies::Who;
use crate::store::now;
use crate::store::pyjson::{float, int, pystr, truthy};

mod moves;
mod teams;
pub use moves::{delete_conversation, delete_folder, move_conversation};

type Fields = Map<String, Value>;

/// Where every chat and folder is stored (`chats.store.ONE_LIST_SLUG`).
const ONE_LIST: &str = "_no_project";
const ARCHIVE: &str = "archive";
const ARCHIVE_SORT_ORDER: f64 = 1_000_000.0;

fn is_archive(folder_id: &str) -> bool {
    folder_id.trim() == ARCHIVE
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

/// The account's chats and folders, for one call.
struct Sidebar<'a> {
    chats: &'a Chats,
    who: Who,
    /// The custom duckies that still have their picture.
    custom: HashSet<String>,
}

async fn sidebar(api: &Api) -> Result<Sidebar<'_>, String> {
    imported(api, "chats", "chats").await?;
    let custom = custom_duckies(api.repos.data_dir()).await;
    Ok(Sidebar { chats: api.repos.chats().await.map_err(failed)?, who: api.repos.who().await.map_err(failed)?, custom })
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

    /// `chats.folders.create_folder`: a new folder, last among its siblings.
    async fn new_folder(&self, name: &str, parent_id: String, group_hub_id: String) -> Result<Folder, String> {
        let mut folders = self.folders().await?;
        let last = folders.iter().filter(|folder| folder.parent_id == parent_id).map(|folder| folder.sort_order).fold(-1.0, f64::max);
        let name = if name.trim().is_empty() { "Folder" } else { name.trim() }.to_string();
        let folder = Folder { id: uuid4(), name, parent_id, sort_order: last + 1.0, group_hub_id };
        folders.push(folder.clone());
        self.save(&folders).await?;
        Ok(folder)
    }

    /// `merge_into_one_list`: rows an older version stored per project move into the
    /// one list first.
    /// ponytail: the move (rows and each chat's attachment folder) is the Python
    /// backend's; port it with the attachments, until then such a list is left to Python.
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

    /// `load_conversation`, without its messages: the chat on any island, None when unknown.
    async fn chat(&self, conv_id: &str) -> Result<Option<Map<String, Value>>, String> {
        let stored = self.chats.conv_get(self.who.clone(), conv_id.to_string(), None, false).await.map_err(failed)?;
        Ok(stored.map(|doc| normalise(&doc, &self.custom)))
    }

    /// `save_conversation` for a chat read in this call: its names and placement are
    /// stored where the chat is, its messages and counts are left alone.
    /// ponytail: nothing is merged with a newer stored copy (`_keep_newer_settings`,
    /// `_keep_newer_placement`, a newer lane in `_keep_stored_lanes`): those are for a
    /// copy held across a turn, which the agent runner needs when it moves here.
    async fn save_chat(&self, mut chat: Map<String, Value>, touch_updated: bool) -> Result<Map<String, Value>, String> {
        if chat["is_group"] == true {
            name_lanes(&mut chat["group_members"]);
        }
        if touch_updated {
            chat.insert("updated".into(), json!(now()));
        }
        let stored_in = self.chats.conv_project_id(self.who.id.clone(), pystr(&chat["id"])).await.map_err(failed)?;
        let project_id = stored_in.unwrap_or_else(|| ONE_LIST.to_string());
        let save = Save { project_id, doc: chat.clone(), messages: None, dirty_from: None, expect_rev: None, base_hashes: None, dirty_rows: Vec::new() };
        self.chats.conv_save(self.who.clone(), save).await.map_err(failed)?;
        Ok(chat)
    }

    /// `ensure_group_folder_hubs`: a group from before group folders gets its folder,
    /// named as the group and where its chat was. One in Archive is a deleted group's
    /// leftover and stays as it is.
    async fn link_groups(&self) -> Result<(), String> {
        let mut linked: HashSet<String> = self.folders().await?.into_iter().map(|folder| folder.group_hub_id).collect();
        for stored in self.chats().await? {
            let (id, parent) = (pystr(&stored["id"]), text(&stored["folder_id"]).trim().to_string());
            if !truthy(&stored["is_group"]) || linked.contains(&id) || is_archive(&parent) {
                continue;
            }
            let mut chat = normalise(&stored, &self.custom);
            let title = pystr(&chat["title"]);
            let folder = self.new_folder(if title.is_empty() { "Group" } else { &title }, parent, id.clone()).await?;
            chat.insert("folder_id".into(), json!(folder.id));
            self.save_chat(chat, true).await?;
            linked.insert(id);
        }
        Ok(())
    }

    /// The chats for a list; the first list of this process numbers them first
    /// (`_maybe_migrate_conversation_sort_orders`).
    async fn numbered_chats(&self) -> Result<Vec<Value>, String> {
        let chats = self.chats().await?;
        if ORDER_CHECKED.load(Relaxed) {
            return Ok(chats);
        }
        let moved = number_unsorted_chats(&chats);
        for (index, order) in &moved {
            let mut chat = normalise(&chats[*index], &self.custom);
            chat.insert("sort_order".into(), json!(order));
            self.save_chat(chat, false).await?;
        }
        ORDER_CHECKED.store(true, Relaxed);
        if moved.is_empty() { Ok(chats) } else { self.chats().await }
    }
}

/// `keep_stored_lanes` for a group read in this call: every saved member row names its
/// four lane fields (null when it has none); rows sharing an id share the last one's.
fn name_lanes(members: &mut Value) {
    let id = |row: &Value| row.get("member_conv_id").filter(|id| truthy(id)).map(pystr).unwrap_or_default();
    let Some(rows) = members.as_array_mut() else { return };
    let stored = rows.clone();
    for row in rows.iter_mut().filter(|row| row.is_object() && !id(row).is_empty()) {
        let lanes = stored.iter().rfind(|other| other.is_object() && id(other) == id(row)).cloned().unwrap_or_default();
        for key in ["write_allowed", "lane_set_by", "lane_set_at", "lane_root"] {
            row[key] = lanes[key].clone();
        }
    }
}

/// Checked once per process, as `_maybe_migrate_conversation_sort_orders` is.
static ORDER_CHECKED: AtomicBool = AtomicBool::new(false);

/// `_migrate_conversation_sort_orders`: chats of one folder that are all still at 0 are
/// numbered, the newest first. Gives `(index, new sort order)` of each chat that moves.
/// ponytail: Python saves the first one too (0 again); that save changes nothing.
fn number_unsorted_chats(chats: &[Value]) -> Vec<(usize, f64)> {
    let folder = |chat: &Value| text(&chat["folder_id"]);
    let mut folders: Vec<String> = Vec::new();
    for chat in chats {
        if !folders.contains(&folder(chat)) {
            folders.push(folder(chat));
        }
    }
    let mut moved = Vec::new();
    for name in folders {
        let mut inside: Vec<usize> = (0..chats.len()).filter(|index| folder(&chats[*index]) == name).collect();
        if inside.iter().all(|index| sort_order(&chats[*index]["sort_order"]) == 0.0) {
            let updated = |index: &usize| float(&chats[*index]["updated"]).unwrap_or(0.0);
            inside.sort_by(|a, b| by_order(updated(b), updated(a)));
            moved.extend(inside.into_iter().enumerate().skip(1).map(|(place, index)| (index, place as f64)));
        }
    }
    moved
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

/// The chats `keep` picks, as sidebar rows in `_sort_conversations` order.
fn rows(mut chats: Vec<Value>, custom: &HashSet<String>, keep: impl Fn(&Value) -> bool) -> Value {
    chats.retain(keep);
    let mut rows: Vec<Value> = chats.iter().map(|chat| chat_row(chat, custom)).collect();
    let key = |row: &Value| (row["sort_order"].as_f64().unwrap_or(0.0), -row["updated"].as_f64().unwrap_or(0.0), pystr(&row["id"]));
    rows.sort_by(|a, b| {
        let (a, b) = (key(a), key(b));
        by_order(a.0, b.0).then(by_order(a.1, b.1)).then(a.2.cmp(&b.2))
    });
    Value::Array(rows)
}

/// A new title, unless it is a placeholder and the chat has a chosen name:
/// `save_conversation` never saves "New ducky" over one.
fn retitle(chat: &mut Map<String, Value>, title: &str) {
    if !is_placeholder(title) || is_placeholder(&pystr(&chat["title"])) {
        chat.insert("title".into(), json!(title));
    }
}

/// `notify_chats_changed(conv.id, conv.title, conv.folder_id, open_tab=False)`.
fn chat_changed(chat: &Map<String, Value>) -> Value {
    json!({"type": "chats_changed", "conv_id": chat["id"], "title": chat["title"], "folder_id": chat["folder_id"], "open": false})
}

// --------------------------------------------------------------------------- methods

pub async fn list_folders(api: Arc<Api>, _args: Fields) -> Result<Value, String> {
    let sidebar = sidebar(&api).await?;
    sidebar.one_list().await?;
    sidebar.link_groups().await?;
    // Loaded again after the group check, as Python does: the load after the one that
    // added Archive numbers the folders.
    Ok(sidebar.folders().await?.iter().map(Folder::row).collect())
}

pub async fn list_all_conversations(api: Arc<Api>, _args: Fields) -> Result<Value, String> {
    let sidebar = sidebar(&api).await?;
    sidebar.one_list().await?;
    Ok(rows(sidebar.numbered_chats().await?, &sidebar.custom, |_| true))
}

pub async fn list_conversations(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let sidebar = sidebar(&api).await?;
    // None lists every chat; a folder id that is no text matches none.
    let folder = args.get("folder_id").cloned().unwrap_or_default();
    let inside = |chat: &Value| folder.is_null() || folder.as_str() == Some(text(&chat["folder_id"]).as_str());
    Ok(rows(sidebar.numbered_chats().await?, &sidebar.custom, inside))
}

pub async fn list_conversations_for_file(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let target = file_path(args.get("file_path").and_then(Value::as_str).unwrap_or_default());
    if target.is_empty() {
        return Ok(json!([]));
    }
    let sidebar = sidebar(&api).await?;
    Ok(rows(sidebar.chats().await?, &sidebar.custom, |chat| file_path(&text(&chat["file_path"])) == target))
}

pub async fn create_folder(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let name = args.get("name").and_then(Value::as_str).ok_or("a folder needs a name")?;
    let parent_id = text(args.get("parent_id").unwrap_or(&Value::Null));
    if is_archive(&parent_id) {
        return Err("Cannot create folders inside Archive".to_string());
    }
    let folder = sidebar(&api).await?.new_folder(name, parent_id, String::new()).await?;
    Ok(json!({"id": folder.id, "name": folder.name}))
}

pub async fn rename_folder(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let name = args.get("name").and_then(Value::as_str).ok_or("a folder needs a name")?.trim();
    let folder_id = text(args.get("folder_id").unwrap_or(&Value::Null));
    if is_archive(&folder_id) {
        return Err("Cannot rename the Archive folder".to_string());
    }
    let sidebar = sidebar(&api).await?;
    let mut folders = sidebar.folders().await?;
    let mut group = None;
    if let Some(folder) = folders.iter_mut().find(|folder| folder.id == folder_id) {
        if !name.is_empty() {
            folder.name = name.to_string();
        }
        group = Some((folder.group_hub_id.clone(), folder.name.clone())).filter(|(hub, name)| !hub.is_empty() && !name.is_empty());
    }
    sidebar.save(&folders).await?;
    // A group's folder and its chat share one name.
    if let Some((hub_id, name)) = group {
        if let Some(mut hub) = sidebar.chat(&hub_id).await?.filter(|hub| hub["is_group"] == true) {
            retitle(&mut hub, &name);
            sidebar.save_chat(hub, true).await?;
        }
    }
    Ok(Value::Null)
}

pub async fn rename_conversation(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let title = args.get("title").and_then(Value::as_str).ok_or("a chat needs a title")?.trim();
    let conv_id = text(args.get("conv_id").unwrap_or(&Value::Null));
    let sidebar = sidebar(&api).await?;
    let Some(mut chat) = sidebar.chat(&conv_id).await? else { return Ok(Value::Null) };
    if !title.is_empty() {
        retitle(&mut chat, title);
    }
    // Other windows, @ mentions and search show the new name at once.
    api.publish(vec![chat_changed(&sidebar.save_chat(chat, true).await?)]);
    Ok(Value::Null)
}

/// Mute or unmute a chat or a group. Only the flag is stored: its agent keeps running.
pub async fn set_muted(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let conv_id = text(args.get("conv_id").unwrap_or(&Value::Null));
    let sidebar = sidebar(&api).await?;
    let Some(mut chat) = sidebar.chat(conv_id.trim()).await? else { return Ok(json!({"ok": false, "error": "Conversation not found"})) };
    chat.insert("muted".into(), truthy(args.get("muted").unwrap_or(&Value::Null)).into());
    let chat = sidebar.save_chat(chat, true).await?;
    api.publish(vec![chat_changed(&chat)]);
    Ok(json!({"ok": true, "id": chat["id"], "muted": chat["muted"], "is_group": chat["is_group"]}))
}

/// `{ok, chats: [{id, title, updated, chats, group}]}`: what was made on this PC signed
/// out, for the account signed in now. Never an error: the sidebar section just isn't there.
pub async fn list_local_chats(api: Arc<Api>, _args: Fields) -> Result<Value, String> {
    let sidebar = sidebar(&api).await?;
    let waiting = sidebar.chats.waiting(sidebar.who.id.clone()).await.unwrap_or_default();
    Ok(json!({"ok": true, "chats": waiting}))
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
    fn chats_of_a_folder_that_are_all_at_zero_are_numbered_newest_first() {
        let chat = |id: &str, folder: &str, order: f64, updated: f64| json!({"id": id, "folder_id": folder, "sort_order": order, "updated": updated});
        let chats = [chat("a", "", 0.0, 1.0), chat("b", "f", 0.0, 5.0), chat("c", "", 0.0, 9.0), chat("d", "f", 2.0, 1.0), chat("e", "", 0.0, 1.0), chat("f", "g", 0.0, 1.0)];
        // "" is c, a, e (the tie keeps its order); f has a numbered chat; g has one chat.
        assert_eq!(number_unsorted_chats(&chats), [(0, 1.0), (4, 2.0)]);
        assert_eq!(number_unsorted_chats(&chats[3..4]), []);
    }

    #[test]
    fn a_placeholder_title_never_replaces_a_chosen_one() {
        let mut chat = normalise(&json!({"id": "c", "title": "My plan", "folder_id": "f"}), &HashSet::new());
        retitle(&mut chat, "New ducky 2");
        assert_eq!(chat["title"], "My plan");
        retitle(&mut chat, "Group");
        assert_eq!(chat["title"], "My plan");
        retitle(&mut chat, "Better plan");
        assert_eq!(chat_changed(&chat), json!({"type": "chats_changed", "conv_id": "c", "title": "Better plan", "folder_id": "f", "open": false}));
        chat.insert("title".into(), json!("Chat"));
        retitle(&mut chat, "New ducky 2");
        assert_eq!(chat["title"], "New ducky 2");
    }

    #[test]
    fn a_saved_group_names_each_members_lane_fields() {
        let mut members = json!([{"member_conv_id": "a", "write_allowed": ["x"]}, "junk", {"name": "no id"}, {"member_conv_id": "a", "lane_set_at": 5.0}]);
        name_lanes(&mut members);
        let last = json!({"member_conv_id": "a", "lane_set_at": 5.0, "write_allowed": null, "lane_set_by": null, "lane_root": null});
        assert_eq!(members, json!([{"member_conv_id": "a", "write_allowed": null, "lane_set_by": null, "lane_set_at": 5.0, "lane_root": null}, "junk", {"name": "no id"}, last]));
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
        assert_eq!((&row["provider"], &row["is_subagent"], &row["ducky_style"]), (&json!("anthropic"), &json!(false), &json!(super::super::chat_doc::BUNDLED.1)));
        assert_eq!(row.as_object().unwrap().len(), 26);
    }
}
