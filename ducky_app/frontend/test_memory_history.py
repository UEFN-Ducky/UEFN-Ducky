"""History survives restarts, rotates within a fixed disk budget and isolates writers."""
import json
import subprocess
import sys

from frontend import memory_history


def test_rotation_keeps_latest_samples_without_growing_forever(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_history, "MAX_FILE_BYTES", 300)
    session = {"pid": 42, "started": 123.0, "version": "test"}
    for n in range(30):
        memory_history.append(tmp_path, session, {"at": n, "app_mb": 123, "top_other": [["private-name", 4]]})
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 2 and all(p.stat().st_size <= 300 for p in files)
    rows = [json.loads(line) for p in files for line in p.read_text().splitlines()]
    assert max(row["at"] for row in rows) == 29
    assert len(rows) < 30 and all("top_other" not in row for row in rows)


def test_pruning_only_removes_old_history_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_history, "KEEP_SESSIONS", 2)
    unrelated = tmp_path / "notes.jsonl"
    unrelated.write_text("keep")
    for n in range(4):
        memory_history.append(tmp_path, {"pid": 42, "started": n, "version": "test"}, {"at": n})
    rows = [json.loads(p.read_text()) for p in tmp_path.glob("memory-*.jsonl")]
    assert sorted(row["started"] for row in rows) == [2, 3]
    assert unrelated.read_text() == "keep"


def test_two_processes_write_distinct_sessions(tmp_path):
    from pathlib import Path

    code = """
import os, sys
from pathlib import Path
from frontend.memory_history import append
for n in range(12):
    append(Path(sys.argv[1]), {'pid': os.getpid(), 'started': 123.0, 'version': 'test'}, {'at': n})
"""
    cwd = Path(__file__).resolve().parents[1]
    children = [subprocess.Popen([sys.executable, "-c", code, str(tmp_path)], cwd=cwd,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    try:
        for child in children:
            _, stderr = child.communicate(timeout=30)
            assert child.returncode == 0, stderr.decode("utf-8", "replace")
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate()
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 2
    for path in files:
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert [row["at"] for row in rows] == list(range(12))
        assert len({row["pid"] for row in rows}) == 1
