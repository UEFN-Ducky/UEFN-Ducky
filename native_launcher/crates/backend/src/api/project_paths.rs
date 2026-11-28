//! Where projects are on disk: a project's root and kind (`frontend/project_kind.py`),
//! every project Ducky knows (`known_projects.py`, `recent_projects.py`) and an island's
//! Verse workspace folders (`verse_editor/lsp/verse_workspace.py`).
//!
//! Every function here reads the disk, so each one runs inside `spawn_blocking`.
//! ponytail: nothing is cached (Python keeps kinds for the process, known projects for
//! 30 s and workspace folders until a build): each is a few folder listings. Cache one
//! here when a trace shows it.
#![allow(clippy::disallowed_methods)] // the whole module runs inside spawn_blocking

use std::collections::{HashMap, HashSet};
use std::path::{Component, Path, PathBuf};

use serde_json::Value;
use sha2::{Digest, Sha256};

use super::file_kinds::{file_name, stem_suffix};
use crate::store::pyjson::{pystr, truthy};

pub const UEFN: &str = "uefn";
pub const FOLDER: &str = "folder";
const CONTENT: &str = "Content";
/// A built-in digest package folder and the workspace root it is shown as.
const DIGEST_PACKAGES: [(&str, &str); 3] = [("Verse", "/Verse.org"), ("UnrealEngine", "/UnrealEngine.com"), ("Fortnite", "/Fortnite.com")];

/// `str(path)`.
pub fn text(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}

/// `str(path).replace("\\", "/")`.
pub fn posix(path: &Path) -> String {
    text(path).replace('\\', "/")
}

/// A path as two of them compare: Windows ignores case.
fn key(path: &Path) -> String {
    if cfg!(windows) { text(path).to_lowercase() } else { text(path) }
}

pub fn same(a: &Path, b: &Path) -> bool {
    key(a) == key(b)
}

/// `Path(path).resolve()`: absolute, without `.` and `..`, links followed and the disk's
/// own spelling for as much of the path as exists.
/// ponytail: `..` is dropped before links are followed (`link/..` is the link's parent
/// here, the target's parent in Python).
pub fn resolve(path: &Path) -> PathBuf {
    let given = if path.as_os_str().is_empty() { Path::new(".") } else { path };
    let mut full = PathBuf::new();
    for part in std::path::absolute(given).unwrap_or_else(|_| given.to_path_buf()).components() {
        match part {
            Component::CurDir => {}
            Component::ParentDir => drop(full.pop()),
            other => full.push(other),
        }
    }
    let mut missing = Vec::new();
    let mut found = full.as_path();
    loop {
        if let Ok(real) = found.canonicalize() {
            // Windows answers `\\?\C:\x` and `\\?\UNC\pc\share\x`.
            let real = text(&real);
            let plain = real.strip_prefix(r"\\?\UNC\").map(|unc| format!(r"\\{unc}")).or_else(|| real.strip_prefix(r"\\?\").map(String::from));
            let mut out = PathBuf::from(plain.unwrap_or(real));
            out.extend(missing.iter().rev());
            return out;
        }
        match (found.parent(), found.file_name()) {
            (Some(parent), Some(name)) => {
                missing.push(name);
                found = parent;
            }
            _ => return full,
        }
    }
}

/// `_is_path_under`: `child` is `ancestor` or inside it.
pub fn under(child: &Path, ancestor: &Path) -> bool {
    relative(child, ancestor).is_some()
}

/// `child.resolve().relative_to(ancestor.resolve())` with `/` between its parts (`.` for
/// the folder itself); None when `child` is not inside.
pub fn relative(child: &Path, ancestor: &Path) -> Option<String> {
    let (child, ancestor) = (resolve(child), resolve(ancestor));
    let mut parts = child.components();
    let inside = ancestor.components().all(|want| parts.next().is_some_and(|have| same(Path::new(have.as_os_str()), Path::new(want.as_os_str()))));
    let rest = || parts.map(|part| text(Path::new(part.as_os_str()))).collect::<Vec<_>>().join("/");
    inside.then(rest).map(|rest| if rest.is_empty() { ".".into() } else { rest })
}

/// The files (and folders) in `folder` whose name ends with `ending`, as `folder.glob("*" + ending)`
/// sorts them.
fn named(folder: &Path, ending: &str) -> Vec<PathBuf> {
    let ends = |name: &str| if cfg!(windows) { name.to_lowercase().ends_with(&ending.to_lowercase()) } else { name.ends_with(ending) };
    let entries = std::fs::read_dir(folder).into_iter().flatten().flatten();
    let mut found: Vec<PathBuf> = entries.filter(|entry| ends(&entry.file_name().to_string_lossy())).map(|entry| entry.path()).collect();
    found.sort_by_key(|path| key(path));
    found
}

fn has_file(root: &Path, ending: &str) -> bool {
    root.is_dir() && named(root, ending).iter().any(|path| path.is_file())
}

/// A `*.uefnproject` in `root`: the only proof on disk of a UEFN island.
pub fn has_uefnproject(root: &Path) -> bool {
    has_file(root, ".uefnproject")
}

/// `project_root_path`: the project root for a pick (the folder, an island's `Content/`
/// or its `.uefnproject` file).
pub fn project_root_path(selection: &str) -> Result<PathBuf, String> {
    let raw = selection.trim();
    if raw.is_empty() {
        return Err("No project folder selected.".into());
    }
    let path = resolve(Path::new(raw));
    let parent = path.parent().map(Path::to_path_buf);
    if path.is_file() {
        let island = stem_suffix(&file_name(&text(&path))).1.eq_ignore_ascii_case(".uefnproject");
        return parent.filter(|_| island).ok_or_else(|| "Pick a project folder, not a file.".into());
    }
    if !path.is_dir() {
        return Err(format!("Folder not found: {raw}"));
    }
    let Some(parent) = parent else { return Err("Pick a project folder, not a whole drive.".into()) };
    let content = path.file_name().is_some_and(|name| name.to_string_lossy().to_lowercase() == "content");
    Ok(if content && !has_uefnproject(&path) && has_uefnproject(&parent) { parent } else { path })
}

/// `normalize_project_path`: the project root as text, or the path itself when it is none.
pub fn normalize(path: &str) -> String {
    let raw = path.trim();
    if raw.is_empty() {
        return String::new();
    }
    text(&project_root_path(raw).unwrap_or_else(|_| resolve(Path::new(raw))))
}

/// `chats.store.project_slug`: the id every project-scoped row is stored under.
/// ponytail: a letter is what Rust calls alphanumeric; Python's `isalnum` differs on a
/// few combining marks. Use its tables if such a folder name turns up.
pub fn project_slug(project_root: &str) -> String {
    let raw = project_root.trim();
    if raw.is_empty() {
        return "_no_project".into();
    }
    let resolved = text(&resolve(Path::new(raw))).to_lowercase();
    let digest: String = Sha256::digest(resolved.as_bytes()).iter().take(8).map(|byte| format!("{byte:02x}")).collect();
    let name = Some(file_name(raw)).filter(|name| !name.is_empty()).unwrap_or_else(|| "project".into());
    let safe: String = name.chars().take(40).map(|c| if c.is_alphanumeric() || c == '-' || c == '_' { c } else { '_' }).collect();
    format!("{safe}_{digest}")
}

/// The project rows of ducky.db, read before the disk work starts.
#[derive(Default)]
pub struct Known {
    /// Slug to its saved kind.
    pub kinds: HashMap<String, String>,
    /// Opened projects, newest first.
    pub opened: Vec<String>,
    /// Projects the person removed.
    pub removed: Vec<String>,
    /// The person's Documents folder ([`documents_dir`]): UEFN keeps its islands in its
    /// `Fortnite Projects`.
    pub documents: PathBuf,
}

impl Known {
    /// `project_kind`: an island or a plain folder. A `.uefnproject` on disk decides, then
    /// a `.uproject`, then what was saved, then a `Content` folder.
    pub fn kind(&self, root: &str) -> &'static str {
        let raw = root.trim();
        let path = Path::new(raw);
        if raw.is_empty() || has_uefnproject(path) {
            return UEFN;
        }
        if has_file(path, ".uproject") {
            return FOLDER;
        }
        match self.kinds.get(&project_slug(&normalize(raw))).map(String::as_str) {
            Some(UEFN) => UEFN,
            Some(FOLDER) => FOLDER,
            _ if path.join(CONTENT).is_dir() => UEFN,
            _ => FOLDER,
        }
    }

    /// `known_project_roots`: the opened projects, then every island found on disk.
    pub fn roots(&self) -> Vec<String> {
        let mut roots: Vec<String> = Vec::new();
        for path in self.opened.iter().map(|path| normalize(path)).filter(|path| !path.is_empty()) {
            if !roots.contains(&path) {
                roots.push(path);
            }
        }
        let removed: HashSet<String> = self.removed.iter().map(|path| key(Path::new(&normalize(path)))).collect();
        let mut parents = vec![self.documents.join("Fortnite Projects")];
        parents.extend(roots.iter().map(Path::new).filter(|root| has_uefnproject(root)).filter_map(Path::parent).map(Path::to_path_buf));
        let mut seen = HashSet::new();
        for parent in parents.into_iter().filter(|parent| seen.insert(key(&resolve(parent)))) {
            for child in std::fs::read_dir(parent).into_iter().flatten().flatten().map(|entry| entry.path()) {
                let island = text(&resolve(&child));
                if child.is_dir() && has_uefnproject(&child) && !removed.contains(&key(Path::new(&island))) && !roots.contains(&island) {
                    roots.push(island);
                }
            }
        }
        roots
    }
}

/// `documents_dir`: the person's Documents folder; `UEFN_DUCKY_DOCUMENTS_DIR` moves it
/// (tests never read the real islands).
pub fn documents_dir() -> PathBuf {
    let env = |name: &str| std::env::var_os(name).filter(|value| !value.to_string_lossy().trim().is_empty()).map(PathBuf::from);
    let home = || env(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).unwrap_or_default().join("Documents");
    env("UEFN_DUCKY_DOCUMENTS_DIR").or_else(known_documents).unwrap_or_else(home)
}

/// Where the shell says Documents is (OneDrive moves it).
#[cfg(windows)]
fn known_documents() -> Option<PathBuf> {
    use std::os::windows::ffi::OsStringExt;
    use windows_sys::Win32::System::Com::CoTaskMemFree;
    use windows_sys::Win32::UI::Shell::{FOLDERID_Documents, SHGetKnownFolderPath};

    let mut out = std::ptr::null_mut();
    // SAFETY: the shell writes one pointer to a NUL-ended string it allocated; it is
    // copied up to that NUL and freed once, also when the call failed.
    unsafe {
        let found = SHGetKnownFolderPath(&FOLDERID_Documents, 0, std::ptr::null_mut(), &mut out) >= 0 && !out.is_null();
        let path = found.then(|| {
            let len = (0..).take_while(|&i| *out.add(i) != 0).count();
            PathBuf::from(std::ffi::OsString::from_wide(std::slice::from_raw_parts(out, len)))
        });
        CoTaskMemFree(out.cast());
        path.filter(|path| !path.as_os_str().is_empty())
    }
}

#[cfg(not(windows))]
fn known_documents() -> Option<PathBuf> {
    None
}

/// `str(value or "").strip()`.
fn clean(value: &Value) -> String {
    if truthy(value) { pystr(value).trim().to_string() } else { String::new() }
}

/// `island_mount_name`: the name UEFN mounts the island's content under (its root
/// plugin), else its first `.uplugin`, else the folder's name.
fn island_mount_name(root: &Path) -> String {
    let base = project_root_path(&text(root)).unwrap_or_else(|_| root.to_path_buf());
    let project = named(&base, ".uefnproject").first().and_then(|file| std::fs::read(file).ok());
    // utf-8-sig: the file may start with a byte order mark.
    let project: Value = project.and_then(|bytes| serde_json::from_slice(bytes.strip_prefix(b"\xef\xbb\xbf").unwrap_or(&bytes)).ok()).unwrap_or_default();
    let plugins = || project["plugins"].as_array().into_iter().flatten().filter(|plugin| plugin.is_object() && !clean(&plugin["name"]).is_empty());
    let root_plugin = plugins().find(|plugin| truthy(&plugin["bIsRoot"])).or_else(|| plugins().next()).map(|plugin| clean(&plugin["name"]));
    let nested = || std::fs::read_dir(base.join("Plugins")).into_iter().flatten().flatten().map(|entry| entry.path());
    let mut plugin_dirs: Vec<PathBuf> = nested().collect();
    plugin_dirs.sort_by_key(|path| key(path));
    let uplugins = named(&base, ".uplugin").into_iter().chain(plugin_dirs.iter().flat_map(|dir| named(dir, ".uplugin")));
    let stem = |file: PathBuf| Some(stem_suffix(&file_name(&text(&file))).0.to_string()).filter(|stem| !stem.is_empty() && file.is_file());
    root_plugin.or_else(|| uplugins.filter_map(stem).next()).unwrap_or_else(|| file_name(&text(&base)))
}

/// One workspace root: the name it is shown under and its folder.
#[derive(Clone, Debug, PartialEq)]
pub struct Folder {
    pub name: String,
    pub path: String,
    /// `uefn` or `folder` for another project's root; empty for the open project's own.
    pub kind: &'static str,
}

fn folder(name: impl Into<String>, path: &Path) -> Folder {
    Folder { name: name.into(), path: text(path), kind: "" }
}

/// UEFN's own `Saved/VerseProject/<name>` folder.
fn verse_saved_root(name: &str) -> PathBuf {
    let local = std::env::var_os("LOCALAPPDATA").or_else(|| std::env::var_os("USERPROFILE")).unwrap_or_default();
    PathBuf::from(local).join("UnrealEditorFortnite").join("Saved").join("VerseProject").join(name)
}

fn has_digest(path: &Path) -> bool {
    path.is_dir() && !named(path, ".digest.verse").is_empty()
}

/// `Digests/BuiltIn/<package>` (new UEFN) or `<package>` (old) under a saved root.
fn digest_package_dir(saved: &Path, package: &str) -> Option<PathBuf> {
    [saved.join("Digests").join("BuiltIn").join(package), saved.join(package)].into_iter().find(|dir| dir.is_dir())
}

/// The `/Verse.org`, `/UnrealEngine.com` and `/Fortnite.com` roots under a `Digests/BuiltIn`.
fn builtin_package_children(builtin: &Path) -> Vec<Folder> {
    let mut children: Vec<PathBuf> = std::fs::read_dir(builtin).into_iter().flatten().flatten().map(|entry| entry.path()).collect();
    children.sort_by_cached_key(|child| file_name(&text(child)).to_lowercase());
    let label = |child: &Path| DIGEST_PACKAGES.iter().find(|(package, _)| file_name(&text(child)).eq_ignore_ascii_case(package)).map(|(_, label)| *label);
    children.iter().filter(|child| child.is_dir()).filter_map(|child| Some(folder(label(child)?, &resolve(child)))).collect()
}

/// `_is_builtin_digests_root`: `Digests/BuiltIn`, or a folder named like "Built-in Digests".
fn is_builtin_digests_root(root: &Folder) -> bool {
    let path = Path::new(&root.path);
    let name = if root.name.is_empty() { file_name(&root.path) } else { root.name.clone() };
    let name = name.trim().to_lowercase().replace(['_', '-'], " ");
    let mut parts = path.components().rev().map(|part| text(Path::new(part.as_os_str())).to_lowercase());
    ["builtin", "built in digests", "built in"].contains(&name.as_str()) || (parts.next().as_deref() == Some("builtin") && parts.next().as_deref() == Some("digests"))
}

/// `_parse_code_workspace`: the folders of UEFN's `*.code-workspace` file.
fn code_workspace_folders(file: &Path) -> Vec<Folder> {
    let data: Value = std::fs::read(file).ok().and_then(|bytes| serde_json::from_slice(&bytes).ok()).unwrap_or_default();
    let rows = data["folders"].as_array().into_iter().flatten().filter(|row| row.is_object());
    let row = |row: &Value| {
        let path = clean(&row["path"]);
        let name = if truthy(&row["name"]) { pystr(&row["name"]) } else { file_name(&path) };
        (!path.is_empty()).then(|| folder(name, &resolve(Path::new(&path))))
    };
    rows.filter_map(row).collect()
}

/// `_saved_layout_folders`: the roots UEFN laid out under `Saved/VerseProject/<name>`.
fn saved_layout_folders(root: &Path, saved: &Path) -> Vec<Folder> {
    let name = file_name(&text(root));
    let mut folders = Vec::new();
    let content = root.join(CONTENT);
    if content.is_dir() {
        folders.push(folder(name.as_str(), &resolve(&content)));
    }
    let assets = format!("{name}-Assets");
    if let Some(assets) = [saved.join("Digests").join(&assets), saved.join(&assets)].into_iter().find(|dir| dir.is_dir()) {
        folders.push(folder(format!("{name} (Assets)"), &resolve(&assets)));
    }
    if saved.join("vproject").is_dir() {
        folders.push(folder("vproject - DO NOT MODIFY", &resolve(&saved.join("vproject"))));
    }
    let packages = DIGEST_PACKAGES.iter().filter_map(|(package, label)| Some(folder(*label, &resolve(&digest_package_dir(saved, package)?))));
    let mut packages: Vec<Folder> = packages.collect();
    let shared = verse_saved_root("FortniteGame").join("Digests").join("BuiltIn");
    if packages.is_empty() && shared.is_dir() {
        packages = builtin_package_children(&shared);
    }
    folders.extend(packages);
    folders
}

/// True when the island's `.vproject` names a built-in digest folder that holds no
/// digest while FortniteGame's shared copy has one: Python then hands verse-lsp a copy
/// of the file that points there (`shadow_vproject_for`).
fn needs_shadow_vproject(vproject: &Path) -> bool {
    let data: Value = std::fs::read(vproject).ok().and_then(|bytes| serde_json::from_slice(&bytes).ok()).unwrap_or_default();
    let missing = |package: &&Value| {
        let field = |name: &str| Some(&package["desc"][name]).filter(|value| truthy(value)).map(pystr).unwrap_or_default();
        let (name, dir) = (field("name"), field("dirPath"));
        let built_in = DIGEST_PACKAGES.iter().any(|(known, _)| *known == name);
        let shared = || digest_package_dir(&verse_saved_root("FortniteGame"), &name).is_some_and(|dir| has_digest(&dir));
        built_in && !(!dir.is_empty() && has_digest(Path::new(&dir))) && shared()
    };
    data["packages"].as_array().into_iter().flatten().any(|package| missing(&package))
}

/// `discover_verse_workspace(root, include_watch_files=False)`: an island's workspace
/// roots, from UEFN's `*.code-workspace`, else its `Saved/VerseProject` layout, else
/// `Content`. `Digests/BuiltIn` becomes one root per package.
/// ponytail: the shadow `.vproject` folder is named here and written by the Python
/// backend, which starts verse-lsp with it; writing it moves here with the Verse methods.
pub fn verse_workspace_folders(root: &Path, data_dir: &Path) -> Vec<Folder> {
    let root = resolve(root);
    let name = file_name(&text(&root));
    let mut folders = named(&root, ".code-workspace").iter().map(|file| code_workspace_folders(file)).find(|folders| !folders.is_empty()).unwrap_or_default();
    // UEFN keys its saved folder by the content mount, which is not always the folder's name.
    let mount = island_mount_name(&root);
    let by_mount = !mount.is_empty() && caseless::default_case_fold_str(&mount) != caseless::default_case_fold_str(&name) && verse_saved_root(&mount).is_dir();
    let saved_name = if by_mount { mount } else { name.clone() };
    let saved = verse_saved_root(&saved_name);
    if folders.is_empty() && saved.is_dir() {
        folders = saved_layout_folders(&root, &saved);
    }
    if folders.is_empty() {
        let content = root.join(CONTENT);
        folders = vec![folder(name.as_str(), &if content.is_dir() { resolve(&content) } else { root.clone() })];
    }
    let shared = verse_saved_root("FortniteGame").join("Digests").join("BuiltIn");
    let expand = |root: Folder| {
        if !is_builtin_digests_root(&root) {
            return vec![root];
        }
        let path = Path::new(&root.path);
        let own = if path.is_dir() { builtin_package_children(path) } else { Vec::new() };
        if own.is_empty() && shared.is_dir() && shared != path { builtin_package_children(&shared) } else { own }
    };
    let mut folders: Vec<Folder> = folders.into_iter().flat_map(expand).collect();
    let vproject = saved.join("vproject").join(format!("{saved_name}.vproject"));
    if vproject.is_file() && needs_shadow_vproject(&vproject) {
        let shadow = text(&data_dir.join("verse-lsp").join("vproject-shadow").join(&name).join("vproject"));
        let is_vproject = |root: &Folder| root.name.trim().to_lowercase().starts_with("vproject") || file_name(&root.path).to_lowercase() == "vproject";
        let mut replaced = false;
        for root in folders.iter_mut().filter(|root| is_vproject(root)) {
            root.path.clone_from(&shadow);
            replaced = true;
        }
        if !replaced {
            folders.push(Folder { name: "vproject (read-only)".into(), path: shadow, kind: "" });
        }
    }
    folders
}

#[cfg(test)]
pub(super) mod tests {
    use super::*;

    /// A fresh folder under the system's temp one, removed when dropped.
    pub struct Temp(pub PathBuf);

    impl Temp {
        pub fn new(name: &str) -> Self {
            let dir = std::env::temp_dir().join(format!("ducky-backend-{name}-{}", crate::api::uuid4()));
            std::fs::create_dir_all(&dir).unwrap();
            Temp(resolve(&dir))
        }

        /// Write `text` at `rel`, making its folders.
        pub fn file(&self, rel: &str, text: &str) -> PathBuf {
            let path = self.0.join(rel);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(&path, text).unwrap();
            path
        }
    }

    impl Drop for Temp {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn a_path_resolves_to_the_disks_spelling_and_keeps_what_is_missing() {
        let temp = Temp::new("resolve");
        temp.file("Island/Content/a.verse", "");
        let asked = temp.0.join("Island").join("Content").join("..").join("Content").join(".").join("missing").join("x.txt");
        assert_eq!(resolve(&asked), temp.0.join("Island").join("Content").join("missing").join("x.txt"));
        // Windows spells what exists as the disk does.
        #[cfg(windows)]
        assert_eq!(resolve(&temp.0.join("island").join("CONTENT")), temp.0.join("Island").join("Content"));
        assert!(!text(&resolve(&temp.0)).starts_with(r"\\?\"));
        assert!(under(&temp.0.join("Island/Content/a.verse"), &temp.0.join("Island")) && under(&temp.0, &temp.0));
        assert!(!under(&temp.0.join("Island2"), &temp.0.join("Island")) && !under(&temp.0, &temp.0.join("Island")));
        assert_eq!(relative(&temp.0.join("Island/Content/a.verse"), &temp.0.join("Island")), Some("Content/a.verse".into()));
        assert_eq!(relative(&temp.0, &temp.0), Some(".".into()));
    }

    #[test]
    fn a_pick_is_its_island_and_a_kind_is_what_the_disk_shows_then_what_was_saved() {
        let temp = Temp::new("kind");
        let project = temp.file("Island/Island.uefnproject", "{}");
        temp.file("Island/Content/a.verse", "");
        temp.file("Repo/Content/readme.md", "");
        temp.file("Game/Game.uproject", "{}");
        temp.file("Game/Content/x.uasset", "");
        temp.file("Plain/a.txt", "");
        let at = |rel: &str| text(&temp.0.join(rel));
        assert_eq!(project_root_path(&text(&project)), Ok(temp.0.join("Island")));
        assert_eq!(project_root_path(&at("Island/Content")), Ok(temp.0.join("Island")));
        assert_eq!(project_root_path(&at("Repo/Content")), Ok(temp.0.join("Repo").join("Content")));
        assert_eq!(project_root_path(&at("Plain/a.txt")), Err("Pick a project folder, not a file.".into()));
        assert_eq!(project_root_path(&at("Nope")), Err(format!("Folder not found: {}", at("Nope"))));
        assert_eq!(project_root_path("  "), Err("No project folder selected.".into()));

        let mut known = Known::default();
        assert_eq!([known.kind(""), known.kind(&at("Island")), known.kind(&at("Game")), known.kind(&at("Repo")), known.kind(&at("Plain"))], [UEFN, UEFN, FOLDER, UEFN, FOLDER]);
        // A repo the person added as a folder stays one, whatever folders it has.
        known.kinds.insert(project_slug(&at("Repo")), FOLDER.into());
        known.kinds.insert(project_slug(&at("Island")), FOLDER.into());
        assert_eq!([known.kind(&at("Repo")), known.kind(&at("Island"))], [FOLDER, UEFN]);
        let slug = project_slug(&at("Plain"));
        assert!(slug.starts_with("Plain_") && slug.len() == "Plain_".len() + 16 && project_slug(" ") == "_no_project");
        assert_eq!(project_slug("C:/x/My Game!"), project_slug("C:/x/My Game!").replace(' ', "_"));
    }

    #[test]
    fn an_islands_workspace_is_its_code_workspace_then_its_content_folder() {
        let temp = Temp::new("workspace");
        // Names no UEFN install has a saved Verse project for.
        temp.file("DuckyTestIsle/Isle.uefnproject", r#"{"plugins": [{"name": "Other"}, {"name": "DuckyTestMount", "bIsRoot": true}]}"#);
        temp.file("DuckyTestIsle/Content/a.verse", "");
        let island = temp.0.join("DuckyTestIsle");
        assert_eq!(island_mount_name(&island), "DuckyTestMount");
        assert_eq!(verse_workspace_folders(&island, &temp.0), [folder("DuckyTestIsle", &island.join("Content"))]);

        temp.file("Digests/BuiltIn/Fortnite/Fortnite.digest.verse", "");
        temp.file("Digests/BuiltIn/Verse/Verse.digest.verse", "");
        temp.file("Digests/BuiltIn/Other/x.verse", "");
        let workspace = serde_json::json!({"folders": [
            {"name": "Mine", "path": text(&island.join("Content"))}, {"path": ""}, "junk",
            {"name": "Built-in Digests", "path": text(&temp.0.join("Digests").join("BuiltIn"))},
        ]});
        temp.file("DuckyTestIsle/Isle.code-workspace", &workspace.to_string());
        let builtin = temp.0.join("Digests").join("BuiltIn");
        let expected = [folder("Mine", &island.join("Content")), folder("/Fortnite.com", &builtin.join("Fortnite")), folder("/Verse.org", &builtin.join("Verse"))];
        assert_eq!(verse_workspace_folders(&island, &temp.0), expected);
    }
}
