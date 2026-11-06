//! Backend-mode file dialogs: the backend sends event `dialog {id, args}` (pywebview's
//! `create_file_dialog` arguments), the launcher shows the native dialog and answers with
//! a `__dialog_result` call carrying pywebview's return value (`null` when cancelled).

use dialogs::DialogRequest;
use serde_json::{json, Value};

/// The `(call id, value)` to send for a `dialog` event; None when it has no id.
/// A bad request or a dialog that fails answers `null`, so the backend never waits forever.
pub fn answer(
    data: &Value,
    show: impl FnOnce(&DialogRequest) -> Result<Option<Vec<String>>, String>,
) -> Option<(String, Value)> {
    let id = match data.get("id") {
        Some(Value::String(s)) if !s.is_empty() => s.clone(),
        Some(Value::Number(n)) => n.to_string(),
        _ => return None,
    };
    let args = data.get("args").cloned().unwrap_or(json!([]));
    let value = match DialogRequest::from_args(&args) {
        Ok(req) => match show(&req) {
            Ok(picked) => dialogs::result_value(req.kind, picked),
            Err(_) => Value::Null,
        },
        Err(_) => Value::Null,
    };
    Some((id, value))
}

/// The backend line [`answer`] turns into (what the supervisor writes).
pub fn call_line(id: &str, value: Value) -> String {
    supervisor::protocol::encode_call(id, "__dialog_result", &[value])
}

#[cfg(test)]
mod tests {
    use super::*;
    use dialogs::DialogKind;

    fn line(data: Value, picked: Option<Vec<&str>>) -> (String, Option<DialogKind>) {
        let mut seen = None;
        let (id, value) = answer(&data, |req| {
            seen = Some(req.kind);
            Ok(picked.map(|p| p.into_iter().map(String::from).collect()))
        })
        .expect("answered");
        (call_line(&id, value), seen)
    }

    fn parsed(l: &str) -> Value {
        serde_json::from_str(l.trim_end()).unwrap()
    }

    #[test]
    fn open_answers_the_list() {
        let (l, kind) = line(json!({"id": "d1", "args": [10, "C:/x", true]}), Some(vec!["C:/a", "C:/b"]));
        assert_eq!(kind, Some(DialogKind::Open));
        assert_eq!(parsed(&l), json!({"t": "call", "id": "d1", "name": "__dialog_result", "args": [["C:/a", "C:/b"]]}));
        assert!(l.ends_with('\n'));
    }

    #[test]
    fn save_answers_one_path() {
        let (l, kind) = line(json!({"id": "d2", "args": {"dialog_type": 30, "save_filename": "a.txt"}}), Some(vec!["C:/a.txt"]));
        assert_eq!(kind, Some(DialogKind::Save));
        assert_eq!(parsed(&l)["args"], json!(["C:/a.txt"]));
    }

    #[test]
    fn folder_answers_a_one_item_list() {
        let (l, kind) = line(json!({"id": "d3", "args": [20]}), Some(vec!["C:/dir"]));
        assert_eq!(kind, Some(DialogKind::Folder));
        assert_eq!(parsed(&l)["args"], json!([["C:/dir"]]));
    }

    #[test]
    fn cancel_answers_null() {
        let (l, _) = line(json!({"id": "d4", "args": [10]}), None);
        assert_eq!(parsed(&l)["args"], json!([null]));
    }

    #[test]
    fn bad_requests_answer_null_and_no_id_is_ignored() {
        let (id, v) = answer(&json!({"id": "d5", "args": [99]}), |_| panic!("not shown")).unwrap();
        assert_eq!((id.as_str(), v), ("d5", Value::Null));
        let (_, v) = answer(&json!({"id": "d6", "args": [10]}), |_| Err("com".into())).unwrap();
        assert_eq!(v, Value::Null);
        assert!(answer(&json!({"args": [10]}), |_| Ok(None)).is_none());
    }
}
