//! Bakes Ducky's app version into the launcher: `DUCKY_APP_VERSION` when the release
//! build sets it, else `__version__` from `ducky_app/frontend/__init__.py`.

use std::path::PathBuf;

fn version_from_python(text: &str) -> Option<String> {
    text.lines().find_map(|line| {
        let rest = line.trim().strip_prefix("__version__")?.trim_start().strip_prefix('=')?.trim();
        let quote = rest.chars().next().filter(|c| *c == '"' || *c == '\'')?;
        let inner = &rest[1..];
        Some(inner[..inner.find(quote)?].to_string())
    })
}

fn main() {
    println!("cargo:rerun-if-env-changed=DUCKY_APP_VERSION");
    let init = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../ducky_app/frontend/__init__.py");
    println!("cargo:rerun-if-changed={}", init.display());
    let version = std::env::var("DUCKY_APP_VERSION")
        .ok()
        .filter(|v| !v.trim().is_empty())
        .or_else(|| std::fs::read_to_string(&init).ok().and_then(|t| version_from_python(&t)))
        .unwrap_or_else(|| "0.0.0".to_string());
    println!("cargo:rustc-env=DUCKY_APP_VERSION={}", version.trim());
}
