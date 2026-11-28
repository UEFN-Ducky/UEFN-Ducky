//! A group's leader and who is on its roster (`panel_chats/groups.py::group_set_leader`
//! and `group_remove`).

use std::sync::Arc;

use serde_json::{json, Value};

use super::teams::{member, roster_rows};
use super::{chat_changed, sidebar, text, Api, Fields, Sidebar};

fn refused(why: &str) -> Value {
    json!({"ok": false, "error": why})
}

/// `group_orchestrator.group_members`: the roster's rows, each with every field.
fn roster(group: &Fields) -> Vec<Value> {
    let rows = group["group_members"].as_array().into_iter().flatten().enumerate();
    rows.filter(|(_, row)| row.is_object()).map(|(index, row)| member(row, index)).collect()
}

fn arg(args: &Fields, name: &str) -> String {
    text(args.get(name).unwrap_or(&Value::Null))
}

impl Sidebar<'_> {
    async fn group(&self, group_id: &str) -> Result<Option<Fields>, String> {
        Ok(self.chat(group_id).await?.filter(|chat| chat["is_group"] == true))
    }

    /// `evict_member_from_group`: a ducky goes to the top level, a nested group's folder
    /// too; either is on its own after. The roster is the caller's to change.
    async fn evict(&self, member_id: &str) -> Result<(), String> {
        let Some(member) = self.chat(member_id).await?.filter(|_| !member_id.is_empty()) else { return Ok(()) };
        if member["is_group"] == true {
            let mut folders = self.folders().await?;
            if let Some(own) = folders.iter_mut().find(|folder| folder.group_hub_id == member_id).filter(|own| !own.parent_id.is_empty()) {
                own.parent_id.clear();
                self.save(&folders).await?;
            }
        } else {
            self.place(member_id, "").await?;
        }
        if let Some(mut member) = self.chat(member_id).await?.filter(|member| !text(&member["parent_conv_id"]).trim().is_empty()) {
            member.insert("parent_conv_id".into(), json!(""));
            self.save_chat(member, true).await?;
        }
        Ok(())
    }
}

/// Name the member that speaks for the group. It keeps its place on this roster
/// wherever it is filed; the other teams it was in follow where it is filed.
pub async fn group_set_leader(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let sidebar = sidebar(&api).await?;
    let Some(mut group) = sidebar.group(&arg(&args, "group_id")).await? else { return Ok(refused("Not a group chat")) };
    let member_id = arg(&args, "member_conv_id").trim().to_string();
    if member_id.is_empty() {
        return Ok(refused("member_conv_id required"));
    }
    let members = roster(&group);
    if !members.iter().any(|row| row["member_conv_id"] == *member_id) {
        return Ok(refused("Member not in this group"));
    }
    group.insert("leader_conv_id".into(), json!(member_id));
    let group = sidebar.save_chat(group, true).await?;
    sidebar.sync_teams(std::slice::from_ref(&member_id), &text(&group["id"])).await?;
    api.publish(vec![chat_changed(&group)]);
    Ok(json!({"ok": true, "leader_conv_id": member_id, "group_members": members}))
}

/// Take a member off the roster and out of the group's folder. A leader that goes
/// hands the group to the first member left.
pub async fn group_remove(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let sidebar = sidebar(&api).await?;
    let Some(mut group) = sidebar.group(&arg(&args, "group_id")).await? else { return Ok(refused("Not a group chat")) };
    let member_id = arg(&args, "member_conv_id").trim().to_string();
    let stored = group["group_members"].as_array().cloned().unwrap_or_default();
    let kept: Vec<Value> = roster(&group).into_iter().filter(|row| row["member_conv_id"] != *member_id).collect();
    let mut leader = text(&group["leader_conv_id"]).trim().to_string();
    if !leader.is_empty() && leader == member_id {
        leader = kept.first().map(|row| text(&row["member_conv_id"])).unwrap_or_default();
        group.insert("leader_conv_id".into(), json!(leader));
    }
    // The roster is saved first, so no later folder sync can pull the member back.
    group.insert("group_members".into(), json!(roster_rows(&kept, &stored)));
    let group = sidebar.save_chat(group, true).await?;
    sidebar.evict(&member_id).await?;
    api.publish(vec![chat_changed(&group)]);
    Ok(json!({"ok": true, "group_members": kept, "leader_conv_id": leader}))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_roster_is_its_rows_with_every_field_and_a_refusal_says_why() {
        let group = json!({"group_members": [{"member_conv_id": " a "}, "junk", {"member_conv_id": "b", "color": "#111111"}]});
        let rows = roster(group.as_object().unwrap());
        // The colour is by the row's place in the stored list, junk rows counted.
        assert_eq!(rows.iter().map(|row| (text(&row["member_conv_id"]), text(&row["color"]))).collect::<Vec<_>>(), [("a".to_string(), "#7aa2f7".to_string()), ("b".to_string(), "#111111".to_string())]);
        assert_eq!(roster(json!({"group_members": null}).as_object().unwrap()), Vec::<Value>::new());
        assert_eq!(refused("Not a group chat"), json!({"ok": false, "error": "Not a group chat"}));
    }
}
