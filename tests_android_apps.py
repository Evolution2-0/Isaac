"""Tests: Android app launch routing (Chrome etc.) — mock bridge."""

from __future__ import annotations

import unittest
from unittest import mock


class TestAppLaunchDetect(unittest.TestCase):
    def test_open_chrome_is_app_open(self):
        from owner_action import detect_owner_action

        for text in (
            "öffne chrome",
            "starte chrome",
            "öffne die app chrome",
            "chrome öffnen",
        ):
            with self.subTest(text=text):
                action = detect_owner_action(text)
                self.assertIsNotNone(action)
                self.assertEqual(action.kind, "app_open")
                self.assertIn(action.params.get("name"), {"chrome", "google chrome"})

        # "öffne Google Chrome": 'Google Chrome' is in _SITE_ALIASES, so by default without 'app' it becomes open_target.
        action_gc = detect_owner_action("öffne Google Chrome")
        self.assertIsNotNone(action_gc)
        self.assertIn(action_gc.kind, {"app_open", "open_target"})

    def test_url_in_chrome(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("öffne youtube.com in chrome")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "app_open")
        self.assertEqual(action.params.get("name"), "chrome")
        self.assertIn("youtube.com", action.params.get("url", ""))

    def test_apps_status(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("apps status")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "apps_status")


class TestAppLaunchExecute(unittest.IsolatedAsyncioTestCase):
    async def test_app_open_chrome_uses_bridge_when_available(self):
        from owner_action import OwnerAction, _app_open

        with mock.patch("owner_action._launch_android_package", new_callable=mock.AsyncMock) as launch:
            launch.return_value = {
                "ok": True,
                "via": "termux_bridge:am",
                "package": "com.android.chrome",
            }
            msg, ok = await _app_open(OwnerAction("app_open", {"name": "chrome"}))
        self.assertTrue(ok)
        self.assertIn("com.android.chrome", msg)
        launch.assert_awaited()

    async def test_app_open_chrome_bridge_down_honest_error(self):
        from owner_action import OwnerAction, _app_open, _BRIDGE_SETUP_HINT

        with mock.patch("owner_action._launch_android_package", new_callable=mock.AsyncMock) as launch:
            launch.return_value = {
                "ok": False,
                "package": "com.android.chrome",
                "attempts": ["bridge_unavailable"],
                "hint": _BRIDGE_SETUP_HINT,
            }
            msg, ok = await _app_open(OwnerAction("app_open", {"name": "chrome"}))
        self.assertFalse(ok)
        self.assertIn("Termux", msg)
        self.assertNotIn("Playwright", msg)


if __name__ == "__main__":
    unittest.main()
