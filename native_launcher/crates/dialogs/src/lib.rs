//! Native file dialogs that take pywebview's `create_file_dialog` arguments and give back
//! the same shapes pywebview (winforms / EdgeChromium, 6.x) returned:
//! - open: a list of paths (one item unless `allow_multiple`), `null` on cancel;
//! - folder: a one-item list, `null` on cancel;
//! - save: a path string, `null` on cancel.
//!
//! Filters are pywebview strings like `"Audio (*.mp3;*.wav)"`; one that pywebview would
//! reject (e.g. a hyphen in an extension) is an error here too, so the Python callers'
//! "retry with All files (*.*)" fallback keeps working.

use serde_json::{json, Value};

#[cfg(windows)]
mod win;

/// pywebview's `FileDialog` values (`OPEN_DIALOG=10`, `FOLDER_DIALOG=20`, `SAVE_DIALOG=30`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DialogKind {
    Open,
    Folder,
    Save,
}

impl DialogKind {
    pub fn from_pywebview(v: i64) -> Option<Self> {
        match v {
            10 => Some(Self::Open),
            20 => Some(Self::Folder),
            30 => Some(Self::Save),
            _ => None,
        }
    }

    pub fn from_value(v: &Value) -> Option<Self> {
        match v {
            Value::Number(n) => n.as_i64().and_then(Self::from_pywebview),
            Value::String(s) => match s.to_ascii_lowercase().as_str() {
                "open" | "10" => Some(Self::Open),
                "folder" | "20" => Some(Self::Folder),
                "save" | "30" => Some(Self::Save),
                _ => None,
            },
            _ => None,
        }
    }
}

/// One parsed filter: `name` shown in the box, `spec` like `*.mp3;*.wav`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileFilter {
    pub name: String,
    pub spec: String,
}

impl FileFilter {
    /// The first concrete extension (`"mp3"` for `*.mp3;*.wav`), for the save box's default.
    pub fn default_extension(&self) -> Option<&str> {
        self.spec
            .split(';')
            .filter_map(|p| p.trim().strip_prefix("*."))
            .find(|e| !e.is_empty() && *e != "*")
    }
}

fn is_word(s: &str) -> bool {
    !s.is_empty() && s.chars().all(|c| c.is_alphanumeric() || c == '_')
}

/// Parse a pywebview filter string the way `webview.util.parse_file_type` does:
/// `^([\w ]+)\((\*(?:\.(?:\w+|\*))*(?:;\s*\*\.\w+)*)\)$`.
pub fn parse_file_type(s: &str) -> Result<FileFilter, String> {
    let bad = || format!("{s} is not a valid file filter");
    let open = s.find('(').ok_or_else(bad)?;
    let (name, rest) = s.split_at(open);
    if name.is_empty() || !name.chars().all(|c| c.is_alphanumeric() || c == '_' || c == ' ') {
        return Err(bad());
    }
    let inner = rest.strip_prefix('(').and_then(|r| r.strip_suffix(')')).ok_or_else(bad)?;
    let mut parts = inner.split(';');
    // First part: `*` then any number of `.word` / `.*`.
    let first = parts.next().ok_or_else(bad)?;
    let tail = first.strip_prefix('*').ok_or_else(bad)?;
    if !tail.is_empty() {
        let segs = tail.strip_prefix('.').ok_or_else(bad)?;
        for seg in segs.split('.') {
            if !(seg == "*" || is_word(seg)) {
                return Err(bad());
            }
        }
    }
    let mut specs = vec![first.to_string()];
    for p in parts {
        let p = p.trim_start();
        let ext = p.strip_prefix("*.").ok_or_else(bad)?;
        if !is_word(ext) {
            return Err(bad());
        }
        specs.push(p.to_string());
    }
    Ok(FileFilter { name: name.trim_end().to_string(), spec: specs.join(";") })
}

/// A dialog request in pywebview's terms.
#[derive(Debug, Clone, PartialEq)]
pub struct DialogRequest {
    pub kind: DialogKind,
    /// Starting folder; empty means the system default.
    pub directory: String,
    pub allow_multiple: bool,
    /// Suggested file name for the save box.
    pub save_filename: String,
    pub filters: Vec<FileFilter>,
}

impl DialogRequest {
    pub fn new(kind: DialogKind) -> Self {
        Self { kind, directory: String::new(), allow_multiple: false, save_filename: String::new(), filters: Vec::new() }
    }

    /// Build from pywebview's positional or keyword arguments:
    /// `[dialog_type, directory, allow_multiple, save_filename, file_types]` or
    /// `{"dialog_type":..,"directory":..,"allow_multiple":..,"save_filename":..,"file_types":[..]}`.
    pub fn from_args(args: &Value) -> Result<Self, String> {
        let get = |i: usize, key: &str| -> Option<&Value> {
            match args {
                Value::Array(a) => a.get(i),
                Value::Object(o) => o.get(key),
                _ => None,
            }
            .filter(|v| !v.is_null())
        };
        let kind = match get(0, "dialog_type") {
            None => DialogKind::Open,
            Some(v) => DialogKind::from_value(v).ok_or_else(|| format!("unknown dialog_type {v}"))?,
        };
        let text = |i, key| get(i, key).and_then(Value::as_str).unwrap_or("").to_string();
        let mut req = Self::new(kind);
        req.directory = text(1, "directory");
        req.allow_multiple = get(2, "allow_multiple").and_then(Value::as_bool).unwrap_or(false);
        req.save_filename = text(3, "save_filename");
        match get(4, "file_types") {
            None => {}
            Some(Value::Array(items)) => {
                for item in items {
                    let s = item.as_str().ok_or("file_types must be strings")?;
                    req.filters.push(parse_file_type(s)?);
                }
            }
            Some(Value::String(s)) => req.filters.push(parse_file_type(s)?),
            Some(_) => return Err("file_types must be a list of strings".into()),
        }
        Ok(req)
    }
}

/// What pywebview would have returned for this kind and pick (`None` = cancelled).
pub fn result_value(kind: DialogKind, picked: Option<Vec<String>>) -> Value {
    let Some(paths) = picked.filter(|p| !p.is_empty()) else {
        return Value::Null;
    };
    match kind {
        DialogKind::Save => Value::String(paths.into_iter().next().unwrap_or_default()),
        DialogKind::Folder => json!([paths[0]]),
        DialogKind::Open => json!(paths),
    }
}

/// Show the dialog owned by `owner_hwnd` (0 = no owner) and return the picked paths,
/// `Ok(None)` when cancelled. Must run on a thread that may pump messages (the UI thread).
#[cfg(windows)]
pub fn show(owner_hwnd: isize, req: &DialogRequest) -> Result<Option<Vec<String>>, String> {
    win::show(owner_hwnd, req)
}

#[cfg(not(windows))]
pub fn show(_owner_hwnd: isize, _req: &DialogRequest) -> Result<Option<Vec<String>>, String> {
    Err("native dialogs are Windows only".into())
}

/// `show` then `result_value`: the value to send back where pywebview's return went.
pub fn pick(owner_hwnd: isize, req: &DialogRequest) -> Result<Value, String> {
    Ok(result_value(req.kind, show(owner_hwnd, req)?))
}
