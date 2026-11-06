//! The backend contract: JSON lines, one object per line, UTF-8, at most 1 MB.
//! Unknown fields are ignored; a protocol outside `[PROTOCOL_MIN, PROTOCOL_MAX]` is a mismatch.

use serde_json::{json, Value};

pub const PROTOCOL_MIN: i64 = 1;
pub const PROTOCOL_MAX: i64 = 1;
pub const MAX_LINE: usize = 1024 * 1024;

#[derive(Debug, Clone, PartialEq)]
pub struct Hello {
    pub protocol: i64,
    pub app_version: String,
    pub port: u16,
    pub origin: String,
    pub profile_dir: String,
    pub pid: u32,
    /// Another Ducky took this launch; a `quit {reason:"handoff"}` event follows.
    pub handoff: bool,
}

impl Hello {
    pub fn protocol_ok(&self) -> bool {
        (PROTOCOL_MIN..=PROTOCOL_MAX).contains(&self.protocol)
    }
}

/// One line from the backend's stdout.
#[derive(Debug, Clone, PartialEq)]
pub enum BackendMsg {
    Hello(Hello),
    /// `ok: true` gives `Ok(value)`, `ok: false` gives `Err(error)`.
    Result { id: String, result: Result<Value, String> },
    Event { name: String, data: Value },
    /// Valid JSON object with an unknown `t`: ignored by callers.
    Other(Value),
}

fn s(v: &Value, k: &str) -> String {
    match v.get(k) {
        Some(Value::String(x)) => x.clone(),
        Some(Value::Number(n)) => n.to_string(),
        _ => String::new(),
    }
}

/// Parse one stdout line; None for blank lines, non-JSON, non-objects or a bad hello.
pub fn parse_line(line: &str) -> Option<BackendMsg> {
    let line = line.trim();
    if line.is_empty() || line.len() > MAX_LINE {
        return None;
    }
    let v: Value = serde_json::from_str(line).ok()?;
    if !v.is_object() {
        return None;
    }
    match v.get("t").and_then(Value::as_str) {
        Some("hello") => {
            let protocol = v.get("protocol")?.as_i64()?;
            let port = v.get("port").and_then(Value::as_u64).filter(|p| *p <= u16::MAX as u64).unwrap_or(0) as u16;
            let mut origin = s(&v, "origin");
            if origin.is_empty() && port != 0 {
                origin = format!("http://127.0.0.1:{port}");
            }
            Some(BackendMsg::Hello(Hello {
                protocol,
                app_version: s(&v, "app_version"),
                port,
                origin,
                profile_dir: s(&v, "profile_dir"),
                pid: v.get("pid").and_then(Value::as_u64).unwrap_or(0) as u32,
                handoff: v.get("handoff").and_then(Value::as_bool).unwrap_or(false),
            }))
        }
        Some("result") => {
            let id = s(&v, "id");
            let ok = v.get("ok").and_then(Value::as_bool).unwrap_or(false);
            let result = if ok {
                Ok(v.get("value").cloned().unwrap_or(Value::Null))
            } else {
                Err(match v.get("error") {
                    Some(Value::String(e)) => e.clone(),
                    Some(other) => other.to_string(),
                    None => "error".to_string(),
                })
            };
            Some(BackendMsg::Result { id, result })
        }
        Some("event") => Some(BackendMsg::Event {
            name: s(&v, "name"),
            data: v.get("data").cloned().unwrap_or(Value::Object(Default::default())),
        }),
        _ => Some(BackendMsg::Other(v)),
    }
}

/// `{"t":"call","id":..,"name":..,"args":[..]}` plus the newline.
pub fn encode_call(id: &str, name: &str, args: &[Value]) -> String {
    let mut line = json!({"t": "call", "id": id, "name": name, "args": args}).to_string();
    line.push('\n');
    line
}

/// `{"t":"bye","reason":..}` plus the newline.
pub fn encode_bye(reason: &str) -> String {
    let mut line = json!({"t": "bye", "reason": reason}).to_string();
    line.push('\n');
    line
}
