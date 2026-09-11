"""Remote panel fetch coalescing + event-poll backoff (source guards)."""

from __future__ import annotations

from pathlib import Path

_WEB = Path(__file__).resolve().parent / "web" / "src" / "hooks"


def test_remote_api_coalesces_inflight_and_swallows_network_blips():
    src = (_WEB / "usePanelApi.ts").read_text(encoding="utf-8")
    assert "isRemoteNetworkBlip" in src
    assert "const _inflight = new Map" in src
    assert "if (hit) return hit" in src
    assert "if (isRemoteNetworkBlip(err)) return undefined" in src


def test_event_poll_backoff_caps_at_8s():
    src = (_WEB / "useAgentEventBus.ts").read_text(encoding="utf-8")
    assert "EVENT_POLL_RETRY_MAX_MS = 8000" in src
    assert "nextEventPollRetryMs" in src
    assert "retryMs = nextEventPollRetryMs(retryMs)" in src
    assert "setTimeout(resolve, 500)" not in src
