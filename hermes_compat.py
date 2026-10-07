from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from audit import AuditLog
from mcp_registry import MCPRegistry
from security_policy import ConfirmationPolicy, SecurityVerdict
from result_contract import ensure_result_contract, error_result


@dataclass(frozen=True)
class ToolInput:
    payload: dict[str, Any]


@dataclass
class ToolResult:
    ok: bool
    output: Any = None
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    queue_id: str = ""
    audit_id: str = ""


@dataclass(frozen=True)
class ExecutionContext:
    caller: str = "hermes"
    level: int = 0
    r_trace: str = ""
    task_id: str = ""


@dataclass(frozen=True)
class PermissionMetadata:
    risk: str = "medium"
    outside_effect: bool = False
    requires_confirmation: bool = False
    timeout: float = 30.0
    allowed_scope: str = "local"

    def as_policy_meta(self) -> dict[str, Any]:
        return {
            "risk": self.risk,
            "outside_effect": self.outside_effect,
            "requires_confirmation": self.requires_confirmation,
            "timeout": self.timeout,
            "allowed_scope": self.allowed_scope,
        }


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    category: str
    input_schema: dict[str, Any]
    permission: PermissionMetadata
    handler: Callable[[ToolInput, ExecutionContext], ToolResult]
    enabled: bool = True


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    tools: tuple[str, ...] = ()
    enabled: bool = True


@dataclass(frozen=True)
class ComputerAction:
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    permission: PermissionMetadata = field(default_factory=PermissionMetadata)


@dataclass(frozen=True)
class BrowserAction(ComputerAction):
    url: str = ""


class SkillRegistry:
    def __init__(self):
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill):
        self._skills[skill.name] = skill

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)


class HermesToolSchemaMapper:
    @staticmethod
    def normalize(raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": raw.get("name", "").strip(),
            "description": raw.get("description", "").strip(),
            "category": (raw.get("category") or "general").strip(),
            "input_schema": dict(raw.get("input_schema") or raw.get("inputSchema") or {"type": "object"}),
            "risk": (raw.get("risk") or "medium").lower(),
            "outside_effect": bool(raw.get("outside_effect", False)),
            "requires_confirmation": bool(raw.get("requires_confirmation", False)),
            "timeout": float(raw.get("timeout", 30.0)),
            "enabled": bool(raw.get("enabled", True)),
        }


class HermesToolAdapter:
    def __init__(self, schema: dict[str, Any], handler: Callable[[dict[str, Any]], Any]):
        self.schema = HermesToolSchemaMapper.normalize(schema)
        self._handler = handler

    def to_tool(self) -> Tool:
        permission = PermissionMetadata(
            risk=self.schema["risk"],
            outside_effect=self.schema["outside_effect"],
            requires_confirmation=self.schema["requires_confirmation"],
            timeout=self.schema["timeout"],
        )

        def _wrapped(tool_input: ToolInput, _ctx: ExecutionContext) -> ToolResult:
            try:
                return ToolResult(ok=True, output=self._handler(tool_input.payload), metadata={"adapter": "hermes"})
            except Exception as e:
                return ToolResult(ok=False, error=str(e), metadata={"adapter": "hermes"})

        return Tool(
            name=self.schema["name"],
            description=self.schema["description"],
            category=self.schema["category"],
            input_schema=self.schema["input_schema"],
            permission=permission,
            handler=_wrapped,
            enabled=self.schema["enabled"],
        )


class HermesSkillAdapter:
    def __init__(self, raw_skill: dict[str, Any]):
        self.raw_skill = raw_skill

    def to_skill(self) -> Skill:
        return Skill(
            name=self.raw_skill.get("name", "unnamed_skill"),
            description=self.raw_skill.get("description", ""),
            tools=tuple(self.raw_skill.get("tools", [])),
            enabled=bool(self.raw_skill.get("enabled", True)),
        )


class HermesComputerUseAdapter:
    ALLOWED_ACTIONS = {
        "screenshot", "browser_navigate", "browser_click", "browser_type", "browser_extract",
        "file_read", "file_write", "shell_command", "wait", "observe",
        "clipboard_get", "clipboard_set", "open", "tap", "type_text", "swipe",
        "ui_dump", "ui_list", "ui_tap", "ui_unlock", "ui_key",
        "credential_access", "credential_list", "credential_screen", "credential_read", "credential_internal",
    }
    BLOCKED_ACTIONS = {"file_delete", "privilege_escalation", "background_persist"}
    OWNER_ONLY_ACTIONS = {
        "credential_access",
        "credential_list",
        "credential_screen",
        "credential_read",
        "credential_internal",
    }

    def validate(self, action: ComputerAction) -> ToolResult:
        from config import is_owner_equivalent_mode

        if action.action in self.OWNER_ONLY_ACTIONS and not is_owner_equivalent_mode():
            return ToolResult(ok=False, error=f"Owner-only action: {action.action}")
        if action.action in self.BLOCKED_ACTIONS:
            return ToolResult(ok=False, error=f"Blocked action: {action.action}")
        if action.action not in self.ALLOWED_ACTIONS:
            return ToolResult(ok=False, error=f"Unknown action: {action.action}")
        if action.permission.timeout <= 0:
            return ToolResult(ok=False, error="Timeout must be positive")
        if action.permission.allowed_scope not in {"local", "workspace", "browser"}:
            return ToolResult(ok=False, error=f"Unsupported scope: {action.permission.allowed_scope}")
        if action.action == "shell_command":
            cmd = str(action.params.get("command", "")).strip()
            if not cmd:
                return ToolResult(ok=False, error="shell_command requires command")
            blocked_fragments = ("sudo ", "rm -rf", "chmod 777", "curl ", "wget ")
            if any(fragment in cmd for fragment in blocked_fragments):
                return ToolResult(ok=False, error="shell_command violates safety policy")
        return ToolResult(ok=True, output={"validated": True, "action": action.action, "scope": action.permission.allowed_scope})

    async def run(self, action: ComputerAction, dry_run: bool = False) -> ToolResult:
        validation = self.validate(action)
        if not validation.ok:
            return validation
        from computer_use import AgentAction, get_computer_use, computer_use_enabled

        if not computer_use_enabled():
            return ToolResult(ok=False, error="Computer-Use ist deaktiviert")
        mapped = self._map_action(action)
        if not mapped:
            return ToolResult(ok=False, error=f"Nicht abbildbare Aktion: {action.action}")
        result = await get_computer_use().execute(mapped, dry_run=dry_run)
        return ToolResult(ok=bool(result.get("ok")), output=result, error=result.get("error", ""))

    @staticmethod
    def _map_action(action: ComputerAction) -> Any:
        from computer_use import AgentAction

        name = (action.action or "").strip().lower()
        params = dict(action.params or {})
        if name == "shell_command":
            return AgentAction("shell", {"command": params.get("command", "")})
        if name == "observe":
            return AgentAction("observe")
        if name == "screenshot":
            return AgentAction("screenshot", {"path": params.get("path", "")})
        if name == "wait":
            return AgentAction("wait", {"seconds": float(params.get("seconds", 1.0))})
        if name == "clipboard_get":
            return AgentAction("clipboard_get")
        if name == "clipboard_set":
            return AgentAction("clipboard_set", {"text": params.get("text", "")})
        if name == "open":
            return AgentAction("open", {"target": params.get("target", "")})
        if name == "tap":
            return AgentAction("tap", {"x": params.get("x", 0), "y": params.get("y", 0)})
        if name == "type_text":
            return AgentAction("type_text", {"text": params.get("text", "")})
        if name == "swipe":
            return AgentAction("swipe", params)
        if name == "ui_dump":
            return AgentAction("ui_dump")
        if name == "ui_list":
            return AgentAction("ui_list")
        if name == "ui_tap":
            return AgentAction("ui_tap", {"text": params.get("text", "")})
        if name == "ui_unlock":
            return AgentAction("ui_unlock")
        if name == "ui_key":
            return AgentAction("ui_key", {"code": params.get("code", "")})
        if name == "credential_list":
            return AgentAction("credential_list")
        if name == "credential_screen":
            return AgentAction("credential_screen", {"site": params.get("site", "")})
        if name == "credential_read":
            return AgentAction(
                "credential_read",
                {"site": params.get("site", ""), "import": bool(params.get("import"))},
            )
        if name == "credential_internal":
            return AgentAction("credential_internal", {"site": params.get("site", "")})
        if name == "credential_access":
            return AgentAction("credential_read", {"site": params.get("site", ""), "import": bool(params.get("import"))})
        return None


class HermesBrowserAdapter:
    def run(self, action: BrowserAction, dry_run: bool = True) -> ToolResult:
        if dry_run:
            return ToolResult(ok=True, output={"dry_run": True, "action": action.action, "url": action.url})
        return ToolResult(ok=False, error="Live browser execution disabled in adapter")


class HermesCompatibilityAdapter:
    def __init__(self, confirmation_policy: ConfirmationPolicy, audit_log=AuditLog):
        self.confirmation_policy = confirmation_policy
        self.audit_log = audit_log
        self.skill_registry = SkillRegistry()
        self.tools: dict[str, Tool] = {}

    def register_skill(self, raw_skill: dict[str, Any]):
        self.skill_registry.register(HermesSkillAdapter(raw_skill).to_skill())

    def register_tool(self, schema: dict[str, Any], handler: Callable[[dict[str, Any]], Any]):
        tool = HermesToolAdapter(schema, handler).to_tool()
        self.tools[tool.name] = tool

    def mirror_tool_to_mcp(self, mcp_registry: MCPRegistry, tool_name: str):
        tool = self.tools[tool_name]
        mcp_registry.register_tool(
            tool.name,
            {"description": tool.description, "inputSchema": tool.input_schema, "category": tool.category},
            handler=lambda **kwargs: self._invoke_mirrored_tool(tool.name, kwargs),
        )

    def _invoke_mirrored_tool(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.execute_tool(tool_name, payload, ExecutionContext(caller="mcp"))
        return ensure_result_contract(result.__dict__, source="hermes_mcp_mirror")

    def _permission_check(self, tool: Tool, ctx: ExecutionContext) -> SecurityVerdict:
        meta = tool.permission.as_policy_meta()
        return self.confirmation_policy.analyze(tool.name, ctx, meta)


    def _validate_payload(self, tool: Tool, payload: dict[str, Any]) -> str:
        schema = tool.input_schema or {}
        required = schema.get("required") or []
        if not isinstance(payload, dict):
            return "invalid_arguments: payload_must_be_object"
        missing = [name for name in required if name not in payload]
        if missing:
            return f"invalid_arguments: missing_required={','.join(missing)}"
        return ""

    def execute_tool(self, tool_name: str, payload: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        tool = self.tools.get(tool_name)
        if not tool or not tool.enabled:
            return ToolResult(**error_result(f"Tool unavailable: {tool_name}", metadata={"tool": tool_name}))

        validation_error = self._validate_payload(tool, payload)
        if validation_error:
            return ToolResult(**error_result(validation_error, metadata={"tool": tool_name}))

        verdict = self._permission_check(tool, ctx)
        if not verdict.allowed:
            self.audit_log.action(ctx.caller, "tool_blocked", f"{tool_name} | {verdict.reason}", level=ctx.level, erfolg=False)
            return ToolResult(**error_result(f"{tool_name}: {verdict.reason}", metadata={"risk": verdict.risk, "tool": tool_name}, queue_id=verdict.queue_id))

        self.audit_log.action(ctx.caller, "tool_execute", tool_name, level=ctx.level, erfolg=True)
        result = tool.handler(ToolInput(payload), ctx)
        normalized = ensure_result_contract(result.__dict__ if isinstance(result, ToolResult) else result, source="hermes_handler")
        normalized["metadata"].setdefault("tool", tool_name)
        if not normalized.get("ok") and tool_name not in normalized.get("error", ""):
            normalized["error"] = f"{tool_name}: {normalized.get('error') or 'tool_execution_failed'}"
        self.audit_log.task(ctx.task_id or "hermes-task", "tool_result", detail=tool_name, score=1.0 if normalized.get("ok") else 0.0)
        return ToolResult(**normalized)

    def execute_flow(self, user_task: str, tool_name: str, payload: dict[str, Any], ctx: ExecutionContext, memory_writeback: Optional[Callable[[str, ToolResult], None]] = None) -> ToolResult:
        self.audit_log.action(ctx.caller, "intent_detected", user_task[:120], level=ctx.level, erfolg=True)
        result = self.execute_tool(tool_name, payload, ctx)
        if result.ok and memory_writeback:
            memory_writeback(user_task, result)
            self.audit_log.memory_write("HermesCompatibilityAdapter", "tool_result", tool_name)
        return result
