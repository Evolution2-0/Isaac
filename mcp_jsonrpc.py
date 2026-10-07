from __future__ import annotations

"""Isaac MCP JSON-RPC dispatcher.

The legacy /api/mcp/jsonrpc endpoint keeps the existing initialize-based
transport for compatibility. The modern stateless Streamable HTTP transport
uses the same dispatcher without requiring initialize/session state.
"""

import json
import logging
import os
from typing import Any

from config import Level
from mcp_registry import MCPRegistry

log = logging.getLogger("Isaac.MCP.JsonRpc")

JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = os.getenv("ISAAC_MCP_PROTOCOL_VERSION", "2026-07-28")
LEGACY_MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "isaac"
SERVER_VERSION = os.getenv("ISAAC_MCP_VERSION", "1.0.0")

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
APPLICATION_ERROR = -32000


class MCPJsonRpcHandler:
    def __init__(self, registry: MCPRegistry, *, service=None, caller: str = "MCP",
                 caller_level: int = Level.TASK, trusted_internal: bool = False):
        self.registry = registry
        self.service = service
        self.caller = caller
        self.caller_level = int(caller_level)
        self.trusted_internal = bool(trusted_internal)
        self._initialized = False
        self._client_info: dict[str, Any] = {}

    def dispatch(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(message, dict):
            return self._error(None, INVALID_REQUEST, "Request must be an object")
        if message.get("jsonrpc") != JSONRPC_VERSION:
            return self._error(message.get("id"), INVALID_REQUEST, "Invalid JSON-RPC version")
        req_id, method, params = message.get("id"), message.get("method"), message.get("params") or {}
        if not method or not isinstance(method, str):
            return self._error(req_id, INVALID_REQUEST, "Method is required")
        if req_id is None:
            self._handle_notification(method, params if isinstance(params, dict) else {})
            return None
        try:
            result = self._handle_request(method, params if isinstance(params, dict) else {})
            return {"jsonrpc": JSONRPC_VERSION, "id": req_id, "result": result}
        except MCPRpcError as exc:
            return self._error(req_id, exc.code, exc.message, exc.data)
        except Exception as exc:
            log.exception("MCP JSON-RPC internal error for %s", method)
            return self._error(req_id, INTERNAL_ERROR, str(exc))

    def dispatch_batch(self, messages: list[Any]) -> list[dict[str, Any]]:
        responses = []
        for message in messages:
            response = self.dispatch(message)
            if response is not None:
                responses.append(response)
        return responses

    def handle_payload(self, payload: Any) -> Any:
        if isinstance(payload, list):
            return self.dispatch_batch(payload)
        if isinstance(payload, dict):
            return self.dispatch(payload)
        raise MCPRpcError(PARSE_ERROR, "Payload must be object or array")

    def _handle_notification(self, method: str, params: dict[str, Any]) -> None:
        if method == "notifications/initialized":
            self._initialized = True
            return
        log.debug("Ignored MCP notification: %s", method)

    def _handle_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            return self._initialize(params)
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": self._format_tools()}
        if method == "tools/call":
            return self._tools_call(params)
        if method == "resources/list":
            return {"resources": self._format_resources()}
        if method == "resources/read":
            return self._resources_read(params)
        if method == "prompts/list":
            return {"prompts": self._format_prompts()}
        if method == "prompts/get":
            return self._prompts_get(params)
        raise MCPRpcError(METHOD_NOT_FOUND, f"Unknown method: {method}")

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        self._client_info = dict(params.get("clientInfo") or {})
        self._initialized = True
        return {
            "protocolVersion": LEGACY_MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}, "resources": {"subscribe": False, "listChanged": False},
                             "prompts": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    def _format_tools(self) -> list[dict[str, Any]]:
        items = (self.service.list_tools(caller_level=self.caller_level, trusted_internal=self.trusted_internal)
                 if self.service is not None else self.registry.tools())
        return [{"name": i.get("name",""), "description": i.get("description",""),
                 "inputSchema": i.get("inputSchema") or {"type":"object","properties":{}}} for i in items]

    def _format_resources(self) -> list[dict[str, Any]]:
        return [{"uri": i.get("uri",""), "name": i.get("name") or i.get("uri",""),
                 "description": i.get("description",""), "mimeType": i.get("mimeType","application/json")}
                for i in self.registry.resources()]

    def _format_prompts(self) -> list[dict[str, Any]]:
        return [{"name": i.get("name",""), "description": i.get("description",""),
                 "arguments": i.get("arguments") or []} for i in self.registry.prompts()]

    def _tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = (params.get("name") or "").strip()
        if not name:
            raise MCPRpcError(INVALID_PARAMS, "tools/call requires name")
        arguments = dict(params.get("arguments") or {})
        result = (self.service.invoke(name, arguments, caller=self.caller, caller_level=self.caller_level,
                                      trusted_internal=self.trusted_internal)
                  if self.service is not None else self.registry.invoke_tool(name, arguments))
        return _tool_result_to_mcp(result)

    def _resources_read(self, params: dict[str, Any]) -> dict[str, Any]:
        uri = (params.get("uri") or "").strip()
        if not uri:
            raise MCPRpcError(INVALID_PARAMS, "resources/read requires uri")
        result = self.registry.read_resource(uri, **dict(params.get("params") or {}))
        if not result.get("ok"):
            raise MCPRpcError(APPLICATION_ERROR, result.get("error","resource read failed"), {"uri":uri})
        schema = next((r for r in self.registry.resources() if r.get("uri") == uri), {})
        resource = result.get("resource", {})
        text = resource if isinstance(resource, str) else json.dumps(resource, ensure_ascii=False, indent=2)
        return {"contents":[{"uri":uri,"mimeType":schema.get("mimeType","application/json"),"text":text}]}

    def _prompts_get(self, params: dict[str, Any]) -> dict[str, Any]:
        name = (params.get("name") or "").strip()
        if not name:
            raise MCPRpcError(INVALID_PARAMS, "prompts/get requires name")
        result = self.registry.get_prompt(name, dict(params.get("arguments") or {}))
        if not result.get("ok"):
            raise MCPRpcError(APPLICATION_ERROR, result.get("error","prompt get failed"), {"name":name})
        return {"description":name,"messages":[{"role":"user","content":{"type":"text","text":str(result.get("prompt",""))}}]}

    @staticmethod
    def _error(req_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
        payload={"jsonrpc":"2.0","id":req_id,"error":{"code":code,"message":message}}
        if data is not None: payload["error"]["data"]=data
        return payload


class MCPRpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message); self.code=code; self.message=message; self.data=data


def _tool_result_to_mcp(result: dict[str, Any]) -> dict[str, Any]:
    if not result.get("ok"):
        return {"content":[{"type":"text","text":str(result.get("error","error"))}],"isError":True}
    output=result.get("output", result.get("content", result))
    text=output if isinstance(output,str) else json.dumps(output, ensure_ascii=False, indent=2)
    return {"content":[{"type":"text","text":text}],"isError":False}


def get_jsonrpc_handler(registry: MCPRegistry | None = None, *, service=None,
                        caller: str = "MCP", caller_level: int = Level.TASK,
                        trusted_internal: bool = False) -> MCPJsonRpcHandler:
    if registry is None:
        from mcp_registry import get_mcp_registry
        registry=get_mcp_registry()
    return MCPJsonRpcHandler(registry, service=service, caller=caller,
                             caller_level=caller_level, trusted_internal=trusted_internal)
