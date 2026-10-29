"""Tests for outline-tree Plans + templates store."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.agent.coding_agents import plans


def test_plans_read_chat_metadata_only() -> None:
    # A plan read loaded every owner's whole chat to see that it exists or find its parent:
    # on a 22 MB chat each get_plan cost a full decode (Oct 24 2026).
    for name in ("plans.py", "plan_ticks.py", "plan_review.py", "plan_hooks.py"):
        source = (Path(plans.__file__).parent / name).read_text(encoding="utf-8")
        assert "load_conversation(" not in source, name


def test_moved_names_are_re_exported() -> None:
    from backend.agent.coding_agents import plan_hooks, plan_review, plan_ticks

    for module, names in (
        (plan_ticks, ("attach_next_tick", "next_tick_dict", "format_plan_tick_nudge")),
        (plan_review, ("PLAN_PROTOCOL", "plan_review", "with_plan_review", "_BIG_STEP_RE", "_REVIEW_MAX")),
        (plan_hooks, ("_push_assignment_invalidated", "_section_audience")),
    ):
        for name in names:
            assert getattr(plans, name) is getattr(module, name), name


class PlansStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self._tpl = tempfile.TemporaryDirectory()
        self.addCleanup(self._tpl.cleanup)
        self._tpl_patch = patch(
            "backend.agent.coding_agents.plans._templates_dir",
            side_effect=lambda create=True: Path(self._tpl.name),
        )
        self._tpl_patch.start()
        self.addCleanup(self._tpl_patch.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_add_node_parent_by_title_or_listed_ids(self) -> None:
        # Oct 29 2026: 22 of 154 ducky_plan_add_node calls failed fast with a bare "parent node not found".
        nodes = [
            {"id": "term", "content": "Terminal", "children": [{"id": "t1", "content": "Kind"}]},
            {"id": "cpu", "content": "Idle CPU", "children": [{"id": "c1", "content": "Timer"}]},
            {"id": "solo", "content": "Loose step"},
        ]
        plans.create_plan("chat-parent", nodes=nodes, project_root=self.root)
        doc = plans.add_node("chat-parent", content="Poll less", parent_id="idle cpu", project_root=self.root)
        cpu = plans._walk_find(doc["nodes"], "cpu")[2]
        self.assertEqual([k["content"] for k in cpu["children"]], ["Timer", "Poll less"])
        with self.assertRaises(ValueError) as caught:
            plans.add_node("chat-parent", content="X", parent_id="Fix", project_root=self.root)
        self.assertIn("parent node not found: 'Fix' (sections: term, cpu; top-level steps: solo)", str(caught.exception))

    def test_long_step_notes_are_capped_and_linked(self) -> None:
        plan = plans.create_plan("chat-notes", nodes=[{"id": "s1", "content": "Step"}], project_root=self.root)
        log = "".join(f"line {i}: build output\n" for i in range(500))  # ~10 KB
        self.assertGreater(len(log), 10_000)
        plans.update_node("chat-notes", "s1", body_markdown=log, project_root=self.root)
        added = plans.add_node("chat-notes", content="Split", body_markdown=log, project_root=self.root)
        self.assertEqual(len(added["nodes"]), 2)
        for node in added["nodes"]:
            body = node["body_markdown"]
            link = f".ducky/plans/{plan['id']}/notes/{node['id']}.md"
            self.assertLessEqual(len(body.encode("utf-8")), 2048)
            self.assertTrue(body.startswith("line 0: build output"))
            self.assertTrue(body.endswith(f"…full notes: {link}"), body[-120:])
            self.assertEqual((Path(self.root) / link).read_text(encoding="utf-8"), log.strip())
        # Short notes stay as written.
        plans.update_node("chat-notes", "s1", body_markdown="Done: tests pass.", project_root=self.root)
        node = plans._walk_find(plans.load_plan("chat-notes", self.root)["nodes"], "s1")[2]
        self.assertEqual(node["body_markdown"], "Done: tests pass.")

    def test_fix_for_a_finished_section_reopens_it_for_its_seat(self) -> None:
        from frontend.ui_web import project_chats

        with patch.object(project_chats, "load_conversation_meta", return_value=object()):
            plans.create_plan("chat-fix", nodes=[
                {"id": "ui", "content": "UI: tab shell", "assignee": "seat-ui", "children": [
                    {"id": "u1", "content": "Tabs"}, {"id": "u2", "content": "List"}]},
                {"id": "other", "content": "Other work"},
            ], project_root=self.root)
            for sid in ("u1", "u2"):
                plans.update_node("chat-fix", sid, status="completed", project_root=self.root, by_user=True)
            ui = plans._walk_find(plans.load_plan("chat-fix", self.root)["nodes"], "ui")[2]
            self.assertEqual(ui["status"], "completed")

            doc = plans.add_node("chat-fix", content="Fix list.js separator", parent_id="ui", project_root=self.root)
        ui = plans._walk_find(doc["nodes"], "ui")[2]
        self.assertEqual(ui["status"], "in_progress")
        self.assertEqual(ui["assignee"], "seat-ui")
        self.assertEqual([(c["content"], c["status"]) for c in ui["children"]], [
            ("Tabs", "completed"), ("List", "completed"), ("Fix list.js separator", "pending")])
        # Finished steps stay frozen: nothing goes under a finished leaf.
        with self.assertRaises(ValueError):
            plans.add_node("chat-fix", content="Under a leaf", parent_id="u1", project_root=self.root)

    def test_planned_assignee_is_set_and_cleared_without_starting_anyone(self) -> None:
        from frontend.ui_web import agent_modes, project_chats

        pushes: list[dict] = []
        with patch.object(project_chats, "load_conversation_meta", return_value=object()), \
                patch.object(agent_modes, "_resolve_push", return_value=pushes.append):
            plans.create_plan("chat-planned", nodes=[
                {"id": "top", "content": "Top", "assignee": "seat-top", "children": [
                    {"id": "mid", "content": "Mid", "children": [
                        {"id": "deep", "content": "Deep", "children": [{"id": "leaf", "content": "Leaf"}]}]}]},
                {"id": "go", "content": "Started"},
            ], project_root=self.root)
            plans.update_node("chat-planned", "go", status="in_progress", project_root=self.root)  # playing
            plans.drain_after_save()
            pushes.clear()
            doc = plans.update_node("chat-planned", "deep", planned_assignee="swarm-speed-developer",
                                    project_root=self.root)
            deep = plans._walk_find(doc["nodes"], "deep")[2]
            self.assertEqual(deep["planned_assignee"], "swarm-speed-developer")
            self.assertNotIn("assignee", deep)
            loaded = plans.load_plan("chat-planned", self.root)
            self.assertEqual(plans._walk_find(loaded["nodes"], "deep")[2]["planned_assignee"],
                             "swarm-speed-developer")
            doc = plans.update_node("chat-planned", "deep", planned_assignee="", project_root=self.root)
            self.assertNotIn("planned_assignee", plans._walk_find(doc["nodes"], "deep")[2])
            plans.drain_after_save()
        self.assertFalse([p for p in pushes if p.get("type") == "plan_assignment_changed"], pushes)

    def test_unstarted_steps_edit_while_the_plan_plays(self) -> None:
        plans.create_plan("chat-play", nodes=[
            {"id": "a", "content": "Started"},
            {"id": "b", "content": "Store step"},
        ], project_root=self.root)
        plans.update_node("chat-play", "a", status="in_progress", project_root=self.root)
        self.assertTrue(plans.plan_structure_locked(plans.load_plan("chat-play", self.root)))
        # A pending step is reworded and split without pausing; the plan keeps playing.
        plans.update_node("chat-play", "b", content="Store: list", project_root=self.root)
        plans.add_node("chat-play", content="Store: detail page", parent_id="b", project_root=self.root)
        loaded = plans.load_plan("chat-play", self.root)
        b = plans._walk_find(loaded["nodes"], "b")[2]
        self.assertEqual(b["content"], "Store: list")
        self.assertEqual([c["content"] for c in b["children"]], ["Store: detail page"])
        self.assertNotEqual(loaded["status"], "paused")
        # A started step stays frozen.
        with self.assertRaises(ValueError):
            plans.update_node("chat-play", "a", content="Renamed", project_root=self.root)

    def test_create_update_merge_get(self) -> None:
        # Legacy on-disk contract (<project>/.ducky/plans/*.json); rows are covered by
        # backend/store/test_phase4.py.
        with patch.dict(os.environ, {"DUCKY_STORE_BACKEND_PLANS": "files"}):
            self._test_create_update_merge_get()

    def _test_create_update_merge_get(self) -> None:
        plan = plans.create_plan(
            "chat-abc",
            title="Blockout city",
            overview="Large canal city",
            body_markdown="# City\nDo the thing",
            todos=[
                {"id": "1", "content": "Survey site", "status": "pending"},
                {"id": "2", "content": "Spawn water", "status": "pending"},
            ],
            project_root=self.root,
        )
        self.assertEqual(plan["chat_id"], "chat-abc")
        self.assertEqual(plan["kind"], "project")
        self.assertEqual(len(plan["nodes"]), 2)
        path = Path(self.root) / ".ducky" / "plans" / "chat-abc.json"
        self.assertTrue(path.is_file())

        updated = plans.update_plan(
            "chat-abc",
            todos=[{"id": "1", "content": "Survey site", "status": "completed"}],
            merge=True,
            project_root=self.root,
        )
        self.assertEqual(updated["nodes"][0]["status"], "completed")
        self.assertEqual(updated["nodes"][1]["status"], "pending")
        prog = plans.todo_progress(updated)
        self.assertEqual(prog["completed"], 1)
        self.assertEqual(prog["total"], 2)

        loaded = plans.load_plan("chat-abc", project_root=self.root)
        assert loaded is not None
        self.assertEqual(loaded["title"], "Blockout city")
        self.assertNotIn("parent_chat_id", loaded)

    def test_format_plan_prompt_block_followable(self) -> None:
        plan = plans.create_plan(
            "chat-fmt",
            title="Ocean square seams",
            overview="Kill axis-aligned grid look",
            nodes=[
                {
                    "id": "diagnose",
                    "content": "Diagnose source of square seams",
                    "status": "completed",
                    "children": [
                        {
                            "id": "d-actors",
                            "content": "find_devices / get_all_actors for WaterZone + ocean mesh",
                            "status": "completed",
                        }
                    ],
                },
                {
                    "id": "fix",
                    "content": "Fix waves",
                    "children": [
                        {
                            "id": "f-instance",
                            "content": "Drop RippleTiling on MI_OceanWaves (Done when <10)",
                            "status": "in_progress",
                        }
                    ],
                },
            ],
            project_root=self.root,
        )
        block = plans.format_plan_prompt_block(plan)
        self.assertIn("Active chat plan", block)
        self.assertIn("Ocean square seams", block)
        self.assertIn("`d-actors`", block)
        self.assertIn("[in_progress]", block)
        self.assertIn("ducky_plan_update_node", block)
        self.assertIn("CHECK OFF NOW", block)
        self.assertIn("`f-instance`", block)
        self.assertEqual(plans.format_plan_prompt_block(None), "")
        self.assertEqual(plans.format_plan_prompt_block({"title": "x", "nodes": []}), "")

    def test_next_open_leaf_prefers_in_progress(self) -> None:
        plan = {
            "nodes": [
                {
                    "id": "a",
                    "content": "pending leaf",
                    "status": "pending",
                    "children": [],
                },
                {
                    "id": "b",
                    "content": "doing",
                    "status": "in_progress",
                    "children": [],
                },
            ]
        }
        lab, node = plans.next_open_leaf(plan)
        self.assertEqual(node["id"], "b")
        tick = plans.next_tick_dict(plan)
        assert tick is not None
        self.assertEqual(tick["action"], "completed")
        self.assertIn("status=\"completed\"", tick["call"])

    def test_plan_mutator_gate_requires_in_progress(self) -> None:
        plan = plans.create_plan(
            "chat-tick",
            title="Tick me",
            overview="Must check off",
            nodes=[{"id": "write", "content": "Write verse", "status": "pending"}],
            project_root=self.root,
        )
        with patch("backend.agent.coding_agents.plans.load_active_plan", return_value=plan):
            reason = plans.plan_mutator_block_reason("workspace_write_file")
            self.assertIsNotNone(reason)
            assert reason is not None
            self.assertIn("in_progress", reason)
            self.assertIn("write", reason)
            self.assertIsNone(plans.plan_mutator_block_reason("workspace_read_file"))
            self.assertIsNone(plans.plan_mutator_block_reason("ducky_plan_update_node"))
            self.assertIsNone(plans.plan_mutator_block_reason("ducky_call_tool"))

        plan["nodes"][0]["status"] = "in_progress"
        with patch("backend.agent.coding_agents.plans.load_active_plan", return_value=plan):
            self.assertIsNone(plans.plan_mutator_block_reason("workspace_write_file"))
            nudge = plans.format_plan_tick_nudge_for_tool("workspace_write_file")
            self.assertIn("CHECK OFF NOW", nudge)
            self.assertIn("completed", nudge)

        with patch("backend.agent.coding_agents.plans.load_active_plan", return_value=None):
            self.assertIsNone(plans.plan_mutator_block_reason("spawn_actor"))

    def test_legacy_todos_migrate_on_load(self) -> None:
        path = Path(self.root) / ".ducky" / "plans"
        path.mkdir(parents=True)
        (path / "legacy.json").write_text(
            '{"id":"x","chat_id":"legacy","title":"Old","todos":'
            '[{"id":"a","content":"A","status":"pending"}],'
            '"parent_chat_id":"other"}',
            encoding="utf-8",
        )
        loaded = plans.load_plan("legacy", project_root=self.root)
        assert loaded is not None
        self.assertEqual(len(loaded["nodes"]), 1)
        self.assertEqual(loaded["nodes"][0]["content"], "A")
        self.assertNotIn("parent_chat_id", loaded)

    def test_list_copy_delete_project_scoped(self) -> None:
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        plans.create_plan(
            "chat-src",
            title="Source plan",
            overview="Do X",
            todos=[
                {"id": "1", "content": "Step A", "status": "completed"},
                {"id": "2", "content": "Step B", "status": "in_progress"},
            ],
            project_root=self.root,
        )
        plans.create_plan(
            "chat-other",
            title="Other project plan",
            todos=[{"id": "1", "content": "Nope", "status": "pending"}],
            project_root=other.name,
        )
        with patch(
            "frontend.settings.PanelSettings.load",
            return_value=type("S", (), {"uefn_project_root": self.root})(),
        ), patch(
            "frontend.ui_web.project_chats.load_conversation",
            return_value=None,
        ):
            listed = plans.list_plans()
        self.assertTrue(any(r["chat_id"] == "chat-src" for r in listed))
        # The database keeps plans with their duckies: every one is listed, whatever project.
        self.assertEqual(any(r["chat_id"] == "chat-other" for r in listed), plans._use_db())
        src_row = next(r for r in listed if r["chat_id"] == "chat-src")
        self.assertEqual(src_row["progress"]["completed"], 1)
        self.assertNotIn("nest_path", src_row)
        self.assertNotIn("parent_chat_id", src_row)

        copied = plans.copy_plan(
            source_chat_id="chat-src",
            dest_chat_id="chat-dst",
            source_project_root=self.root,
            dest_project_root=other.name,
        )
        self.assertEqual(copied["chat_id"], "chat-dst")
        self.assertEqual(copied["title"], "Source plan")
        self.assertTrue(all(n["status"] == "pending" for n in copied["nodes"]))
        self.assertEqual(len(copied["nodes"]), 2)

        self.assertTrue(plans.delete_plan("chat-src", project_root=self.root))
        self.assertIsNone(plans.load_plan("chat-src", project_root=self.root))

    def test_sub_plans_nest_without_a_depth_limit(self) -> None:
        plans.create_plan("chat-deep", title="Deep", nodes=[{"id": "d1", "content": "Level 1"}],
                          project_root=self.root)
        parent = "d1"
        for level in range(2, 11):
            added = plans.add_node("chat-deep", content=f"Level {level}", parent_id=parent, project_root=self.root)
            parent = plans._flatten_nodes(added["nodes"])[-1]["id"]
        loaded = plans.load_plan("chat-deep", project_root=self.root)
        assert loaded is not None
        labels = [lab for lab, _ in plans.outline_numbers(loaded["nodes"])]
        self.assertEqual(labels[-1], ".".join(["1"] * 10))
        self.assertEqual(sum(n["kind"] == "subplan" for n in plans._flatten_nodes(loaded["nodes"])), 9)

    def test_outline_tree_move_delete_gate(self) -> None:
        plans.create_plan(
            "chat-tree",
            title="Arena",
            nodes=[
                {
                    "id": "p1",
                    "content": "Shell",
                    "status": "pending",
                    "kind": "subplan",
                    "children": [
                        {
                            "id": "c1",
                            "content": "Floor",
                            "status": "pending",
                            "kind": "step",
                            "children": [],
                        },
                    ],
                }
            ],
            project_root=self.root,
        )
        with self.assertRaises(ValueError):
            plans.update_node("chat-tree", "p1", status="completed", project_root=self.root)

        # Structure edits while still pending.
        plans.add_node(
            "chat-tree",
            content="Roof",
            parent_id="p1",
            kind="step",
            body_markdown="",
            project_root=self.root,
        )
        plans.move_node("chat-tree", "c1", parent_id="", index=0, project_root=self.root)
        loaded = plans.load_plan("chat-tree", project_root=self.root)
        assert loaded is not None
        self.assertEqual(loaded["nodes"][0]["id"], "c1")
        self.assertEqual(loaded["nodes"][0]["kind"], "step")
        labels = [lab for lab, _ in plans.outline_numbers(loaded["nodes"])]
        self.assertEqual(labels[0], "1")

        plans.delete_node("chat-tree", "c1", project_root=self.root)
        loaded = plans.load_plan("chat-tree", project_root=self.root)
        assert loaded is not None
        self.assertFalse(any(n["id"] == "c1" for n in plans._flatten_nodes(loaded["nodes"])))

        # Mid-flight: playing locks; pause unlocks unfinished work; completed stays frozen.
        plans.update_node("chat-tree", "p1", status="in_progress", project_root=self.root)
        loaded = plans.load_plan("chat-tree", project_root=self.root)
        assert loaded is not None
        self.assertTrue(plans.plan_structure_locked(loaded))
        with self.assertRaises(ValueError):
            plans.update_node("chat-tree", "p1", content="Renamed", project_root=self.root)
        plans.update_plan("chat-tree", status="paused", project_root=self.root)
        loaded = plans.load_plan("chat-tree", project_root=self.root)
        assert loaded is not None
        self.assertFalse(plans.plan_structure_locked(loaded))
        plans.add_node("chat-tree", content="Midflight", project_root=self.root)
        loaded = plans.load_plan("chat-tree", project_root=self.root)
        assert loaded is not None
        mid = next(n for n in plans._flatten_nodes(loaded["nodes"]) if n["content"] == "Midflight")
        plans.update_node("chat-tree", mid["id"], status="completed", project_root=self.root)
        with self.assertRaises(ValueError):
            plans.update_node(
                "chat-tree", mid["id"], content="Nope", project_root=self.root
            )
        plans.update_plan("chat-tree", status="open", project_root=self.root)
        self.assertTrue(
            plans.plan_structure_locked(plans.load_plan("chat-tree", project_root=self.root))
        )

        # Completing everything finishes the plan; duplicate (copy) resets for a fresh edit.
        # Leaves first (status gate refuses parents with unfinished children).
        loaded = plans.load_plan("chat-tree", project_root=self.root)
        assert loaded is not None
        for n in reversed(plans._flatten_nodes(loaded["nodes"])):
            if str(n.get("status")) != "completed":
                plans.update_node("chat-tree", n["id"], status="completed", project_root=self.root)
        loaded = plans.load_plan("chat-tree", project_root=self.root)
        assert loaded is not None
        self.assertEqual(loaded["status"], "finished")
        self.assertTrue(plans.plan_structure_locked(loaded))
        with self.assertRaises(ValueError):
            plans.add_node("chat-tree", content="Extra2", project_root=self.root)
        with self.assertRaises(ValueError):
            plans.update_plan("chat-tree", title="Nope", project_root=self.root)

        copied = plans.copy_plan(
            source_chat_id="chat-tree",
            dest_chat_id="chat-tree-copy",
            source_project_root=self.root,
            dest_project_root=self.root,
        )
        self.assertFalse(plans.plan_structure_locked(copied))
        self.assertTrue(all(n["status"] == "pending" for n in plans._flatten_nodes(copied["nodes"])))
        plans.add_node("chat-tree-copy", content="Extra", kind="subplan", body_markdown="# note", project_root=self.root)
        again = plans.load_plan("chat-tree-copy", project_root=self.root)
        assert again is not None
        extra = next(n for n in plans._flatten_nodes(again["nodes"]) if n["content"] == "Extra")
        self.assertEqual(extra["kind"], "subplan")
        self.assertEqual(extra["body_markdown"], "# note")

    def test_completing_leaves_auto_completes_parent_subplan(self) -> None:
        plans.create_plan(
            "chat-rollup",
            title="Fix links",
            nodes=[
                {
                    "id": "diagnose",
                    "content": "Diagnose",
                    "status": "pending",
                    "kind": "subplan",
                    "children": [
                        {"id": "d1", "content": "Look", "status": "pending", "kind": "step", "children": []},
                        {"id": "d2", "content": "Names", "status": "pending", "kind": "step", "children": []},
                    ],
                },
                {
                    "id": "fix",
                    "content": "Fix",
                    "status": "pending",
                    "kind": "subplan",
                    "children": [
                        {"id": "f1", "content": "Rebuild", "status": "pending", "kind": "step", "children": []},
                    ],
                },
            ],
            project_root=self.root,
        )
        empty = {"id": "e", "content": "Empty", "status": "pending", "kind": "subplan", "children": []}
        plans._rollup_completed_parents([empty])
        self.assertEqual(empty["status"], "pending")

        plans.update_node("chat-rollup", "d1", status="completed", project_root=self.root)
        loaded = plans.load_plan("chat-rollup", project_root=self.root)
        assert loaded is not None
        diagnose = next(n for n in loaded["nodes"] if n["id"] == "diagnose")
        self.assertEqual(diagnose["status"], "pending")

        plans.update_node("chat-rollup", "d2", status="completed", project_root=self.root)
        loaded = plans.load_plan("chat-rollup", project_root=self.root)
        assert loaded is not None
        diagnose = next(n for n in loaded["nodes"] if n["id"] == "diagnose")
        self.assertEqual(diagnose["status"], "completed")
        self.assertNotEqual(loaded["status"], "finished")

        plans.update_node("chat-rollup", "f1", status="completed", project_root=self.root)
        loaded = plans.load_plan("chat-rollup", project_root=self.root)
        assert loaded is not None
        fix = next(n for n in loaded["nodes"] if n["id"] == "fix")
        self.assertEqual(fix["status"], "completed")
        self.assertEqual(loaded["status"], "finished")

    def test_template_instantiate_isolation(self) -> None:
        tpl = plans.create_template(
            title="Island kit",
            overview="Reusable",
            nodes=[
                {
                    "id": "t1",
                    "content": "Core loop",
                    "status": "pending",
                    "children": [
                        {"id": "t1a", "content": "Combat", "status": "pending", "children": []},
                    ],
                }
            ],
        )
        tid = tpl["id"]
        listed = plans.list_templates()
        self.assertTrue(any(r["template_id"] == tid for r in listed))
        self.assertNotIn("progress", listed[0])

        inst = plans.instantiate_template(tid, chat_id="chat-inst", project_root=self.root)
        self.assertEqual(inst["template_id"], tid)
        self.assertEqual(inst["kind"], "project")
        self.assertEqual(inst["nodes"][0]["children"][0]["content"], "Combat")

        plans.update_node("chat-inst", "t1a", content="Melee combat", project_root=self.root)
        plans.update_template(tid, title="Island kit v2")
        tpl2 = plans.load_template(tid)
        assert tpl2 is not None
        self.assertEqual(tpl2["title"], "Island kit v2")
        self.assertEqual(tpl2["nodes"][0]["children"][0]["content"], "Combat")

        inst2 = plans.load_plan("chat-inst", project_root=self.root)
        assert inst2 is not None
        self.assertEqual(inst2["nodes"][0]["children"][0]["content"], "Melee combat")
        self.assertEqual(inst2["title"], "Island kit")

        saved = plans.save_plan_as_template("chat-inst", project_root=self.root)
        self.assertEqual(saved["kind"], "template")
        self.assertNotEqual(saved["id"], tid)

        self.assertTrue(plans.delete_template(tid))
        self.assertIsNone(plans.load_template(tid))
        self.assertIsNotNone(plans.load_plan("chat-inst", project_root=self.root))

    def test_ensure_demo_plan_template_idempotent(self) -> None:
        first = plans.ensure_demo_plan_template()
        assert first is not None
        self.assertEqual(first["id"], plans.DEMO_TEMPLATE_ID)
        self.assertEqual(first["title"], "Getting started")
        self.assertEqual(len(first["nodes"]), 3)
        second = plans.ensure_demo_plan_template()
        assert second is not None
        self.assertEqual(second["id"], plans.DEMO_TEMPLATE_ID)
        ids = [r["template_id"] for r in plans.list_templates()]
        self.assertEqual(ids.count(plans.DEMO_TEMPLATE_ID), 1)
        self.assertEqual(ids.count(plans.VERIFY_LOOP_TEMPLATE_ID), 1)

    def test_verify_leaf_requires_evidence(self) -> None:
        from backend.agent import verify_evidence as ve

        plans.create_plan(
            "chat-verify",
            title="Fix verse",
            nodes=[
                {"id": "fix", "content": "Fix the file", "status": "pending", "children": []},
                {
                    "id": "v1",
                    "content": "Verify: workspace_compile_verse",
                    "status": "pending",
                    "children": [],
                },
            ],
            project_root=self.root,
        )
        plans.update_node("chat-verify", "fix", status="completed", project_root=self.root)
        with self.assertRaises(ValueError) as ctx:
            plans.update_node("chat-verify", "v1", status="completed", project_root=self.root)
        self.assertIn("check tool", str(ctx.exception).lower())
        tok = ve.bind_conversation("chat-verify")
        try:
            ve.record_ok("workspace_compile_verse")
            plans.update_node("chat-verify", "v1", status="completed", project_root=self.root)
        finally:
            ve.reset_conversation(tok)
        loaded = plans.load_plan("chat-verify", project_root=self.root)
        assert loaded is not None
        verify = next(n for n in loaded["nodes"] if n["id"] == "v1")
        self.assertEqual(verify["status"], "completed")

    def test_only_a_step_that_starts_with_verify_needs_check_evidence(self) -> None:
        def leaf(content: str) -> dict:
            return {"id": "n1", "content": content, "children": []}

        self.assertTrue(plans.is_verify_leaf(leaf("Verify contention and recovery using real write paths.")))
        self.assertTrue(plans.is_verify_leaf(leaf("3. **Verification**: run the suite")))
        self.assertFalse(
            plans.is_verify_leaf(
                leaf("Show live file ownership. Done when users see its write/verification/review state.")
            )
        )
        plans.create_plan(
            "chat-build",
            title="Build",
            nodes=[{"id": "ui", "content": "Show who owns each file and its verification state", "status": "pending", "children": []}],
            project_root=self.root,
        )
        plans.update_node("chat-build", "ui", status="completed", project_root=self.root)

    def test_the_users_own_tick_completes_a_verify_step(self) -> None:
        plans.create_plan(
            "chat-owner",
            title="Owner",
            nodes=[{"id": "v1", "content": "Verify: the team's test run", "status": "pending", "children": []}],
            project_root=self.root,
        )
        with self.assertRaises(ValueError):
            plans.update_node("chat-owner", "v1", status="completed", project_root=self.root)
        plans.update_node("chat-owner", "v1", status="completed", project_root=self.root, by_user=True)
        loaded = plans.load_plan("chat-owner", project_root=self.root)
        assert loaded is not None
        self.assertEqual(loaded["nodes"][0]["status"], "completed")

    def test_ensure_verify_loop_template_idempotent(self) -> None:
        first = plans.ensure_verify_loop_template()
        assert first is not None
        self.assertEqual(first["id"], plans.VERIFY_LOOP_TEMPLATE_ID)
        self.assertTrue(any(plans.is_verify_leaf(n) for n in plans._flatten_nodes(first["nodes"])))
        second = plans.ensure_verify_loop_template()
        assert second is not None
        self.assertEqual(second["id"], first["id"])

    def test_push_plan_updated_uses_resolve_push(self) -> None:
        plan = {
            "chat_id": "chat-xyz",
            "title": "Test",
            "nodes": [{"id": "1", "content": "A", "status": "pending", "children": []}],
        }
        forwarded: list[dict] = []

        def fake_resolve(push):
            def sink(event: dict) -> None:
                forwarded.append(event)

            return sink

        with patch("frontend.ui_web.agent_modes._resolve_push", side_effect=fake_resolve):
            plans.push_plan_updated(plan)

        self.assertEqual(len(forwarded), 1)
        evt = forwarded[0]
        self.assertEqual(evt["type"], "plan_updated")
        self.assertEqual(evt["conv_id"], "chat-xyz")
        self.assertEqual(evt["progress"]["total"], 1)


class ResolvePlanChatIdTests(unittest.TestCase):
    def test_explicit_chat_id_wins(self) -> None:
        from backend.tools.panel.ducky_panel import _resolve_plan_chat_id

        self.assertEqual(_resolve_plan_chat_id("explicit-id"), "explicit-id")

    def test_falls_back_to_ducky_conv_id_env(self) -> None:
        from backend.tools.panel.ducky_panel import _resolve_plan_chat_id

        with patch("frontend.ui_web.agent_modes.get_active_conv_id", return_value=None):
            with patch.dict(os.environ, {"DUCKY_CONV_ID": "env-conv-123"}, clear=False):
                self.assertEqual(_resolve_plan_chat_id(""), "env-conv-123")

    def test_active_conv_beats_env(self) -> None:
        from backend.tools.panel.ducky_panel import _resolve_plan_chat_id

        with patch("frontend.ui_web.agent_modes.get_active_conv_id", return_value="active-conv"):
            with patch.dict(os.environ, {"DUCKY_CONV_ID": "env-conv"}, clear=False):
                self.assertEqual(_resolve_plan_chat_id(""), "active-conv")

    def test_the_calling_chat_beats_active_conv_and_env(self) -> None:
        # The shared tool server serves outside agents; only the call identity names the caller.
        from backend.tools.panel.ducky_panel import _resolve_plan_chat_id
        from backend.workspace import identity

        token = identity.bind(identity.RunContext(conv_id="codex-chat", coding_agent="codex"))
        try:
            with patch("frontend.ui_web.agent_modes.get_active_conv_id", return_value="active-conv"):
                with patch.dict(os.environ, {"DUCKY_CONV_ID": "env-conv"}, clear=False):
                    self.assertEqual(_resolve_plan_chat_id(""), "codex-chat")
                    self.assertEqual(_resolve_plan_chat_id("explicit-id"), "explicit-id")
        finally:
            identity.reset(token)

    def test_empty_when_no_sources(self) -> None:
        from backend.tools.panel.ducky_panel import _resolve_plan_chat_id

        with patch("frontend.ui_web.agent_modes.get_active_conv_id", return_value=None):
            os.environ.pop("DUCKY_CONV_ID", None)
            self.assertEqual(_resolve_plan_chat_id(""), "")


class PlanArgRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_coerce_nodes_json_string(self) -> None:
        plan = plans.create_plan(
            "chat-json-str",
            title="String nodes",
            overview="Short summary",
            nodes='[{"id":"a","content":"Do A","children":[{"id":"a1","content":"Sub"}]}]',
            project_root=self.root,
        )
        self.assertEqual(len(plan["nodes"]), 1)
        self.assertEqual(plan["nodes"][0]["id"], "a")
        self.assertEqual(plan["nodes"][0]["children"][0]["content"], "Sub")
        self.assertEqual(plan["overview"], "Short summary")

    def test_recover_nodes_dumped_into_overview(self) -> None:
        mangled = (
            "Replace actors with Scene Graph entities after Verse build."
            "</overview>\n"
            '<parameter name="merge">false</parameter>\n'
            '<parameter name="nodes">'
            '[{"id": "prep", "content": "Groundwork", "children": '
            '[{"id": "p1", "content": "Import mesh", "status": "completed"}]}, '
            '{"id": "gate", "content": "GATE: Build Verse", "children": '
            '[{"id": "g1", "content": "Confirm unlock"}]}]'
        )
        plan = plans.create_plan(
            "chat-mangled",
            title="Solar",
            overview=mangled,
            nodes=None,
            project_root=self.root,
        )
        self.assertNotIn("<parameter", plan["overview"])
        self.assertNotIn("</overview>", plan["overview"])
        self.assertIn("Scene Graph", plan["overview"])
        self.assertEqual(len(plan["nodes"]), 2)
        self.assertEqual(plan["nodes"][0]["id"], "prep")
        self.assertEqual(plan["nodes"][0]["children"][0]["status"], "completed")
        prog = plans.todo_progress(plan)
        self.assertGreater(prog["total"], 0)

    def test_load_heals_and_persists_mangled_plan(self) -> None:
        # Legacy on-disk contract (<project>/.ducky/plans/*.json); rows are covered by
        # backend/store/test_phase4.py.
        with patch.dict(os.environ, {"DUCKY_STORE_BACKEND_PLANS": "files"}):
            self._test_load_heals_and_persists_mangled_plan()

    def _test_load_heals_and_persists_mangled_plan(self) -> None:
        path = Path(self.root) / ".ducky" / "plans"
        path.mkdir(parents=True)
        mangled = (
            "Short blurb.</overview>\n"
            '<parameter name="nodes">'
            '[{"id":"x","content":"Step X","children":[]}]'
        )
        (path / "heal-me.json").write_text(
            json.dumps(
                {
                    "id": "abc",
                    "chat_id": "heal-me",
                    "title": "Broken",
                    "overview": mangled,
                    "body_markdown": "",
                    "nodes": [],
                    "status": "open",
                }
            ),
            encoding="utf-8",
        )
        loaded = plans.load_plan("heal-me", project_root=self.root)
        assert loaded is not None
        self.assertEqual(loaded["overview"], "Short blurb.")
        self.assertEqual(len(loaded["nodes"]), 1)
        self.assertEqual(loaded["nodes"][0]["content"], "Step X")
        on_disk = json.loads((path / "heal-me.json").read_text(encoding="utf-8"))
        self.assertEqual(len(on_disk["nodes"]), 1)
        self.assertNotIn("<parameter", on_disk["overview"])


if __name__ == "__main__":
    unittest.main()
