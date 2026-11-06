//! The early page as the app wires it: the static UI answers before hello, a backend call
//! waits, and the hello's (internal) origin releases it.

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::thread;
use std::time::{Duration, Instant};

fn get(addr: std::net::SocketAddr, path: &str) -> String {
    let mut s = TcpStream::connect(addr).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(10))).unwrap();
    write!(s, "GET {path} HTTP/1.1\r\nHost: {addr}\r\nConnection: close\r\n\r\n").unwrap();
    let mut out = String::new();
    let _ = s.read_to_string(&mut out);
    out
}

#[test]
fn static_before_hello_api_held_until_release() {
    let base = std::env::temp_dir().join(format!("ducky-early-{}", std::process::id()));
    let dist = base.join("_internal/frontend/ui_web/web/dist");
    std::fs::create_dir_all(&dist).unwrap();
    std::fs::write(dist.join("index.html"), "<html>shell</html>").unwrap();
    let found = ducky_launcher::start::web_dist(&base.join("UEFN-Ducky.exe")).expect("dist found");

    let srv = server::Server::bind(0, found, Duration::from_secs(20)).unwrap();
    let addr = srv.addr();
    assert!(get(addr, "/").contains("shell"), "static UI before hello");

    // A fake backend on its own (internal) port, like `UEFN_DUCKY_BACKEND_UI_PORT=0`.
    let backend = TcpListener::bind("127.0.0.1:0").unwrap();
    let origin = format!("http://{}", backend.local_addr().unwrap());
    thread::spawn(move || {
        if let Ok((mut c, _)) = backend.accept() {
            let mut buf = [0u8; 4096];
            let _ = c.read(&mut buf);
            let _ = c.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok");
        }
    });

    let started = Instant::now();
    let call = thread::spawn(move || get(addr, "/api/status"));
    thread::sleep(Duration::from_millis(300));
    assert!(!call.is_finished(), "backend call must wait for hello");
    assert!(srv.release_origin(&origin));
    let reply = call.join().unwrap();
    assert!(reply.ends_with("ok"), "{reply}");
    assert!(started.elapsed() < Duration::from_secs(10));
    let _ = std::fs::remove_dir_all(&base);
}
