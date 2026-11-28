//! JSON the way the Python backend writes and compares it, so a row reads the same
//! whichever backend saved it.

use std::io;

use serde_json::ser::{Formatter, Serializer};
use serde_json::Value;
use unicode_properties::{GeneralCategoryGroup, UnicodeGeneralCategory};

/// `repr(float)`: the same shortest digits Rust prints, with Python's exponent
/// (`1e-07`, `1e+16`) and its switch to one a step earlier (`1e-05`, not `0.00001`).
pub fn float_repr(value: f64) -> String {
    let text = format!("{value:?}");
    if let Some((mantissa, exponent)) = text.split_once('e') {
        let (sign, digits) = exponent.strip_prefix('-').map_or(('+', exponent), |digits| ('-', digits));
        return format!("{mantissa}e{sign}{digits:0>2}");
    }
    let (sign, body) = text.strip_prefix('-').map_or(("", text.as_str()), |body| ("-", body));
    match body.strip_prefix("0.0000") {
        Some(digits) if !digits.is_empty() => {
            let (first, rest) = digits.split_at(1);
            format!("{sign}{first}{}{rest}e-05", if rest.is_empty() { "" } else { "." })
        }
        _ => text,
    }
}

/// `json.dumps(value, ensure_ascii=False)`: `", "` between items, `": "` after keys and
/// floats as Python prints them, so a message hashes the same whichever backend saved it.
/// ponytail: an integer past 64 bits is read as a float (Python keeps every digit), and
/// NaN / Infinity, which Python writes, do not parse here.
pub fn dumps(value: &Value) -> String {
    struct Py;
    impl Formatter for Py {
        fn write_f64<W: ?Sized + io::Write>(&mut self, w: &mut W, value: f64) -> io::Result<()> {
            w.write_all(float_repr(value).as_bytes())
        }
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

/// `json.dumps(value)`: [`dumps`] with everything outside printable ASCII as `\uXXXX`
/// (two of them for a character past the 16-bit range).
pub fn dumps_ascii(value: &Value) -> String {
    let mut out = String::new();
    for ch in dumps(value).chars() {
        if (' '..='~').contains(&ch) {
            out.push(ch);
        } else {
            out.extend(ch.encode_utf16(&mut [0; 2]).iter().map(|unit| format!("\\u{unit:04x}")));
        }
    }
    out
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
pub fn pystr(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        other => repr(other),
    }
}

/// `repr(value)`: what an f-string prints for a dict or list (`{'path': 'a.txt', 'n': None}`).
pub fn repr(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::Bool(b) => if *b { "True" } else { "False" }.into(),
        Value::Number(n) if n.is_f64() => float_repr(n.as_f64().unwrap_or(0.0)),
        Value::Number(n) => n.to_string(),
        Value::String(s) => str_repr(s),
        Value::Array(items) => format!("[{}]", items.iter().map(repr).collect::<Vec<_>>().join(", ")),
        Value::Object(map) => {
            let items: Vec<String> = map.iter().map(|(key, value)| format!("{}: {}", str_repr(key), repr(value))).collect();
            format!("{{{}}}", items.join(", "))
        }
    }
}

fn str_repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') { '"' } else { '\'' };
    let mut out = String::with_capacity(text.len() + 2);
    out.push(quote);
    for ch in text.chars() {
        // Python's str.isprintable: not in an "Other" or "Separator" category, but for the space.
        let hidden = matches!(ch.general_category_group(), GeneralCategoryGroup::Other | GeneralCategoryGroup::Separator);
        match ch {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            ch if ch == quote => out.extend(['\\', ch]),
            ch if ch == ' ' || !hidden => out.push(ch),
            ch if (ch as u32) < 0x100 => out.push_str(&format!("\\x{:02x}", ch as u32)),
            ch if (ch as u32) < 0x10000 => out.push_str(&format!("\\u{:04x}", ch as u32)),
            ch => out.push_str(&format!("\\U{:08x}", ch as u32)),
        }
    }
    out.push(quote);
    out
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
        // `@` stands for the backslash of each escape.
        let escaped = r#"{"@u00e9": "a@u007f@n@ud83d@ude00 ~"}"#.replace('@', "\\");
        assert_eq!(dumps_ascii(&json!({"é": "a\u{7f}\n😀 ~"})), escaped);
    }

    #[test]
    fn floats_print_as_python_prints_them() {
        let printed = [
            (1.0, "1.0"), (0.1, "0.1"), (-2.5, "-2.5"), (1e-7, "1e-07"), (1.5e-7, "1.5e-07"), (0.0001, "0.0001"),
            (0.00001, "1e-05"), (-0.00001234, "-1.234e-05"), (1e15, "1000000000000000.0"), (1e16, "1e+16"),
            (1.2345e20, "1.2345e+20"), (1e100, "1e+100"), (1e-100, "1e-100"), (0.0, "0.0"), (123456.789, "123456.789"),
        ];
        for (value, python) in printed {
            assert_eq!(float_repr(value), python);
        }
        assert_eq!(dumps(&json!([1e-7, 1e16, 0.00005, 3])), "[1e-07, 1e+16, 5e-05, 3]");
    }

    #[test]
    fn equality_and_truth_are_pythons() {
        assert!(py_eq(&json!({"a": 1, "b": [true, 2]}), &json!({"b": [1, 2.0], "a": 1.0})));
        assert!(!py_eq(&json!([1, 2]), &json!([1, 3])) && !py_eq(&json!("1"), &json!(1)) && !py_eq(&json!(null), &json!(0)));
        assert!(!truthy(&json!([])) && !truthy(&json!(0)) && !truthy(&json!("")) && truthy(&json!([0])));
        assert_eq!((int(&json!(7.9)), int(&json!("12")), int(&json!(null)), int(&json!(true))), (7, 12, 0, 1));
        assert_eq!((pystr(&json!("a")), pystr(&json!(null)), pystr(&json!(true)), pystr(&json!(1.5))), ("a".into(), "None".into(), "True".into(), "1.5".into()));
        let value = json!({"p": "it's", "q": "a\"b'\n\u{1f}\u{a0}é", "n": [1, 2.0, null, false]});
        assert_eq!(repr(&value), r#"{'p': "it's", 'q': 'a"b\'\n\x1f\xa0é', 'n': [1, 2.0, None, False]}"#);
        assert_eq!((float(&json!(null)), float(&json!(" 2.5 ")), float(&json!("x")), float(&json!(3))), (Some(0.0), Some(2.5), None, Some(3.0)));
    }
}
