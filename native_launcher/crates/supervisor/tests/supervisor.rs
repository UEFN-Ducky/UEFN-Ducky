use serde_json::{json, Value};
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};
use supervisor::policy::{CrashLoop, Decision, RestartPolicy, LOOP_BACKOFF_S};
use supervisor::protocol::{encode_bye, encode_call, parse_line, BackendMsg};
use supervisor::{exit, Config, Step, Supervisor};

const MODE_ENV: &str = "FAKE_BACKEND_MODE";
const SPAWNS_ENV: &str = "FAKE_BACKEND_SPAWNS";
const EOF_ENV: &str = "FAKE_BACKEND_EOF";

// ---- contract JSON ----

#[test]
fn contract_fixture_lines_parse() {
    let text = include_str!("fixtures/backend_lines.jsonl");
    let msgs: Vec<Option<BackendMsg>> = text.lines().map(parse_line).collect();
    match &msgs[0] {
        Some(BackendMsg::Hello(h)) => {
            assert!(h.protocol_ok());
            assert_eq!((h.port, h.pid, h.app_version.as_str()), (4710, 1234, "1.2.471"));
            assert_eq!(h.origin, "http://127.0.0.1:4710");
            assert_eq!(h.profile_dir, "C:/profile");
        }
        other => panic!("{other:?}"),
    }
    assert_eq!(msgs[1], Some(BackendMsg::Result { id: "7".into(), result: Ok(json!({"a": [1, 2]})) }));
    assert_eq!(msgs[2], Some(BackendMsg::Result { id: "8".into(), result: Err("boom".into()) }));
    assert_eq!(msgs[3], Some(BackendMsg::Event { name: "toast".into(), data: json!({"text": "hi"}) }));
    assert!(matches!(&msgs[5], Some(BackendMsg::Event { name, .. }) if name == "quit"));
    assert!(matches!(&msgs[6], Some(BackendMsg::Other(_))));
    assert_eq!((msgs[7].clone(), msgs[8].clone(), msgs[9].clone()), (None, None, None));
    match &msgs[10] {
        Some(BackendMsg::Hello(h)) => assert!(!h.protocol_ok()),
        other => panic!("{other:?}"),
    }
}

#[test]
fn launcher_lines_encode_to_the_contract() {
    let call = encode_call("c1", "get_settings", &[json!(1), json!("x")]);
    assert!(call.ends_with('\n') && !call.trim_end().contains('\n'));
    let v: Value = serde_json::from_str(call.trim()).unwrap();
    assert_eq!(v, json!({"t": "call", "id": "c1", "name": "get_settings", "args": [1, "x"]}));
    let bye: Value = serde_json::from_str(encode_bye("window closed").trim()).unwrap();
    assert_eq!(bye, json!({"t": "bye", "reason": "window closed"}));
}

// ---- restart state machine ----

#[test]
fn two_restarts_in_60s_then_give_up() {
    let mut p = RestartPolicy::new();
    let t0 = Instant::now();
    assert_eq!(p.on_crash(t0), Decision::Restart);
    assert_eq!(p.on_crash(t0 + Duration::from_secs(10)), Decision::Restart);
    assert_eq!(p.on_crash(t0 + Duration::from_secs(20)), Decision::GiveUp);
}

#[test]
fn crashes_older_than_60s_are_forgotten() {
    let mut p = RestartPolicy::new();
    let t0 = Instant::now();
    assert_eq!(p.on_crash(t0), Decision::Restart);
    assert_eq!(p.on_crash(t0 + Duration::from_secs(30)), Decision::Restart);
    assert_eq!(p.on_crash(t0 + Duration::from_secs(61)), Decision::Restart);
    assert_eq!(p.recent_crashes(), 2);
}

#[test]
fn crash_loop_three_failures_in_10_min_backs_off_24h() {
    let mut cl = CrashLoop::default();
    assert!(!cl.record_failure(1000.0));
    assert!(!cl.record_failure(1100.0));
    assert!(cl.record_failure(1200.0));
    assert_eq!(cl.until, 1200.0 + LOOP_BACKOFF_S);
    assert!(cl.active(1200.0 + LOOP_BACKOFF_S - 1.0));
    assert!(!cl.active(1200.0 + LOOP_BACKOFF_S + 1.0));

    let mut spread = CrashLoop::default();
    spread.record_failure(0.0);
    spread.record_failure(400.0);
    assert!(!spread.record_failure(800.0), "first failure is older than 10 min");
    assert_eq!(spread.failures, vec![400.0, 800.0]);
}

#[test]
fn crash_loop_file_round_trip() {
    let dir = scratch("crash_loop_file");
    let cl = CrashLoop { failures: vec![1.5, 2.5], until: 99.0 };
    cl.save(&dir).unwrap();
    assert!(dir.join("launcher").join("crash_loop.json").is_file());
    assert_eq!(CrashLoop::load(&dir), cl);
    assert_eq!(CrashLoop::parse("garbage"), CrashLoop::default());
    let v: Value = serde_json::from_str(&cl.to_json()).unwrap();
    assert_eq!(v, json!({"failures": [1.5, 2.5], "until": 99.0}));
}

// ---- real processes: this test binary re-runs itself as a fake backend ----

fn scratch(name: &str) -> PathBuf {
    let d = Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!("supervisor-{name}"));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

fn fake(mode: &str, dir: &Path, hello_timeout: Duration) -> Config {
    Config {
        exe: std::env::current_exe().unwrap(),
        args: ["--exact", "fake_backend_entry", "--nocapture", "--test-threads=1"].map(String::from).to_vec(),
        env: vec![
            (MODE_ENV.into(), mode.into()),
            (SPAWNS_ENV.into(), dir.join("spawns.txt").display().to_string()),
            (EOF_ENV.into(), dir.join("stdin_eof.txt").display().to_string()),
        ],
        hello_timeout,
        app_data: dir.to_path_buf(),
    }
}

fn spawns(dir: &Path) -> usize {
    std::fs::read_to_string(dir.join("spawns.txt")).map(|t| t.lines().count()).unwrap_or(0)
}

fn say(line: Value) {
    // Leading newline: libtest prints "test fake_backend_entry ... " with no line end first.
    let mut out = std::io::stdout().lock();
    write!(out, "\n{line}\n").unwrap();
    out.flush().unwrap();
}

/// Not a real test: does nothing unless started by `fake()`.
#[test]
fn fake_backend_entry() {
    let Ok(mode) = std::env::var(MODE_ENV) else { return };
    if let Ok(p) = std::env::var(SPAWNS_ENV) {
        let mut f = std::fs::OpenOptions::new().create(true).append(true).open(p).unwrap();
        writeln!(f, "{}", std::process::id()).unwrap();
    }
    let hello = |protocol: i64| {
        json!({"t": "hello", "protocol": protocol, "app_version": "1.2.test", "port": 4711,
               "origin": "http://127.0.0.1:4711", "profile_dir": "p", "pid": std::process::id()})
    };
    match mode.as_str() {
        "crash" => std::process::exit(3),
        "silent" => std::thread::sleep(Duration::from_secs(30)),
        "badproto" => {
            say(hello(2));
            std::thread::sleep(Duration::from_secs(30));
        }
        "crash_after_hello" => {
            say(hello(1));
            std::process::exit(1);
        }
        "serve" => {
            say(json!({"t": "log", "text": "noise before hello"}));
            say(hello(1));
            for line in std::io::stdin().lock().lines() {
                let Ok(line) = line else { break };
                let v: Value = serde_json::from_str(&line).unwrap_or(Value::Null);
                match (v["t"].as_str(), v["name"].as_str()) {
                    (Some("call"), Some("crash")) => std::process::exit(5),
                    (Some("call"), Some("quit")) => {
                        say(json!({"t": "event", "name": "quit", "data": {}}));
                        std::process::exit(7);
                    }
                    (Some("call"), _) => say(json!({"t": "result", "id": v["id"], "ok": true, "value": v["args"]})),
                    (Some("bye"), _) => {}
                    _ => {}
                }
            }
            if let Ok(p) = std::env::var(EOF_ENV) {
                std::fs::write(p, "eof").unwrap();
            }
            std::process::exit(0);
        }
        other => panic!("unknown mode {other}"),
    }
}

fn next_non_idle(sup: &mut Supervisor) -> Step {
    let deadline = Instant::now() + Duration::from_secs(30);
    loop {
        match sup.next(Duration::from_millis(200)) {
            Step::Idle if Instant::now() < deadline => continue,
            step => return step,
        }
    }
}

#[test]
fn hello_call_result_and_stdin_closed_on_shutdown() {
    let dir = scratch("serve");
    let mut sup = Supervisor::start(fake("serve", &dir, Duration::from_secs(15))).unwrap();
    let hello = sup.hello().unwrap().clone();
    assert_eq!((hello.port, hello.origin.as_str()), (4711, "http://127.0.0.1:4711"));
    sup.call("1", "echo", &[json!("x")]).unwrap();
    assert_eq!(next_non_idle(&mut sup), Step::Msg(BackendMsg::Result { id: "1".into(), result: Ok(json!(["x"])) }));
    sup.shutdown("test done");
    assert_eq!(std::fs::read_to_string(dir.join("stdin_eof.txt")).unwrap(), "eof");
    assert_eq!(spawns(&dir), 1);
}

#[test]
fn crash_after_hello_restarts_then_quit_exits() {
    let dir = scratch("restart");
    let mut sup = Supervisor::start(fake("serve", &dir, Duration::from_secs(15))).unwrap();
    sup.call("1", "crash", &[]).unwrap();
    assert!(matches!(next_non_idle(&mut sup), Step::Restarted(_)));
    assert_eq!(spawns(&dir), 2);
    sup.call("2", "quit", &[]).unwrap();
    assert!(matches!(next_non_idle(&mut sup), Step::Msg(BackendMsg::Event { name, .. }) if name == "quit"));
    assert_eq!(next_non_idle(&mut sup), Step::Exited, "quit then a non-zero exit is not a crash");
    assert!(!dir.join("launcher").join("crash_loop.json").exists());
}

#[test]
fn third_crash_in_60s_gives_22_and_records_crash_loop() {
    let dir = scratch("crash_loop");
    let mut sup = Supervisor::start(fake("crash_after_hello", &dir, Duration::from_secs(15))).unwrap();
    let mut restarts = 0;
    let code = loop {
        match next_non_idle(&mut sup) {
            Step::Restarted(_) => restarts += 1,
            Step::Fatal(e) => break e.code,
            other => panic!("{other:?}"),
        }
    };
    assert_eq!((code, restarts, spawns(&dir)), (exit::CRASH_LOOP, 2, 3));
    assert_eq!(CrashLoop::load(&dir).failures.len(), 1);
}

#[test]
fn dying_before_hello_three_times_gives_22() {
    let dir = scratch("crash_before_hello");
    let err = Supervisor::start(fake("crash", &dir, Duration::from_secs(15))).err().unwrap();
    assert_eq!(err.code, exit::CRASH_LOOP);
    assert_eq!(spawns(&dir), 3);
}

#[test]
fn no_hello_in_time_gives_23() {
    let dir = scratch("silent");
    let started = Instant::now();
    let err = Supervisor::start(fake("silent", &dir, Duration::from_millis(1500))).err().unwrap();
    assert_eq!(err.code, exit::NO_HELLO);
    assert!(started.elapsed() < Duration::from_secs(10));
}

#[test]
fn protocol_mismatch_gives_21() {
    let dir = scratch("badproto");
    let err = Supervisor::start(fake("badproto", &dir, Duration::from_secs(15))).err().unwrap();
    assert_eq!(err.code, exit::PROTOCOL_MISMATCH);
    assert_eq!(spawns(&dir), 1, "no restart on mismatch");
}

#[test]
fn missing_exe_gives_24() {
    let dir = scratch("missing");
    let mut cfg = Config::ducky(dir.join("nope").join("UEFN-Ducky.exe"), &["--x".into()], &dir);
    assert_eq!(cfg.args, vec!["--backend".to_string(), "--x".to_string()]);
    cfg.hello_timeout = Duration::from_secs(1);
    assert_eq!(Supervisor::start(cfg).err().unwrap().code, exit::START_ERROR);
}
