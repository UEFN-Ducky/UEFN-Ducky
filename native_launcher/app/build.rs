//! Bakes Ducky's app version into the launcher: `DUCKY_APP_VERSION` when the release
//! build sets it, else `__version__` from `ducky_app/frontend/__init__.py`. Also links the
//! EXE's icon and version info (see [`embed_resources`]).

use std::path::{Path, PathBuf};
use std::process::Command;

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
    embed_resources(version.trim());
}

/// The resource script: Ducky's icon (the one PyInstaller puts in UEFN-Ducky.exe) and a
/// version block whose FileDescription Windows shows as the app's name.
fn resource_script(icon: &Path, version: &str) -> String {
    let mut parts: Vec<u32> = version.split('.').map(|p| p.trim().parse().unwrap_or(0)).collect();
    parts.resize(4, 0);
    let nums = parts.iter().map(u32::to_string).collect::<Vec<_>>().join(",");
    let icon = icon.to_string_lossy().replace('\\', "/");
    format!(
        r#"1 ICON "{icon}"
1 VERSIONINFO
FILEVERSION {nums}
PRODUCTVERSION {nums}
FILEOS 0x40004
FILETYPE 0x1
BEGIN
  BLOCK "StringFileInfo"
  BEGIN
    BLOCK "040904B0"
    BEGIN
      VALUE "CompanyName", "UEFN Ducky"
      VALUE "FileDescription", "UEFN Ducky"
      VALUE "FileVersion", "{version}"
      VALUE "InternalName", "Ducky-Launcher"
      VALUE "LegalCopyright", "(c) UEFN Ducky. All rights reserved."
      VALUE "OriginalFilename", "Ducky-Launcher.exe"
      VALUE "ProductName", "UEFN Ducky"
      VALUE "ProductVersion", "{version}"
    END
  END
  BLOCK "VarFileInfo"
  BEGIN
    VALUE "Translation", 0x409, 1200
  END
END
"#
    )
}

/// Explorer, Task Manager and the taskbar read the EXE's own icon and FileDescription.
/// Without them the launcher window showed a blank icon named "Ducky-Launcher".
/// windres (MinGW; `WINDRES` overrides) compiles the script; a release build
/// (`DUCKY_APP_VERSION` set) fails without it rather than ship that blank window.
fn embed_resources(version: &str) {
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }
    println!("cargo:rerun-if-env-changed=WINDRES");
    let icon = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../build/app_icon.ico");
    println!("cargo:rerun-if-changed={}", icon.display());
    let icon = icon.canonicalize().unwrap_or(icon);
    let out = PathBuf::from(std::env::var("OUT_DIR").expect("OUT_DIR"));
    let rc = out.join("launcher.rc");
    let obj = out.join("launcher_res.o");
    std::fs::write(&rc, resource_script(&icon, version)).expect("write launcher.rc");
    let windres = std::env::var("WINDRES").unwrap_or_else(|_| "windres".to_string());
    let built = Command::new(&windres)
        .args(["-O", "coff", "-i"])
        .arg(&rc)
        .arg("-o")
        .arg(&obj)
        .status()
        .map(|s| s.success())
        .unwrap_or(false);
    if built {
        println!("cargo:rustc-link-arg-bins={}", obj.display());
        return;
    }
    let why = format!("{windres} could not compile {} (icon {})", rc.display(), icon.display());
    if std::env::var("DUCKY_APP_VERSION").is_ok_and(|v| !v.trim().is_empty()) {
        panic!("{why}: install MinGW windres (scoop install mingw) or set WINDRES");
    }
    println!("cargo:warning={why}: this dev build has no icon or version info");
}
