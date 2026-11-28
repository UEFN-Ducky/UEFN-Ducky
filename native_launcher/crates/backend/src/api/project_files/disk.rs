//! The open project's files as the Content pane shows them (`project_files.py`): which
//! names a tree lists, what a tree path means on disk, and the listing, stat, fingerprint,
//! read and Quick Open index built on those.
//!
//! Tree paths: project-relative (`Content/Verse/a.verse`), `ws:N` (a workspace root),
//! `abs:<path>` (inside a workspace root, another project or a chat's sandbox) and
//! `ext:<path>` (a file the person dropped in; read wherever it is).
#![allow(clippy::disallowed_methods)] // the whole module runs inside spawn_blocking

use std::cell::OnceCell;
use std::collections::HashMap;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::{Arc, LazyLock, Mutex, MutexGuard, PoisonError};
use std::time::UNIX_EPOCH;

use md5::{Digest, Md5};
use serde_json::{json, Map, Value};

use crate::api::file_kinds::{classify, file_name, is_text_exempt_from_nul_sniff, should_show_dot_entry, stem_suffix};
use crate::api::project_paths::{normalize, posix, project_root_path, relative, resolve, same, text, under, verse_workspace_folders, Folder, Known, FOLDER};
use crate::store::pyjson::{pystr, repr};

/// Never listed in an island's tree or a workspace root.
const SKIP_DIRS: &[&str] = &[".git", ".svn", "__pycache__", "node_modules", ".vite", "dist", "Intermediate", "Saved", "DerivedDataCache", "Binaries"];
/// UEFN's own folders and files under Content: listed only with "Show hidden project files".
const HIDDEN_DIRS: &[&str] = &["__ExternalActors__", "__ExternalObjects__", "Collections", "Developers"];
const HIDDEN_FILES: &[&str] = &["GameFeatureData.uasset"];
/// A folder project never lists these, and lists its tool folders only with "Show hidden".
const FOLDER_NEVER: &[&str] = &[".git", ".svn", ".hg"];
const FOLDER_HIDDEN_DIRS: &[&str] =
    &["node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".vite", ".next", ".turbo", ".gradle"];
/// Quick Open's index of a folder project stops here: a big repo must not hold the panel.
const FOLDER_INDEX_MAX_FILES: usize = 50_000;
const FILE_PATHS_REPLY_MAX: usize = 2000;
const BINARY_PREVIEW_MAX_BYTES: usize = 256 * 1024;
/// A bigger text file is refused: it would cross to the page as one string.
const TEXT_OPEN_MAX_BYTES: u64 = 8 * 1024 * 1024;
const CONTENT: &str = "Content";
const ROOTS: &str = "__roots__";
const DEFAULT_PANEL_LISTENER_PORT: i64 = 4200;

/// Which names a listing shows.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Tree {
    /// An island's Content tree.
    Island,
    /// A folder project.
    Folder,
    /// A read-only workspace root (digests, vproject).
    Workspace,
}

/// UEFN's own files under Content: the level, and `<Project>_Default…` assets.
fn is_uefn_engine_file(name: &str) -> bool {
    let (stem, suffix) = stem_suffix(name);
    HIDDEN_FILES.contains(&name) || suffix.eq_ignore_ascii_case(".umap") || stem.contains("_Default")
}

impl Tree {
    pub fn shows(self, name: &str, is_dir: bool, show_hidden: bool) -> bool {
        let dot_hidden = |show_hidden: bool| name.starts_with('.') && !should_show_dot_entry(name, is_dir, show_hidden);
        match self {
            Tree::Folder => !FOLDER_NEVER.contains(&name) && (show_hidden || !(dot_hidden(false) || (is_dir && FOLDER_HIDDEN_DIRS.contains(&name)))),
            Tree::Workspace => !dot_hidden(show_hidden) && !(is_dir && SKIP_DIRS.contains(&name)),
            Tree::Island => {
                let uefns_own = if is_dir { HIDDEN_DIRS.contains(&name) } else { is_uefn_engine_file(name) };
                Tree::Workspace.shows(name, is_dir, show_hidden) && (show_hidden || !uefns_own)
            }
        }
    }
}

/// `text.lower().startswith(prefix)`.
fn starts(text: &str, prefix: &str) -> bool {
    text.get(..prefix.len()).is_some_and(|start| start.eq_ignore_ascii_case(prefix))
}

/// `(relative_path or "").strip().replace("\\", "/")`.
fn clean(path: &str) -> String {
    path.trim().replace('\\', "/")
}

fn quoted(path: &str) -> String {
    repr(&Value::String(path.to_string()))
}

fn io_error(error: std::io::Error) -> String {
    error.to_string()
}

fn mtime_ns(meta: &std::fs::Metadata) -> u64 {
    meta.modified().ok().and_then(|at| at.duration_since(UNIX_EPOCH).ok()).map_or(0, |since| since.as_nanos() as u64)
}

struct Child {
    name: String,
    /// A folder, or a link to one.
    is_dir: bool,
    /// A symlink or a junction: listed, never walked into.
    link: bool,
    mtime_ns: u64,
}

/// What `dir` holds, in the disk's order. `times` reads each one's time too (a stat per
/// name off Windows, so only the fingerprint asks).
fn children(dir: &Path, times: bool) -> std::io::Result<Vec<Child>> {
    let child = |entry: std::fs::DirEntry| {
        let kind = entry.file_type().ok()?;
        let link = kind.is_symlink();
        let is_dir = if link { std::fs::metadata(entry.path()).is_ok_and(|meta| meta.is_dir()) } else { kind.is_dir() };
        let meta = || if link { std::fs::metadata(entry.path()) } else { entry.metadata() };
        let mtime_ns = if times { meta().as_ref().map_or(0, mtime_ns) } else { 0 };
        Some(Child { name: entry.file_name().to_string_lossy().into_owned(), is_dir, link, mtime_ns })
    };
    Ok(std::fs::read_dir(dir)?.flatten().filter_map(child).collect())
}

/// `os.walk` from `top`, folders and files each by lowercase name: every kept file as
/// `(prefix + its path under top, its name)`. A link to a folder is not followed. With a
/// `limit`, the walk ends after the folder that reaches it.
fn walk(top: &Path, prefix: String, keep: &dyn Fn(&str, bool) -> bool, limit: Option<usize>, out: &mut Vec<(String, String)>) {
    let mut todo = vec![(top.to_path_buf(), prefix)];
    while let Some((dir, prefix)) = todo.pop() {
        let mut found = children(&dir, false).unwrap_or_default();
        found.sort_by_cached_key(|child| child.name.to_lowercase());
        for file in found.iter().filter(|child| !child.is_dir && keep(&child.name, false)) {
            out.push((format!("{prefix}{}", file.name), file.name.clone()));
        }
        if let Some(limit) = limit.filter(|limit| out.len() >= *limit) {
            out.truncate(limit);
            return;
        }
        // Last first: the stack hands the first folder back next.
        for sub in found.iter().rev().filter(|child| child.is_dir && !child.link && keep(&child.name, true)) {
            todo.push((dir.join(&sub.name), format!("{prefix}{}/", sub.name)));
        }
    }
}

/// `f"{n:,}"`.
fn commas(n: u64) -> String {
    let digits = n.to_string();
    let groups: Vec<&str> = digits.as_bytes().rchunks(3).rev().map(|group| std::str::from_utf8(group).unwrap_or_default()).collect();
    groups.join(",")
}

/// `_binary_file_preview`: a hex dump of the file's first bytes, never the whole file.
fn binary_preview(name: &str, chunk: &[u8], total: u64) -> String {
    let truncated = total > chunk.len() as u64;
    let shown = if truncated { format!(" — showing first {} KB", chunk.len() / 1024) } else { String::new() };
    let mut lines = vec![format!("# Binary file preview (read-only): {name}"), format!("# Size: {} bytes{shown}", commas(total)), String::new()];
    for (index, row) in chunk.chunks(16).enumerate() {
        let hex: Vec<String> = row.iter().map(|byte| format!("{byte:02x}")).collect();
        let ascii: String = row.iter().map(|&byte| if (32..127).contains(&byte) { byte as char } else { '.' }).collect();
        lines.push(format!("{:08x}  {:<48}  {ascii}", index * 16, hex.join(" ")));
    }
    if truncated {
        lines.extend([String::new(), format!("# … {} more bytes not shown", commas(total - chunk.len() as u64))]);
    }
    lines.join("\n")
}

/// `urllib.parse.quote(text, safe="")`.
fn quote(text: &str) -> String {
    let keep = |byte: u8| byte.is_ascii_alphanumeric() || b"_.-~".contains(&byte);
    text.bytes().map(|byte| if keep(byte) { (byte as char).to_string() } else { format!("%{byte:02X}") }).collect()
}

/// `panel_bind.panel_port`: the port the page is served on, one below the listener's
/// (`UEFN_DUCKY_PANEL_PORT`, else the pair saved in `panel_port.json`, else 4200).
/// ponytail: a pair this run moved without saving it (the default one was busy) is known
/// to the launcher only; it has to hand the port over before these methods are routed.
fn panel_port(data_dir: &Path) -> i64 {
    let pinned = std::env::var("UEFN_DUCKY_PANEL_PORT").ok().and_then(|port| port.trim().parse::<i64>().ok()).filter(|port| *port != 0);
    let saved = || {
        let file: Value = std::fs::read(data_dir.join("panel_port.json")).ok().and_then(|bytes| serde_json::from_slice(&bytes).ok())?;
        file["port"].as_i64().filter(|port| (1026..65535).contains(port))
    };
    pinned.or_else(saved).unwrap_or(DEFAULT_PANEL_LISTENER_PORT) - 1
}

fn media_url(port: i64, tree_path: &str) -> String {
    format!("http://127.0.0.1:{port}/project-media/{}", quote(tree_path))
}

/// `build_model_media_urls`: `(the folder's URL, the file's name)`, so a model can load
/// the textures beside it.
fn model_urls(port: i64, tree_path: &str) -> Result<(String, String), String> {
    let norm = clean(tree_path);
    let prefix = if starts(&norm, "ext:") || starts(&norm, "abs:") { 4 } else { 0 };
    let (dir, name) = norm[prefix..].rsplit_once('/').ok_or("Model path has no directory")?;
    Ok((format!("http://127.0.0.1:{port}/model-files/{}/", quote(&format!("{}{dir}", &norm[..prefix]))), name.to_string()))
}

type Rows = Arc<Vec<(String, String)>>;

/// Quick Open's index for the open project: `(what it was built from, the rows)`.
static ROWS: Mutex<Option<(String, Rows)>> = Mutex::new(None);
/// The last fingerprint answered per folder: a change empties [`ROWS`].
static FINGERPRINTS: LazyLock<Mutex<HashMap<String, String>>> = LazyLock::new(Mutex::default);

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}

/// Forget the index: a file was made, moved or removed.
pub fn forget_rows() {
    *lock(&ROWS) = None;
}

/// The open project, for one call.
pub struct Project {
    /// The `uefn_project_root` setting as saved.
    setting: String,
    /// The project's root, or why there is none.
    root: Result<PathBuf, String>,
    /// A plain folder, not an island.
    folder: bool,
    show_hidden: bool,
    known: Known,
    data_dir: PathBuf,
    workspace: OnceCell<Vec<Folder>>,
    others: OnceCell<Vec<Folder>>,
}

impl Project {
    /// `root` is the `uefn_project_root` setting.
    pub fn open(root: &str, show_hidden: bool, known: Known, data_dir: PathBuf) -> Self {
        let setting = root.trim().to_string();
        let root = if setting.is_empty() { Err("No project selected.".to_string()) } else { project_root_path(root) };
        let folder = root.as_ref().is_ok_and(|root| known.kind(&text(root)) == FOLDER);
        Project { setting, root, folder, show_hidden, known, data_dir, workspace: OnceCell::new(), others: OnceCell::new() }
    }

    /// `lanes.project_roots`: the folders a full-path lane may be in. The open project's
    /// workspace folders and the project itself, then every project Ducky knows; `/`
    /// between the parts and none at the end.
    /// ponytail: an agent's own project and the `UEFN_VSCODE_WORKSPACE_FOLDERS` /
    /// `UEFN_DUCKY_PROJECT_ROOT` environment are the tool server's (R4), not the panel's.
    pub fn lane_roots(&self) -> Vec<String> {
        let mut roots = Vec::new();
        if !self.setting.is_empty() {
            let mut open = resolve(Path::new(&self.setting));
            if open.is_file() && text(&open).to_lowercase().ends_with(".uefnproject") {
                open.pop();
            }
            if self.known.kind(&text(&open)) != FOLDER {
                let folders = verse_workspace_folders(&open, &self.data_dir);
                roots.extend(folders.iter().filter(|folder| !folder.path.is_empty()).map(|folder| resolve(Path::new(&folder.path))));
            }
            roots.push(open);
        }
        roots.extend(self.known.roots().iter().filter(|root| !root.trim().is_empty()).map(|root| resolve(Path::new(root))));
        roots.iter().map(|root| posix(root).trim_end_matches('/').to_string()).collect()
    }

    fn root(&self) -> Result<&Path, String> {
        self.root.as_deref().map_err(String::clone)
    }

    fn tree(&self) -> Tree {
        if self.folder { Tree::Folder } else { Tree::Island }
    }

    /// `_content_dir`: the Content pane's folder (the project itself for a folder project).
    fn content_dir(&self) -> Result<PathBuf, String> {
        let root = self.root()?;
        let content = if self.folder { root.to_path_buf() } else { resolve(&root.join(CONTENT)) };
        if content.is_dir() { Ok(content) } else { Err("Project has no Content folder.".into()) }
    }

    /// `_workspace_folders`: the roots of the tree. A folder project is its own one root;
    /// an island has its Verse workspace, with its Content folder always among them.
    fn workspace(&self) -> Result<&[Folder], String> {
        let root = self.root()?;
        let found = || {
            let name = file_name(&text(root));
            if self.folder {
                return vec![Folder { name, path: text(root), kind: "" }];
            }
            let named = |folder: Folder| {
                let (name, path) = (folder.name.trim().to_string(), folder.path.trim().to_string());
                (!path.is_empty()).then(|| Folder { name: if name.is_empty() { file_name(&path) } else { name }, path, kind: "" })
            };
            let mut folders: Vec<Folder> = verse_workspace_folders(root, &self.data_dir).into_iter().filter_map(named).collect();
            let content = resolve(&root.join(CONTENT));
            if content.is_dir() && !folders.iter().any(|folder| same(&resolve(Path::new(&folder.path)), &content)) {
                folders.insert(0, Folder { name, path: text(&content), kind: "" });
            }
            folders
        };
        Ok(self.workspace.get_or_init(found))
    }

    /// `_other_project_content_folders`: every other known project's Content root, by name.
    fn others(&self) -> &[Folder] {
        let found = || {
            let Ok(current) = self.root() else { return Vec::new() };
            let mut seen: Vec<String> = Vec::new();
            let mut out = Vec::new();
            for path in self.known.roots().iter().map(|path| normalize(path)).filter(|path| !path.is_empty()) {
                let root = resolve(Path::new(&path));
                if seen.contains(&path) || same(&root, current) {
                    continue;
                }
                let kind = self.known.kind(&path);
                let content = if kind == FOLDER { root } else { root.join(CONTENT) };
                if content.is_dir() {
                    let name = Some(file_name(&path).trim().to_string()).filter(|name| !name.is_empty()).unwrap_or_else(|| path.trim().to_string());
                    out.push(Folder { name, path: text(&resolve(&content)), kind });
                }
                seen.push(path);
            }
            out.sort_by_cached_key(|folder| folder.name.to_lowercase());
            out
        };
        self.others.get_or_init(found)
    }

    fn is_content_folder(&self, folder: &Path) -> bool {
        self.content_dir().is_ok_and(|content| same(&resolve(folder), &content))
    }

    fn under_content(&self, target: &Path) -> bool {
        self.content_dir().is_ok_and(|content| under(target, &content))
    }

    fn under_other_project(&self, target: &Path) -> bool {
        self.others().iter().any(|folder| under(target, Path::new(&folder.path)))
    }

    /// `_is_under_any_workspace_folder`: what an `abs:` path may name: a workspace root,
    /// another project's content, or a chat's sandbox (its file links open, read-only).
    fn under_any_workspace(&self, target: &Path) -> Result<bool, String> {
        let in_workspace = self.workspace()?.iter().any(|folder| under(target, Path::new(&folder.path)));
        Ok(in_workspace || self.under_other_project(target) || under(target, &self.data_dir.join("sandboxes")))
    }

    /// `_abs_to_content_rel`: the project-relative form of a full path (plain or `abs:`)
    /// inside the open project.
    fn abs_to_rel(&self, rel: &str) -> Option<String> {
        let candidate = Path::new(if starts(rel, "abs:") { &rel[4..] } else { rel });
        if candidate.is_absolute() { relative(candidate, self.root().ok()?) } else { None }
    }

    /// `_is_workspace_locked_path`: a path outside the open project (another project, a
    /// workspace root): read-only here.
    fn locked(&self, rel: &str) -> bool {
        let rel = self.abs_to_rel(rel).unwrap_or_else(|| rel.to_string());
        starts(&rel, "abs:") || starts(&rel, "ws:") || Path::new(&rel).is_absolute() || rel.is_empty() || rel.eq_ignore_ascii_case(ROOTS)
    }

    fn decode_abs(&self, encoded: &str) -> Result<PathBuf, String> {
        let raw = clean(encoded);
        if !starts(&raw, "abs:") {
            return Err(format!("Not an absolute workspace path: {}", quoted(encoded)));
        }
        let part = &raw[4..];
        if part.is_empty() {
            return Err(format!("Empty absolute workspace path: {}", quoted(encoded)));
        }
        if !part.trim_end_matches('/').contains('/') {
            return Err(format!("Invalid workspace path (not under a folder): {}", quoted(encoded)));
        }
        let target = resolve(Path::new(part));
        if !self.under_any_workspace(&target)? {
            return Err(format!("Path escapes workspace folders: {}", quoted(encoded)));
        }
        Ok(target)
    }

    /// `_parse_ws_index`: the workspace root a `ws:N` path names.
    fn workspace_root(&self, path: &str) -> Result<&Folder, String> {
        let raw = clean(path);
        if !starts(&raw, "ws:") {
            return Err(format!("Not a workspace root path: {}", quoted(path)));
        }
        let index = raw[3..].trim_matches('/');
        if index.is_empty() || !index.bytes().all(|byte| byte.is_ascii_digit()) {
            return Err(format!("Invalid workspace root index: {}", quoted(path)));
        }
        let folders = self.workspace()?;
        index.parse::<usize>().ok().and_then(|index| folders.get(index)).ok_or_else(|| format!("Workspace root index out of range: {}", quoted(path)))
    }

    /// `_resolve_relative`: a project-relative path on disk, never outside the project.
    fn resolve_relative(&self, path: &str) -> Result<PathBuf, String> {
        let root = self.root()?;
        let rel = clean(path);
        let rel = rel.trim_matches('/');
        let target = if rel.is_empty() { root.to_path_buf() } else { resolve(&root.join(rel)) };
        if under(&target, root) { Ok(target) } else { Err(format!("Path escapes project root: {}", quoted(path))) }
    }

    /// `_resolve_file_path`: any tree path on disk.
    fn resolve_file_path(&self, path: &str) -> Result<PathBuf, String> {
        let rel = clean(path);
        let rel = rel.trim_matches('/');
        if rel.is_empty() || rel.eq_ignore_ascii_case(ROOTS) {
            return Err("Use list_workspace_roots for workspace roots.".into());
        }
        if starts(rel, "ext:") {
            // A dropped file: the drop is the permission to read it, wherever it is.
            return if rel.len() > 4 { Ok(resolve(Path::new(&rel[4..]))) } else { Err(format!("Empty external file path: {}", quoted(rel))) };
        }
        if starts(rel, "abs:") {
            return self.decode_abs(rel);
        }
        if starts(rel, "ws:") {
            return Ok(resolve(Path::new(&self.workspace_root(rel)?.path)));
        }
        self.resolve_relative(rel)
    }

    fn encode(prefix: &str, path: &Path) -> String {
        format!("{prefix}{}", posix(&resolve(path)))
    }

    /// A tree path's file and the path the answer names it by.
    fn located(&self, path: &str) -> Result<(PathBuf, String), String> {
        let target = self.resolve_file_path(path)?;
        let given = clean(path);
        if starts(&given, "ext:") {
            return Ok((target.clone(), Self::encode("ext:", &target)));
        }
        if starts(&given, "abs:") {
            return Ok((target.clone(), Self::encode("abs:", &target)));
        }
        match relative(&target, self.root()?) {
            Some(rel) => Ok((target, rel)),
            None => Err(format!("Path is outside the project: {}", quoted(path))),
        }
    }

    /// `list_workspace_roots`.
    pub fn roots(&self) -> Result<Value, String> {
        let row = |(index, folder): (usize, &Folder)| {
            let path = Path::new(&folder.path);
            json!({"id": format!("ws:{index}"), "name": folder.name, "path": text(&resolve(path)), "read_only": !self.is_content_folder(path)})
        };
        Ok(self.workspace()?.iter().enumerate().map(row).collect())
    }

    /// `_list_directory_entries`: a folder's rows, folders first, each by lowercase name.
    fn entries(&self, target: &Path, content_tree: bool) -> Result<Vec<Value>, String> {
        let root = self.root()?;
        let target = resolve(target);
        let content = if self.folder { root.to_path_buf() } else { resolve(&root.join(CONTENT)) };
        let active_content = under(&target, &content);
        let tree = if !content_tree {
            Tree::Workspace
        } else if active_content {
            self.tree()
        } else {
            // Another project's tree follows that project's kind.
            let other = self.others().iter().find(|folder| under(&target, Path::new(&folder.path)));
            if other.is_some_and(|folder| folder.kind == FOLDER) { Tree::Folder } else { Tree::Island }
        };
        let mut found = children(&target, false).map_err(io_error)?;
        found.sort_by_cached_key(|child| (!child.is_dir, child.name.to_lowercase()));
        let mut rows = Vec::new();
        for child in found.iter().filter(|child| tree.shows(&child.name, child.is_dir, self.show_hidden)) {
            let full = target.join(&child.name);
            let resolved = if child.link { resolve(&full) } else { full.clone() };
            let path = if resolved.parent().is_none_or(|parent| !same(parent, &target)) {
                // A link out of this folder: where it leads has to be allowed too.
                self.entry_path(&full)?
            } else if active_content {
                relative(&resolved, root).unwrap_or_default()
            } else {
                format!("abs:{}", posix(&resolved))
            };
            rows.push(json!({"name": child.name, "path": path, "is_dir": child.is_dir}));
        }
        Ok(rows)
    }

    /// `_entry_path_for_target`: project-relative under Content, else `abs:`.
    fn entry_path(&self, target: &Path) -> Result<String, String> {
        if self.under_content(target) {
            if let Some(rel) = relative(target, self.root()?) {
                return Ok(rel);
            }
        }
        if !self.under_any_workspace(target)? {
            return Err(format!("Path is outside workspace: {}", text(target)));
        }
        Ok(Self::encode("abs:", target))
    }

    /// `list_project_files`: the tree's roots, or the rows of one folder.
    pub fn list(&self, path: &str) -> Result<Value, String> {
        let rel = clean(path);
        let rel = rel.trim_matches('/');
        let not_a_dir = || format!("Not a directory: {}", if path.is_empty() { "." } else { path });
        if rel.is_empty() || rel.eq_ignore_ascii_case(ROOTS) {
            let root_name = file_name(&text(self.root()?));
            let mut entries = Vec::new();
            for (index, folder) in self.workspace()?.iter().enumerate() {
                let dir = Path::new(&folder.path);
                let read_only = !self.is_content_folder(dir);
                let (name, kind) = if read_only { (&folder.name, "core") } else { (&root_name, "content") };
                entries.push(json!({"name": name, "path": format!("ws:{index}"), "is_dir": dir.is_dir(), "read_only": read_only, "kind": kind}));
            }
            for other in self.others() {
                let dir = Path::new(&other.path);
                entries.push(json!({"name": other.name, "path": Self::encode("abs:", dir), "is_dir": dir.is_dir(), "read_only": true, "kind": "project"}));
            }
            return Ok(json!({"path": ROOTS, "entries": entries}));
        }
        let (target, content_tree) = if starts(rel, "ws:") {
            let target = resolve(Path::new(&self.workspace_root(rel)?.path));
            let content_tree = self.is_content_folder(&target);
            (target, content_tree)
        } else if starts(rel, "abs:") {
            let target = self.decode_abs(rel)?;
            let content_tree = self.under_content(&target) || self.under_other_project(&target);
            (target, content_tree)
        } else {
            (self.resolve_relative(rel)?, true)
        };
        if !target.is_dir() {
            return Err(not_a_dir());
        }
        Ok(json!({"path": rel, "entries": self.entries(&target, content_tree)?}))
    }

    /// `stat_project_file`: a file's time and size, for noticing a change made elsewhere.
    pub fn stat(&self, path: &str) -> Result<Value, String> {
        let (target, rel) = self.located(path)?;
        if !target.is_file() {
            return Ok(json!({"path": rel, "mtime_ns": 0, "size": 0, "exists": false}));
        }
        let meta = std::fs::metadata(&target).map_err(io_error)?;
        Ok(json!({"path": rel, "mtime_ns": mtime_ns(&meta), "size": meta.len(), "exists": true}))
    }

    /// `_dir_fingerprint`: how many names a folder shows, the newest of their times and a
    /// hash of the names: an add, a removal, a rename or a touch changes it.
    fn fingerprint(&self, dir: &Path) -> std::io::Result<String> {
        let shown = children(dir, true)?.into_iter().filter(|child| self.tree().shows(&child.name, child.is_dir, self.show_hidden));
        let (mut names, times): (Vec<String>, Vec<u64>) = shown.map(|child| (child.name, child.mtime_ns)).unzip();
        names.sort();
        let hash: String = Md5::digest(names.join("\n").as_bytes()).iter().take(6).map(|byte| format!("{byte:02x}")).collect();
        Ok(format!("{}:{}:{hash}", names.len(), times.into_iter().max().unwrap_or(0)))
    }

    /// `fingerprint_project_dirs`: one fingerprint per folder under Content; empty for
    /// anything else, so one bad path cannot fail the poll.
    pub fn fingerprints(&self, paths: &[Value]) -> Value {
        let mut out = Map::new();
        for raw in paths {
            let rel = clean(raw.as_str().unwrap_or_default());
            let rel = rel.trim_matches('/');
            let folder = || self.resolve_relative(rel).ok().filter(|target| self.under_content(target) && target.is_dir());
            let print = if rel.is_empty() || self.locked(rel) { None } else { folder().and_then(|target| self.fingerprint(&target).ok()) };
            // The key as JSON writes it: a null path answers under "null".
            out.insert(raw.as_str().map_or_else(|| raw.to_string(), str::to_string), print.unwrap_or_default().into());
        }
        // A changed fingerprint means something outside Ducky touched the tree: Quick
        // Open's index must not keep the old listing.
        let mut last = lock(&FINGERPRINTS);
        let changed = out.iter().any(|(path, print)| last.get(path).map(String::as_str) != print.as_str());
        last.extend(out.iter().map(|(path, print)| (path.clone(), pystr(print))));
        if changed {
            forget_rows();
        }
        json!({"fingerprints": out})
    }

    /// `read_project_file`: a text file's text; a picture, model, sound or video as the
    /// URL the page loads it from; any other binary as a hex preview of its start.
    pub fn read(&self, path: &str) -> Result<Value, String> {
        let (target, rel) = self.located(path)?;
        if !target.is_file() {
            return Err(format!("Not a file: {path}"));
        }
        let size = std::fs::metadata(&target).map_err(io_error)?.len();
        let mut head = Vec::new();
        std::fs::File::open(&target).map_err(io_error)?.take(BINARY_PREVIEW_MAX_BYTES as u64).read_to_end(&mut head).map_err(io_error)?;
        let info = classify(&rel, Some(&head));
        let (kind, mime) = (info["kind"].as_str().unwrap_or_default(), &info["mime"]);
        let port = panel_port(&self.data_dir);
        if matches!(kind, "image" | "audio" | "video") {
            return Ok(json!({"path": rel, "content": "", "kind": kind, "mime": mime, "media_url": media_url(port, &rel), "size": size.to_string()}));
        }
        if kind == "model" {
            let (base, name) = model_urls(port, &rel)?;
            let url = format!("{base}{}", quote(&name));
            return Ok(json!({"path": rel, "content": "", "kind": kind, "mime": mime, "media_url": url, "media_base_url": base, "media_filename": name, "size": size.to_string()}));
        }
        // A binary suffix, or a NUL in the first bytes: a preview of those bytes; the file
        // is never read whole. A file the editor saves is exempt: a preview opened there
        // could be written back over it.
        let name = file_name(&text(&target));
        if matches!(kind, "binary" | "unreal_asset") || (head.contains(&0) && !is_text_exempt_from_nul_sniff(&name)) {
            let kind = if kind == "unreal_asset" { kind } else { "binary" };
            return Ok(json!({"path": rel, "content": binary_preview(&name, &head, size), "binary_preview": "true", "kind": kind, "mime": mime}));
        }
        if size > TEXT_OPEN_MAX_BYTES {
            let (mb, limit) = (size as f64 / (1024.0 * 1024.0), TEXT_OPEN_MAX_BYTES / (1024 * 1024));
            return Err(format!("File is too large to open in the panel: {mb:.1} MB (limit {limit} MB): {path}"));
        }
        // As Python reads text: bad bytes become U+FFFD, every line end becomes \n.
        let bytes = std::fs::read(&target).map_err(io_error)?;
        let content = String::from_utf8_lossy(&bytes).replace("\r\n", "\n").replace('\r', "\n");
        Ok(json!({"path": rel, "content": content, "kind": "text", "mime": mime}))
    }

    /// `project_file_media_url`: the URL part of [`Project::read`].
    pub fn media(&self, path: &str) -> Result<Value, String> {
        let file = self.read(path)?;
        let field = |name: &str| if file[name].is_null() { json!("") } else { file[name].clone() };
        let mut out = json!({"path": file["path"], "media_url": field("media_url"), "mime": field("mime"), "kind": field("kind")});
        if file["kind"] == "model" {
            out["media_base_url"] = field("media_base_url");
            out["media_filename"] = field("media_filename");
        }
        Ok(out)
    }

    /// `_project_file_rows`: every file the Content tree shows, then the `.verse` files of
    /// the other workspace roots, as `(tree path, name)`. Kept until Content's own time,
    /// a fingerprint or the settings change.
    fn rows(&self) -> Result<Rows, String> {
        let Ok(root) = self.root() else { return Ok(Rows::default()) };
        let content = self.content_dir()?;
        let workspace = self.workspace()?;
        let mtime = std::fs::metadata(&content).map_or(0, |meta| mtime_ns(&meta));
        let key = format!("{}|hidden={}|ws={}|mtime={mtime}", text(root), self.show_hidden, workspace.len());
        if let Some((_, rows)) = lock(&ROWS).as_ref().filter(|(built_from, _)| *built_from == key) {
            return Ok(Arc::clone(rows));
        }
        let mut out = Vec::new();
        let (tree, hidden) = (self.tree(), self.show_hidden);
        let prefix = if self.folder { String::new() } else { format!("{CONTENT}/") };
        walk(&content, prefix, &|name, is_dir| tree.shows(name, is_dir, hidden), self.folder.then_some(FOLDER_INDEX_MAX_FILES), &mut out);
        let verse = |name: &str, is_dir: bool| Tree::Workspace.shows(name, is_dir, hidden) && (is_dir || name.to_lowercase().ends_with(".verse"));
        for dir in workspace.iter().map(|folder| Path::new(&folder.path)).filter(|dir| dir.is_dir() && !self.is_content_folder(dir)) {
            walk(dir, format!("{}/", Self::encode("abs:", dir)), &verse, None, &mut out);
        }
        let rows = Arc::new(out);
        *lock(&ROWS) = Some((key, Arc::clone(&rows)));
        Ok(rows)
    }

    /// `list_project_file_paths`: without a query the first 2,000 paths and how many there
    /// are; with one, the paths holding every word of it, a match in the file's name first.
    pub fn paths(&self, query: &str, limit: i64) -> Result<Value, String> {
        let query = query.to_lowercase();
        let words: Vec<&str> = query.split_whitespace().collect();
        let Some(last) = words.last() else {
            let rows = self.rows()?;
            let paths: Vec<&String> = rows.iter().take(FILE_PATHS_REPLY_MAX).map(|(path, _)| path).collect();
            return Ok(json!({"paths": paths, "total": rows.len(), "truncated": rows.len() > FILE_PATHS_REPLY_MAX}));
        };
        let rows = self.rows()?;
        let hit = |(path, _): &&(String, String)| words.iter().all(|word| path.to_lowercase().contains(word));
        let (by_name, by_path): (Vec<_>, Vec<_>) = rows.iter().filter(hit).partition(|(_, name)| name.to_lowercase().contains(last));
        let cap = limit.clamp(1, FILE_PATHS_REPLY_MAX as i64) as usize;
        let paths: Vec<&String> = by_name.iter().chain(&by_path).take(cap).map(|(path, _)| path).collect();
        Ok(json!({"paths": paths, "truncated": by_name.len() + by_path.len() > cap}))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api::project_paths::tests::Temp;

    // The skip rules are a safety rule as well as a speed rule: these are the cases of
    // `test_project_files_folder_project.py` and `test_project_files_content_roots.py`.
    #[test]
    fn an_island_tree_hides_uefns_own_files_and_never_lists_build_folders() {
        let shows = |name: &str, is_dir: bool, hidden: bool| Tree::Island.shows(name, is_dir, hidden);
        for build in [".git", ".svn", "__pycache__", "node_modules", ".vite", "dist", "Intermediate", "Saved", "DerivedDataCache", "Binaries"] {
            assert!(!shows(build, true, false) && !shows(build, true, true), "{build}");
        }
        for own in ["__ExternalActors__", "__ExternalObjects__", "Collections", "Developers"] {
            assert!(!shows(own, true, false) && shows(own, true, true) && shows(own, false, false), "{own}");
        }
        for own in ["GameFeatureData.uasset", "Level.umap", "LEVEL.UMAP", "MyGame_DefaultHLODLayer.uasset"] {
            assert!(!shows(own, false, false) && shows(own, false, true), "{own}");
        }
        assert!(shows("Verse", true, false) && shows("a.verse", false, false) && shows("Saved", false, false) && shows("Default.uasset", false, false));
        assert!(shows(".gitignore", false, false) && !shows(".DS_Store", false, false) && shows(".DS_Store", false, true) && !shows(".urc", true, false));
        // A workspace root follows the same rules without UEFN's own names.
        assert!(Tree::Workspace.shows("Level.umap", false, false) && Tree::Workspace.shows("Collections", true, false) && !Tree::Workspace.shows("Saved", true, true));
    }

    #[test]
    fn a_folder_project_lists_everything_but_vcs_and_hides_tool_folders() {
        let shows = |name: &str, is_dir: bool, hidden: bool| Tree::Folder.shows(name, is_dir, hidden);
        for vcs in [".git", ".svn", ".hg"] {
            assert!(!shows(vcs, true, false) && !shows(vcs, true, true), "{vcs}");
        }
        for tool in ["node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".vite", ".next", ".turbo", ".gradle", ".idea"] {
            assert!(!shows(tool, true, false) && shows(tool, true, true), "{tool}");
        }
        // None of an island's rules apply.
        for kept in ["Saved", "Binaries", "dist", "Intermediate", "Collections"] {
            assert!(shows(kept, true, false), "{kept}");
        }
        assert!(shows("Level.umap", false, false) && shows("x_Default.uasset", false, false) && shows("venv", false, false) && shows(".env", false, false));
        assert!(!shows(".secret", false, false) && shows(".secret", false, true));
    }

    fn open(root: &Path, data_dir: &Path) -> Project {
        Project::open(&text(root), false, Known::default(), data_dir.to_path_buf())
    }

    #[test]
    fn a_tree_path_never_leaves_the_project_and_abs_paths_stay_in_listed_folders() {
        let temp = Temp::new("paths");
        temp.file("Repo/src/app.py", "print()\r\nx\ry\n");
        temp.file("Repo/.git/config", "");
        temp.file("Elsewhere/secret.txt", "s");
        temp.file("data/sandboxes/chat1/work/note.md", "n");
        let (repo, data) = (temp.0.join("Repo"), temp.0.join("data"));
        let project = open(&repo, &data);
        assert!(project.folder && project.resolve_relative("src/../src/app.py") == Ok(repo.join("src").join("app.py")));
        assert_eq!(project.resolve_relative("../Elsewhere/secret.txt"), Err("Path escapes project root: '../Elsewhere/secret.txt'".into()));
        let outside = format!("abs:{}", posix(&temp.0.join("Elsewhere").join("secret.txt")));
        assert_eq!(project.read(&outside), Err(format!("Path escapes workspace folders: {}", quoted(&outside))));
        assert_eq!(project.read("abs:x"), Err("Invalid workspace path (not under a folder): 'abs:x'".into()));
        assert_eq!(project.read(""), Err("Use list_workspace_roots for workspace roots.".into()));
        assert_eq!(project.list("ws:1"), Err("Workspace root index out of range: 'ws:1'".into()));
        assert_eq!(project.list("ws:x"), Err("Invalid workspace root index: 'ws:x'".into()));
        assert_eq!(project.list("src/app.py"), Err("Not a directory: src/app.py".into()));
        // A dropped file and a chat's sandbox are read wherever they are.
        let dropped = format!("ext:{}", posix(&temp.0.join("Elsewhere").join("secret.txt")));
        assert_eq!(project.read(&dropped).unwrap()["content"], "s");
        let sandbox = format!("abs:{}", posix(&data.join("sandboxes").join("chat1").join("work").join("note.md")));
        assert_eq!(project.read(&sandbox).unwrap(), json!({"path": sandbox, "content": "n", "kind": "text", "mime": "text/markdown"}));
        // Text is read as Python reads it; full paths inside the project are its own.
        assert_eq!(project.read("src\\app.py").unwrap(), json!({"path": "src/app.py", "content": "print()\nx\ny\n", "kind": "text", "mime": "text/x-python"}));
        assert!(!project.locked(&posix(&repo.join("src"))) && !project.locked(&posix(&repo)) && project.locked(&outside) && project.locked("ws:0") && project.locked(ROOTS));
        assert_eq!(project.list("").unwrap(), json!({"path": ROOTS, "entries": [{"name": "Repo", "path": "ws:0", "is_dir": true, "read_only": false, "kind": "content"}]}));
        assert_eq!(project.list("ws:0").unwrap(), json!({"path": "ws:0", "entries": [{"name": "src", "path": "src", "is_dir": true}]}));
        assert_eq!(open(&temp.0.join("Nope"), &data).list(""), Err(format!("Folder not found: {}", text(&temp.0.join("Nope")))));
        assert_eq!(Project::open(" ", false, Known::default(), data.clone()).stat("a"), Err("No project selected.".into()));
    }

    #[test]
    fn a_binary_is_a_hex_preview_and_media_is_a_url() {
        let temp = Temp::new("read");
        std::fs::create_dir_all(temp.0.join("Repo").join("art")).unwrap();
        std::fs::write(temp.0.join("Repo").join("art").join("tool.bin"), [b"MZ\x00\x01".as_slice(), &[b'a'; 20]].concat()).unwrap();
        temp.file("Repo/art/pic one.png", "png");
        temp.file("Repo/art/mesh.glb", "glb");
        let project = open(&temp.0.join("Repo"), &temp.0);
        let preview = format!(
            "# Binary file preview (read-only): tool.bin\n# Size: 24 bytes\n\n\
             00000000  4d 5a 00 01 61 61 61 61 61 61 61 61 61 61 61 61   MZ..aaaaaaaaaaaa\n\
             00000010  61 61 61 61 61 61 61 61{}aaaaaaaa",
            " ".repeat(48 - 23 + 2)
        );
        assert_eq!(project.read("art/tool.bin").unwrap(), json!({"path": "art/tool.bin", "content": preview, "binary_preview": "true", "kind": "binary", "mime": "application/octet-stream"}));
        assert!(binary_preview("x", &[0; 2048], 1_234_567).starts_with("# Binary file preview (read-only): x\n# Size: 1,234,567 bytes — showing first 2 KB\n"));
        assert!(binary_preview("x", &[0; 2048], 1_234_567).ends_with("\n\n# … 1,232,519 more bytes not shown"));
        assert_eq!((commas(0), commas(999), commas(1000), commas(12_345_678)), ("0".into(), "999".into(), "1,000".into(), "12,345,678".into()));
        let port = panel_port(&temp.0);
        let picture = project.read("art/pic one.png").unwrap();
        assert_eq!(picture["media_url"], format!("http://127.0.0.1:{port}/project-media/art%2Fpic%20one.png"));
        assert_eq!((&picture["kind"], &picture["size"], &picture["content"]), (&json!("image"), &json!("3"), &json!("")));
        let base = format!("http://127.0.0.1:{port}/model-files/art/");
        let model = json!({"path": "art/mesh.glb", "media_url": format!("{base}mesh.glb"), "mime": "model/gltf-binary", "kind": "model", "media_base_url": base, "media_filename": "mesh.glb"});
        assert_eq!(project.media("art/mesh.glb").unwrap(), model);
        assert_eq!(quote("abs:C:/a b/é~_.-"), "abs%3AC%3A%2Fa%20b%2F%C3%A9~_.-");
        std::fs::write(temp.0.join("panel_port.json"), r#"{"port": 4400}"#).unwrap();
        assert!(std::env::var_os("UEFN_DUCKY_PANEL_PORT").is_some() || panel_port(&temp.0) == 4399);
    }

    #[test]
    fn quick_open_lists_what_the_tree_shows_and_a_fingerprint_follows_the_folder() {
        let temp = Temp::new("rows");
        for file in ["b.txt", "A.txt", "src/z.py", "src/App.py", "src/deep/readme.md", "node_modules/x/index.js", ".git/HEAD", "Zed/one.txt"] {
            temp.file(&format!("Repo/{file}"), "");
        }
        let project = open(&temp.0.join("Repo"), &temp.0);
        let all = json!({"paths": ["A.txt", "b.txt", "src/App.py", "src/z.py", "src/deep/readme.md", "Zed/one.txt"], "total": 6, "truncated": false});
        assert_eq!(project.paths("", 50).unwrap(), all);
        assert_eq!(project.paths("  ", 1).unwrap(), all);
        assert_eq!(project.paths("SRC py", 50).unwrap(), json!({"paths": ["src/App.py", "src/z.py"], "truncated": false}));
        // A match in the file's own name comes before one in its folders.
        assert_eq!(project.paths("z", 1).unwrap(), json!({"paths": ["src/z.py"], "truncated": true}));
        assert_eq!(project.paths("z", 50).unwrap(), json!({"paths": ["src/z.py", "Zed/one.txt"], "truncated": false}));

        let prints = |project: &Project| project.fingerprints(&[json!("src"), json!("/src/"), json!("nope"), json!("ws:0"), json!("A.txt"), json!("")]);
        let first = prints(&project);
        let print = first["fingerprints"]["src"].as_str().unwrap().to_string();
        assert!(print.starts_with("3:") && print.len() > "3:0:".len() + 12 && first["fingerprints"]["/src/"] == print.as_str());
        assert_eq!((&first["fingerprints"]["nope"], &first["fingerprints"]["ws:0"], &first["fingerprints"]["A.txt"], &first["fingerprints"][""]), (&json!(""), &json!(""), &json!(""), &json!("")));
        // A file added under a watched folder changes its fingerprint and empties the index.
        temp.file("Repo/src/new.py", "");
        assert_ne!(prints(&project)["fingerprints"]["src"], print.as_str());
        assert_eq!(project.paths("new", 50).unwrap(), json!({"paths": ["src/new.py"], "truncated": false}));

        let mut out = Vec::new();
        walk(&temp.0.join("Repo"), String::new(), &|_, _| true, Some(2), &mut out);
        assert_eq!(out, [("A.txt".to_string(), "A.txt".to_string()), ("b.txt".to_string(), "b.txt".to_string())]);
    }
}
