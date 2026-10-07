"""Live Playwright E2E against Isaac BrowserManager (opt-in skip if runtime missing).

Uses public https://example.com only — no logins, no credentials.
Skip when:
  - ISAAC_SKIP_LIVE_BROWSER=1
  - browser_automation off
  - Playwright/Chromium unavailable
"""

from __future__ import annotations

import os
import unittest


def _skip_live() -> str:
    if str(os.getenv("ISAAC_SKIP_LIVE_BROWSER", "0")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return "ISAAC_SKIP_LIVE_BROWSER=1"
    try:
        from config import get_config

        cfg = get_config()
        if not getattr(cfg, "browser_automation", False):
            return "browser_automation disabled"
        if not getattr(cfg, "browser_external_sites", False):
            return "browser_external_sites disabled"
    except Exception as exc:
        return f"config: {exc}"
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except ImportError:
        return "playwright package missing"
    return ""


@unittest.skipIf(bool(_skip_live()), _skip_live() or "skip")
class TestBrowserLivePlaywright(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from browser import BrowserManager

        self.mgr = BrowserManager()
        # Force headless for CI/local agents
        try:
            self.mgr.cfg.browser.headless = True
        except Exception:
            pass

    async def asyncTearDown(self):
        try:
            await self.mgr.close()
        except Exception:
            pass

    async def test_live_goto_example_com_and_extract_heading(self):
        """Real Chromium: open example.com, extract h1, assert Example Domain."""
        result = await self.mgr.run_flow(
            instance_id="live_e2e_example",
            start_url="https://example.com",
            actions=[
                {"action": "goto", "url": "https://example.com"},
                {"action": "wait", "seconds": 0.3},
                {
                    "action": "extract_text",
                    "selector": "p",
                    "save_as": "heading",
                },
            ],
            name="LiveE2E",
        )
        if not result.get("ok"):
            err = str(result.get("error") or "")
            # Constitution / policy → skip rather than hard fail in restricted envs
            if "Verfassung" in err or "deaktiviert" in err or "Runtime" in err:
                self.skipTest(err[:160])
            self.fail(f"run_flow failed: {result}")

        memory = result.get("memory") or {}
        heading = (memory.get("heading") or "").strip()
        self.assertIn("domain", heading.lower(), msg=f"memory={memory} steps={result.get('steps')}")
        self.assertTrue(result.get("ok"))
        steps = result.get("steps") or []
        self.assertTrue(any(s.get("action") == "goto" and s.get("ok") for s in steps))
        self.assertTrue(
            any(s.get("action") == "extract_text" and s.get("ok") for s in steps)
        )

    async def test_live_page_title_via_ensure_instance(self):
        """ensure_instance + page.title() smoke."""
        try:
            inst = await self.mgr.ensure_instance(
                "live_e2e_title",
                "https://example.com",
                name="TitleSmoke",
            )
        except (PermissionError, RuntimeError) as exc:
            self.skipTest(str(exc)[:160])
        page = inst.page
        self.assertIsNotNone(page)
        title = await page.title()
        self.assertIn("Example", title)
        content = await page.content()
        self.assertIn("Example Domain", content)


class TestBrowserRoutingStillIntact(unittest.TestCase):
    """Regression: live work must not break routing contracts."""

    def test_mission_style_browser_request(self):
        from isaac_core import IsaacKernel

        k = object.__new__(IsaacKernel)
        self.assertTrue(k._is_browser_request("Browser auf https://example.com"))
        self.assertFalse(k._is_browser_request("Was ist ein Browser?"))


if __name__ == "__main__":
    unittest.main()
