//! What a health report says is wrong, in plain words, each with its fix
//! (`self_health.findings`, `_footprint_findings`). Pure: a report in, sentences out, the
//! same ones the Python backend writes for the same report.
//!
//! ponytail: a full graphics card is not named here yet (`frontend/gpu_memory.py`); the
//! report's `gpu` is empty until that reader moves.

use serde_json::{json, Value};

use crate::store::pyjson::{float, float_repr, int, pystr, truthy};

/// The parts of Ducky's own total, and what each is called.
pub const PARTS: [(&str, &str); 8] = [
    ("app", "app"),
    ("agent_hosts", "agent hosts"),
    ("tool_servers", "tool servers"),
    ("plugin_workers", "plugin workers"),
    ("webview2", "WebView2"),
    ("terminals", "terminals"),
    ("conhosts", "conhosts"),
    ("agents", "agent CLIs"),
];
/// One Ducky process holding more while no turn runs is a finding.
pub const IDLE_PROCESS_MB: f64 = 500.0;
pub const PLUGIN_IDLE_MIN: i64 = 10;
const MAX_CONHOSTS: i64 = 6;
/// Of one core: a thread busier than this is a finding of its own.
const SPINNING_SHARE: f64 = 0.5;
/// Two frozen frames a minute, all hour.
const UI_STALLS_PER_HOUR: i64 = 120;
/// One bridge method every 6 s, all hour: a loop, not a person clicking.
const UI_CALLS_PER_HOUR: i64 = 600;
const FREE_FIX: &str = "ducky_self_care(action='free_ducky_memory')";

/// `float(value or 0)`.
fn num(value: &Value) -> f64 {
    float(value).unwrap_or(0.0)
}

/// `a or b`.
fn or<'a>(a: &'a Value, b: &'a Value) -> &'a Value {
    if truthy(a) { a } else { b }
}

fn list(value: &Value) -> &[Value] {
    value.as_array().map(Vec::as_slice).unwrap_or_default()
}

/// `round(value, digits)`.
pub fn round_to(value: f64, digits: usize) -> f64 {
    format!("{value:.digits$}").parse().unwrap_or(value)
}

/// `round(value)` as an f-string prints it.
fn whole(value: f64) -> String {
    let text = format!("{value:.0}");
    if text == "-0" { "0".into() } else { text }
}

/// `round(value, 1)` as an f-string prints it.
fn tenth(value: f64) -> String {
    float_repr(round_to(value, 1))
}

pub fn plural(n: i64, one: &str, many: &str) -> String {
    format!("{n} {}", if n == 1 { one } else { many })
}

fn gb_text(mb: f64) -> String {
    if mb >= 1024.0 {
        format!("{} GB", format!("{:.2}", mb / 1024.0).trim_end_matches('0').trim_end_matches('.'))
    } else {
        format!("{mb:.0} MB")
    }
}

fn finding(severity: &str, what: String, why: &str, fix: &str) -> Value {
    json!({"severity": severity, "what": what, "why": why, "fix": fix})
}

/// Rows grouped by a key, groups in the order their first row came.
fn grouped<'a>(rows: impl Iterator<Item = &'a Value>, key: impl Fn(&Value) -> String) -> Vec<(String, Vec<&'a Value>)> {
    let mut groups: Vec<(String, Vec<&Value>)> = Vec::new();
    for row in rows {
        let name = key(row);
        match groups.iter_mut().find(|(have, _)| *have == name) {
            Some((_, rows)) => rows.push(row),
            None => groups.push((name, vec![row])),
        }
    }
    groups
}

/// Ducky's own total (always), and the leftovers that make it big.
fn footprint_findings(dt: &Value, pc: &Value) -> Vec<Value> {
    let mut out = Vec::new();
    let total_gb = num(&dt["total_mb"]) / 1024.0;
    let ram_gb = num(&pc["memory_total_gb"]);
    let severity = if total_gb > 5.0 || (ram_gb != 0.0 && total_gb > 0.15 * ram_gb) {
        "high"
    } else if total_gb > 3.0 {
        "medium"
    } else {
        "ok"
    };
    let size = |key: &str| num(&dt["parts"][key]);
    let mut shown: Vec<String> = PARTS.iter().filter(|(key, _)| *key != "conhosts" && size(key) >= 1.0).map(|(key, label)| format!("{label} {}", gb_text(size(key)))).collect();
    if truthy(&dt["conhosts"]) {
        shown.push(format!("{} conhosts", pystr(&dt["conhosts"])));
    }
    let what = format!("Ducky uses {total_gb:.1} GB: {}.", shown.join(", "));
    if severity == "ok" {
        out.push(finding(severity, what, "Within Ducky's normal range (under 3 GB).", ""));
    } else {
        let why = format!("Ducky's own processes hold more than they should{}", if truthy(&dt["idle"]) { " while no agent runs." } else { "." });
        out.push(finding(severity, what, &why, &format!("{FREE_FIX} frees its leftovers; ducky_self_care has each step on its own.")));
    }
    let big = list(&dt["big_idle_processes"]);
    if !big.is_empty() {
        let name = |b: &Value| format!("{} ({}) {}", pystr(&b["name"]), pystr(&b["part"]).replace('_', " "), gb_text(num(&b["private_mb"])));
        let names: Vec<String> = big.iter().take(6).map(name).collect();
        out.push(finding(
            "high",
            format!("{} Ducky processes hold over {IDLE_PROCESS_MB} MB while no turn runs: {}.", big.len(), names.join(", ")),
            "An idle Ducky process should sit at a few hundred MB; more means leftovers or growth.",
            &format!("{FREE_FIX}; restart Ducky if the app or an agent host stays big."),
        ));
    }
    let held = |rows: &[&Value]| gb_text(rows.iter().map(|row| num(&row["mb"])).sum());
    for (version, rows) in grouped(list(&dt["shared_tool_servers"]).iter(), |s| pystr(or(or(&s["version"], &s["exe"]), &json!("?")))) {
        if rows.len() > 1 {
            out.push(finding(
                "high",
                format!("{} shared tool servers of one version ({version}) hold {} (1 expected).", rows.len(), held(&rows)),
                "A second shared tool server started instead of reusing the first one.",
                "Restart Ducky to keep one; ducky_self_care(action='stop_orphan_bridges') stops orphaned ones.",
            ));
        }
    }
    let stale = list(&dt["plugin_workers"]).iter().filter(|w| !w["idle_min"].is_null() && int(&w["idle_min"]) >= PLUGIN_IDLE_MIN);
    for (plugin, rows) in grouped(stale, |w| pystr(&w["plugin"])) {
        let unused = rows.iter().map(|row| int(&row["idle_min"])).max().unwrap_or_default();
        out.push(finding(
            "high",
            format!("{plugin} plugin workers: {} processes hold {}, unused {unused} min.", rows.len(), held(&rows)),
            &format!("The plugin had no call in {PLUGIN_IDLE_MIN} minutes but its workers stay loaded."),
            &format!("{FREE_FIX} stops idle plugin workers (the plugin starts them again on its next call)."),
        ));
    }
    if int(&dt["conhosts"]) > MAX_CONHOSTS {
        out.push(finding(
            "high",
            format!("{} console hosts (conhost.exe) run under Ducky.", pystr(&dt["conhosts"])),
            "Each console a terminal or tool opened and never closed keeps one alive.",
            &format!("ducky_self_care(action='close_idle_terminals'), or {FREE_FIX}"),
        ));
    }
    out
}

/// Plain-words causes, most important first, each with the fix (a self-care action or a
/// step). `now` is seconds since 1970.
pub fn findings(snap: &Value, now: f64) -> Vec<Value> {
    let mut out: Vec<Value> = Vec::new();
    let falls = &snap["tool_fallbacks"];
    let fell = int(&falls["last_hour"]);
    if fell != 0 {
        let reason = if truthy(&falls["reason"]) { pystr(&falls["reason"]) } else { "not recorded".into() };
        out.push(finding(
            if fell >= 3 { "high" } else { "medium" },
            format!("{} in the last hour could not join Ducky's tool server and loaded one of their own.", plural(fell, "agent start", "agent starts")),
            &format!("Each of those holds a whole tool server in memory, and its first tool calls wait while that loads. The last one's reason: {reason}."),
            "Restart Ducky if it keeps happening. shared_mcp_fallbacks.json in Ducky's data folder lists the latest ones with their reasons.",
        ));
    }
    let healed: Vec<&Value> = list(&snap["auto_heal"]).iter().filter(|h| now - num(&h["at"]) < 3600.0).collect();
    if !healed.is_empty() {
        let fixed: Vec<String> = healed[healed.len().saturating_sub(3)..].iter().map(|h| if truthy(&h["fixed"]) { pystr(&h["fixed"]) } else { String::new() }).collect();
        out.push(finding(
            "low",
            format!("Ducky fixed {} by itself in the last hour", plural(healed.len() as i64, "stuck thing", "stuck things")),
            &fixed.join("; "),
            "Nothing to do. The same fix coming back every few minutes means something keeps breaking it.",
        ));
    }
    let (app, pc) = (&snap["app"], &snap["pc"]);
    let ncpu = int(or(&pc["cpus"], &json!(1))) as f64;
    let app_cpu_pc = num(&app["cpu_percent"]) / ncpu;
    let wait_p95 = num(&app["core_wait_ms"]["p95"]);
    if wait_p95 > 20.0 {
        out.push(finding(
            if wait_p95 > 50.0 { "high" } else { "medium" },
            format!("Ducky's Python core is overloaded: work waits {wait_p95:.0} ms"),
            "Too many threads want Python at once (the GIL)",
            "ducky_perf_report names the busiest calls; fewer running agents",
        ));
    }
    let busy_threads = list(&app["hot_threads"]);
    let percent = |hot: &Value| whole(num(&hot["share"]) * 100.0);
    for hot in busy_threads.iter().filter(|hot| num(&hot["share"]) > SPINNING_SHARE) {
        let stack: Vec<String> = list(&hot["stack"]).iter().map(pystr).collect();
        let place = stack.first().cloned().unwrap_or_else(|| pystr(or(&hot["where"], &json!("native code"))));
        out.push(finding(
            "high",
            format!("Ducky's thread {} used {}% of a core in {place}.", pystr(&hot["name"]), percent(hot)),
            "One thread busy all the time: work repeated per event, or a loop that never waits.",
            &format!("Stack, innermost first: {}", if stack.is_empty() { "(no Python frames)".into() } else { stack.join(" < ") }),
        ));
    }
    let warm: Vec<String> = busy_threads.iter().filter(|hot| num(&hot["share"]) <= SPINNING_SHARE).map(|hot| format!("{} {}% in {}", pystr(&hot["name"]), percent(hot), pystr(&hot["where"]))).collect();
    if !warm.is_empty() {
        out.push(finding(
            "medium",
            format!("Busy Ducky threads: {}.", warm.join(", ")),
            "Each takes a fifth of a core or more; together they make other work wait for Python.",
            "ducky_perf_report names the busiest calls; fewer running agents",
        ));
    }
    let in_window: Vec<String> = list(&snap["agents_in_window"]).iter().map(pystr).collect();
    if !in_window.is_empty() {
        out.push(finding(
            "medium",
            format!("An agent is running inside the window process, not an agent host: chat {}.", in_window.join(", ")),
            "Its turn could not reach an agent host and fell back to the window, where it competes with the UI for Python.",
            "ducky_health's agent_hosts shows whether the hosts started; the next turn tries a host again.",
        ));
    }
    let piled: Vec<String> = list(&app["inflight"]).iter().filter(|r| int(&r["count"]) > 20 || num(&r["oldest_s"]) > 10.0).map(|r| format!("{} ×{}", pystr(&r["method"]), pystr(&r["count"]))).collect();
    if !piled.is_empty() {
        out.push(finding(
            "medium",
            format!("Window calls are piling up: {}", piled.join(", ")),
            "The window asks faster than Python answers (or one call hangs), so the UI shows Loading.",
            "ducky_perf_report names the slow calls; fewer running agents",
        ));
    }
    let ui = &snap["ui_last_hour"];
    let stalls = int(&ui["ui_stalls"]);
    if stalls > UI_STALLS_PER_HOUR || (stalls >= 20 && num(&ui["ui_stall_p95_ms"]) >= 1000.0) {
        let pc_note = if truthy(&ui["ui_stall_pc"]) { pystr(&ui["ui_stall_pc"]) } else { String::new() };
        out.push(finding(
            if stalls > 5 * UI_STALLS_PER_HOUR { "high" } else { "medium" },
            format!("Ducky's window froze {stalls} times in the last hour (p95 {:.0} ms).", num(&ui["ui_stall_p95_ms"])),
            format!("The UI thread is kept busy: a loop or a flood of bridge replies. {pc_note}").trim(),
            "ducky_perf_report: per_process names the busiest calls and slowest scripts.",
        ));
    }
    for call in list(&ui["top_ui_calls"]) {
        let (method, n) = (pystr(&call[0]), &call[1]);
        if int(n) > UI_CALLS_PER_HOUR {
            out.push(finding(
                "high",
                format!("The window called {method} {} times in the last hour.", pystr(n)),
                "Something re-asks it all the time (a per-frame loop, a retry that never backs off); every answer costs the UI thread.",
                &format!("Find the caller of {method} in the web UI and make it send only on change."),
            ));
        }
    }
    if app_cpu_pc > 15.0 {
        out.push(finding(
            "high",
            format!("Ducky's window process uses {app_cpu_pc:.0}% of the PC's CPU."),
            "Something in the app is busy while it should idle (a stuck loop, a flood of UI updates).",
            "ducky_perf_report names the slowest UI work; ducky_memory_report(start_trace=true) shows what grows.",
        ));
    }
    let held = or(&app["private_mb"], &app["memory_mb"]);
    if num(held) > 2048.0 {
        out.push(finding(
            "high",
            format!("Ducky's window process holds {} MB.", pystr(held)),
            "Normal is a few hundred MB; this much means something keeps growing.",
            "ducky_memory_report(start_trace=true), wait a minute, call it again: 'grew' names the cause.",
        ));
    }
    if int(&app["handles"]) > 15000 {
        out.push(finding(
            "medium",
            format!("{} open handles in Ducky's window process.", pystr(&app["handles"])),
            "Handles that only climb are a leak (files, sockets, threads not closed).",
            "ducky_memory_report and ducky_perf_report; restart Ducky if it keeps climbing.",
        ));
    }
    let helpers = list(&snap["helpers"]);
    if let Some(web) = helpers.iter().find(|h| pystr(&h["name"]).to_lowercase().contains("webview2")).filter(|web| num(&web["memory_mb"]) > 3000.0) {
        out.push(finding(
            "medium",
            format!("Ducky's browser engine (WebView2) uses {} MB in {} processes.", pystr(&web["memory_mb"]), pystr(&web["count"])),
            "Many open tabs, a heavy Browser-tab page, or a long chat with many pictures.",
            "Close Browser tabs and chats you are not using.",
        ));
    }
    let others = list(&snap["other_ducky_processes"]);
    let orphans: Vec<&Value> = others.iter().filter(|p| truthy(&p["orphan"])).collect();
    if !orphans.is_empty() {
        out.push(finding(
            "medium",
            format!("{} leftover Ducky tool servers whose IDE or agent is gone ({} MB).", orphans.len(), whole(orphans.iter().map(|p| num(&p["memory_mb"])).sum())),
            "An IDE or agent closed without stopping its Ducky bridge.",
            "ducky_self_care(action='stop_orphan_bridges')",
        ));
    }
    let heavy: Vec<String> = list(&snap["sandboxes"]["programs"])
        .iter()
        .filter(|p| num(&p["memory_mb"]) > 1024.0 || num(&p["cpu_percent"]) / ncpu > 25.0)
        .take(4)
        .map(|p| format!("{} ({} MB, {}% CPU)", pystr(&p["name"]), pystr(&p["memory_mb"]), pystr(&p["cpu_percent"])))
        .collect();
    if !heavy.is_empty() {
        out.push(finding(
            "medium",
            format!("Sandbox programs are heavy: {}.", heavy.join(", ")),
            "A program an agent started in a sandbox is still running.",
            "ducky_self_care(action='stop_sandbox', target='<chat id or all>')",
        ));
    }
    let owned = &snap["terminals"]["agent_owned"];
    if int(owned) > 6 {
        out.push(finding("low", format!("{} agent terminals are open.", pystr(owned)), "Each keeps a shell and its console alive.", "ducky_self_care(action='close_idle_terminals')"));
    }
    let storage = &snap["storage"];
    if num(&storage["ducky_db_wal_mb"]) > 256.0 {
        out.push(finding(
            "low",
            format!("The database's write-ahead log is {} MB.", pystr(&storage["ducky_db_wal_mb"])),
            "It is folded back into ducky.db when nothing holds it; a long-running reader stops that.",
            "Restart Ducky when convenient.",
        ));
    }
    if num(&storage["cache_mb"]) > 1024.0 || num(&storage["sandboxes_mb"]) > 10240.0 {
        out.push(finding(
            "low",
            format!("Caches {} MB, sandboxes {} MB on disk.", pystr(&storage["cache_mb"]), pystr(&storage["sandboxes_mb"])),
            "Downloads and sandboxes that are no longer needed.",
            "ducky_self_care(action='trim_caches') and ducky_self_care(action='prune_sandboxes')",
        ));
    }
    for uefn in list(&snap["uefn"]) {
        let held = num(or(&uefn["private_mb"], &uefn["memory_mb"]));
        if held > 30.0 * 1024.0 {
            out.push(finding(
                "high",
                format!("UEFN uses {} GB.", tenth(held / 1024.0)),
                "UEFN's editor memory only grows during a long session (garbage collections then stall it for seconds); it is not Ducky.",
                "Save, then reopen UEFN (ducky_restart_uefn) to get the memory back.",
            ));
        }
    }
    let used = num(&pc["memory_used_percent"]);
    if used > 88.0 {
        let ducky_mb = num(&app["memory_mb"]) + helpers.iter().map(|h| num(&h["memory_mb"])).sum::<f64>() + others.iter().map(|p| num(&p["memory_mb"])).sum::<f64>();
        let biggest = list(&pc["top_apps"]).first().cloned().unwrap_or_else(|| json!({}));
        let name = biggest.get("name").map_or("?".into(), pystr);
        let mostly_not = ducky_mb < 0.15 * num(or(&pc["memory_total_gb"], &json!(1))) * 1024.0;
        out.push(finding(
            if used > 95.0 { "high" } else { "medium" },
            format!("The PC's memory is {used:.0}% used; Ducky's share is about {} GB.", tenth(ducky_mb / 1024.0)),
            &format!("The biggest user is {name} ({} GB).{}", tenth(num(&biggest["memory_mb"]) / 1024.0), if mostly_not { " It's mostly not Ducky." } else { "" }),
            "Close what you don't need among the top apps listed.",
        ));
    }
    if truthy(&snap["ducky_total"]) {
        out.extend(footprint_findings(&snap["ducky_total"], pc));
    }
    if out.iter().all(|found| found["severity"] == "ok") {
        out.push(finding("ok", "Nothing in Ducky looks wrong right now.".into(), "CPU, memory, handles, helpers, sandboxes and terminals are all in their normal range.", ""));
    }
    out.sort_by_key(|found| ["high", "medium", "low", "ok"].iter().position(|rank| found["severity"] == *rank).unwrap_or(9));
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn numbers_print_as_python_prints_them() {
        assert_eq!((gb_text(1024.0), gb_text(1536.0), gb_text(380.4), gb_text(2900.0)), ("1 GB".into(), "1.5 GB".into(), "380 MB".into(), "2.83 GB".into()));
        assert_eq!((whole(2.5), whole(3.5), whole(-0.2), tenth(45.04), tenth(45.0)), ("2".into(), "4".into(), "0".into(), "45.0".into(), "45.0".into()));
        assert_eq!((round_to(0.125, 2), round_to(2.675, 2), plural(1, "thing", "things"), plural(0, "thing", "things")), (0.12, 2.67, "1 thing".into(), "0 things".into()));
    }

    #[test]
    fn a_healthy_report_says_so_and_a_sick_one_puts_the_worst_first() {
        let healthy = json!({"app": {"cpu_percent": 1, "memory_mb": 300, "private_mb": 280, "handles": 900}, "pc": {"cpus": 8, "memory_used_percent": 40}});
        let found = findings(&healthy, 0.0);
        assert_eq!((found.len(), &found[0]["severity"], &found[0]["fix"]), (1, &json!("ok"), &json!("")));
        let sick = json!({"app": {"handles": 40000, "private_mb": 3500.5}, "terminals": {"agent_owned": 9}, "pc": {}});
        let found = findings(&sick, 0.0);
        let read: Vec<(&str, &str)> = found.iter().map(|f| (f["severity"].as_str().unwrap(), f["what"].as_str().unwrap())).collect();
        assert_eq!(read, [("high", "Ducky's window process holds 3500.5 MB."), ("medium", "40000 open handles in Ducky's window process."), ("low", "9 agent terminals are open.")]);
    }
}
