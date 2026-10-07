from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from config import Level
from mcp_jsonrpc import MCPJsonRpcHandler
from mcp_registry import MCPRegistry, MCP_TOOL_PRIVILEGES
from isaac_mcp import IsaacMCPService, MCPToolPolicy, TOOL_POLICIES


class FakeRegistry:
    def __init__(self):
        self.calls = []

    def tools(self):
        return [
            {"name": "isaac.query_memory", "description": "read", "inputSchema": {"type": "object"}},
            {"name": "isaac.goal_update", "description": "write", "inputSchema": {"type": "object"}},
        ]

    def invoke_tool(self, name, arguments, **kwargs):
        self.calls.append((name, arguments, kwargs))
        return {"ok": True, "output": {"tool": name, "arguments": arguments}}


class TestIsaacMCPService(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=False)
        self.env.start()
        for key in (
            "ISAAC_MCP_API_KEY",
            "ISAAC_MCP_ALLOW_WRITE",
            "ISAAC_MCP_ALLOWED_TOOLS",
            "ISAAC_MCP_RATE_LIMIT",
            "ISAAC_MCP_MAX_ARGUMENT_BYTES",
        ):
            os.environ.pop(key, None)
        self.registry = FakeRegistry()
        self.service = IsaacMCPService(self.registry)

    def tearDown(self):
        self.env.stop()

    def test_http_is_loopback_only_without_key(self):
        ok, _ = self.service.authenticate_http({}, "127.0.0.1")
        self.assertTrue(ok)
        ok, reason = self.service.authenticate_http({}, "203.0.113.10")
        self.assertFalse(ok)
        self.assertIn("API_KEY", reason)

    def test_bearer_key_authentication(self):
        os.environ["ISAAC_MCP_API_KEY"] = "test-secret"
        ok, _ = self.service.authenticate_http(
            {"Authorization": "Bearer test-secret"}, "203.0.113.10"
        )
        self.assertTrue(ok)
        ok, _ = self.service.authenticate_http(
            {"Authorization": "Bearer wrong"}, "203.0.113.10"
        )
        self.assertFalse(ok)

    def test_write_tools_are_hidden_and_blocked_by_default(self):
        tools = self.service.list_tools()
        names = {tool["name"] for tool in tools}
        self.assertIn("isaac.query_memory", names)
        self.assertNotIn("isaac.goal_update", names)

        result = self.service.invoke("isaac.goal_update", {"goal": "x", "status": "done"})
        self.assertFalse(result["ok"])
        self.assertIn("write tools are disabled", result["error"])

    def test_write_tools_can_be_enabled_explicitly(self):
        os.environ["ISAAC_MCP_ALLOW_WRITE"] = "1"
        result = self.service.invoke("isaac.goal_update", {"goal": "x", "status": "done"})
        self.assertTrue(result["ok"])
        self.assertEqual(self.registry.calls[0][0], "isaac.goal_update")

    def test_remote_owner_override_is_rejected(self):
        os.environ["ISAAC_MCP_ALLOW_WRITE"] = "1"
        result = self.service.invoke(
            "isaac.goal_update",
            {"goal": "x", "status": "done", "owner_override": True, "override_reason": "test"},
        )
        self.assertFalse(result["ok"])
        self.assertIn("Owner override", result["error"])

    def test_allowlist_narrows_exposure(self):
        os.environ["ISAAC_MCP_ALLOWED_TOOLS"] = "isaac.query_memory"
        tools = self.service.list_tools()
        self.assertEqual([tool["name"] for tool in tools], ["isaac.query_memory"])
        result = self.service.invoke("isaac.task_status", {"task_id": "x"})
        self.assertFalse(result["ok"])
        self.assertIn("allowlisted", result["error"])

    def test_argument_size_limit(self):
        os.environ["ISAAC_MCP_MAX_ARGUMENT_BYTES"] = "1024"
        result = self.service.invoke("isaac.query_memory", {"query": "x" * 5000})
        self.assertFalse(result["ok"])
        self.assertIn("size limit", result["error"])

    def test_rate_limit_is_enforced(self):
        os.environ["ISAAC_MCP_RATE_LIMIT"] = "1"
        first = self.service.invoke("isaac.query_memory", {"query": "one"}, caller="same")
        second = self.service.invoke("isaac.query_memory", {"query": "two"}, caller="same")
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertIn("rate limit", second["error"])


class TestMCPRegistryCallerBoundary(unittest.TestCase):
    def test_external_task_level_does_not_gain_isaac_privilege(self):
        name = "test.high_privilege"
        previous = MCP_TOOL_PRIVILEGES.get(name)
        MCP_TOOL_PRIVILEGES[name] = "file_write"
        try:
            registry = MCPRegistry()
            registry.register_tool(
                name,
                {"description": "test", "inputSchema": {"type": "object"}},
                handler=lambda **kwargs: {"ok": True},
            )
            denied = registry.invoke_tool(
                name,
                {},
                caller="MCP-HTTP",
                caller_level=Level.TASK,
                allow_owner_override=False,
            )
            self.assertFalse(denied["ok"])

            allowed = registry.invoke_tool(
                name,
                {},
                caller="internal",
                caller_level=Level.ISAAC,
                allow_owner_override=True,
            )
            self.assertTrue(allowed["ok"])
        finally:
            if previous is None:
                MCP_TOOL_PRIVILEGES.pop(name, None)
            else:
                MCP_TOOL_PRIVILEGES[name] = previous


class TestMCPJsonRpcServiceBoundary(unittest.TestCase):
    def test_handler_factory_accepts_governance_context(self):
        registry = FakeRegistry()
        service = IsaacMCPService(registry)
        from mcp_jsonrpc import get_jsonrpc_handler

        handler = get_jsonrpc_handler(
            registry,
            service=service,
            caller="MCP-HTTP",
            caller_level=Level.TASK,
            trusted_internal=False,
        )
        result = handler.handle_payload({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        })
        self.assertEqual(result["jsonrpc"], "2.0")
        self.assertIn("tools", result["result"])

    def test_tools_list_uses_governed_service(self):
        registry = FakeRegistry()
        service = IsaacMCPService(registry)
        handler = MCPJsonRpcHandler(
            registry,
            service=service,
            caller="MCP-HTTP",
            caller_level=Level.TASK,
            trusted_internal=False,
        )
        result = handler.handle_payload({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        })
        names = {item["name"] for item in result["result"]["tools"]}
        self.assertIn("isaac.query_memory", names)
        self.assertNotIn("isaac.goal_update", names)

    def test_tools_call_uses_governed_service(self):
        registry = FakeRegistry()
        service = IsaacMCPService(registry)
        handler = MCPJsonRpcHandler(
            registry,
            service=service,
            caller="MCP-HTTP",
            caller_level=Level.TASK,
            trusted_internal=False,
        )
        result = handler.handle_payload({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "isaac.query_memory", "arguments": {"query": "hello"}},
        })
        self.assertFalse(result["result"]["isError"])
        self.assertEqual(registry.calls[0][0], "isaac.query_memory")

    def test_remote_owner_override_is_blocked_at_jsonrpc_boundary(self):
        registry = FakeRegistry()
        service = IsaacMCPService(registry)
        handler = MCPJsonRpcHandler(
            registry,
            service=service,
            caller="MCP-HTTP",
            caller_level=Level.TASK,
            trusted_internal=False,
        )
        result = handler.handle_payload({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "isaac.query_memory",
                "arguments": {
                    "query": "OWNER_OVERRIDE_REGRESSION",
                    "owner_override": True,
                    "override_reason": "remote-test",
                },
            },
        })
        self.assertTrue(result["result"]["isError"])
        self.assertIn("Owner override is not accepted", result["result"]["content"][0]["text"])
        self.assertEqual(registry.calls, [])

    def test_write_tool_is_blocked_at_jsonrpc_boundary(self):
        registry = FakeRegistry()
        service = IsaacMCPService(registry)
        handler = MCPJsonRpcHandler(
            registry,
            service=service,
            caller="MCP-HTTP",
            caller_level=Level.TASK,
            trusted_internal=False,
        )
        result = handler.handle_payload({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "isaac.goal_update",
                "arguments": {"goal": "GOVERNANCE_REGRESSION", "status": "done"},
            },
        })
        self.assertTrue(result["result"]["isError"])
        self.assertIn("write tools are disabled", result["result"]["content"][0]["text"])
        self.assertEqual(registry.calls, [])


class TestIsaacMCPRegistrySurface(unittest.TestCase):
    def test_canonical_governance_tools_are_registered(self):
        from mcp_registry import get_mcp_registry

        names = {item["name"] for item in get_mcp_registry().tools()}
        expected = {
            "isaac.query_memory",
            "isaac.goal_list",
            "isaac.goal_get",
            "isaac.goal_update",
            "isaac.permission_check",
            "isaac.safety_check",
            "isaac.action_request",
            "isaac.notification_send",
        }
        self.assertTrue(expected.issubset(names))
        self.assertNotIn("isaac.memory_search", names)

    def test_action_request_never_executes_requested_action(self):
        from mcp_registry import get_mcp_registry

        result = get_mcp_registry().invoke_tool(
            "isaac.action_request",
            {"action": "system_command", "reason": "test request"},
            caller="MCP-HTTP",
            caller_level=Level.TASK,
            allow_owner_override=False,
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["output"]["request"]["executed"])


class TestMCPHTTPBoundary(unittest.TestCase):
    def test_remote_http_requires_api_key(self):
        from app import app

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ISAAC_MCP_API_KEY", None)
            client = app.test_client()
            response = client.get(
                "/api/mcp/tools",
                environ_base={"REMOTE_ADDR": "203.0.113.10"},
            )
            self.assertEqual(response.status_code, 401)

    def test_remote_http_accepts_valid_api_key(self):
        from app import app

        with patch.dict(os.environ, {"ISAAC_MCP_API_KEY": "test-secret"}, clear=False):
            client = app.test_client()
            response = client.get(
                "/api/mcp/tools",
                headers={"Authorization": "Bearer test-secret"},
                environ_base={"REMOTE_ADDR": "203.0.113.10"},
            )
            self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
