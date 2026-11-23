//! The portal request a pywebview-style call becomes, and the shapes it gives back, with
//! the portal faked.
#![cfg(target_os = "linux")]

use std::cell::RefCell;
use std::path::PathBuf;

use dialogs::portal::{portal_call, show_with, PortalCall};
use dialogs::{result_value, DialogKind, DialogRequest};
use serde_json::{json, Value};

fn pick(args: Value, answer: Option<Vec<&str>>) -> (PortalCall, Value) {
    let req = DialogRequest::from_args(&args).unwrap();
    let seen = RefCell::new(None);
    let got = show_with(&req, |c| {
        *seen.borrow_mut() = Some(c.clone());
        answer.map(|a| a.into_iter().map(PathBuf::from).collect())
    });
    (seen.into_inner().unwrap(), result_value(req.kind, got))
}

#[test]
fn add_project_opens_the_folder_picker() {
    let (call, v) = pick(json!([20, "/tmp"]), Some(vec!["/home/a/Fortnite Projects/Island"]));
    assert_eq!(call.kind, DialogKind::Folder);
    assert_eq!(call.directory, Some(PathBuf::from("/tmp")));
    assert!(!call.multiple);
    assert_eq!(v, json!(["/home/a/Fortnite Projects/Island"]));
}

#[test]
fn open_many_with_filters() {
    let args = json!({"dialog_type": 10, "allow_multiple": true, "file_types": ["Audio (*.mp3;*.wav)", "All files (*.*)"]});
    let (call, v) = pick(args, Some(vec!["/a.mp3", "/b.wav"]));
    assert!(call.multiple);
    assert_eq!(call.filters, vec![("Audio".to_string(), vec!["mp3".to_string(), "wav".to_string()])]);
    assert_eq!(v, json!(["/a.mp3", "/b.wav"]));
}

#[test]
fn save_gives_a_string_and_keeps_the_name() {
    let (call, v) = pick(json!([30, "", false, "plan.md"]), Some(vec!["/home/a/plan.md"]));
    assert_eq!(call.file_name.as_deref(), Some("plan.md"));
    assert_eq!(call.directory, None);
    assert_eq!(v, json!("/home/a/plan.md"));
}

#[test]
fn cancel_is_null_and_a_missing_folder_is_not_sent() {
    let (call, v) = pick(json!([10, "/no/such/dir"]), None);
    assert_eq!(call.directory, None);
    assert_eq!(v, Value::Null);
}

#[test]
fn multiple_only_applies_to_open() {
    let req = DialogRequest { allow_multiple: true, ..DialogRequest::new(DialogKind::Save) };
    assert!(!portal_call(&req).multiple);
}
