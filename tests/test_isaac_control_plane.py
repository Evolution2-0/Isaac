from __future__ import annotations

import unittest
from unittest.mock import patch

from config import Level
from isaac_control_plane import IsaacAutonomyControlPlane
from mcp_registry import MCPRegistry


class _FakeConfirmationPolicy:
    def __init__(self, verdict):
        self.verdict = verdict
        self.calls = []

    def analyze(self, action, ctx, metadata):
        self.calls.append((action, ctx, metadata))
        return self.verdict


class TestIsaacAutonomyControlPlane(unittest.TestCase):
    def test_evaluation_never_authorizes_execution(self):
        plane = IsaacAutonomyControlPlane()
        result = plane.evaluate(
            action="chat_response",
            reason="test proposal",
            risk="normal",
            outside_effect=False,
            caller="external-agent-test",
            caller_level=Level.TASK,
        )
        self.assertTrue(result["ok"])
        decision = result["output"]["decision"]
        self.assertFalse(decision["execution_authorized"])
        self.assertFalse(decision["proposal"]["executed"])
        self.assertTrue(decision["proposal"]["requires_executor"])
        self.assertIn(decision["status"], {"ready_for_executor", "blocked_permission", "blocked_safety"})

    def test_high_risk_action_enters_confirmation_gate(self):
        from security_policy import SecurityVerdict

        fake = _FakeConfirmationPolicy(
            SecurityVerdict(
                allowed=False,
                reason="review required",
                requires_confirmation=True,
                risk="high",
                queue_id="REV-TEST",
            )
        )
        plane = IsaacAutonomyControlPlane()
        with patch("isaac_control_plane.get_confirmation_policy", return_value=fake):
            result = plane.evaluate(
                action="chat_response",
                reason="external effect",
                risk="high",
                outside_effect=True,
                caller="external-agent-test",
                caller_level=Level.TASK,
            )
        self.assertTrue(result["ok"])
        decision = result["output"]["decision"]
        self.assertEqual(decision["status"], "pending_confirmation")
        self.assertEqual(decision["confirmation"]["queue_id"], "REV-TEST")
        self.assertFalse(decision["execution_authorized"])
        self.assertEqual(fake.calls[0][0], "chat_response")

    def test_remote_level_is_clamped_to_task(self):
        plane = IsaacAutonomyControlPlane()
        result = plane.evaluate(
            action="chat_response",
            caller="remote",
            caller_level=Level.ISAAC,
            trusted_internal=False,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["output"]["decision"]["permission"]["caller_level"], Level.TASK)

    def test_empty_action_is_rejected(self):
        result = IsaacAutonomyControlPlane().evaluate(action="")
        self.assertFalse(result["ok"])


class TestAutonomyMCPTool(unittest.TestCase):
    def test_tool_is_registered_and_governed(self):
        from isaac_mcp import IsaacMCPService

        registry = MCPRegistry()
        from mcp_registry import _register_defaults
        _register_defaults(registry)
        self.assertIn("isaac.autonomy_evaluate", {x["name"] for x in registry.tools()})
        service = IsaacMCPService(registry)
        result = service.invoke(
            "isaac.autonomy_evaluate",
            {"action": "chat_response", "outside_effect": False},
            caller="external-agent-test",
            caller_level=Level.TASK,
            trusted_internal=False,
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["output"]["decision"]["execution_authorized"])


if __name__ == "__main__":
    unittest.main()
