from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from app import app
from agents.isaac_mcp_agent import IsaacMCPAgent


class _FakeResponse:
    def __init__(self, payload): self.payload=payload
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def read(self): return json.dumps(self.payload).encode("utf-8")


class TestStreamableHTTP(unittest.TestCase):
    def setUp(self):
        self.client=app.test_client()
        self.env=patch.dict(os.environ,{"ISAAC_MCP_API_KEY":"test-secret"},clear=False)
        self.env.start()

    def tearDown(self): self.env.stop()

    def _headers(self,method,name=None):
        h={"Authorization":"Bearer test-secret","Content-Type":"application/json",
           "Accept":"application/json, text/event-stream",
           "MCP-Protocol-Version":"2026-07-28","Mcp-Method":method}
        if name: h["Mcp-Name"]=name
        return h

    def _payload(self,method,params=None):
        return {"jsonrpc":"2.0","id":1,"method":method,"params":params or {}}

    def test_tools_list_modern_transport(self):
        r=self.client.post("/api/mcp",
            json=self._payload("tools/list",{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28"}}),
            headers=self._headers("tools/list"),environ_base={"REMOTE_ADDR":"203.0.113.10"})
        self.assertEqual(r.status_code,200)
        names={x["name"] for x in r.get_json()["result"]["tools"]}
        self.assertIn("isaac.goal_list",names)
        self.assertNotIn("isaac.goal_update",names)

    def test_tools_call_is_governed(self):
        r=self.client.post("/api/mcp",
            json=self._payload("tools/call",{"name":"isaac.goal_list","arguments":{},
                                              "_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28"}}),
            headers=self._headers("tools/call","isaac.goal_list"),
            environ_base={"REMOTE_ADDR":"203.0.113.10"})
        self.assertEqual(r.status_code,200)
        self.assertFalse(r.get_json()["result"]["isError"])

    def test_write_and_owner_override_are_blocked(self):
        r=self.client.post("/api/mcp",
            json=self._payload("tools/call",{"name":"isaac.goal_update",
                                              "arguments":{"goal":"x","status":"done"},
                                              "_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28"}}),
            headers=self._headers("tools/call","isaac.goal_update"),
            environ_base={"REMOTE_ADDR":"203.0.113.10"})
        self.assertEqual(r.status_code,200)
        self.assertTrue(r.get_json()["result"]["isError"])

        r=self.client.post("/api/mcp",
            json=self._payload("tools/call",{"name":"isaac.query_memory",
                                              "arguments":{"query":"x","owner_override":True,
                                                          "override_reason":"spoof"},
                                              "_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28"}}),
            headers=self._headers("tools/call","isaac.query_memory"),
            environ_base={"REMOTE_ADDR":"203.0.113.10"})
        self.assertEqual(r.status_code,200)
        self.assertTrue(r.get_json()["result"]["isError"])

    def test_origin_is_validated(self):
        r=self.client.post("/api/mcp",json=self._payload("tools/list"),
            headers={**self._headers("tools/list"),"Origin":"https://evil.example"},
            environ_base={"REMOTE_ADDR":"203.0.113.10"})
        self.assertEqual(r.status_code,403)

    def test_header_mismatch_is_rejected(self):
        r=self.client.post("/api/mcp",json=self._payload("tools/list"),
            headers={**self._headers("tools/list"),"Mcp-Method":"tools/call"},
            environ_base={"REMOTE_ADDR":"203.0.113.10"})
        self.assertEqual(r.status_code,400)

    def test_agent_builds_modern_requests(self):
        seen={}
        def fake(req,timeout=0):
            seen["headers"]=dict(req.headers)
            seen["body"]=json.loads(req.data.decode("utf-8"))
            return _FakeResponse({"jsonrpc":"2.0","id":1,
                                  "result":{"tools":[{"name":"isaac.goal_list"}]}})
        agent=IsaacMCPAgent(endpoint="https://example.test/mcp",api_key="secret",
                           caller_name="external-agent-test",opener=fake)
        result=agent.call("tools/list")
        self.assertEqual(result["result"]["tools"][0]["name"],"isaac.goal_list")
        self.assertEqual(seen["headers"]["Mcp-Method"],"tools/list")
        self.assertEqual(seen["headers"]["Mcp-Protocol-Version"],"2026-07-28")
        self.assertNotIn("Mcp-Session-Id",seen["headers"])
        self.assertEqual(seen["body"]["params"]["_meta"]["io.modelcontextprotocol/clientInfo"]["name"],
                         "external-agent-test")


if __name__=="__main__":
    unittest.main()
