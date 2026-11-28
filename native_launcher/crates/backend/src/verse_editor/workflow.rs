//! UEFN's Verse workflow server (`verse_editor/workflow/`): Build Verse and Push Changes
//! are asked of it over a loopback socket, and it says how the build stands. One
//! connection for the whole app; a connection UEFN dropped is made again by the next
//! connect.
//!
//! ponytail: a build or push here does not save UEFN's unsaved packages first nor press
//! its "Save Content" prompt, as Python does through the UEFN listener and its popup
//! tools. Those arrive with the listener bridge; until then these calls stay Python's.

use std::collections::HashMap;
use std::sync::{Arc, LazyLock, Mutex, MutexGuard};
use std::time::Duration;

use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::tcp::{OwnedReadHalf, OwnedWriteHalf};
use tokio::net::TcpStream;
use tokio::sync::oneshot;
use tokio::task::AbortHandle;

use super::lsp;
use crate::api::Api;
use crate::store::pyjson::{dumps_ascii, int, pystr, truthy};

const PORT: u16 = 1962;
const BUILDING: i64 = 3;
const NO_BUILD: i64 = 4;
/// How long a build or a push may take.
pub const LIMIT: Duration = Duration::from_secs(120);
const NO_SOCKET: &str = "Verse workflow socket not connected";

struct Link {
    number: u64,
    writer: Arc<tokio::sync::Mutex<OwnedWriteHalf>>,
    reader: AbortHandle,
}

struct Client {
    build_state: i64,
    can_push: bool,
    last_log: String,
    /// The windows hear each change: from a connect until a disconnect.
    told: bool,
    link: Option<Link>,
    links: u64,
    sequence: i64,
    /// Requests waiting for their answer, by sequence number.
    waiting: HashMap<i64, oneshot::Sender<Value>>,
}

static CLIENT: LazyLock<Mutex<Client>> = LazyLock::new(|| {
    Mutex::new(Client { build_state: NO_BUILD, can_push: false, last_log: String::new(), told: false, link: None, links: 0, sequence: 1, waiting: HashMap::new() })
});

fn client() -> MutexGuard<'static, Client> {
    CLIENT.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
}

impl Client {
    fn status(&self) -> Value {
        json!({"connected": self.link.is_some(), "build_state": self.build_state, "can_push_verse_changes": self.can_push, "last_log": self.last_log})
    }

    fn tell(&self, api: &Api) {
        if self.told {
            let mut event = self.status();
            event["type"] = "verse_workflow_state".into();
            api.publish(vec![event]);
        }
    }

    /// The connection is gone: whoever waits for an answer hears it now.
    fn unlink(&mut self) {
        if let Some(link) = self.link.take() {
            link.reader.abort();
        }
        self.waiting.clear();
    }
}

pub fn status() -> Value {
    client().status()
}

pub fn connected() -> bool {
    client().link.is_some()
}

fn port() -> u16 {
    // A test's own server, never a running UEFN.
    let test = std::env::var_os("DUCKY_TESTS_ISOLATED_APPDATA").and_then(|_| std::env::var("DUCKY_VERSE_WORKFLOW_PORT").ok());
    test.and_then(|port| port.parse().ok()).unwrap_or(PORT)
}

/// Connect (or find connected) and tell the windows each change from now on.
/// ponytail: a refusal is in the OS's words as Rust prints them, not as Python does.
pub async fn connect(api: &Arc<Api>) -> Value {
    {
        let mut client = client();
        client.told = true;
        if client.link.is_some() {
            return client.status();
        }
    }
    let refused = |why: String| {
        let mut status = status();
        status["error"] = why.into();
        status
    };
    let stream = match tokio::time::timeout(Duration::from_secs(5), TcpStream::connect(("127.0.0.1", port()))).await {
        Ok(Ok(stream)) => stream,
        Ok(Err(failed)) => return refused(failed.to_string()),
        Err(_) => return refused("timed out".into()),
    };
    let (read, write) = stream.into_split();
    let mut client = client();
    client.unlink(); // another connect won the race
    client.links += 1;
    let number = client.links;
    let reader = tokio::spawn(read_loop(Arc::clone(api), read, number)).abort_handle();
    client.link = Some(Link { number, writer: Arc::new(tokio::sync::Mutex::new(write)), reader });
    client.tell(api);
    client.status()
}

pub fn disconnect(api: &Api) -> Value {
    let mut client = client();
    client.told = false;
    client.unlink();
    client.tell(api);
    client.status()
}

/// UEFN dropped connection `number` (it closed or restarted).
fn closed(api: &Api, number: u64) {
    let mut client = client();
    if client.link.as_ref().is_some_and(|link| link.number == number) {
        client.unlink();
        client.tell(api);
    }
}

/// The next whole message in `raw`, taken out of it.
fn take(raw: &mut Vec<u8>) -> Option<Vec<u8>> {
    loop {
        let head = raw.windows(4).position(|window| window == b"\r\n\r\n")?;
        let header = String::from_utf8_lossy(&raw[..head]).into_owned();
        let length = header.split("\r\n").filter_map(|line| line.strip_prefix("Content-Length: ")).filter_map(|length| length.trim().parse::<usize>().ok()).last();
        let Some(length) = length else {
            raw.drain(..head + 4); // a header that names no length: the next one may
            continue;
        };
        if raw.len() < head + 4 + length {
            return None;
        }
        let body = raw[head + 4..head + 4 + length].to_vec();
        raw.drain(..head + 4 + length);
        return Some(body);
    }
}

async fn read_loop(api: Arc<Api>, mut read: OwnedReadHalf, number: u64) {
    let (mut raw, mut chunk) = (Vec::new(), vec![0u8; 65536]);
    while let Ok(count @ 1..) = read.read(&mut chunk).await {
        raw.extend(&chunk[..count]);
        while let Some(body) = take(&mut raw) {
            if let Ok(Value::Object(message)) = serde_json::from_slice(&body) {
                dispatch(&api, Value::Object(message)).await;
            }
        }
    }
    closed(&api, number);
}

/// One message from the server: a notification (type 0) or the answer to a request (2).
async fn dispatch(api: &Api, message: Value) {
    let kind = message["type"].as_f64();
    if kind == Some(2.0) {
        let waiter = client().waiting.remove(&int(&message["seq"]));
        if let Some(waiter) = waiter {
            let _ = waiter.send(message);
        }
        return;
    }
    if kind != Some(0.0) {
        return;
    }
    let params = &message["params"];
    let built = {
        let mut client = client();
        match message["command"].as_str().unwrap_or_default() {
            "logMessage" => {
                client.last_log = Some(&params["message"]).filter(|said| truthy(said)).map(pystr).unwrap_or_default();
                false
            }
            "updateBuildState" => {
                let before = std::mem::replace(&mut client.build_state, int(params));
                client.tell(api);
                before == BUILDING && client.build_state != BUILDING
            }
            "canPushVerseChanges" => {
                client.can_push = truthy(params);
                client.tell(api);
                false
            }
            _ => false,
        }
    };
    if built {
        after_build().await;
    }
}

/// `refresh_editor_lsp_after_build`: a build (UEFN's own button or Ducky's) writes digest
/// folders; the editor's verse-lsp only takes them into its workspace when it starts.
pub async fn after_build() {
    lsp::stop("").await;
}

/// Ask the server for `command` and wait for its answer.
/// ponytail: a connection that drops, or an answer with neither a result nor an error,
/// is told at once; Python waits out its two minutes first.
pub async fn request(api: &Api, command: &str, params: Value, limit: Duration) -> Result<Value, String> {
    let (told, answer) = oneshot::channel();
    let (writer, number, seq) = {
        let mut client = client();
        let Some(link) = &client.link else { return Err(NO_SOCKET.into()) };
        let (writer, number, seq) = (Arc::clone(&link.writer), link.number, client.sequence);
        client.sequence += 1;
        client.waiting.insert(seq, told);
        (writer, number, seq)
    };
    let payload = dumps_ascii(&json!({"seq": seq, "type": 1, "command": command, "params": params}));
    let mut out = format!("Content-Length: {}\r\n\r\n", payload.len()).into_bytes();
    out.extend(payload.as_bytes());
    let sent = writer.lock().await.write_all(&out).await;
    if let Err(failed) = sent {
        closed(api, number);
        return Err(failed.to_string());
    }
    let late = || format!("Verse workflow request timed out: {command}");
    match tokio::time::timeout(limit, answer).await {
        Ok(Ok(answer)) if !answer["result"].is_null() => Ok(answer["result"].clone()),
        Ok(Ok(answer)) if !answer["error"].is_null() => Err(pystr(&answer["error"])),
        Ok(Ok(_)) => Err(late()),
        Ok(Err(_)) => Err(NO_SOCKET.into()),
        Err(_) => {
            client().waiting.remove(&seq);
            Err(late())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn messages_are_taken_whole_and_a_header_without_a_length_is_skipped() {
        let mut raw = b"X: 1\r\n\r\nContent-Length: 2\r\nOther: x\r\n\r\n{}Content-Length: 5\r\n\r\n[1".to_vec();
        assert_eq!(take(&mut raw), Some(b"{}".to_vec()));
        assert_eq!(take(&mut raw), None);
        raw.extend(b",2]");
        assert_eq!((take(&mut raw), take(&mut raw), raw.len()), (Some(b"[1,2]".to_vec()), None, 0));
    }
}
