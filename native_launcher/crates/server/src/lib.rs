//! Ducky's UI port, served by the launcher.
//!
//! Static UI files come straight from the version folder, so the page paints before the
//! Python backend is up. Backend paths (`/api/*`, `/__*`), websocket upgrades and every
//! path with no file here (plugin panels under `/plugin-ui/`, fonts, chat attachments,
//! generated images) are held until the backend's hello gives its origin, then tunnelled
//! to it byte for byte. Requests still held when the hold window (30 s from start) ends get
//! a 503 (a missing file a 404).

pub mod port;

use std::io::{self, Read, Write};
use std::net::{Shutdown, SocketAddr, TcpListener, TcpStream, ToSocketAddrs};
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant};

pub const DEFAULT_HOLD: Duration = Duration::from_secs(30);
const MAX_HEAD: usize = 64 * 1024;

#[derive(Debug, Clone, PartialEq, Eq)]
enum GateState {
    Waiting,
    Open(SocketAddr),
    Closed,
}

/// Where backend requests go: waiting, released to an address, or closed (503).
#[derive(Debug)]
pub struct Gate {
    state: Mutex<GateState>,
    cv: Condvar,
    deadline: Instant,
}

impl Gate {
    pub fn new(hold: Duration) -> Self {
        Gate { state: Mutex::new(GateState::Waiting), cv: Condvar::new(), deadline: Instant::now() + hold }
    }

    /// Release held and future requests to the backend.
    pub fn release(&self, addr: SocketAddr) {
        *self.state.lock().unwrap() = GateState::Open(addr);
        self.cv.notify_all();
    }

    /// The backend is gone for good: answer 503 at once.
    pub fn close(&self) {
        *self.state.lock().unwrap() = GateState::Closed;
        self.cv.notify_all();
    }

    /// Back to holding (a backend restart); the hold window does not restart.
    pub fn hold(&self) {
        *self.state.lock().unwrap() = GateState::Waiting;
    }

    /// The backend address, waiting until release or the hold deadline.
    pub fn wait(&self) -> Option<SocketAddr> {
        let mut st = self.state.lock().unwrap();
        loop {
            match &*st {
                GateState::Open(a) => return Some(*a),
                GateState::Closed => return None,
                GateState::Waiting => {
                    let now = Instant::now();
                    if now >= self.deadline {
                        return None;
                    }
                    st = self.cv.wait_timeout(st, self.deadline - now).unwrap().0;
                }
            }
        }
    }
}

/// `http://127.0.0.1:N` (the hello `origin`) to a socket address.
pub fn origin_addr(origin: &str) -> Option<SocketAddr> {
    let rest = origin.strip_prefix("http://").unwrap_or(origin);
    let host = rest.split('/').next()?;
    host.to_socket_addrs().ok()?.find(|a| a.is_ipv4()).or_else(|| host.to_socket_addrs().ok()?.next())
}

/// Folders static files come from, first match wins (index.html from the first). Hashed
/// asset names are unique per build, so a page loaded from an older root keeps finding its
/// chunks after [`Server::prefer_root`] puts the backend's own build first.
type Roots = Arc<Mutex<Vec<PathBuf>>>;

/// A running UI server.
pub struct Server {
    addr: SocketAddr,
    gate: Arc<Gate>,
    stop: Arc<AtomicBool>,
    roots: Roots,
}

impl Server {
    /// Bind `127.0.0.1:port` (0 = any, for tests) and serve `root`.
    pub fn bind(port: u16, root: impl Into<PathBuf>, hold: Duration) -> io::Result<Server> {
        let listener = TcpListener::bind(("127.0.0.1", port))?;
        Ok(Server::start(listener, root.into(), hold))
    }

    pub fn start(listener: TcpListener, root: PathBuf, hold: Duration) -> Server {
        let addr = listener.local_addr().expect("local addr");
        let gate = Arc::new(Gate::new(hold));
        let stop = Arc::new(AtomicBool::new(false));
        let roots: Roots = Arc::new(Mutex::new(vec![root]));
        let (g, s, r) = (gate.clone(), stop.clone(), roots.clone());
        thread::spawn(move || {
            for conn in listener.incoming() {
                if s.load(Ordering::SeqCst) {
                    break;
                }
                if let Ok(stream) = conn {
                    let (g, roots) = (g.clone(), r.lock().unwrap().clone());
                    thread::spawn(move || {
                        let _ = handle(stream, &roots, &g);
                    });
                }
            }
        });
        Server { addr, gate, stop, roots }
    }

    /// Serve `root` first from now on (the build the backend runs); earlier roots stay as
    /// fallbacks for pages already loaded from them.
    pub fn prefer_root(&self, root: impl Into<PathBuf>) {
        let root = root.into();
        let mut roots = self.roots.lock().unwrap();
        roots.retain(|r| r != &root);
        roots.insert(0, root);
    }

    pub fn addr(&self) -> SocketAddr {
        self.addr
    }

    pub fn gate(&self) -> &Arc<Gate> {
        &self.gate
    }

    /// Release backend requests to the hello `origin`; false when it does not parse.
    pub fn release_origin(&self, origin: &str) -> bool {
        match origin_addr(origin) {
            Some(a) => {
                self.gate.release(a);
                true
            }
            None => false,
        }
    }

    /// Stop accepting (connections in flight finish).
    pub fn stop(&self) {
        self.stop.store(true, Ordering::SeqCst);
        let _ = TcpStream::connect_timeout(&self.addr, Duration::from_millis(200));
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        self.stop();
    }
}

/// Parsed request head: what routing needs.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Head {
    pub method: String,
    pub path: String,
    pub websocket: bool,
}

pub fn parse_head(head: &[u8]) -> Option<Head> {
    let text = std::str::from_utf8(head).ok()?;
    let mut lines = text.split("\r\n");
    let mut first = lines.next()?.split(' ');
    let method = first.next()?.to_string();
    let target = first.next()?;
    let path = target.split(['?', '#']).next().unwrap_or("/").to_string();
    let websocket = lines.any(|l| {
        let mut kv = l.splitn(2, ':');
        let k = kv.next().unwrap_or("").trim();
        let v = kv.next().unwrap_or("").trim();
        k.eq_ignore_ascii_case("upgrade") && v.eq_ignore_ascii_case("websocket")
    });
    Some(Head { method, path, websocket })
}

/// True when the backend answers this request (held until hello).
pub fn is_backend(head: &Head) -> bool {
    head.websocket || head.path == "/api" || head.path.starts_with("/api/") || head.path.starts_with("/__")
}

fn handle(mut client: TcpStream, roots: &[PathBuf], gate: &Gate) -> io::Result<()> {
    client.set_read_timeout(Some(Duration::from_secs(30)))?;
    let mut buf = Vec::with_capacity(4096);
    let mut chunk = [0u8; 4096];
    let end = loop {
        let n = client.read(&mut chunk)?;
        if n == 0 {
            return Ok(());
        }
        buf.extend_from_slice(&chunk[..n]);
        if let Some(i) = find(&buf, b"\r\n\r\n") {
            break i + 4;
        }
        if buf.len() > MAX_HEAD {
            return respond(&mut client, 431, "text/plain", b"request head too large", false);
        }
    };
    let head = match parse_head(&buf[..end]) {
        Some(h) => h,
        None => return respond(&mut client, 400, "text/plain", b"bad request", false),
    };
    client.set_read_timeout(None)?;
    let backend = is_backend(&head);
    // The Python server answers more than /api and /__ (plugin panels' kit CSS/JS sat on 404:
    // every plugin drew as bare HTML), so anything without a file here is the backend's.
    let root = roots.iter().find(|r| resolve_static(r, &head.path).is_some_and(|f| f.is_file()));
    match root {
        Some(root) if !backend => serve_static(&mut client, root, &head),
        _ => match gate.wait() {
            Some(addr) => tunnel(client, addr, &buf),
            None if backend => respond(&mut client, 503, "text/plain", b"Ducky backend not ready", false),
            None => respond(&mut client, 404, "text/plain", b"not found", false),
        },
    }
}

fn find(hay: &[u8], needle: &[u8]) -> Option<usize> {
    hay.windows(needle.len()).position(|w| w == needle)
}

/// Raw two-way copy: the backend sees the exact request (and websocket frames).
fn tunnel(client: TcpStream, addr: SocketAddr, already: &[u8]) -> io::Result<()> {
    let mut upstream = match TcpStream::connect_timeout(&addr, Duration::from_secs(5)) {
        Ok(s) => s,
        Err(_) => {
            let mut c = client;
            return respond(&mut c, 502, "text/plain", b"Ducky backend unreachable", false);
        }
    };
    let _ = upstream.set_nodelay(true);
    let _ = client.set_nodelay(true);
    upstream.write_all(already)?;
    let (mut c_read, mut u_write) = (client.try_clone()?, upstream.try_clone()?);
    let up = thread::spawn(move || {
        let _ = io::copy(&mut c_read, &mut u_write);
        let _ = u_write.shutdown(Shutdown::Write);
    });
    let (mut u_read, mut c_write) = (upstream, client);
    let _ = io::copy(&mut u_read, &mut c_write);
    let _ = c_write.shutdown(Shutdown::Both);
    let _ = u_read.shutdown(Shutdown::Both);
    let _ = up.join();
    Ok(())
}

/// `%xx` decoding of a URL path; None for bad escapes or non-UTF-8.
fn percent_decode(path: &str) -> Option<String> {
    let b = path.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'%' {
            let hex = std::str::from_utf8(b.get(i + 1..i + 3)?).ok()?;
            out.push(u8::from_str_radix(hex, 16).ok()?);
            i += 3;
        } else {
            out.push(b[i]);
            i += 1;
        }
    }
    String::from_utf8(out).ok()
}

/// File under `root` for a URL path; None when it escapes the root.
pub fn resolve_static(root: &Path, url_path: &str) -> Option<PathBuf> {
    let decoded = percent_decode(url_path)?;
    let rel = decoded.trim_start_matches('/');
    let rel = if rel.is_empty() || rel.ends_with('/') { format!("{rel}index.html") } else { rel.to_string() };
    let rel_path = Path::new(&rel);
    if rel.contains('\\') || rel_path.components().any(|c| !matches!(c, Component::Normal(_))) {
        return None;
    }
    let full = root.join(rel_path);
    if full.is_dir() {
        return Some(full.join("index.html"));
    }
    Some(full)
}

pub fn content_type(path: &Path) -> &'static str {
    match path.extension().and_then(|e| e.to_str()).map(|e| e.to_ascii_lowercase()).as_deref() {
        Some("html") | Some("htm") => "text/html; charset=utf-8",
        Some("js") | Some("mjs") => "text/javascript; charset=utf-8",
        Some("css") => "text/css; charset=utf-8",
        Some("json") | Some("map") => "application/json",
        Some("svg") => "image/svg+xml",
        Some("png") => "image/png",
        Some("jpg") | Some("jpeg") => "image/jpeg",
        Some("gif") => "image/gif",
        Some("webp") => "image/webp",
        Some("ico") => "image/x-icon",
        Some("woff2") => "font/woff2",
        Some("woff") => "font/woff",
        Some("ttf") => "font/ttf",
        Some("wasm") => "application/wasm",
        Some("txt") => "text/plain; charset=utf-8",
        _ => "application/octet-stream",
    }
}

fn serve_static(client: &mut TcpStream, root: &Path, head: &Head) -> io::Result<()> {
    if head.method != "GET" && head.method != "HEAD" {
        return respond(client, 405, "text/plain", b"method not allowed", false);
    }
    let file = match resolve_static(root, &head.path) {
        Some(f) => f,
        None => return respond(client, 403, "text/plain", b"forbidden", false),
    };
    match std::fs::read(&file) {
        Ok(body) => respond(client, 200, content_type(&file), &body, head.method == "HEAD"),
        Err(_) => respond(client, 404, "text/plain", b"not found", false),
    }
}

fn reason(status: u16) -> &'static str {
    match status {
        200 => "OK",
        400 => "Bad Request",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        431 => "Request Header Fields Too Large",
        502 => "Bad Gateway",
        503 => "Service Unavailable",
        _ => "",
    }
}

fn respond(client: &mut TcpStream, status: u16, ctype: &str, body: &[u8], head_only: bool) -> io::Result<()> {
    let mut out = format!(
        "HTTP/1.1 {status} {}\r\nContent-Type: {ctype}\r\nContent-Length: {}\r\nCache-Control: no-cache\r\nConnection: close\r\n",
        reason(status),
        body.len()
    );
    if status == 503 {
        out.push_str("Retry-After: 1\r\n");
    }
    out.push_str("\r\n");
    client.write_all(out.as_bytes())?;
    if !head_only {
        client.write_all(body)?;
    }
    client.flush()?;
    let _ = client.shutdown(Shutdown::Write);
    Ok(())
}
