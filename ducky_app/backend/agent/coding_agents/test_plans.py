"""Tests for outline-tree Plans + templates store."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.agent.coding_agents import plans


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

    def test_create_update_merge_get(self) -> None:
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
        self.assertEqual(plans.format_plan_prompt_block(None), "")
        self.assertEqual(plans.format_plan_prompt_block({"title": "x", "nodes": []}), "")

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
        self.assertFalse(any(r["chat_id"] == "chat-other" for r in listed))
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
            plans.add_node("chat-tree", content="Extra", project_root=self.root)
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

    def test_template_instantiate_isolation(self) -> None:
        tpl = plans.create_template(
            title="Roguelike kit",
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
        plans.update_template(tid, title="Roguelike kit v2")
        tpl2 = plans.load_template(tid)
        assert tpl2 is not None
        self.assertEqual(tpl2["title"], "Roguelike kit v2")
        self.assertEqual(tpl2["nodes"][0]["children"][0]["content"], "Combat")

        inst2 = plans.load_plan("chat-inst", project_root=self.root)
        assert inst2 is not None
        self.assertEqual(inst2["nodes"][0]["children"][0]["content"], "Melee combat")
        self.assertEqual(inst2["title"], "Roguelike kit")

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
