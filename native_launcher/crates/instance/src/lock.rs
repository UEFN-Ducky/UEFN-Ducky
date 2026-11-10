//! `Local\DuckyLauncher`: one launcher starts Ducky at a time. A second launch waits for the
//! first to finish starting (or die: an abandoned mutex is taken over at once, so only a
//! living owner is ever waited for, and only for the caller's `wait`), then hands off to
//! the Ducky it started. Ownership is per thread: drop the lock on the thread that took it.

use std::time::Duration;

pub const MUTEX_NAME: &str = "Local\\DuckyLauncher";

#[cfg(windows)]
mod sys {
    pub type Handle = isize;
    pub const ERROR_ALREADY_EXISTS: u32 = 183;
    pub const WAIT_OBJECT_0: u32 = 0;
    pub const WAIT_ABANDONED: u32 = 0x80;
    #[link(name = "kernel32")]
    extern "system" {
        pub fn CreateMutexW(attrs: *const core::ffi::c_void, initial_owner: i32, name: *const u16) -> Handle;
        pub fn GetLastError() -> u32;
        pub fn WaitForSingleObject(h: Handle, ms: u32) -> u32;
        pub fn ReleaseMutex(h: Handle) -> i32;
        pub fn CloseHandle(h: Handle) -> i32;
    }
}

pub struct InstanceLock {
    #[cfg(windows)]
    handle: isize,
    owned: bool,
}

impl InstanceLock {
    /// Take `Local\DuckyLauncher`, waiting up to `wait` for another launcher to finish.
    /// Never fails: on a timeout or error the launch goes on without it ([`Self::owned`] false).
    pub fn acquire(wait: Duration) -> InstanceLock {
        Self::acquire_named(MUTEX_NAME, wait)
    }

    pub fn acquire_named(name: &str, wait: Duration) -> InstanceLock {
        #[cfg(windows)]
        {
            let wide: Vec<u16> = name.encode_utf16().chain(std::iter::once(0)).collect();
            let handle = unsafe { sys::CreateMutexW(std::ptr::null(), 1, wide.as_ptr()) };
            if handle == 0 {
                return InstanceLock { handle, owned: false };
            }
            if unsafe { sys::GetLastError() } != sys::ERROR_ALREADY_EXISTS {
                return InstanceLock { handle, owned: true };
            }
            let ms = wait.as_millis().min(u32::MAX as u128 - 1) as u32;
            let r = unsafe { sys::WaitForSingleObject(handle, ms) };
            InstanceLock { handle, owned: r == sys::WAIT_OBJECT_0 || r == sys::WAIT_ABANDONED }
        }
        #[cfg(not(windows))]
        {
            let _ = (name, wait);
            InstanceLock { owned: true }
        }
    }

    /// True when this launch holds the lock (no other launcher is starting Ducky).
    pub fn owned(&self) -> bool {
        self.owned
    }
}

impl Drop for InstanceLock {
    fn drop(&mut self) {
        #[cfg(windows)]
        unsafe {
            if self.owned {
                sys::ReleaseMutex(self.handle);
            }
            if self.handle != 0 {
                sys::CloseHandle(self.handle);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc;
    use std::thread;

    fn unique(tag: &str) -> String {
        format!("Local\\DuckyLauncherTest-{tag}-{}", std::process::id())
    }

    #[test]
    fn a_second_launch_waits_then_gets_it() {
        let name = unique("wait");
        let (took, took_rx) = mpsc::channel();
        let (release, release_rx) = mpsc::channel::<()>();
        let n = name.clone();
        let first = thread::spawn(move || {
            let lock = InstanceLock::acquire_named(&n, Duration::ZERO);
            took.send(lock.owned()).unwrap();
            release_rx.recv().unwrap();
            drop(lock);
        });
        assert!(took_rx.recv().unwrap());
        let n = name.clone();
        let busy = thread::spawn(move || InstanceLock::acquire_named(&n, Duration::from_millis(100)).owned()).join().unwrap();
        #[cfg(windows)]
        assert!(!busy, "the first launcher still holds it");
        let _ = busy;
        release.send(()).unwrap();
        first.join().unwrap();
        let n = name.clone();
        assert!(thread::spawn(move || InstanceLock::acquire_named(&n, Duration::from_secs(5)).owned()).join().unwrap());
    }

    #[test]
    fn a_dead_launchers_lock_is_taken_over() {
        let name = unique("abandoned");
        let n = name.clone();
        thread::spawn(move || std::mem::forget(InstanceLock::acquire_named(&n, Duration::ZERO))).join().unwrap();
        let n = name.clone();
        assert!(thread::spawn(move || InstanceLock::acquire_named(&n, Duration::from_secs(5)).owned()).join().unwrap());
    }
}
