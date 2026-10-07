from __future__ import annotations

"""Isaac – MCP Registry
Leichtgewichtige MCP-nahe Registry mit lokalen Resource-/Tool-/Prompt-Handlern.
"""

import asyncio
from typing import Dict, Any, List, Callable, Optional

from config import Level
from result_contract import ensure_result_contract

# Privilege-Mapping: MCP-Name -> benötigte Privilege-Aktion
MCP_TOOL_PRIVILEGES: Dict[str, str] = {
    "isaac.task_status": "read_memory",
    "isaac.audit_recent": "read_audit",
    "isaac.query_memory": "read_memory",
    "isaac.start_task": "chat_response",
    "isaac.search_web": "internet_search",
    "isaac.run_browser_action": "browser_navigate",
    "isaac.goal_list": "read_memory",
    "isaac.goal_get": "read_memory",
    "isaac.goal_update": "write_memory",
    "isaac.permission_check": "read_memory",
    "isaac.safety_check": "read_memory",
    "isaac.action_request": "chat_response",
    "isaac.autonomy_evaluate": "read_memory",
    "isaac.notification_send": "chat_response",
}

MCP_RESOURCE_PRIVILEGES: Dict[str, str] = {
    "resource://constitution": "read_memory",
    "resource://self-model": "read_memory",
    "resource://memory/blocks": "read_memory",
    "resource://procedures": "read_memory",
    "resource://audit/tail": "read_audit",
    "isaac://tasks/recent": "read_memory",
    "isaac://tools/registry": "read_memory",
}

MCP_CONSTITUTION_GATED_TOOLS = frozenset({
    "isaac.search_web",
    "isaac.run_browser_action",
    "isaac.start_task",
})


def _handler_result_contract(name: str, result: Any) -> Dict[str, Any]:
    if isinstance(result, dict) and (
        "output" in result or "content" in result or "error" in result or "metadata" in result
    ):
        return ensure_result_contract(result, source=f"mcp_registry:{name}")
    return ensure_result_contract(
        {"ok": True, "output": result, "metadata": {"tool": name}},
        source=f"mcp_registry:{name}",
    )


class MCPRegistry:
    def __init__(self):
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._resources: Dict[str, Dict[str, Any]] = {}
        self._prompts: Dict[str, Dict[str, Any]] = {}

    def register_tool(self, name: str, schema: Dict[str, Any], handler: Callable[..., Any] | None = None):
        entry = dict(schema)
        if handler is not None:
            entry["_handler"] = handler
        self._tools[name] = entry

    def register_resource(self, uri: str, schema: Dict[str, Any], handler: Callable[..., Any] | None = None):
        entry = dict(schema)
        if handler is not None:
            entry["_handler"] = handler
        self._resources[uri] = entry

    def register_prompt(self, name: str, schema: Dict[str, Any], handler: Callable[..., Any] | None = None):
        entry = dict(schema)
        if handler is not None:
            entry["_handler"] = handler
        self._prompts[name] = entry

    def tools(self) -> List[Dict[str, Any]]:
        out = []
        for k, v in sorted(self._tools.items()):
            item = {"name": k, **v}
            item.pop("_handler", None)
            out.append(item)
        return out

    def resources(self) -> List[Dict[str, Any]]:
        out = []
        for k, v in sorted(self._resources.items()):
            item = {"uri": k, **v}
            item.pop("_handler", None)
            out.append(item)
        return out

    def prompts(self) -> List[Dict[str, Any]]:
        out = []
        for k, v in sorted(self._prompts.items()):
            item = {"name": k, **v}
            item.pop("_handler", None)
            out.append(item)
        return out

    def capabilities(self) -> Dict[str, Any]:
        return {
            "tools": sorted(self._tools.keys()),
            "resources": sorted(self._resources.keys()),
            "prompts": sorted(self._prompts.keys()),
            "resource_count": len(self._resources),
            "tool_count": len(self._tools),
            "prompt_count": len(self._prompts),
            "features": ["tools", "resources", "prompts"],
            "tool_privileges": dict(MCP_TOOL_PRIVILEGES),
            "resource_privileges": dict(MCP_RESOURCE_PRIVILEGES),
        }

    def invoke_tool(
        self,
        name: str,
        arguments: Dict[str, Any] | None = None,
        *,
        caller: str = "MCP",
        caller_level: int = Level.ISAAC,
        allow_owner_override: bool = True,
    ) -> Dict[str, Any]:
        tool = self._tools.get(name)
        if not tool:
            return {"ok": False, "error": f"Unknown MCP tool: {name}"}
        args = dict(arguments or {})
        override_ctx = _extract_override_context(
            args,
            source="mcp",
            caller_level=caller_level,
            allow_owner_override=allow_owner_override,
        )
        denied = _authorize_mcp_tool(name, caller=caller, caller_level=caller_level)
        if denied:
            return denied
        if name in MCP_CONSTITUTION_GATED_TOOLS:
            denied = _constitution_gate_mcp_tool(name, override_ctx=override_ctx)
            if denied:
                return denied
        handler = tool.get("_handler")
        if handler is None:
            return {"ok": False, "error": f"Tool has no handler: {name}"}
        try:
            result = handler(**args)
            return _handler_result_contract(name, result)
        except TypeError as e:
            return ensure_result_contract({"ok": False, "error": f"Argument error: {e}", "metadata": {"tool": name}}, source=f"mcp_registry:{name}")
        except Exception as e:
            return ensure_result_contract({"ok": False, "error": str(e), "metadata": {"tool": name}}, source=f"mcp_registry:{name}")

    def read_resource(self, uri: str, **kwargs) -> Dict[str, Any]:
        res = self._resources.get(uri)
        if not res:
            return {"ok": False, "error": f"Unknown MCP resource: {uri}"}
        denied = _authorize_mcp_resource(uri)
        if denied:
            return denied
        handler = res.get("_handler")
        if handler is None:
            return {"ok": False, "error": f"Resource has no handler: {uri}"}
        try:
            value = handler(**kwargs)
            return {"ok": True, "uri": uri, "resource": value}
        except TypeError as e:
            return {"ok": False, "uri": uri, "error": f"Argument error: {e}"}
        except Exception as e:
            return {"ok": False, "uri": uri, "error": str(e)}

    def get_prompt(self, name: str, arguments: Dict[str, Any] | None = None) -> Dict[str, Any]:
        entry = self._prompts.get(name)
        if not entry:
            return {"ok": False, "error": f"Unknown MCP prompt: {name}"}
        handler = entry.get("_handler")
        if handler is None:
            return {"ok": False, "error": f"Prompt has no handler: {name}"}
        try:
            prompt = handler(**(arguments or {}))
            return {"ok": True, "name": name, "prompt": prompt}
        except TypeError as e:
            return {"ok": False, "name": name, "error": f"Argument error: {e}"}
        except Exception as e:
            return {"ok": False, "name": name, "error": str(e)}


def _authorize_mcp_tool(
    name: str,
    *,
    caller: str = "MCP",
    caller_level: int = Level.ISAAC,
) -> Optional[Dict[str, Any]]:
    from privilege import get_gate, PrivCtx

    action = MCP_TOOL_PRIVILEGES.get(name, "chat_response")
    ctx = PrivCtx(
        caller=caller or "MCP",
        level=int(caller_level),
        r_trace=f"MCP tool invoke: {name}",
    )
    ok, reason = get_gate().authorize(action, ctx)
    if ok:
        return None
    return ensure_result_contract(
        {
            "ok": False,
            "error": reason,
            "metadata": {"tool": name, "required_privilege": action},
        },
        source="mcp_registry:privilege",
    )


def _authorize_mcp_resource(uri: str) -> Optional[Dict[str, Any]]:
    from privilege import get_gate, isaac_ctx

    action = MCP_RESOURCE_PRIVILEGES.get(uri, "read_memory")
    ok, reason = get_gate().authorize(action, isaac_ctx("MCP", f"MCP resource read: {uri}"))
    if ok:
        return None
    return {"ok": False, "uri": uri, "error": reason, "required_privilege": action}


def _extract_override_context(
    args: Dict[str, Any],
    source: str = "mcp",
    *,
    caller_level: int = Level.ISAAC,
    allow_owner_override: bool = True,
):
    from constitution_override import build_override_context

    owner_override = bool(args.pop("owner_override", False))
    override_reason = str(args.pop("override_reason", "") or "")
    if not allow_owner_override:
        owner_override = False
        override_reason = ""
    return build_override_context(
        owner_override=owner_override,
        override_reason=override_reason,
        caller_level=Level.STEFFEN if owner_override else int(caller_level),
        source=source,
    )


def _constitution_gate_mcp_tool(
    name: str,
    override_ctx=None,
) -> Optional[Dict[str, Any]]:
    from constitution_override import apply_constitution_gate

    gate = apply_constitution_gate(
        "tool_invoke",
        {"outside_effect": True, "audit_logged": True, "risk": "normal"},
        override_ctx,
    )
    if gate.get("allowed"):
        return None
    blocked = gate.get("blocked_by", [])
    override = gate.get("override") or {}
    return ensure_result_contract(
        {
            "ok": False,
            "error": (
                f"Verfassung blockiert MCP-Tool: {', '.join(blocked)} "
                f"({override.get('reason', 'blockiert')})"
            ),
            "metadata": {"tool": name, "blocked_by": blocked, "override_denied": override},
        },
        source="mcp_registry:constitution",
    )


def _goal_list(status: str = "active", limit: int = 20, **kwargs) -> Dict[str, Any]:
    from goal_store import get_goal_store

    status_value = (status or "").strip().lower() or None
    if status_value not in {None, "active", "paused", "done", "failed"}:
        return {"ok": False, "error": "ungültiger goal status"}
    rows = get_goal_store().list_goals(status=status_value)
    rows = rows[: max(1, min(int(limit), 50))]
    return {"goals": [g.to_dict() for g in rows], "count": len(rows)}


def _goal_get(goal: str = "", **kwargs) -> Dict[str, Any]:
    from goal_store import get_goal_store

    item = get_goal_store().find_goal(goal)
    if not item:
        return {"ok": False, "error": "goal not found"}
    subgoals = get_goal_store().list_subgoals(item.id, status=None)
    return {"goal": item.to_dict(), "subgoals": [s.to_dict() for s in subgoals]}


def _goal_update(goal: str = "", status: str = "", **kwargs) -> Dict[str, Any]:
    from goal_store import get_goal_store

    status_value = (status or "").strip().lower()
    if status_value not in {"active", "paused", "done", "failed"}:
        return {"ok": False, "error": "status must be active, paused, done, or failed"}
    item = get_goal_store().set_status(goal, status_value)
    if not item:
        return {"ok": False, "error": "goal not found"}
    return {"ok": True, "goal": item.to_dict()}


def _permission_check(action: str = "", trace: str = "", **kwargs) -> Dict[str, Any]:
    from privilege import PrivCtx, get_gate

    action = (action or "").strip()
    trace = (trace or "").strip()
    if not action:
        return {"ok": False, "error": "action fehlt"}
    if len(trace) < 15:
        trace = f"MCP permission check for {action}"
    ok, reason = get_gate().authorize(
        action, PrivCtx(caller="MCP", level=Level.TASK, r_trace=trace)
    )
    return {"ok": True, "allowed": bool(ok), "reason": reason, "action": action, "caller_level": Level.TASK}


def _safety_check(
    action: str = "tool_invoke",
    risk: str = "normal",
    outside_effect: bool = True,
    destructive: bool = False,
    **kwargs,
) -> Dict[str, Any]:
    from constitution import get_constitution

    metadata = {
        "outside_effect": bool(outside_effect),
        "audit_logged": True,
        "risk": str(risk or "normal"),
        "destructive": bool(destructive),
        "owner_approved": False,
    }
    verdict = get_constitution().validate_action(action, metadata)
    return {
        "ok": True,
        "allowed": bool(verdict.get("allowed")),
        "warnings": verdict.get("warnings", []),
        "blocked_by": verdict.get("blocked_by", []),
        "action": action,
        "metadata": metadata,
    }


def _action_request(
    action: str = "",
    reason: str = "",
    risk: str = "normal",
    outside_effect: bool = True,
    **kwargs,
) -> Dict[str, Any]:
    """Create a full autonomy-gated action proposal; it never executes."""
    action = (action or "").strip()
    reason = (reason or "").strip()
    if not action:
        return {"ok": False, "error": "action fehlt"}

    from isaac_control_plane import get_autonomy_control_plane

    evaluated = get_autonomy_control_plane().evaluate(
        action=action,
        reason=reason,
        risk=risk,
        outside_effect=outside_effect,
        destructive=bool(kwargs.get("destructive", False)),
        goal=str(kwargs.get("goal", "") or ""),
        memory_query=str(kwargs.get("memory_query", "") or ""),
        caller=str(kwargs.get("_caller", "MCP")),
        caller_level=int(kwargs.get("_caller_level", Level.TASK)),
        trusted_internal=bool(kwargs.get("_trusted_internal", False)),
    )
    decision = evaluated.get("output", {}).get("decision", {})
    return {
        "ok": bool(evaluated.get("ok")),
        "request": {
            **decision.get("proposal", {
                "action": action,
                "reason": reason[:500],
                "risk": str(risk or "normal"),
                "outside_effect": bool(outside_effect),
                "executed": False,
            }),
            "decision": decision,
        },
    }


def _autonomy_evaluate(
    action: str = "",
    reason: str = "",
    risk: str = "normal",
    outside_effect: bool = True,
    destructive: bool = False,
    goal: str = "",
    memory_query: str = "",
    **kwargs,
) -> Dict[str, Any]:
    """Run the full Isaac autonomy gate without executing the action."""
    from isaac_control_plane import get_autonomy_control_plane

    return get_autonomy_control_plane().evaluate(
        action=action,
        reason=reason,
        risk=risk,
        outside_effect=outside_effect,
        destructive=destructive,
        goal=goal,
        memory_query=memory_query,
        caller=str(kwargs.get("_caller", "MCP")),
        caller_level=int(kwargs.get("_caller_level", Level.TASK)),
        trusted_internal=bool(kwargs.get("_trusted_internal", False)),
    )


def _notification_send(
    message: str = "",
    title: str = "Isaac",
    channel: str = "audit",
    **kwargs,
) -> Dict[str, Any]:
    """Record a notification intent; delivery remains outside the MCP registry."""
    message = (message or "").strip()
    if not message:
        return {"ok": False, "error": "message fehlt"}
    from audit import AuditLog

    AuditLog.action(
        "MCP",
        "notification_requested",
        f"title={title[:80]} channel={channel[:40]} message_len={len(message)}",
        erfolg=True,
    )
    return {
        "ok": True,
        "queued": False,
        "recorded": True,
        "channel": channel,
        "title": title[:120],
        "message_preview": message[:240],
        "delivery": "not configured",
    }


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("MCP handler cannot run async coroutine inside active event loop")


def _read_constitution(**kwargs) -> Dict[str, Any]:
    from constitution import get_constitution

    c = get_constitution()
    return {"version": c.version(), "summary": c.summary(), "export": c.export()}


def _read_self_model(**kwargs) -> Dict[str, Any]:
    from self_model import get_self_model

    return get_self_model().snapshot()


def _read_procedures(limit: int = 20, **kwargs) -> Dict[str, Any]:
    from memory import get_memory

    limit = max(1, min(int(limit), 50))
    procedures = get_memory().list_procedures(limit)
    return {
        "procedures": procedures,
        "count": len(procedures),
        "degraded": sum(1 for p in procedures if p.get("degraded")),
    }


def _read_memory_blocks(limit: int = 20, **kwargs) -> Dict[str, Any]:
    from memory import get_memory

    mem = get_memory()
    limit = max(1, min(int(limit), 50))
    blocks: list[dict] = []
    for directive in mem.get_directives()[:limit]:
        blocks.append(
            {
                "block_type": "directive",
                "key": directive.get("id", ""),
                "content": directive.get("text", ""),
                "priority": directive.get("priority", 0),
            }
        )
    for key, value in list(mem.all_facts().items())[:limit]:
        blocks.append({"block_type": "fact", "key": key, "content": value})
    for event in mem.recent_development_events(min(limit, 10)):
        blocks.append(
            {
                "block_type": "development",
                "key": event.get("target_key", ""),
                "content": event.get("reason", ""),
                "delta": event.get("delta", 0.0),
            }
        )
    return {"blocks": blocks[:limit], "count": len(blocks[:limit])}


def _read_audit_tail(n: int = 50, **kwargs) -> list[dict]:
    from audit import AuditLog

    return AuditLog.recent(max(1, min(int(n), 200)))


def _query_memory(query: str = "", limit: int = 6, **kwargs) -> Dict[str, Any]:
    from memory import get_memory

    mem = get_memory()
    q = (query or "").strip()
    ctx = mem.build_retrieval_context(q or "status", n_history=min(int(limit), 10))
    return ctx.as_dict()


def _start_task(prompt: str = "", typ: str = "chat", **kwargs) -> Dict[str, Any]:
    from executor import get_executor, TaskType

    text = (prompt or "").strip()
    if not text:
        return {"ok": False, "error": "prompt fehlt"}
    type_map = {
        "chat": TaskType.CHAT,
        "search": TaskType.SEARCH,
        "code": TaskType.CODE,
    }
    task_type = type_map.get((typ or "chat").lower(), TaskType.CHAT)
    task = get_executor().create_task(task_type, text, beschreibung=text[:120])
    return {"ok": True, "task_id": task.id, "status": task.status.value, "typ": task.typ.value}


def _search_web(query: str = "", **kwargs) -> Dict[str, Any]:
    from search import get_search

    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "query fehlt"}

    async def _do():
        result = await get_search().search(q, max_hits=5, load_fulltext=False)
        return {
            "query": result.query,
            "hits": len(result.hits),
            "fehler": result.fehler[:3],
            "summary": result.als_kontext(max_hits=5)[:1500],
        }

    payload = _run_async(_do())
    return {"ok": True, "query": q, "result": payload}


def _run_browser_action(action: str = "open", url: str = "", **kwargs) -> Dict[str, Any]:
    from browser import get_browser

    target = (url or "").strip()
    if not target:
        return {"ok": False, "error": "url fehlt"}
    browser = get_browser()
    if not browser._browser_allowed(target):
        return {"ok": False, "error": "Browser deaktiviert oder URL nicht erlaubt"}
    if action != "open":
        return {"ok": False, "error": f"Unbekannte Browser-Aktion: {action}"}
    return {
        "ok": True,
        "action": action,
        "url": target,
        "note": "Browser-Flow ist vorbereitet; Live-Ausführung erfolgt über Executor/BrowserManager.",
    }


_registry: MCPRegistry | None = None


def get_mcp_registry() -> MCPRegistry:
    global _registry
    if _registry is None:
        _registry = MCPRegistry()
        _register_defaults(_registry)
    return _registry


def _register_defaults(reg: MCPRegistry):
    if reg.tools():
        return reg

    reg.register_tool(
        "isaac.task_status",
        {
            "description": "Liest den Status eines Isaac-Tasks.",
            "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}}},
            "required_privilege": MCP_TOOL_PRIVILEGES["isaac.task_status"],
        },
        handler=lambda task_id="", **kwargs: (
            __import__("executor").get_executor().get_task(task_id).to_dict()
            if task_id and __import__("executor").get_executor().get_task(task_id)
            else {"ok": True, "tasks": __import__("executor").get_executor().all_tasks(10)}
        ),
    )
    reg.register_tool(
        "isaac.audit_recent",
        {
            "description": "Liest die letzten Audit-Einträge.",
            "inputSchema": {"type": "object", "properties": {"n": {"type": "integer"}}},
            "required_privilege": MCP_TOOL_PRIVILEGES["isaac.audit_recent"],
        },
        handler=_read_audit_tail,
    )
    reg.register_tool(
        "isaac.query_memory",
        {
            "description": "Baut einen strukturierten Retrieval-Kontext für eine Anfrage.",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            },
            "required_privilege": MCP_TOOL_PRIVILEGES["isaac.query_memory"],
        },
        handler=_query_memory,
    )
    reg.register_tool(
        "isaac.start_task",
        {
            "description": "Erstellt einen neuen Isaac-Task (ohne automatische Ausführung).",
            "inputSchema": {
                "type": "object",
                "properties": {"prompt": {"type": "string"}, "typ": {"type": "string"}},
                "required": ["prompt"],
            },
            "required_privilege": MCP_TOOL_PRIVILEGES["isaac.start_task"],
        },
        handler=_start_task,
    )
    reg.register_tool(
        "isaac.search_web",
        {
            "description": "Führt eine Websuche über Isaacs Search-Modul aus.",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            "required_privilege": MCP_TOOL_PRIVILEGES["isaac.search_web"],
        },
        handler=_search_web,
    )
    reg.register_tool(
        "isaac.goal_list",
        {
            "description": "Listet Isaacs aktive oder abgeschlossene Owner-Ziele.",
            "inputSchema": {"type": "object", "properties": {"status": {"type": "string"}, "limit": {"type": "integer"}}},
            "required_privilege": "read_memory",
        },
        handler=_goal_list,
    )
    reg.register_tool(
        "isaac.goal_get",
        {
            "description": "Liest ein Owner-Ziel und seine Subgoals.",
            "inputSchema": {"type": "object", "properties": {"goal": {"type": "string"}}, "required": ["goal"]},
            "required_privilege": "read_memory",
        },
        handler=_goal_get,
    )
    reg.register_tool(
        "isaac.goal_update",
        {
            "description": "Ändert den Status eines Owner-Ziels; keine Tool-Ausführung.",
            "inputSchema": {"type": "object", "properties": {"goal": {"type": "string"}, "status": {"type": "string"}}, "required": ["goal", "status"]},
            "required_privilege": "write_memory",
        },
        handler=_goal_update,
    )
    reg.register_tool(
        "isaac.permission_check",
        {
            "description": "Prüft, ob ein MCP-Aufruf unter Isaacs Privilege-Gate zulässig wäre.",
            "inputSchema": {"type": "object", "properties": {"action": {"type": "string"}, "trace": {"type": "string"}}, "required": ["action"]},
            "required_privilege": "read_memory",
        },
        handler=_permission_check,
    )
    reg.register_tool(
        "isaac.safety_check",
        {
            "description": "Prüft eine geplante Aktion gegen Isaacs Constitution, ohne sie auszuführen.",
            "inputSchema": {"type": "object", "properties": {"action": {"type": "string"}, "risk": {"type": "string"}, "outside_effect": {"type": "boolean"}, "destructive": {"type": "boolean"}}},
            "required_privilege": "read_memory",
        },
        handler=_safety_check,
    )
    reg.register_tool(
        "isaac.autonomy_evaluate",
        {
            "description": "Führt Intent-, Memory-, Goal-, Permission-, Safety- und Confirmation-Prüfung zusammen; führt niemals aus.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "reason": {"type": "string"},
                    "risk": {"type": "string"},
                    "outside_effect": {"type": "boolean"},
                    "destructive": {"type": "boolean"},
                    "goal": {"type": "string"},
                    "memory_query": {"type": "string"},
                },
                "required": ["action"],
            },
            "required_privilege": MCP_TOOL_PRIVILEGES["isaac.autonomy_evaluate"],
        },
        handler=_autonomy_evaluate,
    )
    reg.register_tool(
        "isaac.action_request",
        {
            "description": "Erzeugt einen geprüften Aktionsvorschlag; führt die Aktion niemals aus.",
            "inputSchema": {"type": "object", "properties": {"action": {"type": "string"}, "reason": {"type": "string"}, "risk": {"type": "string"}, "outside_effect": {"type": "boolean"}}, "required": ["action"]},
            "required_privilege": "chat_response",
        },
        handler=_action_request,
    )
    reg.register_tool(
        "isaac.notification_send",
        {
            "description": "Erfasst eine Benachrichtigungsanforderung; externe Zustellung ist nicht implizit.",
            "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}, "title": {"type": "string"}, "channel": {"type": "string"}}, "required": ["message"]},
            "required_privilege": "chat_response",
        },
        handler=_notification_send,
    )

    reg.register_tool(
        "isaac.run_browser_action",
        {
            "description": "Führt eine explizite Browser-Aktion aus.",
            "inputSchema": {
                "type": "object",
                "properties": {"action": {"type": "string"}, "url": {"type": "string"}},
                "required": ["url"],
            },
            "required_privilege": MCP_TOOL_PRIVILEGES["isaac.run_browser_action"],
        },
        handler=_run_browser_action,
    )

    reg.register_resource(
        "resource://constitution",
        {"description": "Isaac-Verfassung (Version, Summary, Export).", "mimeType": "application/json"},
        handler=_read_constitution,
    )
    reg.register_resource(
        "resource://self-model",
        {"description": "Explizites Isaac-Selbstmodell.", "mimeType": "application/json"},
        handler=_read_self_model,
    )
    reg.register_resource(
        "resource://memory/blocks",
        {"description": "Strukturierte Memory-Blöcke (Fakten, Direktiven, Entwicklung).", "mimeType": "application/json"},
        handler=_read_memory_blocks,
    )
    reg.register_resource(
        "resource://procedures",
        {"description": "Gespeicherte Task-Verfahren mit Zuverlässigkeit und Degradierung.", "mimeType": "application/json"},
        handler=_read_procedures,
    )
    reg.register_resource(
        "resource://audit/tail",
        {"description": "Letzte Audit-Einträge.", "mimeType": "application/json"},
        handler=_read_audit_tail,
    )
    reg.register_resource(
        "isaac://tasks/recent",
        {"description": "Aktuelle und letzte Tasks als Resource."},
        handler=lambda limit=20, **kwargs: __import__("executor").get_executor().all_tasks(int(limit)),
    )
    reg.register_resource(
        "isaac://tools/registry",
        {"description": "Lokale Tool-Registry als Resource."},
        handler=lambda **kwargs: __import__("tool_registry").get_tool_registry().list_tools(),
    )
    reg.register_prompt(
        "tool.refine_input",
        {"description": "Erzeugt einen nächsten Arbeitsinput aus einem Tool-Ergebnis."},
        handler=lambda original_prompt="", tool_name="", tool_output="", **kwargs: (
            f"Arbeite an der Aufgabe weiter: {original_prompt}\n\n"
            f"Neues Ergebnis von {tool_name}:\n{tool_output[:1200]}\n\n"
            "Extrahiere den nächsten konkreten Schritt und fokussiere nur die neuen, relevanten Informationen."
        ),
    )
    reg.register_prompt(
        "research.next_step",
        {"description": "Erzeugt einen nächsten Recherche-Schritt."},
        handler=lambda topic="", **kwargs: (
            f"Bestimme den nächsten Recherche-Schritt für: {topic}. "
            "Konzentriere dich auf fehlende Fakten, Quellen oder offene Fragen."
        ),
    )
    return reg
