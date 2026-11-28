//! Ducky looking at itself (`frontend/self_health.py`): what uses this PC's CPU and memory,
//! which of it is Ducky, why the app may feel slow, and the safe fixes.
//!
//! One report reads the whole process table twice, a second apart (CPU), and asks only
//! Ducky's own few processes for their command lines. Every probe that touches the disk
//! or other processes runs on a blocking thread under its own limit: one that hangs (a
//! dead network drive) leaves its part empty and the report still answers.
//!
//! What the Python backend measured of itself is measured of this one:
//! - `core_wait_ms`: how late the async runtime wakes a task that sleeps 10 ms;
//! - `inflight`: the requests this backend is answering now ([`guard::in_flight`]).
//!
//! ponytail: `hot_threads` stays empty (no per-thread CPU here: a busy backend shows in
//! `core_wait_ms`); add thread times when a hot thread needs a name.
//! ponytail: the parts other services own are empty until those services are answered
//! here: sandbox programs, terminals, running agents, agent hosts, agents in the window,
//! the graphics card, the window's last hour, auto-heal, the perf report, and when a
//! plugin was last called. With no running agents known, Ducky is never called idle, and
//! the self-care actions that belong to those services answer that they are not here.

mod findings;

use std::collections::{HashMap, HashSet, VecDeque};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde_json::{json, Map, Value};

use super::Api;
use crate::guard;
use crate::proc::{self, Proc, Table};
use crate::store::pyjson::{float, int, pystr, truthy};
use findings::{plural, round_to, IDLE_PROCESS_MB, PARTS, PLUGIN_IDLE_MIN};

type Fields = Map<String, Value>;
/// A process's command line and program file.
type Asked = (Vec<String>, String);

const MB: f64 = 1024.0 * 1024.0;
const GB: f64 = 1024.0 * MB;
const DUCKY_NAME: &str = "uefn-ducky";
/// This backend and the launcher that starts it.
const OWN_NAMES: [&str; 2] = ["ducky-launcher", "ducky-backend"];
const UEFN_NAMES: [&str; 2] = ["unrealeditorfortnite", "unrealeditor"];
const SHELLS: [&str; 7] = ["bash.exe", "sh.exe", "powershell.exe", "pwsh.exe", "cmd.exe", "wsl.exe", "openconsole.exe"];
const AGENT_CLIS: [&str; 4] = ["claude", "codex", "cursor-agent", "gemini"];
pub const ACTIONS: [&str; 8] = ["stop_sandbox", "close_idle_terminals", "stop_orphan_bridges", "trim_caches", "prune_sandboxes", "collect_garbage", "free_ducky_memory", "free_uefn_memory"];
/// The actions whose service is still answered by the Python backend.
const NOT_HERE: [&str; 4] = ["stop_sandbox", "close_idle_terminals", "prune_sandboxes", "free_uefn_memory"];
/// % of a core over the sample: a worker this busy is in a call.
const WORKER_BUSY_CPU: f64 = 0.5;
/// The process look: a one second CPU sample and a few command lines.
const LOOK_LIMIT: Duration = Duration::from_secs(8);
/// Folder sizes and the small files read for a report.
const FILES_LIMIT: Duration = Duration::from_secs(3);
/// Folder sizes are walked at most this often: tens of thousands of chat files take
/// seconds on a busy disk.
const STORAGE_TTL: Duration = Duration::from_secs(600);
const STORAGE_FOLDERS: [&str; 6] = ["chats", "sandboxes", "cache", "perf", "logs", "tools"];
const FOLDER_CAP_FILES: usize = 60_000;
/// The meter runs this long after the last report asked for it.
const CORE_WAIT_KEEP: Duration = Duration::from_secs(180);
const CORE_WAIT_SAMPLES: usize = 6000;

fn mb(bytes: f64) -> f64 {
    round_to(bytes / MB, 1)
}

fn now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|since| since.as_secs_f64()).unwrap_or_default()
}

fn ducky_named(proc: &Proc) -> bool {
    proc.name.to_lowercase().starts_with(DUCKY_NAME)
}

fn own_named(name: &str) -> bool {
    OWN_NAMES.iter().any(|own| name.starts_with(own))
}

/// A blocking probe on its own thread under its own limit: one that hangs answers
/// nothing (`T::default()`) and holds the report no longer.
async fn probe<T: Default + Send + 'static>(limit: Duration, work: impl FnOnce() -> T + Send + 'static) -> T {
    tokio::time::timeout(limit, tokio::task::spawn_blocking(work)).await.ok().and_then(Result::ok).unwrap_or_default()
}

// ----------------------------------------------------------------------- the core-wait meter

/// How long work waits for the runtime: lateness of a 10 ms sleep beyond the idle
/// baseline (the median of the first 200 wakes), last ~60 s.
struct Meter {
    until: Option<Instant>,
    running: bool,
    baseline: Option<f64>,
    samples: VecDeque<f64>,
}

static METER: Mutex<Meter> = Mutex::new(Meter { until: None, running: false, baseline: None, samples: VecDeque::new() });

/// Start the meter, or keep it running: each report moves its end out. Left running
/// for good it would wake 100 times a second in an idle app.
fn start_core_wait() {
    let Ok(mut meter) = METER.lock() else { return };
    meter.until = Some(Instant::now() + CORE_WAIT_KEEP);
    if !meter.running {
        meter.running = true;
        tokio::spawn(core_wait_loop());
    }
}

async fn core_wait_loop() {
    let mut first: Vec<f64> = Vec::new();
    loop {
        let started = Instant::now();
        tokio::time::sleep(Duration::from_millis(10)).await;
        let late = (started.elapsed().as_secs_f64() - 0.010) * 1000.0;
        let Ok(mut meter) = METER.lock() else { return };
        if meter.until.is_none_or(|until| Instant::now() >= until) {
            meter.running = false;
            return;
        }
        match meter.baseline {
            Some(baseline) => {
                if meter.samples.len() == CORE_WAIT_SAMPLES {
                    meter.samples.pop_front();
                }
                meter.samples.push_back((late - baseline).max(0.0));
            }
            None => {
                first.push(late);
                if first.len() >= 200 {
                    first.sort_by(f64::total_cmp);
                    meter.baseline = Some((first[99] + first[100]) / 2.0);
                }
            }
        }
    }
}

/// p50 / p95 / max lateness in ms over `samples`.
fn core_wait_stats(samples: &[f64]) -> Value {
    let mut vals = samples.to_vec();
    vals.sort_by(f64::total_cmp);
    let Some(max) = vals.last() else { return json!({"p50": null, "p95": null, "max": null, "samples": 0}) };
    let pct = |q: f64| round_to(vals[((q * vals.len() as f64).ceil() as usize).saturating_sub(1)], 1);
    json!({"p50": pct(0.50), "p95": pct(0.95), "max": round_to(*max, 1), "samples": vals.len()})
}

/// The busiest requests in flight, most calls first, then oldest (`inflight.top`), and
/// how many there are in all.
fn inflight() -> (Vec<Value>, usize) {
    let live = guard::in_flight();
    let mut rows: Vec<(String, i64, f64)> = Vec::new();
    for (path, age) in &live {
        let method = path.rsplit('/').next().unwrap_or(path);
        let age = round_to(age.as_secs_f64(), 1);
        match rows.iter_mut().find(|row| row.0 == method) {
            Some(row) => *row = (row.0.clone(), row.1 + 1, row.2.max(age)),
            None => rows.push((method.to_string(), 1, age)),
        }
    }
    rows.sort_by(|a, b| b.1.cmp(&a.1).then(b.2.total_cmp(&a.2)));
    let top = rows.into_iter().take(5).map(|(method, count, oldest)| json!({"source": "rust", "method": method, "count": count, "oldest_s": oldest}));
    (top.collect(), live.len())
}

// ----------------------------------------------------------------------- whose process is it

/// What a Ducky process is, from its command line.
fn role(cmdline: &[String]) -> &'static str {
    let args = cmdline.iter().skip(1).map(String::as_str).collect::<Vec<_>>().join(" ").to_lowercase();
    let spaced = format!(" {args}");
    if args.contains("--shared-daemon") {
        "shared tool server"
    } else if args.contains("--adapter") {
        "IDE adapter"
    } else if spaced.contains(" agent-host") {
        "agent host"
    } else if spaced.contains(" bridge") {
        "tool server (bridge)"
    } else if args.contains("--multiprocessing-fork") {
        // A process another Ducky process started for work of its own.
        "worker"
    } else {
        "app"
    }
}

fn word(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

/// The first `…<before><name><after>…` in `text`, whatever the case of `before` and
/// `after`: `name` is the longest run of `keep` characters, and is never empty. With
/// `slashes`, more `/` may follow `before`.
fn between(text: &[char], before: &str, slashes: bool, keep: impl Fn(char) -> bool, after: &str) -> Option<String> {
    let at = |from: usize, lit: &str| lit.chars().enumerate().all(|(i, c)| text.get(from + i).is_some_and(|have| have.eq_ignore_ascii_case(&c)));
    (0..text.len()).find_map(|start| {
        if !at(start, before) {
            return None;
        }
        let mut from = start + before.chars().count();
        if slashes {
            from += text[from..].iter().take_while(|c| **c == '/').count();
        }
        let length = text[from..].iter().take_while(|c| keep(**c)).count();
        (length > 0 && at(from + length, after)).then(|| text[from..from + length].iter().collect())
    })
}

/// The plugin whose folder a command line runs in ('forge' from `…\uefn_plugins\forge\…`
/// or `…\forge-engine\…`), or '' when it runs in no plugin folder.
fn plugin_home(cmd: &[String]) -> String {
    let text: Vec<char> = cmd.join(" ").replace('\\', "/").chars().collect();
    let hit = between(&text, "/uefn_plugins/", true, |c| c != '/' && c != '"' && !c.is_whitespace(), "/")
        .or_else(|| between(&text, "uefn-plugin-", false, |c| word(c) || c == '-', ""))
        .or_else(|| between(&text, "/", false, word, "-engine/"))
        .unwrap_or_default();
    let prefix = "uefn-plugin-";
    if hit.get(..prefix.len()).is_some_and(|head| head.eq_ignore_ascii_case(prefix)) { hit[prefix.len()..].to_string() } else { hit }
}

/// The plugin a worker belongs to, from its command line.
fn plugin_of(cmd: &[String]) -> String {
    let home = plugin_home(cmd);
    if !home.is_empty() {
        return home;
    }
    let text: Vec<char> = cmd.join(" ").replace('\\', "/").chars().collect();
    if let Some(hit) = between(&text, "/plugins/", false, |c| c != '/' && !c.is_whitespace(), "/") {
        return hit;
    }
    let script = cmd.iter().rev().find(|arg| arg.to_lowercase().ends_with(".py"));
    let folder = script.and_then(|script| Some(Path::new(script).parent()?.file_name()?.to_string_lossy().into_owned()));
    script.map_or("plugin".into(), |_| folder.unwrap_or_default())
}

/// What a footprint is told besides the process table.
#[derive(Default)]
struct Told {
    /// pid -> (role, program file), already read.
    known: HashMap<i64, (String, String)>,
    host_pids: HashSet<i64>,
    idle: bool,
    now: f64,
    /// Plugin id -> seconds since 1970 of its last tool call.
    last_used: HashMap<String, f64>,
    /// pid -> plugin, for the process a plugin runs its own work in.
    own_workers: HashMap<i64, String>,
}

/// Everything Ducky holds (`ducky_footprint`): `me`'s process tree plus Ducky's other
/// processes (shared tool servers), in parts (MB of private memory). Command lines are
/// asked only for Python children (plugin workers).
fn footprint(procs: &Table, me: i64, told: &Told, cmdline: &dyn Fn(i64) -> Asked) -> Value {
    let tree = proc::tree(procs, me);
    let mine: HashSet<i64> = tree.iter().copied().collect();
    let others = procs.values().filter(|proc| ducky_named(proc) && !mine.contains(&proc.pid)).map(|proc| proc.pid);
    let pids: Vec<i64> = tree.iter().copied().chain(others).collect();
    let mut parts: Vec<(&str, f64)> = PARTS.iter().map(|(key, _)| (*key, 0.0)).collect();
    let (mut servers, mut workers, mut big, mut conhosts) = (Vec::new(), Vec::new(), Vec::new(), 0);
    for pid in pids {
        let proc = &procs[&pid];
        let (name, size) = (proc.name.to_lowercase(), mb(proc.private as f64));
        let (role, exe) = told.known.get(&pid).map(|(role, exe)| (role.as_str(), exe.as_str())).unwrap_or_default();
        let python = name.starts_with("python");
        let part = if pid == me || own_named(&name) {
            "app"
        } else if told.host_pids.contains(&pid) || role == "agent host" {
            "agent_hosts"
        } else if told.own_workers.contains_key(&pid) && ducky_named(proc) && ["", "worker"].contains(&role) {
            // The plugin's own process: it closes itself when idle, so it is never a
            // leftover to stop (idle_min stays null).
            workers.push(json!({"pid": pid, "plugin": told.own_workers[&pid], "mb": size, "idle_min": null}));
            "plugin_workers"
        } else if ducky_named(proc) {
            if role == "shared tool server" {
                let version = Path::new(exe).parent().and_then(Path::file_name).map(|name| name.to_string_lossy().into_owned()).unwrap_or_default();
                servers.push(json!({"pid": pid, "exe": exe, "version": version, "mb": size}));
            }
            "tool_servers"
        } else if name.contains("webview2") {
            "webview2"
        } else if name == "conhost.exe" {
            conhosts += 1;
            "conhosts"
        } else if let Some(cmd) = python.then(|| cmdline(pid).0).filter(|cmd| cmd.iter().any(|arg| arg.to_lowercase().contains("worker")) || !plugin_home(cmd).is_empty()) {
            let plugin = plugin_of(&cmd);
            let joined = cmd.join(" ").to_lowercase();
            let used = told.last_used.iter().filter(|(key, _)| joined.contains(&key.to_lowercase()) || key.to_lowercase().contains(&plugin.to_lowercase())).map(|(_, at)| *at).fold(0.0, f64::max);
            let idle_min = (used != 0.0).then(|| round_to((told.now - used) / 60.0, 0) as i64);
            workers.push(json!({"pid": pid, "plugin": plugin, "mb": size, "idle_min": idle_min}));
            "plugin_workers"
        } else if SHELLS.contains(&name.as_str()) {
            "terminals"
        } else {
            "agents"
        };
        if let Some(slot) = parts.iter_mut().find(|slot| slot.0 == part) {
            slot.1 = round_to(slot.1 + size, 1);
        }
        if told.idle && size > IDLE_PROCESS_MB {
            big.push(json!({"pid": pid, "name": proc.name, "part": part, "private_mb": size}));
        }
    }
    big.sort_by(|a, b| b["private_mb"].as_f64().unwrap_or(0.0).total_cmp(&a["private_mb"].as_f64().unwrap_or(0.0)));
    let total = round_to(parts.iter().map(|part| part.1).sum(), 1);
    let parts: Fields = parts.into_iter().map(|(key, size)| (key.to_string(), size.into())).collect();
    json!({"total_mb": total, "parts": parts, "idle": told.idle, "conhosts": conhosts, "shared_tool_servers": servers, "plugin_workers": workers, "big_idle_processes": big})
}

/// Ducky tool servers whose IDE or agent is gone (never the app or a shared tool server,
/// never a process outside Ducky): `[{pid, role, mb}]`, and each one's name.
fn orphan_bridges(procs: &Table, me: i64, cmdline: &dyn Fn(i64) -> Asked) -> Vec<(Value, String)> {
    let mut out = Vec::new();
    for proc in procs.values().filter(|proc| proc.pid != me && ducky_named(proc) && proc::orphan(procs, proc.pid)) {
        let role = role(&cmdline(proc.pid).0);
        if !["app", "shared tool server"].contains(&role) {
            out.push((json!({"pid": proc.pid, "role": role, "mb": mb(proc.private as f64)}), proc.name.clone()));
        }
    }
    out
}

/// Plugin worker processes Ducky may stop (`idle_plugin_workers`): descendants of a Ducky
/// process whose command line runs in a plugin folder, never an agent CLI, terminal,
/// console host or WebView2. Idle means its plugin had no tool call in PLUGIN_IDLE_MIN
/// (or, with no call on record, the worker is that old) and its whole tree used no CPU
/// over the sample. `[{pid, plugin, tree, mb}]`, one row per top-most worker.
fn idle_plugin_workers(procs: &Table, cpu: &HashMap<i64, f64>, me: i64, now: f64, last_used: &HashMap<String, f64>, cmdline: &dyn Fn(i64) -> Asked) -> Vec<Value> {
    let mut under: Vec<i64> = procs.values().filter(|proc| proc.pid == me || ducky_named(proc)).flat_map(|root| proc::tree(procs, root.pid).into_iter().skip(1)).collect();
    under.sort_unstable();
    under.dedup();
    let mut workers: Vec<(i64, String)> = Vec::new();
    for pid in under {
        let proc = &procs[&pid];
        let name = proc.name.to_lowercase();
        let cli = name.strip_suffix(".exe").unwrap_or(&name);
        if ducky_named(proc) || SHELLS.contains(&name.as_str()) || name == "conhost.exe" || name.contains("webview2") || AGENT_CLIS.iter().any(|agent| cli.starts_with(agent)) {
            continue;
        }
        let plugin = plugin_home(&cmdline(pid).0);
        if !plugin.is_empty() {
            workers.push((pid, plugin));
        }
    }
    let mut out = Vec::new();
    for (pid, plugin) in &workers {
        // A worker under a worker is stopped with it.
        if workers.iter().any(|(parent, _)| *parent == procs[pid].ppid) {
            continue;
        }
        let tree = proc::tree(procs, *pid);
        if tree.iter().any(|pid| cpu.get(pid).copied().unwrap_or(0.0) > WORKER_BUSY_CPU) {
            continue;
        }
        let wanted = plugin.to_lowercase();
        let called = last_used.iter().filter(|(key, _)| key.to_lowercase().contains(&wanted)).map(|(_, at)| *at).fold(0.0, f64::max);
        // A worker younger than its last call counts from its start.
        let started = procs[pid].started;
        let quiet_since = called.max(if started != 0.0 { started } else { now });
        if now - quiet_since < (PLUGIN_IDLE_MIN * 60) as f64 {
            continue;
        }
        let size = round_to(tree.iter().map(|pid| mb(procs[pid].private as f64)).sum(), 1);
        out.push(json!({"pid": pid, "plugin": plugin, "tree": tree, "mb": size}));
    }
    out
}

// ----------------------------------------------------------------------- files

/// A folder's size in bytes (`sandbox.paths.dir_size`): links are counted once, never
/// followed, and the walk stops after `cap` files.
/// ponytail: past the cap, which files were counted depends on the walk's order.
#[allow(clippy::disallowed_methods)] // only ever run by spawn_blocking
fn dir_size(root: &Path, cap: usize) -> u64 {
    let (mut total, mut seen, mut folders) = (0u64, 0usize, vec![root.to_path_buf()]);
    while let Some(folder) = folders.pop() {
        for entry in std::fs::read_dir(&folder).into_iter().flatten().flatten() {
            let Ok(kind) = entry.file_type() else { continue };
            if kind.is_dir() {
                folders.push(entry.path());
            } else if !(kind.is_symlink() && entry.path().is_dir()) {
                seen += 1;
                if seen > cap {
                    return total;
                }
                total += entry.metadata().map(|meta| meta.len()).unwrap_or(0);
            }
        }
    }
    total
}

fn folder_sizes(base: &Path) -> Fields {
    let size = |name: &str| mb(dir_size(&base.join(name), FOLDER_CAP_FILES) as f64);
    STORAGE_FOLDERS.iter().map(|name| (format!("{name}_mb"), size(name).into())).collect()
}

/// When the folders were last walked, and their sizes.
static STORAGE: Mutex<Option<(Instant, Fields)>> = Mutex::new(None);
/// Checks that arrive together share one walk.
static WALK: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

/// Folder sizes: walked at most every ten minutes, and waited for [`FILES_LIMIT`] at
/// most. A walk that takes longer goes on by itself and shows in the next report.
async fn folders(base: PathBuf) -> Fields {
    let walk = tokio::spawn(async move {
        let _one = WALK.lock().await;
        let fresh = STORAGE.lock().is_ok_and(|seen| seen.as_ref().is_some_and(|(at, _)| at.elapsed() < STORAGE_TTL));
        if !fresh {
            if let Ok(sizes) = tokio::task::spawn_blocking(move || folder_sizes(&base)).await {
                if let Ok(mut seen) = STORAGE.lock() {
                    *seen = Some((Instant::now(), sizes));
                }
            }
        }
    });
    let _ = tokio::time::timeout(FILES_LIMIT, walk).await;
    STORAGE.lock().ok().and_then(|seen| seen.as_ref().map(|(_, sizes)| sizes.clone())).unwrap_or_default()
}

/// Agent starts that loaded a tool server of their own because they could not join
/// Ducky's (`tool_fallbacks`): `shared_mcp_fallbacks.json` counts them and keeps the
/// latest with their reason.
fn tool_fallbacks(data: &Value, now: f64) -> Value {
    if !data.is_object() {
        return json!({"count": 0, "last_hour": 0, "reason": ""});
    }
    let at = |row: &Value| float(&row["at"]).unwrap_or(0.0);
    let mut rows: Vec<Value> = data["recent"].as_array().into_iter().flatten().filter(|row| row.is_object()).cloned().collect();
    if rows.is_empty() {
        // Written before the latest ones were kept.
        rows.push(json!({"at": data["last_at"], "reason": data["reason"]}));
    }
    let hour: Vec<&Value> = rows.iter().filter(|row| now - at(row) < 3600.0).collect();
    let count = match &data["count"] {
        count if !truthy(count) => 0,
        Value::String(text) => text.trim().parse().unwrap_or(rows.len() as i64),
        count @ (Value::Number(_) | Value::Bool(_)) => int(count),
        _ => rows.len() as i64,
    };
    let last = hour.last().copied().or(rows.last()).map(|row| &row["reason"]).filter(|reason| truthy(reason)).map(pystr).unwrap_or_default();
    json!({"count": count, "last_hour": hour.len(), "reason": last})
}

/// What a report reads from small files: the database's sizes, the fallbacks file, how
/// many chats have a sandbox.
#[allow(clippy::disallowed_methods)] // only ever run by spawn_blocking
fn small_files(base: &Path) -> (Fields, Value, usize) {
    let mut sizes = Fields::new();
    for (file, key) in [("ducky.db", "ducky_db_mb"), ("ducky.db-wal", "ducky_db_wal_mb")] {
        if let Ok(meta) = std::fs::metadata(base.join(file)) {
            sizes.insert(key.into(), mb(meta.len() as f64).into());
        }
    }
    let falls = std::fs::read_to_string(base.join("shared_mcp_fallbacks.json")).ok().and_then(|text| serde_json::from_str::<Value>(&text).ok()).unwrap_or_default();
    // A sandbox is a chat's folder with its marker, or its own layout (`is_chat_sandbox`).
    let sandbox = |folder: &Path| folder.join(".last_used").is_file() || ["work", "home", "logs"].iter().all(|sub| folder.join(sub).is_dir());
    let named = |name: &str| (1..=80).contains(&name.len()) && name.bytes().all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b));
    let sandboxes = std::fs::read_dir(base.join("sandboxes")).into_iter().flatten().flatten();
    let count = sandboxes.filter(|entry| entry.file_name().to_str().is_some_and(named) && entry.path().is_dir() && sandbox(&entry.path())).count();
    (sizes, tool_fallbacks(&falls, now()), count)
}

/// pid -> plugin, for the process a plugin runs its own work in: each one writes its
/// pid to `plugin_local/<plugin>/worker-*.json`.
#[allow(clippy::disallowed_methods)] // only ever run by spawn_blocking
fn own_plugin_workers(base: &Path) -> HashMap<i64, String> {
    let mut out = HashMap::new();
    for plugin in std::fs::read_dir(base.join("plugin_local")).into_iter().flatten().flatten() {
        for file in std::fs::read_dir(plugin.path()).into_iter().flatten().flatten() {
            let name = file.file_name().to_string_lossy().into_owned();
            if !(name.starts_with("worker-") && name.ends_with(".json")) {
                continue;
            }
            let note = std::fs::read_to_string(file.path()).ok().and_then(|text| serde_json::from_str::<Value>(&text).ok());
            if let Some(pid) = note.as_ref().and_then(|note| note.get("pid")).filter(|pid| pid.is_number() || pid.is_string()) {
                out.insert(int(pid), plugin.file_name().to_string_lossy().into_owned());
            }
        }
    }
    out
}

// ----------------------------------------------------------------------- the report

/// The Ducky process everything hangs under: this backend's topmost ancestor that is
/// Ducky's own (the launcher), or this process when something else started it.
fn root(procs: &Table, own: i64) -> i64 {
    let mut pid = own;
    for _ in 0..8 {
        let Some(proc) = procs.get(&pid) else { break };
        let parent = procs.get(&proc.ppid).filter(|parent| parent.pid != pid && parent.started <= proc.started);
        match parent.filter(|parent| ducky_named(parent) || own_named(&parent.name.to_lowercase())) {
            Some(parent) => pid = parent.pid,
            None => break,
        }
    }
    pid
}

/// The parts of a report that come from the process table: `app`, `helpers`,
/// `other_ducky_processes`, `uefn`, `pc` and `ducky_total`.
fn process_parts(procs: &Table, cpu: &HashMap<i64, f64>, memory: (u64, u64), own_workers: HashMap<i64, String>, cmdline: &dyn Fn(i64) -> Asked) -> Fields {
    let me_pid = root(procs, i64::from(std::process::id()));
    let fallback = Proc { pid: me_pid, ..Proc::default() };
    let me = procs.get(&me_pid).unwrap_or(&fallback);
    let used = |pid: i64| cpu.get(&pid).copied().unwrap_or(0.0);
    let info = |proc: &Proc| json!({"pid": proc.pid, "name": proc.name, "memory_mb": mb(proc.rss as f64), "private_mb": mb(proc.private as f64), "cpu_percent": used(proc.pid)});
    let uptime = (me.started != 0.0).then(|| round_to((now() - me.started) / 60.0, 1));
    let app = json!({
        "pid": me_pid,
        "memory_mb": mb(me.rss as f64),
        "private_mb": mb(me.private as f64),
        "threads": me.threads,
        "handles": (me.handles != 0).then_some(me.handles),
        "cpu_percent": used(me_pid),
        "uptime_min": uptime,
    });
    // name -> (count, memory_mb, cpu_percent), in the order the names came.
    let mut helpers: Vec<(String, i64, f64, f64)> = Vec::new();
    for pid in proc::tree(procs, me_pid).into_iter().skip(1) {
        let proc = &procs[&pid];
        let (size, busy) = (mb(proc.rss as f64), used(pid));
        match helpers.iter_mut().find(|slot| slot.0 == proc.name) {
            Some(slot) => *slot = (slot.0.clone(), slot.1 + 1, round_to(slot.2 + size, 1), round_to(slot.3 + busy, 1)),
            None => helpers.push((proc.name.clone(), 1, round_to(size, 1), round_to(busy, 1))),
        }
    }
    helpers.sort_by(|a, b| b.2.total_cmp(&a.2));
    let helpers: Vec<Value> = helpers.into_iter().take(12).map(|(name, count, size, busy)| json!({"name": name, "count": count, "memory_mb": size, "cpu_percent": busy})).collect();

    let mut ducky: Vec<&Proc> = procs.values().filter(|proc| ducky_named(proc) && proc.pid != me_pid).collect();
    ducky.sort_by_key(|proc| std::cmp::Reverse(proc.rss));
    let mut told = Told { now: now(), own_workers, ..Told::default() };
    let mut ducky_rows = Vec::new();
    for proc in ducky.into_iter().take(20) {
        let (cmd, exe) = cmdline(proc.pid);
        let role = role(&cmd);
        let orphan = !["app", "shared tool server", "agent host"].contains(&role) && proc::orphan(procs, proc.pid);
        let mut row = info(proc);
        row["role"] = role.into();
        row["exe"] = exe.clone().into();
        row["orphan"] = orphan.into();
        ducky_rows.push(row);
        told.known.insert(proc.pid, (role.to_string(), exe));
    }
    let total = footprint(procs, me_pid, &told, cmdline);

    let mut merged: Vec<(String, f64)> = Vec::new();
    for proc in procs.values().filter(|proc| proc.pid != 0) {
        let name = if proc.name.is_empty() { "?" } else { &proc.name };
        match merged.iter_mut().find(|slot| slot.0 == name) {
            Some(slot) => slot.1 += proc.rss as f64,
            None => merged.push((name.to_string(), proc.rss as f64)),
        }
    }
    merged.sort_by(|a, b| b.1.total_cmp(&a.1));
    let top_apps: Vec<Value> = merged.into_iter().take(8).map(|(name, bytes)| json!({"name": name, "memory_mb": mb(bytes)})).collect();
    // pid 0 is the idle process.
    let busy: f64 = cpu.iter().filter(|(pid, _)| **pid != 0).map(|(_, used)| *used).sum();
    let ncpu = std::thread::available_parallelism().map_or(1, usize::from);
    let (total_bytes, free_bytes) = (memory.0 as f64, memory.1 as f64);
    let used_percent = if total_bytes > 0.0 { round_to((total_bytes - free_bytes) / total_bytes * 100.0, 1) } else { 0.0 };
    let pc = json!({
        "memory_used_percent": used_percent,
        "memory_total_gb": round_to(total_bytes / GB, 1),
        "memory_free_gb": round_to(free_bytes / GB, 1),
        "cpu_percent": round_to((busy / ncpu as f64).min(100.0), 1),
        "cpus": ncpu,
        "processes": procs.len(),
        "top_apps": top_apps,
    });
    let mut uefn: Vec<&Proc> = procs.values().filter(|proc| UEFN_NAMES.iter().any(|name| proc.name.to_lowercase().starts_with(name))).collect();
    uefn.sort_by_key(|proc| std::cmp::Reverse(proc.private));
    let uefn: Vec<Value> = uefn.into_iter().take(3).map(info).collect();
    let parts = [("app", app), ("helpers", helpers.into()), ("other_ducky_processes", ducky_rows.into()), ("uefn", uefn.into()), ("pc", pc), ("ducky_total", total)];
    parts.into_iter().map(|(key, value)| (key.to_string(), value)).collect()
}

/// `ducky_health` from an agent's tool server, and the Settings health card.
pub async fn ducky_health_snapshot(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    start_core_wait();
    let base = api.repos.data_dir().to_path_buf();
    let (plugins, files) = (base.clone(), base.clone());
    let look = probe(LOOK_LIMIT, move || {
        let (procs, cpu) = proc::sample(Duration::from_secs(1));
        Some(process_parts(&procs, &cpu, proc::memory(), own_plugin_workers(&plugins), &proc::cmdline))
    });
    let small = probe(FILES_LIMIT, move || Some(small_files(&files)));
    let (look, small, mut storage) = tokio::join!(look, small, folders(base));
    // A look that hung: the same parts, of an empty table.
    let mut parts = look.unwrap_or_else(|| process_parts(&Table::new(), &HashMap::new(), (0, 0), HashMap::new(), &|_| Asked::default()));
    let (sizes, falls, sandboxes) = small.unwrap_or_else(|| (Fields::new(), tool_fallbacks(&Value::Null, 0.0), 0));
    storage.extend(sizes);
    let (calls, calls_total) = inflight();
    let waits: Vec<f64> = METER.lock().map(|meter| meter.samples.iter().copied().collect()).unwrap_or_default();
    if let Some(app) = parts.get_mut("app").and_then(Value::as_object_mut) {
        app.extend([("hot_threads".to_string(), json!([])), ("core_wait_ms".to_string(), core_wait_stats(&waits)), ("inflight".to_string(), calls.into()), ("inflight_total".to_string(), calls_total.into())]);
    }
    let mut take = |key: &str| parts.remove(key).unwrap_or_default();
    let mut out = json!({
        "ok": true,
        "app": take("app"),
        "helpers": take("helpers"),
        "other_ducky_processes": take("other_ducky_processes"),
        "uefn": take("uefn"),
        "pc": take("pc"),
        "sandboxes": {"count": sandboxes, "programs": [], "programs_memory_mb": 0.0},
        "terminals": {"open": 0, "agent_owned": 0},
        "storage": storage,
        "running_agents": {"count": 0, "agents": []},
        "agent_hosts": [],
        "ducky_total": take("ducky_total"),
        "gpu": null,
        "agents_in_window": [],
        "ui_last_hour": {},
        "auto_heal": [],
        "tool_fallbacks": falls,
    });
    if args.get("detail").is_some_and(truthy) {
        out["perf"] = json!({"error": "The perf report is not read by this backend yet."});
    }
    let found = findings::findings(&out, now());
    out["findings"] = found.into();
    Ok(out)
}

// ----------------------------------------------------------------------- self-care

/// Delete the downloaded-video cache; MB freed on disk.
#[allow(clippy::disallowed_methods)] // only ever run by spawn_blocking
fn trim_caches(base: &Path) -> f64 {
    let cache = base.join("cache").join("web_video");
    let _ = std::fs::create_dir_all(&cache);
    let mut freed = 0u64;
    for entry in std::fs::read_dir(&cache).into_iter().flatten().flatten() {
        if let Some(meta) = std::fs::metadata(entry.path()).ok().filter(|meta| meta.is_file()) {
            freed += meta.len();
            let _ = std::fs::remove_file(entry.path());
        }
    }
    mb(freed as f64)
}

/// `'Forge Engine'` from `'forge-engine'` (`str.title`).
fn title(name: &str) -> String {
    let mut out = String::new();
    let mut inside = false;
    for c in name.replace('-', " ").chars() {
        if c.is_alphabetic() {
            out.extend(if inside { c.to_lowercase().collect::<Vec<_>>() } else { c.to_uppercase().collect() });
        } else {
            out.push(c);
        }
        inside = c.is_alphabetic();
    }
    out
}

/// `['4 idle Forge workers']`, one per plugin.
fn workers_text(rows: &[Value]) -> Vec<String> {
    let mut counts: Vec<(String, i64)> = Vec::new();
    for row in rows {
        let plugin = pystr(&row["plugin"]);
        match counts.iter_mut().find(|slot| slot.0 == plugin) {
            Some(slot) => slot.1 += 1,
            None => counts.push((plugin, 1)),
        }
    }
    counts.iter().map(|(plugin, n)| plural(*n, &format!("idle {} worker", title(plugin)), &format!("idle {} workers", title(plugin)))).collect()
}

fn own_rss() -> f64 {
    proc::table().get(&i64::from(std::process::id())).map_or(0.0, |me| me.rss as f64)
}

fn stop_orphan_bridges() -> Vec<Value> {
    let procs = proc::table();
    let found = orphan_bridges(&procs, i64::from(std::process::id()), &proc::cmdline);
    found.into_iter().filter(|(row, name)| proc::terminate(int(&row["pid"]), name)).map(|(row, _)| row).collect()
}

/// Free what Ducky itself holds and no longer uses (`free_ducky_memory`): orphaned
/// bridges and tool servers, idle plugin workers, the video cache. Never a running
/// turn, UEFN, or anything outside Ducky. `{freed_mb, did: [..]}`.
fn free_ducky_memory(base: &Path) -> Fields {
    let before = own_rss();
    let mut did: Vec<String> = Vec::new();
    let bridges = stop_orphan_bridges();
    if !bridges.is_empty() {
        did.push(plural(bridges.len() as i64, "leftover bridge", "leftover bridges"));
    }
    let (procs, cpu) = proc::sample(Duration::from_secs(1));
    let idle = idle_plugin_workers(&procs, &cpu, i64::from(std::process::id()), now(), &HashMap::new(), &proc::cmdline);
    let workers: Vec<Value> = idle.into_iter().filter(|row| procs.get(&int(&row["pid"])).is_some_and(|proc| proc::terminate_tree(proc.pid, &proc.name))).collect();
    did.extend(workers_text(&workers));
    trim_caches(base);
    let stopped: f64 = bridges.iter().chain(&workers).map(|row| row["mb"].as_f64().unwrap_or(0.0)).sum();
    let freed = stopped + if before > 0.0 { mb(before - own_rss()).max(0.0) } else { 0.0 };
    Fields::from_iter([("freed_mb".to_string(), round_to(freed, 1).into()), ("did".to_string(), did.into())])
}

/// One self-care action, on a blocking thread.
fn care(base: &Path, action: &str) -> Value {
    if NOT_HERE.contains(&action) {
        return json!({"ok": false, "error": format!("{action} is not answered by this backend yet.")});
    }
    let before = own_rss();
    let mut done = Fields::from_iter([("ok".to_string(), true.into()), ("action".to_string(), action.into())]);
    match action {
        "stop_orphan_bridges" => {
            let stopped: Vec<Value> = stop_orphan_bridges().iter().map(|row| json!({"pid": row["pid"], "role": row["role"]})).collect();
            done.insert("stopped".into(), stopped.into());
        }
        "trim_caches" => {
            done.insert("freed_mb".into(), trim_caches(base).into());
        }
        // Nothing here waits for a collector: memory is given back when its owner goes.
        "collect_garbage" => {
            done.insert("objects_freed".into(), 0.into());
        }
        "free_ducky_memory" => done.extend(free_ducky_memory(base)),
        _ => return json!({"ok": false, "error": format!("action must be one of: {}.", ACTIONS.join(", "))}),
    }
    done.insert("app_memory_mb".into(), json!({"before": mb(before), "after": mb(own_rss())}));
    done.into()
}

/// `ducky_self_care` from an agent's tool server.
pub async fn ducky_self_care_run(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let action = args.get("action").filter(|action| truthy(action)).map(pystr).unwrap_or_default().trim().to_string();
    let base = api.repos.data_dir().to_path_buf();
    tokio::task::spawn_blocking(move || care(&base, &action)).await.map_err(|stopped| stopped.to_string())
}

// ----------------------------------------------------------------------- the tests' way in

fn table_of(rows: &Value) -> Table {
    let row = |p: &Value| Proc {
        pid: int(&p["pid"]),
        ppid: int(&p["ppid"]),
        name: pystr(&p["name"]),
        rss: p["rss"].as_u64().unwrap_or(0),
        private: p["private"].as_u64().unwrap_or(0),
        threads: int(&p["threads"]) as u32,
        handles: int(&p["handles"]) as u32,
        cpu_s: p["cpu_s"].as_f64().unwrap_or(0.0),
        started: p["started"].as_f64().unwrap_or(0.0),
    };
    rows.as_array().into_iter().flatten().map(|p| (int(&p["pid"]), row(p))).collect()
}

/// `{"<pid>": value}` by pid.
fn by_pid<T>(map: &Value, read: impl Fn(&Value) -> T) -> HashMap<i64, T> {
    map.as_object().into_iter().flatten().filter_map(|(pid, value)| Some((pid.parse().ok()?, read(value)))).collect()
}

fn strings(value: &Value) -> Vec<String> {
    value.as_array().into_iter().flatten().map(pystr).collect()
}

/// The parity tests' way in (`/__rs/health/<what>`): the pure parts, on the process
/// table and command lines a test gives. Nothing here looks at this PC or stops anything.
pub fn check(what: &str, body: &Value) -> Value {
    let procs = table_of(&body["procs"]);
    let cmds = by_pid(&body["cmdlines"], strings);
    let cmdline = |pid: i64| (cmds.get(&pid).cloned().unwrap_or_default(), String::new());
    let seconds = |map: &Value| -> HashMap<String, f64> { map.as_object().into_iter().flatten().map(|(key, at)| (key.clone(), at.as_f64().unwrap_or(0.0))).collect() };
    let (me, at) = (int(&body["me"]), body["now"].as_f64().unwrap_or(0.0));
    match what {
        "findings" => findings::findings(&body["snap"], at).into(),
        "core_wait" => core_wait_stats(&body["samples"].as_array().into_iter().flatten().filter_map(Value::as_f64).collect::<Vec<_>>()),
        "tool_fallbacks" => tool_fallbacks(&body["data"], at),
        "plugin" => json!({"role": role(&strings(&body["cmd"])), "home": plugin_home(&strings(&body["cmd"])), "of": plugin_of(&strings(&body["cmd"]))}),
        "footprint" => {
            let told = Told {
                known: by_pid(&body["known"], |pair| (pystr(&pair[0]), pystr(&pair[1]))),
                host_pids: body["host_pids"].as_array().into_iter().flatten().map(int).collect(),
                idle: truthy(&body["idle"]),
                now: at,
                last_used: seconds(&body["last_used"]),
                own_workers: by_pid(&body["own_workers"], pystr),
            };
            footprint(&procs, me, &told, &cmdline)
        }
        "orphans" => orphan_bridges(&procs, me, &cmdline).into_iter().map(|(row, _)| row).collect(),
        "idle_workers" => idle_plugin_workers(&procs, &by_pid(&body["cpu"], |used| used.as_f64().unwrap_or(0.0)), me, at, &seconds(&body["last_used"]), &cmdline).into(),
        "workers_text" => workers_text(body["rows"].as_array().map(Vec::as_slice).unwrap_or_default()).into(),
        _ => Value::Null,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(words: &[&str]) -> Vec<String> {
        words.iter().map(|word| word.to_string()).collect()
    }

    #[test]
    fn a_command_line_says_the_role_and_the_plugin() {
        assert_eq!(role(&args(&["x", "bridge", "--shared-daemon"])), "shared tool server");
        assert_eq!(role(&args(&["x", "Bridge", "--port", "1"])), "tool server (bridge)");
        assert_eq!((role(&args(&["bridge"])), role(&args(&["x", "agent-host"])), role(&args(&["x", "--multiprocessing-fork"]))), ("app", "agent host", "worker"));
        let forge = args(&["python.exe", "C:\\Users\\x\\AppData\\Local\\UEFN-Ducky\\UEFN_Plugins\\\\forge\\forge-engine\\worker.py"]);
        assert_eq!((plugin_home(&forge), plugin_of(&forge)), ("forge".into(), "forge".into()));
        assert_eq!(plugin_home(&args(&["python", "D:/repos/uefn-plugin-city-builder/run.py"])), "city-builder");
        assert_eq!(plugin_home(&args(&["python", "D:/tools/blend_3-Engine/run.py"])), "blend_3");
        assert_eq!((plugin_home(&args(&["python", "D:/uefn_plugins/"])), plugin_of(&args(&["python", "D:/x/plugins/atlas/w.py"]))), (String::new(), "atlas".into()));
        assert_eq!((plugin_of(&args(&["python", "D:/tools/jobs/w.py", "-v"])), plugin_of(&args(&["python", "-m", "worker"]))), ("jobs".into(), "plugin".into()));
        assert_eq!(workers_text(&[json!({"plugin": "forge-3d"}), json!({"plugin": "atlas"}), json!({"plugin": "forge-3d"})]), ["2 idle Forge 3D workers", "1 idle Atlas worker"]);
    }

    #[test]
    fn percentiles_and_fallbacks_read_as_python_reads_them() {
        let samples: Vec<f64> = (1..=100).rev().map(f64::from).collect();
        assert_eq!(core_wait_stats(&samples), json!({"p50": 50.0, "p95": 95.0, "max": 100.0, "samples": 100}));
        assert_eq!(core_wait_stats(&[])["samples"], 0);
        let recent = json!([{"at": 2800.0, "reason": "old"}, {"at": 9940.0, "reason": "one"}, "junk", {"at": "9995", "reason": "two"}]);
        assert_eq!(tool_fallbacks(&json!({"count": 91, "recent": recent}), 10_000.0), json!({"count": 91, "last_hour": 2, "reason": "two"}));
        assert_eq!(tool_fallbacks(&json!({"count": "x", "last_at": 9970, "reason": "r"}), 10_000.0), json!({"count": 1, "last_hour": 1, "reason": "r"}));
        assert_eq!(tool_fallbacks(&json!([1]), 10_000.0), json!({"count": 0, "last_hour": 0, "reason": ""}));
    }

    #[tokio::test]
    async fn a_probe_that_hangs_does_not_hold_the_report() {
        let started = Instant::now();
        #[allow(clippy::disallowed_methods)] // on a blocking thread
        let slow = probe(Duration::from_millis(50), || {
            std::thread::sleep(Duration::from_secs(2));
            Some(1)
        });
        assert_eq!((slow.await, probe(Duration::from_secs(5), || Some(2)).await), (None, Some(2)));
        assert!(started.elapsed() < Duration::from_secs(1));
    }

    #[tokio::test]
    async fn the_meter_runs_while_reports_ask_for_it() {
        start_core_wait();
        start_core_wait();
        let meter = METER.lock().unwrap();
        assert!(meter.running && meter.until.is_some());
    }
}
