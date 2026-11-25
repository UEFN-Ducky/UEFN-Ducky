---
description: "Plugins on Windows and Linux: code that runs on both, which build each platform gets, releasing Windows + Linux x86_64 + Linux arm64, and the fixes for Linux-only failures"
metadata:
  order: 4
  label: "Plugins: Windows and Linux"
  default_enabled: false
  load_condition: "Writing or fixing a plugin that must run on Linux as well as Windows, a 'Windows only' / 'no Linux build yet' message, a Linux user's plugin bug, or releasing a plugin for more than one platform"
---

# Plugins on Windows and Linux

UEFN Ducky runs on **Windows x64**, **Linux x86_64** and **Linux arm64**. Write every
plugin so it runs on all three, then ship a build for each.

## Which build runs where

| What | Runs on |
|---|---|
| A draft (`ducky_plugin_*`, source) | Any platform: it runs from its `.py` files. |
| A compiled Store build | Only the platform it was compiled on. Windows builds a `.pyd`, Linux a `.so`; `plugin.json` names it in `python_abi` (`cp313-win_amd64`, `cpython-313-x86_64-linux-gnu`, `cpython-313-aarch64-linux-gnu`). |

- The Store keeps **one build per platform** for each version, and every app downloads
  its own. With no build for the viewer's platform, the Store card says **Windows only**
  and an installed copy says **This plugin has no Linux build yet**. That is a missing
  build, not a bug in the code.
- `ducky_plugin_publish` (Ducky Account) compiles **on this PC, for this PC's OS and
  CPU only**: Nuitka can't cross-compile. Published from Windows, the version ships
  Windows only. Tell the user which platforms a release covers before publishing.
- A plugin with its own repo ships all three in one release (below).

## Write code that runs on both

A draft that works on Windows can still fail to even load on Linux. Check every row.

| Windows habit | Do this instead |
|---|---|
| `C:\…`, `%LOCALAPPDATA%`, `os.environ["LOCALAPPDATA"]` / `APPDATA` / `USERPROFILE` (a `KeyError` on Linux) | `api.local_dir()` for this PC's files, `api.data` for records, `Path.home()` and `pathlib` joins. Ducky's own data is `%LOCALAPPDATA%/UEFN-Ducky/` on Windows and `~/.local/share/UEFN-Ducky/` on Linux. |
| `import winreg`, `ctypes.windll`, `win32api` / `win32gui`, `msvcrt` at the top of a module | The plugin fails to load on Linux (`ImportError` / `AttributeError`). Import inside `if sys.platform == "win32":` and give Linux its own branch, or a clear `{"ok": False, "error": "Windows only"}`. |
| `subprocess.CREATE_NO_WINDOW`, `STARTUPINFO`, `creationflags=` | Those names exist only on Windows: pass them only when `sys.platform == "win32"`. |
| `"blender.exe"`, `C:\Program Files\…`, `py -3`, `cmd /c`, `start`, `powershell` | `shutil.which("blender")`, `sys.executable` for Python, a list of arguments with no shell. Search Ducky's own finders first: `ducky_find_tools("blender")`. |
| `os.startfile(path)` (missing on Linux) | Links: `webbrowser.open(url)`. Files and folders: `os.startfile` on Windows, `subprocess.Popen(["xdg-open", path])` on Linux. |
| `open(path)` with no encoding; `subprocess` text with no encoding | Always `encoding="utf-8"`; for program output also `errors="replace"`. Windows and Linux default to different encodings. |
| `Assets/Icon.png` in code, `assets/icon.png` on disk | Linux file names are case-sensitive: match the exact case everywhere, `plugin.json` included. |
| Shipped shell scripts saved with CRLF | `bash` fails with `$'\r': command not found`. Save scripts with LF line endings. |
| `os.kill(pid, signal.CTRL_C_EVENT)`, `taskkill` | `proc.terminate()` / `proc.kill()`. |
| Replacing a file another process has open | Windows raises `PermissionError`, Linux allows it. Close the file before `os.replace`, and retry a few times on Windows. |
| `win32gui` windows, `PIL.ImageGrab`, `pyautogui` for the screen | Linux has X11 and Wayland, and Wayland allows no grabbing. Call Ducky's capture and window tools with `api.call_tool` (`ducky_find_tools("screenshot")`). |
| Assuming UEFN runs on this PC | On Linux UEFN runs under Proton, or on a linked Windows PC. Reach it only through `api.listener` and the UEFN tools. |

`ducky_plugin_validate` doesn't catch these: search your own backend for `windll`,
`winreg`, `win32`, `LOCALAPPDATA`, `APPDATA`, `.exe`, `startfile`, `CREATE_NO_WINDOW` and
`powershell` before you say it's done.

## Release Windows, Linux x86_64 and Linux arm64 together

This applies to a plugin with its own repo and these scripts:

- `scripts/build_zip.py` with `build_compiled_zip(release=True, target="")`. `target`
  is `""` for Windows, or `linux-x86_64` / `linux-arm64` on that Linux machine. Each
  build writes `deploy/<id>-<version>[-<target>]-compiled.ducky-plugin.zip`.
- `scripts/release.py`, which uploads (`upload_with_ticket`, `mcp_call`).
- `scripts/commit_before_store.py`, which commits and pushes before the release.

1. **Raise `version`** in `plugin.json`. The Store never takes the same number twice, a
   failed upload's included.
2. **Start Docker Desktop** (`docker desktop start`). The Linux builds run in
   `python:3.13-bookworm` containers, and arm64 runs under QEMU.
3. **Run it** from UEFN Ducky's repo, with `DUCKYOS_API_KEY` set:
   ```bash
   cd <UEFN-Ducky>/ducky_app
   python -m backend.uefn_plugins.release_platforms <plugin repo> --publish --changelog "What changed"
   ```
   It builds Windows on this PC, then Linux x86_64 and arm64 in Docker. It uploads all
   three and publishes one release that lists every build. If any build fails, nothing
   uploads.
4. **Run one release at a time.** Two at once run the PC out of memory. A plugin takes
   10–20 min; a big one on arm64 takes up to an hour. Run it with `background=true`.

## Linux build failures and their fixes

| Error | Fix |
|---|---|
| `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine` | Docker Desktop isn't running (an app update or restart closes it). Run `docker desktop start`, then release again. |
| `exec format error` in the arm64 build | Docker lost its arm64 emulation (it goes when Docker restarts). The release registers it again before each arm64 build: release again. |
| `the build wrote no *-linux-x86_64-compiled.ducky-plugin.zip` | The plugin's `build_compiled_zip` ignores `target`, or names the zip differently. Copy `target=` handling and the zip name from a plugin that has them (Google's `scripts/build_zip.py`). |
| `--target linux-arm64 builds on a linux-arm64 machine` | A Linux build ran on the wrong machine. Build Linux only through `release_platforms`, never `build_zip --target` on Windows. |
| `No module named iced_x86` | An old copy of UEFN Ducky's repo: pull `main` (the release installs it in the container now). |
| `no UEFN-Ducky` / the build kit isn't found | Run from UEFN Ducky's `ducky_app` folder; the release sets `DUCKY_REPO` for the plugin's build. |
| `Build failed: The build does not load…` with `ImportError: winreg` or `ctypes.windll` | A Windows-only import at module level: move it inside `if sys.platform == "win32":`. |
| Inno Setup `Out of memory`, or Nuitka / LLVM killed | Another release or build is running: let it finish, then run this one alone. |
| `version already exists` | Raise `version` and release again. |

After a release, the Store card shows every platform and no **Windows only** badge. On
a Linux PC, `ducky_store_install` of the plugin loads it with no "no Linux build" error.
