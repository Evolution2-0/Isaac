from __future__ import annotations

"""Isaac MCP service boundary.

This module is the security/transport policy layer around Isaac's existing
MCPRegistry. It deliberately does not become a second router or executor.

Design:
- MCP is an infrastructure interface; Isaac governance remains authoritative.
- Remote HTTP callers are authenticated when ISAAC_MCP_API_KEY is configured.
- Without a configured key, HTTP MCP is loopback-only.
- Remote MCP callers run at TASK privilege, never STEFFEN/ISAAC.
- Owner override fields are never accepted from remote MCP callers.
- Mutating MCP tools require ISAAC_MCP_ALLOW_WRITE=1.
- Requests are size-limited and audited without logging arguments/secrets.
- Tool allowlisting can be narrowed with ISAAC_MCP_ALLOWED_TOOLS.
"""

import hmac
import ipaddress
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping

from audit import AuditLog
from config import Level
from mcp_registry import MCPRegistry, get_mcp_registry
from result_contract import ensure_result_contract


DEFAULT_MAX_BODY_BYTES = 256 * 1024
DEFAULT_MAX_ARGUMENT_BYTES = 64 * 1024
DEFAULT_RATE_LIMIT = 60
DEFAULT_RATE_WINDOW_SECONDS = 60.0


@dataclass(frozen=True)
class MCPToolPolicy:
    """External MCP exposure policy for one registered tool."""

    scope: str = "read"
    description: str = ""
    owner_only: bool = False


# Existing tools plus the governance-facing Isaac surface.
# Unknown tools remain read-scoped only if explicitly allowlisted.
TOOL_POLICIES: dict[str, MCPToolPolicy] = {
    "isaac.task_status": MCPToolPolicy("read"),
    "isaac.audit_recent": MCPToolPolicy("read"),
    "isaac.query_memory": MCPToolPolicy("read"),
    "isaac.start_task": MCPToolPolicy("write"),
    "isaac.search_web": MCPToolPolicy("read"),
    "isaac.run_browser_action": MCPToolPolicy("write"),
    "isaac.goal_list": MCPToolPolicy("read"),
    "isaac.goal_get": MCPToolPolicy("read"),
    "isaac.goal_update": MCPToolPolicy("write"),
    "isaac.permission_check": MCPToolPolicy("read"),
    "isaac.safety_check": MCPToolPolicy("read"),
    "isaac.action_request": MCPToolPolicy("read"),
    "isaac.autonomy_evaluate": MCPToolPolicy("read"),
    "isaac.notification_send": MCPToolPolicy("write"),
}


class _RateLimiter:
    """Small process-local fixed-window limiter.

    It is intentionally not presented as a distributed security boundary.
    Deployments with multiple workers should put a real gateway/rate limiter
    in front of the service.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, int]] = {}

    def allow(self, key: str, limit: int, window: float) -> bool:
        now = time.monotonic()
        with self._lock:
            start, count = self._buckets.get(key, (now, 0))
            if now - start >= window:
                start, count = now, 0
            if count >= limit:
                self._buckets[key] = (start, count)
                return False
            self._buckets[key] = (start, count + 1)
            if len(self._buckets) > 4096:
                cutoff = now - window
                self._buckets = {
                    k: v for k, v in self._buckets.items() if v[0] >= cutoff
                }
            return True


class IsaacMCPService:
    """Secure caller boundary for Isaac's MCP registry."""

    def __init__(self, registry: MCPRegistry | None = None) -> None:
        self.registry = registry or get_mcp_registry()
        self._limiter = _RateLimiter()

    @staticmethod
    def api_key() -> str:
        return os.getenv("ISAAC_MCP_API_KEY", "").strip()

    @staticmethod
    def allowed_tools() -> set[str] | None:
        raw = os.getenv("ISAAC_MCP_ALLOWED_TOOLS", "").strip()
        if not raw:
            return None
        return {item.strip() for item in raw.split(",") if item.strip()}

    @staticmethod
    def allow_write() -> bool:
        return os.getenv("ISAAC_MCP_ALLOW_WRITE", "0").strip().lower() in {
            "1", "true", "yes", "on"
        }

    @staticmethod
    def max_body_bytes() -> int:
        return max(4096, int(os.getenv("ISAAC_MCP_MAX_BODY_BYTES", DEFAULT_MAX_BODY_BYTES)))

    @staticmethod
    def max_argument_bytes() -> int:
        return max(1024, int(os.getenv("ISAAC_MCP_MAX_ARGUMENT_BYTES", DEFAULT_MAX_ARGUMENT_BYTES)))

    @staticmethod
    def rate_limit() -> int:
        return max(1, int(os.getenv("ISAAC_MCP_RATE_LIMIT", DEFAULT_RATE_LIMIT)))

    @staticmethod
    def _is_loopback(remote_addr: str | None) -> bool:
        try:
            return ipaddress.ip_address((remote_addr or "").strip()).is_loopback
        except ValueError:
            return False

    def authenticate_http(
        self,
        headers: Mapping[str, str] | None,
        remote_addr: str | None,
    ) -> tuple[bool, str]:
        """Authenticate an HTTP MCP caller.

        A configured API key is mandatory for non-loopback callers. With no
        configured key, only loopback callers are accepted.
        """

        headers = headers or {}
        configured = self.api_key()
        if not configured:
            if self._is_loopback(remote_addr):
                return True, "loopback"
            return False, "ISAAC_MCP_API_KEY is not configured"

        auth = str(headers.get("Authorization") or "").strip()
        prefix = "Bearer "
        supplied = auth[len(prefix):].strip() if auth.startswith(prefix) else ""
        if supplied and hmac.compare_digest(supplied, configured):
            return True, "bearer"
        return False, "invalid MCP credentials"

    def authorize_request(
        self,
        *,
        caller: str = "mcp",
        caller_level: int = Level.TASK,
        tool_name: str,
        arguments: Mapping[str, Any] | None,
        trusted_internal: bool = False,
    ) -> tuple[bool, str]:
        """Apply exposure policy before Isaac's own registry gates."""

        allowed = self.allowed_tools()
        if allowed is not None and tool_name not in allowed:
            return False, "MCP tool is not allowlisted"

        policy = TOOL_POLICIES.get(tool_name)
        if policy is None:
            return False, "MCP tool is not exposed by Isaac policy"

        if policy.owner_only and caller_level < Level.STEFFEN:
            return False, "MCP tool requires owner level"

        if policy.scope == "write" and not trusted_internal and not self.allow_write():
            return False, "MCP write tools are disabled (set ISAAC_MCP_ALLOW_WRITE=1)"

        args = dict(arguments or {})
        if not trusted_internal and (
            "owner_override" in args or "override_reason" in args
        ):
            return False, "Owner override is not accepted from remote MCP callers"

        if caller_level > Level.TASK and not trusted_internal:
            return False, "Remote MCP callers cannot claim elevated privilege"

        if not self._limiter.allow(
            caller,
            self.rate_limit(),
            DEFAULT_RATE_WINDOW_SECONDS,
        ):
            return False, "MCP rate limit exceeded"

        return True, "OK"

    def invoke(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        caller: str = "mcp",
        caller_level: int = Level.TASK,
        trusted_internal: bool = False,
    ) -> dict[str, Any]:
        args = dict(arguments or {})
        raw_size = len(repr(args).encode("utf-8", errors="replace"))
        if raw_size > self.max_argument_bytes():
            return self._deny(tool_name, "MCP arguments exceed configured size limit")

        ok, reason = self.authorize_request(
            caller=caller,
            caller_level=caller_level,
            tool_name=tool_name,
            arguments=args,
            trusted_internal=trusted_internal,
        )
        if not ok:
            return self._deny(tool_name, reason)

        # Never permit an external caller to smuggle an owner context into the
        # lower-level registry. Internal callers retain the existing behavior.
        if not trusted_internal:
            args.pop("owner_override", None)
            args.pop("override_reason", None)

        AuditLog.action(
            "MCP",
            "tool_invoke",
            f"tool={tool_name[:120]} caller={caller[:80]} scope={TOOL_POLICIES[tool_name].scope}",
            level=int(caller_level),
            erfolg=True,
        )
        try:
            result = self.registry.invoke_tool(
                tool_name,
                args,
                caller=caller,
                caller_level=caller_level,
                allow_owner_override=trusted_internal,
            )
        except TypeError:
            # Backward-compatible fallback for third-party/custom registries
            # implementing the old two-argument invoke_tool contract.
            result = self.registry.invoke_tool(tool_name, args)
        except Exception as exc:
            AuditLog.error("MCP", "tool_invoke_failed", type(exc).__name__)
            return ensure_result_contract(
                {"ok": False, "error": "MCP tool execution failed"},
                source=f"isaac_mcp:{tool_name}",
            )

        if not result.get("ok"):
            AuditLog.action(
                "MCP",
                "tool_denied_or_failed",
                f"tool={tool_name[:120]} caller={caller[:80]}",
                level=int(caller_level),
                erfolg=False,
            )
        return ensure_result_contract(result, source=f"isaac_mcp:{tool_name}")

    def list_tools(
        self,
        *,
        caller_level: int = Level.TASK,
        trusted_internal: bool = False,
    ) -> list[dict[str, Any]]:
        allowed = self.allowed_tools()
        result: list[dict[str, Any]] = []
        for item in self.registry.tools():
            name = str(item.get("name") or "")
            policy = TOOL_POLICIES.get(name)
            if not policy:
                continue
            if allowed is not None and name not in allowed:
                continue
            if policy.owner_only and caller_level < Level.STEFFEN:
                continue
            if policy.scope == "write" and not trusted_internal and not self.allow_write():
                continue
            row = dict(item)
            row["isaacScope"] = policy.scope
            row["isaacGoverned"] = True
            result.append(row)
        return result

    def capabilities(self, *, caller_level: int = Level.TASK) -> dict[str, Any]:
        tools = self.list_tools(caller_level=caller_level)
        return {
            "name": "isaac",
            "version": os.getenv("ISAAC_MCP_VERSION", "1.0.0"),
            "governance": "isaac",
            "caller_level": int(caller_level),
            "write_enabled": self.allow_write(),
            "authentication": "bearer" if self.api_key() else "loopback-only",
            "tools": [tool["name"] for tool in tools],
            "tool_count": len(tools),
            "protocol": "MCP-over-JSON-RPC-2.0",
        }

    def _deny(self, tool_name: str, reason: str) -> dict[str, Any]:
        AuditLog.action(
            "MCP",
            "tool_blocked",
            f"tool={tool_name[:120]} reason={reason[:160]}",
            erfolg=False,
        )
        return ensure_result_contract(
            {
                "ok": False,
                "error": reason,
                "metadata": {"tool": tool_name, "governed": True},
            },
            source=f"isaac_mcp:policy:{tool_name}",
        )


_service: IsaacMCPService | None = None
_service_registry: MCPRegistry | None = None


def get_isaac_mcp_service(registry: MCPRegistry | None = None) -> IsaacMCPService:
    global _service, _service_registry
    selected = registry or get_mcp_registry()
    if _service is None or _service_registry is not selected:
        _service = IsaacMCPService(selected)
        _service_registry = selected
    return _service
