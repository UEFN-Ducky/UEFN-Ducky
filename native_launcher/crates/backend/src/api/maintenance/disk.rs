//! The App Data folder on disk: what is in it, how big, and the deletes.
//!
//! A delete is permanent, so a path is only ever built by [`resolve_rel`] (inside the
//! folder, no `.` or `..`) or from a project id that is one plain name ([`plain_name`]).
//! A link is removed itself and never followed: a plugin folder is a junction to its
//! copy under `scopes`, and that copy is counted and deleted there, once.
#![allow(clippy::disallowed_methods)] // the whole module runs inside spawn_blocking

use std::collections::BTreeSet;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value};

use crate::api::file_kinds::file_name;
use crate::api::project_paths::{normalize, project_slug, resolve, same, text};

/// Folders that hold one folder per project id, and what the page calls each.
pub const PROJECT_AREAS: [(&str, &str); 6] = [
    ("chats/projects", "Chats"),
    ("workspace/projects", "Workspace"),
    ("file_history", "File history"),
    ("verse_diagnostics", "Diagnostics"),
    ("changesets", "Changesets"),
    ("memory/projects", "Memory"),
];
const CHILD_LIMIT: usize = 80;

/// name, label, description, kind (cache | user | install | runtime | settings | other).
const KNOWN: &[(&str, &str, &str, &str)] = &[
    ("chats", "Chat files", "Per-chat pipeline run files (and leftover pre-database conversations).", "user"),
    ("workspace", "Workspace leftover", "Pre-database editor layout. Now in ducky.db.", "cache"),
    ("file_history", "File history leftover", "Pre-database undo snapshots. Now in ducky.db.", "cache"),
    ("verse_diagnostics", "Diagnostics leftover", "Pre-database Verse scan cache. Now in ducky.db.", "cache"),
    ("changesets", "Changesets leftover", "Pre-database write ledger. Now in ducky.db.", "cache"),
    ("memory", "Memory leftover", "Pre-database notes. Now in ducky.db.", "cache"),
    ("tool_captures", "Captures", "Screenshots and snips from tools.", "cache"),
    ("generated_images", "Generated images", "Gateway and agent image generations kept for chat reuse.", "user"),
    ("tool_spills", "Tool spills", "Oversized nested MCP results spilled off the model context.", "cache"),
    ("backups", "Backups leftover", "Old JSON .bak copies. Snapshots replaced this.", "cache"),
    ("listener", "Listener", "UEFN Python listener shipped here. Recreated on app start.", "runtime"),
    ("skill_packs", "Skill packs", "Installed skill packs.", "install"),
    ("uefn_plugins", "Desktop plugins", "Installed Store plugins (each plugin stays in its own folder).", "install"),
    ("mcp_plugins", "MCP plugins leftover", "Legacy MCP packs. Unused after Store plugins.", "cache"),
    ("ai_plugins", "AI plugin drafts", "In-progress plugin drafts.", "install"),
    ("coding_agents", "Coding agents", "Shared CLI agent temp files and bridge copies.", "cache"),
    ("verse-lsp", "Verse LSP", "Language server cache.", "cache"),
    ("plan_templates", "Plan templates leftover", "Pre-database templates. Now in ducky.db.", "cache"),
    ("verse_templates", "Verse templates", "Custom Verse templates.", "user"),
    ("automation_templates", "Automation templates", "Custom Automations graphs.", "user"),
    ("perf", "Perf leftover", "Pre-database traces. Now events in ducky.db.", "cache"),
    ("tasks", "Tasks leftover", "Pre-database task files. Now in ducky.db.", "cache"),
    ("sounds", "Sounds", "Custom notification audio.", "user"),
    ("duckies", "Custom duckies", "Custom ducky avatars.", "user"),
    ("uefn_plugin_cache", "Plugin cache leftover", "Now plugin_kv rows in ducky.db.", "cache"),
    ("uefn_plugin_prefs", "Plugin prefs leftover", "Now plugin_kv rows in ducky.db.", "cache"),
    ("webview2_browser", "In-app browser", "Embedded browser profile (Discord login, cookies).", "cache"),
    ("panel_settings.json", "Settings leftover", "Pre-database settings. Now rows in ducky.db.", "cache"),
    ("config.json", "Config", "Listener config JSON (projection).", "settings"),
    ("credentials.dat", "Credentials leftover", "Pre-database keys. Now secrets rows.", "cache"),
    ("recent_projects.json", "Recent leftover", "Pre-database project list. Now in ducky.db.", "cache"),
    ("workspace_dock.json", "Dock leftover", "Pre-database dock layout. Now in ducky.db.", "cache"),
    ("models_cache.json", "Models leftover", "Pre-database catalog. Now cache_docs in ducky.db.", "cache"),
    ("agent_crashes.jsonl", "Crashes leftover", "Pre-database crash log. Now events in ducky.db.", "cache"),
    ("uefn_plugin_load_errors.jsonl", "Plugin errors leftover", "Now events in ducky.db.", "cache"),
    ("mcp.json", "MCP config", "MCP server list export. Rows in ducky.db are source of truth.", "settings"),
    ("pyinstaller-work", "Build scratch", "PyInstaller work dir. Does not belong in AppData.", "cache"),
    ("ship_stamp.json", "Ship stamp", "Last listener/skill deploy stamp.", "runtime"),
    ("setup-progress.txt", "Setup progress", "Installer host progress file.", "runtime"),
    ("panel.pid", "Panel lock", "Running panel process id.", "runtime"),
    // The store and its sidecars. Never clearable, never deletable.
    ("ducky.db", "Database", "All app state (chats, settings, ledger, plans, memory).", "settings"),
    ("ducky.db-wal", "Database log", "SQLite write-ahead log for ducky.db.", "settings"),
    ("ducky.db-shm", "Database index", "SQLite shared-memory index for ducky.db.", "settings"),
    ("snapshots", "Database snapshots", "Consistent copies of ducky.db (newest 3).", "settings"),
    ("legacy", "Legacy stores", "Pre-database files kept until three clean boots.", "cache"),
    ("exports", "Exports", "Tables exported from Settings → App Data → Database.", "user"),
    (
        "scopes",
        "Plugin data",
        "Plugins' data per account and team, and the copies of folders plugins share with a team. Change it from each plugin's bar.",
        "user",
    ),
];
const UNLISTED: &str = "Unlisted AppData item.";
const LOCAL_COPIES: (&str, &str, &str) =
    ("scopes/_pc", "Local folder copies", "This PC's copy of each plugin folder shared with a team; every account on this PC uses it.");
const FOLDER_COPY: (&str, &str) = ("Folder copies", "One plugin's copies of the folders it shares, as it writes them.");
const JUNCTION: &str = "Points at this folder's copy under Plugin data (scopes); its files are counted there.";
const PROTECTED: [&str; 10] =
    ["credentials.dat", "panel_settings.json", "config.json", "mcp.json", "listener", "ducky.db", "ducky.db-wal", "ducky.db-shm", "snapshots", "scopes"];

/// What a path is when it is a link.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Link {
    No,
    Symlink,
    /// A Windows junction (`os.path.isjunction`).
    Junction,
}

pub fn link(path: &Path) -> Link {
    match fs::symlink_metadata(path) {
        Ok(found) if found.file_type().is_symlink() => {
            if junction(path) {
                Link::Junction
            } else {
                Link::Symlink
            }
        }
        _ => Link::No,
    }
}

#[cfg(not(windows))]
fn junction(_path: &Path) -> bool {
    false
}

/// The link's reparse tag says so: Rust calls a junction a symlink, Python does not.
#[cfg(windows)]
fn junction(path: &Path) -> bool {
    use std::os::windows::ffi::OsStrExt;
    const MOUNT_POINT: u32 = 0xA000_0003;
    #[repr(C)]
    #[allow(dead_code)] // only the tag is read
    struct Found {
        attributes: u32,
        times: [u32; 6],
        size: [u32; 2],
        tag: u32,
        reserved: u32,
        name: [u16; 260],
        short: [u16; 14],
    }
    #[link(name = "kernel32")]
    extern "system" {
        fn FindFirstFileW(name: *const u16, found: *mut Found) -> isize;
        fn FindClose(handle: isize) -> i32;
    }
    // A long path is only read in its `\\?\` form.
    let plain = text(path);
    let long = match plain.strip_prefix(r"\\") {
        Some(_) if plain.starts_with(r"\\?\") => plain,
        Some(share) => format!(r"\\?\UNC\{share}"),
        None => format!(r"\\?\{plain}"),
    };
    let wide: Vec<u16> = std::ffi::OsStr::new(&long).encode_wide().chain([0]).collect();
    let mut found = Found { attributes: 0, times: [0; 6], size: [0; 2], tag: 0, reserved: 0, name: [0; 260], short: [0; 14] };
    // SAFETY: `wide` ends with a 0 and `found` is the struct the call fills.
    let handle = unsafe { FindFirstFileW(wide.as_ptr(), &mut found) };
    if handle == -1 {
        return false;
    }
    // SAFETY: the handle came from FindFirstFileW just above.
    unsafe { FindClose(handle) };
    found.tag == MOUNT_POINT
}

/// `os.unlink`: a file, or a link to a folder (Windows removes that as a folder).
pub fn unlink(path: &Path) -> io::Result<()> {
    fs::remove_file(path).or_else(|failed| if cfg!(windows) && link(path) != Link::No { fs::remove_dir(path) } else { Err(failed) })
}

#[cfg(unix)]
fn writable(path: &Path) -> io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o666))
}

#[cfg(not(unix))]
fn writable(path: &Path) -> io::Result<()> {
    let mut permissions = fs::symlink_metadata(path)?.permissions();
    #[allow(clippy::permissions_set_readonly_false)] // Windows: only the read-only flag
    permissions.set_readonly(false);
    fs::set_permissions(path, permissions)
}

/// A removal that is refused runs once more with the path made writable.
fn removed(path: &Path, remove: fn(&Path) -> io::Result<()>) -> io::Result<()> {
    remove(path).or_else(|_| {
        writable(path)?;
        remove(path)
    })
}

/// `shutil.rmtree`: stops at the first thing that will not go.
fn remove_tree(path: &Path) -> io::Result<()> {
    for entry in fs::read_dir(path)? {
        let entry = entry?;
        // A link inside is never a folder here: it goes itself.
        if entry.file_type()?.is_dir() {
            remove_tree(&entry.path())?;
        } else {
            removed(&entry.path(), unlink)?;
        }
    }
    removed(path, |path| fs::remove_dir(path))
}

/// `_safe_rmtree`: delete a folder. A junction is removed itself; a symlink is refused.
pub fn safe_rmtree(path: &Path) -> bool {
    match link(path) {
        Link::Junction => fs::remove_dir(path).is_ok(),
        Link::Symlink => false,
        Link::No => remove_tree(path).is_ok(),
    }
}

/// One path part that names something inside its folder: not empty, `.` or `..`, nor a
/// name Windows reads as one of those (it drops trailing dots and spaces).
fn plain_part(part: &str) -> bool {
    !part.trim_end_matches(['.', ' ']).is_empty()
}

/// A project id usable as one folder name.
pub fn plain_name(name: &str) -> bool {
    plain_part(name) && !name.contains(['/', '\\', ':'])
}

fn clean(rel: &str) -> String {
    rel.replace('\\', "/").trim_matches('/').to_string()
}

/// `path` under `root` with `/` between its parts; None when it is not inside.
fn rel_to(path: &Path, root: &Path) -> Option<String> {
    let mut parts = path.components();
    for want in root.components() {
        if !parts.next().is_some_and(|have| same(Path::new(have.as_os_str()), Path::new(want.as_os_str()))) {
            return None;
        }
    }
    Some(parts.map(|part| part.as_os_str().to_string_lossy().into_owned()).collect::<Vec<_>>().join("/"))
}

/// `resolve_appdata_rel`: `rel` under `root` (resolved), None when it would leave it.
/// The last part is never resolved: a junction is that link, not the copy it points at.
pub fn resolve_rel(root: &Path, rel: &str) -> Option<PathBuf> {
    let raw = clean(rel);
    if raw.is_empty() {
        return Some(root.to_path_buf());
    }
    if !raw.split('/').all(plain_part) {
        return None;
    }
    let full = root.join(&raw);
    let path = resolve(full.parent()?).join(raw.rsplit('/').next()?);
    rel_to(&path, root).filter(|inside| !inside.is_empty()).map(|_| path)
}

/// `_walk_stats`: (bytes, files, folders) without following a link.
pub fn walk_stats(path: &Path) -> (u64, u64, u64) {
    let (mut size, mut files, mut dirs) = (0, 0, 0);
    if link(path) == Link::Junction {
        return (0, 0, 0);
    }
    let mut stack = vec![path.to_path_buf()];
    while let Some(current) = stack.pop() {
        for entry in fs::read_dir(&current).into_iter().flatten().flatten() {
            let Ok(kind) = entry.file_type() else { continue };
            if kind.is_dir() {
                dirs += 1;
                stack.push(entry.path());
            } else if kind.is_file() {
                files += 1;
                size += entry.metadata().map_or(0, |found| found.len());
            }
        }
    }
    (size, files, dirs)
}

fn name_of(path: &Path) -> String {
    path.file_name().map(|name| name.to_string_lossy().into_owned()).unwrap_or_default()
}

/// `_rel_to_root`.
fn rel_of(path: &Path, root: &Path) -> String {
    let name = name_of(path);
    path.parent().and_then(|parent| rel_to(&resolve(parent).join(&name), root)).unwrap_or(name)
}

fn describe(name: &str) -> (&str, &'static str, &'static str) {
    match KNOWN.iter().find(|known| known.0 == name) {
        Some(known) => (known.1, known.2, known.3),
        None => (name, UNLISTED, "other"),
    }
}

/// `_item_dict`: one row of the page.
fn item(path: &Path, root: &Path, nested: bool) -> Value {
    let name = name_of(path);
    let rel = rel_of(path, root);
    let (mut label, mut description, mut kind) = describe(&name);
    let open;
    if nested {
        // A nested row keeps its own name; its kind and its lock are its top folder's.
        let top = if rel.is_empty() { name.as_str() } else { rel.split('/').next().unwrap_or_default() };
        (_, description, kind) = describe(top);
        label = &name;
        open = !PROTECTED.contains(&top);
        if rel == LOCAL_COPIES.0 {
            (label, description) = (LOCAL_COPIES.1, LOCAL_COPIES.2);
        } else if top == "scopes" && rel.ends_with("/folders") {
            (label, description) = FOLDER_COPY;
        }
    } else {
        open = !(PROTECTED.contains(&name.as_str()) || kind == "runtime" || kind == "settings");
    }
    let linked = link(path);
    if linked == Link::Junction {
        description = JUNCTION;
    }
    let is_dir = linked == Link::No && path.is_dir();
    let (size, files, dirs) = if is_dir {
        walk_stats(path)
    } else if path.is_file() {
        (fs::metadata(path).map_or(0, |found| found.len()), 1, 0)
    } else {
        (0, 0, 0)
    };
    json!({
        "name": name, "rel": rel, "is_dir": is_dir, "bytes": size, "files": files, "dirs": dirs, "kind": kind,
        "label": label, "description": description, "clearable": open, "deletable": open, "protected": !open,
    })
}

fn count(row: &Value, field: &str) -> u64 {
    row[field].as_u64().unwrap_or(0)
}

/// The rows of `folder`, biggest first, then by name.
fn rows(folder: &Path, root: &Path, nested: bool) -> Vec<Value> {
    let mut rows: Vec<Value> = fs::read_dir(folder).into_iter().flatten().flatten().map(|entry| item(&entry.path(), root, nested)).collect();
    rows.sort_by_cached_key(|row| (std::cmp::Reverse(count(row, "bytes")), row["name"].as_str().unwrap_or_default().to_lowercase()));
    rows
}

/// `appdata_overview`.
pub fn overview(data_dir: &Path) -> Value {
    let _ = fs::create_dir_all(data_dir);
    let root = resolve(data_dir);
    let items = rows(&root, &root, false);
    let total = |field: &str| items.iter().map(|row| count(row, field)).sum::<u64>();
    json!({"root": text(&root), "bytes": total("bytes"), "files": total("files"), "dirs": total("dirs"), "items": items})
}

/// `appdata_children`: what is inside `rel`, at most 80 rows.
pub fn children(data_dir: &Path, rel: &str) -> Value {
    let root = resolve(data_dir);
    let Some(path) = resolve_rel(&root, rel).filter(|path| path.is_dir()) else {
        return json!({"rel": rel, "items": [], "truncated": false, "total": 0, "error": "not_found"});
    };
    let mut rows = rows(&path, &root, true);
    let total = rows.len();
    rows.truncate(CHILD_LIMIT);
    json!({"rel": if path == root { String::new() } else { rel_of(&path, &root) }, "items": rows, "truncated": total > CHILD_LIMIT, "total": total})
}

/// `_clear_contents`: a file goes; a folder is emptied and kept.
fn clear_contents(path: &Path) -> u64 {
    if path.is_file() || link(path) == Link::Symlink {
        return unlink(path).is_ok() as u64;
    }
    if !path.is_dir() {
        return 0;
    }
    fn gone(child: &Path) -> bool {
        match link(child) {
            Link::Junction => fs::remove_dir(child).is_ok(), // the link goes, never what it points at
            Link::Symlink => unlink(child).is_ok(),
            Link::No if child.is_file() => unlink(child).is_ok(),
            Link::No => child.is_dir() && safe_rmtree(child),
        }
    }
    let children: Vec<PathBuf> = fs::read_dir(path).into_iter().flatten().flatten().map(|entry| entry.path()).collect();
    children.iter().filter(|child| gone(child)).count() as u64
}

fn refused(error: &str) -> Value {
    json!({"ok": false, "error": error, "removed": 0})
}

/// The path a clear or delete of `rel` may touch, or why not.
fn target(root: &Path, rel: &str) -> Result<PathBuf, Value> {
    let raw = clean(rel);
    if raw.is_empty() {
        return Err(refused("refused_root"));
    }
    let path = resolve_rel(root, &raw).ok_or_else(|| refused("not_found"))?;
    if PROTECTED.contains(&raw.split('/').next().unwrap_or_default()) {
        return Err(refused("protected"));
    }
    if path.exists() { Ok(path) } else { Err(refused("not_found")) }
}

/// `appdata_clear`: empty a folder (and keep it) or delete a file.
pub fn clear(data_dir: &Path, rel: &str) -> Value {
    match target(&resolve(data_dir), rel) {
        Ok(path) => json!({"ok": true, "removed": clear_contents(&path)}),
        Err(refusal) => refusal,
    }
}

/// `appdata_delete`: delete a file or a folder.
pub fn delete(data_dir: &Path, rel: &str) -> Value {
    let path = match target(&resolve(data_dir), rel) {
        Ok(path) => path,
        Err(refusal) => return refusal,
    };
    if path.is_dir() && link(&path) != Link::Symlink {
        let ok = safe_rmtree(&path);
        return json!({"ok": ok, "removed": ok as u64, "error": if ok { Value::Null } else { "busy".into() }});
    }
    if unlink(&path).is_ok() { json!({"ok": true, "removed": 1}) } else { refused("busy") }
}

/// `appdata_clear_caches`: clear every top-level row that is a cache.
pub fn clear_caches(data_dir: &Path) -> Value {
    let (mut removed, mut cleared) = (0, Vec::new());
    let overview = overview(data_dir);
    for row in overview["items"].as_array().into_iter().flatten().filter(|row| row["kind"] == "cache" && row["clearable"] == true) {
        let rel = row["rel"].as_str().unwrap_or_default();
        let done = clear(data_dir, rel);
        if done["ok"] == true {
            removed += count(&done, "removed");
            cleared.push(rel.to_string());
        }
    }
    json!({"ok": true, "removed": removed, "cleared": cleared})
}

/// `appdata_projects`: each project's folders and rows. `opened` are the saved projects
/// newest first, `current` the open one, `rows` each project id's row counts.
pub fn projects(data_dir: &Path, opened: &[String], current: &str, rows: &Map<String, Value>) -> Value {
    let root = resolve(data_dir);
    let mut paths: Vec<(String, String)> = Vec::new();
    let mut seen = Vec::new();
    for path in opened.iter().map(|path| normalize(path)).filter(|path| !path.is_empty()) {
        if !seen.contains(&path) {
            let slug = project_slug(&path);
            paths.retain(|known| known.0 != slug);
            paths.push((slug, path.clone()));
            seen.push(path);
        }
    }
    let current = current.trim();
    if !current.is_empty() && !paths.iter().any(|known| known.0 == project_slug(current)) {
        paths.push((project_slug(current), current.to_string()));
    }
    let mut slugs: BTreeSet<String> = paths.iter().map(|known| known.0.clone()).chain(rows.keys().cloned()).collect();
    for (area, _label) in PROJECT_AREAS {
        let folders = fs::read_dir(root.join(area)).into_iter().flatten().flatten().filter(|entry| entry.path().is_dir());
        slugs.extend(folders.map(|entry| entry.file_name().to_string_lossy().into_owned()));
    }
    let mut projects = Vec::new();
    for slug in slugs {
        let (mut areas, mut total) = (Vec::new(), 0);
        for (area, label) in PROJECT_AREAS {
            let folder = root.join(area).join(&slug);
            if folder.is_dir() {
                let (size, files, dirs) = walk_stats(&folder);
                areas.push(json!({"name": label, "rel": rel_of(&folder, &root), "bytes": size, "files": files, "dirs": dirs}));
                total += size;
            }
        }
        let counted = rows.get(&slug).cloned().unwrap_or_else(|| json!({}));
        if areas.is_empty() && !counted.as_object().is_some_and(|rows| !rows.is_empty()) {
            continue;
        }
        let path = paths.iter().find(|known| known.0 == slug).map(|known| known.1.clone()).unwrap_or_default();
        // `project_display_name`: the folder's name.
        let label = Some(file_name(path.trim())).filter(|name| !name.is_empty()).unwrap_or_else(|| if path.is_empty() { slug.clone() } else { path.trim().to_string() });
        projects.push(json!({"slug": slug, "label": label, "path": path, "bytes": total, "areas": areas, "rows": counted}));
    }
    projects.sort_by_cached_key(|row| (std::cmp::Reverse(count(row, "bytes")), row["label"].as_str().unwrap_or_default().to_lowercase()));
    let total: u64 = projects.iter().map(|row| count(row, "bytes")).sum();
    json!({"projects": projects, "bytes": total})
}

/// The folders of `delete_project_appdata`: how many of the project's went.
pub fn delete_project(data_dir: &Path, slug: &str) -> i64 {
    if !plain_name(slug) {
        return 0;
    }
    let folders = PROJECT_AREAS.iter().map(|(area, _label)| data_dir.join(area).join(slug));
    folders.filter(|folder| folder.is_dir() && safe_rmtree(folder)).count() as i64
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let dir = resolve(&std::env::temp_dir()).join(format!("ducky-maintenance-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn a_path_never_leaves_the_folder() {
        let root = scratch("rel");
        fs::create_dir_all(root.join("chats").join("p")).unwrap();
        assert_eq!(resolve_rel(&root, ""), Some(root.clone()));
        assert_eq!(resolve_rel(&root, "\\chats/p/"), Some(root.join("chats").join("p")));
        for out in ["..", "chats/..", "chats/../..", "chats/.", ".", "chats//p", "chats/.. ", "chats/...", " ", "chats/ /p"] {
            assert_eq!(resolve_rel(&root, out), None, "{out}");
        }
        let outside = std::env::temp_dir();
        assert_eq!(resolve_rel(&root, &text(&outside)).filter(|path| !path.starts_with(&root)), None);
        for name in ["", ".", "..", "a/b", "a\\b", "C:", ".. ", " "] {
            assert!(!plain_name(name), "{name}");
            assert_eq!(delete_project(&root, name), 0);
        }
        assert!(plain_name("Island_0123abcd") && root.join("chats").join("p").is_dir());
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn a_clear_keeps_the_folder_and_a_delete_takes_it() {
        let root = scratch("clear");
        for file in ["tool_spills/a/b.txt", "tool_spills/c.txt", "snapshots/ducky-1.db", "chats/projects/p/x.json"] {
            fs::create_dir_all(root.join(file).parent().unwrap()).unwrap();
            fs::write(root.join(file), "12345").unwrap();
        }
        assert_eq!(walk_stats(&root), (20, 4, 6));
        assert_eq!(clear(&root, "snapshots"), refused("protected"));
        assert_eq!(delete(&root, "snapshots/ducky-1.db"), refused("protected"));
        assert_eq!(clear(&root, ""), refused("refused_root"));
        assert_eq!(delete(&root, "nothing"), refused("not_found"));
        assert_eq!(clear(&root, "tool_spills"), json!({"ok": true, "removed": 2}));
        assert!(root.join("tool_spills").is_dir() && walk_stats(&root.join("tool_spills")) == (0, 0, 0));
        assert_eq!(delete(&root, "tool_spills"), json!({"ok": true, "removed": 1, "error": null}));
        assert_eq!(delete_project(&root, "p"), 1);
        assert!(!root.join("tool_spills").exists() && root.join("snapshots").join("ducky-1.db").is_file() && !root.join("chats/projects/p").exists());
        fs::remove_dir_all(&root).unwrap();
    }
}
