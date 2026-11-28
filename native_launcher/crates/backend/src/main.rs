//! `Ducky-Backend [--port N]`: bind loopback, say hello on stdout, serve until stdin closes
//! (the launcher went away) or a `bye` line arrives.

use std::io::{BufRead, Write};
use std::process::ExitCode;

use tokio::net::TcpListener;
use tokio::sync::oneshot;

fn port_arg() -> u16 {
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        if a == "--port" {
            return args.next().and_then(|p| p.parse().ok()).unwrap_or(0);
        }
    }
    0
}

/// Resolves when the launcher says bye or its end of stdin closes.
fn launcher_gone() -> oneshot::Receiver<()> {
    let (tx, rx) = oneshot::channel();
    // A plain thread: reading stdin blocks, and it has to outlive nothing.
    std::thread::spawn(move || {
        for line in std::io::stdin().lock().lines() {
            let Ok(line) = line else { break };
            let bye = serde_json::from_str::<serde_json::Value>(&line).is_ok_and(|v| v["t"] == "bye");
            if bye {
                break;
            }
        }
        let _ = tx.send(());
    });
    rx
}

#[tokio::main]
async fn main() -> ExitCode {
    let listener = match TcpListener::bind(("127.0.0.1", port_arg())).await {
        Ok(l) => l,
        Err(e) => {
            eprintln!("bind 127.0.0.1: {e}");
            return ExitCode::FAILURE;
        }
    };
    let port = listener.local_addr().map(|a| a.port()).unwrap_or(0);
    {
        let mut out = std::io::stdout().lock();
        let _ = writeln!(out, "{}", backend::hello_line(port));
        let _ = out.flush();
    }
    let gone = launcher_gone();
    match backend::serve(listener, async move { drop(gone.await) }).await {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("serve: {e}");
            ExitCode::FAILURE
        }
    }
}
