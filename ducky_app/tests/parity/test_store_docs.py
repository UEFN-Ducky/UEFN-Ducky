"""Workflows (legacy rows, saved versions, per-account state), memory notes, plans and tasks."""

from __future__ import annotations

from backend.store import db
from backend.store.repos import automations, memory, plans, workflows

from parity_harness import callers, plain
from test_store_chats import _sql

_TABLES = {
    "automations": "id, name, enabled, graph, runs, updated, last_run, kind, description",
    "workflow_versions": "workflow_id, saved_at, snapshot",
    "workflow_runtime": "account_id, workflow_id, runs, last_run, run_here",
    "space_meta": "account_id, space_id, workflows_writable, web_slug",
    "memory_entries": "project_id, name, parent, description, author, updated, body",
    "plans": "project_id, key, kind, plan_id, title, status, updated_at, doc",
    "tasks": "project_id, id, updated, doc",
}


def _both(script, *tables: str) -> list:
    """Run ``script(call)`` through Python, then on cleared tables through Rust: the
    answers and the rows must be the same. Returns the answers."""
    seen = []
    for index in (0, 1):
        for table in tables:
            _sql(f"DELETE FROM {table}")
        answers = plain(script(index))
        seen.append((answers, [_sql(f"SELECT {_TABLES[table]} FROM {table} ORDER BY rowid") for table in tables]))
    assert seen[0] == seen[1]
    return seen[0][0]


def test_legacy_workflow_rows(rust_store) -> None:
    calls = callers(rust_store, "automations", automations)

    def script(index: int) -> list:
        call = calls[index]
        call("put", doc={"id": "w1", "name": "One é", "graph": {"nodes": [{"id": "n", "x": 1.5}], "edges": []}, "runs": [{"ok": True}], "updated": 5, "last_run": 2.5})
        call("put", doc={"id": "w2", "enabled": False, "description": "off", "updated": 9.0})
        call("put", doc={"id": "w3", "enabled": 0, "graph": {}, "runs": None, "name": None, "updated": 5})
        call("put", doc={"id": "w1", "name": "One again", "enabled": "yes", "updated": 5})
        return [call("delete", workflow_id="w3"), call("delete", workflow_id="none"), call("has_legacy"), call("list_all")]

    answers = _both(script, "automations")
    assert answers[:3] == [True, False, True] and [doc["id"] for doc in answers[3]] == ["w2", "w1"]
    # Rows that are not JSON, or not the right shape, read as empty by both.
    _sql("UPDATE automations SET graph='{oops', runs='{\"a\": 1}' WHERE id='w1'")
    _sql("UPDATE automations SET graph='[1]', runs='nope' WHERE id='w2'")
    assert calls[1]("list_all") == automations.list_all()
    _sql("DELETE FROM automations")
    assert calls[1]("has_legacy") is False is automations.has_legacy()


def _archive(conn_call, **args) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn_call(conn, **args)


def test_saved_versions(rust_store) -> None:
    _, rust = callers(rust_store, "automations", automations)

    def python(function: str, **args):
        if function in ("archive", "copy_versions"):
            return _archive(getattr(automations, function), **args)
        if function == "reseal_versions":
            new = dict(args["converted"])
            ids = {text: version_id for version_id, text in automations.version_rows(args["old_id"])}
            return automations.reseal_versions(args["old_id"], args["new_id"], lambda text: new.get(ids[text]))
        return getattr(automations, function)(**args)

    def script(index: int) -> list:
        call = (python, rust)[index]
        for at, snapshot in ((1.5, "first é"), (2.0, "enc1:second"), (0, "third")):
            call("archive", workflow_id="w1", saved_at=at, snapshot=snapshot)
        call("archive", workflow_id="w2", saved_at=7.0, snapshot="other")
        call("copy_versions", old_id="w1", new_id="w3")
        rows = call("version_rows", workflow_id="w1")
        assert [text for _, text in rows] == ["third", "enc1:second", "first é"] and len({row[0] for row in rows}) == 3
        assert all(len(row[0]) == 36 and row[0][14] == "4" for row in rows)
        call("reseal_versions", old_id="w1", new_id="w9", converted=[[rows[0][0], "sealed third"], [rows[2][0], "sealed first"]])
        call("reseal_versions", old_id="w2", new_id="w2", converted=[])
        return [
            [text for _, text in call("version_rows", workflow_id=workflow_id)] for workflow_id in ("w1", "w2", "w3", "w9")
        ] + [call("has_versions", workflow_id="w1"), call("has_versions", workflow_id="w9"), call("version_row", workflow_id="w9", version_id=rows[1][0]),
             call("version_row", workflow_id="w1", version_id=rows[1][0]), call("version_row", workflow_id="w9", version_id="none")]

    answers = _both(script, "workflow_versions")
    assert answers == [[], ["other"], ["third", "enc1:second", "first é"], ["sealed third", "enc1:second", "sealed first"], False, True, "enc1:second", None, None]
    # Each reads what the other stored.
    for workflow_id in ("w2", "w3", "w9", "none"):
        assert rust("version_rows", workflow_id=workflow_id) == plain(automations.version_rows(workflow_id))


def test_workflow_state_per_account(rust_store) -> None:
    _, rust = callers(rust_store, "workflows", workflows)

    def python(function: str, **args):
        if function == "runtime_put":
            return workflows.runtime_put(args["account"], args["workflow_id"], **args["fields"])
        return getattr(workflows, function)(**args)

    def script(index: int) -> list:
        call = (python, rust)[index]
        call("runtime_put", account="a", workflow_id="w1", fields={"runs": "[1]", "last_run": 5})
        call("runtime_put", account="a", workflow_id="w1", fields={"run_here": True})
        call("runtime_put", account="a", workflow_id="w2", fields={"runs": None, "last_run": None, "run_here": 1})
        call("runtime_put", account="b", workflow_id="w1", fields={})
        call("runtime_put", account="a", workflow_id="w3", fields={"runs": "gone"})
        call("runtime_delete", account="a", workflow_ids=["w3", "none"])
        call("runtime_delete", account="a", workflow_ids=[])
        call("meta_put", account="a", space="s1", workflows_writable=True, web_slug="page é")
        call("meta_put", account="a", space="s2", workflows_writable=False, web_slug="")
        call("meta_put", account="a", space="s2", workflows_writable=False, web_slug="later")
        return [call("runtime_get", account=account, workflow_id=workflow_id) for account in ("a", "b") for workflow_id in ("w1", "w2", "w3")] + [
            [call("workflows_writable", account=account, space=space), call("web_slug", account=account, space=space)]
            for account in ("a", "b") for space in ("s1", "s2")
        ]

    answers = _both(script, "workflow_runtime", "space_meta")
    assert answers[0] == {"runs": "[1]", "last_run": 5.0, "run_here": True} and answers[2] == {"runs": "", "last_run": 0.0, "run_here": False}
    assert answers[6:] == [[True, "page é"], [False, "later"], [False, ""], [False, ""]]

    _sql("DELETE FROM scope_sync WHERE account_id='t-acc'")
    for space, label in (("s3", "beta"), ("s1", "Alpha"), ("s2", "alpha"), ("s4", "")):
        _sql("INSERT INTO scope_sync(account_id, space_id, label) VALUES ('t-acc', ?, ?)", space, label)
    assert rust("synced_spaces", account="t-acc") == workflows.synced_spaces("t-acc") == ["s4", "s1", "s2", "s3"]
    assert rust("synced_spaces", account="none") == []
    _sql("DELETE FROM scope_sync WHERE account_id='t-acc'")


def test_memory_notes(rust_store) -> None:
    calls = callers(rust_store, "memory", memory)

    def python(function: str, **args):
        return getattr(memory, function)(args.pop("project_id"), *(args.pop(name) for name in ("name", "entry", "query") if name in args), **args)

    def script(index: int) -> list:
        call = (python, calls[1])[index]
        for name, updated, body in (
            ("team", "2026-01-02", "How the team works é"),
            ("team/zeta", "2026-01-05", "zeta sub about worktrees"),
            ("team/alpha", "2026-01-04", "alpha sub about \"quoted\" pulls and rebase"),
            ("bug", "2026-01-03", "a freeze in the lock; pull the trace " + "padding " * 30 + "end marker"),
            ("lost/sub", "2026-01-09", "its entry is gone"),
            ("solo", "2026-01-02", ""),
        ):
            call("put", project_id="p", name=name, description=f"about {name}", author="Dev é", updated=updated, body=body)
        call("put", project_id="other", name="team", description="elsewhere", author="", updated="2026-02-01", body="worktrees elsewhere")
        call("put", project_id="p", name="solo", description="rewritten", author="B", updated="2026-01-02", body="now with a body")
        reads = [call("index", project_id=project) for project in ("p", "other", "none")]
        reads += [call("subs_of", project_id="p", entry=entry) for entry in ("team", "lost", "bug")]
        reads += [call("get", project_id="p", name=name) for name in ("team/alpha", "solo", "none")]
        reads += [call("search", project_id="p", query=query, limit=limit) for query, limit in (
            ("worktrees", 20), ("pull", 20), ("pull", 1), ("\"quoted\" pulls", 20), ("team works", 20), ("  ", 20), ("nothing-here", 20), ("end marker", 20),
        )]
        reads += [call("has_rows", project_id=project) for project in ("p", "none")]
        reads += [call("delete", project_id="p", name="team", with_subs=True), call("delete", project_id="p", name="lost", with_subs=True),
                  call("delete", project_id="p", name="bug", with_subs=False), call("delete", project_id="p", name="none", with_subs=False)]
        return reads + [call("index", project_id="p"), call("search", project_id="p", query="worktrees", limit=20)]

    answers = _both(script, "memory_entries")
    assert [top["name"] for top in answers[0]] == ["bug", "solo", "team"] and [sub["name"] for sub in answers[0][2]["subs"]] == ["team/alpha", "team/zeta"]
    assert [hit["name"] for hit in answers[9]] == ["team/zeta"] and answers[14] == [] and answers[-6:-2] == [3, 1, 1, 0]
    assert answers[-1] == [] and [top["name"] for top in answers[-2]] == ["solo"]


def test_plans_and_tasks(rust_store) -> None:
    calls = callers(rust_store, "plans", plans)

    def python(function: str, **args):
        return plans.merge_into_one_list(again=True) if function == "merge_into_one_list" else getattr(plans, function)(**args)

    def script(index: int) -> list:
        call = (python, calls[1])[index]
        one = plans.ONE_LIST_PROJECT
        for project, key, updated in (("p1", "c1", 10), ("p2", "c1", 30), (one, "c1", 20), ("p1", "c2", 5), (one, "c3", 50), ("p2", "c3", 50), ("p2", "c4", None)):
            doc = {"id": f"{project}-{key}", "title": f"Plan é {key}", "status": "active", "created_at": 1, "nodes": [{"id": "n", "weight": 1.5}]}
            call("plan_put", project_id=project, key=key, kind="project", doc=doc if updated is None else {**doc, "updated_at": updated})
        call("plan_put", project_id="", key="t1", kind="template", doc={"title": "Template"})
        call("plan_put", project_id="p1", key="t2", kind="template", doc={"id": 7, "updated_at": 99})
        call("plan_put", project_id="p1", key="c2", kind="project", doc={"id": "again", "updated_at": 6.5})
        moved = [call("merge_into_one_list"), call("merge_into_one_list")]
        reads = [call("plan_docs", project_id=project, kind=kind) for project in (one, "p1", "p2", "") for kind in ("project", "template")]
        reads += [call("plan_get", project_id=one, key="c1"), call("plan_get", project_id="p2", key="c1"), call("plan_get", project_id="p1", key="none")]
        reads += [call("plan_delete", project_id="p1", key="t2"), call("plan_delete", project_id="p1", key="t2")]
        for task_id, updated in (("t1", 5), ("t2", 9.5), ("t3", 5), (4, None)):
            call("task_put", project_id="p1", doc={"id": task_id, "updated": updated, "title": "Task é", "steps": [1, None]})
        call("task_put", project_id="p1", doc={"id": "t1", "updated": 5, "title": "again"})
        call("task_put", project_id="p2", doc={"id": "t1", "updated": 1})
        return moved + reads + [call("task_docs", project_id=project) for project in ("p1", "p2", "none")] + [
            call("task_get", project_id="p1", task_id=task_id) for task_id in ("t1", "4", "none")
        ]

    answers = _both(script, "plans", "tasks")
    # c1: p2's (30) swapped with the one list's (20); c2 and c4 moved; c3 is not newer and stays.
    assert answers[:2] == [3, 0] and answers[10]["id"] == "p2-c1" and answers[11]["id"] == "_no_project-c1"
    assert [key for key, _ in answers[2]] == ["c1", "c2", "c3", "c4"] and [doc["id"] for doc in answers[-6]] == ["t2", "t1", "t3", 4]
