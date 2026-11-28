//! Where Epic's verse-lsp is (`verse_editor/lsp/detect.py`): Ducky's own copy under the
//! data folder, else the one shipped with the app, a VS Code extension's or the one in
//! UEFN's `Verse.vsix`; each is copied to the data folder beside its DLLs.
#![allow(clippy::disallowed_methods)] // every function here runs inside spawn_blocking

use std::io;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use crate::api::project_paths::text;

const NAMES: [&str; 3] = ["verse-lsp-latest.exe", "verse-lsp.exe", "uLangServer.exe"];
/// The folder of the verse-lsp shipped with the app; whoever starts the backend names it.
pub const SHIPPED_ENV: &str = "DUCKY_VERSE_LSP_BIN";
const NOT_FOUND: &str = "verse-lsp not found. Install UEFN, or run: py scripts/extract_vscode_ext.py";

fn env(name: &str) -> Option<String> {
    std::env::var(name).ok().map(|value| value.trim().to_string()).filter(|value| !value.is_empty())
}

fn children(dir: &Path) -> Vec<PathBuf> {
    let mut found: Vec<PathBuf> = std::fs::read_dir(dir).into_iter().flatten().flatten().map(|entry| entry.path()).collect();
    found.sort();
    found
}

fn lower_name(path: &Path) -> String {
    path.file_name().map(|name| name.to_string_lossy().to_lowercase()).unwrap_or_default()
}

/// `_find_in_dir`: one of the known names in `dir`, else any `verse-lsp*.exe` under it.
/// ponytail: folders are read in name order (Python takes them as the disk lists them).
fn find_in_dir(dir: &Path) -> Option<PathBuf> {
    if !dir.is_dir() {
        return None;
    }
    if let Some(named) = NAMES.iter().map(|name| dir.join(name)).find(|file| file.is_file()) {
        return Some(named);
    }
    let mut pending = vec![dir.to_path_buf()];
    while let Some(folder) = pending.pop() {
        let entries = children(&folder);
        let lsp = |file: &&PathBuf| file.is_file() && lower_name(file).starts_with("verse-lsp") && lower_name(file).ends_with(".exe");
        if let Some(found) = entries.iter().find(lsp) {
            return Some(found.clone());
        }
        pending.extend(entries.into_iter().rev().filter(|entry| entry.is_dir()));
    }
    None
}

/// `_cache_lsp`: copy the program and the DLLs beside it to `cache`, when newer.
fn cache_lsp(cache: &Path, exe: &Path) -> io::Result<PathBuf> {
    std::fs::create_dir_all(cache)?;
    let modified = |file: &Path| file.metadata().and_then(|meta| meta.modified()).ok();
    let dlls = exe.parent().map(children).unwrap_or_default().into_iter().filter(|file| lower_name(file).ends_with(".dll"));
    for file in std::iter::once(exe.to_path_buf()).chain(dlls) {
        let dest = cache.join(file.file_name().unwrap_or_default());
        if !dest.exists() || modified(&dest) < modified(&file) {
            std::fs::copy(&file, &dest)?;
        }
    }
    Ok(cache.join(exe.file_name().unwrap_or_default()))
}

/// `_extract_from_vsix`: unpack UEFN's extension under `cache` and take its verse-lsp.
fn from_vsix(cache: &Path, vsix: &Path) -> io::Result<Option<PathBuf>> {
    if !vsix.is_file() {
        return Ok(None);
    }
    let out = cache.join("vsix_extract");
    let unpacked = std::fs::File::open(vsix).ok().and_then(|file| zip::ZipArchive::new(file).ok()).and_then(|mut pack| pack.extract(&out).ok());
    if unpacked.is_none() {
        return Ok(None);
    }
    for sub in ["extension/bin/Win64", "extension/bin", "bin/Win64", "bin", "extension"] {
        if let Some(found) = find_in_dir(&sub.split('/').fold(out.clone(), |dir, part| dir.join(part))) {
            return cache_lsp(cache, &found).map(Some);
        }
    }
    Ok(find_in_dir(&out))
}

/// `_vsix_candidates`: `UEFN_DUCKY_FORTNITE_VSCODE` first, then UEFN's install folders.
fn vsix_candidates() -> Vec<PathBuf> {
    let mut dirs: Vec<PathBuf> = env("UEFN_DUCKY_FORTNITE_VSCODE").map(PathBuf::from).into_iter().collect();
    for (name, default) in [("ProgramFiles", r"C:\Program Files"), ("ProgramFiles(x86)", r"C:\Program Files (x86)")] {
        let base = std::env::var(name).unwrap_or_else(|_| default.into());
        if !base.is_empty() {
            dirs.push(Path::new(&base).join("Epic Games").join("Fortnite").join("VSCode"));
        }
    }
    dirs.iter().flat_map(|dir| ["Verse.vsix", "Verse-language.vsix"].map(|name| dir.join(name))).collect()
}

/// `_vscode_extension_dirs`: installed VS Code and Cursor extensions named like Verse.
fn extension_dirs() -> Vec<PathBuf> {
    let home = PathBuf::from(env(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).unwrap_or_default());
    let roots = [".vscode", ".vscode-insiders", ".cursor"].map(|editor| home.join(editor).join("extensions"));
    roots.iter().flat_map(|root| children(root)).filter(|dir| dir.is_dir() && lower_name(dir).contains("verse")).collect()
}

fn found(path: &Path, source: &str) -> Value {
    json!({"available": true, "path": text(path), "source": source, "error": ""})
}

/// `detect_verse_lsp`: `{available, path, source, error}`. An error is a copy that failed.
pub fn detect(data_dir: &Path) -> io::Result<Value> {
    let cache = data_dir.join("verse-lsp");
    if let Some(cached) = find_in_dir(&cache) {
        return Ok(found(&cached, "appdata_cache"));
    }
    if let Some(shipped) = env(SHIPPED_ENV).and_then(|dir| find_in_dir(Path::new(&dir))) {
        return Ok(found(&cache_lsp(&cache, &shipped)?, "builtin_bin"));
    }
    for dir in extension_dirs() {
        let bin = dir.join("bin");
        if let Some(theirs) = find_in_dir(&bin.join("Win64")).or_else(|| find_in_dir(&bin)).or_else(|| find_in_dir(&dir)) {
            return Ok(found(&cache_lsp(&cache, &theirs)?, "vscode_ext"));
        }
    }
    for vsix in vsix_candidates() {
        if let Some(unpacked) = from_vsix(&cache, &vsix)? {
            return Ok(found(&unpacked, "vsix"));
        }
    }
    Ok(json!({"available": false, "path": "", "source": "", "error": NOT_FOUND}))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api::project_paths::tests::Temp;

    #[test]
    fn a_known_name_wins_then_any_verse_lsp_below_and_a_copy_takes_its_dlls() {
        let temp = Temp::new("lsp-detect");
        assert_eq!(find_in_dir(&temp.0.join("missing")), None);
        let deep = temp.file("ext/bin/Win64/verse-lsp-1.2.exe", "deep");
        temp.file("ext/bin/Win64/helper.dll", "dll");
        temp.file("ext/readme.exe", "no");
        assert_eq!(find_in_dir(&temp.0.join("ext")).map(|file| lower_name(&file)), Some("verse-lsp-1.2.exe".into()));
        temp.file("ext/uLangServer.exe", "named");
        assert_eq!(find_in_dir(&temp.0.join("ext")), Some(temp.0.join("ext").join("uLangServer.exe")));

        let cache = temp.0.join("data").join("verse-lsp");
        let copied = cache_lsp(&cache, &deep).unwrap();
        assert_eq!((std::fs::read_to_string(&copied).unwrap(), std::fs::read_to_string(cache.join("helper.dll")).unwrap()), ("deep".into(), "dll".into()));
        assert_eq!(detect(&temp.0.join("data")).unwrap(), found(&copied, "appdata_cache"));
    }
}
