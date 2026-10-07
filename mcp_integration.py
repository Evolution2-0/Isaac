from __future__ import annotations

"""Isaac – MCP Integration Core Module
Integration manager for MCP (Model Context Protocol) servers supporting JSON-RPC 2.0,
stdio and HTTP/SSE transports, tool discovery, tool registration, and tool execution.
"""

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from mcp_jsonrpc import JSONRPC_VERSION
from mcp_registry import MCPRegistry, get_mcp_registry
from result_contract import ensure_result_contract

log = logging.getLogger("Isaac.MCP.Integration")


def build_jsonrpc_request(
    method: str,
    params: Dict[str, Any] | None = None,
    req_id: int | str = 1,
) -> Dict[str, Any]:
    """Build a standard JSON-RPC 2.0 request payload."""
    payload: Dict[str, Any] = {
        "jsonrpc": JSONRPC_VERSION,
        "id": req_id,
        "method": method,
    }
    if params is not None:
        payload["params"] = params
    return payload


def parse_jsonrpc_response(response: Dict[str, Any]) -> Dict[str, Any]:
    """Parse a JSON-RPC 2.0 response payload and format result contract."""
    if not isinstance(response, dict):
        return ensure_result_contract(
            {"ok": False, "error": "Invalid JSON-RPC response format (expected dict)"},
            source="mcp_integration",
        )

    if "error" in response and response["error"]:
        err = response["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        return ensure_result_contract(
            {"ok": False, "error": msg or "JSON-RPC error response"},
            source="mcp_integration",
        )

    result = response.get("result", {})
    return ensure_result_contract(
        {"ok": True, "output": result},
        source="mcp_integration",
    )


class MCPIntegrationManager:
    """Manages MCP server configurations, stdio/SSE connections, tool discovery, and execution."""

    def __init__(self, config_source: Union[str, Path, Dict[str, Any], None] = None):
        self.servers: Dict[str, Dict[str, Any]] = {}
        self.discovered_tools: Dict[str, List[Dict[str, Any]]] = {}
        if config_source is not None:
            self.load_config(config_source)

    def load_config(
        self, config_source: Union[str, Path, Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """Load and parse MCP server configurations from file path or dict."""
        if isinstance(config_source, (str, Path)):
            path = Path(config_source)
            if not path.is_file():
                raise FileNotFoundError(f"MCP configuration file not found: {path}")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in MCP configuration file: {exc}") from exc
        elif isinstance(config_source, dict):
            data = config_source
        else:
            raise TypeError("config_source must be a file path or dict")

        if not isinstance(data, dict):
            raise ValueError("Configuration root must be a JSON object")

        servers = data.get("mcpServers") or data.get("mcp_servers") or {}
        if not isinstance(servers, dict):
            raise ValueError("'mcpServers' must be a dictionary")

        self.servers = servers
        log.info("Loaded %d MCP server configurations", len(self.servers))
        return self.servers

    def list_servers(self) -> List[str]:
        """Return names of all configured MCP servers."""
        return list(self.servers.keys())

    def get_server_config(self, server_name: str) -> Optional[Dict[str, Any]]:
        """Get configuration for a specific MCP server."""
        return self.servers.get(server_name)

    def discover_tools_sync(self, server_name: str) -> List[Dict[str, Any]]:
        """Synchronously discover tools provided by a configured stdio MCP server."""
        config = self.get_server_config(server_name)
        if not config:
            raise ValueError(f"Server '{server_name}' not configured")

        command = config.get("command")
        if not command:
            # If server has static tool definitions in config
            if "tools" in config and isinstance(config["tools"], list):
                self.discovered_tools[server_name] = config["tools"]
                return config["tools"]
            raise ValueError(f"Server '{server_name}' missing command or static tools")

        args = config.get("args", [])
        env = {**os.environ, **config.get("env", {})}

        req = build_jsonrpc_request("tools/list", req_id=1)
        req_str = json.dumps(req) + "\n"

        try:
            proc = subprocess.Popen(
                [command] + args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            stdout, stderr = proc.communicate(input=req_str, timeout=10)
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as exc:
            log.error("Failed to run MCP server process for %s: %s", server_name, exc)
            return []

        tools: List[Dict[str, Any]] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                res = json.loads(line)
                if isinstance(res, dict) and res.get("id") == 1:
                    result = res.get("result", {})
                    tools = result.get("tools", []) if isinstance(result, dict) else []
                    break
            except json.JSONDecodeError:
                continue

        self.discovered_tools[server_name] = tools
        return tools

    async def discover_tools(
        self, server_name: Optional[str] = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Asynchronously discover tools for specified server or all configured servers."""
        target_servers = [server_name] if server_name else self.list_servers()
        results: Dict[str, List[Dict[str, Any]]] = {}

        for s_name in target_servers:
            try:
                loop = asyncio.get_running_loop()
                tools = await loop.run_in_executor(None, self.discover_tools_sync, s_name)
                results[s_name] = tools
            except Exception as exc:
                log.warning("Discovery failed for server '%s': %s", s_name, exc)
                results[s_name] = []

        return results

    def register_tools_with_mcp_registry(
        self,
        registry: Optional[MCPRegistry] = None,
        server_name: Optional[str] = None,
    ) -> int:
        """Register discovered tools into Isaac's MCPRegistry."""
        reg = registry or get_mcp_registry()
        count = 0
        target_servers = [server_name] if server_name else list(self.discovered_tools.keys())

        for s_name in target_servers:
            tools = self.discovered_tools.get(s_name, [])
            for tool in tools:
                raw_name = tool.get("name")
                if not raw_name:
                    continue
                mcp_tool_name = f"{s_name}.{raw_name}"
                description = tool.get("description", f"MCP Tool from {s_name}")
                input_schema = tool.get("inputSchema", {})

                # Handler wrapper
                def make_handler(srv: str, t_name: str):
                    def handler(**kwargs):
                        return self.execute_tool_sync(srv, t_name, kwargs)

                    return handler

                reg.register_tool(
                    name=mcp_tool_name,
                    schema={
                        "description": description,
                        "server": s_name,
                        "inputSchema": input_schema,
                    },
                    handler=make_handler(s_name, raw_name),
                )
                count += 1

        log.info("Registered %d MCP tools into registry", count)
        return count

    def execute_tool_sync(
        self,
        server_name: str,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Synchronously execute a tool call on an MCP server via JSON-RPC 2.0 over stdio."""
        config = self.get_server_config(server_name)
        if not config:
            return ensure_result_contract(
                {"ok": False, "error": f"Server '{server_name}' not configured"},
                source=f"mcp_integration:{server_name}.{tool_name}",
            )

        command = config.get("command")
        if not command:
            return ensure_result_contract(
                {"ok": False, "error": f"Server '{server_name}' has no command configured"},
                source=f"mcp_integration:{server_name}.{tool_name}",
            )

        args = config.get("args", [])
        env = {**os.environ, **config.get("env", {})}

        req = build_jsonrpc_request(
            "tools/call",
            params={"name": tool_name, "arguments": arguments or {}},
            req_id=2,
        )
        req_str = json.dumps(req) + "\n"

        try:
            proc = subprocess.Popen(
                [command] + args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            stdout, stderr = proc.communicate(input=req_str, timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            return ensure_result_contract(
                {"ok": False, "error": f"Tool execution timed out on server '{server_name}'"},
                source=f"mcp_integration:{server_name}.{tool_name}",
            )
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as exc:
            return ensure_result_contract(
                {"ok": False, "error": f"Failed to execute MCP tool process: {exc}"},
                source=f"mcp_integration:{server_name}.{tool_name}",
            )

        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                res = json.loads(line)
                if isinstance(res, dict) and res.get("id") == 2:
                    if res.get("error"):
                        err = res["error"]
                        msg = err.get("message") if isinstance(err, dict) else str(err)
                        return ensure_result_contract(
                            {"ok": False, "error": msg},
                            source=f"mcp_integration:{server_name}.{tool_name}",
                        )
                    result = res.get("result", {})
                    is_error = result.get("isError", False) if isinstance(result, dict) else False
                    content = result.get("content", []) if isinstance(result, dict) else result
                    return ensure_result_contract(
                        {"ok": not is_error, "output": content, "raw_result": result},
                        source=f"mcp_integration:{server_name}.{tool_name}",
                    )
            except json.JSONDecodeError:
                continue

        return ensure_result_contract(
            {"ok": False, "error": f"No valid JSON-RPC response from '{server_name}'"},
            source=f"mcp_integration:{server_name}.{tool_name}",
        )

    async def execute_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Asynchronously execute a tool call on an MCP server."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.execute_tool_sync, server_name, tool_name, arguments
        )


# Alias for convenience
MCPIntegration = MCPIntegrationManager
