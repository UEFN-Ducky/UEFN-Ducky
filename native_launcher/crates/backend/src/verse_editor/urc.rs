//! Epic's revision control programs, `urc.exe` and `lore.exe`: where UEFN installs them
//! (`urc/detect_urc.py`), one run of either, and the source control pane's calls through
//! `urc.exe` (`urc/cli_service.py`).
//! ponytail: a program's output is read as UTF-8; Python reads `urc.exe`'s in the PC's
//! own code page, which garbles a name with an accent.

use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::Duration;

use serde_json::{json, Map, Value};
use tokio::process::Command;

use crate::api::project_paths::text;
use crate::store::pyjson::truthy;

type Fields = Map<String, Value>;

const WIN64: [&str; 5] = ["Epic Games", "Fortnite", "FortniteGame", "Binaries", "Win64"];
pub(super) const DRIVES: &str = "CDEFGHIJKLMNOPQRSTUVWXYZ";
/// One `urc.exe` call may take this long.
const URC_LIMIT: Duration = Duration::from_secs(120);

/// `str(Path(...))`: Windows spells every separator `\`.
pub(super) fn shown(path: &Path) -> String {
    if cfg!(windows) { text(path).replace('/', "\\") } else { text(path) }
}

fn fields(value: Value) -> Fields {
    value.as_object().cloned().unwrap_or_default()
}

/// `_detect`: `{available, path, source, error}`. The first that is a file: what an
/// environment variable names, the program under a Program Files folder, the same on
/// every drive.
fn detect(exe: &str, keys: &[&str]) -> Fields {
    let found = |path: &Path, source: &str| fields(json!({"available": true, "path": shown(path), "source": source, "error": ""}));
    for key in keys {
        let named = std::env::var(key).unwrap_or_default();
        if !named.trim().is_empty() && Path::new(named.trim()).is_file() {
            return found(Path::new(named.trim()), &key.to_lowercase());
        }
    }
    let under = |base: PathBuf| WIN64.iter().fold(base, |path, part| path.join(part)).join(exe);
    for name in ["ProgramW6432", "ProgramFiles(x86)", "ProgramFiles"] {
        let candidate = std::env::var_os(name).filter(|base| !base.is_empty()).map(|base| under(PathBuf::from(base)));
        if let Some(candidate) = candidate.filter(|candidate| candidate.is_file()) {
            return found(&candidate, name);
        }
    }
    for letter in DRIVES.chars() {
        let candidate = under(PathBuf::from(format!("{letter}:/Program Files")));
        if candidate.is_file() {
            return found(&candidate, "program_files_scan");
        }
    }
    fields(json!({"available": false, "path": "", "source": "", "error": format!("{exe} not found — set {} or install Fortnite/UEFN", keys[0])}))
}

async fn detected(exe: &'static str, keys: &'static [&'static str]) -> Fields {
    tokio::task::spawn_blocking(move || detect(exe, keys)).await.unwrap_or_default()
}

/// `detect_urc_executable`. Never `lore.exe` in its place: the calls here are urc's own.
pub async fn urc_program() -> Fields {
    detected("urc.exe", &["URC_CLI_LOCATION", "URC_PATH"]).await
}

/// `detect_lore_executable`: the Lore program UEFN ships beside `urc.exe`.
/// ponytail: on Linux, only what `LORE_CLI_LOCATION` names; the one inside UEFN's Wine
/// or Proton prefix is found once the prefix lookup is in this backend.
pub async fn lore_program() -> Fields {
    detected("lore.exe", &["LORE_CLI_LOCATION"]).await
}

/// What a program that ran left behind.
pub(super) struct Ran {
    /// Its exit code; -1 when a signal ended it.
    pub code: i32,
    pub out: String,
    pub err: String,
}

/// Why a program left nothing: it ran past its limit, or the OS would not start it.
pub(super) enum Stopped {
    Late,
    Os(String),
}

/// A program's output as Python's text mode reads it: every line ends with `\n`.
fn lines(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes).replace("\r\n", "\n").replace('\r', "\n")
}

/// Run a program in `cwd` without a window and wait for it, `limit` at most (it is
/// ended then).
pub(super) async fn run(program: &str, args: &[String], cwd: &str, limit: Duration) -> Result<Ran, Stopped> {
    let mut command = Command::new(program);
    command.args(args).current_dir(cwd).stdin(Stdio::null()).kill_on_drop(true);
    #[cfg(windows)]
    command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    match tokio::time::timeout(limit, command.output()).await {
        Err(_) => Err(Stopped::Late),
        Ok(Err(refused)) => Err(Stopped::Os(refused.to_string())),
        Ok(Ok(done)) => Ok(Ran { code: done.status.code().unwrap_or(-1), out: lines(&done.stdout), err: lines(&done.stderr) }),
    }
}

/// `_run_urc`: what it printed. `Err(Ok(text))` is urc's own refusal, `Err(Err(text))`
/// the OS's (the program or the folder is not there).
async fn run_urc(program: &str, root: &str, args: &[&str]) -> Result<String, Result<String, String>> {
    let args: Vec<String> = args.iter().map(|arg| arg.to_string()).collect();
    match run(program, &args, root, URC_LIMIT).await {
        Err(Stopped::Late) => Err(Ok(format!("urc {} timed out after 120 seconds", args.join(" ")))),
        Err(Stopped::Os(refused)) => Err(Err(refused)),
        Ok(ran) if ran.code != 0 => {
            let said = if ran.err.is_empty() { ran.out.trim() } else { ran.err.trim() };
            Err(Ok(if said.is_empty() { format!("urc exited {}", ran.code) } else { said.to_string() }))
        }
        Ok(ran) => Ok(ran.out.trim().to_string()),
    }
}

/// `_file_names`: a change list as paths, whatever shape its items have.
fn file_names(items: Option<&Value>) -> Vec<Value> {
    let name = |item: &Value| match item {
        Value::String(_) => Some(item.clone()),
        Value::Object(item) => ["path", "relativePath", "file", "filename", "name"].iter().filter_map(|key| item.get(*key)).find(|name| name.as_str().is_some_and(|name| !name.is_empty())).cloned(),
        _ => None,
    };
    items.and_then(Value::as_array).map(|items| items.iter().filter_map(name).collect()).unwrap_or_default()
}

/// `get_status`'s answer from what `urc project status --json` printed.
fn status(output: &str) -> Result<Fields, String> {
    let told: Value = serde_json::from_str(output).map_err(|_| format!("Invalid URC JSON: {}", output.chars().take(200).collect::<String>()))?;
    let data = match told {
        Value::Object(told) if told.get("data").is_some_and(Value::is_object) => told["data"].clone(),
        Value::Object(told) => Value::Object(told),
        other => json!({"data": other}),
    };
    let list = |long: &str, short: &str| file_names([long, short].iter().filter_map(|key| data.get(*key)).find(|list| truthy(list)));
    let (staged, unstaged) = (list("stagedChanges", "staged"), list("unstagedChanges", "unstaged"));
    Ok(fields(json!({"available": true, "staged_count": staged.len(), "unstaged_count": unstaged.len(), "staged": staged, "unstaged": unstaged, "raw": data, "error": ""})))
}

/// `configure`: where urc is, for this island.
async fn configured(root: &str) -> Fields {
    let mut found = urc_program().await;
    if found.get("path").is_some_and(truthy) {
        found = fields(json!({"available": true, "path": found["path"], "project_root": root, "error": ""}));
    } else {
        found.insert("project_root".into(), root.into());
    }
    found
}

/// The source control pane's changes: what `urc project status` lists for the island.
pub async fn get_status(root: &str) -> Result<Value, String> {
    let mut answer = configured(root).await;
    if !answer.get("available").is_some_and(truthy) {
        return Ok(Value::Object(answer));
    }
    if root.is_empty() {
        return Err("No project root".into());
    }
    let program = answer["path"].as_str().unwrap_or_default().to_string();
    match run_urc(&program, root, &["project", "status", "--json"]).await {
        Ok(output) => answer.extend(status(&output)?),
        Err(Ok(refused)) => return Err(refused),
        Err(Err(os)) => answer.extend(fields(json!({"error": os, "staged_count": 0, "unstaged_count": 0}))),
    }
    Ok(Value::Object(answer))
}

/// Ask UEFN to check the island in (`urc trigger --command CheckIn`).
pub async fn commit(root: &str, message: &str) -> Result<Value, String> {
    let found = configured(root).await;
    let Some(program) = found.get("path").and_then(Value::as_str).filter(|path| !path.is_empty()) else {
        return Err("URC not configured".into());
    };
    if root.is_empty() {
        return Err("No project root".into());
    }
    run_urc(program, root, &["trigger", "--command", "CheckIn", "--json"]).await.map_err(|refused| refused.unwrap_or_else(|os| os))?;
    Ok(json!({"ok": true, "message": if message.is_empty() { "CheckIn triggered" } else { message }}))
}

/// `urc.exe` has no push.
pub fn push() -> Value {
    json!({"ok": false, "error": "Push not available via URC CLI v1 in this project"})
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_status_counts_the_changes_whatever_their_shape() {
        let told = status(r#"{"data": {"stagedChanges": ["a.verse", {"relativePath": "b.verse"}, {"name": ""}, 4], "unstaged": [{"file": "c.verse"}]}}"#).unwrap();
        assert_eq!((told["staged_count"].clone(), told["unstaged_count"].clone()), (json!(2), json!(1)));
        assert_eq!(told["staged"], json!(["a.verse", "b.verse"]));
        // No `data` key: the whole answer is the data. A list has no changes to name.
        assert_eq!(status(r#"{"staged": ["x"]}"#).unwrap()["raw"], json!({"staged": ["x"]}));
        assert_eq!(status("[1]").unwrap()["raw"], json!({"data": [1]}));
        assert_eq!(status("nope ").unwrap_err(), "Invalid URC JSON: nope ");
    }

    #[test]
    fn output_is_read_with_unix_line_ends() {
        assert_eq!(lines(b"a\r\nb\rc\n\xff"), "a\nb\nc\n\u{fffd}");
    }
}
