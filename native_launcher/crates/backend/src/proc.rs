//! Every process on the PC in one look (`backend/util/proc_table.py`): pid, parent, name,
//! memory, threads, handles, CPU time.
//!
//! Windows answers with one `NtQuerySystemInformation` call, Linux is read from `/proc`,
//! macOS from one `ps`. A command line is only ever asked for one process at a time, and
//! only for Ducky's own few.
//!
//! Everything here blocks (system calls, files, a one second CPU sample): callers run it
//! inside `spawn_blocking`.
//!
//! ponytail: no thread count on macOS (`ps` has none), and no handle count off Windows.
#![allow(clippy::disallowed_methods)]

use std::collections::{BTreeMap, HashMap, HashSet};
use std::time::{Duration, Instant};

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Proc {
    pub pid: i64,
    pub ppid: i64,
    pub name: String,
    /// Working set, bytes.
    pub rss: u64,
    /// Private (commit) bytes; resident minus shared on Linux, resident on macOS.
    pub private: u64,
    pub threads: u32,
    pub handles: u32,
    /// User and kernel seconds so far.
    pub cpu_s: f64,
    /// Seconds since 1970; 0 when unknown.
    pub started: f64,
}

/// By pid, lowest first.
pub type Table = BTreeMap<i64, Proc>;

pub fn table() -> Table {
    #[cfg(windows)]
    return windows::table();
    #[cfg(target_os = "linux")]
    return linux_table(std::path::Path::new("/proc"));
    #[cfg(target_os = "macos")]
    return mac::table();
    #[cfg(not(any(windows, target_os = "linux", target_os = "macos")))]
    return Table::new();
}

/// The table now, and each process's CPU % over `interval` (100 = one full core).
pub fn sample(interval: Duration) -> (Table, HashMap<i64, f64>) {
    let (first, start) = (table(), Instant::now());
    std::thread::sleep(interval.max(Duration::from_millis(50)));
    let last = table();
    let span = start.elapsed().as_secs_f64().max(1e-3);
    let mut cpu = HashMap::new();
    for (pid, proc) in &last {
        // The same process, not a new one that got its pid (macOS start times are whole seconds).
        if let Some(old) = first.get(pid).filter(|old| (old.started - proc.started).abs() < 1.5) {
            cpu.insert(*pid, round1((proc.cpu_s - old.cpu_s).max(0.0) / span * 100.0));
        }
    }
    (last, cpu)
}

fn round1(value: f64) -> f64 {
    format!("{value:.1}").parse().unwrap_or(value)
}

/// `root` and everything it started (a reused parent pid never counts).
pub fn tree(procs: &Table, root: i64) -> Vec<i64> {
    let mut kids: HashMap<i64, Vec<i64>> = HashMap::new();
    for proc in procs.values().filter(|proc| proc.pid != proc.ppid) {
        kids.entry(proc.ppid).or_default().push(proc.pid);
    }
    let (mut out, mut seen) = (Vec::new(), HashSet::new());
    let mut stack: Vec<i64> = procs.contains_key(&root).then_some(root).into_iter().collect();
    while let Some(pid) = stack.pop() {
        if !seen.insert(pid) {
            continue;
        }
        out.push(pid);
        let started = procs[&pid].started;
        stack.extend(kids.get(&pid).into_iter().flatten().filter(|kid| procs[kid].started >= started));
    }
    out
}

/// True when `pid`'s parent is gone, or its pid now belongs to a newer process.
pub fn orphan(procs: &Table, pid: i64) -> bool {
    let Some(proc) = procs.get(&pid) else { return false };
    procs.get(&proc.ppid).is_none_or(|parent| parent.started != 0.0 && proc.started != 0.0 && parent.started > proc.started)
}

/// A process's command line and program file; empty for one that may not be asked.
pub fn cmdline(pid: i64) -> (Vec<String>, String) {
    #[cfg(windows)]
    return windows::cmdline(pid);
    #[cfg(not(windows))]
    return unix::cmdline(pid);
}

/// Total and available memory of the PC, in bytes.
pub fn memory() -> (u64, u64) {
    #[cfg(windows)]
    return windows::memory();
    #[cfg(not(windows))]
    return unix::memory();
}

fn kill(pid: i64) -> bool {
    #[cfg(windows)]
    return windows::kill(pid);
    #[cfg(not(windows))]
    return unix::kill(pid);
}

/// Stop `pid` if it is still the program called `name` (its pid may have gone to another).
pub fn terminate(pid: i64, name: &str) -> bool {
    table().get(&pid).is_some_and(|proc| proc.name.eq_ignore_ascii_case(name)) && kill(pid)
}

/// Stop `pid` and everything it started, children first.
pub fn terminate_tree(pid: i64, name: &str) -> bool {
    let procs = table();
    if !procs.get(&pid).is_some_and(|proc| proc.name.eq_ignore_ascii_case(name)) {
        return false;
    }
    for child in tree(&procs, pid).into_iter().skip(1).rev() {
        kill(child);
    }
    kill(pid)
}

#[cfg(windows)]
mod windows {
    use std::ffi::c_void;

    use super::{Proc, Table};

    /// 1601-01-01 to 1970-01-01, seconds.
    const EPOCH_DIFF_S: f64 = 11_644_473_600.0;
    const INFO_LENGTH_MISMATCH: u32 = 0xC000_0004;
    const SYSTEM_PROCESS_INFORMATION: u32 = 5;
    const PROCESS_COMMAND_LINE: u32 = 60;
    const PROCESS_TERMINATE: u32 = 0x0001;
    const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;

    #[repr(C)]
    #[allow(dead_code)] // Windows' layout: every field has its place
    struct UnicodeString {
        length: u16,
        maximum_length: u16,
        buffer: *const u16,
    }

    /// `SYSTEM_PROCESS_INFORMATION`.
    #[repr(C)]
    #[allow(dead_code)]
    struct ProcInfo {
        next_entry_offset: u32,
        number_of_threads: u32,
        working_set_private_size: i64,
        hard_fault_count: u32,
        number_of_threads_high_watermark: u32,
        cycle_time: u64,
        create_time: i64,
        user_time: i64,
        kernel_time: i64,
        image_name: UnicodeString,
        base_priority: i32,
        unique_process_id: usize,
        inherited_from_unique_process_id: usize,
        handle_count: u32,
        session_id: u32,
        unique_process_key: usize,
        peak_virtual_size: usize,
        virtual_size: usize,
        page_fault_count: u32,
        peak_working_set_size: usize,
        working_set_size: usize,
        quota_peak_paged_pool_usage: usize,
        quota_paged_pool_usage: usize,
        quota_peak_non_paged_pool_usage: usize,
        quota_non_paged_pool_usage: usize,
        pagefile_usage: usize,
        peak_pagefile_usage: usize,
        private_page_count: usize,
    }

    /// `MEMORYSTATUSEX`.
    #[repr(C)]
    #[allow(dead_code)]
    struct MemoryStatus {
        length: u32,
        memory_load: u32,
        total_phys: u64,
        avail_phys: u64,
        rest: [u64; 5],
    }

    #[link(name = "ntdll")]
    extern "system" {
        fn NtQuerySystemInformation(class: u32, info: *mut c_void, length: u32, needed: *mut u32) -> i32;
        fn NtQueryInformationProcess(process: *mut c_void, class: u32, info: *mut c_void, length: u32, needed: *mut u32) -> i32;
    }

    #[link(name = "kernel32")]
    extern "system" {
        fn OpenProcess(access: u32, inherit: i32, pid: u32) -> *mut c_void;
        fn CloseHandle(handle: *mut c_void) -> i32;
        fn TerminateProcess(process: *mut c_void, code: u32) -> i32;
        fn QueryFullProcessImageNameW(process: *mut c_void, flags: u32, name: *mut u16, size: *mut u32) -> i32;
        fn GlobalMemoryStatusEx(status: *mut MemoryStatus) -> i32;
        fn LocalFree(memory: *mut c_void) -> *mut c_void;
    }

    #[link(name = "shell32")]
    extern "system" {
        fn CommandLineToArgvW(line: *const u16, count: *mut i32) -> *mut *mut u16;
    }

    pub fn table() -> Table {
        let mut out = Table::new();
        let mut size = 1usize << 20;
        let mut filled: Option<Vec<u64>> = None;
        for _ in 0..6 {
            let mut reply = vec![0u64; size / 8];
            let mut needed = 0u32;
            // SAFETY: `reply` is `size` bytes long and outlives the call.
            let status = unsafe { NtQuerySystemInformation(SYSTEM_PROCESS_INFORMATION, reply.as_mut_ptr().cast(), size as u32, &mut needed) };
            if status == 0 {
                filled = Some(reply);
                break;
            }
            if status as u32 != INFO_LENGTH_MISMATCH {
                return out;
            }
            size = (size * 2).max(needed as usize + (64 << 10)) / 8 * 8;
        }
        let Some(reply) = filled else { return out };
        let base = reply.as_ptr().cast::<u8>();
        let mut offset = 0usize;
        while offset + std::mem::size_of::<ProcInfo>() <= size {
            // SAFETY: the entry lies inside `reply` (checked above); Windows linked it.
            let info = unsafe { std::ptr::read_unaligned(base.add(offset).cast::<ProcInfo>()) };
            let name = if info.image_name.buffer.is_null() {
                String::new()
            } else {
                // SAFETY: an entry's name is `length` bytes of UTF-16 inside the same reply.
                String::from_utf16_lossy(unsafe { std::slice::from_raw_parts(info.image_name.buffer, usize::from(info.image_name.length) / 2) })
            };
            let pid = info.unique_process_id as i64;
            let proc = Proc {
                pid,
                ppid: info.inherited_from_unique_process_id as i64,
                name: if name.is_empty() && pid == 0 { "System Idle Process".into() } else { name },
                rss: info.working_set_size as u64,
                private: info.pagefile_usage as u64,
                threads: info.number_of_threads,
                handles: info.handle_count,
                cpu_s: (info.user_time + info.kernel_time) as f64 / 1e7,
                started: if info.create_time > 0 { info.create_time as f64 / 1e7 - EPOCH_DIFF_S } else { 0.0 },
            };
            out.insert(pid, proc);
            if info.next_entry_offset == 0 {
                break;
            }
            offset += info.next_entry_offset as usize;
        }
        out
    }

    struct Handle(*mut c_void);

    impl Drop for Handle {
        fn drop(&mut self) {
            // SAFETY: the handle came from OpenProcess and is closed once.
            unsafe { CloseHandle(self.0) };
        }
    }

    fn open(pid: i64, access: u32) -> Option<Handle> {
        // SAFETY: no pointers; a pid that is gone or not ours answers null.
        let handle = unsafe { OpenProcess(access, 0, u32::try_from(pid).ok()?) };
        (!handle.is_null()).then_some(Handle(handle))
    }

    pub fn cmdline(pid: i64) -> (Vec<String>, String) {
        let Some(process) = open(pid, PROCESS_QUERY_LIMITED_INFORMATION) else { return Default::default() };
        let mut path = vec![0u16; 32768];
        let mut length = path.len() as u32;
        // SAFETY: `path` holds `length` characters.
        let exe = if unsafe { QueryFullProcessImageNameW(process.0, 0, path.as_mut_ptr(), &mut length) } != 0 {
            String::from_utf16_lossy(&path[..length as usize])
        } else {
            String::new()
        };
        // A command line is at most 32,767 characters, after the 16 bytes that say where it is.
        let mut reply = vec![0u64; 8200];
        let mut needed = 0u32;
        // SAFETY: `reply` is as long as the length given and outlives the call.
        let status = unsafe { NtQueryInformationProcess(process.0, PROCESS_COMMAND_LINE, reply.as_mut_ptr().cast(), (reply.len() * 8) as u32, &mut needed) };
        if status != 0 {
            return (Vec::new(), exe);
        }
        // SAFETY: on success the reply starts with a UNICODE_STRING whose text is inside it.
        let line = unsafe { std::ptr::read(reply.as_ptr().cast::<UnicodeString>()) };
        if line.buffer.is_null() || line.length == 0 {
            return (Vec::new(), exe);
        }
        // SAFETY: as above: `length` bytes of UTF-16 inside `reply`.
        let mut text = unsafe { std::slice::from_raw_parts(line.buffer, usize::from(line.length) / 2) }.to_vec();
        text.push(0);
        let mut count = 0i32;
        // SAFETY: `text` ends with a NUL; the answer is freed below.
        let args = unsafe { CommandLineToArgvW(text.as_ptr(), &mut count) };
        if args.is_null() {
            return (Vec::new(), exe);
        }
        let mut out = Vec::new();
        for index in 0..usize::try_from(count).unwrap_or_default() {
            // SAFETY: `args` holds `count` NUL-ended strings.
            unsafe {
                let arg = *args.add(index);
                let mut length = 0;
                while *arg.add(length) != 0 {
                    length += 1;
                }
                out.push(String::from_utf16_lossy(std::slice::from_raw_parts(arg, length)));
            }
        }
        // SAFETY: CommandLineToArgvW's answer is one LocalAlloc block.
        unsafe { LocalFree(args.cast()) };
        (out, exe)
    }

    pub fn kill(pid: i64) -> bool {
        // SAFETY: the handle is ours until `Handle` drops.
        open(pid, PROCESS_TERMINATE).is_some_and(|process| unsafe { TerminateProcess(process.0, 15) } != 0)
    }

    pub fn memory() -> (u64, u64) {
        let mut status = MemoryStatus { length: std::mem::size_of::<MemoryStatus>() as u32, memory_load: 0, total_phys: 0, avail_phys: 0, rest: [0; 5] };
        // SAFETY: `status` says its own length.
        if unsafe { GlobalMemoryStatusEx(&mut status) } == 0 {
            return (0, 0);
        }
        (status.total_phys, status.avail_phys)
    }
}

/// Linux: every process from `/proc/<pid>/stat` and `status` (two small reads each).
#[cfg(any(target_os = "linux", test))]
fn linux_table(root: &std::path::Path) -> Table {
    let read = |path: std::path::PathBuf| std::fs::read(path).map(|bytes| String::from_utf8_lossy(&bytes).into_owned());
    let boot = read(root.join("stat")).ok().and_then(|text| text.lines().find_map(|line| line.strip_prefix("btime ")?.trim().parse::<f64>().ok())).unwrap_or(0.0);
    let mut out = Table::new();
    for entry in std::fs::read_dir(root).into_iter().flatten().flatten() {
        let Some(pid) = entry.file_name().to_str().and_then(|name| name.parse::<i64>().ok()) else { continue };
        let base = entry.path();
        // A process that exited between the listing and here has no files.
        let (Ok(stat), Ok(status)) = (read(base.join("stat")), read(base.join("status"))) else { continue };
        // "pid (comm) state ppid ...": comm may hold spaces and brackets, so split after the last ")".
        let (Some(open), Some(close)) = (stat.find('('), stat.rfind(')')) else { continue };
        let mut name = stat[open + 1..close].to_string();
        let f: Vec<&str> = stat[close + 1..].split_whitespace().collect();
        if f.len() < 22 {
            continue;
        }
        if name.len() == 15 {
            // comm is cut at 15 characters: the real name is the first argument's file name.
            if let Ok(line) = read(base.join("cmdline")) {
                let first = line.split('\0').next().unwrap_or_default();
                let file = first.rsplit('/').next().unwrap_or_default();
                if file.starts_with(&name) {
                    name = file.to_string();
                }
            }
        }
        let kb = |key: &str| status.lines().find_map(|line| line.strip_prefix(key)?.split_whitespace().next()?.parse::<u64>().ok()).unwrap_or(0) * 1024;
        // Clock ticks are a hundredth of a second on every Linux.
        let ticks = |index: usize| f[index].parse::<f64>().unwrap_or(0.0) / 100.0;
        let proc = Proc {
            pid,
            ppid: f[1].parse().unwrap_or(0),
            name,
            rss: kb("VmRSS:"),
            private: kb("RssAnon:"),
            threads: f[17].parse().unwrap_or(0),
            handles: 0,
            cpu_s: ticks(11) + ticks(12),
            started: if boot > 0.0 { boot + ticks(19) } else { 0.0 },
        };
        out.insert(pid, proc);
    }
    out
}

/// ps's `[[dd-]hh:]mm:ss[.cc]` as seconds.
#[cfg(any(target_os = "macos", test))]
fn clock_s(text: &str) -> f64 {
    let (days, rest) = text.rsplit_once('-').unwrap_or(("", text));
    let seconds = rest.split(':').fold(0.0, |total, part| total * 60.0 + part.parse::<f64>().unwrap_or(0.0));
    seconds + days.parse::<f64>().unwrap_or(0.0) * 86400.0
}

/// macOS: every process from the text of one `ps -axo pid=,ppid=,rss=,time=,etime=,comm=`.
#[cfg(any(target_os = "macos", test))]
fn mac_table(text: &str, now: f64) -> Table {
    let mut out = Table::new();
    for line in text.lines() {
        let mut rest = line.trim_start();
        let mut f: Vec<&str> = Vec::new();
        while f.len() < 5 {
            let Some((word, tail)) = rest.split_once(char::is_whitespace) else { break };
            f.push(word);
            rest = tail.trim_start();
        }
        let (Some(pid), true) = (f.first().and_then(|pid| pid.parse::<i64>().ok()), f.len() == 5) else { continue };
        let rss = f[2].parse::<u64>().unwrap_or(0) * 1024;
        // The program's path may hold spaces: it is the rest of the line.
        let name = rest.trim().rsplit('/').next().unwrap_or_default().to_string();
        let proc = Proc { pid, ppid: f[1].parse().unwrap_or(0), name, rss, private: rss, threads: 0, handles: 0, cpu_s: clock_s(f[3]), started: now - clock_s(f[4]) };
        out.insert(pid, proc);
    }
    out
}

#[cfg(target_os = "macos")]
mod mac {
    use std::process::Command;
    use std::time::{SystemTime, UNIX_EPOCH};

    pub fn table() -> super::Table {
        let now = SystemTime::now().duration_since(UNIX_EPOCH).map(|since| since.as_secs_f64()).unwrap_or_default();
        let listed = Command::new("ps").args(["-axo", "pid=,ppid=,rss=,time=,etime=,comm="]).env("LC_ALL", "C").output();
        listed.map(|ps| super::mac_table(&String::from_utf8_lossy(&ps.stdout), now)).unwrap_or_default()
    }
}

#[cfg(not(windows))]
mod unix {
    use std::process::{Command, Stdio};

    fn said(program: &str, args: &[&str]) -> String {
        let run = Command::new(program).args(args).env("LC_ALL", "C").stdin(Stdio::null()).stderr(Stdio::null()).output();
        run.map(|out| String::from_utf8_lossy(&out.stdout).into_owned()).unwrap_or_default()
    }

    pub fn cmdline(pid: i64) -> (Vec<String>, String) {
        if cfg!(target_os = "linux") {
            let base = std::path::Path::new("/proc").join(pid.to_string());
            let line = std::fs::read(base.join("cmdline")).unwrap_or_default();
            let args = String::from_utf8_lossy(&line).split('\0').filter(|arg| !arg.is_empty()).map(str::to_string).collect();
            let exe = std::fs::read_link(base.join("exe")).map(|path| path.to_string_lossy().into_owned()).unwrap_or_default();
            return (args, exe);
        }
        // ponytail: an argument with a space in it reads as two; the roles and plugin
        // folders looked for hold none.
        let pid = pid.to_string();
        let args = said("ps", &["-p", &pid, "-o", "args="]).split_whitespace().map(str::to_string).collect();
        (args, said("ps", &["-p", &pid, "-o", "comm="]).trim().to_string())
    }

    pub fn kill(pid: i64) -> bool {
        Command::new("kill").arg(pid.to_string()).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).status().is_ok_and(|status| status.success())
    }

    /// The number after `key` in a `key: number unit` listing.
    fn listed(text: &str, key: &str) -> u64 {
        text.lines().find_map(|line| line.strip_prefix(key)?.trim().trim_end_matches('.').split_whitespace().next()?.parse().ok()).unwrap_or(0)
    }

    pub fn memory() -> (u64, u64) {
        if cfg!(target_os = "linux") {
            let info = std::fs::read_to_string("/proc/meminfo").unwrap_or_default();
            return (listed(&info, "MemTotal:") * 1024, listed(&info, "MemAvailable:") * 1024);
        }
        let total = said("sysctl", &["-n", "hw.memsize"]).trim().parse().unwrap_or(0);
        let stat = said("vm_stat", &[]);
        // "Mach Virtual Memory Statistics: (page size of 16384 bytes)".
        let page = stat.split("page size of ").nth(1).and_then(|rest| rest.split_whitespace().next()?.parse::<u64>().ok()).unwrap_or(4096);
        (total, (listed(&stat, "Pages free:") + listed(&stat, "Pages inactive:")) * page)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn proc(pid: i64, ppid: i64, started: f64) -> (i64, Proc) {
        (pid, Proc { pid, ppid, started, ..Proc::default() })
    }

    #[test]
    fn this_process_is_in_the_table_with_its_command_line() {
        let me = i64::from(std::process::id());
        let procs = table();
        let mine = &procs[&me];
        assert!(mine.rss > 0 && mine.started > 0.0 && !mine.name.is_empty(), "{mine:?}");
        assert!(procs.len() > 5);
        let (args, exe) = cmdline(me);
        assert!(!args.is_empty() && !exe.is_empty(), "{args:?} {exe}");
        let (total, free) = memory();
        assert!(total > free && free > 0, "{total} {free}");
        // Another program's name never matches: nothing is stopped.
        assert!(!terminate(me, "not-this-program") && !terminate_tree(me, "not-this-program"));
    }

    #[test]
    fn a_tree_and_an_orphan_never_count_a_reused_parent_pid() {
        let procs: Table = [proc(1, 0, 1.0), proc(10, 1, 100.0), proc(11, 10, 200.0), proc(12, 11, 300.0), proc(13, 10, 50.0), proc(14, 999, 100.0), proc(15, 15, 100.0)].into();
        assert_eq!(tree(&procs, 10), [10, 11, 12]);
        assert_eq!(tree(&procs, 404), Vec::<i64>::new());
        assert!(orphan(&procs, 14) && orphan(&procs, 13) && !orphan(&procs, 11) && !orphan(&procs, 404));
    }

    #[test]
    fn ps_and_proc_are_read() {
        assert_eq!((clock_s("01:02.50"), clock_s("1:00:00"), clock_s("2-00:00:01")), (62.5, 3600.0, 172801.0));
        let ps = "    1     0   9000   0:01.50 1-00:00:00 /sbin/launchd\n  321     1  20480   1:02.00    10:00 /Applications/My App.app/Contents/MacOS/My App\nnot a row\n";
        let procs = mac_table(ps, 100_000.0);
        assert_eq!(procs[&1], Proc { pid: 1, ppid: 0, name: "launchd".into(), rss: 9_216_000, private: 9_216_000, threads: 0, handles: 0, cpu_s: 1.5, started: 13_600.0 });
        assert_eq!((procs[&321].name.as_str(), procs[&321].cpu_s, procs[&321].started, procs.len()), ("My App", 62.0, 99_400.0, 2));

        let root = std::env::temp_dir().join(format!("ducky-proc-{}", std::process::id()));
        let dir = root.join("42");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::create_dir_all(root.join("self")).unwrap();
        std::fs::write(root.join("stat"), "cpu 1 2 3\nbtime 1000\n").unwrap();
        let fields = "S 7 42 42 0 -1 4194304 100 0 0 0 250 50 0 0 20 0 3 0 500 1000000 200 18446744073709551615";
        std::fs::write(dir.join("stat"), format!("42 (a (long) name12) {fields}\n")).unwrap();
        std::fs::write(dir.join("status"), "Name:\ta (long) name12\nVmRSS:\t    2048 kB\nRssAnon:\t    1024 kB\n").unwrap();
        std::fs::write(dir.join("cmdline"), "/opt/x/a (long) name1234\0--flag\0").unwrap();
        let procs = linux_table(&root);
        std::fs::remove_dir_all(&root).unwrap();
        let expected = Proc { pid: 42, ppid: 7, name: "a (long) name1234".into(), rss: 2_097_152, private: 1_048_576, threads: 3, handles: 0, cpu_s: 3.0, started: 1005.0 };
        assert_eq!(procs, Table::from([(42, expected)]));
    }
}
