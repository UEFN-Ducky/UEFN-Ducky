//! Teams follow the folders (`chats/groups.py::sync_membership`): after a chat or a group
//! is filed somewhere else, each moved chat leaves the other groups' rosters, joins the
//! group its folder is in and points its parent there; a group whose leader left gets
//! the member that can take over.
//!
//! ponytail: the moved agents are not told (`nesting_notice`): their inboxes are the
//! Python backend's until the agent runner is in this process.

use std::collections::{BTreeSet, HashMap, HashSet};

use serde_json::{json, Value};

use super::{float, is_archive, pystr, text, truthy, Fields, Folder};

const COLORS: [&str; 8] = ["#7aa2f7", "#9ece6a", "#e0af68", "#bb9af7", "#f7768e", "#7dcfff", "#ff9e64", "#c0caf5"];
const LANE_KEYS: [&str; 4] = ["write_allowed", "lane_set_by", "lane_set_at", "lane_root"];

/// `str(row.get(name) or "").strip()`.
fn trimmed(row: &Value, name: &str) -> String {
    row.get(name).filter(|value| truthy(value)).map(pystr).unwrap_or_default().trim().to_string()
}

fn member_id(row: &Value) -> String {
    trimmed(row, "member_conv_id")
}

/// `normalize_glob`: one lane pattern in its canonical form, None when it can never be safe.
/// A full path must be inside one of `roots` (the person's projects, `/` between parts).
fn glob(pattern: &str, roots: &[String]) -> Option<String> {
    let mut path = pattern.trim().replace('\\', "/");
    while let Some(rest) = path.strip_prefix("./") {
        path = rest.to_string();
    }
    if path.is_empty() || path.split('/').any(|part| part == "..") {
        return None;
    }
    let absolute = path.starts_with('/') || path.chars().nth(1) == Some(':');
    let parts: Vec<&str> = path.trim_end_matches('/').split('/').enumerate().filter(|(index, part)| *part != "." && (*index == 0 || !part.is_empty())).map(|(_, part)| part).collect();
    let mut path = parts.join("/");
    if path.is_empty() {
        return None;
    }
    if absolute && path.chars().nth(1) == Some(':') {
        let mut rest = path.chars();
        let drive: String = rest.next().into_iter().flat_map(char::to_uppercase).collect();
        path = drive + rest.as_str();
    }
    if absolute {
        // Up to the first wildcard part, in any letter case.
        let fixed = path.split('/').take_while(|part| !part.contains(['*', '?', '['])).collect::<Vec<_>>().join("/").to_lowercase();
        let inside = |root: &String| fixed.strip_prefix(&root.to_lowercase()).is_some_and(|rest| rest.is_empty() || rest.starts_with('/'));
        if !roots.iter().any(inside) {
            return None;
        }
    }
    let folder = !path.contains(['*', '?', '[']) && !path.rsplit('/').next().unwrap_or_default().contains('.');
    Some(if folder { path + "/**" } else { path })
}

/// `_normalize_lane_field`: None is unrestricted, `[]` read-only. A pattern that no
/// longer parses is dropped; the lane never turns into unrestricted.
fn lane(raw: &Value, roots: &[String]) -> Value {
    let split = |text: &str| text.split(['\n', ',']).map(str::to_string).collect::<Vec<_>>();
    let items = match raw {
        Value::Null | Value::Bool(_) => return Value::Null,
        Value::String(text) => split(text),
        Value::Array(items) => items.iter().map(pystr).collect(),
        _ => return json!([]),
    };
    let all = |items: &[String]| items.iter().filter(|item| !item.trim().is_empty()).map(|item| glob(item, roots)).collect::<Option<BTreeSet<String>>>();
    // Python's second try reads each item of a list as a lane of its own (its commas
    // split too) and keeps the ones that parse whole; a text that failed stays failed.
    let each = || items.iter().filter(|_| raw.is_array()).filter_map(|item| all(&split(item))).flatten().collect();
    json!(all(&items).unwrap_or_else(each))
}

/// `normalize_member`: one roster row with every field it has. `roots`: the folders a
/// full-path lane may be in (`Sidebar::lane_roots`).
pub(super) fn member(raw: &Value, index: usize, roots: &[String]) -> Value {
    let number = |name: &str| raw.get(name).filter(|value| truthy(value)).and_then(float).unwrap_or(0.0);
    let or = |value: String, fallback: &str| if value.is_empty() { fallback.to_string() } else { value };
    json!({
        "member_conv_id": member_id(raw),
        "profile_id": trimmed(raw, "profile_id"),
        "name": or(trimmed(raw, "name"), "Ducky"),
        "ducky_name": trimmed(raw, "ducky_name"),
        "ducky_style": trimmed(raw, "ducky_style"),
        "model": trimmed(raw, "model"),
        "coding_agent": trimmed(raw, "coding_agent"),
        "tts_voice": trimmed(raw, "tts_voice"),
        "tts_speed": number("tts_speed"),
        "color": or(trimmed(raw, "color"), COLORS[index % COLORS.len()]),
        "is_group": raw.get("is_group").is_some_and(truthy),
        "write_allowed": lane(raw.get("write_allowed").unwrap_or(&Value::Null), roots),
        "lane_set_by": trimmed(raw, "lane_set_by"),
        "lane_set_at": number("lane_set_at"),
        "lane_root": trimmed(raw, "lane_root"),
    })
}

/// `keep_stored_lanes`: a row the stored roster already has keeps the stored lane fields.
fn stored_lanes(mut row: Value, stored: &[Value]) -> Value {
    let id = |row: &Value| row.get("member_conv_id").filter(|id| truthy(id)).map(pystr).unwrap_or_default();
    if let Some(old) = stored.iter().rfind(|old| old.is_object() && !id(&row).is_empty() && id(old) == id(&row)) {
        for key in LANE_KEYS {
            row[key] = old[key].clone();
        }
    }
    row
}

/// `save_group_roster`: the rows as they are stored, lanes never older than the stored ones.
pub(super) fn roster_rows(rows: &[Value], stored: &[Value], roots: &[String]) -> Vec<Value> {
    rows.iter().enumerate().map(|(index, row)| stored_lanes(member(&stored_lanes(row.clone(), stored), index, roots), stored)).collect()
}

/// Every chat and folder of the list, while the moved chats' teams are put right.
pub(super) struct Teams {
    chats: HashMap<String, Fields>,
    folders: HashMap<String, Folder>,
    /// Each group's folder, by its hub chat.
    hub_folder: HashMap<String, String>,
    /// Chats to save, in the order they first changed; with their stored roster when it changed.
    changed: Vec<String>,
    rosters: HashMap<String, Vec<Value>>,
    /// The folders a full-path lane may be in.
    roots: Vec<String>,
}

impl Teams {
    pub(super) fn new(chats: Vec<Fields>, folders: &[Folder], roots: Vec<String>) -> Teams {
        let groups = folders.iter().filter(|folder| !folder.group_hub_id.is_empty() && !is_archive(&folder.id));
        Teams {
            chats: chats.into_iter().map(|chat| (text(&chat["id"]), chat)).collect(),
            hub_folder: groups.map(|folder| (folder.group_hub_id.clone(), folder.id.clone())).collect(),
            folders: folders.iter().map(|folder| (folder.id.clone(), folder.clone())).collect(),
            changed: Vec::new(),
            rosters: HashMap::new(),
            roots,
        }
    }

    fn field(&self, conv_id: &str, name: &str) -> String {
        self.chats.get(conv_id).map(|chat| text(&chat[name]).trim().to_string()).unwrap_or_default()
    }

    fn is_group(&self, conv_id: &str) -> bool {
        self.chats.get(conv_id).is_some_and(|chat| chat["is_group"] == true)
    }

    /// The group a folder is in: its own, or the nearest one around it.
    fn container(&self, folder_id: &str) -> String {
        let (mut id, mut seen) = (folder_id.to_string(), HashSet::new());
        while let Some(folder) = self.folders.get(&id).filter(|_| !id.is_empty() && seen.insert(id.clone())) {
            if self.hub_folder.contains_key(&folder.group_hub_id) {
                return folder.group_hub_id.clone();
            }
            id = folder.parent_id.trim().to_string();
        }
        String::new()
    }

    /// The group around a group.
    fn outer_of(&self, hub: &str) -> String {
        let folder = self.hub_folder.get(hub).and_then(|id| self.folders.get(id));
        folder.map(|folder| self.container(folder.parent_id.trim())).unwrap_or_default()
    }

    fn inside(&self, hub: &str, outer: &str) -> bool {
        let (mut hub, mut seen) = (hub.to_string(), HashSet::new());
        while !hub.is_empty() && seen.insert(hub.clone()) {
            if hub == outer {
                return true;
            }
            hub = self.outer_of(&hub);
        }
        false
    }

    /// The team a chat is filed in ("" for none).
    fn team_of(&self, conv_id: &str) -> String {
        if self.is_group(conv_id) { self.outer_of(conv_id) } else { self.container(&self.field(conv_id, "folder_id")) }
    }

    fn leader(&self, hub: &str) -> String {
        self.field(hub, "leader_conv_id")
    }

    fn roster(&self, hub: &str) -> Vec<Value> {
        let rows = self.chats.get(hub).and_then(|chat| chat["group_members"].as_array());
        rows.into_iter().flatten().filter(|row| row.is_object()).cloned().collect()
    }

    fn on_roster(&self, hub: &str, conv_id: &str) -> bool {
        self.roster(hub).iter().any(|row| member_id(row) == conv_id)
    }

    /// (name, chat id) of the group's members that are filed in its own folder.
    fn seats(&self, hub: &str) -> Vec<(String, String)> {
        let seat = |row: &Value| {
            let id = member_id(row);
            let name = row.get("name").filter(|name| truthy(name)).map(pystr).unwrap_or_else(|| self.field(&id, "title"));
            (self.chats.contains_key(&id) && !self.is_group(&id) && self.team_of(&id) == hub).then_some((name, id))
        };
        self.roster(hub).iter().filter_map(seat).collect()
    }

    fn lead_seat(&self, hub: &str) -> String {
        self.seats(hub).into_iter().find(|(name, _)| name.trim().to_lowercase().starts_with("lead:")).map(|(_, id)| id).unwrap_or_default()
    }

    /// Who takes a group over: its "Lead: …" seat, else its only member.
    fn heir(&self, hub: &str) -> String {
        let seats = self.seats(hub);
        Some(self.lead_seat(hub)).filter(|lead| !lead.is_empty()).unwrap_or_else(|| if seats.len() == 1 { seats[0].1.clone() } else { String::new() })
    }

    /// The leader is gone, or sits in a team unrelated to this one, or sits on its own
    /// while the group has a "Lead: …" seat of its own.
    fn leader_is_stale(&self, hub: &str) -> bool {
        let leader = self.leader(hub);
        if leader.is_empty() || leader == hub {
            return false;
        }
        if !self.chats.contains_key(&leader) {
            return true;
        }
        let sits: Vec<String> = [self.team_of(&leader), self.field(&leader, "parent_conv_id")].into_iter().filter(|team| self.hub_folder.contains_key(team)).collect();
        if sits.iter().any(|team| team != hub && !self.inside(hub, team) && !self.inside(team, hub)) {
            return true;
        }
        sits.is_empty() && !["", leader.as_str()].contains(&self.lead_seat(hub).as_str())
    }

    fn set(&mut self, conv_id: &str, name: &str, value: Value) {
        let Some(chat) = self.chats.get_mut(conv_id) else { return };
        if name == "group_members" {
            let stored = chat["group_members"].as_array().cloned().unwrap_or_default();
            self.rosters.entry(conv_id.to_string()).or_insert(stored);
        }
        chat.insert(name.to_string(), value);
        if !self.changed.iter().any(|id| id == conv_id) {
            self.changed.push(conv_id.to_string());
        }
    }

    /// Take a chat off a group's roster; a leader that leaves hands the group over.
    fn drop(&mut self, hub: &str, conv_id: &str) {
        let was_leader = self.leader(hub) == conv_id;
        let rows: Vec<Value> = self.roster(hub).into_iter().filter(|row| member_id(row) != conv_id).collect();
        self.set(hub, "group_members", json!(rows));
        if was_leader {
            self.set(hub, "leader_conv_id", json!(self.heir(hub)));
        }
    }

    /// `keep_hub`: the group the caller just added these chats to or made them lead;
    /// their place there stays as asked.
    pub(super) fn sync(&mut self, conv_ids: &[String], keep_hub: &str) {
        for conv_id in conv_ids {
            if !self.chats.contains_key(conv_id) || is_archive(&self.field(conv_id, "folder_id")) {
                continue;
            }
            let (team, is_group, parent) = (self.team_of(conv_id), self.is_group(conv_id), self.field(conv_id, "parent_conv_id"));
            let known = |teams: &Teams, id: &str| teams.hub_folder.contains_key(id);
            let mut touched: BTreeSet<String> = self.hub_folder.keys().filter(|hub| self.on_roster(hub, conv_id)).cloned().collect();
            touched.extend([team.clone(), parent.clone()].into_iter().filter(|hub| *hub == team || known(self, hub)));
            touched.remove("");
            for hub in &touched {
                if [team.as_str(), conv_id.as_str(), keep_hub].contains(&hub.as_str()) || !self.on_roster(hub, conv_id) {
                    continue;
                }
                // A lead stays on the groups it leads from outside, and on its subgroups.
                let leads_from_outside = self.leader(hub) == *conv_id && parent != *hub && (team.is_empty() || self.inside(hub, &team));
                if !leads_from_outside && !(!team.is_empty() && self.leader(&team) == *conv_id && self.inside(hub, &team)) {
                    self.drop(hub, conv_id);
                }
            }
            // A parent that is a group this chat left, or a chat that is gone, is let go.
            let parent_follows = parent != team && (known(self, &parent) || !self.chats.contains_key(&parent));
            if is_group {
                // A nested group's parent is its outer group: it follows its folder.
                if !parent.is_empty() && parent_follows {
                    self.set(conv_id, "parent_conv_id", json!(team));
                }
            } else if !team.is_empty() {
                if !self.on_roster(&team, conv_id) && self.chats.contains_key(&team) {
                    let rows = self.roster(&team);
                    let chat = &self.chats[conv_id];
                    let named = |name: &str| text(&chat[name]);
                    let name = [named("ducky_name"), named("title")].into_iter().find(|name| !name.is_empty()).unwrap_or("Ducky".into());
                    let joined = json!({
                        "member_conv_id": conv_id, "name": name, "ducky_name": named("ducky_name"), "ducky_style": named("ducky_style"),
                        "profile_id": named("profile_id"), "model": named("model"), "coding_agent": named("coding_agent"),
                    });
                    let rows = [rows.clone(), vec![member(&joined, rows.len(), &self.roots)]].concat();
                    self.set(&team, "group_members", json!(rows));
                }
                if parent.is_empty() || parent_follows {
                    self.set(conv_id, "parent_conv_id", json!(team));
                }
            } else if known(self, &parent) {
                self.set(conv_id, "parent_conv_id", json!(""));
            }
            for hub in touched.iter().filter(|hub| known(self, hub) && *hub != keep_hub).cloned().collect::<Vec<_>>() {
                if self.leader_is_stale(&hub) {
                    let leader = self.leader(&hub);
                    self.drop(&hub, &leader);
                }
            }
        }
    }

    /// The chats to save, a changed roster as `save_group_roster` stores it.
    pub(super) fn changed(mut self) -> Vec<Fields> {
        let mut out = Vec::new();
        for conv_id in &self.changed {
            let Some(mut chat) = self.chats.remove(conv_id) else { continue };
            if let Some(stored) = self.rosters.get(conv_id) {
                let rows = chat["group_members"].as_array().cloned().unwrap_or_default();
                chat.insert("group_members".into(), json!(roster_rows(&rows, stored, &self.roots)));
            }
            out.push(chat);
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_lane_pattern_gets_its_canonical_form() {
        let roots = ["C:/Repos".to_string(), "é:".to_string()];
        let cases = [("Content\\Verse\\Shop\\", "Content/Verse/Shop/**"), ("./src//a/./b.py", "src/a/b.py"), ("src/*.rs", "src/*.rs"), ("c:/Repos/x", "C:/Repos/x/**")];
        for (given, wanted) in cases {
            assert_eq!(glob(given, &roots).as_deref(), Some(wanted), "{given}");
        }
        let glob = |pattern: &str| glob(pattern, &roots);
        let lane = |raw: &Value| lane(raw, &roots);
        assert_eq!([glob(" "), glob("a/../b"), glob("./"), glob("/")], [None, None, None, None]);
        assert_eq!(lane(&json!(null)), json!(null));
        assert_eq!(lane(&json!(true)), json!(null));
        assert_eq!(lane(&json!(7)), json!([]));
        assert_eq!(lane(&json!("b, a\n a ")), json!(["a/**", "b/**"]));
        assert_eq!(lane(&json!(["b", "", "a.py", "b/"])), json!(["a.py", "b/**"]));
        // One bad pattern: the rest is kept, and never as unrestricted.
        assert_eq!(lane(&json!(["../x", "ok,also"])), json!(["also/**", "ok/**"]));
        assert_eq!(lane(&json!(["../x", "ok,../y"])), json!([]));
        assert_eq!(lane(&json!("../x, ok")), json!([]));
        assert_eq!(glob("é:/x").as_deref(), Some("É:/x/**"));
    }

    #[test]
    fn a_full_path_lane_is_inside_one_of_the_projects() {
        let roots = ["C:/Repos/App".to_string(), "/home/me/isle".to_string()];
        let glob = |pattern: &str| glob(pattern, &roots);
        let kept = [
            ("c:\\repos\\app", "C:/repos/app/**"), ("C:/Repos/App/src/*.rs", "C:/Repos/App/src/*.rs"), ("C:/Repos/App/a.py", "C:/Repos/App/a.py"),
            ("/home/me/isle/Content/", "/home/me/isle/Content/**"), ("/home/me/isle/**", "/home/me/isle/**"),
        ];
        for (given, wanted) in kept {
            assert_eq!(glob(given).as_deref(), Some(wanted), "{given}");
        }
        // Another folder, a longer name, a parent of a project, a wildcard before the project ends.
        for outside in ["C:/Other/x", "C:/Repos/Apple/x", "C:/Repos", "C:/Repos/*/src", "/home/me", "D:/Repos/App/a.py"] {
            assert_eq!(glob(outside), None, "{outside}");
        }
        assert_eq!(super::glob("C:/Repos/App/x", &[]), None);
        assert_eq!(lane(&json!(["C:/Other/x", "C:/Repos/App/x", "src"]), &roots), json!(["C:/Repos/App/x/**", "src/**"]));
    }

    #[test]
    fn a_roster_row_has_every_field_and_keeps_its_stored_lane() {
        let row = member(&json!({"member_conv_id": " m ", "name": " ", "tts_speed": "1.5", "is_group": 1, "write_allowed": ["src"], "extra": 1}), 9, &[]);
        let wanted = json!({
            "member_conv_id": "m", "profile_id": "", "name": "Ducky", "ducky_name": "", "ducky_style": "", "model": "", "coding_agent": "", "tts_voice": "",
            "tts_speed": 1.5, "color": "#9ece6a", "is_group": true, "write_allowed": ["src/**"], "lane_set_by": "", "lane_set_at": 0.0, "lane_root": "",
        });
        assert_eq!(row, wanted);
        let stored = [json!({"member_conv_id": "m", "write_allowed": []})];
        let rows = roster_rows(&[json!({"member_conv_id": "m", "write_allowed": ["src"]}), json!({"member_conv_id": "new"})], &stored, &[]);
        // The stored member keeps its stored lane fields as they are; the new one gets the defaults.
        assert_eq!((&rows[0]["write_allowed"], &rows[0]["lane_set_by"], &rows[1]["write_allowed"], &rows[1]["lane_set_by"]), (&json!([]), &json!(null), &json!(null), &json!("")));
    }

    fn chat(id: &str, folder: &str, fields: Value) -> Fields {
        let mut chat = json!({"id": id, "title": id, "folder_id": folder, "parent_conv_id": "", "leader_conv_id": "", "is_group": false, "group_members": [], "ducky_name": "", "ducky_style": "", "profile_id": "", "model": "", "coding_agent": ""});
        chat.as_object_mut().unwrap().extend(fields.as_object().unwrap().clone());
        chat.as_object().unwrap().clone()
    }

    fn folder(id: &str, parent: &str, hub: &str) -> Folder {
        Folder { id: id.into(), name: id.into(), parent_id: parent.into(), sort_order: 0.0, group_hub_id: hub.into() }
    }

    #[test]
    fn a_moved_chat_changes_team_and_a_group_without_its_leader_gets_its_lead_seat() {
        let row = |id: &str, name: &str| json!({"member_conv_id": id, "name": name});
        let chats = vec![
            chat("old", "g-old", json!({"is_group": true, "leader_conv_id": "mover", "group_members": [row("mover", "Boss"), row("lead", "Lead: build"), row("other", "Other"), "junk"]})),
            chat("new", "g-new", json!({"is_group": true})),
            chat("mover", "g-new", json!({"parent_conv_id": "old", "ducky_name": "Mo"})),
            chat("lead", "g-old", json!({"parent_conv_id": "old"})),
            chat("other", "g-old", json!({"parent_conv_id": "old"})),
        ];
        let mut teams = Teams::new(chats, &[folder("g-old", "", "old"), folder("g-new", "", "new")], Vec::new());
        teams.sync(&["mover".to_string(), "gone".to_string()], "");
        let changed: HashMap<String, Fields> = teams.changed().into_iter().map(|chat| (text(&chat["id"]), chat)).collect();
        assert_eq!(changed.len(), 3);
        assert_eq!(changed["mover"]["parent_conv_id"], "new");
        let ids = |hub: &str| changed[hub]["group_members"].as_array().unwrap().iter().map(member_id).collect::<Vec<_>>();
        assert_eq!((ids("old"), &changed["old"]["leader_conv_id"]), (vec!["lead".to_string(), "other".to_string()], &json!("lead")));
        assert_eq!((ids("new"), &changed["new"]["group_members"][0]["name"]), (vec!["mover".to_string()], &json!("Mo")));
    }

    #[test]
    fn a_chat_dragged_out_of_every_group_is_on_its_own_and_a_nested_group_follows_its_folder() {
        let chats = vec![
            chat("outer", "g-outer", json!({"is_group": true, "group_members": [{"member_conv_id": "m"}]})),
            chat("inner", "g-inner", json!({"is_group": true, "parent_conv_id": "outer"})),
            chat("m", "", json!({"parent_conv_id": "outer"})),
        ];
        // The inner group's folder is at the top now.
        let mut teams = Teams::new(chats, &[folder("g-outer", "", "outer"), folder("g-inner", "", "inner")], Vec::new());
        teams.sync(&["m".to_string(), "inner".to_string()], "");
        let changed: HashMap<String, Fields> = teams.changed().into_iter().map(|chat| (text(&chat["id"]), chat)).collect();
        assert_eq!((&changed["m"]["parent_conv_id"], &changed["inner"]["parent_conv_id"], &changed["outer"]["group_members"]), (&json!(""), &json!(""), &json!([])));
    }
}
