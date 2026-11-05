"""Plan review asks for a Split: step when a step's file is near its test_file_sizes.py cap."""

from __future__ import annotations

from backend.agent.coding_agents import plan_review

BODY = ("**Why:** grow it. **Files:** `ducky_app/backend/big.py`, `backend/small.py` **Do:** 1. add the "
        "feature to both modules and wire it up. **Tests:** test_big.py. **Done when:** the tests pass, "
        "committed and pushed. **Example:** one more function in each of the two files.")


def _app(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "test_file_sizes.py").write_text(
        "MAX_LINES = 1500\nSPLITTING: dict[str, int] = {\n    'backend/big.py': 200,\n}\n", encoding="utf-8")
    (tmp_path / "backend" / "big.py").write_text("x = 1\n" * 150, encoding="utf-8")  # cap - 50
    (tmp_path / "backend" / "small.py").write_text("x = 1\n" * 10, encoding="utf-8")
    return tmp_path


def test_a_file_at_cap_minus_50_gets_split_advice(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(plan_review, "_APP", _app(tmp_path))
    plan = {"nodes": [{"id": "s1", "content": "Grow the modules", "body_markdown": BODY}]}
    issues = [i for i in plan_review.plan_review(plan) if "Split:" in i["issue"]]
    assert issues == [{"n": "1", "issue": "Add a `Split:` step first: ducky_app/backend/big.py is 150/200 lines"}]


def test_the_split_step_itself_gets_no_advice(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(plan_review, "_APP", _app(tmp_path))
    plan = {"nodes": [{"id": "s1", "content": "Split: move code out of big.py", "body_markdown": BODY}]}
    assert not [i for i in plan_review.plan_review(plan) if "Split:" in i["issue"]]


def test_unlisted_files_use_max_lines(tmp_path) -> None:
    app = _app(tmp_path)
    (app / "backend" / "small.py").write_text("x = 1\n" * 1450, encoding="utf-8")
    assert plan_review.near_size_cap(["backend/small.py", "backend/test_x.py"], app) == [("backend/small.py", 1450, 1500)]
