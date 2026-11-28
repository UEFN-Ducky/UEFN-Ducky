//! A verse-lsp of the scan's own (`verse_editor/lsp/stdio_session.py`): Ducky is its
//! client over stdio. The editor's verse-lsp is never used for a scan: 88 opened files
//! starved every completion and hover of the person typing.

use std::collections::HashMap;
use std::path::Path;
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::Duration;

use serde_json::{json, Map, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::ChildStdin;
use tokio::sync::{oneshot, Notify};

use super::{lsp, lsp_detect};
use crate::store::pyjson::{pystr, truthy};

pub const NOT_RUNNING: &str = "LSP process not running";

/// What verse-lsp sent so far.
#[derive(Default)]
struct Inbox {
    /// The diagnostics last published per document, in the order first published.
    published: Map<String, Value>,
    /// The last publish per document that held a syntax error.
    syntax: Map<String, Value>,
    /// Requests waiting for their answer.
    waiting: HashMap<i64, oneshot::Sender<Value>>,
    ended: bool,
}

type Stdin = Arc<tokio::sync::Mutex<ChildStdin>>;

pub struct Session {
    root: String,
    stdin: Stdin,
    inbox: Arc<Mutex<Inbox>>,
    kill: Arc<Notify>,
    next_id: AtomicI64,
}

fn lock(inbox: &Mutex<Inbox>) -> MutexGuard<'_, Inbox> {
    inbox.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
}

/// `urllib.parse.quote(text, safe="/")`.
fn quote(text: &str) -> String {
    let keep = |byte: u8| byte.is_ascii_alphanumeric() || b"_.-~/".contains(&byte);
    text.bytes().map(|byte| if keep(byte) { (byte as char).to_string() } else { format!("%{byte:02X}") }).collect()
}

/// `to_monaco_file_uri`: the `file:///` address of a path as the editor writes it, the
/// drive letter small.
pub fn file_uri(path: &str) -> String {
    let full = path.replace('\\', "/");
    let mut letters = full.chars();
    match (letters.next(), letters.next()) {
        (Some(drive), Some(':')) => format!("file:///{}", quote(&format!("{}:{}", drive.to_lowercase(), letters.as_str()))),
        _ => format!("file:///{}", quote(full.trim_start_matches('/'))),
    }
}

/// `_norm_uri`: one spelling of an address; verse-lsp writes ours back with other
/// letter case and other `%XX`.
fn norm_uri(uri: &str) -> String {
    let (bytes, mut out, mut at) = (uri.as_bytes(), Vec::new(), 0);
    while at < bytes.len() {
        let hex = bytes.get(at + 1..at + 3).and_then(|pair| std::str::from_utf8(pair).ok()).and_then(|pair| u8::from_str_radix(pair, 16).ok());
        match hex.filter(|_| bytes[at] == b'%') {
            Some(byte) => {
                out.push(byte);
                at += 3;
            }
            None => {
                out.push(bytes[at]);
                at += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).replace('\\', "/").to_lowercase()
}

/// `is_syntax_error`: a parse error (`vErr:S…`), not a type or name check.
fn is_syntax_error(diag: &Value) -> bool {
    let text = |name: &str| Some(&diag[name]).filter(|value| truthy(value)).map(pystr).unwrap_or_default();
    let severe = diag["severity"].is_null() || diag["severity"].as_f64() == Some(1.0);
    severe && (text("code").starts_with("vErr:S") || text("message").starts_with("vErr:S"))
}

/// `handle_server_requests`: the answer to what verse-lsp asks its client.
fn answer(method: &str, params: &Value) -> Value {
    match method {
        "workspace/configuration" => params["items"].as_array().map_or_else(|| json!([]), |items| items.iter().map(|_| json!({})).collect()),
        "workspace/applyEdit" => json!({"applied": false}),
        _ => Value::Null,
    }
}

async fn write(stdin: &Stdin, message: &Value) -> Result<(), String> {
    let body = message.to_string();
    let mut out = format!("Content-Length: {}\r\n\r\n", body.len()).into_bytes();
    out.extend(body.as_bytes());
    let mut stdin = stdin.lock().await;
    // ponytail: Python reports the OS's own words for a closed pipe here.
    stdin.write_all(&out).await.map_err(|_| NOT_RUNNING.to_string())?;
    stdin.flush().await.map_err(|_| NOT_RUNNING.to_string())
}

impl Session {
    /// Start a verse-lsp in `root`; an error says why none could be.
    pub async fn start(root: &str, data_dir: &Path) -> Result<Session, String> {
        let dir = data_dir.to_path_buf();
        let found = tokio::task::spawn_blocking(move || lsp_detect::detect(&dir)).await.map_err(|stopped| stopped.to_string())?.map_err(|failed| failed.to_string())?;
        if found["available"] != true {
            return Err(found["error"].as_str().filter(|error| !error.is_empty()).unwrap_or("verse-lsp not found").to_string());
        }
        let mut child = lsp::command(found["path"].as_str().unwrap_or_default(), root).spawn().map_err(|failed| failed.to_string())?;
        let pipes = (child.stdin.take(), child.stdout.take(), child.stderr.take());
        let (Some(stdin), Some(stdout), Some(stderr)) = pipes else { return Err("verse-lsp started without its pipes".into()) };
        let session = Session { root: root.to_string(), stdin: Arc::new(tokio::sync::Mutex::new(stdin)), inbox: Arc::default(), kill: Arc::default(), next_id: AtomicI64::new(1) };
        tokio::spawn(async move {
            let mut lines = BufReader::new(stderr).split(b'\n');
            while let Ok(Some(_)) = lines.next_segment().await {}
        });
        let (inbox, stdin) = (Arc::clone(&session.inbox), Arc::clone(&session.stdin));
        tokio::spawn(async move {
            let mut stdout = BufReader::new(stdout);
            while let Some(raw) = lsp::message(&mut stdout).await {
                if let Ok(Value::Object(message)) = serde_json::from_slice(&raw) {
                    read(&inbox, &stdin, message).await;
                }
            }
        });
        let (inbox, kill) = (Arc::clone(&session.inbox), Arc::clone(&session.kill));
        tokio::spawn(async move {
            tokio::select! {
                _ = child.wait() => {}
                () = kill.notified() => drop(child.kill().await),
            }
            let mut inbox = lock(&inbox);
            inbox.ended = true;
            // Whoever waits for an answer hears now that none comes.
            inbox.waiting.clear();
        });
        Ok(session)
    }

    /// `file_uri`: the address of a path under the island.
    pub fn uri(&self, relative: &str) -> String {
        let root = self.root.replace('\\', "/");
        let (root, relative) = (root.trim_end_matches('/'), relative.replace('\\', "/"));
        let relative = relative.trim_start_matches('/');
        file_uri(&if relative.is_empty() { root.to_string() } else { format!("{root}/{relative}") })
    }

    /// False once the process ended, by itself or stopped: a server that stops
    /// answering must never read as "checked, clean".
    pub fn is_alive(&self) -> bool {
        !lock(&self.inbox).ended
    }

    pub fn stop(&self) {
        lock(&self.inbox).ended = true;
        self.kill.notify_one();
    }

    pub async fn notify(&self, method: &str, params: Value) -> Result<(), String> {
        if !self.is_alive() {
            return Err(NOT_RUNNING.into());
        }
        write(&self.stdin, &json!({"jsonrpc": "2.0", "method": method, "params": params})).await
    }

    /// The result of a request; an error for verse-lsp's own error, no answer in time,
    /// or a process that ended.
    pub async fn request(&self, method: &str, params: Value, limit: Duration) -> Result<Value, String> {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let (tell, told) = oneshot::channel();
        {
            let mut inbox = lock(&self.inbox);
            if inbox.ended {
                return Err(NOT_RUNNING.into());
            }
            inbox.waiting.insert(id, tell);
        }
        write(&self.stdin, &json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params})).await?;
        match tokio::time::timeout(limit, told).await {
            Ok(Ok(answer)) if answer.get("error").is_some() => Err(pystr(&answer["error"])),
            Ok(Ok(answer)) => Ok(answer["result"].clone()),
            Ok(Err(_)) => Err(NOT_RUNNING.into()),
            Err(_) => {
                lock(&self.inbox).waiting.remove(&id);
                Err(format!("LSP request timed out: {method}"))
            }
        }
    }

    /// `diagnostics_for_uri`: what was published under exactly this address.
    pub fn diagnostics_for(&self, uri: &str) -> Vec<Value> {
        lock(&self.inbox).published.get(uri).and_then(Value::as_array).cloned().unwrap_or_default()
    }

    /// `published_diagnostics`: what verse-lsp published for a document; None while it
    /// has not (an empty list is "checked, clean").
    pub fn published(&self, uri: &str) -> Option<Vec<Value>> {
        find(&lock(&self.inbox).published, uri)
    }

    /// `syntax_errors`: the last publish for a document that held a syntax error.
    /// verse-lsp flips between a file's real errors and none for the same text (Oct 17
    /// 2026: 1, 0, 1, 1, 0 in one scan); a parse error for unchanged text is real.
    pub fn syntax_errors(&self, uri: &str) -> Vec<Value> {
        find(&lock(&self.inbox).syntax, uri).unwrap_or_default()
    }

    /// How many documents have a publish.
    pub fn published_count(&self) -> usize {
        lock(&self.inbox).published.len()
    }
}

impl Drop for Session {
    fn drop(&mut self) {
        self.stop();
    }
}

fn find(by_uri: &Map<String, Value>, uri: &str) -> Option<Vec<Value>> {
    let wanted = norm_uri(uri);
    let found = by_uri.get(uri).or_else(|| by_uri.iter().find(|(known, _)| norm_uri(known) == wanted).map(|(_, diags)| diags));
    found.and_then(Value::as_array).cloned()
}

/// `_dispatch`: one message from verse-lsp.
async fn read(inbox: &Mutex<Inbox>, stdin: &Stdin, message: Map<String, Value>) {
    let method = message.get("method").filter(|method| truthy(method)).map(pystr);
    let id = message.get("id").filter(|id| !id.is_null());
    let answered = message.contains_key("result") || message.contains_key("error");
    if let (Some(method), Some(id), false) = (&method, id, answered) {
        let params = message.get("params").cloned().unwrap_or(Value::Null);
        let _ = write(stdin, &json!({"jsonrpc": "2.0", "id": id, "result": answer(method, &params)})).await;
    }
    let mut inbox = lock(inbox);
    if method.as_deref() == Some("textDocument/publishDiagnostics") {
        let uri = message.get("params").map(|params| &params["uri"]).filter(|uri| truthy(uri)).map(pystr).unwrap_or_default();
        if let Some(diags) = message.get("params").and_then(|params| params["diagnostics"].as_array()).filter(|_| !uri.is_empty()) {
            if diags.iter().any(is_syntax_error) {
                inbox.syntax.insert(uri.clone(), json!(diags));
            }
            inbox.published.insert(uri, json!(diags));
        }
    }
    if let Some(waiter) = id.filter(|_| answered).and_then(Value::as_i64).and_then(|id| inbox.waiting.remove(&id)) {
        let _ = waiter.send(Value::Object(message));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_address_is_the_editors_and_two_spellings_of_it_are_one() {
        assert_eq!(file_uri(r"C:\Isle One\Content\é.verse"), "file:///c%3A/Isle%20One/Content/%C3%A9.verse");
        assert_eq!(file_uri("/home/me/Isle/a.verse"), "file:///home/me/Isle/a.verse");
        let mut published = Map::new();
        published.insert("file:///C:/Isle%20One/Content/A.verse".into(), json!([{"message": "x"}]));
        assert_eq!(find(&published, "file:///c%3A/Isle%20One/Content/a.verse"), Some(vec![json!({"message": "x"})]));
        assert_eq!(find(&published, "file:///c%3A/Isle/b.verse"), None);
        assert_eq!(norm_uri("a%2"), "a%2");
    }

    #[test]
    fn a_syntax_error_is_told_by_its_code_and_a_question_gets_its_answer() {
        assert!(is_syntax_error(&json!({"code": "vErr:S77", "message": "x"})) && is_syntax_error(&json!({"severity": 1, "message": "vErr:S01: y"})));
        assert!(!is_syntax_error(&json!({"severity": 2, "code": "vErr:S77"})) && !is_syntax_error(&json!({"code": 3506, "message": "Unknown identifier"})));
        assert_eq!(answer("workspace/configuration", &json!({"items": [1, 2]})), json!([{}, {}]));
        assert_eq!(answer("workspace/configuration", &json!(null)), json!([]));
        assert_eq!((answer("workspace/applyEdit", &json!({})), answer("client/registerCapability", &json!({}))), (json!({"applied": false}), Value::Null));
    }
}
