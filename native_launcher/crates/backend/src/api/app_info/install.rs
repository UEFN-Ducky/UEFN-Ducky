//! Installed or portable (`frontend/install_info.py`): Windows asks Setup's uninstall
//! entry in the registry, Linux and macOS say how this copy got here.
#![allow(clippy::disallowed_methods)] // the whole module runs inside spawn_blocking

use std::path::Path;

use serde_json::{json, Value};

/// NEVER change: `MyAppId` of `release/installer/UEFN-Ducky.iss`, with the `_is1` Inno
/// Setup appends. Another id would make every install look portable.
#[cfg(windows)]
const UNINSTALL_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{EAD694ED-E221-40B0-909B-AFFD7F683C9E}_is1";

/// `get_install_info`. `versions` is the folder the downloaded app versions live in.
pub fn info(versions: &Path) -> Value {
    let mut info = json!({
        "installed": false,
        "install_location": null,
        "install_scope": null,
        "registry_version": null,
        "uninstall_command": null,
        "quiet_uninstall_command": null,
    });
    #[cfg(not(windows))]
    {
        let (kind, _) = (kind(), versions);
        info["install_kind"] = kind.into();
        info["install_label"] = label(kind).into();
    }
    #[cfg(windows)]
    if let Some(([location, version, uninstall, quiet], scope)) = uninstall_entry() {
        let location = location.unwrap_or_default().trim().to_string();
        if !location.is_empty() && started_inside(Path::new(&location), versions) {
            info["installed"] = true.into();
            info["install_location"] = location.into();
            info["install_scope"] = scope.into();
            info["registry_version"] = version.into();
            info["uninstall_command"] = uninstall.into();
            info["quiet_uninstall_command"] = quiet.into();
        }
    }
    info
}

/// The uninstall entry's values and whose it is: the person's first, then the PC's, each
/// in the 64-bit view and then the 32-bit one (a legacy or repackaged Setup).
/// ponytail: text values up to 2,047 characters; a longer or non-text one reads as absent.
#[cfg(windows)]
fn uninstall_entry() -> Option<([Option<String>; 4], &'static str)> {
    use windows_sys::Win32::System::Registry::{RegCloseKey, RegGetValueW, RegOpenKeyExW, HKEY, HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE, KEY_READ, KEY_WOW64_32KEY, KEY_WOW64_64KEY, RRF_RT_REG_SZ};

    let wide = |text: &str| text.encode_utf16().chain([0]).collect::<Vec<u16>>();
    let key = wide(UNINSTALL_KEY);
    for (root, scope) in [(HKEY_CURRENT_USER, "user"), (HKEY_LOCAL_MACHINE, "machine")] {
        for view in [KEY_WOW64_64KEY, KEY_WOW64_32KEY] {
            let mut opened: HKEY = std::ptr::null_mut();
            // SAFETY: `key` ends in NUL and `opened` takes the one handle, closed below.
            if unsafe { RegOpenKeyExW(root, key.as_ptr(), 0, KEY_READ | view, &mut opened) } != 0 {
                continue;
            }
            let values = ["InstallLocation", "DisplayVersion", "UninstallString", "QuietUninstallString"].map(|name| {
                let name = wide(name);
                let (mut text, mut bytes) = (vec![0u16; 2048], 4096u32);
                // SAFETY: `name` ends in NUL; `text` holds `bytes` bytes.
                let status = unsafe { RegGetValueW(opened, std::ptr::null(), name.as_ptr(), RRF_RT_REG_SZ, std::ptr::null_mut(), text.as_mut_ptr().cast(), &mut bytes) };
                (status == 0).then(|| String::from_utf16_lossy(&text[..(bytes as usize / 2).min(text.len())]).trim_end_matches('\0').to_string())
            });
            // SAFETY: the handle opened above, closed once.
            unsafe { RegCloseKey(opened) };
            return Some((values, scope));
        }
    }
    None
}

/// The program that was started lives in the install folder (`_exe_under_install_location`):
/// a portable copy run from Downloads stays portable on a PC that also has an install.
/// A version Ducky downloaded by itself counts as the install it belongs to.
#[cfg(windows)]
fn started_inside(location: &Path, versions: &Path) -> bool {
    let named = std::env::var_os("UEFN_DUCKY_LAUNCHER_EXE").filter(|exe| !exe.to_string_lossy().trim().is_empty()).map(std::path::PathBuf::from);
    let own = std::env::current_exe().unwrap_or_default();
    belongs(named.as_deref(), &own, location, versions, apart_from_the_install)
}

/// `named`: the program a hand-off says was started; `own`: this one.
#[cfg(windows)]
fn belongs(named: Option<&Path>, own: &Path, location: &Path, versions: &Path, apart: impl FnOnce() -> bool) -> bool {
    let under = |file: &Path, folder: &Path| match (file.parent().and_then(|parent| parent.canonicalize().ok()), folder.canonicalize()) {
        (Some(parent), Ok(folder)) => parent.starts_with(folder),
        _ => false,
    };
    match named {
        Some(named) => under(named, location),
        None => under(own, location) || (under(own, versions) && !apart() && location.join("UEFN-Ducky.exe").is_file()),
    }
}

/// `app_versions.apart_from_the_install`: a test copy, or one on another profile folder
/// (a sandbox with its own LOCALAPPDATA). The registry still names the person's real
/// install, which such a copy is no part of.
#[cfg(windows)]
fn apart_from_the_install() -> bool {
    use windows_sys::Win32::UI::Shell::{FOLDERID_LocalAppData, FOLDERID_Profile};

    use crate::api::project_paths::known_folder;

    if std::env::var_os("DUCKY_SANDBOX").is_some_and(|set| !set.is_empty()) {
        return true;
    }
    let same = |name: &str, real: &Path| {
        let set = std::env::var_os(name).unwrap_or_default();
        !set.is_empty() && matches!((Path::new(&set).canonicalize(), real.canonicalize()), (Ok(set), Ok(real)) if set == real)
    };
    // The shell knows the profile from the logon, not the environment.
    let Some(profile) = known_folder(&FOLDERID_Profile) else { return false };
    !same("USERPROFILE", &profile) || known_folder(&FOLDERID_LocalAppData).is_some_and(|local| !same("LOCALAPPDATA", &local))
}

/// `install_kind` off Windows. Linux: flatpak, appimage, deb, rpm, aur or source; macOS:
/// app (inside a `*.app/Contents/MacOS` bundle) or source.
/// ponytail: the package managers are asked with no 10 s limit; they look one file up in
/// a local database, and a slow one holds a blocking thread, never the async workers.
#[cfg(not(windows))]
fn kind() -> &'static str {
    use std::process::{Command, Stdio};

    let exe = std::env::current_exe().and_then(|exe| exe.canonicalize()).unwrap_or_default();
    if cfg!(target_os = "macos") {
        let parts: Vec<String> = exe.iter().map(|part| part.to_string_lossy().into_owned()).collect();
        let bundled = parts.windows(3).any(|at| at[0].ends_with(".app") && at[1] == "Contents" && at[2] == "MacOS");
        return if bundled { "app" } else { "source" };
    }
    if Path::new("/.flatpak-info").is_file() {
        return "flatpak";
    }
    if std::env::var_os("APPIMAGE").is_some_and(|image| Path::new(&image).is_file()) {
        return "appimage";
    }
    // Only system folders can belong to a package; a checkout never asks the package managers.
    if exe.starts_with("/usr") || exe.starts_with("/opt") {
        for (kind, program, flag) in [("deb", "dpkg", "-S"), ("rpm", "rpm", "-qf"), ("aur", "pacman", "-Qo")] {
            let asked = Command::new(program).arg(flag).arg(&exe).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).status();
            if asked.is_ok_and(|status| status.success()) {
                return kind;
            }
        }
    }
    "source"
}

/// `install_label`: what Settings shows, e.g. `AppImage · x86_64`.
/// ponytail: the machine is the one this program was built for, in Python's words for
/// x86_64 and Apple's arm64; a 32-bit build would read `x86` where Python says `i686`.
#[cfg(not(windows))]
fn label(kind: &str) -> String {
    let name = match kind {
        "appimage" => "AppImage",
        "deb" => "Debian package",
        "rpm" => "RPM package",
        "flatpak" => "Flatpak",
        "aur" => "AUR package",
        "source" => "Source checkout",
        "app" => "App",
        other => other,
    };
    let machine = if cfg!(all(target_os = "macos", target_arch = "aarch64")) { "arm64" } else { std::env::consts::ARCH };
    format!("{name} · {machine}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_install_names_its_folder() {
        let info = info(Path::new("no-such-versions-folder"));
        // Installed or not is this PC's own state (a test started from the installed app
        // inherits the program it names): only that the answer hangs together.
        assert_eq!(info["installed"] == true, info["install_location"].is_string() && info["install_scope"].is_string(), "{info}");
        #[cfg(not(windows))]
        assert!(info["install_kind"] == "source" && info["install_label"].as_str().unwrap().starts_with("Source checkout · "), "{info}");
    }

    #[cfg(windows)]
    #[test]
    fn only_a_program_in_the_install_folder_is_the_install() {
        let root = std::env::temp_dir().join(format!("ducky-install-test-{}", std::process::id()));
        let (install, versions, downloads) = (root.join("Install"), root.join("versions"), root.join("Downloads"));
        for folder in [&install, &versions.join("app"), &downloads] {
            std::fs::create_dir_all(folder).unwrap();
        }
        let (installed, portable, updated) = (install.join("Ducky-Backend.exe"), downloads.join("UEFN-Ducky.exe"), versions.join("app").join("Ducky-Backend.exe"));
        let here = |named: Option<&Path>, own: &Path, apart: bool| belongs(named, own, &install, &versions, || apart);
        assert!(here(None, &installed, false) && !here(None, &portable, false));
        // What a hand-off names wins over where this program is.
        assert!(here(Some(&install.join("UEFN-Ducky.exe")), &portable, false) && !here(Some(&portable), &installed, false));
        // A downloaded version is the install's once the install is on disk, and never a test copy's.
        assert!(!here(None, &updated, false));
        std::fs::write(install.join("UEFN-Ducky.exe"), b"").unwrap();
        assert!(here(None, &updated, false) && !here(None, &updated, true));
        assert!(!belongs(None, &installed, &root.join("gone"), &versions, || false));
        std::fs::remove_dir_all(&root).unwrap();
    }
}
