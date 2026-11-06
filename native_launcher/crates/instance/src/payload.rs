//! The hand-off body: `{"paths": [...], "links": [...]}` byte for byte as Python's
//! `json.dumps` writes it in `open_files.try_handoff_to_running` (", " / ": " separators,
//! `ensure_ascii`: everything outside space..`~` as lowercase `\uXXXX` UTF-16 units).

use std::ffi::{OsStr, OsString};
use std::path::{Path, PathBuf};

/// Flags that take a value: both are skipped when scanning argv for paths.
pub const VALUE_FLAGS: &[&str] = &["--port"];
pub const DEEP_LINK_PREFIX: &str = "uefn-ducky://";

/// The hand-off body for the user's arguments (argv[0] removed).
pub fn handoff_body(args: &[OsString], home: Option<&Path>) -> Vec<u8> {
    encode_body(&open_paths(args, home), &deep_links(args))
}

/// `cli_open_paths`: absolute paths of existing files, never flags, flag values or `bridge`.
pub fn open_paths(args: &[OsString], home: Option<&Path>) -> Vec<PathBuf> {
    if args.first().is_some_and(|a| a == "bridge") {
        return vec![];
    }
    let mut out = vec![];
    let mut skip_next = false;
    for arg in args {
        if skip_next {
            skip_next = false;
            continue;
        }
        if VALUE_FLAGS.iter().any(|f| arg == *f) {
            skip_next = true;
            continue;
        }
        if starts_with_dash(arg) {
            continue;
        }
        let path = expand_user(arg, home);
        if path.is_file() {
            if let Ok(real) = std::fs::canonicalize(&path) {
                out.push(strip_verbatim(real));
            }
        }
    }
    out
}

/// `cli_deep_links`: `uefn-ducky://` arguments, whitespace trimmed.
pub fn deep_links(args: &[OsString]) -> Vec<String> {
    if args.first().is_some_and(|a| a == "bridge") {
        return vec![];
    }
    args.iter()
        .filter_map(|a| a.to_str())
        .map(str::trim)
        .filter(|a| a.len() >= DEEP_LINK_PREFIX.len() && a[..DEEP_LINK_PREFIX.len()].eq_ignore_ascii_case(DEEP_LINK_PREFIX))
        .map(str::to_string)
        .collect()
}

fn starts_with_dash(arg: &OsStr) -> bool {
    wide(arg).first() == Some(&(b'-' as u16))
}

fn expand_user(arg: &OsStr, home: Option<&Path>) -> PathBuf {
    if let (Some(s), Some(h)) = (arg.to_str(), home) {
        if s == "~" {
            return h.to_path_buf();
        }
        if let Some(rest) = s.strip_prefix("~/").or_else(|| s.strip_prefix("~\\")) {
            return h.join(rest);
        }
    }
    PathBuf::from(arg)
}

/// `canonicalize` gives `\\?\C:\...`; Python's `resolve()` gives `C:\...`.
fn strip_verbatim(p: PathBuf) -> PathBuf {
    let Some(s) = p.to_str() else { return p };
    if let Some(rest) = s.strip_prefix(r"\\?\UNC\") {
        return PathBuf::from(format!(r"\\{rest}"));
    }
    if let Some(rest) = s.strip_prefix(r"\\?\") {
        return PathBuf::from(rest);
    }
    p
}

fn wide(s: &OsStr) -> Vec<u16> {
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        s.encode_wide().collect()
    }
    #[cfg(not(windows))]
    {
        s.to_string_lossy().encode_utf16().collect()
    }
}

/// `json.dumps({"paths": paths, "links": links})`.
pub fn encode_body(paths: &[PathBuf], links: &[String]) -> Vec<u8> {
    let mut out = String::from("{\"paths\": [");
    for (i, p) in paths.iter().enumerate() {
        if i > 0 {
            out.push_str(", ");
        }
        push_json_string(&mut out, &wide(p.as_os_str()));
    }
    out.push_str("], \"links\": [");
    for (i, l) in links.iter().enumerate() {
        if i > 0 {
            out.push_str(", ");
        }
        push_json_string(&mut out, &l.encode_utf16().collect::<Vec<_>>());
    }
    out.push_str("]}");
    out.into_bytes()
}

/// Python's `py_encode_basestring_ascii` over UTF-16 units (lone surrogates included).
fn push_json_string(out: &mut String, units: &[u16]) {
    out.push('"');
    for &u in units {
        match u {
            0x22 => out.push_str("\\\""),
            0x5C => out.push_str("\\\\"),
            0x0A => out.push_str("\\n"),
            0x0D => out.push_str("\\r"),
            0x09 => out.push_str("\\t"),
            0x08 => out.push_str("\\b"),
            0x0C => out.push_str("\\f"),
            0x20..=0x7E => out.push(u as u8 as char),
            _ => out.push_str(&format!("\\u{u:04x}")),
        }
    }
    out.push('"');
}

#[cfg(test)]
mod tests {
    use super::*;

    fn os(v: &[&str]) -> Vec<OsString> {
        v.iter().map(OsString::from).collect()
    }

    /// `json.dumps({"paths": [...], "links": [...]}).encode("utf-8")` from Python
    /// (`open_files.try_handoff_to_running`), captured as bytes.
    const FIXTURE: &[u8] = br#"{"paths": ["C:\\Users\\a\\D\u00e9sktop\\x \"q\".verse", "C:\\e\\\ud83d\ude00\t.txt"], "links": ["uefn-ducky://store/install?id=a&b=\u00fc"]}"#;
    const EMPTY_FIXTURE: &[u8] = br#"{"paths": [], "links": []}"#;

    #[test]
    fn body_matches_python_json_dumps_bytes() {
        let paths = vec![PathBuf::from("C:\\Users\\a\\Désktop\\x \"q\".verse"), PathBuf::from("C:\\e\\😀\t.txt")];
        let links = vec!["uefn-ducky://store/install?id=a&b=ü".to_string()];
        assert_eq!(String::from_utf8(encode_body(&paths, &links)).unwrap(), std::str::from_utf8(FIXTURE).unwrap());
        assert_eq!(encode_body(&[], &[]), EMPTY_FIXTURE);
    }

    #[test]
    fn control_and_del_are_escaped_like_python() {
        let mut s = String::new();
        push_json_string(&mut s, &[0x01, 0x7F, 0x41, 0xD800]);
        assert_eq!(s, r#""\u0001\u007fA\ud800""#);
    }

    #[test]
    fn deep_links_are_trimmed_and_case_blind() {
        let args = os(&["  uefn-ducky://a  ", "UEFN-DUCKY://B", "https://x", "--port", "4200"]);
        assert_eq!(deep_links(&args), vec!["uefn-ducky://a".to_string(), "UEFN-DUCKY://B".to_string()]);
        assert!(deep_links(&os(&["bridge", "uefn-ducky://a"])).is_empty());
    }

    #[test]
    fn open_paths_keep_files_only_in_order() {
        let dir = std::env::temp_dir().join(format!("ducky-instance-paths-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let a = dir.join("a.verse");
        let b = dir.join("b é.txt");
        std::fs::write(&a, "x").unwrap();
        std::fs::write(&b, "x").unwrap();
        let args = vec![
            OsString::from("--background"),
            OsString::from("--port"),
            a.clone().into_os_string(), // a value of --port: skipped
            b.clone().into_os_string(),
            dir.clone().into_os_string(), // a folder: skipped
            OsString::from("uefn-ducky://x"),
            a.clone().into_os_string(),
            dir.join("missing.txt").into_os_string(),
        ];
        let got = open_paths(&args, None);
        let want = vec![strip_verbatim(std::fs::canonicalize(&b).unwrap()), strip_verbatim(std::fs::canonicalize(&a).unwrap())];
        assert_eq!(got, want);
        assert!(got.iter().all(|p| !p.to_string_lossy().starts_with(r"\\?\")));
        assert!(open_paths(&[OsString::from("bridge"), b.into_os_string()], None).is_empty());
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn tilde_expands_to_home() {
        let home = std::env::temp_dir().join(format!("ducky-instance-home-{}", std::process::id()));
        std::fs::create_dir_all(&home).unwrap();
        std::fs::write(home.join("h.txt"), "x").unwrap();
        let got = open_paths(&os(&["~/h.txt"]), Some(&home));
        assert_eq!(got.len(), 1);
        assert!(got[0].ends_with("h.txt"));
        let _ = std::fs::remove_dir_all(home);
    }

    #[test]
    fn verbatim_prefixes_are_removed() {
        assert_eq!(strip_verbatim(PathBuf::from(r"\\?\C:\x\y")), PathBuf::from(r"C:\x\y"));
        assert_eq!(strip_verbatim(PathBuf::from(r"\\?\UNC\srv\share\f")), PathBuf::from(r"\\srv\share\f"));
        assert_eq!(strip_verbatim(PathBuf::from(r"C:\x")), PathBuf::from(r"C:\x"));
    }
}
