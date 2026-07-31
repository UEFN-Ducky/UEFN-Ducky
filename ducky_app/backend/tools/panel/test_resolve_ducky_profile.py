"""profile_id-first ducky resolution — rename / duplicate names must not collide."""

from __future__ import annotations

import unittest
from unittest.mock import patch


class ResolveDuckyProfileTests(unittest.TestCase):
    def test_prefers_profile_id(self) -> None:
        from backend.tools.panel.ducky_panel import _resolve_ducky_profile

        profiles = [
            {"id": "a1", "name": "Star"},
            {"id": "a2", "name": "Star"},
        ]
        with (
            patch("backend.tools.panel.ducky_panel.get_agent_profile", return_value=profiles[1]),
            patch(
                "backend.tools.panel.ducky_panel.list_agent_profiles_available",
                return_value=profiles,
            ),
        ):
            got = _resolve_ducky_profile("a2")
        self.assertEqual(got["id"], "a2")

    def test_unique_name_still_works(self) -> None:
        from backend.tools.panel.ducky_panel import _resolve_ducky_profile

        profiles = [
            {"id": "brand-new", "name": "Brand New"},
            {"id": "star", "name": "Star"},
        ]
        with (
            patch("backend.tools.panel.ducky_panel.get_agent_profile", return_value=None),
            patch(
                "backend.tools.panel.ducky_panel.list_agent_profiles_available",
                return_value=profiles,
            ),
        ):
            got = _resolve_ducky_profile("brand new")
        self.assertEqual(got["id"], "brand-new")

    def test_duplicate_name_raises(self) -> None:
        from backend.tools.panel.ducky_panel import _resolve_ducky_profile

        profiles = [
            {"id": "a1", "name": "Star"},
            {"id": "a2", "name": "Star"},
        ]
        with (
            patch("backend.tools.panel.ducky_panel.get_agent_profile", return_value=None),
            patch(
                "backend.tools.panel.ducky_panel.list_agent_profiles_available",
                return_value=profiles,
            ),
        ):
            with self.assertRaises(ValueError) as ctx:
                _resolve_ducky_profile("Star")
        self.assertIn("Multiple duckies", str(ctx.exception))
        self.assertIn("a1", str(ctx.exception))
        self.assertIn("a2", str(ctx.exception))


# Discord match_profile tests live with the plugin:
# UEFN-Ducky/plugins/uefn-plugin-discord/backend/test_multi_bot.py


if __name__ == "__main__":
    unittest.main()