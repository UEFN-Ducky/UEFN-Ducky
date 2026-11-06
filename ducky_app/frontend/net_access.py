"""Reach the internet the way the PC's browser does.

Python's urllib ignores Windows' proxy auto-config (a PAC script or WPAD, common on
school and work networks) and tries a host's addresses one after another with the
whole timeout each, IPv6 first. Edge does both right, so on those PCs the site opened
in the browser while the app timed out. ``install()`` (launcher, once) fixes both for
every urllib call in the process:

- ``PacProxyHandler`` asks WinHTTP which proxy a URL takes (what Edge asks), per host,
  cached; anything that fails means direct, never an error.
- ``socket.create_connection`` gets a short first try per address and alternates IPv6
  and IPv4, so a PC with dead IPv6 connects over IPv4 in about 2 s.
"""
from __future__ import annotations

import http.client
import socket
import sys
import threading
import time
import urllib.request
from typing import Any
from urllib.parse import urlsplit

# A non-final address gets this long before the next one is tried (browsers use ~0.3 s;
# 2 s still lets a slow-but-working link through).
FIRST_TRY_S = 2.0
PAC_CACHE_S = 300.0
# WPAD/PAC lookups can hang on a broken network; past this the request goes direct.
PAC_WAIT_S = 3.0

_LOOPBACK = ("localhost", "127.0.0.1", "::1")
_installed = False
_orig_create_connection = socket.create_connection


# ----------------------------------------------------------------------------- fast fallback


def _ordered(infos: list[tuple]) -> list[tuple]:
    """Alternate families, keeping the resolver's order inside each one."""
    first = infos[0][0]
    a = [i for i in infos if i[0] == first]
    b = [i for i in infos if i[0] != first]
    out: list[tuple] = []
    for i in range(max(len(a), len(b))):
        out += a[i:i + 1] + b[i:i + 1]
    return out


def create_connection(address: tuple, timeout: Any = socket._GLOBAL_DEFAULT_TIMEOUT,  # type: ignore[attr-defined]
                      source_address: Any = None, **kwargs: Any) -> socket.socket:
    host, port = address[0], address[1]
    try:
        infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    except OSError:
        return _orig_create_connection(address, timeout, source_address, **kwargs)
    if len({i[0] for i in infos}) < 2:
        return _orig_create_connection(address, timeout, source_address, **kwargs)
    order = _ordered(infos)
    short = FIRST_TRY_S if not isinstance(timeout, (int, float)) else min(float(timeout), FIRST_TRY_S)
    err: OSError | None = None
    for n, info in enumerate(order):
        last = n == len(order) - 1
        try:
            sock = _orig_create_connection(info[4][:2], timeout if last else short, source_address)
        except OSError as exc:
            err = exc
            continue
        if not last:
            # The short timeout is for connecting only. Left on the socket, every TLS
            # handshake and read on it gave up after 2 s (1.2.464: update ranges, slow
            # Store answers and long polls failed while the browser was fine).
            sock.settimeout(socket.getdefaulttimeout() if timeout is socket._GLOBAL_DEFAULT_TIMEOUT else timeout)  # type: ignore[attr-defined]
        return sock
    raise err  # type: ignore[misc]


# ----------------------------------------------------------------------------- proxy auto-config


def _winhttp_config() -> dict[str, Any]:
    """The user's Internet Options proxy settings (what Edge uses): auto, url, proxy."""
    if sys.platform != "win32":
        return {}
    import ctypes
    from ctypes import wintypes

    class Cfg(ctypes.Structure):
        _fields_ = [("fAutoDetect", wintypes.BOOL), ("lpszAutoConfigUrl", wintypes.LPWSTR),
                    ("lpszProxy", wintypes.LPWSTR), ("lpszProxyBypass", wintypes.LPWSTR)]

    cfg = Cfg()
    winhttp = ctypes.WinDLL("winhttp")
    if not winhttp.WinHttpGetIEProxyConfigForCurrentUser(ctypes.byref(cfg)):
        return {}
    out = {"auto": bool(cfg.fAutoDetect), "url": cfg.lpszAutoConfigUrl or "", "proxy": cfg.lpszProxy or ""}
    for p in (cfg.lpszAutoConfigUrl, cfg.lpszProxy, cfg.lpszProxyBypass):
        if p:
            ctypes.windll.kernel32.GlobalFree(ctypes.cast(p, ctypes.c_void_p))
    return out


def _winhttp_proxy_for(url: str, cfg: dict[str, Any]) -> str:
    """WinHTTP's answer for one URL ("host:port;..." or "" for direct)."""
    import ctypes
    from ctypes import wintypes

    class Opts(ctypes.Structure):
        _fields_ = [("dwFlags", wintypes.DWORD), ("dwAutoDetectFlags", wintypes.DWORD),
                    ("lpszAutoConfigUrl", wintypes.LPCWSTR), ("lpvReserved", ctypes.c_void_p),
                    ("dwReserved", wintypes.DWORD), ("fAutoLogonIfChallenged", wintypes.BOOL)]

    class Info(ctypes.Structure):
        _fields_ = [("dwAccessType", wintypes.DWORD), ("lpszProxy", wintypes.LPWSTR),
                    ("lpszProxyBypass", wintypes.LPWSTR)]

    winhttp = ctypes.WinDLL("winhttp")
    winhttp.WinHttpOpen.restype = ctypes.c_void_p
    winhttp.WinHttpGetProxyForUrl.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p]
    winhttp.WinHttpCloseHandle.argtypes = [ctypes.c_void_p]
    session = winhttp.WinHttpOpen("UEFN-Ducky", 1, None, None, 0)  # WINHTTP_ACCESS_TYPE_NO_PROXY
    if not session:
        return ""
    try:
        opts = Opts()
        if cfg.get("url"):
            opts.dwFlags, opts.lpszAutoConfigUrl = 2, cfg["url"]  # WINHTTP_AUTOPROXY_CONFIG_URL
        else:
            opts.dwFlags, opts.dwAutoDetectFlags = 1, 3  # AUTO_DETECT via DHCP | DNS_A
        opts.fAutoLogonIfChallenged = True
        info = Info()
        if not winhttp.WinHttpGetProxyForUrl(session, url, ctypes.byref(opts), ctypes.byref(info)):
            return ""
        proxy = info.lpszProxy or "" if info.dwAccessType == 3 else ""  # WINHTTP_ACCESS_TYPE_NAMED_PROXY
        for p in (info.lpszProxy, info.lpszProxyBypass):
            if p:
                ctypes.windll.kernel32.GlobalFree(ctypes.cast(p, ctypes.c_void_p))
        return proxy
    finally:
        winhttp.WinHttpCloseHandle(session)


def pick_proxy(answer: str, scheme: str) -> str:
    """First proxy for ``scheme`` from a WinHTTP list ("p:80", "http=p:80;https=q:443", "a:1 b:2")."""
    for part in answer.replace(" ", ";").split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            key, _, part = part.partition("=")
            if key.strip().lower() != scheme:
                continue
        return part if "://" in part else f"http://{part}"
    return ""


class PacProxyHandler(urllib.request.ProxyHandler):
    """Per-URL proxy from the PC's auto-config; Internet Options' fixed proxy otherwise."""

    handler_order = 100

    def __init__(self, resolve: Any = None, config: Any = None) -> None:
        super().__init__({})  # no fixed proxies here: every request asks pick() below
        self._resolve = resolve or _winhttp_proxy_for
        self._config = config or _winhttp_config
        self._cache: dict[tuple[str, str], tuple[float, str]] = {}
        self._lock = threading.Lock()
        self._cfg: dict[str, Any] | None = None

    def config(self) -> dict[str, Any]:
        if self._cfg is None:
            try:
                self._cfg = self._config() or {}
            except Exception:
                self._cfg = {}
        return self._cfg

    def proxy_for(self, url: str) -> str:
        parts = urlsplit(url)
        scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
        if not host or host in _LOOPBACK or host.startswith("127."):
            return ""
        key, now = (scheme, host), time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < PAC_CACHE_S:
                return hit[1]
        proxy = ""
        cfg = self.config()
        if cfg.get("auto") or cfg.get("url"):
            got: list[str] = []
            worker = threading.Thread(target=lambda: got.append(self._safe_resolve(url, cfg)), daemon=True)
            worker.start()
            worker.join(PAC_WAIT_S)
            proxy = pick_proxy(got[0], scheme) if got else ""
        if not proxy and not urllib.request.proxy_bypass(host):
            proxy = urllib.request.getproxies().get(scheme, "")
        with self._lock:
            self._cache[key] = (now, proxy)
        return proxy

    def _safe_resolve(self, url: str, cfg: dict[str, Any]) -> str:
        try:
            return self._resolve(url, cfg) or ""
        except Exception:
            return ""

    def _apply(self, req: urllib.request.Request) -> urllib.request.Request:
        if not req.has_proxy():
            proxy = self.proxy_for(req.full_url)
            if proxy:
                # The proxy's own scheme: an https request through an http proxy tunnels (CONNECT).
                p = urlsplit(proxy)
                req.set_proxy(p.netloc or proxy, p.scheme or "http")
        return req

    http_request = _apply
    https_request = _apply


class WindowsTrustHTTPSHandler(urllib.request.HTTPSHandler):
    """HTTPS verified by Windows' certificate store on every connection (frontend/ssl_boot.py),
    whether or not the process-wide truststore patch has run."""

    def https_open(self, req: urllib.request.Request) -> Any:
        from frontend.ssl_boot import https_context

        return self.do_open(http.client.HTTPSConnection, req, context=https_context())


class _ContentCheckedHTTPSHandler(urllib.request.HTTPSHandler):
    """Encrypted but unverified: only for update_network.urlopen(content_checked=True)."""

    def https_open(self, req: urllib.request.Request) -> Any:
        import ssl

        return self.do_open(http.client.HTTPSConnection, req, context=ssl._create_unverified_context())


def content_checked_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(proxy_handler(), _ContentCheckedHTTPSHandler())


def build_opener(*handlers: Any) -> urllib.request.OpenerDirector:
    """urllib.request.build_opener with the PC's proxy and Windows certificate checks."""
    return urllib.request.build_opener(proxy_handler(), WindowsTrustHTTPSHandler(), *handlers)


_HANDLER: PacProxyHandler | None = None


def proxy_handler() -> PacProxyHandler:
    """The process's one handler (its cache is shared by every opener)."""
    global _HANDLER
    if _HANDLER is None:
        _HANDLER = PacProxyHandler()
    return _HANDLER


def proxy_mode() -> str:
    """none | static | pac: how this PC reaches the internet (for error reports)."""
    cfg = proxy_handler().config()
    if cfg.get("url"):
        return "pac"
    return "static" if (cfg.get("proxy") or urllib.request.getproxies()) else "none"


# ----------------------------------------------------------------------------- plain errors + test

NET_STAGES = ("dns", "connect", "tls", "proxy", "timeout", "http", "other")


def stage_of(exc: BaseException) -> str:
    """Where a request died, as one word from NET_STAGES."""
    import ssl
    import urllib.error

    if isinstance(exc, urllib.error.HTTPError):
        return "proxy" if exc.code in (407,) else "http"
    reason = getattr(exc, "reason", exc) if isinstance(exc, urllib.error.URLError) else exc
    text = str(reason).lower()
    if isinstance(reason, socket.gaierror) or "getaddrinfo" in text or "name or service" in text:
        return "dns"
    if "tunnel connection failed" in text or "proxy" in text:
        return "proxy"
    if isinstance(reason, ssl.SSLError) or "certificate" in text or "ssl" in text:
        return "tls"
    if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in text:
        return "timeout"
    if isinstance(reason, (ConnectionError, OSError)):
        return "connect"
    return "other"


_PLAIN = {
    "dns": "UEFN Ducky can't look up {host} (DNS). Check the internet connection, or a VPN/DNS filter blocking it.",
    "connect": "UEFN Ducky can't connect to {host}. If it opens in your browser, a firewall or antivirus is blocking UEFN Ducky: allow it, or press Test network.",
    "timeout": "UEFN Ducky timed out reaching {host}. If it opens in your browser, a firewall, antivirus, VPN or proxy is blocking UEFN Ducky: press Test network.",
    "tls": "UEFN Ducky reached {host} but the secure connection failed. Antivirus HTTPS scanning or a wrong PC clock usually causes this.",
    "proxy": "The network's proxy refused UEFN Ducky's connection to {host}. Press Test network for details.",
    "http": "{host} answered with an error. Try again in a minute.",
    "other": "UEFN Ducky can't reach {host}. Press Test network for details.",
}


def explain(exc: BaseException, host: str) -> tuple[str, str]:
    """(stage, a sentence a person can act on); the raw error is appended in brackets."""
    stage = stage_of(exc)
    raw = str(getattr(exc, "reason", "") or exc)[:120]
    return stage, f"{_PLAIN[stage].format(host=host)} ({raw})"


def diagnose(url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    """What a person (or support) needs to see why this PC can't reach ``url``.

    Stays on the PC: the panel shows it, nothing is sent anywhere.
    """
    parts = urlsplit(url)
    host, port = parts.hostname or "", parts.port or (443 if parts.scheme == "https" else 80)
    out: dict[str, Any] = {"host": host, "proxy_mode": proxy_mode(), "proxy": proxy_handler().proxy_for(url) or "direct"}
    t = time.monotonic()
    try:
        infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
        out["dns"] = {"ok": True, "ms": round((time.monotonic() - t) * 1000),
                      "ipv4": sum(i[0] == socket.AF_INET for i in infos), "ipv6": sum(i[0] == socket.AF_INET6 for i in infos)}
    except OSError as exc:
        out["dns"] = {"ok": False, "error": str(exc)[:120]}
        infos = []
    for fam, name in ((socket.AF_INET, "ipv4"), (socket.AF_INET6, "ipv6")):
        addr = next((i[4] for i in infos if i[0] == fam), None)
        if addr is None:
            out[name] = {"ok": None}
            continue
        t = time.monotonic()
        try:
            _orig_create_connection(addr[:2], timeout).close()
            out[name] = {"ok": True, "ms": round((time.monotonic() - t) * 1000)}
        except OSError as exc:
            out[name] = {"ok": False, "error": str(exc)[:120]}
    t = time.monotonic()
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "UEFN-Ducky"})
        with build_opener().open(req, timeout=timeout) as resp:
            out["https"] = {"ok": True, "status": resp.status, "ms": round((time.monotonic() - t) * 1000)}
    except Exception as exc:
        code = getattr(exc, "code", None)
        if code:  # the server answered (even 4xx): the route works
            out["https"] = {"ok": True, "status": code, "ms": round((time.monotonic() - t) * 1000)}
        else:
            stage, text = explain(exc, host)
            out["https"] = {"ok": False, "stage": stage, "error": text}
    out["ok"] = bool(out["https"].get("ok"))
    return out


def install() -> None:
    """Once, early in the launcher: every urllib call then connects like the browser."""
    global _installed
    if _installed:
        return
    _installed = True
    socket.create_connection = create_connection  # type: ignore[assignment]
    urllib.request.install_opener(build_opener())
