from __future__ import annotations

"""Isaac autonomy control plane.

This module composes the existing governance primitives into one explicit
decision pipeline. It never executes an external action.

Pipeline:
intent -> memory -> goal -> permission -> safety -> confirmation -> proposal
-> separate executor -> audit
"""

from dataclasses import dataclass
from typing import Any, Mapping

from audit import AuditLog
from config import Level
from privilege import PrivCtx, get_gate
from result_contract import ensure_result_contract
from security_policy import SecurityVerdict, get_confirmation_policy
from constitution import get_constitution


@dataclass(frozen=True)
class AutonomyDecision:
    status: str
    execution_authorized: bool
    permission: dict[str, Any]
    safety: dict[str, Any]
    confirmation: dict[str, Any]
    proposal: dict[str, Any]
    context: dict[str, Any]


class IsaacAutonomyControlPlane:
    """Single governed decision point before any consequential execution."""

    def evaluate(
        self,
        *,
        action: str,
        reason: str = "",
        risk: str = "normal",
        outside_effect: bool = True,
        destructive: bool = False,
        goal: str = "",
        memory_query: str = "",
        caller: str = "MCP",
        caller_level: int = Level.TASK,
        trusted_internal: bool = False,
    ) -> dict[str, Any]:
        action = (action or "").strip()
        reason = (reason or "").strip()
        risk = (risk or "normal").strip().lower()
        if not action:
            return ensure_result_contract(
                {"ok": False, "error": "action fehlt"},
                source="isaac_control_plane",
            )

        # Remote callers cannot elevate their governance level.
        effective_level = int(caller_level)
        if not trusted_internal:
            effective_level = min(effective_level, int(Level.TASK))

        context: dict[str, Any] = {
            "goal": self._goal_context(goal),
            "memory": self._memory_context(memory_query),
        }

        permission_action = self._permission_action(action)
        permission_ctx = PrivCtx(
            caller=caller or "MCP",
            level=effective_level,
            r_trace=f"Autonomy evaluation: {action}",
        )
        allowed, permission_reason = get_gate().authorize(
            permission_action, permission_ctx
        )
        permission = {
            "allowed": bool(allowed),
            "reason": permission_reason,
            "action": permission_action,
            "caller_level": effective_level,
        }

        metadata = {
            "outside_effect": bool(outside_effect),
            "audit_logged": True,
            "risk": risk,
            "destructive": bool(destructive),
            "owner_approved": effective_level >= int(Level.STEFFEN),
        }
        constitution_verdict = get_constitution().validate_action(action, metadata)
        safety = {
            "allowed": bool(constitution_verdict.get("allowed")),
            "warnings": constitution_verdict.get("warnings", []),
            "blocked_by": constitution_verdict.get("blocked_by", []),
            "action": action,
            "metadata": metadata,
        }

        confirmation = self._confirmation(
            action=action,
            caller=caller,
            caller_level=effective_level,
            risk=risk,
            outside_effect=outside_effect,
            destructive=destructive,
            permission_allowed=bool(allowed),
            safety_allowed=bool(safety["allowed"]),
        )

        if not allowed:
            status = "blocked_permission"
        elif not safety["allowed"]:
            status = "blocked_safety"
        elif confirmation["requires_confirmation"]:
            status = "pending_confirmation"
        else:
            status = "ready_for_executor"

        # This object is only a proposal. Execution is deliberately false here.
        proposal = {
            "action": action,
            "reason": reason[:500],
            "risk": risk,
            "outside_effect": bool(outside_effect),
            "destructive": bool(destructive),
            "status": status,
            "executed": False,
            "execution_authorized": False,
            "requires_executor": True,
        }

        AuditLog.action(
            "ISAAC",
            "autonomy_evaluated",
            f"action={action[:120]} status={status} caller={str(caller)[:80]}",
            level=effective_level,
            erfolg=status in {"ready_for_executor", "pending_confirmation"},
        )

        return ensure_result_contract(
            {
                "ok": True,
                "output": {
                    "decision": AutonomyDecision(
                        status=status,
                        execution_authorized=False,
                        permission=permission,
                        safety=safety,
                        confirmation=confirmation,
                        proposal=proposal,
                        context=context,
                    ).__dict__
                },
            },
            source="isaac_control_plane",
        )

    @staticmethod
    def _permission_action(action: str) -> str:
        """Map high-level intent names to Isaac's privilege vocabulary."""
        mapping = {
            "browser": "browser_navigate",
            "browser_action": "browser_navigate",
            "web_search": "internet_search",
            "search": "internet_search",
            "memory_write": "write_memory",
            "goal_update": "write_memory",
            "file_write": "file_write",
            "file_delete": "file_delete",
            "system_command": "system_command",
            "execute_code": "execute_code",
        }
        return mapping.get(action, "chat_response")

    @staticmethod
    def _goal_context(goal: str) -> dict[str, Any]:
        if not goal:
            return {"requested": False}
        try:
            from goal_store import get_goal_store

            item = get_goal_store().find_goal(goal)
            if not item:
                return {"requested": True, "found": False}
            return {
                "requested": True,
                "found": True,
                "goal": item.to_dict(),
            }
        except Exception as exc:
            return {
                "requested": True,
                "found": False,
                "degraded": True,
                "error": type(exc).__name__,
            }

    @staticmethod
    def _memory_context(query: str) -> dict[str, Any]:
        if not query:
            return {"requested": False}
        try:
            from memory import get_memory

            ctx = get_memory().build_retrieval_context(query, n_history=6)
            return {"requested": True, "context": ctx.as_dict()}
        except Exception as exc:
            return {
                "requested": True,
                "degraded": True,
                "error": type(exc).__name__,
            }

    @staticmethod
    def _confirmation(
        *,
        action: str,
        caller: str,
        caller_level: int,
        risk: str,
        outside_effect: bool,
        destructive: bool,
        permission_allowed: bool,
        safety_allowed: bool,
    ) -> dict[str, Any]:
        if not permission_allowed or not safety_allowed:
            return {
                "requires_confirmation": False,
                "status": "not_reached",
                "queue_id": "",
                "reason": "",
            }

        ctx = PrivCtx(
            caller=caller or "MCP",
            level=caller_level,
            r_trace=f"Autonomy evaluation: {action}",
        )
        verdict: SecurityVerdict = get_confirmation_policy().analyze(
            action,
            ctx,
            {
                "risk": risk,
                "outside_effect": bool(outside_effect),
                "destructive": bool(destructive),
                "privilege_escalation": False,
            },
        )
        return {
            "requires_confirmation": bool(verdict.requires_confirmation),
            "status": "pending" if verdict.requires_confirmation else "not_required",
            "queue_id": verdict.queue_id,
            "reason": verdict.reason,
            "risk": verdict.risk,
        }


_control_plane: IsaacAutonomyControlPlane | None = None


def get_autonomy_control_plane() -> IsaacAutonomyControlPlane:
    global _control_plane
    if _control_plane is None:
        _control_plane = IsaacAutonomyControlPlane()
    return _control_plane
