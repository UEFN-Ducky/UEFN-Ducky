//! A stand-in for Epic's verse-lsp, for the tests of the editor's bridge and of the
//! project scan. While it runs it holds a loopback port open, named in
//! `fake-verse-lsp.port` in the folder it was started in: a test tells a running one
//! from an ended one by connecting there. It stays 30 s after its input closes, so a
//! test sees whether it was stopped or only left alone.
//!
//! To the bridge it answers each `Content-Length` framed message with the same message
//! and says one line on stderr; a message holding `"exit"` ends it with code 3.
//!
//! A client that sends `initialize` with a `rootUri` (the scan) gets a language server
//! instead. It asks for `workspace/configuration` and publishes nothing until that is
//! answered. A document's diagnostics are read from its text, one per line:
//! `ERR x` an error, `WARN x` a warning, `INFO x` a note, `ERR2 x` one error said twice,
//! `ONCE x` an error gone at the second change, `SYNTAXONCE` a syntax error said only
//! when the document opens, `AWAIT` the error UEFN's compiler does not have, and a
//! carriage return is an error of its own. A document holding `PULL` is never published:
//! its diagnostics are the answer to `textDocument/diagnostic`; one holding `HANG` gets
//! no answer at all. Addresses come back spelled another way than they were sent.
#![allow(clippy::disallowed_methods)] // no async here: a plain program

use std::collections::HashMap;
use std::io::{BufRead, Read, Write};

use serde_json::{json, Value};

const CONFIGURATION: i64 = 9000;

fn main() {
    let alive = std::net::TcpListener::bind(("127.0.0.1", 0)).expect("a loopback port");
    let port = alive.local_addr().expect("its port").port();
    let _ = std::fs::write("fake-verse-lsp.port", port.to_string());
    eprintln!("fake verse-lsp up  ");
    answer();
    std::thread::sleep(std::time::Duration::from_secs(30));
}

fn send(body: &[u8]) {
    let mut stdout = std::io::stdout().lock();
    let _ = write!(stdout, "Content-Length: {}\r\n\r\n", body.len());
    let _ = stdout.write_all(body).and_then(|()| stdout.flush());
}

/// Answer each message until the input closes.
fn answer() {
    let mut stdin = std::io::stdin().lock();
    let mut server = None::<Server>;
    loop {
        let mut length = 0usize;
        loop {
            let mut line = String::new();
            if stdin.read_line(&mut line).unwrap_or(0) == 0 {
                return;
            }
            if line.trim().is_empty() {
                break;
            }
            if let Some(value) = line.to_lowercase().strip_prefix("content-length:") {
                length = value.trim().parse().unwrap_or(0);
            }
        }
        let mut body = vec![0u8; length];
        if stdin.read_exact(&mut body).is_err() {
            return;
        }
        let message: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
        if message["method"] == "initialize" && message["params"]["rootUri"].is_string() {
            server = Some(Server::default());
        }
        match &mut server {
            Some(server) => server.take(&message),
            None if String::from_utf8_lossy(&body).contains("\"exit\"") => std::process::exit(3),
            None => send(&body),
        }
    }
}

/// The language server the scan talks to.
#[derive(Default)]
struct Server {
    /// The client answered `workspace/configuration`.
    configured: bool,
    /// Per document: its text and how often it was changed.
    documents: HashMap<String, (String, usize)>,
}

impl Server {
    fn take(&mut self, message: &Value) {
        let params = &message["params"];
        let uri = params["textDocument"]["uri"].as_str().unwrap_or_default().to_string();
        match message["method"].as_str().unwrap_or_default() {
            "initialize" => send(json!({"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": {}}}).to_string().as_bytes()),
            "initialized" => {
                let ask = json!({"jsonrpc": "2.0", "id": CONFIGURATION, "method": "workspace/configuration", "params": {"items": [{}, {}]}});
                send(ask.to_string().as_bytes());
            }
            "" if message["id"] == CONFIGURATION => {
                self.configured = message["result"] == json!([{}, {}]);
                // What was opened while the question was on its way.
                let mut opened: Vec<&String> = self.documents.keys().collect();
                opened.sort();
                opened.into_iter().for_each(|uri| self.publish(uri));
            }
            "textDocument/didOpen" => {
                let text = params["textDocument"]["text"].as_str().unwrap_or_default().to_string();
                self.documents.insert(uri.clone(), (text, 0));
                self.publish(&uri);
            }
            "textDocument/didChange" => {
                let text = params["contentChanges"][0]["text"].as_str().unwrap_or_default().to_string();
                let changes = self.documents.get(&uri).map_or(0, |(_, changes)| *changes);
                self.documents.insert(uri.clone(), (text, changes + 1));
                self.publish(&uri);
            }
            "textDocument/diagnostic" => {
                let (text, _) = self.documents.get(&uri).cloned().unwrap_or_default();
                if !text.contains("HANG") {
                    let found = diagnostics(&text.replace("PULL", "ERR"), 9);
                    // The shapes a pull answers in: a report, a bare diagnostic, junk.
                    let items = json!([{"diagnostics": found}, {"message": "bare", "range": {"start": {"line": 6, "character": 0}}}, "junk"]);
                    send(json!({"jsonrpc": "2.0", "id": message["id"], "result": {"items": items}}).to_string().as_bytes());
                }
            }
            _ => {}
        }
    }

    fn publish(&self, uri: &str) {
        let (text, changes) = &self.documents[uri];
        if !self.configured || text.contains("PULL") || text.contains("HANG") {
            return;
        }
        // Another spelling of the same address, as Epic's does.
        let params = json!({"uri": uri.replace("%3A", ":").replace("file:///c", "file:///C"), "diagnostics": diagnostics(text, *changes)});
        send(json!({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": params}).to_string().as_bytes());
    }
}

/// What a document's text says is wrong with it, after `changes` changes.
fn diagnostics(text: &str, changes: usize) -> Vec<Value> {
    let mut found = Vec::new();
    for (line, said) in text.split('\n').enumerate() {
        let at = |character: usize| json!({"start": {"line": line, "character": character}, "end": {"line": line, "character": character + 1}});
        let (word, rest) = said.split_once(' ').unwrap_or((said, ""));
        match word.trim_end_matches('\r') {
            "ERR" => found.push(json!({"range": at(2), "message": rest})),
            "WARN" => found.push(json!({"range": at(0), "message": rest, "severity": 2})),
            "INFO" => found.push(json!({"range": at(0), "message": rest, "severity": 3})),
            "ERR2" => found.extend([json!({"range": at(9), "message": rest, "severity": 1}), json!({"range": at(4), "message": rest, "severity": 1})]),
            "ONCE" if changes < 2 => found.push(json!({"range": at(0), "message": rest, "severity": 1})),
            "SYNTAXONCE" if changes == 0 => found.push(json!({"range": at(3), "message": "vErr:S77: Expected expression", "code": "vErr:S77", "severity": 1})),
            "AWAIT" => found.push(json!({"range": at(0), "message": "Invalid access of internal function `(/Verse.org/Verse:)Await`", "severity": 1})),
            _ => {}
        }
        if said.contains('\r') {
            found.push(json!({"range": at(0), "message": "a carriage return"}));
        }
    }
    found
}
