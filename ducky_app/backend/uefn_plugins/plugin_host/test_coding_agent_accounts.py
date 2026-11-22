from __future__ import annotations

from backend.uefn_plugins.plugin_host import loader, state


def test_a_single_sign_in_agent_gets_no_logins_table(tmp_path) -> None:
    """Antigravity has one sign-in per PC: its plugin says accounts=false and Settings drops
    the "<agent> logins" table (two Log in buttons, one with no box for the code)."""
    pid = "probe_accounts"
    manifest = {"id": pid, "kind": "plugin", "version": "1.0.0", "contributes": {"llm.coding_agents": [
        {"id": "one", "label": "One", "accounts": False}, {"id": "two", "label": "Two"}]}}
    saved = list(state._CONTRIBUTIONS["llm_coding_agents"])
    try:
        loader._load_one(pid, tmp_path, manifest, register=False)
        rows = {r["id"]: r for r in state._CONTRIBUTIONS["llm_coding_agents"] if r["plugin_id"] == pid}
        assert rows["one"]["accounts"] is False
        assert "accounts" not in rows["two"]
    finally:
        state._CONTRIBUTIONS["llm_coding_agents"] = saved
