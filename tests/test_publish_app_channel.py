"""A plain release publishes to stable (every client reads it) and swaps apt/dnf; AUR/Flathub only on request."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_publish_app_linux_repo import VERSION, FakeStore, _load, _repo, _wire  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "release" / "publish_app.py").read_text(encoding="utf-8")


def test_publish_defaults_to_stable():
    arg = re.search(r'"--channel",(.*?)\)', SRC, re.S)
    assert arg and 'default="stable"' in arg.group(1) and "choices=RELEASE_CHANNELS" in arg.group(1)
    assert _load().RELEASE_CHANNELS == ("beta", "stable")
    assert '"channel": args.channel' in SRC  # sent to uds_app_release


def test_beta_skips_the_linux_repo(tmp_path):
    pub, store = _load(), FakeStore()
    _wire(pub, store)
    assert pub.publish_linux_repo("https://uefnducky.org", "k", VERSION, _repo(tmp_path), channel="beta") is None
    assert store.calls == [] and store.uploaded == []


def test_stable_swaps_the_linux_repo(tmp_path):
    pub, store = _load(), FakeStore()
    _wire(pub, store)
    pub.publish_linux_repo("https://uefnducky.org", "k", VERSION, _repo(tmp_path), channel="stable")
    assert [c[0] for c in store.calls] == ["uds_linux_repo"]


def test_workflow_channel_input_defaults_to_stable_and_reaches_publish():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "store-publish.yml").read_text(encoding="utf-8"))
    inputs = wf[True]["workflow_dispatch"]["inputs"]  # PyYAML reads the `on:` key as True
    assert inputs["channel"]["default"] == "stable" and inputs["channel"]["options"] == ["beta", "stable"]
    upload = next(s for s in wf["jobs"]["publish"]["steps"] if s.get("name") == "Upload to the Store")
    assert 'extra=(--channel "${CHANNEL}")' in upload["run"] and "inputs.channel" in upload["env"]["CHANNEL"]
    assert "inputs.channel == 'stable'" in wf["jobs"]["linux-bumps"]["if"]
