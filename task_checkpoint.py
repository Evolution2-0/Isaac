from __future__ import annotations

"""Isaac – Task Checkpoint States
Zustandsmaschine für Task-Checkpointing und Resume.

Transition-Regeln sind *soft*: Helfen bei Diagnose und Evals, blockieren
den Executor nicht (kein zweites Parallel-State-Machine-Modul).
"""

from typing import Any


CHECKPOINT_MAX_PER_TASK = 25
CHECKPOINT_MAX_AGE_DAYS = 30
CHECKPOINT_GLOBAL_MAX = 5000
TERMINAL_CHECKPOINT_STATES = frozenset({
    "done",
    "failed",
    "learning_commit",
})


class CheckpointState:
    PLANNING = "planning"
    TOOL_PENDING = "tool_pending"
    EVALUATING = "evaluating"
    LEARNING_COMMIT = "learning_commit"
    DONE = "done"
    FAILED = "failed"

    # Legacy alias
    TOOL_RUNNING = "tool_running"

    ALL = frozenset({
        PLANNING,
        TOOL_PENDING,
        TOOL_RUNNING,
        EVALUATING,
        LEARNING_COMMIT,
        DONE,
        FAILED,
    })

    RESUMABLE = frozenset({
        PLANNING,
        TOOL_PENDING,
        TOOL_RUNNING,
        EVALUATING,
        LEARNING_COMMIT,
    })


# Bevorzugte Pipeline (Happy Path) — dokumentiert, für Soft-Checks/Evals.
PREFERRED_TRANSITIONS: dict[str, frozenset[str]] = {
    CheckpointState.PLANNING: frozenset({
        CheckpointState.TOOL_PENDING,
        CheckpointState.EVALUATING,  # Chat ohne Tools
        CheckpointState.LEARNING_COMMIT,  # lightweight local answer
        CheckpointState.FAILED,
    }),
    CheckpointState.TOOL_PENDING: frozenset({
        CheckpointState.EVALUATING,
        CheckpointState.LEARNING_COMMIT,
        CheckpointState.FAILED,
    }),
    CheckpointState.EVALUATING: frozenset({
        CheckpointState.LEARNING_COMMIT,
        CheckpointState.TOOL_PENDING,  # Follow-up / Tool-Nachzug
        CheckpointState.DONE,  # short path when learning skipped
        CheckpointState.FAILED,
    }),
    CheckpointState.LEARNING_COMMIT: frozenset({
        CheckpointState.DONE,
        CheckpointState.FAILED,
    }),
    CheckpointState.DONE: frozenset(),
    CheckpointState.FAILED: frozenset(),
}

# Zusätzliche Übergänge, die der Executor real nutzt (Resume, Re-Checkpoint).
# Soft-valid: kein Hard-Fail im Runtime-Pfad.
ALLOWED_SOFT_TRANSITIONS: dict[str, frozenset[str]] = {
    CheckpointState.PLANNING: frozenset({
        CheckpointState.PLANNING,
        CheckpointState.TOOL_PENDING,
        CheckpointState.EVALUATING,
        CheckpointState.LEARNING_COMMIT,
        CheckpointState.DONE,
        CheckpointState.FAILED,
    }),
    CheckpointState.TOOL_PENDING: frozenset({
        CheckpointState.PLANNING,
        CheckpointState.TOOL_PENDING,
        CheckpointState.EVALUATING,
        CheckpointState.LEARNING_COMMIT,
        CheckpointState.DONE,
        CheckpointState.FAILED,
    }),
    CheckpointState.EVALUATING: frozenset({
        CheckpointState.PLANNING,
        CheckpointState.TOOL_PENDING,
        CheckpointState.EVALUATING,
        CheckpointState.LEARNING_COMMIT,
        CheckpointState.DONE,
        CheckpointState.FAILED,
    }),
    CheckpointState.LEARNING_COMMIT: frozenset({
        CheckpointState.LEARNING_COMMIT,
        CheckpointState.EVALUATING,
        CheckpointState.DONE,
        CheckpointState.FAILED,
    }),
    CheckpointState.DONE: frozenset({
        CheckpointState.DONE,
        CheckpointState.FAILED,
        CheckpointState.PLANNING,  # neuer Zyklus / Requeue
    }),
    CheckpointState.FAILED: frozenset({
        CheckpointState.FAILED,
        CheckpointState.PLANNING,
        CheckpointState.TOOL_PENDING,
        CheckpointState.EVALUATING,
        CheckpointState.LEARNING_COMMIT,
    }),
}


def is_resumable_state(state_name: str) -> bool:
    return (state_name or "") in CheckpointState.RESUMABLE


def normalize_state(state_name: str) -> str:
    name = (state_name or "").strip()
    if name == CheckpointState.TOOL_RUNNING:
        return CheckpointState.TOOL_PENDING
    # Resume-Marker im Executor sind Meta-Zustände, keine Pipeline-Steps.
    if name in {"resume_requested", "resume_completed"}:
        return CheckpointState.PLANNING
    return name


def is_preferred_transition(old_state: str, new_state: str) -> bool:
    """True wenn Übergang dem Happy-Path-Graph entspricht."""
    old_n = normalize_state(old_state)
    new_n = normalize_state(new_state)
    if not old_n:
        return new_n in CheckpointState.ALL or new_n == CheckpointState.PLANNING
    if old_n == new_n:
        return True
    allowed = PREFERRED_TRANSITIONS.get(old_n)
    if allowed is None:
        return False
    return new_n in allowed


def is_valid_transition(old_state: str, new_state: str, *, strict: bool = False) -> bool:
    """
    Soft-State-Machine-Check.

    strict=False (Default): erlaubte Soft-Übergänge inkl. Executor-Realität.
    strict=True: nur Preferred Happy-Path.
    Unbekannte Zustände → False.
    """
    old_n = normalize_state(old_state)
    new_n = normalize_state(new_state)
    if not new_n or new_n not in CheckpointState.ALL:
        return False
    if not old_n:
        return True  # erster Checkpoint
    if strict:
        return is_preferred_transition(old_n, new_n)
    allowed = ALLOWED_SOFT_TRANSITIONS.get(old_n)
    if allowed is None:
        return False
    return new_n in allowed


def transition_note(old_state: str, new_state: str) -> str:
    """Kurzer Diagnose-String für Logs/Evals."""
    old_n = normalize_state(old_state) or "(start)"
    new_n = normalize_state(new_state) or "(empty)"
    if is_preferred_transition(old_state, new_state):
        return f"preferred:{old_n}->{new_n}"
    if is_valid_transition(old_state, new_state, strict=False):
        return f"soft_ok:{old_n}->{new_n}"
    return f"invalid:{old_n}->{new_n}"


def build_input_snapshot(task: Any, *, current_prompt: str = "") -> dict[str, Any]:
    return {
        "task_id": task.id,
        "typ": task.typ.value,
        "prompt": task.prompt,
        "current_prompt": current_prompt or task.prompt,
        "beschreibung": task.beschreibung,
        "provider": task.provider,
        "provider_used": task.provider_used,
        "iteration": task.iteration,
        "status": task.status.value,
        "sudo": task.sudo_aktiv,
        "used_tools": list(getattr(task, "used_tools", [])[-8:]),
        "interaction_class": getattr(task, "interaction_class", ""),
    }


def build_result_snapshot(
    *,
    antwort: str = "",
    provider: str = "",
    score_total: float | None = None,
    partial: bool | None = None,
    via: str = "",
    resume_reason: str = "",
) -> dict[str, Any]:
    preview = (antwort or "")[:400]
    return {
        "answer_preview": preview,
        "answer_full": antwort or "",
        "provider": provider,
        "score_total": score_total,
        "partial": partial,
        "via": via,
        "resume_reason": resume_reason,
    }