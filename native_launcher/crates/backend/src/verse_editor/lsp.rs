//! The editor's verse-lsp (`verse_editor/lsp/bridge.py`): one process per app window,
//! its stdio joined to a loopback WebSocket the page's editor connects to.
//!
//! The process outlives its listener: a page that reloads gets a new listener on the
//! same process. One left with no page for [`IDLE_STOP`] is stopped.
//! ponytail: a backend that is killed leaves its verse-lsp running, as Python's did; the
//! health report finds those. A job object would end them with it.

use std::collections::VecDeque;
use std::path::Path;
use std::process::Stdio;
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::Duration;

use base64::Engine;
use serde_json::{json, Map, Value};
use sha1::{Digest, Sha1};
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::tcp::OwnedWriteHalf;
use tokio::net::TcpListener;
use tokio::process::{Child, ChildStdin, ChildStdout, Command};
use tokio::sync::Notify;
use tokio::task::AbortHandle;

use super::lsp_detect;
use crate::api::project_paths::verse_workspace;

const STDERR_KEEP: usize = 80;
/// Nov 16 2026 a verse-lsp no page was connected to held 1,458 MB at 96% of a core.
const IDLE_STOP: Duration = Duration::from_secs(300);
/// VS Code runs one language server per window; so does Ducky, for this many windows.
const MAX_CLIENTS: usize = 3;
/// A frame or a request head bigger than this is no editor's.
const FRAME_MAX: u64 = 64 * 1024 * 1024;
const WS_GUID: &str = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";

/// The page's end of the WebSocket, once its handshake is done.
type Client = Arc<tokio::sync::Mutex<Option<OwnedWriteHalf>>>;

/// A running verse-lsp.
struct Run {
    id: u64,
    stdin: Arc<tokio::sync::Mutex<ChildStdin>>,
    client: Client,
    kill: Arc<Notify>,
    /// The open listener: its port, its number and its task.
    listener: Option<(u16, u64, AbortHandle)>,
}

#[derive(Default)]
struct State {
    run: Option<Run>,
    root: String,
    /// The workspace roots the process was started with.
    fingerprint: String,
    error: String,
    last_exit: Option<i32>,
    stderr: VecDeque<String>,
    /// Counts processes and listeners: each gets the next number as its id.
    made: u64,
    /// The id of the last process started, running or not.
    spawned: u64,
}

/// One window's verse-lsp.
#[derive(Default)]
pub struct Session {
    /// One start or stop at a time: the editor's bind, a reconnect and a scan all call.
    starting: tokio::sync::Mutex<()>,
    state: Mutex<State>,
}

/// Every window's session, the most recently used last.
static SESSIONS: Mutex<Vec<(String, Arc<Session>)>> = Mutex::new(Vec::new());

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
}

fn idle_stop() -> Duration {
    // The parity tests cannot wait five minutes.
    let tests = std::env::var_os("DUCKY_TESTS_ISOLATED_APPDATA").and_then(|_| std::env::var("DUCKY_VERSE_LSP_IDLE_MS").ok());
    tests.and_then(|ms| ms.parse().ok()).map_or(IDLE_STOP, Duration::from_millis)
}

/// `_lsp_for`: the window's session; the least recently used one beyond three is stopped.
pub async fn session(client_id: &str) -> Arc<Session> {
    let id = Some(client_id.trim()).filter(|id| !id.is_empty()).unwrap_or("default");
    let (found, stale) = {
        let mut sessions = lock(&SESSIONS);
        let found = sessions.iter().position(|(known, _)| known == id).map(|at| sessions.remove(at).1).unwrap_or_default();
        sessions.push((id.to_string(), Arc::clone(&found)));
        let extra = sessions.len().saturating_sub(MAX_CLIENTS);
        (found, sessions.drain(..extra).collect::<Vec<_>>())
    };
    for (_, session) in stale {
        session.stop().await;
    }
    found
}

/// `stop_lsp`: stop one window's verse-lsp, or every one when no window is named.
pub async fn stop(client_id: &str) {
    let id = client_id.trim();
    let stopped: Vec<_> = {
        let mut sessions = lock(&SESSIONS);
        let (stopped, kept): (Vec<_>, Vec<_>) = sessions.drain(..).partition(|(known, _)| id.is_empty() || known == id);
        *sessions = kept;
        stopped
    };
    for (_, session) in stopped {
        session.stop().await;
    }
}

/// `workspace_folder_fingerprint`: the roots verse-lsp is started with. The digest
/// folders only exist after the first Verse build; a process from before it says
/// `Fortnite.com` is unknown, so a change here starts a new one.
#[allow(clippy::disallowed_methods)] // reads folders: runs inside spawn_blocking
fn fingerprint(root: &str, data_dir: &Path) -> String {
    let mut parts: Vec<String> = verse_workspace(Path::new(root), data_dir, false).folders.iter().map(|folder| format!("{}\t{}", folder.name, folder.path)).collect();
    parts.sort();
    parts.join("\n")
}

async fn blocking<T: Send + 'static>(work: impl FnOnce() -> T + Send + 'static) -> Result<T, String> {
    tokio::task::spawn_blocking(work).await.map_err(|stopped| stopped.to_string())
}

impl Session {
    /// `get_status`.
    pub async fn status(&self, data_dir: &Path) -> Result<Map<String, Value>, String> {
        let dir = data_dir.to_path_buf();
        let found = blocking(move || lsp_detect::detect(&dir)).await?.map_err(|failed| failed.to_string())?;
        let state = lock(&self.state);
        let port = state.run.as_ref().and_then(|run| run.listener.as_ref()).map(|open| open.0);
        let status = json!({
            "available": found["available"],
            "lsp_path": found["path"],
            "source": found["source"],
            "running": port.is_some(),
            "ws_url": port.map(|port| format!("ws://127.0.0.1:{port}")).unwrap_or_default(),
            "project_root": state.root,
            "error": if state.error.is_empty() { found["error"].clone() } else { json!(state.error) },
            "stderr_tail": state.stderr.iter().collect::<Vec<_>>(),
            // "The process ended" and "its listener closed" are told apart by these.
            "proc_alive": state.run.is_some(),
            "bridge_up": port.is_some(),
            "last_exit_code": state.last_exit,
        });
        Ok(status.as_object().cloned().unwrap_or_default())
    }

    /// `start`: the status of a verse-lsp running for `root` (an island's folder).
    pub async fn start(self: &Arc<Self>, root: &str, data_dir: &Path) -> Result<Map<String, Value>, String> {
        let one = self.starting.lock().await;
        let (dir, at) = (data_dir.to_path_buf(), root.to_string());
        let print = blocking(move || fingerprint(&at, &dir)).await?;
        let reused = {
            let mut state = lock(&self.state);
            let same = state.root == root && state.fingerprint == print;
            // Its listener went with the page that left: the next page gets a new one
            // on the same process. A second `initialize` is answered with an error, and
            // restarting on every reconnect broke the editor's start.
            let reused = same && self.listen(&mut state);
            if reused {
                state.error.clear();
            }
            reused
        };
        if !reused {
            self.stop_locked().await;
            let dir = data_dir.to_path_buf();
            let found = blocking(move || lsp_detect::detect(&dir)).await?.map_err(|failed| failed.to_string())?;
            let mut state = lock(&self.state);
            if found["available"] == true {
                (state.root, state.fingerprint, state.last_exit) = (root.to_string(), print, None);
                state.stderr.clear();
                state.error = match self.spawn(&mut state, found["path"].as_str().unwrap_or_default()) {
                    Ok(()) => String::new(),
                    Err(failed) => failed.to_string(),
                };
            } else {
                state.error = found["error"].as_str().unwrap_or("verse-lsp not found").to_string();
            }
        }
        drop(one);
        self.status(data_dir).await
    }

    /// Start the process hidden, in the island's folder, with its three pipes read.
    fn spawn(self: &Arc<Self>, state: &mut State, program: &str) -> std::io::Result<()> {
        let mut child = command(program, &state.root).spawn()?;
        let pipes = (child.stdin.take(), child.stdout.take(), child.stderr.take());
        let (Some(stdin), Some(stdout), Some(stderr)) = pipes else { return Err(std::io::Error::other("verse-lsp started without its pipes")) };
        state.made += 1;
        let run = Run { id: state.made, stdin: Arc::new(tokio::sync::Mutex::new(stdin)), client: Client::default(), kill: Arc::default(), listener: None };
        let (session, id) = (Arc::clone(self), run.id);
        tokio::spawn(async move {
            let mut lines = BufReader::new(stderr).split(b'\n');
            while let Ok(Some(line)) = lines.next_segment().await {
                let line = String::from_utf8_lossy(&line).trim_end().to_string();
                let mut state = lock(&session.state);
                // Lines after its end are kept too (why it ended); a newer process's are not mixed in.
                if !line.is_empty() && state.spawned == id {
                    if state.stderr.len() == STDERR_KEEP {
                        state.stderr.pop_front();
                    }
                    state.stderr.push_back(line);
                }
            }
        });
        // One reader for the process's whole life: a reader per page left the old one
        // taking the new page's answers.
        tokio::spawn(pump(stdout, Arc::clone(&run.client)));
        tokio::spawn(Arc::clone(self).watch(child, id, Arc::clone(&run.kill)));
        state.spawned = id;
        state.run = Some(run);
        self.listen(state);
        Ok(())
    }

    /// `_spawn_bridge_listener`: give the running process a listener when it has none.
    /// False when no process runs or no port could be opened.
    fn listen(self: &Arc<Self>, state: &mut State) -> bool {
        state.made += 1;
        let number = state.made;
        let Some(run) = state.run.as_mut() else { return false };
        if run.listener.is_some() {
            return true;
        }
        let bound = std::net::TcpListener::bind(("127.0.0.1", 0)).and_then(|listener| {
            listener.set_nonblocking(true)?;
            let port = listener.local_addr()?.port();
            Ok((TcpListener::from_std(listener)?, port))
        });
        let Ok((listener, port)) = bound else {
            state.error = "verse-lsp bridge failed to start listening".into();
            return false;
        };
        let task = tokio::spawn(Arc::clone(self).bridge(listener, run.id, number, Arc::clone(&run.stdin), Arc::clone(&run.client)));
        run.listener = Some((port, number, task.abort_handle()));
        true
    }

    /// `stop`.
    pub async fn stop(&self) {
        let _one = self.starting.lock().await;
        self.stop_locked().await;
    }

    async fn stop_locked(&self) {
        let run = {
            let mut state = lock(&self.state);
            state.fingerprint.clear();
            state.run.take()
        };
        if let Some(run) = run {
            run.end().await;
            run.kill.notify_one();
        }
    }

    /// Owns the process: kills it when told to, else notes how it ended.
    async fn watch(self: Arc<Self>, mut child: Child, id: u64, kill: Arc<Notify>) {
        let ended = tokio::select! {
            ended = child.wait() => ended,
            () = kill.notified() => {
                let _ = child.kill().await;
                return;
            }
        };
        let run = {
            let mut state = lock(&self.state);
            // A process a newer start replaced: its end is old news.
            if state.run.as_ref().is_none_or(|run| run.id != id) {
                return;
            }
            let code = ended.ok().and_then(|status| status.code()).unwrap_or(-1);
            state.last_exit = Some(code);
            state.fingerprint.clear();
            state.error = format!("verse-lsp exited (code {code}) — reopen a .verse file to reconnect");
            state.run.take()
        };
        if let Some(run) = run {
            run.end().await;
        }
    }

    /// `_bridge_loop`: serve one page on `listener`, then close it. A page that was
    /// here and left starts the idle clock.
    async fn bridge(self: Arc<Self>, listener: TcpListener, run: u64, number: u64, stdin: Arc<tokio::sync::Mutex<ChildStdin>>, client: Client) {
        let came = serve(&listener, &stdin, &client).await;
        drop(listener);
        *client.lock().await = None;
        {
            let mut state = lock(&self.state);
            let Some(ours) = state.run.as_mut().filter(|ours| ours.id == run && ours.listener.as_ref().is_some_and(|open| open.1 == number)) else { return };
            ours.listener = None;
        }
        if came {
            tokio::time::sleep(idle_stop()).await;
            let _one = self.starting.lock().await;
            let unattached = lock(&self.state).run.as_ref().is_some_and(|ours| ours.id == run && ours.listener.is_none());
            if unattached {
                self.stop_locked().await;
            }
        }
    }
}

impl Run {
    /// Close its listener and its page.
    async fn end(&self) {
        if let Some((_, _, task)) = &self.listener {
            task.abort();
        }
        *self.client.lock().await = None;
    }
}

/// verse-lsp as Ducky starts it: no window, in the island's folder, its pipes ours.
pub(super) fn command(program: &str, root: &str) -> Command {
    let mut command = Command::new(program);
    command.current_dir(root).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped()).kill_on_drop(true);
    #[cfg(windows)]
    command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    command
}

/// Accept one page, shake hands and hand its messages to verse-lsp until it leaves.
/// True when a page connected.
async fn serve(listener: &TcpListener, stdin: &tokio::sync::Mutex<ChildStdin>, client: &Client) -> bool {
    let Ok((socket, _)) = listener.accept().await else { return false };
    let (read, mut write) = socket.into_split();
    let mut read = BufReader::new(read);
    let (mut key, mut head) = (String::new(), 0);
    loop {
        let mut line = Vec::new();
        match read.read_until(b'\n', &mut line).await {
            Ok(count) if count > 0 && head < 64 * 1024 => head += count,
            _ => return true,
        }
        let line = String::from_utf8_lossy(&line);
        if line.trim().is_empty() {
            break;
        }
        if let Some((_, value)) = line.split_once(':').filter(|(name, _)| name.trim().eq_ignore_ascii_case("sec-websocket-key")) {
            key = value.trim().to_string();
        }
    }
    let accept = base64::engine::general_purpose::STANDARD.encode(Sha1::digest(format!("{key}{WS_GUID}")));
    let answer = format!("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n");
    if write.write_all(answer.as_bytes()).await.is_err() {
        return true;
    }
    // Only now may verse-lsp's messages go to the page: never into the handshake.
    *client.lock().await = Some(write);
    let mut message = Vec::new();
    while let Ok((last, opcode, payload)) = frame(&mut read).await {
        match opcode {
            8 => break,
            9 => {
                if let Some(page) = client.lock().await.as_mut() {
                    let _ = send(page, 0x8A, &payload).await;
                }
            }
            0..=2 => {
                message.extend(payload);
                if last && !message.is_empty() {
                    let mut out = format!("Content-Length: {}\r\n\r\n", message.len()).into_bytes();
                    out.append(&mut message);
                    let mut stdin = stdin.lock().await;
                    if stdin.write_all(&out).await.is_err() || stdin.flush().await.is_err() {
                        break;
                    }
                }
            }
            _ => {}
        }
    }
    true
}

/// One WebSocket frame: `(the message's last, its opcode, its bytes unmasked)`.
async fn frame(from: &mut (impl AsyncRead + Unpin)) -> std::io::Result<(bool, u8, Vec<u8>)> {
    let mut head = [0u8; 2];
    from.read_exact(&mut head).await?;
    let length = match head[1] & 0x7F {
        126 => u64::from(from.read_u16().await?),
        127 => from.read_u64().await?,
        short => u64::from(short),
    };
    if length > FRAME_MAX {
        return Err(std::io::Error::other("frame too big"));
    }
    let mut mask = [0u8; 4];
    if head[1] & 0x80 != 0 {
        from.read_exact(&mut mask).await?;
    }
    let mut payload = vec![0u8; length as usize];
    from.read_exact(&mut payload).await?;
    for (at, byte) in payload.iter_mut().enumerate() {
        *byte ^= mask[at % 4];
    }
    Ok((head[0] & 0x80 != 0, head[0] & 0x0F, payload))
}

/// Send one unmasked frame.
async fn send(page: &mut OwnedWriteHalf, first: u8, data: &[u8]) -> std::io::Result<()> {
    let mut out = vec![first];
    match data.len() {
        short @ 0..126 => out.push(short as u8),
        medium @ 126..65536 => {
            out.push(126);
            out.extend((medium as u16).to_be_bytes());
        }
        long => {
            out.push(127);
            out.extend((long as u64).to_be_bytes());
        }
    }
    out.extend(data);
    page.write_all(&out).await
}

/// `_pump_stdout`: every message verse-lsp writes goes to the page that is connected.
/// One written while no page is (a reload) is dropped: verse-lsp publishes its
/// diagnostics again on the next change.
async fn pump(stdout: ChildStdout, client: Client) {
    let mut stdout = BufReader::new(stdout);
    while let Some(message) = message(&mut stdout).await {
        if let Some(page) = client.lock().await.as_mut() {
            let _ = send(page, 0x81, String::from_utf8_lossy(&message).as_bytes()).await;
        }
    }
}

/// `_lsp_read_message`: one `Content-Length` framed message; None at the end.
pub(super) async fn message(from: &mut (impl AsyncBufReadExt + Unpin)) -> Option<Vec<u8>> {
    let mut length = 0usize;
    loop {
        let mut line = Vec::new();
        if from.read_until(b'\n', &mut line).await.ok()? == 0 {
            return None;
        }
        let line = String::from_utf8_lossy(&line);
        if line.trim().is_empty() {
            break;
        }
        if let Some((_, value)) = line.split_once(':').filter(|(name, _)| name.trim().eq_ignore_ascii_case("content-length")) {
            length = value.trim().parse().unwrap_or(0);
        }
    }
    let mut body = vec![0u8; length];
    from.read_exact(&mut body).await.ok()?;
    (length > 0).then_some(body)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn frames_and_messages_are_read_as_the_page_and_verse_lsp_write_them() {
        // A masked text frame "Hi", then a 126-long binary one, as a browser sends them.
        let mut wire = vec![0x81, 0x82, 1, 2, 3, 4, b'H' ^ 1, b'i' ^ 2];
        wire.extend([0x02, 126, 0, 126]);
        wire.extend([7u8; 126]);
        let mut wire = wire.as_slice();
        assert_eq!(frame(&mut wire).await.unwrap(), (true, 1, b"Hi".to_vec()));
        assert_eq!(frame(&mut wire).await.unwrap(), (false, 2, vec![7u8; 126]));
        assert!(frame(&mut wire).await.is_err());
        let huge = [0x81u8, 127, 0, 0, 0, 1, 0, 0, 0, 0];
        assert!(frame(&mut huge.as_slice()).await.is_err());

        let mut out = b"Content-Type: x\r\ncontent-length: 4\r\n\r\n{}\r\nContent-Length: 0\r\n\r\n".as_slice();
        assert_eq!(message(&mut out).await, Some(b"{}\r\n".to_vec()));
        assert_eq!(message(&mut out).await, None);
    }

    #[tokio::test]
    async fn the_fourth_window_stops_the_one_used_longest_ago() {
        let first = session("w1").await;
        for id in ["w2", "w3", " w1 ", "w4"] {
            session(id).await;
        }
        let ids = || lock(&SESSIONS).iter().map(|(id, _)| id.clone()).collect::<Vec<_>>();
        assert_eq!(ids(), ["w3", "w1", "w4"]);
        assert!(Arc::ptr_eq(&first, &session("w1").await));
        stop("w3").await;
        assert_eq!(ids(), ["w4", "w1"]);
        stop("").await;
        assert!(ids().is_empty());
    }
}
