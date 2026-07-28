"""Self-check for plugin_host_api cache + disk prefs."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from frontend.ui_web import plugin_host_api as pha


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with patch.object(pha, "cache_dir", side_effect=lambda pid: root / "cache" / pid):
            pha.cache_set("demo", "es", {"Hello": "Hola", "Save": "Guardar"})
            data = pha.cache_get("demo", "es")
            assert data["Hello"] == "Hola"
            assert data["Save"] == "Guardar"
            assert (root / "cache" / "demo" / "es.json").is_file()

            pha.cache_set("demo", "Scottish Gaelic", {"Hi": "Halò"})
            assert pha.cache_get("demo", "Scottish Gaelic")["Hi"] == "Halò"
            assert (root / "cache" / "demo" / "Scottish_Gaelic.json").is_file()

            pha.cache_set("demo", "es", {"Hello": "Hola", "Cancel": "Cancelar"})
            data2 = pha.cache_get("demo", "es")
            assert data2["Cancel"] == "Cancelar"
            assert "Save" not in data2  # full replace

            cleared = pha.cache_clear("demo", "es")
            assert cleared["ok"]
            assert pha.cache_get("demo", "es") == {}
            assert not (root / "cache" / "demo" / "es.json").is_file()

            # Prefix clear (Verse visual files vf_<lang>_*)
            pha.cache_set("demo", "vf_zh_aaa111", {"text": "一"})
            pha.cache_set("demo", "vf_zh_bbb222", {"text": "二"})
            pha.cache_set("demo", "vf_bg_ccc333", {"text": "три"})
            pha.cache_set("demo", "Chinese", {"Support": "支持"})
            pref = pha.cache_clear("demo", "vf_zh_*")
            assert pref["ok"]
            assert "vf_zh_aaa111" in pref["cleared"]
            assert "vf_zh_bbb222" in pref["cleared"]
            assert pha.cache_get("demo", "vf_zh_aaa111") == {}
            assert pha.cache_get("demo", "vf_bg_ccc333")["text"] == "три"
            assert pha.cache_get("demo", "Chinese")["Support"] == "支持"

            # Simulate another process (Cursor MCP bridge) writing disk while
            # this process still has a stale in-memory entry.
            pha.cache_set("demo", "state", {"board": ["", "X"], "turn": "O"})
            import json as _json

            (root / "cache" / "demo" / "state.json").write_text(
                _json.dumps({"board": ["", "X", "O"], "turn": "X"}) + "\n",
                encoding="utf-8",
            )
            assert pha.cache_get("demo", "state")["board"] == ["", "X", "O"]
            assert pha.cache_get("demo", "state")["turn"] == "X"

        with patch.object(pha, "prefs_dir", return_value=root / "prefs"):
            assert pha.prefs_all_get() == {}
            pha.prefs_plugin_set(
                "translation",
                {"language": "Spanish", "languages": "Spanish,Bulgarian", "model": "openai:gpt-4o"},
            )
            got = pha.prefs_plugin_get("translation")
            assert got["language"] == "Spanish"
            assert got["languages"] == "Spanish,Bulgarian"
            assert (root / "prefs" / "all.json").is_file()

            # Restart simulation: clear mem, reload from disk
            again = pha.prefs_all_get()
            assert again["translation"]["language"] == "Spanish"

            pha.prefs_all_set({"discord": {"showInHeader": True}})
            # Merge — translation slot must survive a sibling write.
            assert pha.prefs_all_get()["translation"]["language"] == "Spanish"
            assert pha.prefs_all_get()["discord"]["showInHeader"] is True

        # llm_start returns immediately; llm_poll finishes without holding bridge .result().
        import time
        from backend.uefn_plugins import host as uefn_host

        with patch.object(uefn_host, "is_plugin_enabled", return_value=True), patch.object(
            pha, "_llm_work", return_value={"ok": True, "text": '{"Hi":"Hola"}'}
        ):
            started = pha.llm_start("translation", system="s", user="u", model="openai:gpt-4o-mini")
            assert started["ok"] and started.get("job_id") and started.get("pending")
            jid = started["job_id"]
            deadline = time.monotonic() + 2.0
            polled: dict = {"pending": True}
            while time.monotonic() < deadline:
                polled = pha.llm_poll(jid)
                if not polled.get("pending"):
                    break
                time.sleep(0.01)
            assert polled.get("ok") is True
            assert polled.get("text") == '{"Hi":"Hola"}'
            assert pha.llm_poll(jid).get("ok") is False  # consumed

    print("test_plugin_host_api: ok")


if __name__ == "__main__":
    main()
