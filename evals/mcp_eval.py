from __future__ import annotations

"""MCP contract eval suite (Track C2).

Covers JSON-RPC initialize/list/call/read, all registered resources,
prompts, privilege-map completeness, unknown rejection shapes, and
stdio transport smoke. No subagent expansion.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mcp_jsonrpc import get_jsonrpc_handler
from mcp_registry import (
    MCP_CONSTITUTION_GATED_TOOLS,
    MCP_RESOURCE_PRIVILEGES,
    MCP_TOOL_PRIVILEGES,
    get_mcp_registry,
)

# Canonical inventory — must match mcp_registry._register_defaults
EXPECTED_TOOLS = frozenset({
    "isaac.task_status",
    "isaac.audit_recent",
    "isaac.query_memory",
    "isaac.start_task",
    "isaac.search_web",
    "isaac.run_browser_action",
    "isaac.goal_list",
    "isaac.goal_get",
    "isaac.goal_update",
    "isaac.permission_check",
    "isaac.safety_check",
    "isaac.action_request",
    "isaac.autonomy_evaluate",
    "isaac.notification_send",
})

EXPECTED_RESOURCES = frozenset({
    "resource://constitution",
    "resource://self-model",
    "resource://memory/blocks",
    "resource://procedures",
    "resource://audit/tail",
    "isaac://tasks/recent",
    "isaac://tools/registry",
})

EXPECTED_PROMPTS = frozenset({
    "tool.refine_input",
    "research.next_step",
})


def _case(name: str, ok: bool, detail: dict | None = None) -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail or {}}


def run() -> dict:
    reg = get_mcp_registry()
    caps = reg.capabilities()
    handler = get_jsonrpc_handler(reg)
    cases: list[dict] = []

    # --- initialize ---
    init = handler.dispatch({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {}},
    })
    server_info = (init or {}).get("result", {}).get("serverInfo", {})
    init_caps = (init or {}).get("result", {}).get("capabilities", {})
    cases.append(_case(
        "jsonrpc_initialize",
        bool(server_info.get("name") == "isaac" and server_info.get("version")),
        server_info,
    ))
    cases.append(_case(
        "jsonrpc_initialize_capabilities",
        all(k in init_caps for k in ("tools", "resources", "prompts")),
        {"keys": sorted(init_caps.keys())},
    ))

    # --- tools/list: full inventory ---
    tools_rpc = handler.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tool_names = {
        t.get("name")
        for t in (tools_rpc or {}).get("result", {}).get("tools", [])
        if t.get("name")
    }
    cases.append(_case(
        "jsonrpc_tools_list",
        EXPECTED_TOOLS.issubset(tool_names) and "isaac.query_memory" in tool_names,
        {"count": len(tool_names), "names": sorted(tool_names)},
    ))
    cases.append(_case(
        "tools_inventory_exact",
        tool_names == EXPECTED_TOOLS,
        {
            "missing": sorted(EXPECTED_TOOLS - tool_names),
            "extra": sorted(tool_names - EXPECTED_TOOLS),
        },
    ))

    # --- tools/call happy path ---
    tool_call = handler.dispatch({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "isaac.query_memory", "arguments": {"query": "status", "limit": 2}},
    })
    cases.append(_case(
        "jsonrpc_tools_call",
        bool(tool_call and not (tool_call.get("result") or {}).get("isError")),
        {"has_content": bool((tool_call or {}).get("result", {}).get("content"))},
    ))

    # --- resources/list + read inventory ---
    resources_rpc = handler.dispatch({
        "jsonrpc": "2.0", "id": 4, "method": "resources/list", "params": {},
    })
    resource_uris = {
        r.get("uri")
        for r in (resources_rpc or {}).get("result", {}).get("resources", [])
        if r.get("uri")
    }
    cases.append(_case(
        "jsonrpc_resources_list",
        resource_uris == EXPECTED_RESOURCES,
        {
            "count": len(resource_uris),
            "missing": sorted(EXPECTED_RESOURCES - resource_uris),
            "extra": sorted(resource_uris - EXPECTED_RESOURCES),
        },
    ))

    resource_read = handler.dispatch({
        "jsonrpc": "2.0",
        "id": 5,
        "method": "resources/read",
        "params": {"uri": "resource://constitution"},
    })
    resource_text = ""
    contents = (resource_read or {}).get("result", {}).get("contents") or []
    if contents:
        resource_text = str(contents[0].get("text", ""))
    cases.append(_case(
        "jsonrpc_resources_read",
        bool(resource_read and contents and "constitution" in resource_text.lower()),
        {"bytes": len(resource_text)},
    ))

    for uri in sorted(EXPECTED_RESOURCES):
        present = uri in caps.get("resources", [])
        read_ok = False
        detail: dict = {}
        if present:
            result = reg.read_resource(uri, limit=5, n=5)
            read_ok = bool(result.get("ok"))
            resource = result.get("resource")
            if isinstance(resource, dict):
                detail = {"keys": sorted(resource.keys())}
            else:
                detail = {"type": type(resource).__name__}
            # Contract: successful reads expose uri + resource, no error
            if read_ok:
                detail["has_uri"] = result.get("uri") == uri
                read_ok = read_ok and detail["has_uri"] and "error" not in result
        short = uri.split("://", 1)[-1].replace("/", "_")
        cases.append(_case(
            f"resource_{short}",
            present and read_ok,
            detail,
        ))

    # --- unknown resource (registry + JSON-RPC) ---
    unknown_res = reg.read_resource("resource://does_not_exist")
    cases.append(_case(
        "unknown_resource_rejected",
        (
            not unknown_res.get("ok")
            and "Unknown MCP resource" in str(unknown_res.get("error", ""))
        ),
        {"error": unknown_res.get("error", "")},
    ))
    unknown_res_rpc = handler.dispatch({
        "jsonrpc": "2.0",
        "id": 6,
        "method": "resources/read",
        "params": {"uri": "resource://does_not_exist"},
    })
    err_obj = (unknown_res_rpc or {}).get("error") or {}
    cases.append(_case(
        "jsonrpc_unknown_resource_error",
        bool(err_obj.get("message")) and "Unknown MCP resource" in str(err_obj.get("message", "")),
        {"error": err_obj},
    ))

    # --- prompts/list + get ---
    prompts_rpc = handler.dispatch({
        "jsonrpc": "2.0", "id": 7, "method": "prompts/list", "params": {},
    })
    prompt_names = {
        p.get("name")
        for p in (prompts_rpc or {}).get("result", {}).get("prompts", [])
        if p.get("name")
    }
    cases.append(_case(
        "jsonrpc_prompts_list",
        prompt_names == EXPECTED_PROMPTS,
        {
            "count": len(prompt_names),
            "missing": sorted(EXPECTED_PROMPTS - prompt_names),
            "extra": sorted(prompt_names - EXPECTED_PROMPTS),
        },
    ))

    prompt_get = handler.dispatch({
        "jsonrpc": "2.0",
        "id": 8,
        "method": "prompts/get",
        "params": {
            "name": "research.next_step",
            "arguments": {"topic": "MCP contracts"},
        },
    })
    messages = (prompt_get or {}).get("result", {}).get("messages") or []
    prompt_text = ""
    if messages:
        content = messages[0].get("content") or {}
        prompt_text = str(content.get("text", ""))
    cases.append(_case(
        "jsonrpc_prompts_get",
        bool(messages) and "MCP contracts" in prompt_text,
        {"messages": len(messages), "preview": prompt_text[:120]},
    ))

    refine = reg.get_prompt(
        "tool.refine_input",
        {
            "original_prompt": "test task",
            "tool_name": "search",
            "tool_output": "found facts",
        },
    )
    cases.append(_case(
        "prompt_refine_input",
        bool(refine.get("ok")) and "test task" in str(refine.get("prompt", "")),
        {"ok": refine.get("ok")},
    ))

    unknown_prompt = reg.get_prompt("does.not.exist", {})
    cases.append(_case(
        "unknown_prompt_rejected",
        (
            not unknown_prompt.get("ok")
            and "Unknown MCP prompt" in str(unknown_prompt.get("error", ""))
        ),
        {"error": unknown_prompt.get("error", "")},
    ))

    # --- privilege maps complete & exact ---
    tool_priv = caps.get("tool_privileges") or {}
    res_priv = caps.get("resource_privileges") or {}
    cases.append(_case(
        "privilege_map_present",
        bool(tool_priv) and bool(res_priv),
        {
            "tools": len(MCP_TOOL_PRIVILEGES),
            "resources": len(MCP_RESOURCE_PRIVILEGES),
        },
    ))
    cases.append(_case(
        "privilege_map_tools_complete",
        set(MCP_TOOL_PRIVILEGES.keys()) == EXPECTED_TOOLS
        and set(tool_priv.keys()) == EXPECTED_TOOLS,
        {
            "map_keys": sorted(MCP_TOOL_PRIVILEGES.keys()),
            "missing": sorted(EXPECTED_TOOLS - set(MCP_TOOL_PRIVILEGES)),
        },
    ))
    cases.append(_case(
        "privilege_map_resources_complete",
        set(MCP_RESOURCE_PRIVILEGES.keys()) == EXPECTED_RESOURCES
        and set(res_priv.keys()) == EXPECTED_RESOURCES,
        {
            "map_keys": sorted(MCP_RESOURCE_PRIVILEGES.keys()),
            "missing": sorted(EXPECTED_RESOURCES - set(MCP_RESOURCE_PRIVILEGES)),
        },
    ))
    # Sensitive tools must map to non-trivial privileges
    cases.append(_case(
        "privilege_sensitive_tools",
        (
            MCP_TOOL_PRIVILEGES.get("isaac.search_web") == "internet_search"
            and MCP_TOOL_PRIVILEGES.get("isaac.run_browser_action") == "browser_navigate"
            and MCP_TOOL_PRIVILEGES.get("isaac.audit_recent") == "read_audit"
            and MCP_RESOURCE_PRIVILEGES.get("resource://audit/tail") == "read_audit"
        ),
        {
            "search": MCP_TOOL_PRIVILEGES.get("isaac.search_web"),
            "browser": MCP_TOOL_PRIVILEGES.get("isaac.run_browser_action"),
            "audit_tool": MCP_TOOL_PRIVILEGES.get("isaac.audit_recent"),
            "audit_res": MCP_RESOURCE_PRIVILEGES.get("resource://audit/tail"),
        },
    ))
    cases.append(_case(
        "constitution_gated_tools",
        MCP_CONSTITUTION_GATED_TOOLS == frozenset({
            "isaac.search_web",
            "isaac.run_browser_action",
            "isaac.start_task",
        }),
        {"gated": sorted(MCP_CONSTITUTION_GATED_TOOLS)},
    ))

    # --- direct tool invoke + unknown ---
    query = reg.invoke_tool("isaac.query_memory", {"query": "Isaac status", "limit": 3})
    cases.append(_case(
        "query_memory_tool",
        bool(query.get("ok")),
        {"has_output": "output" in query},
    ))

    unknown = reg.invoke_tool("isaac.does_not_exist", {})
    cases.append(_case(
        "unknown_tool_rejected",
        not unknown.get("ok") and "Unknown MCP tool" in str(unknown.get("error", "")),
        {"error": unknown.get("error", "")},
    ))

    # Unknown tool via JSON-RPC should surface as isError content
    unknown_rpc = handler.dispatch({
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {"name": "isaac.does_not_exist", "arguments": {}},
    })
    unknown_result = (unknown_rpc or {}).get("result") or {}
    cases.append(_case(
        "jsonrpc_unknown_tool_is_error",
        bool(unknown_result.get("isError")),
        {"isError": unknown_result.get("isError"), "has_content": bool(unknown_result.get("content"))},
    ))

    # --- capabilities surface ---
    cases.append(_case(
        "capabilities_features",
        set(caps.get("features") or []) >= {"tools", "resources", "prompts"}
        and caps.get("tool_count") == len(EXPECTED_TOOLS)
        and caps.get("resource_count") == len(EXPECTED_RESOURCES)
        and caps.get("prompt_count") == len(EXPECTED_PROMPTS),
        {
            "features": caps.get("features"),
            "tool_count": caps.get("tool_count"),
            "resource_count": caps.get("resource_count"),
            "prompt_count": caps.get("prompt_count"),
        },
    ))

    # --- stdio transport smoke ---
    from mcp_server import run_stdio_transport
    import io

    old_stdin = sys.stdin
    old_stdout = sys.stdout
    try:
        sys.stdin = io.StringIO(
            '{"jsonrpc":"2.0","id":99,"method":"initialize",'
            '"params":{"protocolVersion":"2024-11-05"}}\n'
        )
        sys.stdout = io.StringIO()
        res_code = run_stdio_transport()
        stdio_out = sys.stdout.getvalue()
        stdio_ok = res_code == 0 and "serverInfo" in stdio_out
    finally:
        sys.stdin = old_stdin
        sys.stdout = old_stdout

    cases.append(_case(
        "stdio_transport_smoke",
        stdio_ok,
        {"stdio_response_received": stdio_ok},
    ))

    passed = sum(1 for c in cases if c["ok"])
    return {"suite": "mcp", "passed": passed, "total": len(cases), "cases": cases}


if __name__ == "__main__":
    import json

    print(json.dumps(run(), ensure_ascii=False, indent=2))
