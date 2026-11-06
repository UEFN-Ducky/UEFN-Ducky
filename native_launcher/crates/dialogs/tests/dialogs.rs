//! Filter strings (as pywebview 6 parses them) and result shapes. No dialog is shown.

use dialogs::{parse_file_type, result_value, DialogKind, DialogRequest, FileFilter};
use serde_json::{json, Value};

#[test]
fn filters_used_by_ducky_parse() {
    let cases = [
        ("All files (*.*)", "All files", "*.*"),
        ("Skill packs (*.zip)", "Skill packs", "*.zip"),
        ("Audio (*.mp3;*.wav;*.ogg;*.m4a;*.webm)", "Audio", "*.mp3;*.wav;*.ogg;*.m4a;*.webm"),
        ("Images (*.png; *.jpg)", "Images", "*.png;*.jpg"),
        ("Anything (*)", "Anything", "*"),
    ];
    for (input, name, spec) in cases {
        assert_eq!(parse_file_type(input).unwrap(), FileFilter { name: name.into(), spec: spec.into() }, "{input}");
    }
}

#[test]
fn filters_pywebview_rejects_are_rejected() {
    for bad in [
        "Skill packs (*.ducky-skill-pack;*.zip)",
        "No parens",
        "(*.zip)",
        "Zip (*.zip",
        "Zip (zip)",
        "Zip (*.zip;.txt)",
        "Bad-name (*.zip)",
    ] {
        assert!(parse_file_type(bad).is_err(), "{bad}");
    }
}

#[test]
fn default_extension() {
    assert_eq!(parse_file_type("Skill packs (*.zip)").unwrap().default_extension(), Some("zip"));
    assert_eq!(parse_file_type("All files (*.*)").unwrap().default_extension(), None);
    assert_eq!(parse_file_type("Audio (*.mp3;*.wav)").unwrap().default_extension(), Some("mp3"));
}

#[test]
fn request_from_pywebview_args() {
    let kw = json!({"dialog_type": 30, "save_filename": "pack.zip", "file_types": ["Skill packs (*.zip)", "All files (*.*)"]});
    let r = DialogRequest::from_args(&kw).unwrap();
    assert_eq!(r.kind, DialogKind::Save);
    assert_eq!(r.save_filename, "pack.zip");
    assert_eq!(r.filters.len(), 2);

    let pos = json!([10, "C:\\Users", true, null, ["Audio (*.mp3;*.wav)"]]);
    let r = DialogRequest::from_args(&pos).unwrap();
    assert_eq!((r.kind, r.directory.as_str(), r.allow_multiple), (DialogKind::Open, "C:\\Users", true));

    assert_eq!(DialogRequest::from_args(&json!([20])).unwrap().kind, DialogKind::Folder);
    assert_eq!(DialogRequest::from_args(&json!({})).unwrap().kind, DialogKind::Open);
    assert!(DialogRequest::from_args(&json!([99])).is_err());
    assert!(DialogRequest::from_args(&json!({"file_types": ["Bad (*.a-b)"]})).is_err());
}

#[test]
fn result_shapes_match_pywebview() {
    let one = Some(vec!["C:\\a.txt".to_string()]);
    let two = Some(vec!["C:\\a.txt".to_string(), "C:\\b.txt".to_string()]);
    assert_eq!(result_value(DialogKind::Open, one.clone()), json!(["C:\\a.txt"]));
    assert_eq!(result_value(DialogKind::Open, two), json!(["C:\\a.txt", "C:\\b.txt"]));
    assert_eq!(result_value(DialogKind::Folder, Some(vec!["C:\\proj".into()])), json!(["C:\\proj"]));
    assert_eq!(result_value(DialogKind::Save, one), json!("C:\\a.txt"));
    for kind in [DialogKind::Open, DialogKind::Folder, DialogKind::Save] {
        assert_eq!(result_value(kind, None), Value::Null);
        assert_eq!(result_value(kind, Some(vec![])), Value::Null);
    }
}

#[test]
fn kind_values() {
    assert_eq!(DialogKind::from_pywebview(10), Some(DialogKind::Open));
    assert_eq!(DialogKind::from_pywebview(20), Some(DialogKind::Folder));
    assert_eq!(DialogKind::from_pywebview(30), Some(DialogKind::Save));
    assert_eq!(DialogKind::from_value(&json!("save")), Some(DialogKind::Save));
    assert_eq!(DialogKind::from_pywebview(1), None);
}
