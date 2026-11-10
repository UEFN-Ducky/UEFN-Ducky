use serde_json::{json, Value};
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};
use supervisor::policy::{hello_deadline, CrashLoop, Decision, RestartPolicy, LOOP_BACKOFF_S};
use supervisor::protocol::{encode_bye, encode_call, parse_line, BackendMsg};
use supervisor::{exit, Config, Step, Supervisor, HELLO_CAP, HELLO_SILENCE};

const MODE_ENV: &str = "FAKE_BACKEND_MODE";
const SPAWNS_ENV: &str = "FAKE_BACKEND_SPAWNS";
const EOF_ENV: &str = "FAKE_BACKEND_EOF";
const DELAY_ENV: &str = "UEFN_DUCKY_TEST_HELLO_DELAY_S";

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
    let cl = CrashLoop { failures: vec![1.5, 2.5], until: 99.0, reason: String::new() };
    cl.save(&dir).unwrap();
    assert!(dir.join("launcher").join("crash_loop.json").is_file());
    assert_eq!(CrashLoop::load(&dir), cl);
    assert_eq!(CrashLoop::parse("garbage"), CrashLoop::default());
    let v: Value = serde_json::from_str(&cl.to_json()).unwrap();
    assert_eq!(v, json!({"failures": [1.5, 2.5], "until": 99.0}));
    let named = CrashLoop { reason: "port_blocked".into(), ..cl };
    named.save(&dir).unwrap();
    assert_eq!(CrashLoop::load(&dir).reason, "port_blocked");
}

#[test]
fn hello_deadline_follows_stages_up_to_the_cap() {
    let t0 = Instant::now();
    let s = |n: u64| t0 + Duration::from_secs(n);
    // Stages every 10 s for 40 s: the wait is still open 20 s after the last one.
    assert_eq!(hello_deadline(t0, s(40), HELLO_SILENCE, HELLO_CAP), s(60));
    // Silent from the start: 20 s.
    assert_eq!(hello_deadline(t0, t0, HELLO_SILENCE, HELLO_CAP), s(20));
    // Stages at 8/30/78 s, hello at 96 s is inside the 98 s deadline.
    assert!(hello_deadline(t0, s(78), HELLO_SILENCE, HELLO_CAP) > s(96));
    // Lines never push it past 180 s.
    assert_eq!(hello_deadline(t0, s(175), HELLO_SILENCE, HELLO_CAP), s(180));
}

// ---- real processes: this test binary re-runs itself as a fake backend ----

fn scratch(name: &str) -> PathBuf {
    let d = Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!("supervisor-{name}"));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

fn fake(mode: &str, dir: &Path, hello_silence: Duration) -> Config {
    fake_slow(mode, dir, hello_silence, Duration::from_secs(60), 0.0)
}

fn fake_slow(mode: &str, dir: &Path, hello_silence: Duration, hello_cap: Duration, delay_s: f64) -> Config {
    Config {
        exe: std::env::current_exe().unwrap(),
        args: ["--exact", "fake_backend_entry", "--nocapture", "--test-threads=1"].map(String::from).to_vec(),
        env: vec![
            (MODE_ENV.into(), mode.into()),
            (SPAWNS_ENV.into(), dir.join("spawns.txt").display().to_string()),
            (EOF_ENV.into(), dir.join("stdin_eof.txt").display().to_string()),
            (DELAY_ENV.into(), delay_s.to_string()),
        ],
        hello_silence,
        hello_cap,
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
        // Not 3: that is the backend's BUSY exit, a start error rather than a crash.
        "crash" => std::process::exit(4),
        // The 10013 bind failure that only the Python fallback used to show.
        "port_blocked" => {
            eprintln!("Traceback (most recent call last):");
            eprintln!(r"  File C:\Users\Someone\AppData\Local\ducky\server.py");
            eprintln!(r"PermissionError: [WinError 10013] An attempt was made to access a socket in C:\Users\Someone\x");
            std::process::exit(1);
        }
        "silent" => std::thread::sleep(Duration::from_secs(30)),
        // A slow PC: a stage line every 0.3 s until the delay, then hello.
        "stages" => {
            let delay: f64 = std::env::var(DELAY_ENV).ok().and_then(|v| v.parse().ok()).unwrap_or(0.0);
            let started = Instant::now();
            while started.elapsed().as_secs_f64() < delay {
                say(json!({"t": "event", "name": "stage", "data": {"name": "panel_api_init", "ms": started.elapsed().as_millis() as u64}}));
                std::thread::sleep(Duration::from_millis(300));
            }
            say(hello(1));
            std::thread::sleep(Duration::from_secs(30));
        }
        // A slow PC whose boot only logs to stderr (imports, no stage events yet).
        "stderr_stages" => {
            let delay: f64 = std::env::var(DELAY_ENV).ok().and_then(|v| v.parse().ok()).unwrap_or(0.0);
            let started = Instant::now();
            while started.elapsed().as_secs_f64() < delay {
                eprintln!("boot stage import_panel_api");
                std::thread::sleep(Duration::from_millis(300));
            }
            say(hello(1));
            std::thread::sleep(Duration::from_secs(30));
        }
        "stall" => {
            say(json!({"t": "event", "name": "stage", "data": {"name": "panel_api_init", "ms": 5}}));
            std::thread::sleep(Duration::from_secs(30));
        }
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
fn crash_loop_names_why_the_backend_died() {
    let dir = scratch("port_blocked");
    let err = Supervisor::start(fake("port_blocked", &dir, Duration::from_secs(15))).err().unwrap();
    assert_eq!(err.code, exit::CRASH_LOOP);
    assert!(err.reason.contains("PermissionError: [WinError 10013]"), "{}", err.reason);
    assert!(err.reason.contains(r"socket in ~\x") && !err.reason.contains("Someone"), "{}", err.reason);
    assert_eq!(supervisor::crash_slug(&err.reason), "port_blocked");
    assert_eq!(CrashLoop::load(&dir).reason, "port_blocked");
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
fn stages_keep_the_hello_wait_open_past_the_silence() {
    let dir = scratch("stages_ok");
    let mut sup = Supervisor::start(fake_slow("stages", &dir, Duration::from_secs(1), Duration::from_secs(30), 3.0)).unwrap();
    assert!(sup.hello().unwrap().protocol_ok());
    sup.shutdown("test done");
}

#[test]
fn stderr_boot_lines_keep_the_hello_wait_open_too() {
    // Scaled from "a stage every 10 s for 60 s": 1 s silence, lines every 0.3 s for 3 s.
    let dir = scratch("stderr_stages");
    let mut sup = Supervisor::start(fake_slow("stderr_stages", &dir, Duration::from_secs(1), Duration::from_secs(30), 3.0)).unwrap();
    assert!(sup.hello().unwrap().protocol_ok());
    sup.shutdown("test done");
}

#[test]
fn silence_after_a_stage_gives_23_naming_it() {
    let dir = scratch("stall");
    let err = Supervisor::start(fake("stall", &dir, Duration::from_secs(1))).err().unwrap();
    assert_eq!(err.code, exit::NO_HELLO);
    assert!(err.reason.starts_with("no hello, last stage panel_api_init after "), "{}", err.reason);
}

#[test]
fn stages_never_outlast_the_cap() {
    let dir = scratch("stages_cap");
    let started = Instant::now();
    let err = Supervisor::start(fake_slow("stages", &dir, Duration::from_secs(1), Duration::from_secs(2), 30.0)).err().unwrap();
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
    assert_eq!((cfg.hello_silence, cfg.hello_cap), (HELLO_SILENCE, HELLO_CAP));
    cfg.hello_silence = Duration::from_secs(1);
    assert_eq!(Supervisor::start(cfg).err().unwrap().code, exit::START_ERROR);
}

// ---- cross-check against the Python side's fixtures ----

fn python_fixture(name: &str) -> String {
    // NATIVE_HOST_FIXTURES overrides; default is the repo's Python fixtures.
    let dir = std::env::var_os("NATIVE_HOST_FIXTURES").map(PathBuf::from).unwrap_or_else(|| {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../ducky_app/frontend/native_host/fixtures")
    });
    std::fs::read_to_string(dir.join(name)).unwrap_or_else(|e| panic!("{name}: {e}"))
}

fn backend_msgs(name: &str) -> Vec<BackendMsg> {
    // Launcher → backend lines (call, bye) in the fixtures are not the decoder's input.
    python_fixture(name)
        .lines()
        .filter(|l| !l.contains(r#""t":"call""#) && !l.contains(r#""t":"bye""#))
        .map(|l| parse_line(l).unwrap_or_else(|| panic!("did not decode: {l}")))
        .collect()
}

#[test]
fn python_valid_and_unknown_fields_decode_the_same() {
    let valid = backend_msgs("valid.jsonl");
    assert_eq!(valid, backend_msgs("unknown_fields.jsonl"));
    match &valid[0] {
        BackendMsg::Hello(h) => {
            assert!(h.protocol_ok());
            assert_eq!((h.port, h.pid, h.app_version.as_str()), (51234, 4242, "1.2.471"));
            assert_eq!(h.origin, "http://127.0.0.1:51234");
            assert_eq!(h.profile_dir, r"C:\Profile\UEFN-Ducky");
        }
        other => panic!("{other:?}"),
    }
    assert_eq!(valid[1], BackendMsg::Result { id: "c1".into(), result: Ok(json!({"x": 10, "y": 20, "width": 1280, "height": 800})) });
    assert_eq!(valid[2], BackendMsg::Result { id: "c2".into(), result: Ok(Value::Null) });
    assert_eq!(valid[3], BackendMsg::Result { id: "c3".into(), result: Err("unknown".into()) });
    assert_eq!(valid[4], BackendMsg::Event { name: "toast".into(), data: json!({"text": "Saved", "level": "info"}) });
    assert_eq!(valid[5], BackendMsg::Event { name: "title".into(), data: json!({"title": "UEFN Ducky — Héllo"}) });
    assert_eq!(valid[6], BackendMsg::Event { name: "quit".into(), data: json!({}) });
    // The Python encoder's call/bye lines round-trip through the Rust encoder byte-for-value.
    for l in python_fixture("valid.jsonl").lines().filter(|l| l.contains(r#""t":"call""#)) {
        let v: Value = serde_json::from_str(l).unwrap();
        let args: Vec<Value> = v["args"].as_array().unwrap().clone();
        let mine: Value = serde_json::from_str(encode_call(v["id"].as_str().unwrap(), v["name"].as_str().unwrap(), &args).trim()).unwrap();
        assert_eq!(mine, v);
    }
}

#[test]
fn python_mismatch_hellos_are_mismatches() {
    let msgs = backend_msgs("mismatch.jsonl");
    assert_eq!(msgs.len(), 3);
    for m in msgs {
        assert!(matches!(m, BackendMsg::Hello(ref h) if !h.protocol_ok()), "{m:?}");
    }
}

#[test]
fn python_invalid_lines_never_give_a_usable_hello() {
    // Rust is lenient where Python rejects; the line that matters is that none starts a window.
    for l in python_fixture("invalid.jsonl").lines() {
        if let Some(BackendMsg::Hello(h)) = parse_line(l) {
            assert!(!h.protocol_ok() || h.port == 0, "usable hello from invalid line: {l}");
        }
    }
}
