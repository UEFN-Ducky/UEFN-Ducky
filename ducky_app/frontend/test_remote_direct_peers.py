"""Two real WebRTC peers meet through this app's direct signaling over loopback.

The viewer offers, ``remote_direct.rtc_connect`` hands the offer to the "page" (an
aiortc answerer standing in for the WebView), the page answers through
``resolve_answer``, and a message crosses the ``rpc`` DataChannel both ways. Runs the
same on Windows and Linux (aiortc ships manylinux and win_amd64 wheels).
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

import frontend.remote_direct as rd

aiortc = pytest.importorskip("aiortc")


async def _gathered(pc) -> None:
    while pc.iceGatheringState != "complete":
        await asyncio.sleep(0.02)


def _plain(desc) -> dict:
    return {"type": desc.type, "sdp": desc.sdp}


def test_viewer_and_page_connect_over_loopback_signaling(monkeypatch) -> None:
    published: list[dict] = []
    monkeypatch.setattr("frontend.ui_web.panel_httpd.publish_panel_events", lambda rows: published.extend(rows))
    monkeypatch.setattr(rd, "ANSWER_WAIT_S", 15.0)

    async def run() -> str:
        viewer = aiortc.RTCPeerConnection()
        page = aiortc.RTCPeerConnection()
        got: asyncio.Future = asyncio.get_running_loop().create_future()
        try:
            channel = viewer.createDataChannel("rpc")
            channel.on("open", lambda: channel.send('{"id":1,"method":"ping"}'))
            channel.on("message", lambda msg: got.done() or got.set_result(msg))

            @page.on("datachannel")
            def _dc(dc) -> None:
                dc.on("message", lambda msg: dc.send(msg.replace("ping", "pong")))

            await viewer.setLocalDescription(await viewer.createOffer())
            await _gathered(viewer)

            async def answer_like_the_page() -> None:
                while not published:
                    await asyncio.sleep(0.02)
                ev = published[0]
                assert ev["type"] == "direct_rtc" and ev["session"] == "peers1"
                await page.setRemoteDescription(aiortc.RTCSessionDescription(**ev["offer"]))
                await page.setLocalDescription(await page.createAnswer())
                await _gathered(page)
                assert rd.resolve_answer("peers1", _plain(page.localDescription), fingerprint="fp")

            page_task = asyncio.create_task(answer_like_the_page())
            out = await asyncio.to_thread(
                rd.rtc_connect,
                {"session": "peers1", "offer": _plain(viewer.localDescription), "protocol": rd.PROTOCOL_VERSION},
            )
            await page_task
            assert out.get("answer", {}).get("type") == "answer", out
            await viewer.setRemoteDescription(aiortc.RTCSessionDescription(**out["answer"]))
            return await asyncio.wait_for(got, 15)
        finally:
            await viewer.close()
            await page.close()

    assert asyncio.run(run()) == '{"id":1,"method":"pong"}'


def test_remote_window_keeps_native_frame_off_windows(monkeypatch) -> None:
    import frontend.ui_web.remote_ducky_window as rdw

    made: dict = {}

    class _Events:
        def __init__(self) -> None:
            self.shown = self.closed = self

        def __iadd__(self, fn):
            made.setdefault("handlers", []).append(fn)
            return self

    def create_window(title, **kw):
        made.update(kw, title=title)
        return types.SimpleNamespace(events=_Events())

    monkeypatch.setitem(sys.modules, "webview", types.SimpleNamespace(create_window=create_window))
    monkeypatch.setattr("frontend.ui_web.panel_httpd.panel_ui_http_url", lambda: "http://127.0.0.1:1")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(rdw, "_open", {})
    rdw.open_remote_ducky("k1", "Desktop-PC")
    assert made["title"] == "Desktop-PC · UEFN Ducky"
    assert made["frameless"] is False
    assert made["url"].startswith("http://127.0.0.1:1/")
