"""profile_id-first ducky resolution — rename / duplicate names must not collide."""

from __future__ import annotations

import unittest
from unittest.mock import patch


class ResolveDuckyProfileTests(unittest.TestCase):
    def test_prefers_profile_id(self) -> None:
        from backend.tools.ducky_panel import _resolve_ducky_profile

        profiles = [
            {"id": "a1", "name": "Star"},
            {"id": "a2", "name": "Star"},
        ]
        with (
            patch("backend.tools.ducky_panel.get_agent_profile", return_value=profiles[1]),
            patch(
                "backend.tools.ducky_panel.list_agent_profiles_available",
                return_value=profiles,
            ),
        ):
            got = _resolve_ducky_profile("a2")
        self.assertEqual(got["id"], "a2")

    def test_unique_name_still_works(self) -> None:
        from backend.tools.ducky_panel import _resolve_ducky_profile

        profiles = [
            {"id": "brand-new", "name": "Brand New"},
            {"id": "star", "name": "Star"},
        ]
        with (
            patch("backend.tools.ducky_panel.get_agent_profile", return_value=None),
            patch(
                "backend.tools.ducky_panel.list_agent_profiles_available",
                return_value=profiles,
            ),
        ):
            got = _resolve_ducky_profile("brand new")
        self.assertEqual(got["id"], "brand-new")

    def test_duplicate_name_raises(self) -> None:
        from backend.tools.ducky_panel import _resolve_ducky_profile

        profiles = [
            {"id": "a1", "name": "Star"},
            {"id": "a2", "name": "Star"},
        ]
        with (
            patch("backend.tools.ducky_panel.get_agent_profile", return_value=None),
            patch(
                "backend.tools.ducky_panel.list_agent_profiles_available",
                return_value=profiles,
            ),
        ):
            with self.assertRaises(ValueError) as ctx:
                _resolve_ducky_profile("Star")
        self.assertIn("Multiple duckies", str(ctx.exception))
        self.assertIn("a1", str(ctx.exception))
        self.assertIn("a2", str(ctx.exception))


class DiscordMatchProfileTests(unittest.TestCase):
    def test_id_beats_duplicate_name(self) -> None:
        from backend.discord.commands import match_profile

        profiles = [
            {"id": "star-1", "name": "Star"},
            {"id": "star-2", "name": "Star"},
        ]
        p, rest = match_profile(profiles, "star-2 do the thing")
        self.assertEqual(p["id"], "star-2")
        self.assertEqual(rest, "do the thing")

    def test_ambiguous_name_refuses(self) -> None:
        from backend.discord.commands import match_profile

        profiles = [
            {"id": "star-1", "name": "Star"},
            {"id": "star-2", "name": "Star"},
        ]
        p, rest = match_profile(profiles, "Star hello")
        self.assertIsNone(p)
        self.assertEqual(rest, "Star hello")


if __name__ == "__main__":
    unittest.main()
