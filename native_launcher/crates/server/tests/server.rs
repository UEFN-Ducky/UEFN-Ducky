use server::port::{choose_listener_port, parse_saved_port, saved_port, PortChoice};
use server::{is_backend, origin_addr, parse_head, resolve_static, Server};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::thread;
use std::time::{Duration, Instant};

fn fixtures() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
}

fn get(addr: std::net::SocketAddr, raw: &str) -> String {
    let mut s = TcpStream::connect(addr).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(10))).unwrap();
    s.write_all(raw.as_bytes()).unwrap();
    let mut out = String::new();
    let _ = s.read_to_string(&mut out);
    out
}

// ---- port ----

#[test]
fn port_from_fixture_file() {
    assert_eq!(saved_port(&fixtures()), 4500);
    let c = choose_listener_port(None, saved_port(&fixtures()), |_| false);
    assert_eq!(c, PortChoice { listener: 4500, moved: false });
    assert_eq!(c.ui_port(), 4499);
}

#[test]
fn port_defaults_and_bad_files() {
    assert_eq!(saved_port(Path::new("does/not/exist")), 0);
    assert_eq!(parse_saved_port("{\"port\": 80}"), 0);
    assert_eq!(parse_saved_port("not json"), 0);
    assert_eq!(parse_saved_port("{\"port\": \"4600\"}"), 4600);
    let c = choose_listener_port(None, 0, |_| false);
    assert_eq!((c.listener, c.ui_port(), c.moved), (4200, 4199, false));
}

#[test]
fn env_port_wins_without_probing() {
    let c = choose_listener_port(Some("5123"), 4500, |_| panic!("no probe with env"));
    assert_eq!(c, PortChoice { listener: 5123, moved: false });
}

#[test]
fn reserved_pair_moves_to_first_free_candidate() {
    // 4199 reserved, 4300 pair reserved too: lands on 4400.
    let c = choose_listener_port(None, 0, |p| p == 4199 || p == 4300);
    assert_eq!(c, PortChoice { listener: 4400, moved: true });
}

// ---- routing ----

#[test]
fn routing_rules() {
    let h = parse_head(b"GET /api/chat?x=1 HTTP/1.1\r\nHost: a\r\n\r\n").unwrap();
    assert_eq!(h.path, "/api/chat");
    assert!(is_backend(&h));
    let ws = parse_head(b"GET /live HTTP/1.1\r\nUpgrade: WebSocket\r\nConnection: Upgrade\r\n\r\n").unwrap();
    assert!(ws.websocket && is_backend(&ws));
    let ev = parse_head(b"POST /__panel_event HTTP/1.1\r\n\r\n").unwrap();
    assert!(is_backend(&ev));
    let st = parse_head(b"GET /app.js HTTP/1.1\r\n\r\n").unwrap();
    assert!(!is_backend(&st));
    assert!(resolve_static(Path::new("r"), "/../secret").is_none());
    assert!(resolve_static(Path::new("r"), "/%2e%2e/secret").is_none());
    assert_eq!(resolve_static(Path::new("r"), "/"), Some(Path::new("r").join("index.html")));
    assert_eq!(origin_addr("http://127.0.0.1:4321").unwrap().port(), 4321);
}

// ---- live server ----

#[test]
fn serves_static_ui_before_hello() {
    let srv = Server::bind(0, fixtures().join("ui"), Duration::from_secs(30)).unwrap();
    let resp = get(srv.addr(), "GET / HTTP/1.1\r\nHost: x\r\n\r\n");
    assert!(resp.starts_with("HTTP/1.1 200"), "{resp}");
    assert!(resp.contains("text/html") && resp.contains("fixture"));
    srv.gate().close(); // no backend: a path with no file here is a plain 404
    let miss = get(srv.addr(), "GET /nope.js HTTP/1.1\r\n\r\n");
    assert!(miss.starts_with("HTTP/1.1 404"), "{miss}");
}

#[test]
fn paths_without_a_file_here_go_to_the_backend() {
    // Plugin panels load the kit from /plugin-ui/: served from the launcher's folder it was
    // a 404 and every plugin drew as bare HTML.
    let srv = Server::bind(0, fixtures().join("ui"), Duration::from_secs(30)).unwrap();
    let (backend, seen) = fake_backend();
    srv.release_origin(&format!("http://{backend}"));
    let resp = get(srv.addr(), "GET /plugin-ui/forge/kit/ducky.css HTTP/1.1\r\nHost: x\r\n\r\n");
    assert!(resp.starts_with("HTTP/1.1 200") && resp.ends_with("from-backend"), "{resp}");
    let req = String::from_utf8(seen.join().unwrap()).unwrap();
    assert!(req.starts_with("GET /plugin-ui/forge/kit/ducky.css HTTP/1.1"), "{req}");
    // Files the launcher has are still its own.
    assert!(get(srv.addr(), "GET /index.html HTTP/1.1\r\n\r\n").contains("fixture"));
}

/// A fake backend that answers one request with its own path, echoing the request bytes.
fn fake_backend() -> (std::net::SocketAddr, thread::JoinHandle<Vec<u8>>) {
    let l = TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = l.local_addr().unwrap();
    let h = thread::spawn(move || {
        let (mut s, _) = l.accept().unwrap();
        let mut buf = vec![0u8; 4096];
        let n = s.read(&mut buf).unwrap();
        buf.truncate(n);
        let body = b"from-backend";
        let _ = s.write_all(
            format!("HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", body.len()).as_bytes(),
        );
        let _ = s.write_all(body);
        buf
    });
    (addr, h)
}

#[test]
fn api_is_held_then_released_on_hello() {
    let srv = Server::bind(0, fixtures().join("ui"), Duration::from_secs(30)).unwrap();
    let (backend, seen) = fake_backend();
    let addr = srv.addr();
    let started = Instant::now();
    let client = thread::spawn(move || get(addr, "GET /api/hello?q=1 HTTP/1.1\r\nHost: x\r\n\r\n"));
    thread::sleep(Duration::from_millis(300));
    assert!(!client.is_finished(), "request must be held until hello");
    assert!(srv.release_origin(&format!("http://{backend}")));
    let resp = client.join().unwrap();
    assert!(started.elapsed() >= Duration::from_millis(300));
    assert!(resp.starts_with("HTTP/1.1 200") && resp.ends_with("from-backend"), "{resp}");
    let req = String::from_utf8(seen.join().unwrap()).unwrap();
    assert!(req.starts_with("GET /api/hello?q=1 HTTP/1.1"), "{req}");
}

#[test]
fn websocket_bytes_are_tunnelled_both_ways() {
    let srv = Server::bind(0, fixtures().join("ui"), Duration::from_secs(30)).unwrap();
    let l = TcpListener::bind("127.0.0.1:0").unwrap();
    let backend = l.local_addr().unwrap();
    let echo = thread::spawn(move || {
        let (mut s, _) = l.accept().unwrap();
        let mut buf = [0u8; 4096];
        let n = s.read(&mut buf).unwrap();
        assert!(String::from_utf8_lossy(&buf[..n]).contains("Upgrade: websocket"));
        s.write_all(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n").unwrap();
        let n = s.read(&mut buf).unwrap();
        s.write_all(&buf[..n]).unwrap(); // echo a "frame"
    });
    srv.release_origin(&format!("http://{backend}"));
    let mut c = TcpStream::connect(srv.addr()).unwrap();
    c.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
    c.write_all(b"GET /ws HTTP/1.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n").unwrap();
    let mut buf = [0u8; 4096];
    let n = c.read(&mut buf).unwrap();
    assert!(String::from_utf8_lossy(&buf[..n]).starts_with("HTTP/1.1 101"));
    c.write_all(b"\x81\x02hi").unwrap();
    let n = c.read(&mut buf).unwrap();
    assert_eq!(&buf[..n], b"\x81\x02hi");
    echo.join().unwrap();
}

#[test]
fn held_requests_get_503_after_the_hold_window() {
    let srv = Server::bind(0, fixtures().join("ui"), Duration::from_millis(400)).unwrap();
    let started = Instant::now();
    let resp = get(srv.addr(), "GET /api/x HTTP/1.1\r\n\r\n");
    assert!(resp.starts_with("HTTP/1.1 503"), "{resp}");
    assert!(resp.contains("Retry-After"));
    assert!(started.elapsed() >= Duration::from_millis(350));
    // Static files still work after the window.
    assert!(get(srv.addr(), "GET /index.html HTTP/1.1\r\n\r\n").starts_with("HTTP/1.1 200"));
}

#[test]
fn closed_gate_answers_503_at_once() {
    let srv = Server::bind(0, fixtures().join("ui"), Duration::from_secs(30)).unwrap();
    srv.gate().close();
    let started = Instant::now();
    assert!(get(srv.addr(), "GET /api/x HTTP/1.1\r\n\r\n").starts_with("HTTP/1.1 503"));
    assert!(started.elapsed() < Duration::from_secs(2));
}
