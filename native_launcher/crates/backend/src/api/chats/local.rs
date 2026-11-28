//! Chats made on this PC before signing in (`panel_chats/local.py`): the signed-in
//! person adds them to the account or deletes them for good.
//!
//! ponytail: a deleted chat's "Allow everything" and its sandbox are cleared by the
//! Python backend, which answers these calls until the agent runner is in this process.

use std::path::PathBuf;
use std::sync::Arc;

use serde_json::{json, Value};

use super::moves::{chat_folder, remove_chat_files, removed, safe_id};
use super::{failed, imported, pystr, truthy, Api, Fields, ONE_LIST};
use crate::store::account::{self, LOCKED};
use crate::store::sealed_files::Files;

const NO_KEY: &str = "Your account's data key isn't on this PC yet. Go online and try again.";

/// `[str(i) for i in conv_ids if i]`; None when no chat is named.
fn named(args: &Fields) -> Option<Vec<String>> {
    match args.get("conv_ids") {
        None | Some(Value::Null) => None,
        Some(ids) => Some(ids.as_array().into_iter().flatten().filter(|id| truthy(id)).map(pystr).collect()),
    }
}

/// Where a chat's files can be: under the project it is stored in, and in the one list.
fn folders(api: &Api, conv_id: &str, project: &str) -> Vec<PathBuf> {
    let mut projects = vec![project, ONE_LIST];
    projects.dedup();
    let safe = |project: &&str| safe_id(project) && safe_id(conv_id);
    projects.into_iter().filter(safe).map(|project| chat_folder(api.repos.data_dir(), project, conv_id)).collect()
}

fn or(error: String, fallback: &str) -> String {
    if error.is_empty() { fallback.to_string() } else { error }
}

/// Move the named chats made signed out into the signed-in account, each with the
/// chats it started or its whole group; every one when none is named.
/// `{ok, moved, failed}`: a set that could not move stays as it was, whole.
pub async fn adopt_local_chats(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    imported(&api, "chats", "chats").await?;
    let nothing = |why: String| json!({"ok": false, "error": why, "moved": [], "failed": []});
    let who = match api.repos.who().await {
        Ok(who) => who,
        Err(error) if error.0 == LOCKED => return Ok(nothing(NO_KEY.to_string())),
        Err(error) => return Err(failed(error)),
    };
    let chats = api.repos.chats().await.map_err(failed)?;
    let out = match chats.adopt(who.clone(), named(&args)).await {
        Ok(out) => out,
        // Nothing moved: the chats are where they were.
        Err(error) => return Ok(nothing(or(error.0, "Couldn't add them to your account."))),
    };
    let moved: Vec<String> = out["moved"].as_array().into_iter().flatten().map(pystr).collect();
    if !moved.is_empty() {
        let files = Files { store: api.repos.store().await.map_err(failed)?, root: api.repos.data_dir() };
        for conv_id in &moved {
            let project = chats.conv_project_id(who.id.clone(), conv_id.clone()).await.map_err(failed)?.unwrap_or_default();
            for folder in folders(&api, conv_id, &project) {
                // Sealed for this PC's signed-out owner until now; the account's from here on.
                files.open_local_folder(folder.join("attachments")).await.map_err(failed)?;
            }
        }
        api.publish(vec![removed(&[], &[])]);
    }
    Ok(json!({"ok": !truthy(&out["failed"]) || !moved.is_empty(), "moved": moved, "failed": out["failed"]}))
}

/// Delete the named chats made signed out for good, each with what belongs to it.
/// `{ok, deleted}`.
pub async fn delete_local_chats(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    imported(&api, "chats", "chats").await?;
    // Only who is signed in matters: nothing of the account is opened, so its data key
    // need not be on this PC.
    let account = account::current(api.repos.store().await.map_err(failed)?).await.map_err(failed)?.id;
    let chats = api.repos.chats().await.map_err(failed)?;
    let rows = match chats.remove(account, named(&args).unwrap_or_default()).await {
        Ok(rows) => rows,
        Err(error) => return Ok(json!({"ok": false, "error": or(error.0, "Couldn't delete them."), "deleted": []})),
    };
    let gone: Vec<PathBuf> = rows.iter().flat_map(|(conv_id, project)| folders(&api, conv_id, project)).collect();
    let cleared = tokio::task::spawn_blocking(move || gone.into_iter().filter_map(|folder| remove_chat_files(vec![folder]).err()).collect::<Vec<_>>());
    for left in cleared.await.unwrap_or_default() {
        // The chat itself is deleted; its folder goes with the next sweep.
        eprintln!("a deleted chat's folder was left behind: {left}");
    }
    let deleted: Vec<String> = rows.into_iter().map(|(conv_id, _)| conv_id).collect();
    if !deleted.is_empty() {
        api.publish(vec![removed(&deleted, &[])]);
    }
    Ok(json!({"ok": true, "deleted": deleted}))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_named_chats_are_read_as_python_reads_them() {
        let args = |ids: Value| json!({"conv_ids": ids}).as_object().unwrap().clone();
        assert_eq!(named(&Fields::new()), None);
        assert_eq!(named(&args(json!(null))), None);
        assert_eq!(named(&args(json!(["a", "", null, 7, 0]))), Some(vec!["a".to_string(), "7".to_string()]));
        assert_eq!(named(&args(json!([]))), Some(Vec::new()));
        assert_eq!((or(String::new(), "why"), or("it broke".into(), "why")), ("why".to_string(), "it broke".to_string()));
    }
}
