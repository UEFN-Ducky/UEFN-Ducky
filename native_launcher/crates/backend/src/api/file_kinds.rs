//! What a file is by its name and first bytes: text, image, model, audio, video, an Unreal
//! asset or another binary (`frontend/ui_web/file_kinds.py`). Nothing here reads a disk;
//! on Windows the first mime lookup reads the registry's types once, so call it from a
//! blocking task.

use serde_json::{json, Value};

const IMAGE: &[&str] = &[".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".svg"];
const MODEL: &[&str] = &[".fbx", ".glb", ".gltf", ".obj", ".stl", ".ply", ".dae"];
const AUDIO: &[&str] = &[".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac"];
const VIDEO: &[&str] = &[".mp4", ".webm", ".mov", ".m4v", ".ogv"];
const UNREAL: &[&str] = &[".umap", ".uasset", ".ubulk", ".uexp", ".uptod", ".udic", ".ufont", ".ushaderbytecode", ".upipelinecache"];
/// Never opened as text; the last six are pictures no browser draws.
const BINARY: &[&str] = &[
    ".exe", ".dll", ".pdb", ".zip", ".7z", ".rar", ".pak", ".bin", ".dat", ".ttf", ".otf", ".woff", ".woff2", ".tga", ".psd", ".tif", ".tiff",
    ".exr", ".hdr",
];
const TEXT: &[&str] = &[
    ".verse", ".versetest", ".vson", ".py", ".txt", ".json", ".md", ".cfg", ".ini", ".toml", ".yaml", ".yml", ".xml", ".csv", ".log", ".ts",
    ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css", ".scss", ".less", ".html", ".htm", ".rs", ".php", ".c", ".h", ".cpp", ".cxx", ".cc", ".hpp",
    ".hh", ".go", ".gitignore", ".gitattributes", ".editorconfig", ".dockerignore", ".env", ".htaccess", ".npmrc", ".prettierrc", ".eslintrc",
];
/// Whole names (lowercase) that are text whatever their suffix.
const TEXT_NAMES: &[&str] = &[
    ".gitignore", ".gitattributes", ".editorconfig", ".dockerignore", ".env", ".env.local", ".env.development", ".env.production", ".npmrc",
    ".prettierrc", ".eslintrc", ".htaccess", "dockerfile", "makefile", "gnumakefile", "cmakelists.txt", "license", "licence", "copying",
    "readme", "readme.md", "readme.txt", "authors", "contributors", "changelog", "changes", "todo", "gemfile", "rakefile", "procfile",
    "vagrantfile", "jenkinsfile", "pipfile", "poetry.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
];
const TEXT_STEMS: &[&str] = &["readme", "license", "licence", "copying", "authors", "changelog", "changes", "todo"];
/// Dotfiles the tree shows with "Show hidden" off.
const VISIBLE_DOTFILES: &[&str] = &[".gitignore", ".gitattributes", ".editorconfig", ".dockerignore", ".env", ".uefnproject"];
/// Dot folders the tree never shows.
const NEVER_DOT_DIRS: &[&str] = &[".git", ".svn", ".hg"];

/// Types the browser checks strictly: never from a table the PC can change.
const WEB_TYPES: &[(&str, &str)] = &[
    (".js", "text/javascript"), (".mjs", "text/javascript"), (".css", "text/css"), (".html", "text/html"), (".htm", "text/html"),
    (".json", "application/json"), (".map", "application/json"), (".svg", "image/svg+xml"), (".wasm", "application/wasm"),
    (".woff2", "font/woff2"), (".woff", "font/woff"),
];
/// What `mime_for_path` answers for a suffix `mimetypes` does not know.
const FALLBACK_TYPES: &[(&str, &str)] = &[
    (".svg", "image/svg+xml"), (".glb", "model/gltf-binary"), (".gltf", "model/gltf+json"), (".fbx", "application/octet-stream"),
    (".obj", "text/plain"), (".stl", "model/stl"), (".ply", "application/x-ply"), (".dae", "model/vnd.collada+xml"),
];
const MEDIA_TYPES: &[(&str, &str)] = &[
    (".mp3", "audio/mpeg"), (".wav", "audio/wav"), (".ogg", "audio/ogg"), (".m4a", "audio/mp4"), (".aac", "audio/aac"), (".flac", "audio/flac"),
    (".mp4", "video/mp4"), (".webm", "video/webm"), (".mov", "video/quicktime"), (".m4v", "video/x-m4v"), (".ogv", "video/ogg"),
];
/// Python 3.13's `mimetypes` table (`types_map`, strict).
/// On Windows the registry's types come first ([`registry_types`]).
#[rustfmt::skip]
const PY_TYPES: &[(&str, &str)] = &[
    (".3g2", "audio/3gpp2"), (".3gp", "audio/3gpp"), (".3gpp", "audio/3gpp"), (".3gpp2", "audio/3gpp2"), (".a", "application/octet-stream"),
    (".aac", "audio/aac"), (".adts", "audio/aac"), (".ai", "application/postscript"), (".aif", "audio/x-aiff"), (".aifc", "audio/x-aiff"),
    (".aiff", "audio/x-aiff"), (".ass", "audio/aac"), (".au", "audio/basic"), (".avi", "video/x-msvideo"), (".avif", "image/avif"),
    (".bat", "text/plain"), (".bcpio", "application/x-bcpio"), (".bin", "application/octet-stream"), (".bmp", "image/bmp"), (".c", "text/plain"),
    (".cdf", "application/x-netcdf"), (".cpio", "application/x-cpio"), (".csh", "application/x-csh"), (".css", "text/css"), (".csv", "text/csv"),
    (".dll", "application/octet-stream"), (".doc", "application/msword"), (".dot", "application/msword"), (".dvi", "application/x-dvi"),
    (".eml", "message/rfc822"), (".eps", "application/postscript"), (".etx", "text/x-setext"), (".exe", "application/octet-stream"),
    (".gif", "image/gif"), (".gtar", "application/x-gtar"), (".h", "text/plain"), (".h5", "application/x-hdf5"), (".hdf", "application/x-hdf"),
    (".heic", "image/heic"), (".heif", "image/heif"), (".htm", "text/html"), (".html", "text/html"), (".ico", "image/vnd.microsoft.icon"),
    (".ief", "image/ief"), (".jpe", "image/jpeg"), (".jpeg", "image/jpeg"), (".jpg", "image/jpeg"), (".js", "text/javascript"),
    (".json", "application/json"), (".ksh", "text/plain"), (".latex", "application/x-latex"), (".loas", "audio/aac"), (".m1v", "video/mpeg"),
    (".m3u", "application/vnd.apple.mpegurl"), (".m3u8", "application/vnd.apple.mpegurl"), (".man", "application/x-troff-man"),
    (".markdown", "text/markdown"), (".md", "text/markdown"), (".me", "application/x-troff-me"), (".mht", "message/rfc822"),
    (".mhtml", "message/rfc822"), (".mif", "application/x-mif"), (".mjs", "text/javascript"), (".mov", "video/quicktime"),
    (".movie", "video/x-sgi-movie"), (".mp2", "audio/mpeg"), (".mp3", "audio/mpeg"), (".mp4", "video/mp4"), (".mpa", "video/mpeg"),
    (".mpe", "video/mpeg"), (".mpeg", "video/mpeg"), (".mpg", "video/mpeg"), (".ms", "application/x-troff-ms"), (".n3", "text/n3"),
    (".nc", "application/x-netcdf"), (".nq", "application/n-quads"), (".nt", "application/n-triples"), (".nws", "message/rfc822"),
    (".o", "application/octet-stream"), (".obj", "application/octet-stream"), (".oda", "application/oda"), (".opus", "audio/opus"),
    (".p12", "application/x-pkcs12"), (".p7c", "application/pkcs7-mime"), (".pbm", "image/x-portable-bitmap"), (".pdf", "application/pdf"),
    (".pfx", "application/x-pkcs12"), (".pgm", "image/x-portable-graymap"), (".pl", "text/plain"), (".png", "image/png"),
    (".pnm", "image/x-portable-anymap"), (".pot", "application/vnd.ms-powerpoint"), (".ppa", "application/vnd.ms-powerpoint"),
    (".ppm", "image/x-portable-pixmap"), (".pps", "application/vnd.ms-powerpoint"), (".ppt", "application/vnd.ms-powerpoint"),
    (".ps", "application/postscript"), (".pwz", "application/vnd.ms-powerpoint"), (".py", "text/x-python"), (".pyc", "application/x-python-code"),
    (".pyo", "application/x-python-code"), (".qt", "video/quicktime"), (".ra", "audio/x-pn-realaudio"), (".ram", "application/x-pn-realaudio"),
    (".ras", "image/x-cmu-raster"), (".rdf", "application/xml"), (".rgb", "image/x-rgb"), (".roff", "application/x-troff"), (".rst", "text/x-rst"),
    (".rtf", "text/rtf"), (".rtx", "text/richtext"), (".sgm", "text/x-sgml"), (".sgml", "text/x-sgml"), (".sh", "application/x-sh"),
    (".shar", "application/x-shar"), (".snd", "audio/basic"), (".so", "application/octet-stream"), (".src", "application/x-wais-source"),
    (".srt", "text/plain"), (".sv4cpio", "application/x-sv4cpio"), (".sv4crc", "application/x-sv4crc"), (".svg", "image/svg+xml"),
    (".swf", "application/x-shockwave-flash"), (".t", "application/x-troff"), (".tar", "application/x-tar"), (".tcl", "application/x-tcl"),
    (".tex", "application/x-tex"), (".texi", "application/x-texinfo"), (".texinfo", "application/x-texinfo"), (".tif", "image/tiff"),
    (".tiff", "image/tiff"), (".tr", "application/x-troff"), (".trig", "application/trig"), (".tsv", "text/tab-separated-values"),
    (".txt", "text/plain"), (".ustar", "application/x-ustar"), (".vcf", "text/x-vcard"), (".vtt", "text/vtt"), (".wasm", "application/wasm"),
    (".wav", "audio/x-wav"), (".webm", "video/webm"), (".webmanifest", "application/manifest+json"), (".webp", "image/webp"),
    (".wiz", "application/msword"), (".wsdl", "application/xml"), (".xbm", "image/x-xbitmap"), (".xlb", "application/vnd.ms-excel"),
    (".xls", "application/vnd.ms-excel"), (".xml", "text/xml"), (".xpdl", "application/xml"), (".xpm", "image/x-xpixmap"),
    (".xsl", "application/xml"), (".xwd", "image/x-xwindowdump"), (".zip", "application/zip"),
];
const PY_SUFFIX_MAP: &[(&str, &str)] =
    &[(".svgz", ".svg.gz"), (".taz", ".tar.gz"), (".tbz2", ".tar.bz2"), (".tgz", ".tar.gz"), (".txz", ".tar.xz"), (".tz", ".tar.gz")];
const PY_ENCODINGS: &[&str] = &[".Z", ".br", ".bz2", ".gz", ".xz"];

fn find(table: &[(&'static str, &'static str)], suffix: &str) -> Option<&'static str> {
    table.iter().find(|(known, _)| *known == suffix).map(|(_, mime)| *mime)
}

/// `Path(path).name`: the last part of a path with either slash.
pub fn file_name(path: &str) -> String {
    let path = path.replace('\\', "/");
    let mut parts = path.split('/').filter(|part| !part.is_empty() && *part != ".");
    let first = parts.next().unwrap_or_default();
    match parts.next_back() {
        Some(last) => last.to_string(),
        // A lone `C:name` is the drive and the name.
        None if cfg!(windows) && first.as_bytes().get(1) == Some(&b':') && first.as_bytes()[0].is_ascii_alphabetic() => first[2..].to_string(),
        None => first.to_string(),
    }
}

/// `(Path(name).stem, Path(name).suffix)`: the suffix starts at the last dot that is
/// neither the first nor the last character.
pub fn stem_suffix(name: &str) -> (&str, &str) {
    match name.rfind('.') {
        Some(dot) if dot > 0 && dot < name.len() - 1 => name.split_at(dot),
        _ => (name, ""),
    }
}

/// The name's suffix, lowercase (`_suffix`).
pub fn suffix(name: &str) -> String {
    stem_suffix(&file_name(name)).1.to_lowercase()
}

/// `_basename`: the file name of a tree path, whatever its `abs:` / `ext:` / `ws:N/` start.
pub fn basename(relative_path: &str) -> String {
    let norm = relative_path.trim().replace('\\', "/");
    let starts = |prefix: &str| norm.get(..prefix.len()).is_some_and(|start| start.eq_ignore_ascii_case(prefix));
    let mut rest = if starts("abs:") || starts("ext:") { &norm[4..] } else { norm.as_str() };
    if rest.get(..3).is_some_and(|start| start.eq_ignore_ascii_case("ws:")) {
        rest = &rest[3..];
        rest = rest.split_once('/').map_or(rest, |(_index, path)| path);
    }
    file_name(rest)
}

pub fn is_known_text_filename(name: &str) -> bool {
    let lower = file_name(name).to_lowercase();
    let (stem, suffix) = stem_suffix(&lower);
    TEXT_NAMES.contains(&lower.as_str()) || TEXT_STEMS.contains(&if suffix.is_empty() { lower.as_str() } else { stem })
}

/// A file that opens in the editor to be saved: it never becomes a hex preview there.
pub fn is_text_exempt_from_nul_sniff(name: &str) -> bool {
    is_known_text_filename(name) || TEXT.contains(&suffix(name).as_str())
}

/// `looks_like_text`: no NUL, and few control bytes in the first 4 KB.
pub fn looks_like_text(head: &[u8]) -> bool {
    if head.contains(&0) {
        return false;
    }
    let sample = &head[..head.len().min(4096)];
    let weird = sample.iter().filter(|&&b| b < 9 || ((14..32).contains(&b) && b != 27)).count();
    sample.is_empty() || (weird as f64 / sample.len() as f64) < 0.30
}

/// `posixpath.splitext`: the extension starts at the last dot that follows a character
/// other than a dot.
fn splitext(name: &str) -> (&str, &str) {
    match name.rfind('.') {
        Some(dot) if name[..dot].chars().any(|c| c != '.') => name.split_at(dot),
        _ => (name, ""),
    }
}

/// `mimetypes.guess_type(name)[0]` for a file name.
fn guess_type(name: &str) -> Option<&'static str> {
    let (mut base, mut ext) = splitext(name).to_owned_pair();
    while let Some(long) = find(PY_SUFFIX_MAP, &ext.to_lowercase()) {
        (base, ext) = splitext(&format!("{base}{long}")).to_owned_pair();
    }
    if PY_ENCODINGS.contains(&ext.as_str()) {
        ext = splitext(&base).1.to_string();
    }
    let known = |ext: &str| registry_types().get(ext).map(String::as_str).or_else(|| find(PY_TYPES, ext));
    known(&ext).or_else(|| known(&ext.to_lowercase()))
}

/// The types this PC's registry names per suffix (`HKEY_CLASSES_ROOT\.ext`, value
/// `Content Type`), read once as `mimetypes` does: they replace the table's.
#[cfg(windows)]
fn registry_types() -> &'static std::collections::HashMap<String, String> {
    use windows_sys::Win32::System::Registry::{RegEnumKeyExW, RegGetValueW, HKEY_CLASSES_ROOT, RRF_RT_REG_SZ};

    static TYPES: std::sync::LazyLock<std::collections::HashMap<String, String>> = std::sync::LazyLock::new(|| {
        let mut out = std::collections::HashMap::new();
        let value: Vec<u16> = "Content Type\0".encode_utf16().collect();
        for index in 0.. {
            let (mut name, mut name_len) = ([0u16; 256], 256u32);
            // SAFETY: `name` holds `name_len` characters; the other outputs are not asked for.
            let status = unsafe {
                RegEnumKeyExW(HKEY_CLASSES_ROOT, index, name.as_mut_ptr(), &mut name_len, std::ptr::null(), std::ptr::null_mut(), std::ptr::null_mut(), std::ptr::null_mut())
            };
            match status {
                0 => {}
                234 => continue, // ERROR_MORE_DATA: a name too long to be a suffix
                _ => break,      // ERROR_NO_MORE_ITEMS, or the registry cannot be read
            }
            if name[0] != u16::from(b'.') {
                continue;
            }
            let (mut kind, mut bytes) = ([0u16; 256], 512u32);
            // SAFETY: `name` and `value` end in NUL; `kind` holds `bytes` bytes.
            let status = unsafe { RegGetValueW(HKEY_CLASSES_ROOT, name.as_ptr(), value.as_ptr(), RRF_RT_REG_SZ, std::ptr::null_mut(), kind.as_mut_ptr().cast(), &mut bytes) };
            let kind = String::from_utf16_lossy(&kind[..(bytes as usize / 2).min(256)]);
            let kind = kind.trim_end_matches('\0');
            if status == 0 && !kind.is_empty() {
                out.insert(String::from_utf16_lossy(&name[..name_len as usize]), kind.to_string());
            }
        }
        out
    });
    &TYPES
}

#[cfg(not(windows))]
fn registry_types() -> &'static std::collections::HashMap<String, String> {
    static NONE: std::sync::LazyLock<std::collections::HashMap<String, String>> = std::sync::LazyLock::new(Default::default);
    &NONE
}

trait OwnedPair {
    fn to_owned_pair(self) -> (String, String);
}

impl OwnedPair for (&str, &str) {
    fn to_owned_pair(self) -> (String, String) {
        (self.0.to_string(), self.1.to_string())
    }
}

pub fn mime_for_path(relative_path: &str) -> &'static str {
    let name = basename(relative_path);
    let suffix = suffix(&name);
    let suffix = suffix.as_str();
    let text = || (is_known_text_filename(&name) || TEXT.contains(&suffix)).then_some("text/plain");
    None.or_else(|| find(WEB_TYPES, suffix))
        .or_else(|| guess_type(&name))
        .or_else(|| find(FALLBACK_TYPES, suffix))
        .or_else(|| IMAGE.contains(&suffix).then_some("image/png"))
        .or_else(|| find(MEDIA_TYPES, suffix))
        .or_else(text)
        .unwrap_or("application/octet-stream")
}

/// `classify_by_name`: the kind the name alone decides; None needs the first bytes.
pub fn classify_by_name(relative_path: &str) -> Option<&'static str> {
    let name = basename(relative_path);
    let suffix = suffix(&name);
    let kinds = [(UNREAL, "unreal_asset"), (IMAGE, "image"), (MODEL, "model"), (AUDIO, "audio"), (VIDEO, "video"), (BINARY, "binary"), (TEXT, "text")];
    // A known text name comes after the binary suffixes, as the text suffixes do.
    let by_suffix = kinds.iter().find(|(suffixes, kind)| suffixes.contains(&suffix.as_str()) || (*kind == "text" && is_known_text_filename(&name)));
    by_suffix.map(|(_, kind)| *kind)
}

/// `classify_project_file`: `{kind, mime, editable, name}`. Without bytes an unknown name
/// is text, so the editor can try it.
pub fn classify(relative_path: &str, head: Option<&[u8]>) -> Value {
    let kind = classify_by_name(relative_path).unwrap_or(if head.is_none_or(looks_like_text) { "text" } else { "binary" });
    json!({"kind": kind, "mime": mime_for_path(relative_path), "editable": kind == "text", "name": basename(relative_path)})
}

/// `should_show_dot_entry`: whether the tree shows a name that starts with a dot.
pub fn should_show_dot_entry(name: &str, is_dir: bool, show_hidden: bool) -> bool {
    if !name.starts_with('.') {
        return true;
    }
    if is_dir {
        return !NEVER_DOT_DIRS.contains(&name) && show_hidden;
    }
    VISIBLE_DOTFILES.contains(&name) || is_known_text_filename(name) || show_hidden
}

#[cfg(test)]
mod tests {
    use super::*;

    // `test_file_kinds.py` and `test_project_files_classification.py`, with the names they use.
    #[test]
    fn a_name_decides_the_kind_and_bytes_decide_the_rest() {
        let kind = |path: &str| classify(path, None)["kind"].clone();
        for (path, expected) in [
            ("Content/a.verse", "text"), ("Content/Map.umap", "unreal_asset"), ("abs:C:/x/Pic.PNG", "image"), ("ws:0/m/Mesh.glb", "model"),
            ("a.flac", "audio"), ("a.ogv", "video"), ("tool.exe", "binary"), ("art.psd", "binary"), ("Dockerfile", "text"), (".env.local", "text"),
            ("README.rst", "text"), ("notes", "text"), ("x.unknown", "text"),
        ] {
            assert_eq!(kind(path), expected, "{path}");
        }
        assert_eq!(classify("x.unknown", Some(b"a\0b")), json!({"kind": "binary", "mime": "application/octet-stream", "editable": false, "name": "x.unknown"}));
        assert_eq!(classify("ext:C:\\drop\\LICENSE", Some(b"MIT")), json!({"kind": "text", "mime": "text/plain", "editable": true, "name": "LICENSE"}));
        assert!(looks_like_text(b"") && looks_like_text("héllo\r\n\t".as_bytes()) && !looks_like_text(&[1u8; 40]) && looks_like_text(&[27u8; 40]));
        assert!(is_text_exempt_from_nul_sniff("a.JSON") && is_text_exempt_from_nul_sniff("Makefile") && !is_text_exempt_from_nul_sniff("a.uasset"));
    }

    #[test]
    fn a_mime_is_the_web_type_then_pythons_table_then_the_fallbacks() {
        for (path, mime) in [
            ("a.js", "text/javascript"), ("a.map", "application/json"), ("a.woff", "font/woff"), ("a.PNG", "image/png"), ("a.jpg", "image/jpeg"),
            ("a.ico", "image/vnd.microsoft.icon"), ("a.obj", "application/octet-stream"), ("a.glb", "model/gltf-binary"), ("a.dae", "model/vnd.collada+xml"),
            ("a.wav", "audio/x-wav"), ("a.ogg", "audio/ogg"), ("a.m4v", "video/x-m4v"), ("a.verse", "text/plain"), ("a.py", "text/x-python"),
            ("a.md", "text/markdown"), ("a.tar.gz", "application/x-tar"), ("a.tgz", "application/x-tar"), ("a.gz", "application/octet-stream"),
            ("a.svgz", "image/svg+xml"), (".gitignore", "text/plain"), ("a.uasset", "application/octet-stream"), ("a.", "application/octet-stream"),
        ] {
            // A suffix this PC's registry names (Windows) answers the registry's type.
            let named = |suffix: &str| registry_types().keys().any(|key| key.eq_ignore_ascii_case(suffix));
            let mut suffixes = path.split('.').skip(1).map(|piece| format!(".{piece}")).chain([".tar".to_string(), ".svg".to_string()]);
            if !suffixes.any(|suffix| named(&suffix)) {
                assert_eq!(mime_for_path(path), mime, "{path}");
            }
        }
    }

    #[test]
    fn names_stems_and_suffixes_are_pathlibs() {
        assert_eq!((file_name("a/b/c.txt/"), file_name("a\\b\\."), file_name(""), file_name("..")), ("c.txt".into(), "b".into(), String::new(), "..".into()));
        assert_eq!((stem_suffix("a.tar.gz"), stem_suffix(".env"), stem_suffix("a."), stem_suffix("..a")), (("a.tar", ".gz"), (".env", ""), ("a.", ""), (".", ".a")));
        assert_eq!((basename(" ABS:C:/x/y.verse "), basename("ws:12/deep/z.md"), basename("ws:3"), basename("Ext:D:\\q.png")), ("y.verse".into(), "z.md".into(), "3".into(), "q.png".into()));
    }

    #[test]
    fn a_dot_name_shows_when_it_is_a_known_text_file_or_hidden_files_are_on() {
        let shown = |name: &str, is_dir: bool, hidden: bool| should_show_dot_entry(name, is_dir, hidden);
        assert!(shown("a", false, false) && shown(".gitignore", false, false) && shown(".env.local", false, false) && shown(".uefnproject", false, false));
        assert!(!shown(".DS_Store", false, false) && shown(".DS_Store", false, true));
        assert!(!shown(".vscode", true, false) && shown(".vscode", true, true) && !shown(".git", true, true) && !shown(".hg", true, true));
    }
}
