"""End-to-end: phone panel in direct (tunnel-free) mode against the local desktop.

Drives a headless Edge/Chrome viewer over the Chrome DevTools Protocol and
reads the desktop WebView2 console over CDP too. Pass = the viewer connects
peer-to-peer, PanelApi calls work over the rpc channel, a window stream
paints frames, input and the blob allowlist behave, and neither side logged
a [remote-*] error (the viewer page must have no uncaught exceptions at all).

Prerequisites (this script checks them and says what is missing):
  * desktop running with --remote-debugging-port=9222
    (tests/e2e/remote_view/run_desktop.ps1, or the installed app started
    with WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS containing that flag)
  * a panel page reachable at --viewer-url that serves the bundle
    same-origin with /__panel_api: the desktop's own http://127.0.0.1:4199/
    (built dist) or Vite dev on :5173

Usage:
  .venv/Scripts/python.exe tests/e2e/remote_view/direct_e2e.py
  .venv/Scripts/python.exe tests/e2e/remote_view/direct_e2e.py --viewer-url "http://localhost:5173/?direct=local&desktop="
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

try:
    import websockets
except ImportError:  # pragma: no cover
    print("pip install websockets", file=sys.stderr)
    sys.exit(2)

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]

ICE_DUMP_JS = """(async () => {
  const t = window.__duckyDirectTransport; const pc = t && t.peer; if (!pc) return 'no pc';
  const st = await pc.getStats();
  const out = { ice: pc.iceConnectionState, gather: pc.iceGatheringState, sig: pc.signalingState, conn: pc.connectionState, local: [], remote: [], pairs: [] };
  st.forEach(r => {
    if (r.type === 'local-candidate') out.local.push(r.candidateType + ' ' + r.protocol + ' ' + (r.address || '') + ':' + r.port);
    if (r.type === 'remote-candidate') out.remote.push(r.candidateType + ' ' + r.protocol + ' ' + (r.address || '') + ':' + r.port);
    if (r.type === 'candidate-pair') out.pairs.push(r.state + (r.nominated ? ' nominated' : ''));
  });
  return JSON.stringify(out);
})()"""

PAIR_JS = """(async () => {
  const pc = window.__duckyDirectTransport.peer; const st = await pc.getStats(); let out = '';
  st.forEach(r => { if (r.type === 'candidate-pair' && (r.nominated || r.state === 'succeeded')) { const l = st.get(r.localCandidateId); out = (l && l.candidateType) || ''; } });
  return out;
})()"""

VIDEO_JS = """(async () => {
  const pc = window.__duckyDirectTransport.peer; const st = await pc.getStats(); let f = 0, c = '', w = 0, h = 0;
  const codecs = {}; st.forEach(r => { if (r.type === 'codec') codecs[r.id] = r.mimeType; });
  st.forEach(r => { if (r.type === 'inbound-rtp' && r.kind === 'video') { f = r.framesDecoded || 0; c = codecs[r.codecId] || ''; w = r.frameWidth || 0; h = r.frameHeight || 0; } });
  return { f, c, w, h };
})()"""


def find_browser() -> str:
    for c in EDGE_CANDIDATES:
        if os.path.isfile(c):
            return c
    for name in ("msedge", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    raise SystemExit("no Edge/Chrome found for the headless viewer")


def cdp_targets(port: int) -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3) as r:
        return json.load(r)


class Cdp:
    def __init__(self, ws_url: str, label: str):
        self.ws_url = ws_url
        self.label = label
        self.ws = None
        self.mid = 0
        self.log: list[str] = []

    async def __aenter__(self):
        self.ws = await websockets.connect(self.ws_url, max_size=None)
        await self.send("Runtime.enable")
        await self.send("Log.enable")
        return self

    async def __aexit__(self, *_):
        await self.ws.close()

    async def send(self, method: str, params: dict | None = None) -> dict:
        self.mid += 1
        mid = self.mid
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == mid:
                return msg
            self._note(msg)

    async def drain(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            try:
                msg = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=max(0.05, end - time.time())))
            except asyncio.TimeoutError:
                return
            self._note(msg)

    def _note(self, msg: dict) -> None:
        m = msg.get("method")
        p = msg.get("params", {})
        if m == "Runtime.consoleAPICalled":
            args = " ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", []))
            self.log.append(f"{self.label}[{p.get('type')}] {args}")
        elif m == "Runtime.exceptionThrown":
            d = p.get("exceptionDetails", {})
            self.log.append(f"{self.label}[exception] {d.get('text')} {d.get('exception', {}).get('description', '')}")
        elif m == "Log.entryAdded":
            e = p.get("entry", {})
            self.log.append(f"{self.label}[log:{e.get('level')}] {e.get('text')}")

    async def eval(self, expr: str, *, await_promise: bool = True, timeout: float = 30) -> object:
        res = await asyncio.wait_for(
            self.send("Runtime.evaluate", {"expression": expr, "awaitPromise": await_promise, "returnByValue": True}),
            timeout=timeout,
        )
        r = res.get("result", {})
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description", "eval failed"))
        return r.get("result", {}).get("value")


def dump(title: str, rows: list[str]) -> None:
    if rows:
        print(f"{title}:", *rows, sep="\n  ")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--viewer-url", default="http://127.0.0.1:4199/?direct=local&desktop=")
    ap.add_argument("--desktop-cdp", type=int, default=9222)
    ap.add_argument("--viewer-cdp", type=int, default=0, help="0 = pick a free port per run")
    ap.add_argument("--keep", action="store_true", help="leave the headless viewer running")
    args = ap.parse_args()

    try:
        targets = cdp_targets(args.desktop_cdp)
    except Exception as exc:
        print(f"FAIL desktop CDP not reachable on {args.desktop_cdp}: {exc}")
        return 1
    desktop = next((t for t in targets if t.get("type") == "page"), None)
    if not desktop:
        print("FAIL desktop has no page target")
        return 1

    browser = find_browser()
    # Fresh profile per run: a reused profile served a stale index.html from
    # the HTTP cache and the viewer ran an old bundle against a new desktop.
    profile = tempfile.mkdtemp(prefix="ducky-e2e-viewer-")
    if not args.viewer_cdp:
        import socket

        with socket.socket() as sk:
            sk.bind(("127.0.0.1", 0))
            args.viewer_cdp = sk.getsockname()[1]
    viewer_url = args.viewer_url + ("&" if "?" in args.viewer_url else "?") + f"t={int(time.time())}"
    proc = subprocess.Popen(
        [
            browser,
            "--headless=new",
            f"--remote-debugging-port={args.viewer_cdp}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--use-fake-ui-for-media-stream",
            "--autoplay-policy=no-user-gesture-required",
            "--window-size=1280,800",
            viewer_url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ok = False
    try:
        viewer = None
        for _ in range(60):
            try:
                for t in cdp_targets(args.viewer_cdp):
                    if t.get("type") == "page" and "direct=" in (t.get("url") or ""):
                        viewer = t
                        break
            except Exception:
                pass
            if viewer:
                break
            time.sleep(0.5)
        if not viewer:
            print("FAIL headless viewer did not open the panel page")
            return 1

        async with Cdp(desktop["webSocketDebuggerUrl"], "desktop") as dcdp, Cdp(viewer["webSocketDebuggerUrl"], "viewer") as vcdp:
            t0 = time.time()
            state = ""
            for _ in range(120):
                state = await vcdp.eval("(window.__duckyDirectTransport && window.__duckyDirectTransport.status.state) || ''", await_promise=False)
                if state in ("live", "failed"):
                    break
                await asyncio.sleep(0.25)
            connect_ms = int((time.time() - t0) * 1000)
            reason = await vcdp.eval("(window.__duckyDirectTransport && window.__duckyDirectTransport.status.reason) || ''", await_promise=False)
            print(f"connect: state={state} after {connect_ms} ms {reason}")
            if state != "live":
                await dcdp.drain(1)
                await vcdp.drain(0.5)
                print("viewer ice:", await vcdp.eval(ICE_DUMP_JS))
                print("viewer timeline:", await vcdp.eval("JSON.stringify(window.__duckyDirectTransport && window.__duckyDirectTransport.timeline)", await_promise=False))
                print("viewer lastFailure:", await vcdp.eval("JSON.stringify(window.__duckyDirectTransport && window.__duckyDirectTransport.lastFailure)", await_promise=False))
                dump("desktop log", dcdp.log[-12:])
                dump("viewer log", vcdp.log[-12:])
                return 1

            for name in ("get_settings", "get_project", "list_folders"):
                kind = await vcdp.eval(
                    f"window.__duckyDirectTransport.invoke('{name}', []).then(v => typeof v + ':' + (v === null ? 'null' : Array.isArray(v) ? 'array' : Object.keys(v || {{}}).length), e => 'ERR ' + e.message)"
                )
                print(f"probe: {name} -> {kind}")

            views = await vcdp.eval("window.__duckyDirectTransport.invoke('list_window_views', [])")
            assert isinstance(views, list), f"list_window_views over rpc: {views!r}"
            print(f"rpc: list_window_views -> {len(views)} windows")
            uefn = next((v for v in views if v.get("kind") == "uefn"), views[0] if views else None)
            if not uefn:
                print("FAIL no windows to watch")
                return 1

            pair = await vcdp.eval(PAIR_JS)
            print(f"ice: candidate pair = {pair or 'unknown'}")

            res = await vcdp.eval(f"window.__duckyDirectTransport.invoke('watch_window', [{{hwnd: '{uefn['id']}'}}])")
            print(f"watch_window -> {res}")
            # The overlay sends its size so the desktop fits the window to the
            # viewer's aspect; do the same and expect the stream to follow.
            await vcdp.eval("window.__duckyDirectTransport.sendInput({type:'size', w:1280, h:720})", await_promise=False)
            frames, codec, w, h = 0, "", 0, 0
            for _ in range(40):
                stats = await vcdp.eval(VIDEO_JS)
                if isinstance(stats, dict):
                    frames, codec, w, h = int(stats.get("f", 0)), str(stats.get("c", "")), int(stats.get("w", 0)), int(stats.get("h", 0))
                if frames > 10:
                    break
                await asyncio.sleep(0.5)
            print(f"video: framesDecoded={frames} codec={codec} {w}x{h}")
            if frames <= 10:
                print("FAIL no video frames decoded")
                await dcdp.drain(1)
                dump("desktop log", dcdp.log[-12:])
                return 1
            # After fit + crop the frame must be the window, not the whole monitor.
            for _ in range(20):
                stats = await vcdp.eval(VIDEO_JS)
                if isinstance(stats, dict):
                    w, h = int(stats.get("w", 0)), int(stats.get("h", 0))
                if 0 < w <= 1400 and 0 < h <= 800:
                    break
                await asyncio.sleep(0.5)
            print(f"video after size fit: {w}x{h}")
            if not (0 < w <= 1400 and 0 < h <= 800):
                print("FAIL stream is not cropped/fitted to the watched window")
                return 1

            shot = await vcdp.send("Page.captureScreenshot", {"format": "png"})
            data = shot.get("result", {}).get("data")
            if data:
                import base64

                out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last-viewer.png")
                with open(out, "wb") as fh:
                    fh.write(base64.b64decode(data))
                print(f"screenshot: {out}")
            sent = await vcdp.eval("window.__duckyDirectTransport.sendInput({type:'move', x:0.5, y:0.5, button:0})", await_promise=False)
            print(f"input: sendInput -> {sent}")

            forbidden = await vcdp.eval("window.__duckyDirectTransport.fetchBlob('/__panel_api/x').then(r => r.status)")
            print(f"blob: forbidden path -> {forbidden}")
            assert forbidden == 403, "blob allowlist not enforced"

            # A real desktop asset must come back over the channel, and when the
            # panel is served from a subdirectory the worker must be scoped to it
            # (that scope is the only reason those URLs are interceptable).
            allowed = await vcdp.eval(
                "window.__duckyDirectTransport.fetchBlob('/duckies/Artist.png')"
                ".then(r => r.status + ':' + r.headers.get('Content-Type'))"
            )
            print(f"blob: /duckies/Artist.png -> {allowed}")
            if not str(allowed).startswith("200:image/png"):
                print("FAIL desktop asset did not come back over the blob channel")
                return 1
            scope = await vcdp.eval(
                "navigator.serviceWorker.getRegistration().then(r => r ? r.scope : 'none')"
            )
            base = await vcdp.eval("new URL('.', document.baseURI).pathname", await_promise=False)
            print(f"worker: scope={scope} panel dir={base}")
            if str(scope) != "none" and not str(scope).endswith(str(base)):
                print("FAIL service worker scope does not cover the panel directory")
                return 1

            await dcdp.drain(1)
            await vcdp.drain(0.5)
            remote_errors = [l for l in dcdp.log + vcdp.log if ("[error]" in l or "[exception]" in l) and "remote-" in l]
            viewer_exceptions = [l for l in vcdp.log if "[exception]" in l]
            desktop_exceptions = [l for l in dcdp.log if "[exception]" in l]
            dump("desktop page exceptions (reported, not Remote View)", desktop_exceptions[:8])
            if remote_errors or viewer_exceptions:
                dump("FAIL errors logged", remote_errors + viewer_exceptions)
                return 1
            ok = True
            print(f"PASS direct e2e: connect {connect_ms} ms, {pair or '?'} path, {codec or '?'} video {w}x{h}, {frames} frames")
            return 0
    finally:
        if not args.keep:
            # Edge hands off to a separate browser process; kill the whole tree or
            # the next run finds a zombie viewer on the same port.
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
            time.sleep(1)
            shutil.rmtree(profile, ignore_errors=True)
        print("result:", "ok" if ok else "failed")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
