//! A stand-in for Epic's verse-lsp, for the tests of the editor's bridge: it answers
//! each `Content-Length` framed message with the same message and says one line on
//! stderr. A message holding `"exit"` ends it with code 3. While it runs it holds a
//! loopback port open, named in `fake-verse-lsp.port` in the folder it was started in:
//! a test tells a running one from an ended one by connecting there. It stays 30 s
//! after its input closes, so a test sees whether it was stopped or only left alone.
#![allow(clippy::disallowed_methods)] // no async here: a plain program

use std::io::{BufRead, Read, Write};

fn main() {
    let alive = std::net::TcpListener::bind(("127.0.0.1", 0)).expect("a loopback port");
    let port = alive.local_addr().expect("its port").port();
    let _ = std::fs::write("fake-verse-lsp.port", port.to_string());
    eprintln!("fake verse-lsp up  ");
    answer();
    std::thread::sleep(std::time::Duration::from_secs(30));
}

/// Answer each message until the input closes.
fn answer() {
    let (mut stdin, mut stdout) = (std::io::stdin().lock(), std::io::stdout().lock());
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
        if String::from_utf8_lossy(&body).contains("\"exit\"") {
            std::process::exit(3);
        }
        let _ = write!(stdout, "Content-Length: {length}\r\n\r\n");
        let _ = stdout.write_all(&body).and_then(|()| stdout.flush());
    }
}
