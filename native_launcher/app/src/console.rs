//! Text output for a GUI-subsystem EXE: stdout when it is redirected (a pipe or file),
//! else the parent's console, else nothing. Never opens a window.

use std::io::Write;

#[cfg(windows)]
mod sys {
    pub type Handle = isize;
    pub const STD_OUTPUT_HANDLE: u32 = -11i32 as u32;
    pub const ATTACH_PARENT_PROCESS: u32 = u32::MAX;
    #[link(name = "kernel32")]
    extern "system" {
        pub fn GetStdHandle(n: u32) -> Handle;
        pub fn AttachConsole(pid: u32) -> i32;
    }
}

/// Print one line where the person or the calling script can read it.
pub fn print_line(text: &str) {
    let line = format!("{text}\n");
    #[cfg(windows)]
    {
        let handle = unsafe { sys::GetStdHandle(sys::STD_OUTPUT_HANDLE) };
        if (handle == 0 || handle == -1) && unsafe { sys::AttachConsole(sys::ATTACH_PARENT_PROCESS) } != 0 {
            if let Ok(mut con) = std::fs::OpenOptions::new().write(true).open("CONOUT$") {
                let _ = con.write_all(line.as_bytes());
                return;
            }
        }
    }
    let mut out = std::io::stdout();
    let _ = out.write_all(line.as_bytes());
    let _ = out.flush();
}
