//! A tiny loopback HTTP/1.1 POST: enough for the panel's hand-off endpoint, no dependencies.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::Duration;

/// POST `body` as JSON to `http://127.0.0.1:<port><path>`; true on a 2xx status.
pub fn post_json(port: u16, path: &str, body: &[u8], timeout: Duration) -> bool {
    if port == 0 {
        return false;
    }
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut s) = TcpStream::connect_timeout(&addr, timeout) else { return false };
    let _ = s.set_read_timeout(Some(timeout));
    let _ = s.set_write_timeout(Some(timeout));
    let head = format!(
        "POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    );
    if s.write_all(head.as_bytes()).is_err() || s.write_all(body).is_err() || s.flush().is_err() {
        return false;
    }
    let mut got = Vec::with_capacity(64);
    let mut buf = [0u8; 64];
    while got.len() < 12 {
        match s.read(&mut buf) {
            Ok(0) | Err(_) => break,
            Ok(n) => got.extend_from_slice(&buf[..n]),
        }
    }
    status_code(&got).is_some_and(|c| (200..300).contains(&c))
}

/// `HTTP/1.x NNN ...` -> NNN.
pub fn status_code(head: &[u8]) -> Option<u16> {
    let s = std::str::from_utf8(head.get(..12)?).ok()?;
    if !s.starts_with("HTTP/1.") || s.as_bytes()[8] != b' ' {
        return None;
    }
    s[9..12].parse().ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;
    use std::sync::mpsc;

    fn serve_once(status: &'static str) -> (u16, mpsc::Receiver<Vec<u8>>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            let (mut c, _) = listener.accept().unwrap();
            let mut req = Vec::new();
            let mut buf = [0u8; 1024];
            loop {
                let n = c.read(&mut buf).unwrap();
                req.extend_from_slice(&buf[..n]);
                if let Some(end) = req.windows(4).position(|w| w == b"\r\n\r\n") {
                    let head = String::from_utf8_lossy(&req[..end]).to_ascii_lowercase();
                    let len: usize = head
                        .lines()
                        .find_map(|l| l.strip_prefix("content-length:"))
                        .map(|v| v.trim().parse().unwrap())
                        .unwrap_or(0);
                    if req.len() >= end + 4 + len || n == 0 {
                        break;
                    }
                }
            }
            c.write_all(format!("HTTP/1.0 {status}\r\nContent-Length: 0\r\n\r\n").as_bytes()).unwrap();
            tx.send(req).unwrap();
        });
        (port, rx)
    }

    #[test]
    fn posts_the_exact_body_and_reads_2xx() {
        let (port, rx) = serve_once("200 OK");
        let body = br#"{"paths": [], "links": ["uefn-ducky://x"]}"#;
        assert!(post_json(port, "/__panel_open_files", body, Duration::from_secs(5)));
        let req = rx.recv().unwrap();
        let text = String::from_utf8(req).unwrap();
        assert!(text.starts_with("POST /__panel_open_files HTTP/1.1\r\n"), "{text}");
        assert!(text.contains("Content-Type: application/json\r\n"));
        assert!(text.ends_with(std::str::from_utf8(body).unwrap()));
    }

    #[test]
    fn a_refusal_status_is_not_a_hand_off() {
        let (port, _rx) = serve_once("409 Conflict");
        assert!(!post_json(port, "/__panel_open_files", b"{}", Duration::from_secs(5)));
    }

    #[test]
    fn nothing_listening_is_false_fast() {
        let port = TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port();
        let t = std::time::Instant::now();
        assert!(!post_json(port, "/x", b"{}", Duration::from_millis(350)));
        assert!(t.elapsed() < Duration::from_secs(3));
    }

    #[test]
    fn status_lines() {
        assert_eq!(status_code(b"HTTP/1.0 200 OK\r\n"), Some(200));
        assert_eq!(status_code(b"HTTP/1.1 204 No"), Some(204));
        assert_eq!(status_code(b"garbage here!"), None);
        assert_eq!(status_code(b"HTTP/1.0"), None);
    }
}
