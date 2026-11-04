"""Browser-tab logins at rest.

Default (``memory``): panes run InPrivate in their own profile folder, so no cookie or
login ever reaches the disk. With the browser pref ``keepLogins`` (``saved``), panes use
the saved profile, and the key Chromium encrypts its cookies with (Local State
``os_crypt.encrypted_key``: user DPAPI, which any program running as the person opens) is
on disk only while Ducky runs. The rest of the time a Windows Hello key holds it: TPM-bound,
never exported, and every unlock needs the person's face, fingerprint or PIN. A watcher
process takes the plain copy out of Local State once Ducky and its browser have exited,
however they exit.
"""

from __future__ import annotations

import base64
import ctypes
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

_log = logging.getLogger("browser_vault")

_HELLO_PROVIDER = "Microsoft Passport Key Storage Provider"
_KEY_LEAF = "UEFN-Ducky/Browser/Logins"
_PROMPT = "Unlock your saved Browser logins in UEFN Ducky"
_NTE_BAD_KEYSET = 0x80090016
_NTE_NOT_FOUND = 0x80090011
_PAD_PKCS1 = 0x2
_LEGACY_LOGIN_FILES = ("Network/Cookies", "Network/Cookies-journal", "Cookies", "Cookies-journal",
                       "Login Data", "Login Data-journal", "Web Data", "Web Data-journal")


def keep_logins() -> bool:
    try:
        from frontend.ui_web.plugin_host_api import prefs_plugin_get

        return bool(prefs_plugin_get("browser").get("keepLogins"))
    except Exception:
        return False


def profile_dir(mode: str) -> Path:
    """``saved``: the profile that keeps logins; ``memory``: the one InPrivate panes use."""
    from backend.skills.discovery import appdata_dir

    p = appdata_dir() / ("webview2_browser" if mode == "saved" else "webview2_browser_private")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _local_state(profile: Path) -> Path:
    return profile / "EBWebView" / "Local State"


def _vault(profile: Path) -> Path:
    return profile / "logins.hello"


def _read_state(profile: Path) -> dict:
    try:
        data = json.loads(_local_state(profile).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _write_state(profile: Path, state: dict) -> None:
    _write_atomic(_local_state(profile), json.dumps(state, separators=(",", ":")).encode("utf-8"))


def _sealed_key(state: dict) -> bytes | None:
    """The DPAPI blob of Chromium's cookie key, or None when Local State has none."""
    raw = str((state.get("os_crypt") or {}).get("encrypted_key") or "")
    try:
        blob = base64.b64decode(raw) if raw else b""
    except ValueError:
        return None
    return blob[5:] if blob[:5] == b"DPAPI" else None


def browser_running(profile: Path) -> bool:
    """True while a browser process holds the profile (Chromium's lockfile is open)."""
    lock = profile / "EBWebView" / "lockfile"
    if not lock.exists():
        return False
    try:
        os.replace(lock, lock)  # refused while the browser has it open
    except OSError:
        return True
    return False


class _Hello:
    """One RSA key in the Windows Hello (Passport) key store: encrypt is free, decrypt asks."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("Windows Hello is Windows-only")
        H = ctypes.c_size_t
        lib = ctypes.WinDLL("ncrypt.dll")
        for name, args in (
            ("NCryptOpenStorageProvider", [ctypes.POINTER(H), ctypes.c_wchar_p, ctypes.c_uint32]),
            ("NCryptOpenKey", [H, ctypes.POINTER(H), ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]),
            ("NCryptCreatePersistedKey", [H, ctypes.POINTER(H), ctypes.c_wchar_p, ctypes.c_wchar_p,
                                          ctypes.c_uint32, ctypes.c_uint32]),
            ("NCryptSetProperty", [H, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]),
            ("NCryptFinalizeKey", [H, ctypes.c_uint32]),
            ("NCryptDeleteKey", [H, ctypes.c_uint32]),
            ("NCryptFreeObject", [H]),
        ):
            fn = getattr(lib, name)
            fn.argtypes, fn.restype = args, ctypes.c_long
        for name in ("NCryptEncrypt", "NCryptDecrypt"):
            fn = getattr(lib, name)
            fn.argtypes = [H, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p,
                           ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32]
            fn.restype = ctypes.c_long
        self.lib, self.H = lib, H
        self.name = f"{_user_sid()}//{_KEY_LEAF}"

    @staticmethod
    def _ok(status: int, what: str) -> None:
        if status:
            raise OSError(f"{what} failed (0x{status & 0xFFFFFFFF:08X})")

    def _provider(self) -> ctypes.c_size_t:
        prov = self.H()
        self._ok(self.lib.NCryptOpenStorageProvider(ctypes.byref(prov), _HELLO_PROVIDER, 0),
                 "Opening Windows Hello")
        return prov

    def _open(self, prov: ctypes.c_size_t) -> ctypes.c_size_t | None:
        key = self.H()
        status = self.lib.NCryptOpenKey(prov, ctypes.byref(key), self.name, 0, 0) & 0xFFFFFFFF
        if status in (_NTE_BAD_KEYSET, _NTE_NOT_FOUND):
            return None
        self._ok(status, "Opening the Windows Hello key")
        return key

    def _prop_u32(self, key: ctypes.c_size_t, name: str, value: int) -> int:
        v = ctypes.c_uint32(value)
        return self.lib.NCryptSetProperty(key, name, ctypes.byref(v), 4, 0)

    def _context(self, key: ctypes.c_size_t, hwnd: int) -> None:
        if hwnd:
            h = ctypes.c_size_t(hwnd)
            self.lib.NCryptSetProperty(key, "HWND Handle", ctypes.byref(h), ctypes.sizeof(h), 0)
        msg = ctypes.create_unicode_buffer(_PROMPT)
        self.lib.NCryptSetProperty(key, "Use Context", msg, ctypes.sizeof(msg), 0)

    def exists(self) -> bool:
        prov = self._provider()
        try:
            key = self._open(prov)
            if key is not None:
                self.lib.NCryptFreeObject(key)
            return key is not None
        finally:
            self.lib.NCryptFreeObject(prov)

    def create(self, hwnd: int = 0) -> None:
        """Make the key (Windows Hello asks the person to confirm) unless it exists."""
        prov = self._provider()
        try:
            key = self._open(prov)
            if key is not None:
                self.lib.NCryptFreeObject(key)
                return
            key = self.H()
            self._ok(self.lib.NCryptCreatePersistedKey(prov, ctypes.byref(key), "RSA", self.name, 0, 0),
                     "Creating the Windows Hello key")
            try:
                self._prop_u32(key, "Length", 2048)
                self._prop_u32(key, "Key Usage", 0x1)  # NCRYPT_ALLOW_DECRYPT_FLAG
                # Every decrypt asks: no cached unlock another program could reuse.
                if self._prop_u32(key, "NgcCacheType", 1):
                    self._prop_u32(key, "NgcCacheTypeProperty", 1)
                self._context(key, hwnd)
                self._ok(self.lib.NCryptFinalizeKey(key, 0), "Creating the Windows Hello key")
            finally:
                self.lib.NCryptFreeObject(key)
        finally:
            self.lib.NCryptFreeObject(prov)

    def delete(self) -> None:
        prov = self._provider()
        try:
            key = self._open(prov)
            if key is not None:
                self.lib.NCryptDeleteKey(key, 0)  # frees the handle too
        finally:
            self.lib.NCryptFreeObject(prov)

    def _crypt(self, data: bytes, hwnd: int, *, decrypt: bool) -> bytes:
        prov = self._provider()
        try:
            key = self._open(prov)
            if key is None:
                raise OSError("The Windows Hello key for saved logins is gone")
            try:
                if decrypt:
                    self._context(key, hwnd)
                fn = self.lib.NCryptDecrypt if decrypt else self.lib.NCryptEncrypt
                out = ctypes.create_string_buffer(512)  # one call: a sizing call would ask twice
                size = ctypes.c_uint32()
                self._ok(fn(key, data, len(data), None, out, len(out), ctypes.byref(size), _PAD_PKCS1),
                         "Unlocking with Windows Hello" if decrypt else "Locking with Windows Hello")
                return out.raw[: size.value]
            finally:
                self.lib.NCryptFreeObject(key)
        finally:
            self.lib.NCryptFreeObject(prov)

    def encrypt(self, data: bytes) -> bytes:
        return self._crypt(data, 0, decrypt=False)

    def decrypt(self, data: bytes, hwnd: int = 0) -> bytes:
        return self._crypt(data, hwnd, decrypt=True)


def _user_sid() -> str:
    out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True,
                         timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    sid = out.stdout.strip().rsplit(",", 1)[-1].strip().strip('"')
    if not sid.startswith("S-1-"):
        raise OSError("Couldn't read this Windows user's id")
    return sid


def hello_available() -> bool:
    try:
        _Hello()._provider()
        return True
    except OSError:
        return False


def unlock(hwnd: int = 0, hello: _Hello | None = None) -> str:
    """Put the cookie key back for this run (Windows Hello asks); '' or why the saved logins
    stay locked. The first time it only creates the Hello key: there is nothing locked yet."""
    if browser_running(profile_dir("saved")):
        return ""  # this Ducky (or one before it) already unlocked it
    profile = profile_dir("saved")
    try:
        hello = hello or _Hello()
        vault = _vault(profile)
        if vault.is_file():
            from backend.agent.secrets import protect_plain

            key = hello.decrypt(vault.read_bytes(), hwnd)
            state = _read_state(profile)
            state.setdefault("os_crypt", {})["encrypted_key"] = base64.b64encode(
                b"DPAPI" + protect_plain(key)).decode("ascii")
            _write_state(profile, state)
        else:
            hello.create(hwnd)
    except OSError as exc:
        _log.warning("saved browser logins stay locked: %s", exc)
        return str(exc)
    start_watcher()
    return ""


def lock(hello: _Hello | None = None) -> str:
    """Seal Chromium's cookie key with Windows Hello and take it out of Local State.
    Only while no browser holds the profile. '' when locked (or nothing to lock)."""
    profile = profile_dir("saved")
    if browser_running(profile):
        return "the browser still has the profile open"
    state = _read_state(profile)
    sealed = _sealed_key(state)
    if sealed is None:
        return ""
    try:
        from backend.agent.secrets import unprotect_plain

        _write_atomic(_vault(profile), (hello or _Hello()).encrypt(unprotect_plain(sealed)))
    except OSError as exc:
        return f"not locked, the key stays as it was: {exc}"  # never drop a key we couldn't seal
    del state["os_crypt"]["encrypted_key"]
    _write_state(profile, state)
    return ""


def relock_after(pid: int, wait_s: float = 120.0) -> int:
    """``UEFN-Ducky.exe --browser-relock <pid>``: wait for Ducky and its browser to exit, then lock."""
    from frontend.app_versions import wait_for_exit

    wait_for_exit(pid, timeout_s=4_000_000.0)  # ~46 days: WaitForSingleObject's longest finite wait
    deadline = time.monotonic() + wait_s
    while browser_running(profile_dir("saved")) and time.monotonic() < deadline:
        time.sleep(0.5)
    why = lock()
    if why:
        _log.warning("browser relock: %s", why)
    return 1 if why else 0


def start_watcher() -> None:
    """A process outside Ducky that locks the saved logins once Ducky has exited."""
    from frontend.bundle_root import is_packaged_runtime

    pid = str(os.getpid())
    if is_packaged_runtime():
        cmd, cwd = [sys.executable, "--browser-relock", pid], None
    else:
        cmd, cwd = [sys.executable, "-m", "frontend", "--browser-relock", pid], str(Path(__file__).resolve().parents[2])
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    for extra in (0x01000000, 0) if os.name == "nt" else (0,):  # CREATE_BREAKAWAY_FROM_JOB first
        try:
            subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=flags | extra, close_fds=True)
            return
        except OSError:
            continue


def forget_unlocked_logins() -> None:
    """Memory-only mode: logins an older Ducky left in the saved profile with no Hello lock
    are deleted (cookie key and login files), so nothing stealable stays on disk."""
    profile = profile_dir("saved")
    if _vault(profile).is_file() or browser_running(profile):
        return  # locked by Hello, or in use
    state = _read_state(profile)
    if _sealed_key(state) is None:
        return
    base = profile / "EBWebView" / "Default"
    for rel in _LEGACY_LOGIN_FILES:
        try:
            (base / rel).unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            _log.warning("could not delete %s: %s", rel, exc)
    del state["os_crypt"]["encrypted_key"]
    _write_state(profile, state)
