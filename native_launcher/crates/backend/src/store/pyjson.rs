//! JSON the way the Python backend writes and compares it, so a row reads the same
//! whichever backend saved it.

use std::io;

use serde_json::ser::{Formatter, Serializer};
use serde_json::Value;

/// `json.dumps(value, ensure_ascii=False)`: `", "` between items and `": "` after keys.
/// ponytail: floats with an exponent print as `1e-7` where Python prints `1e-07`; both
/// parse to the same number.
pub fn dumps(value: &Value) -> String {
    struct Py;
    impl Formatter for Py {
        fn begin_array_value<W: ?Sized + io::Write>(&mut self, w: &mut W, first: bool) -> io::Result<()> {
            if first { Ok(()) } else { w.write_all(b", ") }
        }
        fn begin_object_key<W: ?Sized + io::Write>(&mut self, w: &mut W, first: bool) -> io::Result<()> {
            if first { Ok(()) } else { w.write_all(b", ") }
        }
        fn begin_object_value<W: ?Sized + io::Write>(&mut self, w: &mut W) -> io::Result<()> {
            w.write_all(b": ")
        }
    }
    let mut out = Vec::new();
    // Writing a Value into a Vec cannot fail.
    let _ = serde::Serialize::serialize(value, &mut Serializer::with_formatter(&mut out, Py));
    String::from_utf8(out).unwrap_or_default()
}

/// The value with every object's keys in order: `dumps(&sorted(v))` is
/// `json.dumps(v, ensure_ascii=False, sort_keys=True)`.
pub fn sorted(value: &Value) -> Value {
    match value {
        Value::Array(items) => Value::Array(items.iter().map(sorted).collect()),
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            Value::Object(keys.into_iter().map(|key| (key.clone(), sorted(&map[key]))).collect())
        }
        other => other.clone(),
    }
}

/// Python's `==` on parsed JSON: `1 == 1.0 == True`, and key order never matters.
pub fn py_eq(a: &Value, b: &Value) -> bool {
    let bit = |b: bool| if b { 1.0 } else { 0.0 };
    match (a, b) {
        (Value::Number(x), Value::Number(y)) => x == y || x.as_f64() == y.as_f64(),
        (Value::Bool(x), Value::Number(n)) | (Value::Number(n), Value::Bool(x)) => n.as_f64() == Some(bit(*x)),
        (Value::Array(x), Value::Array(y)) => x.len() == y.len() && x.iter().zip(y).all(|(x, y)| py_eq(x, y)),
        (Value::Object(x), Value::Object(y)) => {
            x.len() == y.len() && x.iter().all(|(k, v)| y.get(k).is_some_and(|w| py_eq(v, w)))
        }
        _ => a == b,
    }
}

/// Python's truth of a parsed JSON value (`value or default`).
pub fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `int(value or 0)` for the numbers the repos store.
pub fn int(value: &Value) -> i64 {
    match value {
        Value::Bool(b) => *b as i64,
        Value::Number(n) => n.as_i64().unwrap_or_else(|| n.as_f64().unwrap_or(0.0) as i64),
        Value::String(s) => s.trim().parse().unwrap_or(0),
        _ => 0,
    }
}

/// `str(value)` for what a JSON document holds.
/// ponytail: a list or object prints as JSON, not as Python's repr; nothing stores one.
pub fn pystr(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Null => "None".into(),
        Value::Bool(b) => if *b { "True" } else { "False" }.into(),
        other => other.to_string(),
    }
}

/// `float(value or 0.0)`; None where Python raises (text that is not a number, a list).
pub fn float(value: &Value) -> Option<f64> {
    match value {
        _ if !truthy(value) => Some(0.0),
        Value::Bool(_) => Some(1.0),
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn dumps_is_pythons_text() {
        let value = json!({"b": [1, 2.5, "é\n\"x\""], "a": {"k": null, "t": true}, "e": [], "o": {}});
        assert_eq!(dumps(&value), r#"{"b": [1, 2.5, "é\n\"x\""], "a": {"k": null, "t": true}, "e": [], "o": {}}"#);
    }

    #[test]
    fn equality_and_truth_are_pythons() {
        assert!(py_eq(&json!({"a": 1, "b": [true, 2]}), &json!({"b": [1, 2.0], "a": 1.0})));
        assert!(!py_eq(&json!([1, 2]), &json!([1, 3])) && !py_eq(&json!("1"), &json!(1)) && !py_eq(&json!(null), &json!(0)));
        assert!(!truthy(&json!([])) && !truthy(&json!(0)) && !truthy(&json!("")) && truthy(&json!([0])));
        assert_eq!((int(&json!(7.9)), int(&json!("12")), int(&json!(null)), int(&json!(true))), (7, 12, 0, 1));
        assert_eq!((pystr(&json!("a")), pystr(&json!(null)), pystr(&json!(true)), pystr(&json!(1.5))), ("a".into(), "None".into(), "True".into(), "1.5".into()));
        assert_eq!((float(&json!(null)), float(&json!(" 2.5 ")), float(&json!("x")), float(&json!(3))), (Some(0.0), Some(2.5), None, Some(3.0)));
    }
}
