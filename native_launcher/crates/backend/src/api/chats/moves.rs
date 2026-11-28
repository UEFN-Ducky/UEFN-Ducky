//! Deleting folders and chats (`chats/folders.py::delete_folder`, `chats/groups.py::_remove_folder`,
//! `chats/conversations.py::delete_conversation`). A deleted group goes to Archive, with
//! its duckies when asked; a deleted chat takes the chats it started with it.
//!
//! ponytail: only what is stored changes here. A running agent, its terminals, its
//! "Allow everything" and its sandbox are stopped and cleared by the Python backend,
//! which answers these calls until the agent runner is in this process.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use serde_json::{json, Map, Value};

use super::{failed, is_archive, normalise, pystr, sidebar, text, truthy, Api, Fields, Folder, Sidebar, ARCHIVE, ONE_LIST};

/// `root` and everything under it, parents first; a loop in the data ends the walk.
fn tree(root: &str, children: impl Fn(&str) -> Vec<String>) -> Vec<String> {
    let (mut out, mut queue) = (Vec::new(), std::collections::VecDeque::from([root.to_string()]));
    while let Some(id) = queue.pop_front() {
        if !out.contains(&id) {
            queue.extend(children(&id));
            out.push(id);
        }
    }
    out
}

/// `folder_subtree_ids`: the folder and every folder nested in it; none when unknown.
fn subtree(folders: &[Folder], folder_id: &str) -> Vec<String> {
    if !folders.iter().any(|folder| folder.id == folder_id) {
        return Vec::new();
    }
    tree(folder_id, |id| folders.iter().filter(|folder| folder.parent_id == id).map(|folder| folder.id.clone()).collect())
}

/// `group_hub_ids_in`: the groups among these folders.
fn hubs_in(folders: &[Folder], ids: &[String]) -> Vec<String> {
    folders.iter().filter(|folder| ids.contains(&folder.id) && !folder.group_hub_id.is_empty()).map(|folder| folder.group_hub_id.clone()).collect()
}

fn field(chat: &Fields, name: &str) -> String {
    text(&chat[name])
}

fn put<const N: usize>(chat: &mut Fields, fields: [(&str, &str); N]) {
    for (name, value) in fields {
        chat.insert(name.into(), json!(value));
    }
}

/// `notify_chats_changed(open_tab=False, removed_conv_ids=…, removed_folder_ids=…)`:
/// every open window drops these rows at once.
fn removed(conv_ids: &[String], folder_ids: &[String]) -> Value {
    let mut event = Map::from_iter([("type".to_string(), json!("chats_changed")), ("open".to_string(), json!(false))]);
    for (name, ids) in [("removed_conv_ids", conv_ids), ("removed_folder_ids", folder_ids)] {
        if !ids.is_empty() {
            event.insert(name.into(), ids.iter().filter(|id| !id.is_empty()).cloned().collect());
        }
    }
    Value::Object(event)
}

/// A chat id that is safe as a folder name (`_CONV_ID_RE`).
fn safe_id(conv_id: &str) -> bool {
    let plain = |c: char| c.is_ascii_alphanumeric() || "._-".contains(c);
    (1..=80).contains(&conv_id.len()) && conv_id.chars().all(plain) && conv_id != "." && conv_id != ".."
}

/// `_remove_chat_folder`: a chat's own files (its attachments) go with it. A file another
/// program still reads gets about 1.5 s; leftovers of a chat that is gone are left behind.
#[allow(clippy::disallowed_methods)] // only ever run by spawn_blocking: file removal and its short waits
fn remove_chat_files(folders: Vec<PathBuf>) -> Result<(), String> {
    for folder in folders.iter().filter(|folder| folder.is_dir()) {
        let mut delay = Duration::from_millis(50);
        for attempt in 0..6 {
            match std::fs::remove_dir_all(folder) {
                Err(error) if folder.exists() && attempt < 5 => {
                    let _ = error;
                    std::thread::sleep(delay);
                    delay *= 2;
                }
                Err(error) if folder.join("conversation.json").exists() => return Err(format!("Couldn't delete the chat: a file in it is in use ({error})")),
                _ => break,
            }
        }
    }
    Ok(())
}

fn chat_folder(data_dir: &Path, conv_id: &str) -> PathBuf {
    data_dir.join("chats").join("projects").join(ONE_LIST).join("conversations").join(conv_id)
}

impl Sidebar<'_> {
    /// Every chat as `Conversation.from_dict(...).to_dict()`, without its messages.
    async fn docs(&self) -> Result<Vec<Fields>, String> {
        Ok(self.chats().await?.iter().map(|doc| normalise(doc, &self.custom)).collect())
    }

    /// `conversation_descendant_ids`: the chats this one started, and theirs, parents first.
    async fn descendants(&self, conv_id: &str) -> Result<Vec<String>, String> {
        let home = self.chats.conv_project_id(self.who.id.clone(), conv_id.to_string()).await.map_err(failed)?;
        let started = self.chats.conv_ids_by_parent(self.who.id.clone(), home.unwrap_or_else(|| ONE_LIST.into())).await.map_err(failed)?;
        let children = |id: &str| started.get(id).and_then(Value::as_array).into_iter().flatten().map(pystr).collect();
        Ok(tree(conv_id, children).split_off(1))
    }

    /// `chats.folders.delete_folder`: gives the groups that went to Archive.
    async fn remove_folder(&self, folder_id: &str, members_too: bool) -> Result<Vec<String>, String> {
        if is_archive(folder_id) {
            return Err("Cannot delete the Archive folder".to_string());
        }
        let folders = self.folders().await?;
        let Some(target) = folders.iter().find(|folder| folder.id == folder_id).cloned() else { return Ok(Vec::new()) };
        let mut gone = subtree(&folders, folder_id);
        let archived = match members_too {
            true => self.archive_inside(folders, &gone).await?,
            false => self.lift_out(&target, folders).await?,
        };
        if !archived.is_empty() || members_too {
            gone.extend(self.drop_emptied(target.parent_id.trim().to_string()).await?);
        }
        // Marked deleted, so a stale folder list can't bring them back.
        let still: HashSet<String> = self.folders().await?.into_iter().map(|folder| folder.id).collect();
        self.chats.folders_mark_deleted(gone.into_iter().filter(|id| !still.contains(id)).collect()).await.map_err(failed)?;
        Ok(archived)
    }

    /// The folder alone goes. What was in a plain folder surfaces at the top; what was in
    /// a group moves up to where the group was, on its own, and the group goes to Archive.
    async fn lift_out(&self, target: &Folder, mut folders: Vec<Folder>) -> Result<Vec<String>, String> {
        let hub = &target.group_hub_id;
        let up = if hub.is_empty() { "" } else { target.parent_id.trim() };
        folders.retain(|folder| folder.id != target.id);
        for folder in folders.iter_mut().filter(|folder| folder.parent_id == target.id) {
            folder.parent_id = up.to_string();
        }
        self.save(&folders).await?;
        for mut chat in self.docs().await? {
            let is_hub = !hub.is_empty() && field(&chat, "id") == *hub;
            let linked = !hub.is_empty() && field(&chat, "parent_conv_id").trim() == hub;
            let inside = field(&chat, "folder_id") == target.id;
            if is_hub {
                // Still a group row in Archive: Return to active gives it a folder again.
                put(&mut chat, [("folder_id", ARCHIVE), ("parent_conv_id", ""), ("leader_conv_id", "")]);
            } else if inside || linked {
                if inside {
                    put(&mut chat, [("folder_id", up)]);
                }
                if linked {
                    put(&mut chat, [("parent_conv_id", ""), ("leader_conv_id", "")]);
                }
            } else {
                continue;
            }
            self.save_chat(chat, true).await?;
        }
        Ok(if hub.is_empty() { Vec::new() } else { vec![hub.clone()] })
    }

    /// The folders `gone` and all in them go to Archive and keep their shape there: a
    /// ducky stays linked to its group, a nested group to the group around it.
    async fn archive_inside(&self, folders: Vec<Folder>, gone: &[String]) -> Result<Vec<String>, String> {
        let by_id: HashMap<&str, &Folder> = folders.iter().map(|folder| (folder.id.as_str(), folder)).collect();
        // The group a folder of `gone` belongs to: itself or its nearest group around it.
        let container = |folder_id: &str| {
            let (mut id, mut seen) = (folder_id.to_string(), HashSet::new());
            while let Some(folder) = by_id.get(id.as_str()).filter(|folder| gone.contains(&folder.id) && seen.insert(folder.id.clone())) {
                if !folder.group_hub_id.is_empty() {
                    return folder.group_hub_id.clone();
                }
                id = folder.parent_id.trim().to_string();
            }
            String::new()
        };
        let hubs = hubs_in(&folders, gone);
        let around: HashMap<&str, String> =
            folders.iter().filter(|folder| hubs.contains(&folder.group_hub_id) && gone.contains(&folder.id)).map(|folder| (folder.group_hub_id.as_str(), container(folder.parent_id.trim()))).collect();
        let kept: Vec<Folder> = folders.iter().filter(|folder| !gone.contains(&folder.id)).cloned().collect();
        self.save(&kept).await?;

        let docs = self.docs().await?;
        let parent = |chat: &Fields| field(chat, "parent_conv_id").trim().to_string();
        let mut seen: HashSet<String> = hubs.iter().cloned().collect();
        let mut queue: Vec<&Fields> = docs.iter().filter(|chat| gone.contains(&field(chat, "folder_id")) || hubs.contains(&parent(chat))).collect();
        while let Some(chat) = queue.pop() {
            let id = field(chat, "id");
            if !seen.insert(id.clone()) {
                continue;
            }
            // The chats it started come along.
            queue.extend(docs.iter().filter(|other| parent(other) == id));
            let mut member = chat.clone();
            if parent(chat).is_empty() && gone.contains(&field(chat, "folder_id")) {
                // A ducky that sat in the group's folder belongs to that group in Archive.
                put(&mut member, [("parent_conv_id", &container(&field(chat, "folder_id")))]);
            }
            put(&mut member, [("folder_id", ARCHIVE)]);
            self.save_chat(member, true).await?;
        }
        for chat in docs.iter().filter(|chat| hubs.contains(&field(chat, "id"))) {
            // Its leader stays, for Return to active; a nested group points at the one around it.
            let mut hub = chat.clone();
            put(&mut hub, [("folder_id", ARCHIVE), ("parent_conv_id", around.get(field(chat, "id").as_str()).map_or("", String::as_str))]);
            self.save_chat(hub, true).await?;
        }
        let mut hubs = hubs;
        hubs.sort();
        hubs.dedup();
        Ok(hubs)
    }

    /// `_drop_emptied_parents`: plain folders that held only what was just archived go
    /// too, the nearest first.
    async fn drop_emptied(&self, mut parent_id: String) -> Result<Vec<String>, String> {
        let mut dropped = Vec::new();
        while !parent_id.is_empty() && !is_archive(&parent_id) {
            let mut folders = self.folders().await?;
            let Some(parent) = folders.iter().find(|folder| folder.id == parent_id).cloned() else { break };
            let in_use = !parent.group_hub_id.is_empty() || folders.iter().any(|folder| folder.parent_id == parent_id);
            if in_use || self.chats().await?.iter().any(|chat| text(&chat["folder_id"]) == parent_id) {
                break;
            }
            folders.retain(|folder| folder.id != parent_id);
            self.save(&folders).await?;
            dropped.push(parent_id);
            parent_id = parent.parent_id.trim().to_string();
        }
        Ok(dropped)
    }
}

/// Delete a folder; the answer is the groups that went to Archive. A group always does;
/// `archive_members` sends what is in the folder there too, else that stays active.
pub async fn delete_folder(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let folder_id = text(args.get("folder_id").unwrap_or(&Value::Null));
    let members_too = truthy(args.get("archive_members").unwrap_or(&Value::Null));
    let sidebar = sidebar(&api).await?;
    // Read twice as Python does: the read after the one that added Archive numbers the folders.
    let nested = subtree(&sidebar.folders().await?, &folder_id);
    let scope = if members_too { nested } else { vec![folder_id.clone()] };
    let mut leaving = Vec::new();
    for hub in hubs_in(&sidebar.folders().await?, &scope) {
        let started = if members_too { sidebar.descendants(&hub).await? } else { Vec::new() };
        leaving.push(hub);
        leaving.extend(started);
    }
    let archived = sidebar.remove_folder(&folder_id, members_too).await?;
    let folders = if scope.is_empty() { vec![folder_id] } else { scope };
    api.publish(vec![removed(&[archived.clone(), leaving].concat(), &folders)]);
    Ok(json!(archived))
}

/// Delete a chat for good, with the chats it started. `keep_inside`: only the group
/// goes, what was in it stays where it is, on its own.
pub async fn delete_conversation(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let conv_id = text(args.get("conv_id").unwrap_or(&Value::Null));
    let sidebar = sidebar(&api).await?;
    if truthy(args.get("keep_inside").unwrap_or(&Value::Null)) {
        for child in sidebar.descendants(&conv_id).await? {
            if let Some(mut chat) = sidebar.chat(&child).await?.filter(|chat| field(chat, "parent_conv_id").trim() == conv_id) {
                put(&mut chat, [("parent_conv_id", ""), ("leader_conv_id", "")]);
                sidebar.save_chat(chat, true).await?;
            }
        }
    }
    let doomed = [vec![conv_id.clone()], sidebar.descendants(&conv_id).await?].concat();
    for id in doomed.iter().rev() {
        sidebar.chats.conv_delete(sidebar.who.id.clone(), id.clone()).await.map_err(failed)?;
    }
    let folders = doomed.iter().filter(|id| safe_id(id)).map(|id| chat_folder(api.repos.data_dir(), id)).collect();
    tokio::task::spawn_blocking(move || remove_chat_files(folders)).await.map_err(|stopped| stopped.to_string())??;
    api.publish(vec![removed(&doomed, &[])]);
    Ok(Value::Null)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn folder(id: &str, parent: &str, hub: &str) -> Folder {
        Folder { id: id.into(), name: id.into(), parent_id: parent.into(), sort_order: 0.0, group_hub_id: hub.into() }
    }

    #[test]
    fn a_subtree_is_parents_first_and_survives_a_loop() {
        let folders = [folder("a", "", ""), folder("b", "a", "hub-b"), folder("c", "b", ""), folder("d", "", "hub-d"), folder("x", "y", ""), folder("y", "x", "")];
        assert_eq!(subtree(&folders, "a"), ["a", "b", "c"]);
        assert_eq!(subtree(&folders, "x"), ["x", "y"]);
        assert!(subtree(&folders, "nope").is_empty());
        assert_eq!(hubs_in(&folders, &subtree(&folders, "a")), ["hub-b"]);
    }

    #[test]
    fn the_removed_event_names_only_what_went() {
        assert_eq!(removed(&[], &[]), json!({"type": "chats_changed", "open": false}));
        let ids = ["a".to_string(), String::new()];
        assert_eq!(removed(&ids, &ids[..1]), json!({"type": "chats_changed", "open": false, "removed_conv_ids": ["a"], "removed_folder_ids": ["a"]}));
    }

    #[test]
    fn only_a_plain_chat_id_names_a_folder_to_remove() {
        assert!(safe_id("d0f7ed45-912d_4.x") && !safe_id("") && !safe_id("..") && !safe_id("a/b") && !safe_id("a\\b") && !safe_id(&"a".repeat(81)));
        assert!(chat_folder(Path::new("data"), "c1").ends_with("chats/projects/_no_project/conversations/c1"));
        assert_eq!(remove_chat_files(vec![PathBuf::from("no such folder")]), Ok(()));
    }
}
