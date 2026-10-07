from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from mcp_integration import (
    MCPIntegrationManager,
    build_jsonrpc_request,
    parse_jsonrpc_response,
)
from mcp_registry import MCPRegistry


class TestMCPIntegration(unittest.TestCase):

    def setUp(self):
        self.sample_config = {
            "mcpServers": {
                "fetch": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-fetch"],
                },
                "time": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-time"],
                },
                "custom_static": {
                    "tools": [
                        {
                            "name": "static_tool",
                            "description": "Static tool for test",
                            "inputSchema": {"type": "object"},
                        }
                    ]
                },
            }
        }

    def test_build_jsonrpc_request(self):
        req = build_jsonrpc_request("tools/list", {"param": "val"}, req_id=42)
        self.assertEqual(req["jsonrpc"], "2.0")
        self.assertEqual(req["id"], 42)
        self.assertEqual(req["method"], "tools/list")
        self.assertEqual(req["params"], {"param": "val"})

    def test_parse_jsonrpc_response_success(self):
        response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"tools": [{"name": "test_tool"}]},
        }
        res = parse_jsonrpc_response(response)
        self.assertTrue(res["ok"])
        self.assertEqual(res["output"], {"tools": [{"name": "test_tool"}]})

    def test_parse_jsonrpc_response_error(self):
        response = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32601, "message": "Method not found"},
        }
        res = parse_jsonrpc_response(response)
        self.assertFalse(res["ok"])
        self.assertIn("Method not found", res["error"])

    def test_load_config_from_dict(self):
        manager = MCPIntegrationManager(self.sample_config)
        self.assertEqual(len(manager.list_servers()), 3)
        self.assertIn("fetch", manager.list_servers())
        self.assertIn("time", manager.list_servers())
        self.assertIn("custom_static", manager.list_servers())

    def test_load_config_from_file(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
            json.dump(self.sample_config, f)
            temp_path = f.name

        try:
            manager = MCPIntegrationManager()
            servers = manager.load_config(temp_path)
            self.assertIn("fetch", servers)
            self.assertEqual(manager.get_server_config("fetch")["command"], "npx")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_load_config_file_not_found(self):
        manager = MCPIntegrationManager()
        with self.assertRaises(FileNotFoundError):
            manager.load_config("non_existent_mcp_config.json")

    def test_load_config_invalid_json(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
            f.write("{ invalid json ")
            temp_path = f.name

        try:
            manager = MCPIntegrationManager()
            with self.assertRaises(ValueError):
                manager.load_config(temp_path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_discover_tools_static(self):
        manager = MCPIntegrationManager(self.sample_config)
        tools = manager.discover_tools_sync("custom_static")
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "static_tool")

    @patch("subprocess.Popen")
    def test_discover_tools_sync_mock_process(self, mock_popen):
        mock_proc = MagicMock()
        json_resp = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "tools": [
                    {"name": "fetch_url", "description": "Fetch content from URL"}
                ]
            },
        })
        mock_proc.communicate.return_value = (json_resp, "")
        mock_popen.return_value = mock_proc

        manager = MCPIntegrationManager(self.sample_config)
        tools = manager.discover_tools_sync("fetch")
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "fetch_url")

    def test_register_tools_with_mcp_registry(self):
        manager = MCPIntegrationManager(self.sample_config)
        manager.discovered_tools = {
            "fetch": [
                {
                    "name": "fetch_url",
                    "description": "Fetch URL",
                    "inputSchema": {"type": "object"},
                }
            ]
        }
        registry = MCPRegistry()
        count = manager.register_tools_with_mcp_registry(registry)
        self.assertEqual(count, 1)

        tool_names = [t["name"] for t in registry.tools()]
        self.assertIn("fetch.fetch_url", tool_names)

    @patch("subprocess.Popen")
    def test_execute_tool_sync_success(self, mock_popen):
        mock_proc = MagicMock()
        json_resp = json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "content": [{"type": "text", "text": "Page content here"}],
                "isError": False,
            },
        })
        mock_proc.communicate.return_value = (json_resp, "")
        mock_popen.return_value = mock_proc

        manager = MCPIntegrationManager(self.sample_config)
        res = manager.execute_tool_sync("fetch", "fetch_url", {"url": "https://example.com"})
        self.assertTrue(res["ok"])
        self.assertEqual(res["output"], [{"type": "text", "text": "Page content here"}])

    @patch("subprocess.Popen")
    def test_execute_tool_sync_timeout(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="npx", timeout=15)
        mock_popen.return_value = mock_proc

        manager = MCPIntegrationManager(self.sample_config)
        res = manager.execute_tool_sync("fetch", "fetch_url", {"url": "https://example.com"})
        self.assertFalse(res["ok"])
        self.assertIn("timed out", res["error"])

    def test_execute_tool_unconfigured_server(self):
        manager = MCPIntegrationManager(self.sample_config)
        res = manager.execute_tool_sync("unknown_server", "some_tool")
        self.assertFalse(res["ok"])
        self.assertIn("not configured", res["error"])

    def test_execute_tool_async(self):
        manager = MCPIntegrationManager(self.sample_config)
        with patch.object(
            manager,
            "execute_tool_sync",
            return_value={"ok": True, "output": "async_ok"},
        ):
            res = asyncio.run(manager.execute_tool("fetch", "fetch_url", {}))
            self.assertTrue(res["ok"])
            self.assertEqual(res["output"], "async_ok")


if __name__ == "__main__":
    unittest.main()
