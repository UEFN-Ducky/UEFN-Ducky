//! Lore's own library (`urc/lore_ffi.py`): `lore_file_unstage`, `lore_file_reset`,
//! `lore_repository_status` and `lore_revision_revert`. The library is taken out of
//! the `URC.vsix` UEFN installs (it is never shipped with Ducky). Never a check-in.
//!
//! A `LoreString`'s length is its UTF-8 bytes without a NUL: with one, the library
//! answers "Repository not found".
//! ponytail: the library is Windows x64 only, as UEFN is; elsewhere every call says the
//! `URC.vsix` is not there. One call at a time: the events of a call are kept in one list.

use std::ffi::c_void;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, PoisonError};

use serde_json::{json, Map, Value};

use super::lore_host::Backend;
use super::urc::{shown, DRIVES};
use crate::store::pyjson::{pystr, truthy};

type Fields = Map<String, Value>;

const VSIX_MEMBER: &str = "extension/node_modules/@lore-vcs/sdk-amd64-unknown-windows/lorelib-amd64-unknown-windows.dll";
const NO_VSIX: &str = "URC.vsix not found — install UEFN";
const TAG_ERROR: u32 = 1;
const TAG_COMPLETE: u32 = 2;
const TAG_STATUS_REV: u32 = 151;
const TAG_STATUS_FILE: u32 = 152;
/// In `LoreRepositoryStatusRevisionEventData`: two 16-byte ids, a string, hashes...
const STATUS_REV_LOCAL_AHEAD_OFF: usize = 264;
const ACTION_ADD: u64 = 1;

/// The events of the call that is running, and the turn of one call at a time.
static EVENTS: Mutex<Vec<Fields>> = Mutex::new(Vec::new());
static TURN: Mutex<()> = Mutex::new(());

#[repr(C)]
#[derive(Clone, Copy)]
struct LoreString {
    string: *const u8,
    length: usize,
}

impl LoreString {
    const NONE: Self = LoreString { string: std::ptr::null(), length: 0 };

    /// `text` must outlive every use of the string.
    fn of(text: &str) -> Self {
        LoreString { string: text.as_ptr(), length: text.len() }
    }
}

// The fields of the structures below are read by the library, not here.
#[repr(C)]
#[derive(Clone, Copy)]
#[allow(dead_code)]
struct LoreStringArray {
    ptr: *const LoreString,
    count: usize,
}

#[repr(C)]
#[allow(dead_code)]
struct LoreGlobalArgs {
    repository_path: LoreString,
    correlation_id: LoreString,
    identity: LoreString,
    force: u8,
    offline: u8,
    local: u8,
    remote: u8,
    dry_run: u8,
    no_atime: u8,
    max_connections: u32,
    search_limit: u32,
    search_nearest: u8,
    no_gc: u8,
    in_memory: u8,
    file_count_limit: u64,
    file_size_limit: u64,
    compress_task_limit: u64,
    store_keep_alive: u8,
    store_keep_alive_seconds: u64,
    sync_data: u8,
    cache: u8,
}

#[repr(C)]
#[allow(dead_code)]
struct LoreFileUnstageArgs {
    paths: LoreStringArray,
}

#[repr(C)]
#[allow(dead_code)]
struct LoreFileResetArgs {
    paths: LoreStringArray,
    revision: LoreString,
    purge: u8,
}

#[repr(C)]
#[allow(dead_code)]
struct LoreRepositoryStatusArgs {
    staged: u8,
    scan: u8,
    check_dirty: u8,
    reset: u8,
    sync_point: u8,
    revision_only: u8,
    count: u8,
    paths: LoreStringArray,
}

#[repr(C)]
#[allow(dead_code)]
struct LoreRevisionRevertArgs {
    revision: LoreString,
    message: LoreString,
    no_commit: u8,
}

type Callback = extern "C" fn(event: *const u8, context: u64);

#[repr(C)]
#[derive(Clone, Copy)]
#[allow(dead_code)]
struct LoreEventCallback {
    user_context: u64,
    callback: Callback,
}

/// One of the library's four calls.
type Call<A> = unsafe extern "C" fn(*const LoreGlobalArgs, *const A, LoreEventCallback) -> i32;

/// The string at `data + offset`, up to its first NUL.
///
/// # Safety
/// `data + offset` holds a `LoreString` the library filled.
unsafe fn lore_str(data: *const u8, offset: usize) -> String {
    let string = data.add(offset).cast::<LoreString>().read_unaligned();
    if string.string.is_null() || string.length == 0 {
        return String::new();
    }
    let text = String::from_utf8_lossy(std::slice::from_raw_parts(string.string, string.length));
    text.split('\0').next().unwrap_or_default().to_string()
}

/// What an event says, by its tag.
///
/// # Safety
/// `event` is an event the library handed to its callback.
unsafe fn parse_event(event: *const u8) -> Fields {
    let tag = event.cast::<u32>().read_unaligned();
    let data = event.add(8);
    let number = |offset: usize| data.add(offset).cast::<u32>().read_unaligned();
    let told = match tag {
        TAG_ERROR => json!({"tag": tag, "errorType": number(0), "errorInner": lore_str(data, 8)}),
        TAG_COMPLETE => json!({"tag": tag, "status": number(0) as i32, "errorCode": number(8) as i32, "message": lore_str(data, 16)}),
        TAG_STATUS_FILE => json!({"tag": tag, "path": lore_str(data, 0), "action": number(24), "flagStaged": *data.add(32), "flagDirty": *data.add(39)}),
        TAG_STATUS_REV => json!({"tag": tag, "isLocalAhead": *data.add(STATUS_REV_LOCAL_AHEAD_OFF) != 0, "branchName": lore_str(data, 32)}),
        _ => json!({"tag": tag}),
    };
    told.as_object().cloned().unwrap_or_default()
}

extern "C" fn on_event(event: *const u8, _context: u64) {
    if !event.is_null() {
        // SAFETY: the library calls this with an event that lives for the call.
        let told = unsafe { parse_event(event) };
        EVENTS.lock().unwrap_or_else(PoisonError::into_inner).push(told);
    }
}

/// The last message the events carry, a call's own before an error's.
fn message(events: &[Fields]) -> String {
    let said = |event: &Fields| ["message", "errorInner"].iter().filter_map(|key| event.get(*key)).find(|said| truthy(said)).map(pystr);
    events.iter().filter_map(said).next_back().unwrap_or_default()
}

/// `_complete`.
fn complete(rc: i32, events: &[Fields]) -> Value {
    json!({"rc": rc, "ok": rc == 0, "message": message(events), "events": events})
}

/// `_status_from_events`: the changelist as the status call's events list it.
fn status_from_events(rc: i32, events: &[Fields]) -> Value {
    let (mut paths, mut staged, mut untracked) = (Vec::new(), Vec::new(), Vec::new());
    let (mut local_ahead, mut branch) = (false, String::new());
    for event in events {
        let tag = event.get("tag").and_then(Value::as_u64).unwrap_or_default();
        if tag == u64::from(TAG_STATUS_REV) {
            local_ahead = event.get("isLocalAhead").is_some_and(truthy);
            branch = event.get("branchName").map(pystr).unwrap_or_default();
        }
        let rel = event.get("path").map(pystr).unwrap_or_default().replace('\\', "/").trim_start_matches('/').to_string();
        if tag != u64::from(TAG_STATUS_FILE) || rel.is_empty() {
            continue;
        }
        if ["flagStaged", "flagDirty"].iter().any(|flag| event.get(*flag).is_some_and(truthy)) {
            staged.push(rel.clone());
        }
        if event.get("action").and_then(Value::as_u64) == Some(ACTION_ADD) {
            untracked.push(rel.clone());
        }
        paths.push(rel);
    }
    json!({"ok": rc == 0, "status_rc": rc, "message": message(events), "paths": paths, "staged": staged, "untracked": untracked, "is_local_ahead": local_ahead, "branch": branch})
}

fn find_urc_vsix() -> Option<PathBuf> {
    let under = |base: PathBuf| base.join("Epic Games").join("Fortnite").join("VSCode").join("URC.vsix");
    let named = ["ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"].into_iter().filter_map(std::env::var_os).filter(|base| !base.is_empty()).map(PathBuf::from);
    let drives = DRIVES.chars().map(|letter| PathBuf::from(format!("{letter}:/Program Files")));
    named.chain(drives).map(under).find(|vsix| vsix.is_file())
}

/// `ensure_lorelib`: the library under Ducky's data folder, taken out of the vsix once.
#[allow(clippy::disallowed_methods)] // reads and writes files: runs inside spawn_blocking
fn ensure_lorelib(data_dir: &Path) -> Result<PathBuf, String> {
    let dest = data_dir.join("lorelib").join("lorelib.dll");
    if dest.metadata().is_ok_and(|found| found.is_file() && found.len() > 1_000_000) {
        return Ok(dest);
    }
    let vsix = find_urc_vsix().ok_or(NO_VSIX)?;
    let mut bytes = Vec::new();
    let mut pack = std::fs::File::open(&vsix).map_err(|error| error.to_string()).and_then(|file| zip::ZipArchive::new(file).map_err(|error| error.to_string()))?;
    pack.by_name(VSIX_MEMBER).map_err(|error| error.to_string())?.read_to_end(&mut bytes).map_err(|error| error.to_string())?;
    std::fs::create_dir_all(dest.parent().unwrap_or(data_dir)).and_then(|()| std::fs::write(&dest, bytes)).map_err(|error| error.to_string())?;
    Ok(dest)
}

#[cfg(windows)]
#[link(name = "kernel32")]
extern "system" {
    fn LoadLibraryW(name: *const u16) -> *mut c_void;
    fn GetProcAddress(module: *mut c_void, name: *const u8) -> *mut c_void;
}

/// The address of one of the library's calls; `name` ends with a NUL.
#[cfg(windows)]
fn function(data_dir: &Path, name: &[u8]) -> Result<*mut c_void, String> {
    use std::os::windows::ffi::OsStrExt;
    let library = ensure_lorelib(data_dir)?;
    let wide: Vec<u16> = library.as_os_str().encode_wide().chain([0]).collect();
    // SAFETY: both names end with a NUL and live past the calls.
    let found = unsafe {
        let module = LoadLibraryW(wide.as_ptr());
        if module.is_null() { module } else { GetProcAddress(module, name.as_ptr()) }
    };
    if found.is_null() {
        return Err(format!("{} could not be loaded: {}", shown(&library), std::io::Error::last_os_error()));
    }
    Ok(found)
}

#[cfg(not(windows))]
fn function(data_dir: &Path, _name: &[u8]) -> Result<*mut c_void, String> {
    ensure_lorelib(data_dir).and(Err(NO_VSIX.into()))
}

/// Lore's library, loaded from Ducky's data folder.
pub struct Ffi {
    pub data_dir: PathBuf,
}

impl Ffi {
    /// One call of the library: its return code and the events it sent meanwhile.
    fn call<A>(&self, name: &[u8], globals: &LoreGlobalArgs, args: &A) -> Result<(i32, Vec<Fields>), String> {
        let function = function(&self.data_dir, name)?;
        let _turn = TURN.lock().unwrap_or_else(PoisonError::into_inner);
        EVENTS.lock().unwrap_or_else(PoisonError::into_inner).clear();
        // SAFETY: `function` is the library's export of this name, whose arguments are
        // these structures (their layout is checked by the tests below); they and the
        // strings they point at live until it returns.
        let rc = unsafe {
            let call = std::mem::transmute::<*mut c_void, Call<A>>(function);
            call(globals, args, LoreEventCallback { user_context: 0, callback: on_event })
        };
        Ok((rc, std::mem::take(&mut *EVENTS.lock().unwrap_or_else(PoisonError::into_inner))))
    }
}

/// The official client's unstage and reset pass the repository's path only.
fn globals(root: &str, offline: u8) -> LoreGlobalArgs {
    // SAFETY: all-zero is every field's "not set".
    let mut globals: LoreGlobalArgs = unsafe { std::mem::zeroed() };
    globals.repository_path = LoreString::of(root);
    globals.offline = offline;
    globals
}

impl Backend for Ffi {
    fn status(&self, root: &str) -> Result<Value, String> {
        let root = shown(Path::new(root));
        let args = LoreRepositoryStatusArgs { staged: 1, scan: 1, check_dirty: 1, reset: 0, sync_point: 1, revision_only: 0, count: 0, paths: LoreStringArray { ptr: std::ptr::null(), count: 0 } };
        let mut last = Value::Null;
        // Without the server first; with it when that fails.
        for offline in [1, 0] {
            let (rc, events) = self.call(b"lore_repository_status\0", &globals(&root, offline), &args)?;
            last = status_from_events(rc, &events);
            if rc == 0 {
                break;
            }
        }
        Ok(last)
    }

    fn drop_paths(&self, root: &str, paths: &[String]) -> Result<Value, String> {
        let root = shown(Path::new(root));
        let spelled: Vec<String> = paths.iter().map(|path| path.replace('\\', "/")).collect();
        let items: Vec<LoreString> = spelled.iter().map(|path| LoreString::of(path)).collect();
        let (array, globals) = (LoreStringArray { ptr: items.as_ptr(), count: items.len() }, globals(&root, 0));
        let (unstage_rc, unstaged) = self.call(b"lore_file_unstage\0", &globals, &LoreFileUnstageArgs { paths: array })?;
        let (reset_rc, reset) = self.call(b"lore_file_reset\0", &globals, &LoreFileResetArgs { paths: array, revision: LoreString::NONE, purge: 1 })?;
        Ok(json!({"ok": unstage_rc == 0 || reset_rc == 0, "dropped": paths, "unstage_rc": unstage_rc, "reset_rc": reset_rc, "unstage_message": message(&unstaged), "reset_message": message(&reset)}))
    }

    fn revert_local(&self, root: &str) -> Result<Value, String> {
        let root = shown(Path::new(root));
        let args = LoreRevisionRevertArgs { revision: LoreString::NONE, message: LoreString::NONE, no_commit: 1 };
        let (rc, events) = self.call(b"lore_revision_revert\0", &globals(&root, 0), &args)?;
        Ok(complete(rc, &events))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::mem::size_of;

    #[cfg(target_pointer_width = "64")]
    #[test]
    fn the_structures_are_laid_out_as_the_library_reads_them() {
        let sizes = [size_of::<LoreString>(), size_of::<LoreStringArray>(), size_of::<LoreGlobalArgs>(), size_of::<LoreFileUnstageArgs>(), size_of::<LoreFileResetArgs>(), size_of::<LoreRepositoryStatusArgs>(), size_of::<LoreRevisionRevertArgs>(), size_of::<LoreEventCallback>()];
        assert_eq!(sizes, [16, 16, 120, 16, 40, 24, 40, 16]);
        // A string is its UTF-8 bytes, with no NUL counted.
        assert_eq!(LoreString::of("C:\\é").length, 5);
    }

    /// An event as the library lays it out: its tag, then `data` 8 bytes in.
    fn event(tag: u32, size: usize, fill: impl Fn(&mut [u8])) -> Vec<u8> {
        let mut bytes = vec![0; 8 + size];
        bytes[..4].copy_from_slice(&tag.to_ne_bytes());
        fill(&mut bytes[8..]);
        bytes
    }

    fn put(data: &mut [u8], offset: usize, text: &[u8]) {
        data[offset..offset + size_of::<usize>()].copy_from_slice(&(text.as_ptr() as usize).to_ne_bytes());
        data[offset + 8..offset + 8 + size_of::<usize>()].copy_from_slice(&text.len().to_ne_bytes());
    }

    #[cfg(target_pointer_width = "64")]
    #[test]
    fn events_are_read_by_their_tag_and_make_the_status() {
        let file = |path: &'static [u8], action: u32, staged: u8| {
            event(TAG_STATUS_FILE, 40, |data| {
                put(data, 0, path);
                data[24..28].copy_from_slice(&action.to_ne_bytes());
                data[32] = staged;
            })
        };
        let rev = event(TAG_STATUS_REV, 272, |data| {
            put(data, 32, b"main-1\0junk");
            data[STATUS_REV_LOCAL_AHEAD_OFF] = 1;
        });
        let done = event(TAG_COMPLETE, 32, |data| put(data, 16, "fini é".as_bytes()));
        let failed = event(TAG_ERROR, 24, |data| put(data, 8, b"no server"));
        let raw = [rev, file(b"\\Content\\New.uasset", 1, 0), file(b"Content/Old.uasset", 2, 1), file(b"", 1, 1), done, failed, event(7, 0, |_| ())];
        // SAFETY: each buffer is laid out as the event of its tag.
        let events: Vec<Fields> = raw.iter().map(|bytes| unsafe { parse_event(bytes.as_ptr()) }).collect();
        assert_eq!(events[0], *json!({"tag": 151, "isLocalAhead": true, "branchName": "main-1"}).as_object().unwrap());
        assert_eq!(events[6], *json!({"tag": 7}).as_object().unwrap());
        let status = status_from_events(0, &events);
        let want = json!({"ok": true, "status_rc": 0, "message": "no server", "paths": ["Content/New.uasset", "Content/Old.uasset"], "staged": ["Content/Old.uasset"], "untracked": ["Content/New.uasset"], "is_local_ahead": true, "branch": "main-1"});
        assert_eq!(status, want);
        assert_eq!(complete(3, &events[4..5]), json!({"rc": 3, "ok": false, "message": "fini é", "events": [{"tag": 2, "status": 0, "errorCode": 0, "message": "fini é"}]}));
    }
}
