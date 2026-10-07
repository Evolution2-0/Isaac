from __future__ import annotations

"""Isaac – governed MCP HTTP and stdio transports."""

import json
import os
import sys
from urllib.parse import urlparse

from flask import Blueprint, jsonify, request

from audit import AuditLog
from config import Level
from isaac_mcp import get_isaac_mcp_service
from mcp_jsonrpc import get_jsonrpc_handler

mcp_api = Blueprint("mcp_api", __name__, url_prefix="/api/mcp")


def _registry():
    from mcp_registry import get_mcp_registry
    return get_mcp_registry()


def _service():
    return get_isaac_mcp_service(_registry())


def _origin_allowed(origin: str) -> bool:
    if not origin:
        return True
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    host = (parsed.hostname or "").lower()
    request_host = (request.host.split(":", 1)[0] or "").lower()
    configured = {h.strip().lower() for h in os.getenv("ISAAC_MCP_ALLOWED_ORIGINS", "").split(",") if h.strip()}
    return host == request_host or host in configured


@mcp_api.before_request
def _mcp_guard():
    service=_service()
    if (request.content_length or 0) > service.max_body_bytes():
        AuditLog.action("MCP","request_blocked","body_too_large",erfolg=False)
        return jsonify({"ok":False,"error":"MCP request body exceeds configured limit"}),413
    if request.path == "/api/mcp" and request.method == "POST" and not _origin_allowed(request.headers.get("Origin","")):
        AuditLog.action("MCP","request_blocked","invalid_origin",erfolg=False)
        return jsonify({"jsonrpc":"2.0","id":None,"error":{"code":-32000,"message":"Invalid Origin"}}),403
    ok,reason=service.authenticate_http(request.headers,request.remote_addr)
    if not ok:
        AuditLog.action("MCP","authentication_failed","invalid_or_missing_credentials",erfolg=False)
        return jsonify({"ok":False,"error":reason}),401


@mcp_api.get("/")
def root():
    reg=_registry(); service=_service()
    return jsonify({"ok":True,"transport":["rest","jsonrpc","streamable-http","stdio"],
                    "jsonrpc_endpoint":"/api/mcp/jsonrpc","mcp_endpoint":"/api/mcp",
                    "capabilities":service.capabilities(caller_level=Level.TASK),
                    "tools":service.list_tools(caller_level=Level.TASK),
                    "resources":reg.resources(),"prompts":reg.prompts()})


@mcp_api.post("")
def streamable_http():
    payload=request.get_json(silent=True)
    if payload is None:
        return jsonify({"jsonrpc":"2.0","id":None,"error":{"code":-32700,"message":"Parse error"}}),400
    if isinstance(payload,list):
        return jsonify({"jsonrpc":"2.0","id":None,"error":{"code":-32600,"message":"Batch requests are not supported"}}),400

    protocol=request.headers.get("MCP-Protocol-Version","").strip()
    if protocol!="2026-07-28":
        return jsonify({"jsonrpc":"2.0","id":payload.get("id"),
                        "error":{"code":-32000,"message":"Unsupported MCP protocol version",
                                 "data":{"supported":["2026-07-28"]}}}),400

    method=request.headers.get("Mcp-Method","").strip()
    params=payload.get("params") or {}
    if method != str(payload.get("method","")).strip():
        return jsonify({"jsonrpc":"2.0","id":payload.get("id"),
                        "error":{"code":-32000,"message":"Mcp-Method header mismatch"}}),400

    if method in {"tools/call","resources/read","prompts/get"}:
        expected=str(params.get("name") or params.get("uri") or "").strip()
        if request.headers.get("Mcp-Name","").strip()!=expected:
            return jsonify({"jsonrpc":"2.0","id":payload.get("id"),
                            "error":{"code":-32000,"message":"Mcp-Name header mismatch"}}),400

    meta=params.get("_meta") or {}
    body_version=meta.get("io.modelcontextprotocol/protocolVersion")
    if body_version and body_version != protocol:
        return jsonify({"jsonrpc":"2.0","id":payload.get("id"),
                        "error":{"code":-32000,"message":"HeaderMismatch: protocol version"}}),400

    result=get_jsonrpc_handler(_registry(),service=_service(),
                               caller="MCP-STREAMABLE-HTTP",caller_level=Level.TASK,
                               trusted_internal=False).handle_payload(payload)
    if result is None:
        return "",202
    response=jsonify(result)
    response.headers["Content-Type"]="application/json"
    return response,200


@mcp_api.post("/jsonrpc")
def jsonrpc():
    payload=request.get_json(silent=True)
    if payload is None:
        return jsonify({"jsonrpc":"2.0","id":None,"error":{"code":-32700,"message":"Parse error"}}),400
    result=get_jsonrpc_handler(_registry(),service=_service(),caller="MCP-HTTP",
                               caller_level=Level.TASK,trusted_internal=False).handle_payload(payload)
    return jsonify(result),200


@mcp_api.get("/capabilities")
def capabilities():
    return jsonify({"ok":True,"capabilities":_service().capabilities(caller_level=Level.TASK)})


@mcp_api.get("/resources")
def resources():
    return jsonify({"ok":True,"resources":_registry().resources()})


@mcp_api.post("/resource/read")
def read_resource():
    data=request.get_json(silent=True) or {}
    result=_registry().read_resource((data.get("uri") or "").strip(),**dict(data.get("params") or {}))
    return jsonify(result),(200 if result.get("ok") else 404)


@mcp_api.get("/prompts")
def prompts():
    return jsonify({"ok":True,"prompts":_registry().prompts()})


@mcp_api.post("/prompts/get")
def get_prompt():
    data=request.get_json(silent=True) or {}
    result=_registry().get_prompt((data.get("name") or "").strip(),dict(data.get("arguments") or {}))
    return jsonify(result),(200 if result.get("ok") else 404)


@mcp_api.get("/tools")
def tools():
    return jsonify({"ok":True,"tools":_service().list_tools(caller_level=Level.TASK)})


@mcp_api.post("/tools/invoke")
def invoke_tool():
    data=request.get_json(silent=True) or {}
    result=_service().invoke((data.get("name") or "").strip(),dict(data.get("arguments") or {}),
                             caller="MCP-HTTP",caller_level=Level.TASK,trusted_internal=False)
    return jsonify(result),(200 if result.get("ok") else 400)


def run_stdio_transport() -> int:
    handler=get_jsonrpc_handler(_registry(),service=_service(),caller="MCP-STDIO",
                                caller_level=Level.TASK,trusted_internal=False)
    for line in sys.stdin:
        line=line.strip()
        if not line: continue
        try: payload=json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":None,"error":{"code":-32700,"message":"Parse error"}})+"\n"); sys.stdout.flush(); continue
        try: result=handler.handle_payload(payload)
        except Exception as exc: result={"jsonrpc":"2.0","id":None,"error":{"code":-32603,"message":str(exc)}}
        if isinstance(result,list):
            for item in result: sys.stdout.write(json.dumps(item,ensure_ascii=False)+"\n")
        elif result is not None:
            sys.stdout.write(json.dumps(result,ensure_ascii=False)+"\n")
        sys.stdout.flush()
    return 0


if __name__=="__main__":
    raise SystemExit(run_stdio_transport() if "--stdio" in sys.argv else 0)
