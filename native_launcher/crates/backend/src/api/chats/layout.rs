//! The sidebar's drag and drop (`chats/folders.py::apply_sidebar_layout`): where each
//! folder and chat sits and in what order. After it every group's roster is rebuilt from
//! what is in its folder (`group_orchestrator.py::reconcile_group_rosters_from_folders`
//! and `sync_group_members_from_folder`).
//!
//! A window can show a stale tree: rows for ids the list no longer has are left out,
//! never created, and a layout never moves a chat into or out of Archive.

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use serde_json::{json, Value};

use super::moves::removed;
use super::teams::{member, roster_rows};
use super::{failed, float, is_archive, normalise, pystr, sidebar, text, truthy, Api, Fields, Folder, Sidebar, ONE_LIST};

/// The rows by id as a Python dict keeps them: the first one's place, the last one's row.
fn by_id(rows: &[Value]) -> Vec<(String, &Value)> {
    let mut out: Vec<(String, &Value)> = Vec::new();
    for row in rows.iter().filter(|row| row.is_object() && !text(&row["id"]).trim().is_empty()) {
        let id = pystr(&row["id"]);
        match out.iter_mut().find(|(seen, _)| *seen == id) {
            Some(slot) => slot.1 = row,
            None => out.push((id, row)),
        }
    }
    out
}

/// `str(row.get(name, default) or "")`.
fn named(row: &Value, name: &str, default: &str) -> String {
    row.get(name).map_or(default.to_string(), text)
}

/// `float(row.get("sort_order", current))`; the current one when that is no number.
fn order(row: &Value, current: f64) -> f64 {
    match row.get("sort_order") {
        Some(value @ (Value::Bool(_) | Value::Number(_))) => float(value).unwrap_or(current),
        Some(Value::String(number)) => number.trim().parse().unwrap_or(current),
        _ => current,
    }
}

/// `_would_create_cycle`: the folder would end up inside itself.
fn cycle(folders: &[Folder], folder_id: &str, new_parent: &str) -> bool {
    let (mut current, mut seen) = (new_parent.to_string(), HashSet::new());
    while !current.is_empty() && seen.insert(current.clone()) {
        if current == folder_id {
            return true;
        }
        current = folders.iter().rfind(|folder| folder.id == current).map(|folder| folder.parent_id.clone()).unwrap_or_default();
    }
    false
}

/// The first of these that says something.
fn first<const N: usize>(options: [String; N], fallback: &str) -> String {
    options.into_iter().find(|option| !option.is_empty()).unwrap_or_else(|| fallback.to_string())
}

/// `{**prev, **fields}`.
fn over(prev: &Value, fields: Value) -> Value {
    let mut row = prev.as_object().cloned().unwrap_or_default();
    row.extend(fields.as_object().cloned().unwrap_or_default());
    Value::Object(row)
}

fn kept(prev: &Value, name: &str) -> String {
    prev.get(name).map(text).unwrap_or_default()
}

/// A ducky's roster row: what its chat says now, else what the roster had.
/// `joins`: it sits in the group's folder, so its own profile counts too.
fn ducky_row(chat: &Fields, prev: &Value, joins: bool, index: usize) -> Value {
    let own = |name: &str| text(&chat[name]);
    let either = |name: &str| first([own(name), kept(prev, name)], "");
    let speed = [&chat["tts_speed"], &prev["tts_speed"]].into_iter().find(|speed| truthy(speed)).and_then(float).unwrap_or(0.0);
    let fields = json!({
        "member_conv_id": own("id"),
        "name": first([kept(prev, "name").trim().to_string(), own("ducky_name").trim().to_string(), own("title").trim().to_string()], "Ducky"),
        "ducky_name": either("ducky_name"), "ducky_style": either("ducky_style"), "model": either("model"),
        "coding_agent": either("coding_agent"), "tts_voice": either("tts_voice"), "tts_speed": speed,
        "profile_id": first([kept(prev, "profile_id"), if joins { own("profile_id") } else { String::new() }], ""),
        "is_group": false,
    });
    member(&over(prev, fields), index)
}

/// A nested group's roster row: one representative speaks for the whole subgroup.
fn group_row(hub: &str, label: &str, prev: &Value, index: usize) -> Value {
    let label = first([label.trim().to_string()], "Group");
    member(&over(prev, json!({"member_conv_id": hub, "name": label, "ducky_name": label, "profile_id": kept(prev, "profile_id"), "is_group": true})), index)
}

/// `stays_with_outer_group`: the chat leads the group whose folder it sits in, and
/// `target` is a group folder nested inside that one. Such a lead stays at the top of
/// its own group instead of being pulled into each subgroup in turn.
fn leads_from_outside(chat: &Fields, target: &str, folders: &[Folder], docs: &[Fields]) -> bool {
    let (home, target) = (text(&chat["folder_id"]).trim().to_string(), target.trim());
    let folder = |id: &str| folders.iter().rfind(|folder| folder.id == id);
    let hub = folder(&home).map(|folder| folder.group_hub_id.clone()).unwrap_or_default();
    let leads = docs.iter().find(|doc| text(&doc["id"]) == hub).is_some_and(|doc| text(&doc["leader_conv_id"]).trim() == text(&chat["id"]));
    if home.is_empty() || target.is_empty() || home == target || hub.is_empty() || !leads {
        return false;
    }
    let above = |id: &str| folder(id).map(|folder| folder.parent_id.trim().to_string()).unwrap_or_default();
    let (mut current, mut seen) = (above(target), HashSet::new());
    while !current.is_empty() && seen.insert(current.clone()) {
        if current == home {
            return true;
        }
        current = above(&current);
    }
    false
}

impl Sidebar<'_> {
    /// The folders' new parents and order; gives the ids of the folders there are now.
    async fn place_folders(&self, rows: &[Value]) -> Result<HashSet<String>, String> {
        let mut current = self.folders().await?;
        let mut changed: Vec<(String, String, f64)> = Vec::new();
        for (id, row) in by_id(rows) {
            let parent = named(row, "parent_id", "");
            let Some(at) = current.iter().position(|folder| folder.id == id) else { continue };
            let lost = !parent.is_empty() && (is_archive(&parent) || !current.iter().any(|folder| folder.id == parent));
            if is_archive(&id) || lost || cycle(&current, &id, &parent) {
                continue;
            }
            let sort_order = order(row, current[at].sort_order);
            if current[at].parent_id != parent || current[at].sort_order != sort_order {
                (current[at].parent_id, current[at].sort_order) = (parent.clone(), sort_order);
                changed.push((id, parent, sort_order));
            }
        }
        if !changed.is_empty() {
            // Read again right before writing: a folder deleted meanwhile stays deleted,
            // and folders this layout did not touch keep whatever they have now.
            current = self.folders().await?;
            for at in 0..current.len() {
                let Some((id, parent, sort_order)) = changed.iter().find(|(id, ..)| *id == current[at].id) else { continue };
                let lost = !parent.is_empty() && !current.iter().any(|folder| folder.id == *parent);
                if !lost && !cycle(&current, id, parent) {
                    (current[at].parent_id, current[at].sort_order) = (parent.clone(), *sort_order);
                }
            }
            self.save(&current).await?;
        }
        Ok(current.into_iter().map(|folder| folder.id).collect())
    }

    /// The chats' new folders and order. Into or out of Archive is `move_conversation`'s job.
    async fn place_chats(&self, rows: &[Value], folders: &HashSet<String>) -> Result<(), String> {
        let rows = by_id(rows);
        if rows.is_empty() {
            return Ok(());
        }
        let docs = self.docs().await?;
        for (id, row) in rows {
            let Some(chat) = docs.iter().find(|chat| text(&chat["id"]) == id) else { continue };
            let current = text(&chat["folder_id"]);
            let folder_id = named(row, "folder_id", &current);
            if is_archive(&current) || is_archive(&folder_id) || (!folder_id.is_empty() && !folders.contains(&folder_id)) {
                continue;
            }
            let was = chat["sort_order"].as_f64().unwrap_or(0.0);
            let sort_order = order(row, was);
            if current != folder_id || was != sort_order {
                let mut moved = chat.clone();
                moved.insert("folder_id".into(), json!(folder_id));
                moved.insert("sort_order".into(), json!(sort_order));
                self.save_chat(moved, false).await?;
            }
        }
        Ok(())
    }

    /// `sync_group_members_from_folder`: the roster is the duckies in the group's folder
    /// and the groups nested in it, its leader first.
    /// ponytail: no roster chat is fetched from another project first
    /// (`_rehome_group_roster`): there is one list.
    async fn rebuild_roster(&self, group: Fields) -> Result<(), String> {
        let group_id = text(&group["id"]);
        // The folders as stored: this read never adds Archive or numbers anything.
        let stored = self.chats.folders_get(self.who.clone(), ONE_LIST.into()).await.map_err(failed)?;
        let folders: Vec<Folder> = stored.iter().map(Folder::from).collect();
        let Some(own) = folders.iter().find(|folder| folder.group_hub_id == group_id) else { return Ok(()) };
        let docs = self.docs().await?;
        let chat = |id: &str| docs.iter().find(|doc| text(&doc["id"]) == id);
        let roster = group["group_members"].as_array().cloned().unwrap_or_default();
        let existing: Vec<Value> = roster.iter().enumerate().filter(|(_, row)| row.is_object()).map(|(index, row)| member(row, index)).filter(|row| row["member_conv_id"] != "").collect();
        let prev = |id: &str| existing.iter().rfind(|row| row["member_conv_id"] == id).cloned().unwrap_or_else(|| json!({}));

        let mut rows: Vec<Value> = Vec::new();
        for doc in docs.iter().filter(|doc| text(&doc["folder_id"]) == own.id && text(&doc["id"]) != group_id && doc["is_group"] != true) {
            let parent = text(&doc["parent_conv_id"]).trim().to_string();
            // What a ducky started is that ducky's, never a member of the group.
            if !parent.is_empty() && chat(&parent).is_some_and(|parent| parent["is_group"] != true) {
                continue;
            }
            rows.push(ducky_row(doc, &prev(&text(&doc["id"])), true, rows.len()));
            if parent.is_empty() {
                let mut joined = doc.clone();
                joined.insert("parent_conv_id".into(), json!(group_id));
                self.save_chat(joined, true).await?;
            }
        }
        for folder in folders.iter().filter(|folder| !folder.group_hub_id.is_empty() && folder.group_hub_id != group_id && folder.parent_id == own.id) {
            let prev = prev(&folder.group_hub_id);
            let label = first([folder.name.clone(), kept(&prev, "name")], "Group");
            rows.push(group_row(&folder.group_hub_id, &label, &prev, rows.len()));
        }
        // An outer group's lead on this roster is filed in its own group, not this folder.
        let mut seen = HashSet::new();
        for row in existing.iter().filter(|row| seen.insert(text(&row["member_conv_id"]))) {
            let id = text(&row["member_conv_id"]);
            let last = prev(&id);
            let absent = !rows.iter().any(|row| row["member_conv_id"] == *id) && last["is_group"] != true;
            if absent && chat(&id).is_some_and(|chat| leads_from_outside(chat, &own.id, &folders, &docs)) {
                rows.push(member(&last, rows.len()));
            }
        }
        // The leader stays on the roster, first: a leader is a pointer, not a folder
        // member to move, so its folder and parent are left as they are.
        let leader = text(&group["leader_conv_id"]).trim().to_string();
        if !leader.is_empty() && leader != group_id {
            if let Some(row) = rows.iter().find(|row| row["member_conv_id"] == *leader).cloned() {
                rows.retain(|row| row["member_conv_id"] != *leader);
                rows.insert(0, row);
            } else if let Some(doc) = chat(&leader) {
                let prev = prev(&leader);
                let label = first([kept(&prev, "name").trim().to_string(), text(&doc["ducky_name"]).trim().to_string(), text(&doc["title"]).trim().to_string()], "Group");
                rows.insert(0, if doc["is_group"] == true { group_row(&leader, &label, &prev, 0) } else { ducky_row(doc, &prev, false, 0) });
            }
        }
        let mut group = group;
        group.insert("group_members".into(), json!(roster_rows(&rows, &roster)));
        self.save_chat(group, true).await.map(|_| ())
    }

    /// `reconcile_group_rosters_from_folders`: every group's roster from its folder; a
    /// ducky that left its group's folder is on its own.
    async fn rebuild_rosters(&self) -> Result<(), String> {
        let (mut hubs, mut home) = (HashSet::new(), HashMap::new());
        for folder in self.folders().await?.into_iter().filter(|folder| !folder.group_hub_id.is_empty()) {
            home.insert(folder.group_hub_id.clone(), folder.id);
            if let Some(group) = self.chat(&folder.group_hub_id).await?.filter(|chat| chat["is_group"] == true) {
                self.rebuild_roster(group).await?;
                hubs.insert(folder.group_hub_id);
            }
        }
        for stored in self.numbered_chats().await? {
            let parent = text(&stored["parent_conv_id"]).trim().to_string();
            if truthy(&stored["is_group"]) || !hubs.contains(&parent) || text(&stored["folder_id"]) == home[&parent] {
                continue;
            }
            let mut chat = normalise(&stored, &self.custom);
            chat.insert("parent_conv_id".into(), json!(""));
            self.save_chat(chat, true).await?;
        }
        Ok(())
    }
}

/// Save the tree the sidebar shows after a drag: `payload` holds `folders` (id,
/// parent_id, sort_order) and `chats` (id, folder_id, sort_order). The teams follow.
/// ponytail: a `project_slug` other than the one list is refused; every chat is in the
/// one list, and the sidebar sends none.
pub async fn apply_sidebar_layout(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let payload = args.get("payload").unwrap_or(&Value::Null);
    let (Some(folders), Some(chats)) = (payload["folders"].as_array(), payload["chats"].as_array()) else {
        return Err("layout payload must include folders and chats arrays".to_string());
    };
    let project = named(payload, "project_slug", "").trim().to_string();
    if !project.is_empty() && project != ONE_LIST {
        return Err(format!("Unknown project: {project}"));
    }
    let sidebar = sidebar(&api).await?;
    let known = sidebar.place_folders(folders).await?;
    sidebar.place_chats(chats, &known).await?;
    sidebar.rebuild_rosters().await?;
    // Dragging in or out of a group folder changes its team, leader and parent.
    let dragged = chats.iter().filter(|row| row.is_object()).map(|row| named(row, "id", ""));
    let groups = folders.iter().filter(|row| row.is_object()).map(|row| named(row, "group_hub_id", ""));
    sidebar.sync_teams(&dragged.chain(groups).collect::<Vec<_>>(), "").await?;
    api.publish(vec![removed(&[], &[])]);
    Ok(Value::Null)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn folder(id: &str, parent: &str, hub: &str) -> Folder {
        Folder { id: id.into(), name: id.into(), parent_id: parent.into(), sort_order: 0.0, group_hub_id: hub.into() }
    }

    #[test]
    fn layout_rows_are_read_as_python_reads_them() {
        let rows = [json!({"id": "a", "n": 1}), json!("junk"), json!({"id": " "}), json!({"id": 7}), json!({"id": "a", "n": 2}), json!({})];
        let read: Vec<(String, i64)> = by_id(&rows).into_iter().map(|(id, row)| (id, row["n"].as_i64().unwrap_or(0))).collect();
        assert_eq!(read, [("a".to_string(), 2), ("7".to_string(), 0)]);
        assert_eq!([named(&json!({"f": null}), "f", "was"), named(&json!({}), "f", "was"), named(&json!({"f": 5}), "f", "was")], ["", "was", "5"]);
        let orders = [json!({"sort_order": "2.5"}), json!({"sort_order": true}), json!({"sort_order": null}), json!({"sort_order": "x"}), json!({"sort_order": []}), json!({})];
        assert_eq!(orders.iter().map(|row| order(row, 9.0)).collect::<Vec<_>>(), [2.5, 1.0, 9.0, 9.0, 9.0, 9.0]);
    }

    #[test]
    fn a_folder_never_goes_inside_itself() {
        let folders = [folder("a", "", ""), folder("b", "a", ""), folder("c", "b", ""), folder("x", "y", ""), folder("y", "x", "")];
        assert!(cycle(&folders, "a", "c") && cycle(&folders, "a", "a") && !cycle(&folders, "c", "a") && !cycle(&folders, "a", "") && !cycle(&folders, "", ""));
        // A loop already in the data ends the walk.
        assert!(!cycle(&folders, "a", "x"));
    }

    #[test]
    fn a_lead_of_the_outer_group_stays_on_its_subgroups() {
        let doc = |id: &str, folder: &str, leader: &str| json!({"id": id, "folder_id": folder, "leader_conv_id": leader}).as_object().unwrap().clone();
        let folders = [folder("g-out", "", "out"), folder("mid", "g-out", ""), folder("g-in", "mid", "in"), folder("g-far", "", "far")];
        let docs = [doc("out", "g-out", "lead"), doc("lead", "g-out", ""), doc("worker", "g-out", "")];
        assert!(leads_from_outside(&docs[1], "g-in", &folders, &docs));
        assert!(!leads_from_outside(&docs[1], "g-far", &folders, &docs) && !leads_from_outside(&docs[1], "g-out", &folders, &docs));
        assert!(!leads_from_outside(&docs[2], "g-in", &folders, &docs));
    }

    #[test]
    fn a_roster_row_takes_what_the_chat_says_now_and_keeps_the_rest() {
        let chat = json!({"id": "m", "title": "Chat m", "ducky_name": "", "ducky_style": "robot", "model": "", "coding_agent": "", "tts_voice": "", "tts_speed": 0.0, "profile_id": "dev"});
        let prev = member(&json!({"member_conv_id": "m", "model": "x:y", "tts_speed": 1.5, "color": "#123456"}), 3);
        let row = ducky_row(chat.as_object().unwrap(), &prev, true, 0);
        // The stored name was blank, so it was "Ducky" already.
        assert_eq!((&row["name"], &row["ducky_style"], &row["model"], &row["tts_speed"], &row["color"], &row["profile_id"]), (&json!("Ducky"), &json!("robot"), &json!("x:y"), &json!(1.5), &json!("#123456"), &json!("dev")));
        let fresh = ducky_row(chat.as_object().unwrap(), &json!({}), false, 1);
        assert_eq!((&fresh["name"], &fresh["profile_id"], &fresh["color"]), (&json!("Chat m"), &json!(""), &json!("#9ece6a")));
        let nested = group_row("hub2", "  ", &json!({}), 0);
        assert_eq!((&nested["name"], &nested["ducky_name"], &nested["is_group"]), (&json!("Group"), &json!("Group"), &json!(true)));
    }
}
