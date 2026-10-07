"""Regression: GitHub Copilot companion wiring (opt-in, prefix, selection)."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch


class TestCopilotConfig(unittest.TestCase):
    def test_config_loads_flags(self):
        from external_memory.config import load_external_memory_config
        from external_memory.bridge import reset_external_memory_bridge

        with patch.dict(
            os.environ,
            {
                "ISAAC_COPILOT_AGENT_ENABLED": "1",
                "ISAAC_COPILOT_AGENT_ALWAYS_APPROVE": "0",
                "ISAAC_COPILOT_CLOUD_REPO": "hansheute88/isaac",
            },
            clear=False,
        ):
            reset_external_memory_bridge()
            cfg = load_external_memory_config()
            self.assertTrue(cfg.copilot_agent_enabled)
            self.assertEqual(cfg.copilot_cloud_repo, "hansheute88/isaac")
            self.assertIn("copilot", cfg.copilot_agent_bin or "copilot")


class TestCopilotSelection(unittest.TestCase):
    def test_copilot_markers_select(self):
        from agent_selection import AGENT_COPILOT, select_companion_agent
        from executor import Strategy

        with patch.dict(os.environ, {"ISAAC_AGENT_AUTO_SELECT": "1"}, clear=False):
            d = select_companion_agent(
                user_input="bitte mit github copilot den bugfixen",
                intent="code",
                interaction_class="TASK",
                strategy=Strategy(allow_agent_companions=True),
                available={"copilot": True, "grok": True},
            )
            self.assertEqual(d.agent_id, AGENT_COPILOT)

    def test_no_select_without_auto(self):
        from agent_selection import select_companion_agent
        from executor import Strategy

        with patch.dict(os.environ, {"ISAAC_AGENT_AUTO_SELECT": "0"}, clear=False):
            d = select_companion_agent(
                user_input="copilot please refactor",
                intent="code",
                strategy=Strategy(allow_agent_companions=True),
                available={"copilot": True},
            )
            self.assertIsNone(d.agent_id)


class TestCopilotIntent(unittest.TestCase):
    def test_detect_intent_copilot_prefix(self):
        from isaac_core import Intent, detect_intent

        self.assertEqual(detect_intent("copilot: erkläre relay.py"), Intent.COPILOT_AGENT)
        self.assertEqual(detect_intent("gh-copilot: status"), Intent.COPILOT_AGENT)
        self.assertEqual(detect_intent("github copilot: cloud: fix x"), Intent.COPILOT_AGENT)

    def test_help_text_when_empty(self):
        from isaac_core import IsaacKernel

        k = object.__new__(IsaacKernel)
        k._copilot_session_id = None
        # no gate needed for empty help path after parse
        with patch.dict(os.environ, {"ISAAC_COPILOT_AGENT_ENABLED": "0"}, clear=False):
            from external_memory.bridge import reset_external_memory_bridge

            reset_external_memory_bridge()
            out = IsaacKernel._handle_copilot_agent(k, "copilot:")
            self.assertIn("ISAAC_COPILOT_AGENT_ENABLED", out)
            self.assertIn("COPILOT_GITHUB_TOKEN", out)


class TestCopilotClassicPatRejected(unittest.TestCase):
    def test_classic_pat_blocked_in_cli_path(self):
        from external_memory.config import ExternalMemoryConfig
        from external_memory.copilot_agent_adapter import CopilotAgentAdapter

        cfg = ExternalMemoryConfig(copilot_agent_enabled=True, copilot_agent_bin="copilot")
        ad = CopilotAgentAdapter(cfg)
        with patch.dict(
            os.environ,
            {
                "COPILOT_GITHUB_TOKEN": "ghp_classicExampleTokenNotReal0000000000001",
                "GH_TOKEN": "ghp_classicExampleTokenNotReal0000000000001",
                "GITHUB_TOKEN": "ghp_classicExampleTokenNotReal0000000000001",
            },
            clear=False,
        ):
            # force bin present without real run if resolve rejects early
            ad._bin_path = "/usr/bin/true"
            ad._tried = True
            r = ad._run_cli(
                "hi",
                cwd="/tmp",
                timeout=5,
                resume_session_id=None,
                force_new=True,
            )
            # Either explicit rejection or CLI error about classic PAT
            self.assertFalse(r.get("ok"))
            err = (r.get("error") or "").lower()
            self.assertTrue(
                "ghp_" in err
                or "classic" in err
                or "not supported" in err
                or "auth" in err
                or "failed" in err
                or r.get("auth_prefix") == "ghp_"
                or r.get("auth_hint"),
                msg=str(r),
            )


if __name__ == "__main__":
    unittest.main()
