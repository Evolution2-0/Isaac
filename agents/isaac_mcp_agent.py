from __future__ import annotations

"""Minimal external MCP client for Isaac.

The client has no Isaac privilege of its own. It authenticates to the remote
MCP boundary and lets Isaac decide which tools are visible and callable.
"""

import json
import os
from typing import Any
from urllib import request


class IsaacMCPAgent:
    def __init__(self, endpoint: str | None = None, api_key: str | None = None,
                 *, caller_name: str | None = None, protocol_version: str = "2026-07-28",
                 opener=request.urlopen, timeout: float = 20.0):
        self.endpoint = endpoint or os.getenv("ISAAC_MCP_URL", "http://127.0.0.1:8766/api/mcp")
        self.api_key = api_key or os.getenv("ISAAC_MCP_API_KEY", "")
        self.caller_name = caller_name or os.getenv("ISAAC_MCP_CALLER", "external-agent-01")
        self.protocol_version = protocol_version
        self.opener = opener
        self.timeout = timeout
        self._request_id = 0

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = dict(params or {})
        meta = dict(params.get("_meta") or {})
        meta.setdefault("io.modelcontextprotocol/protocolVersion", self.protocol_version)
        meta.setdefault("io.modelcontextprotocol/clientInfo",
                        {"name": self.caller_name, "version": "0.1.0"})
        params["_meta"] = meta
        self._request_id += 1
        payload = {"jsonrpc":"2.0","id":self._request_id,"method":method,"params":params}
        headers = {
            "Accept":"application/json, text/event-stream",
            "Content-Type":"application/json",
            "MCP-Protocol-Version":self.protocol_version,
            "Mcp-Method":method,
        }
        if method in {"tools/call","resources/read","prompts/get"}:
            headers["Mcp-Name"]=str(params.get("name") or params.get("uri") or "").strip()
        if self.api_key:
            headers["Authorization"]=f"Bearer {self.api_key}"
        req=request.Request(self.endpoint,data=json.dumps(payload).encode("utf-8"),
                            headers=headers,method="POST")
        with self.opener(req,timeout=self.timeout) as response:
            body=response.read().decode("utf-8")
            return json.loads(body) if body else {"accepted":True}

    def list_tools(self) -> list[dict[str, Any]]:
        return list(self.call("tools/list").get("result",{}).get("tools",[]))

    def call_tool(self,name: str,arguments: dict[str,Any] | None = None) -> dict[str,Any]:
        return self.call("tools/call",{"name":name,"arguments":dict(arguments or {})})

    def smoke_test(self) -> dict[str,Any]:
        tools=self.list_tools()
        names={tool.get("name") for tool in tools}
        goal_list=self.call_tool("isaac.goal_list")
        write_probe=self.call_tool("isaac.goal_update",
                                   {"goal":"__external_agent_probe__","status":"done"})
        spoof=self.call_tool("isaac.query_memory",
                             {"query":"external-agent-governance-probe",
                              "owner_override":True,"override_reason":"smoke-test"})
        return {
            "caller":self.caller_name,
            "protocol_version":self.protocol_version,
            "tools_visible":sorted(n for n in names if n),
            "goal_list_ok":not goal_list.get("result",{}).get("isError",True),
            "write_blocked":write_probe.get("result",{}).get("isError",False),
            "owner_override_blocked":spoof.get("result",{}).get("isError",False),
        }


if __name__=="__main__":
    print(json.dumps(IsaacMCPAgent().smoke_test(),ensure_ascii=False,indent=2))
