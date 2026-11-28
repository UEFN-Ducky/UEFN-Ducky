//! The real `Ducky-Backend` binary, run the way the launcher runs it.

use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::Path;
use std::time::{Duration, Instant};

use supervisor::{Config, Supervisor};

fn get(origin: &str, path: &str) -> String {
    let mut s = TcpStream::connect(origin.strip_prefix("http://").unwrap()).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(10))).unwrap();
    write!(s, "GET {path} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n").unwrap();
    let mut out = String::new();
    let _ = s.read_to_string(&mut out);
    out
}

#[test]
fn the_supervisor_starts_it_health_answers_and_closing_stdin_ends_it() {
    let cfg = Config::helper(env!("CARGO_BIN_EXE_Ducky-Backend"), Path::new(env!("CARGO_TARGET_TMPDIR")));
    let mut sup = Supervisor::start(cfg).unwrap();
    let hello = sup.hello().unwrap().clone();
    assert!(hello.protocol_ok() && hello.port != 0, "{hello:?}");
    assert_eq!(Some(hello.pid), sup.backend_pid());
    assert_eq!(hello.origin, format!("http://127.0.0.1:{}", hello.port));

    let resp = get(&hello.origin, "/__rs/health");
    assert!(resp.starts_with("HTTP/1.1 200"), "{resp}");
    let body: serde_json::Value = serde_json::from_str(resp.split("\r\n\r\n").nth(1).unwrap()).unwrap();
    assert_eq!((&body["ok"], &body["pid"]), (&true.into(), &hello.pid.into()), "{body}");
    assert!(get(&hello.origin, "/nope").starts_with("HTTP/1.1 404"));

    let asked = Instant::now();
    let status = sup.shutdown("test").expect("exit status");
    assert!(status.success(), "{status:?}");
    assert!(asked.elapsed() < Duration::from_secs(4), "ended by the kill, not by itself");
}

#[test]
#[allow(clippy::disallowed_methods)] // a plain test thread: nothing async to hold up
fn a_taken_port_is_an_error_exit_with_no_hello() {
    let held = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let port = held.local_addr().unwrap().port().to_string();
    let out = std::process::Command::new(env!("CARGO_BIN_EXE_Ducky-Backend")).args(["--port", &port]).output().unwrap();
    assert!(!out.status.success());
    assert!(out.stdout.is_empty(), "{}", String::from_utf8_lossy(&out.stdout));
    assert!(String::from_utf8_lossy(&out.stderr).starts_with("bind 127.0.0.1"));
}
