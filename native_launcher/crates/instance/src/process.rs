//! Is a pid a living Ducky? Read-only queries: the launcher never terminates a process.

#[cfg(windows)]
mod sys {
    pub type Handle = isize;
    pub const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;
    pub const STILL_ACTIVE: u32 = 259;
    #[link(name = "kernel32")]
    extern "system" {
        pub fn OpenProcess(access: u32, inherit: i32, pid: u32) -> Handle;
        pub fn GetExitCodeProcess(h: Handle, code: *mut u32) -> i32;
        pub fn QueryFullProcessImageNameW(h: Handle, flags: u32, buf: *mut u16, size: *mut u32) -> i32;
        pub fn CloseHandle(h: Handle) -> i32;
    }

    pub struct Proc(pub Handle);
    impl Proc {
        pub fn open(pid: u32) -> Option<Proc> {
            let h = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
            (h != 0).then_some(Proc(h))
        }
    }
    impl Drop for Proc {
        fn drop(&mut self) {
            unsafe { CloseHandle(self.0) };
        }
    }
}

/// True when `pid` is running. An exited process that something still holds open is not
/// (`open_files.panel_process_alive`: exit code must be STILL_ACTIVE).
pub fn pid_alive(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    #[cfg(windows)]
    {
        let Some(p) = sys::Proc::open(pid) else { return false };
        let mut code = 0u32;
        (unsafe { sys::GetExitCodeProcess(p.0, &mut code) }) != 0 && code == sys::STILL_ACTIVE
    }
    #[cfg(not(windows))]
    {
        std::path::Path::new(&format!("/proc/{pid}")).exists()
    }
}

/// The running process's EXE file name, when Windows tells it.
pub fn image_name(pid: u32) -> Option<String> {
    #[cfg(windows)]
    {
        let p = sys::Proc::open(pid)?;
        let mut buf = vec![0u16; 1024];
        let mut size = buf.len() as u32;
        if unsafe { sys::QueryFullProcessImageNameW(p.0, 0, buf.as_mut_ptr(), &mut size) } == 0 {
            return None;
        }
        let full = String::from_utf16_lossy(&buf[..size as usize]);
        full.rsplit(['\\', '/']).next().map(str::to_string)
    }
    #[cfg(not(windows))]
    {
        let _ = pid;
        None
    }
}

/// `UEFN-Ducky*` names (the panel EXE and its versioned copies), as `_ducky_process_alive`.
pub fn is_ducky_name(name: &str) -> bool {
    name.to_ascii_lowercase().starts_with("uefn-ducky")
}

/// `pid` is a running Ducky. Alive with a name we can't read counts as Ducky: not knowing
/// its name is no reason to give up on it (`webview_app._ducky_process_alive`).
pub fn is_ducky(pid: u32) -> bool {
    pid_alive(pid) && image_name(pid).map_or(true, |n| is_ducky_name(&n))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn this_process_is_alive_and_not_ducky() {
        let me = std::process::id();
        assert!(pid_alive(me));
        #[cfg(windows)]
        {
            let name = image_name(me).expect("own image name");
            assert!(name.to_ascii_lowercase().ends_with(".exe"), "{name}");
            assert!(!is_ducky(me));
        }
    }

    #[cfg(windows)]
    #[test]
    fn an_exited_child_is_not_alive_while_its_handle_is_open() {
        let mut child = std::process::Command::new("cmd").args(["/c", "exit 0"]).spawn().unwrap();
        let pid = child.id();
        assert!(child.wait().unwrap().success());
        assert!(!pid_alive(pid)); // `child` still holds its handle
        drop(child);
    }

    #[test]
    fn zero_is_never_alive() {
        assert!(!pid_alive(0));
    }

    #[test]
    fn ducky_names() {
        assert!(is_ducky_name("UEFN-Ducky.exe"));
        assert!(is_ducky_name("uefn-ducky-1.2.471.exe"));
        assert!(!is_ducky_name("Ducky-Launcher.exe"));
        assert!(!is_ducky_name("python.exe"));
    }
}
