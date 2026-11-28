//! A stand-in for Epic's `lore.exe` and `urc.exe` in the parity tests: it prints what
//! the test scripted for its arguments, and never reads or changes a repository.
//!
//! The script is the JSON file `FAKE_CLI_SCRIPT` names:
//! `{"<arguments joined by |>": {"out": "...", "err": "...", "code": 0, "sleep": 0}}`
//! (lore's leading `-P --repository <root>` is not part of the key). Every call adds
//! its key as a line to `<script>.calls`. A call that is not scripted fails.

use std::io::Write;

#[allow(clippy::disallowed_methods)] // a test program: nothing shares its thread
fn main() {
    let mut args: Vec<String> = std::env::args().skip(1).collect();
    if args.first().is_some_and(|first| first == "-P") {
        args.drain(..args.len().min(3));
    }
    let (key, script) = (args.join("|"), std::env::var("FAKE_CLI_SCRIPT").unwrap_or_default());
    if let Ok(mut calls) = std::fs::OpenOptions::new().create(true).append(true).open(format!("{script}.calls")) {
        let _ = writeln!(calls, "{key}");
    }
    let table: serde_json::Value = std::fs::read_to_string(&script).ok().and_then(|text| serde_json::from_str(&text).ok()).unwrap_or_default();
    let Some(row) = table.get(&key) else {
        eprintln!("unexpected call {key}");
        std::process::exit(2);
    };
    std::thread::sleep(std::time::Duration::from_secs_f64(row["sleep"].as_f64().unwrap_or(0.0)));
    let _ = std::io::stdout().write_all(row["out"].as_str().unwrap_or_default().as_bytes());
    let _ = std::io::stderr().write_all(row["err"].as_str().unwrap_or_default().as_bytes());
    std::process::exit(row["code"].as_i64().unwrap_or(0) as i32);
}
