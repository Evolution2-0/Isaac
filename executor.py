"""
Isaac – Executor v2.0
=======================
Task-Engine. Vollständig async. Keine blockierenden Calls.

Korrekturen gegenüber v1:
  - asyncio.create_subprocess_exec statt subprocess.run (Event Loop safe)
  - Task-Persistenz in SQLite (überleben Neustarts)
  - Watchdog-Integration (Hang-Detection, Neustart)
  - Dispatcher-Integration (Multi-KI-Verteilung)
  - Pre-Flight-Validation vor Ausführung
  - Korrekte Callback-Sicherheit (asyncio.create_task nur im Event Loop)
"""
from __future__ import annotations

import asyncio
import time
import re
import uuid
import json
import logging
import ast
import os
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, Any

from config    import get_config, WORKSPACE, Level
from privilege import get_gate, isaac_ctx, task_ctx
from audit     import AuditLog
from logic     import get_logic, QualityScore, FollowUpDecision
from relay     import get_relay
from tool_runtime import select_live_tool_for_task, run_selected_tool, constitution_gate_for_tool
from memory import get_memory
from task_checkpoint import (
    CheckpointState,
    build_input_snapshot,
    build_result_snapshot,
    is_resumable_state,
    is_valid_transition,
    normalize_state,
    transition_note,
)
from task_tool_state import get_task_tool_state_store
from low_complexity import ClassificationResult
from decision_trace import (
    DecisionTrace,
    TracePhase,
    build_execution_llm_trace_data,
    maybe_export_portable_trace,
)
from result_contract import ensure_result_contract
from tool_policy import (
    ToolPolicy,
    ToolDecisionReason,
    ToolEligibilityDecision,
    evaluate_tool_eligibility,
)

log = logging.getLogger("Isaac.Executor")

_ALLOWED_IMPORTS = {"math", "json", "random", "statistics", "collections", "itertools", "functools", "datetime"}
_FORBIDDEN_NAMES = {"eval", "exec", "compile", "open", "input", "breakpoint", "help", "__import__"}
_FORBIDDEN_ATTRS = {"system", "popen", "spawn", "remove", "unlink", "rmtree", "kill", "fork", "chmod", "chown"}


def _normalize_code(raw: str) -> str:
    raw = re.sub(r"```python\n?", "", raw)
    raw = re.sub(r"```\n?", "", raw)
    return raw.strip()


def _fingerprint(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())[:800]


def _limit_subprocess_resources():
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1_000_000, 1_000_000))
        resource.setrlimit(resource.RLIMIT_NOFILE, (16, 16))
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
    except Exception:
        pass


def _validate_generated_code(code: str) -> tuple[bool, str]:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"Syntaxfehler im generierten Code: {e.msg}"

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name.split(".", 1)[0] for alias in node.names]
            if any(name not in _ALLOWED_IMPORTS for name in names):
                return False, f"Import nicht erlaubt: {', '.join(names)}"
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_NAMES:
                return False, f"Aufruf nicht erlaubt: {node.func.id}()"
            if isinstance(node.func, ast.Attribute) and node.func.attr in _FORBIDDEN_ATTRS:
                return False, f"Attribut nicht erlaubt: .{node.func.attr}"
        elif isinstance(node, ast.Attribute) and node.attr.startswith('__'):
            return False, "Dunder-Zugriff nicht erlaubt"
    return True, ""


class TaskStatus(Enum):
    QUEUED     = "queued"
    RUNNING    = "running"
    EVALUATING = "evaluating"
    FOLLOWUP   = "followup"
    RESUMABLE  = "resumable"
    BLOCKED    = "blocked"
    DONE       = "done"
    FAILED     = "failed"
    CANCELLED  = "cancelled"


class TaskType(Enum):
    CHAT       = "chat"
    SEARCH     = "search"
    RESEARCH   = "research"
    ANALYSIS   = "analysis"
    TRANSLATE  = "translate"
    CODE       = "code"
    PLAN       = "plan"
    FILE       = "file"
    AGGREGATE  = "aggregate"
    BROADCAST  = "broadcast"   # Multi-KI
    SPLIT      = "split"       # Multi-KI aufgeteilt
    PIPELINE   = "pipeline"    # Multi-KI iterativ


@dataclass(frozen=True)
class Strategy:
    allow_tools: bool = True
    allow_followup: bool = True
    allow_provider_switch: bool = True
    # Companion agents (Grok/OI/Letta) — opt-in via strategy + ISAAC_AGENT_AUTO_SELECT
    allow_agent_companions: bool = False
    preferred_agent: str = ""  # "grok" | "copilot" | "open_interpreter" | "letta" | ""
    style_note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "allow_tools": self.allow_tools,
            "allow_followup": self.allow_followup,
            "allow_provider_switch": self.allow_provider_switch,
            "allow_agent_companions": self.allow_agent_companions,
            "preferred_agent": self.preferred_agent,
            "style_note": self.style_note,
        }


@dataclass
class Task:
    id:            str
    typ:           TaskType
    prompt:        str
    beschreibung:  str
    prioritaet:    float = 5.0
    provider:      Optional[str] = None
    parent_id:     Optional[str] = None
    system_prompt: str           = ""
    sudo_aktiv:    bool          = False   # SUDO-Modus

    status:        TaskStatus = TaskStatus.QUEUED
    iteration:     int        = 0
    erstellt:      str        = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
    gestartet:     str        = ""
    abgeschlossen: str        = ""
    antwort:       str        = ""
    provider_used: str        = ""
    score:         Optional[QualityScore] = None
    followup:      Optional[FollowUpDecision] = None
    fehler:        str        = ""
    sub_task_ids:  list       = field(default_factory=list)
    dauer_sek:     float      = 0.0
    progress:      float      = 0.0
    log_entries:   list       = field(default_factory=list)
    used_tools:    list       = field(default_factory=list)
    tool_strategy: dict       = field(default_factory=dict)
    strategy:      Strategy   = field(default_factory=Strategy)
    tool_policy:   ToolPolicy = field(default_factory=ToolPolicy)
    tool_decision_reason: str = ""
    tool_selection_reason: str = ""
    interaction_class: str    = ""
    classification: Optional[ClassificationResult] = None
    retrieved_context: dict   = field(default_factory=dict)
    decision_trace: DecisionTrace = field(default_factory=DecisionTrace)

    # Watchdog
    _last_watchdog_progress: float = 0.0

    # Checkpoint / Resume
    checkpoint_state: str = ""
    checkpoint_ts: str = ""
    resume_checkpoint_id: int = 0
    resume_strategy: str = ""
    resume_current_prompt: str = ""
    resume_used_tool_ids: list = field(default_factory=list)
    resume_tool_context: str = ""
    resume_start_iteration: int = 0
    watchdog_resume_pending: bool = False

    # TaskGraph (Pillar 2 - Prove the Kernel)
    dependencies: list = field(default_factory=list)
    goal_id: Optional[str] = None
    subgoal_ids: list = field(default_factory=list)
    required_capabilities: list = field(default_factory=list)
    risk_level: str = "low"
    actions: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    decision: dict = field(default_factory=dict)
    outcome: dict = field(default_factory=dict)
    learned_from: list = field(default_factory=list)

    @property
    def task_id(self) -> str:
        return self.id

    def record_action(self, action_type: str, details: Optional[dict] = None) -> dict:
        entry = {
            "ts": time.time(),
            "time": time.strftime("%H:%M:%S"),
            "action_type": action_type,
            "details": details or {},
        }
        self.actions.append(entry)
        return entry

    def record_evidence(self, source: str, content: Any, confidence: float = 1.0) -> dict:
        entry = {
            "ts": time.time(),
            "time": time.strftime("%H:%M:%S"),
            "source": source,
            "content": content,
            "confidence": confidence,
        }
        self.evidence.append(entry)
        return entry

    def record_decision(self, summary: str, details: Optional[dict] = None):
        self.decision = {
            "ts": time.time(),
            "summary": summary,
            "details": details or {},
        }

    def record_outcome(self, status: str, result_summary: str, details: Optional[dict] = None):
        self.outcome = {
            "ts": time.time(),
            "status": status,
            "summary": result_summary,
            "details": details or {},
        }

    def to_graph_node(self) -> dict:
        return {
            "task_id": self.id,
            "parent_id": self.parent_id,
            "dependencies": list(self.dependencies),
            "goal": self.goal_id,
            "subgoals": list(self.subgoal_ids),
            "classification": self.classification.as_dict() if self.classification else None,
            "strategy": self.strategy.as_dict(),
            "required_capabilities": list(self.required_capabilities),
            "risk_level": self.risk_level,
            "actions": list(self.actions),
            "evidence": list(self.evidence),
            "decision": dict(self.decision),
            "outcome": dict(self.outcome),
            "errors": [self.fehler] if self.fehler else [],
            "learned_from": list(self.learned_from),
            "decision_trace": self.decision_trace.to_list(),
            "timestamps": {
                "created_at": self.erstellt,
                "started_at": self.gestartet,
                "completed_at": self.abgeschlossen,
            },
            "status": self.status.value,
        }

    def log(self, msg: str):
        self.log_entries.append({"ts": time.strftime("%H:%M:%S"), "msg": msg})
        if len(self.log_entries) > 50:
            self.log_entries = self.log_entries[-50:]

    def execution_query(self) -> str:
        """Owner-facing query for search/tools — never the retrieval mashup.

        `_standard_task` stores retrieval context + user text in `prompt` and the
        raw owner line in `beschreibung`. MultiSearch/tool selection must use the
        owner line only; otherwise markers like procedure `weather` false-trigger
        Open-Meteo and search runs against `[active_directives]…`.
        """
        prompt = (self.prompt or "").strip()
        desc = (self.beschreibung or "").strip()
        if desc.lower().startswith("retry:"):
            desc = desc.split(":", 1)[1].strip()
        # Drop ensemble/sub prefixes for tool routing
        for prefix in ("ensemble[", "[sub]"):
            if desc.lower().startswith(prefix):
                # ensemble[x]:body or [Sub] body
                if ":" in desc:
                    desc = desc.split(":", 1)[1].strip()
                break

        retrievalish = bool(
            prompt.startswith("[")
            or prompt.startswith("[active_directives]")
            or "[active_directives]" in prompt[:120]
            or "[conversation_history]" in prompt[:400]
            or "[relevant_procedures]" in prompt
            or "[relevant_facts]" in prompt[:200]
        )
        if retrievalish and "\n\n" in prompt:
            tail = prompt.rsplit("\n\n", 1)[-1].strip()
            if tail and not tail.startswith("["):
                return tail
        if desc and not desc.startswith("["):
            return desc
        if retrievalish:
            return desc or prompt
        return prompt or desc

    def to_dict(self) -> dict:
        return {
            "id":            self.id,
            "typ":           self.typ.value,
            "beschreibung":  self.beschreibung,
            "prioritaet":    self.prioritaet,
            "status":        self.status.value,
            "iteration":     self.iteration,
            "erstellt":      self.erstellt,
            "gestartet":     self.gestartet,
            "abgeschlossen": self.abgeschlossen,
            "provider":      self.provider_used or self.provider or "auto",
            "score":         round(self.score.total, 2) if self.score else None,
            "score_detail":  self.score.as_dict() if self.score else None,
            "followup_mode": self.followup.mode if self.followup else None,
            "parent_id":     self.parent_id,
            "sub_tasks":     len(self.sub_task_ids),
            "dauer":         round(self.dauer_sek, 2),
            "progress":      round(self.progress, 3),
            "antwort_kurz":  self.antwort[:200] if self.antwort else "",
            # Full answer for monitor recovery (capped to keep WS payloads bounded)
            "antwort":       (self.antwort or "")[:12000],
            "fehler":        self.fehler[:100] if self.fehler else "",
            "log":           self.log_entries[-10:],
            "used_tools":    self.used_tools[-8:],
            "tool_strategy": self.tool_strategy or {},
            "strategy":      self.strategy.as_dict(),
            "tool_policy":   self.tool_policy.as_dict(),
            "tool_decision_reason": self.tool_decision_reason,
            "tool_selection_reason": self.tool_selection_reason,
            "classification": self.classification.as_dict() if self.classification else None,
            "decision_trace": self.decision_trace.to_list(),
            "sudo":          self.sudo_aktiv,
            "checkpoint_state": self.checkpoint_state,
            "checkpoint_ts": self.checkpoint_ts,
            "resume_checkpoint_id": self.resume_checkpoint_id,
            "resume_strategy": self.resume_strategy,
            "task_graph": self.to_graph_node(),
        }

    @property
    def allow_tools(self) -> bool:
        return self.strategy.allow_tools

    @property
    def allow_followup(self) -> bool:
        return self.strategy.allow_followup

    @property
    def allow_provider_switch(self) -> bool:
        return self.strategy.allow_provider_switch

    @property
    def current_interaction_class(self) -> str:
        if self.classification:
            return self.classification.interaction_class
        return self.interaction_class


class Executor:
    # Local backends that often appear "available" in config but are offline
    # in cloud/dev environments — never prefer them for quality follow-up.
    _LOCAL_FALLBACK_SKIP = frozenset({
        "ollama", "local", "local_ollama", "lmstudio", "llama_cpp", "vllm",
    })

    def __init__(self):
        self._tasks:    dict[str, Task]          = {}
        self._queue_obj: Optional[asyncio.PriorityQueue] = None
        self._queue_loop: Optional[asyncio.AbstractEventLoop] = None
        self._running:  set[str]                 = set()
        self._callbacks: list[Callable]          = []
        self._loop:     Optional[asyncio.AbstractEventLoop] = None
        self.logic      = get_logic()
        self.relay      = get_relay()
        self.gate       = get_gate()
        self._watchdog  = None   # lazy
        self._dispatcher= None   # lazy
        self._search    = None   # lazy
        self._tool_state = get_task_tool_state_store()
        self._load_persisted_tasks()
        log.info("Executor v2.0 online")

    def _provider_looks_online(self, name: str) -> bool:
        """Best-effort: skip blacklisted / health-cooled / keyless local backends."""
        n = (name or "").strip().lower()
        if not n:
            return False
        cfg = get_config()
        pcfg = cfg.get_provider(n)
        if not pcfg or not getattr(pcfg, "available", False):
            return False
        if not cfg.is_provider_allowed(n):
            return False
        # Prefer cloud keys; local loopback without key is often dead
        ptype = (getattr(pcfg, "provider_type", "") or "").lower()
        pid = (getattr(pcfg, "provider_id", "") or n).lower()
        if n in self._LOCAL_FALLBACK_SKIP or pid in self._LOCAL_FALLBACK_SKIP or ptype in (
            "ollama", "local_ollama",
        ):
            # Only allow if relay health recently succeeded
            try:
                health = getattr(self.relay, "_health", {}).get(n)
                if health is not None:
                    if not health.available_now():
                        return False
                    if health.consecutive_failures > 0 and health.success_count == 0:
                        return False
                    if health.success_count == 0 and health.failure_count > 0:
                        return False
                    # Never cold-start ollama on follow-up when never succeeded this process
                    if health.success_count == 0:
                        return False
            except Exception:
                return False
        try:
            from watchdog import get_blacklist
            if not get_blacklist().is_available(n):
                return False
        except Exception:
            pass
        try:
            health = getattr(self.relay, "_health", {}).get(n)
            if health is not None and not health.available_now():
                return False
        except Exception:
            pass
        return True

    def _pick_followup_provider(self, current: Optional[str]) -> Optional[str]:
        """Next provider for quality follow-up — online cloud before dead Ollama."""
        cur = (current or "").strip().lower()
        ranked: list[str] = []
        try:
            from watchdog import get_blacklist
            ranked = [p for p in get_blacklist().ranked_providers(cur) if p != cur]
        except Exception:
            ranked = []
        # Merge with relay-preferred cloud order
        cloud_first: list[str] = []
        try:
            from free_cloud import recommended_free_providers
            cloud_first = list(recommended_free_providers())
        except Exception:
            cloud_first = ["openrouter", "groq", "gemini"]
        primary = (get_config().relay.primary_provider or "").strip().lower()
        ordered: list[str] = []
        for p in [primary, *cloud_first, *ranked, *list(get_config().available_providers)]:
            pl = (p or "").strip().lower()
            if pl and pl not in ordered and pl != cur:
                ordered.append(pl)
        for p in ordered:
            if self._provider_looks_online(p):
                return p
        return None

    def _get_watchdog(self):
        if not self._watchdog:
            from watchdog import get_watchdog
            self._watchdog = get_watchdog()
            self._watchdog.set_executor(self)
        return self._watchdog

    def _get_dispatcher(self):
        if not self._dispatcher:
            from dispatcher import get_dispatcher
            self._dispatcher = get_dispatcher()
        return self._dispatcher

    def _get_search(self):
        if not self._search:
            from search import get_search
            self._search = get_search()
        return self._search

    # ── Task erstellen ─────────────────────────────────────────────────────────
    def create_task(self, typ: TaskType, prompt: str,
                    beschreibung: str = "", prioritaet: float = 5.0,
                    provider: Optional[str] = None,
                    parent_id: Optional[str] = None,
                    system_prompt: str = "",
                    sudo_aktiv: bool = False,
                    strategy: Optional[Strategy] = None,
                    interaction_class: str = "",
                    classification: Optional[ClassificationResult] = None,
                    allow_tools: Optional[bool] = None,
                    allow_followup: Optional[bool] = None,
                    allow_provider_switch: Optional[bool] = None,
                    tool_policy: Optional[ToolPolicy] = None,
                    retrieved_context: Optional[dict] = None) -> Task:
        if strategy is None:
            strategy = Strategy(
                allow_tools=True if allow_tools is None else allow_tools,
                allow_followup=True if allow_followup is None else allow_followup,
                allow_provider_switch=True if allow_provider_switch is None else allow_provider_switch,
            )
        elif any(flag is not None for flag in (allow_tools, allow_followup, allow_provider_switch)):
            log.warning("create_task: allow_* flags ignoriert, da Strategy explizit gesetzt ist")
        task = Task(
            id            = self.next_task_id(),
            typ           = typ,
            prompt        = prompt,
            beschreibung  = beschreibung or prompt[:80],
            prioritaet    = prioritaet,
            provider      = provider,
            parent_id     = parent_id,
            system_prompt = system_prompt,
            sudo_aktiv    = sudo_aktiv,
            strategy      = strategy,
            tool_policy   = tool_policy or ToolPolicy(),
            interaction_class = interaction_class,
            classification = classification,
            retrieved_context = retrieved_context or {},
        )
        self._tasks[task.id] = task
        AuditLog.task(task.id, "created", task.beschreibung[:100])
        return task


    def next_task_id(self) -> str:
        return uuid.uuid4().hex[:8]

    async def submit(self, task: Task) -> Task:
        await self._queue.put((-task.prioritaet, task.id))
        task.log("In Queue")
        self._notify(task)
        return task

    async def submit_and_wait(self, task: Task,
                              timeout: float = 180.0) -> Task:
        await self.submit(task)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if task.status in (TaskStatus.DONE, TaskStatus.FAILED,
                               TaskStatus.CANCELLED):
                return task
            await asyncio.sleep(0.3)
        task.status = TaskStatus.FAILED
        task.fehler = "Timeout"
        return task

    # ── Worker ────────────────────────────────────────────────────────────────
    async def start_worker(self, concurrency: int = 4):
        self._loop = asyncio.get_running_loop()
        log.info(f"Worker gestartet (concurrency={concurrency})")
        sem = asyncio.Semaphore(concurrency)

        # Watchdog starten
        await self._get_watchdog().start()

        async def _run():
            while True:
                try:
                    _, task_id = await asyncio.wait_for(
                        self._queue.get(), timeout=5.0
                    )
                    task = self._tasks.get(task_id)
                    if (task and task.status == TaskStatus.QUEUED
                            and task_id not in self._running):
                        self._running.add(task_id)
                        asyncio.create_task(self._with_sem(task, sem))
                except asyncio.TimeoutError:
                    pass
                except Exception as e:
                    log.error(f"Worker: {e}")

        asyncio.create_task(_run())

    async def _with_sem(self, task: Task, sem: asyncio.Semaphore):
        async with sem:
            await self._execute(task)
        self._running.discard(task.id)
        if task.watchdog_resume_pending:
            task.watchdog_resume_pending = False
            if task.status == TaskStatus.CANCELLED:
                task.status = TaskStatus.RESUMABLE
                self.resume_task(task.id)

    # ── Pre-Flight-Validation ─────────────────────────────────────────────────
    def _preflight(self, task: Task) -> Optional[str]:
        """
        Prüft einen Task bevor er ausgeführt wird.
        Gibt None zurück wenn OK, sonst Fehlermeldung.
        Wenn SUDO aktiv: Steffen's Befehl — immer OK.
        """
        if task.sudo_aktiv:
            return None  # Steffen hat autorisiert. Keine weitere Prüfung.

        wortanzahl = len(task.prompt.split())
        if wortanzahl < 1:
            return "Leerer Prompt"

        return None

    # ── Haupt-Execute ─────────────────────────────────────────────────────────
    async def _execute(self, task: Task):
        preflight_fehler = self._preflight(task)
        if preflight_fehler:
            task.status = TaskStatus.FAILED
            task.fehler = preflight_fehler
            self._notify(task)
            return

        t0 = time.monotonic()
        resumed_mid = False
        if task.resume_strategy == "from_checkpoint" or task.resume_checkpoint_id:
            resume_action = await self._resume_from_checkpoint(task)
            task.resume_strategy = ""
            if resume_action == "done":
                self._finalize_execute(task, t0)
                return
            resumed_mid = resume_action == "continue"

        if task.status != TaskStatus.RUNNING:
            task.status = TaskStatus.RUNNING
            task.gestartet = time.strftime("%Y-%m-%d %H:%M:%S")
            task.progress = 0.1
            task.log(f"Start │ {task.typ.value} │ sudo={task.sudo_aktiv}")
            state = self._tool_state.get_or_create(task.id, task.prompt)
            if state.status not in ("idle", "done", "failed"):
                self._tool_state.mark_resume(task.id)
            self._tool_state.set_status(task.id, "running")
            task.tool_strategy = self._tool_state.get_or_create(task.id).to_dict()
            self._notify(task)
            AuditLog.task(task.id, "running", task.beschreibung[:80])
            if not resumed_mid:
                self._checkpoint(task, CheckpointState.PLANNING)

        try:
            if task.typ == TaskType.FILE:
                await self._execute_file(task)
            elif task.typ == TaskType.CODE:
                await self._execute_code(task)
            elif task.typ == TaskType.SEARCH:
                await self._execute_search(task)
            elif task.typ == TaskType.RESEARCH:
                await self._execute_research(task)
            elif task.typ in (TaskType.BROADCAST, TaskType.SPLIT,
                              TaskType.PIPELINE):
                await self._execute_multi_ki(task)
            else:
                await self._execute_ai(task)
        except Exception as e:
            task.log(f"Fehler: {e}")
            AuditLog.error("Executor", str(e), f"task={task.id}")
            cp = get_memory().get_latest_checkpoint(task.id)
            cp_state = normalize_state((cp or {}).get("state_name", ""))
            if is_resumable_state(cp_state):
                self._mark_resumable(task, f"exception:{type(e).__name__}")
                task.fehler = str(e)[:200]
            else:
                task.status = TaskStatus.FAILED
                task.fehler = str(e)[:200]
        finally:
            self._finalize_execute(task, t0)

    def _finalize_execute(self, task: Task, t0: float) -> None:
        if task.status == TaskStatus.CANCELLED:
            return
        if task.status in (TaskStatus.DONE, TaskStatus.FAILED):
            # Bounded learning marker for procedure / score feedback (C4)
            try:
                tools = [
                    (t.get("name") if isinstance(t, dict) else str(t))
                    for t in (getattr(task, "used_tools", None) or [])[-6:]
                ]
                task.decision_trace.add(
                    TracePhase.LEARNING,
                    "procedure_record" if tools else "turn_complete",
                    {
                        "ok": task.status == TaskStatus.DONE,
                        "score_total": round(float(task.score.total), 3) if task.score else None,
                        "tools": tools,
                        "provider": (task.provider_used or "")[:40],
                    },
                )
            except Exception:
                pass
            self._checkpoint(
                task,
                CheckpointState.LEARNING_COMMIT,
                result_snapshot=build_result_snapshot(
                    antwort=task.antwort,
                    provider=task.provider_used,
                    score_total=task.score.total if task.score else None,
                ),
            )
            self._checkpoint(
                task,
                CheckpointState.DONE if task.status == TaskStatus.DONE else CheckpointState.FAILED,
                result_snapshot=build_result_snapshot(
                    antwort=task.antwort,
                    provider=task.provider_used,
                    score_total=task.score.total if task.score else None,
                ),
            )
            # C1 residual: optional portable file export (opt-in env)
            try:
                maybe_export_portable_trace(
                    getattr(task, "decision_trace", None),
                    request_id=str(task.id or ""),
                )
            except Exception:
                pass
            self._persist_task(task)
        elif task.status == TaskStatus.RESUMABLE:
            self._persist_task(task)
        task.dauer_sek = round(time.monotonic() - t0, 2)
        task.abgeschlossen = time.strftime("%Y-%m-%d %H:%M:%S")
        task.progress = 1.0 if task.status != TaskStatus.RESUMABLE else task.progress
        self._tool_state.set_status(task.id, task.status.value)
        task.tool_strategy = self._tool_state.get_or_create(task.id).to_dict()
        self._notify(task)

    def _should_try_tool(self, task: Task, prompt: str, iteration: int) -> bool:
        return self._evaluate_tool_eligibility(task, prompt, iteration).eligible

    def _evaluate_tool_eligibility(self, task: Task, prompt: str, iteration: int) -> ToolEligibilityDecision:
        decision = evaluate_tool_eligibility(task, prompt, iteration, task.tool_policy)
        task.tool_decision_reason = decision.reason.value
        task.decision_trace.add(
            TracePhase.ELIGIBILITY,
            "evaluated",
            {
                "eligible": decision.eligible,
                "reason": decision.reason.value,
                "iteration": iteration,
                "prompt_nonempty": bool((prompt or "").strip()),
            },
        )
        return decision

    def _tool_context_block(self, tool_name: str, tool_kind: str, via: str, result: dict) -> str:
        content = str(result.get('output') or result.get('error') or '').strip()[:2200]
        return (
            "\n\n[Tool-Kontext]\n"
            f"Tool: {tool_name}\n"
            f"Typ: {tool_kind} via {via}\n"
            f"Ergebnis:\n{content}\n"
            "[/Tool-Kontext]"
        )

    def _tool_limit(self) -> int:
        return 3 if bool(getattr(get_config(), "multi_tool_mode", False)) else 1

    async def _maybe_use_tool(self, task: Task, prompt: str, iteration: int, used_tool_ids: set[str]) -> tuple[str, str]:
        eligibility = self._evaluate_tool_eligibility(task, prompt, iteration)
        if not eligibility.eligible:
            return "", ""
        task.tool_selection_reason = ToolDecisionReason.ELIGIBLE.value
        context_blocks: list[str] = []
        successful_outputs: list[str] = []
        tool_runs = 0

        while tool_runs < self._tool_limit():
            selection_decision = await select_live_tool_for_task(task, prompt, iteration, task.tool_policy)
            task.tool_selection_reason = selection_decision.reason.value
            selection = selection_decision.selected
            if not selection:
                task.decision_trace.add(
                    TracePhase.SELECTION,
                    "no_candidate",
                    {
                        "reason": selection_decision.reason.value,
                        "iteration": iteration,
                        "metadata": dict(selection_decision.metadata or {}),
                    },
                )
                break
            if selection.get("identifier") in used_tool_ids:
                task.decision_trace.add(
                    TracePhase.SELECTION,
                    "candidate_skipped_already_used",
                    {
                        "identifier": selection.get("identifier", ""),
                        "name": selection.get("name", ""),
                    },
                )
                break
            task.decision_trace.add(
                TracePhase.SELECTION,
                "selected_candidate",
                {
                    "reason": selection_decision.reason.value,
                    "identifier": selection.get("identifier", ""),
                    "name": selection.get("name", ""),
                    "kind": selection.get("kind", ""),
                    "category": selection.get("category", "general"),
                    "source": selection.get("source", ""),
                    "metadata": dict(selection_decision.metadata or {}),
                },
            )
            task.log(f"Tool-Auswahl: {selection.get('name')} [{selection.get('kind')}/{selection.get('category')}]")
            self._notify(task)
            task.decision_trace.add(
                TracePhase.EXECUTION,
                "execution_started",
                {
                    "identifier": selection.get("identifier", ""),
                    "name": selection.get("name", ""),
                    "iteration": iteration,
                    "run_index": tool_runs + 1,
                },
            )
            self._checkpoint(
                task,
                CheckpointState.TOOL_PENDING,
                current_prompt=prompt,
                tool_snapshot={
                    "tool": selection.get("name", ""),
                    "identifier": selection.get("identifier", ""),
                    "kind": selection.get("kind", ""),
                    "pending": True,
                },
            )
            from config import Level
            from constitution_override import build_override_context

            override_ctx = build_override_context(
                prompt=prompt,
                sudo_active=task.sudo_aktiv,
                caller_level=Level.STEFFEN if task.sudo_aktiv else Level.TASK,
                source="executor",
            )
            blocked = constitution_gate_for_tool(
                selection, prompt, override_ctx=override_ctx,
            )
            if blocked:
                result = ensure_result_contract(blocked, source="constitution")
            else:
                result = ensure_result_contract(
                    await run_selected_tool(
                        selection,
                        prompt,
                        override_ctx=override_ctx,
                        skip_constitution=True,
                    ),
                    source="executor_boundary",
                )
            identifier = selection.get("identifier", "")
            name = selection.get("name", identifier)
            kind = selection.get("kind", "")
            category = selection.get("category", "general")
            via = result.get('via') or selection.get('source') or kind
            self._tool_state.record_call(
                task.id, source=selection.get("source", "unknown"), identifier=identifier, name=name,
                feature_type=selection.get("mcp_feature", "tool"), ok=bool(result.get('ok')), category=category,
                kind=kind, note=result.get('error', '') or result.get('via', ''), status_code=result.get('status_code'),
                output=str(result.get('output') or result.get('error') or '')
            )
            task.tool_strategy = self._tool_state.get_or_create(task.id).to_dict()
            self._checkpoint(
                task,
                CheckpointState.TOOL_PENDING,
                current_prompt=prompt,
                tool_snapshot={"tool": name, "identifier": identifier, "kind": kind, "pending": False},
                result_snapshot=build_result_snapshot(
                    partial=bool(result.get("ok")),
                    via=via,
                    antwort=str(result.get("output") or result.get("error") or "")[:1200],
                ),
                side_effect_refs=[f"{kind}:{prompt[:80]}"],
            )
            tool_runs += 1
            if not result.get('ok'):
                meta = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
                constitution_blocked = (
                    str(meta.get("source") or "") == "constitution"
                    or "Verfassung" in str(result.get("error") or "")
                )
                task.decision_trace.add(
                    TracePhase.EXECUTION,
                    "constitution_blocked" if constitution_blocked else "execution_failed",
                    {
                        "identifier": identifier,
                        "name": name,
                        "via": via,
                        "status_code": result.get("status_code"),
                        "error": result.get("error", ""),
                        "blocked_by": list(meta.get("blocked_by") or []),
                        "source": meta.get("source") or via,
                    },
                )
                task.decision_trace.add(
                    TracePhase.CONTEXT_INTEGRATION,
                    "context_skipped",
                    {
                        "identifier": identifier,
                        "name": name,
                        "reason": "constitution_blocked" if constitution_blocked else "tool_execution_failed",
                    },
                )
                task.log(f"Tool fehlgeschlagen: {name} – {result.get('error', 'unbekannt')}")
                continue
            task.decision_trace.add(
                TracePhase.EXECUTION,
                "execution_succeeded",
                {
                    "identifier": identifier,
                    "name": name,
                    "via": via,
                    "status_code": result.get("status_code"),
                },
            )
            used_tool_ids.add(identifier)
            tool_note = {
                "tool_id": identifier,
                "name": name,
                "kind": kind,
                "category": category,
                "via": via,
                "status_code": result.get('status_code'),
                "source": selection.get('source'),
            }
            task.used_tools.append(tool_note)
            task.used_tools = task.used_tools[-12:]
            task.log(f"Tool genutzt: {name}")
            AuditLog.action("Executor", "tool_used", f"task={task.id} tool={name}", Level.ISAAC)
            context_blocks.append(self._tool_context_block(name, kind, via, result))
            task.decision_trace.add(
                TracePhase.CONTEXT_INTEGRATION,
                "context_appended",
                {
                    "identifier": identifier,
                    "name": name,
                    "via": via,
                },
            )
            successful_outputs.append(f"{name}:\n{str(result.get('output') or result.get('error') or '').strip()[:1600]}")
            task.tool_selection_reason = ToolDecisionReason.SELECTED_CANDIDATE.value

        next_input = ""
        if successful_outputs:
            next_input = self._tool_state.generate_next_input(
                task.id,
                task.prompt,
                "mehreren Tools" if len(successful_outputs) > 1 else task.used_tools[-1]["name"],
                "\n\n".join(successful_outputs),
            )
        else:
            task.decision_trace.add(
                TracePhase.CONTEXT_INTEGRATION,
                "context_skipped",
                {
                    "reason": "no_successful_tool_output",
                },
            )
        task.decision_trace.add(
            TracePhase.FOLLOWUP,
            "followup_generated" if bool(next_input) else "followup_not_generated",
            {
                "has_successful_outputs": bool(successful_outputs),
                "output_count": len(successful_outputs),
            },
        )
        return "".join(context_blocks), next_input

    # ── AI-Task ───────────────────────────────────────────────────────────────
    async def _execute_ai(self, task: Task):
        system = self._build_system(task)
        current_prompt = task.resume_current_prompt or task.prompt
        if task.resume_current_prompt:
            task.resume_current_prompt = ""
        current_prov = task.provider
        antwort = ""
        last_score_total = -1.0
        stale_rounds = 0
        seen_answers: set[str] = set()
        used_tool_ids: set[str] = set(task.resume_used_tool_ids or [])
        task.resume_used_tool_ids = []
        saved_tool_context = task.resume_tool_context or ""
        task.resume_tool_context = ""
        start_iteration = int(task.resume_start_iteration or 0)
        task.resume_start_iteration = 0
        ai_t0 = time.perf_counter()

        for iteration in range(get_config().logic.max_followup_rounds + 1):
            if iteration < start_iteration:
                continue
            if task.status == TaskStatus.CANCELLED:
                task.log("Vor Ausführung abgebrochen")
                return

            task.iteration = iteration
            task.progress = 0.1 + (iteration / (get_config().logic.max_followup_rounds + 1)) * 0.7
            task.status = TaskStatus.RUNNING
            task.log(f"Iter {iteration}: → {current_prov or 'auto'}")
            self._checkpoint(
                task,
                CheckpointState.PLANNING,
                current_prompt=current_prompt,
                result_snapshot={"iteration": iteration, "provider": current_prov or "auto"},
            )
            self._notify(task)

            choose_t0 = time.perf_counter()
            if saved_tool_context:
                tool_context = saved_tool_context
                generated_next_input = ""
                saved_tool_context = ""
            else:
                tool_context, generated_next_input = await self._maybe_use_tool(
                    task, current_prompt, iteration, used_tool_ids,
                )
            effective_prompt = current_prompt + tool_context if tool_context else current_prompt
            provider_hint_ms = round((time.perf_counter() - choose_t0) * 1000, 2)

            call_t0 = time.perf_counter()
            antwort, prov = await self.relay.ask_with_fallback(
                effective_prompt, system, preferred=current_prov, task_id=task.id
            )
            model_call_ms = round((time.perf_counter() - call_t0) * 1000, 2)
            task.provider_used = prov
            task.log(f"Latency: prep={provider_hint_ms}ms model={model_call_ms}ms provider={prov}")

            model_name = ""
            try:
                pcfg = get_config().get_provider(prov) if prov else None
                model_name = str(getattr(pcfg, "model", "") or getattr(pcfg, "default_model", "") or "")
            except Exception:
                model_name = ""
            # Rough token estimate without sending raw prompts into the trace.
            try:
                from relay import RateLimiter

                approx_in = RateLimiter.estimate_tokens(system + "\n" + effective_prompt)
                approx_out = RateLimiter.estimate_tokens(antwort or "")
            except Exception:
                approx_in = max(1, (len(system) + len(effective_prompt)) // 4)
                approx_out = max(0, len(antwort or "") // 4)

            if task.status == TaskStatus.CANCELLED:
                task.log("Während Provider-Aufruf abgebrochen")
                return

            fp = _fingerprint(antwort)
            if fp in seen_answers and iteration > 0:
                task.log("Loop-Schutz: identische Antwort erkannt")
                break
            seen_answers.add(fp)

            if antwort.startswith("[RELAY"):
                # ask_with_fallback already exhausted usable providers — fail fast
                # instead of RESUMABLE (would hang submit_and_wait up to timeout).
                task.antwort = antwort
                task.fehler = antwort[:200]
                task.status = TaskStatus.FAILED
                task.decision_trace.add(
                    TracePhase.EXECUTION,
                    "model_call_failed",
                    build_execution_llm_trace_data(
                        provider=prov or "",
                        model=model_name,
                        latency_ms=model_call_ms,
                        iteration=iteration,
                        prompt_chars=len(effective_prompt),
                        response_chars=len(antwort or ""),
                        input_tokens=approx_in,
                        output_tokens=approx_out,
                        ok=False,
                        error=antwort[:200],
                        extra={"prep_ms": provider_hint_ms},
                    ),
                )
                task.log(f"Relay fehlgeschlagen nach Fallback: {antwort[:120]}")
                return

            task.decision_trace.add(
                TracePhase.EXECUTION,
                "model_call",
                build_execution_llm_trace_data(
                    provider=prov or "",
                    model=model_name,
                    latency_ms=model_call_ms,
                    iteration=iteration,
                    prompt_chars=len(effective_prompt),
                    response_chars=len(antwort or ""),
                    input_tokens=approx_in,
                    output_tokens=approx_out,
                    ok=True,
                    extra={"prep_ms": provider_hint_ms},
                ),
            )

            self._get_watchdog().record_progress(task.id)

            task.status = TaskStatus.EVALUATING
            self._checkpoint(
                task,
                CheckpointState.EVALUATING,
                current_prompt=effective_prompt,
                result_snapshot=build_result_snapshot(
                    antwort=antwort,
                    provider=prov,
                ),
            )
            eval_t0 = time.perf_counter()
            score = self.logic.evaluate(antwort, task.prompt, task.id)
            eval_ms = round((time.perf_counter() - eval_t0) * 1000, 2)
            task.score = score
            task.decision_trace.add(
                TracePhase.EVALUATION,
                "quality_scored",
                {
                    "score_total": round(float(score.total), 3),
                    "acceptable": bool(getattr(score, "acceptable", False)),
                    "iteration": iteration,
                    "provider": prov or "",
                    "model": model_name,
                    "eval_ms": eval_ms,
                    "summary": (score.summary() if hasattr(score, "summary") else "")[:120],
                },
            )
            self._checkpoint(
                task,
                CheckpointState.EVALUATING,
                current_prompt=effective_prompt,
                result_snapshot=build_result_snapshot(
                    antwort=antwort,
                    provider=prov,
                    score_total=score.total,
                ),
            )
            task.log(f"Score: {score.summary()} | eval={eval_ms}ms")
            self._notify(task)

            if score.total <= last_score_total + 0.2:
                stale_rounds += 1
            else:
                stale_rounds = 0
            last_score_total = score.total

            if score.acceptable or iteration >= get_config().logic.max_followup_rounds:
                break

            # Advisory only: Klassifikation beeinflusst Tool-/Follow-up-Eligibility nicht.
            if task.typ == TaskType.CHAT and task.current_interaction_class:
                task.log(f"Hinweis: Klassifikation advisory ({task.current_interaction_class})")

            if not task.allow_followup:
                break

            decision = self.logic.decide_followup(antwort, task.prompt, score, iteration, prov, task.id)
            task.followup = decision
            task.status = TaskStatus.FOLLOWUP
            task.log(f"Nachfrage: {decision.mode} – {decision.reason}")
            self._notify(task)

            task.decision_trace.add(
                TracePhase.FOLLOWUP,
                "followup_decided",
                {
                    "needed": bool(decision.needed),
                    "mode": getattr(decision, "mode", "") or "",
                    "reason": (getattr(decision, "reason", "") or "")[:120],
                    "iteration": iteration,
                    "stale_rounds": stale_rounds,
                },
            )
            if not decision.needed:
                break

            if stale_rounds >= 2:
                task.log("Loop-Schutz: Qualität verbessert sich nicht weiter")
                task.decision_trace.add(
                    TracePhase.FOLLOWUP,
                    "followup_aborted_stale",
                    {"stale_rounds": stale_rounds, "last_score": last_score_total},
                )
                break

            if decision.mode == "decompose" and decision.sub_tasks:
                results = await self._execute_sub_tasks(task, decision.sub_tasks, prov)
                antwort = self._aggregate(results)
                break

            if task.allow_provider_switch and (decision.switch_provider or stale_rounds >= 1):
                next_prov = self._pick_followup_provider(prov)
                if next_prov and next_prov != prov:
                    current_prov = next_prov
                    task.log(f"Provider: {prov} → {current_prov}")
                elif decision.switch_provider:
                    task.log(f"Provider-Switch übersprungen (kein gesunder Fallback für {prov})")

            queued_next_input = self._tool_state.pop_next_input(task.id)
            if generated_next_input and not queued_next_input:
                queued_next_input = generated_next_input
            if queued_next_input:
                current_prompt = queued_next_input
                task.log("Nächster Input aus Tool-Ergebnis generiert")
            elif decision.followup_prompt:
                current_prompt = decision.followup_prompt

            task.tool_strategy = self._tool_state.get_or_create(task.id).to_dict()
            await asyncio.sleep(get_config().relay.min_interval)

        task.tool_strategy = self._tool_state.get_or_create(task.id).to_dict()
        total_ai_ms = round((time.perf_counter() - ai_t0) * 1000, 2)
        task.log(f"Latency total ai={total_ai_ms}ms")
        if task.used_tools:
            tool_lines = ", ".join(f"{t['name']} ({t['kind']})" for t in task.used_tools[-4:])
            antwort = f"[Tools genutzt: {tool_lines}]\n\n" + antwort
        task.antwort = antwort
        if task.status != TaskStatus.FAILED:
            task.status = TaskStatus.DONE
        AuditLog.task(
            task.id,
            "done" if task.status == TaskStatus.DONE else "failed",
            f"score={task.score.total:.1f} iter={task.iteration+1}" if task.score else f"iter={task.iteration+1}",
            score=task.score.total if task.score else 0.0,
            iteration=task.iteration
        )

    # ── Search-Task ───────────────────────────────────────────────────────────
    async def _execute_search(self, task: Task):
        """Echte Websuche über MultiSearch."""
        task.log("Websuche gestartet")
        used_tool_ids: set[str] = set()
        query = task.execution_query()
        tool_context, _ = await self._maybe_use_tool(task, query, iteration=0, used_tool_ids=used_tool_ids)
        search = self._get_search()
        result = await search.search(
            query,
            max_hits=10,
            load_fulltext=True,
        )
        task.progress = 0.6
        self._notify(task)

        # Suchergebnisse an KI zur Synthese schicken
        kontext = result.als_kontext(max_hits=8)
        weatherish = any(
            m in (query or "").lower()
            for m in ("wetter", "weather", "temperatur", "vorhersage")
        )
        # Live-Wetter-APIs liefern bereits fertige Fakten → nicht per LLM verwässern
        if weatherish and result.abstract and (
            "Open-Meteo" in result.abstract or "wttr.in" in result.abstract or "Aktuell in" in result.abstract
        ):
            antwort = (
                result.abstract.strip()
                + "\n\nQuellen: Open-Meteo / wttr.in (Live-Web-Abruf)."
            )
            if tool_context:
                antwort += f"\n\nZusatz:\n{tool_context[:500]}"
            prov = "weather_api"
            task.log("Wetter: Live-API-Antwort ohne LLM-Umschreibung")
        else:
            if weatherish and (result.abstract or result.hits):
                synth_prompt = (
                    f"Beantworte die Wetterfrage mit den gelieferten LIVE-Daten.\n"
                    f"Frage: {query}\n\n"
                    f"Daten:\n{kontext}\n\n"
                    "Regeln: Nenne Ort, Tage, Temperatur min/max, Bedingungen und Quelle. "
                    "Keine Platzhalter wie [Temperatur]. Erfinde keine Zahlen."
                )
                system = (
                    "Du bist ein knapper Wetter-Assistent. "
                    "Nutze nur die gelieferten Live-Daten. Keine Halluzinationen."
                )
            else:
                # Synthesis may use full task.prompt (retrieval) as background if present
                bg = ""
                if (task.prompt or "").strip() and (task.prompt or "").strip() != query:
                    bg = f"\n\nHintergrundkontext (nicht als Suchquery):\n{(task.prompt or '')[:2500]}"
                synth_prompt = (
                    f"Basierend auf diesen Suchergebnissen, beantworte: {query}\n\n"
                    f"Suchergebnisse:\n{kontext}{bg}\n\n"
                    f"Antworte strukturiert mit Quellenangaben. "
                    f"Erfinde keine Fakten und keine Platzhalter."
                )
                system = (
                    "Du bist ein Recherche-Synthesizer. "
                    "Fasse nur die gelieferten Suchergebnisse zusammen; halluziniere nicht."
                )
            if tool_context:
                synth_prompt = f"{synth_prompt}\n\nZusätzlicher Tool-Kontext:\n{tool_context}"
            antwort, prov = await self.relay.ask_with_fallback(
                synth_prompt,
                system=system,
                task_id=task.id
            )
        task.antwort       = antwort
        task.provider_used = prov
        task.status        = TaskStatus.DONE
        task.score         = self.logic.evaluate(antwort, query, task.id)
        task.decision_trace.add(
            TracePhase.EVALUATION,
            "quality_scored",
            {
                "score_total": round(float(task.score.total), 3) if task.score else 0.0,
                "acceptable": bool(getattr(task.score, "acceptable", False)),
                "provider": prov or "",
                "via": "search",
                "hits": len(getattr(result, "hits", []) or []),
                "execution_query": (query or "")[:120],
            },
        )
        task.log(f"Suche: {len(result.hits)} Hits aus {result.quellen} q={query[:60]!r}")

    async def _execute_research(self, task: Task):
        """Tiefere Web-Recherche: mehr Engines, Volltext, Quellen-Synthese."""
        task.log("Recherche gestartet")
        used_tool_ids: set[str] = set()
        query = task.execution_query()
        tool_context, _ = await self._maybe_use_tool(task, query, iteration=0, used_tool_ids=used_tool_ids)
        search = self._get_search()
        for prefix in ("recherche:", "recherchiere:"):
            if query.lower().startswith(prefix):
                query = query.split(":", 1)[1].strip()
                break
        result = await search.search(
            query,
            max_hits=15,
            load_fulltext=True,
            engines=["ddg", "brave", "wikipedia", "searxng", "reddit", "arxiv", "github"],
        )
        task.progress = 0.65
        self._notify(task)

        kontext = result.als_kontext(max_hits=12)
        weatherish = any(
            m in (query or "").lower()
            for m in ("wetter", "weather", "temperatur", "vorhersage")
        )
        if weatherish and result.abstract and (
            "Open-Meteo" in result.abstract or "wttr.in" in result.abstract or "Aktuell in" in result.abstract
        ):
            antwort = (
                result.abstract.strip()
                + "\n\nQuellen: Open-Meteo / wttr.in (Live-Web-Abruf)."
            )
            if tool_context:
                antwort += f"\n\nZusatz:\n{tool_context[:500]}"
            prov = "weather_api"
            task.log("Wetter-Recherche: Live-API-Antwort ohne LLM-Umschreibung")
        else:
            if weatherish and (result.abstract or result.hits):
                synth_prompt = (
                    f"Beantworte die Wetter-Recherche mit LIVE-Daten.\n"
                    f"Frage: {query}\n\n"
                    f"Daten:\n{kontext}\n\n"
                    "Kurzfassung mit Ort, Tagen, Temperaturen, Bedingungen, Quellen. "
                    "Keine Platzhalter, keine erfundenen Werte."
                )
                system = (
                    "Du bist ein knapper Wetter-Rechercheur. "
                    "Nur gelieferte Live-Daten; halluziniere nicht."
                )
            else:
                synth_prompt = (
                    f"Führe eine eigenständige Web-Recherche durch und beantworte: {query}\n\n"
                    f"Recherche-Ergebnisse:\n{kontext}\n\n"
                    "Strukturiere die Antwort mit:\n"
                    "1) Kurzfassung\n2) Kernerkenntnisse\n3) Quellenliste (URL/Titel)\n"
                    "Markiere Unsicherheiten explizit. Keine erfundenen Fakten."
                )
                system = (
                    "Du bist ein Recherche-Analyst. Nutze nur die gelieferten Quellen, "
                    "halluziniere keine Fakten, und nenne Unsicherheiten."
                )
            if tool_context:
                synth_prompt = f"{synth_prompt}\n\nZusätzlicher Tool-Kontext:\n{tool_context}"
            antwort, prov = await self.relay.ask_with_fallback(
                synth_prompt,
                system=system,
                task_id=task.id,
            )
        task.antwort = antwort
        task.provider_used = prov
        task.status = TaskStatus.DONE
        task.score = self.logic.evaluate(antwort, query, task.id)
        task.log(f"Recherche: {len(result.hits)} Hits aus {result.quellen} q={query[:60]!r}")

    # ── Multi-KI-Task ─────────────────────────────────────────────────────────
    async def _execute_multi_ki(self, task: Task):
        """Verteilt Task auf mehrere KI-Instanzen."""
        dispatcher = self._get_dispatcher()
        from browser import get_browser
        browser    = get_browser()
        instance_ids = browser.get_active_ids()

        if not instance_ids:
            # Fallback: API-Provider als "Instanzen"
            instance_ids = get_config().available_providers[:4]

        task.log(f"Multi-KI: {len(instance_ids)} Instanzen")
        self._notify(task)

        system = self._build_system(task)

        if task.typ == TaskType.BROADCAST:
            result = await dispatcher.broadcast(
                task.prompt, instance_ids, system=system
            )
        elif task.typ == TaskType.SPLIT:
            result = await dispatcher.split(
                task.prompt, instance_ids, system=system
            )
        elif task.typ == TaskType.PIPELINE:
            result = await dispatcher.pipeline(
                task.prompt, instance_ids, system=system
            )

        task.antwort = result.final
        task.status  = TaskStatus.DONE
        task.score   = self.logic.evaluate(result.final, task.prompt, task.id)
        task.log(
            f"Multi-KI done: {result.n_instanzen} Instanzen, "
            f"{result.dauer:.1f}s"
        )

    # ── Code-Ausführung (ASYNC, kein blocking!) ───────────────────────────────
    async def _execute_code(self, task: Task):
        """CODE path: Repo SEARCH/REPLACE edits when allowed, else sandboxed snippet run.

        Phase 2.7:
          - Structured edits only if strategy.allow_tools and ISAAC_CODE_EDIT
          - apply via code_edit (Constitution + path roots + DecisionTrace)
          - No opportunistic edits on CHAT (this method is CODE-only)
          - Fallback: generate pure Python and run isolated (legacy path)
        """
        ctx = isaac_ctx("Executor", f"Code ausführen: task {task.id}")
        self.gate.require("execute_code", ctx)

        allow_edits = bool(getattr(task, "allow_tools", False))
        try:
            from code_edit import code_edit_enabled, looks_like_edit_blocks

            edits_feature_on = code_edit_enabled()
        except Exception:
            edits_feature_on = False
            looks_like_edit_blocks = lambda _t: False  # type: ignore

        if allow_edits and edits_feature_on:
            code_prompt = (
                f"Aufgabe:\n{task.prompt}\n\n"
                "Wenn du bestehende Dateien im Repo ändern sollst, antworte mit einem oder mehreren "
                "SEARCH/REPLACE-Blöcken in diesem Format (Dateipfad in der Zeile davor):\n"
                "path/to/file.py\n"
                "<<<<<<< SEARCH\n"
                "exakte alte Zeilen\n"
                "=======\n"
                "neue Zeilen\n"
                ">>>>>>> REPLACE\n\n"
                "SEARCH muss exakt und eindeutig im File vorkommen.\n"
                "Wenn du stattdessen nur ein kurzes lauffähiges Python-Snippet brauchst "
                "(kein Repo-Edit), antworte NUR mit dem Code ohne Markdown."
            )
        else:
            code_prompt = (
                f"Schreibe Python-Code für:\n{task.prompt}\n\n"
                "Antworte NUR mit dem Code ohne Erklärungen oder Markdown."
            )

        raw_code, prov = await self.relay.ask_with_fallback(code_prompt, task_id=task.id)
        task.provider_used = prov

        # Phase 2.7: structured repo edit path (Strategy.allow_tools gated)
        if allow_edits and edits_feature_on and looks_like_edit_blocks(raw_code or ""):
            handled = await self._execute_code_edits(task, raw_code or "")
            if handled:
                return

        code = _normalize_code(raw_code)

        ok, reason = _validate_generated_code(code)
        if not ok:
            task.antwort = f"[CODE] Geblockt: {reason}"
            task.status = TaskStatus.FAILED
            AuditLog.action("Executor", "code_blocked", reason, Level.ISAAC, erfolg=False)
            return

        exec_dir = WORKSPACE / ".isaac_exec"
        exec_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".py", dir=exec_dir, delete=False, encoding="utf-8") as tmp:
                tmp.write(code)
                tmp_path = Path(tmp.name)

            kwargs = {}
            if os.name != "nt":
                kwargs["preexec_fn"] = _limit_subprocess_resources

            proc = await asyncio.create_subprocess_exec(
                "python3", "-I", "-S", str(tmp_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(WORKSPACE),
                env={"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"},
                **kwargs,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=8.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                task.antwort = "[CODE] Timeout (8s)"
                task.status = TaskStatus.FAILED
                return

            output = (stdout.decode(errors="replace") or stderr.decode(errors="replace") or "(kein Output)").strip()
            task.antwort = f"```python\n{code[:700]}\n```\n\n**Output:**\n```\n{output[:1200]}\n```"
            task.status = TaskStatus.DONE if proc.returncode == 0 else TaskStatus.FAILED
            if proc.returncode != 0:
                task.fehler = output[:200]
            AuditLog.system_cmd("Executor", "python3 -I -S [tempfile]", proc.returncode)
        except FileNotFoundError:
            task.antwort = "[CODE] python3 nicht gefunden"
            task.status = TaskStatus.FAILED
        finally:
            if tmp_path:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass

    async def _execute_code_edits(self, task: Task, model_text: str) -> bool:
        """Apply SEARCH/REPLACE from model output. Returns True if path fully handled."""
        from code_edit import apply_from_model_text, code_edit_dry_run

        try:
            from config import BASE_DIR

            root = Path(BASE_DIR)
        except Exception:
            root = Path.cwd()

        dry = code_edit_dry_run()
        task.decision_trace.add(
            TracePhase.EXECUTION,
            "code_edit_start",
            {
                "dry_run": dry,
                "allow_tools": bool(getattr(task, "allow_tools", False)),
                "chars": len(model_text or ""),
            },
        )

        # apply_from_model_text is sync/IO-bound — run in thread to keep loop free
        summary = await asyncio.to_thread(
            apply_from_model_text,
            model_text,
            dry_run=dry,
            root=root,
            decision_trace=task.decision_trace,
            stop_on_error=True,
            skip_constitution=False,
        )

        if summary.get("reason") == "no_hunks":
            task.decision_trace.add(
                TracePhase.EXECUTION,
                "code_edit_skip",
                {"reason": "no_hunks"},
            )
            return False

        used = getattr(task, "used_tools", None)
        if isinstance(used, list):
            used.append({"name": "code_edit", "kind": "code_edit", "via": "executor_code"})
        else:
            task.used_tools = [{"name": "code_edit", "kind": "code_edit", "via": "executor_code"}]

        ok = bool(summary.get("ok"))
        mode = "DRY-RUN" if summary.get("dry_run") else "APPLY"
        body = summary.get("summary") or ""
        paths = [
            (r.get("path") or "")[:120]
            for r in (summary.get("results") or [])
            if r.get("ok") and r.get("path")
        ][:12]
        fail_paths = [
            (r.get("path") or "")[:120]
            for r in (summary.get("results") or [])
            if not r.get("ok") and r.get("path")
        ][:8]
        task.antwort = (
            f"[CODE_EDIT:{mode}] hunks={summary.get('n_hunks', 0)} "
            f"ok={summary.get('n_ok', 0)}\n{body}"
        )[:4000]
        task.status = TaskStatus.DONE if ok else TaskStatus.FAILED
        if not ok:
            # Prefer first concrete apply failure over generic partial_or_failed
            first_fail = ""
            for r in summary.get("results") or []:
                if not r.get("ok"):
                    first_fail = str(r.get("message") or "")[:120]
                    break
            task.fehler = (first_fail or summary.get("reason") or "code_edit_failed")[:200]

        task.decision_trace.add(
            TracePhase.EXECUTION,
            "code_edit_done",
            {
                "ok": ok,
                "dry_run": bool(summary.get("dry_run")),
                "n_hunks": summary.get("n_hunks"),
                "n_ok": summary.get("n_ok"),
                "reason": summary.get("reason"),
                "paths": paths,
                "fail_paths": fail_paths,
                "repair_hints": len(summary.get("repair_hints") or []),
            },
        )
        AuditLog.action(
            "Executor",
            "code_edit",
            f"ok={ok} dry_run={summary.get('dry_run')} hunks={summary.get('n_hunks')}",
            Level.ISAAC,
            erfolg=ok,
        )

        # Phase 3.7: optional auto-commit after successful real writes
        if (
            ok
            and not summary.get("dry_run")
            and paths
            and bool(getattr(task, "allow_tools", False))
        ):
            git_block = await self._maybe_auto_commit_code_edit(
                task, root=root, paths=paths
            )
            if git_block:
                task.antwort = (task.antwort + "\n\n" + git_block)[:4000]
        return True

    async def _maybe_auto_commit_code_edit(
        self, task: Task, *, root: Path, paths: list[str]
    ) -> str:
        """Commit edited paths when ISAAC_GIT_OPS_AUTO_COMMIT=1. Fail-soft."""
        try:
            from git_ops import (
                format_git_result,
                git_commit,
                git_ops_auto_commit_enabled,
                git_ops_enabled,
            )
        except Exception:
            return ""
        if not git_ops_enabled() or not git_ops_auto_commit_enabled():
            return ""

        desc = (task.beschreibung or task.prompt or "code edit").strip()
        # one-line message
        msg = re.sub(r"\s+", " ", desc)[:160]
        if not msg:
            msg = "Isaac code_edit"
        if not msg.lower().startswith("isaac"):
            msg = f"Isaac: {msg}"

        task.decision_trace.add(
            TracePhase.EXECUTION,
            "git_ops_auto_commit_start",
            {"paths": paths[:12], "message": msg[:120]},
        )
        result = await asyncio.to_thread(
            git_commit,
            msg,
            paths=paths,
            root=root,
            dry_run=None,  # honor ISAAC_GIT_OPS_DRY_RUN
            decision_trace=task.decision_trace,
            skip_constitution=False,
            allow_all_tracked=False,
        )
        used = getattr(task, "used_tools", None)
        note = {"name": "git_commit", "kind": "git_ops", "via": "auto_after_code_edit"}
        if isinstance(used, list):
            used.append(note)
        else:
            task.used_tools = [note]

        task.decision_trace.add(
            TracePhase.EXECUTION,
            "git_ops_auto_commit_done",
            {
                "ok": result.ok,
                "message": result.message[:120],
                "sha": (result.meta or {}).get("sha", ""),
                "dry_run": result.dry_run,
            },
        )
        AuditLog.action(
            "Executor",
            "git_auto_commit",
            f"ok={result.ok} sha={(result.meta or {}).get('sha', '')[:12]}",
            Level.ISAAC,
            erfolg=result.ok,
        )
        return format_git_result(result)

    # ── File-Task ────────────────────────────────────────────────────────────
    async def _execute_file(self, task: Task):
        from file_access import parse_file_command, execute_file_command

        ctx = isaac_ctx("Executor", f"Datei-Op: task {task.id}")
        cmd = parse_file_command(task.prompt)
        if not cmd:
            task.antwort = (
                "[FILE] Befehl nicht erkannt.\n"
                "Beispiele:\n"
                "  lese: workspace/notiz.txt\n"
                "  datei: read isaac_core.py\n"
                "  datei: list workspace\n"
                "  datei: write workspace/out.txt inhalt: Hallo\n"
                "  datei: info config.py"
            )
            task.status = TaskStatus.FAILED
            return

        op = (cmd.operation or "read").lower()
        if op in {"write", "append"}:
            self.gate.require("file_write", ctx)
        elif op == "delete":
            self.gate.require("file_delete", ctx)
        else:
            self.gate.require("file_read", ctx)

        antwort, ok = execute_file_command(cmd)
        task.antwort = antwort
        task.status = TaskStatus.DONE if ok else TaskStatus.FAILED

    # ── Sub-Tasks ─────────────────────────────────────────────────────────────
    async def _execute_sub_tasks(self, parent: Task,
                                  sub_prompts: list[str],
                                  provider: Optional[str]) -> list[str]:
        tasks = []
        for prompt in sub_prompts:
            sub = self.create_task(
                typ          = parent.typ,
                prompt       = prompt,
                beschreibung = f"[Sub] {prompt[:60]}",
                prioritaet   = parent.prioritaet + 1,
                provider     = provider,
                parent_id    = parent.id,
                system_prompt = parent.system_prompt,
                sudo_aktiv   = parent.sudo_aktiv,
                strategy     = parent.strategy,
                interaction_class = parent.interaction_class,
                classification = parent.classification,
                retrieved_context = parent.retrieved_context,
            )
            parent.sub_task_ids.append(sub.id)
            tasks.append(sub)
        await asyncio.gather(*[self._execute(t) for t in tasks])
        return [t.antwort for t in tasks if t.antwort]

    def _aggregate(self, results: list[str]) -> str:
        if not results:
            return "[Keine Ergebnisse]"
        if len(results) == 1:
            return results[0]
        return "\n\n---\n\n".join(
            f"**Teil {i+1}:**\n{r}" for i, r in enumerate(results)
        )

    # ── System-Prompt ─────────────────────────────────────────────────────────
    def _build_system(self, task: Task) -> str:
        if task.system_prompt:
            return task.system_prompt
        basis = {
            TaskType.CHAT:      "Du bist Isaac. Antworte vollständig, präzise und strukturiert.",
            TaskType.SEARCH:    "Du bist ein Recherche-Synthesizer. Fasse Suchergebnisse vollständig zusammen.",
            TaskType.ANALYSIS:  "Du bist ein Analyse-Experte. Untersuche systematisch.",
            TaskType.TRANSLATE: "Du bist ein Experte für Sprachen und Schriften. Antworte präzise.",
            TaskType.CODE:      "Du bist ein Programmierer. Schreibe sauberen, lauffähigen Code.",
            TaskType.PLAN:      "Du bist ein strategischer Planer. Erstelle detaillierte Pläne.",
        }
        return basis.get(task.typ, "Du bist Isaac, ein autonomes KI-System.")

    # ── Persistenz ────────────────────────────────────────────────────────────
    def _persist_task(self, task: Task):
        """Speichert abgeschlossene Tasks in SQLite."""
        if task.status not in (TaskStatus.DONE, TaskStatus.FAILED):
            return
        try:
            from memory import get_memory
            get_memory().save_task_result(
                task_id     = task.id,
                description = task.beschreibung[:200],
                result      = task.antwort,
                score       = task.score.total if task.score else 0.0,
                iterations  = task.iteration + 1,
                provider    = task.provider_used,
            )
            from procedure_memory import record_task_outcome
            proc = record_task_outcome(task)
            if proc:
                task.decision_trace.add(
                    TracePhase.LEARNING,
                    "procedure_recorded",
                    {
                        "signature": str(proc.get("signature") or "")[:16],
                        "reliability": proc.get("reliability"),
                        "degraded": bool(proc.get("degraded")),
                        "success_count": proc.get("success_count"),
                        "failure_count": proc.get("failure_count"),
                    },
                )
                try:
                    self._checkpoint(
                        task,
                        CheckpointState.LEARNING_COMMIT,
                        current_prompt=task.prompt,
                        result_snapshot=build_result_snapshot(
                            antwort=task.antwort or "",
                            provider=task.provider_used or "",
                            score_total=task.score.total if task.score else None,
                            via="procedure_record",
                        ),
                    )
                except Exception:
                    pass
            # Goal-bound learning (Slice 3): Facts/Inquiries mit source=goal:{id}
            try:
                from goal_inquiry import record_goal_learning_from_task

                goal_learn = record_goal_learning_from_task(task)
                if goal_learn.get("ok") and goal_learn.get("goal_id"):
                    task.decision_trace.add(
                        TracePhase.LEARNING,
                        "goal_learning_recorded",
                        {
                            "goal_id": goal_learn.get("goal_id"),
                            "subgoal_id": goal_learn.get("subgoal_id"),
                            "facts": goal_learn.get("facts"),
                            "inquiries": goal_learn.get("inquiries"),
                        },
                    )
            except Exception as exc:
                log.debug("Goal-learning skip: %s", exc)
        except Exception as e:
            log.warning(f"Task-Persistenz: {e}")

    def _load_persisted_tasks(self):
        """Lädt die letzten abgeschlossenen Tasks als Dashboard-Historie."""
        try:
            from memory import _conn
            with _conn() as con:
                rows = con.execute(
                    "SELECT task_id, description, result, score, iterations, provider, ts FROM task_results ORDER BY id DESC LIMIT 50"
                ).fetchall()
            for row in reversed(rows):
                task = Task(
                    id=row["task_id"],
                    typ=TaskType.CHAT,
                    prompt=row["description"],
                    beschreibung=row["description"],
                )
                task.status = TaskStatus.DONE
                task.antwort = row["result"] or ""
                task.provider_used = row["provider"] or ""
                task.iteration = max(0, int((row["iterations"] or 1) - 1))
                task.abgeschlossen = row["ts"] or ""
                self._tasks.setdefault(task.id, task)
        except Exception as e:
            log.debug(f"Task-Historie konnte nicht geladen werden: {e}")

    # ── Callbacks ─────────────────────────────────────────────────────────────
    def register_callback(self, fn: Callable):
        self._callbacks.append(fn)

    def unregister_callback(self, fn: Callable):
        self._callbacks = [c for c in self._callbacks if c != fn]

    def _notify(self, task: Task):
        data = task.to_dict()
        try:
            loop = self._loop or asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        for cb in self._callbacks:
            try:
                if loop and loop.is_running():
                    loop.call_soon_threadsafe(asyncio.create_task, self._safe_cb(cb, data))
                else:
                    result = cb(data)
                    if asyncio.iscoroutine(result):
                        asyncio.run(result)
            except Exception as e:
                log.debug(f"Callback-Fehler: {e}")

    async def _safe_cb(self, cb: Callable, data: dict):
        try:
            result = cb(data)
            if asyncio.iscoroutine(result):
                await result
        except (RuntimeError, ValueError) as e:
            log.debug(f"Callback async Fehler: {e}")

    # ── Checkpoints / Resume ───────────────────────────────────────────────────
    def _mark_resumable(self, task: Task, reason: str) -> None:
        task.status = TaskStatus.RESUMABLE
        state = normalize_state(task.checkpoint_state) or CheckpointState.PLANNING
        if state not in CheckpointState.RESUMABLE:
            state = CheckpointState.EVALUATING
        self._checkpoint(
            task,
            state,
            result_snapshot=build_result_snapshot(
                antwort=task.antwort,
                provider=task.provider_used,
                score_total=task.score.total if task.score else None,
                partial=True,
                resume_reason=reason,
            ),
        )
        task.log(f"Resumable: {reason}")

    def _checkpoint(
        self,
        task: Task,
        state_name: str,
        *,
        current_prompt: str = "",
        tool_snapshot: dict | None = None,
        result_snapshot: dict | None = None,
        side_effect_refs: list | None = None,
        memory_refs: list | None = None,
    ) -> int:
        # Soft transition check — log only, never block execution.
        prev = normalize_state(getattr(task, "checkpoint_state", "") or "")
        new_norm = normalize_state(state_name)
        note = transition_note(prev, new_norm)
        if prev and not is_valid_transition(prev, new_norm, strict=False):
            # True graph break — warn. Soft_ok/preferred never reach here.
            log.warning(
                "checkpoint soft-invalid transition task=%s %s",
                task.id,
                note,
            )
        elif note.startswith("soft_ok:"):
            # Expected alternate path (resume, requeue) — debug only (C3 polish)
            log.debug("checkpoint soft_ok task=%s %s", task.id, note)
        # Persist normalized pipeline state (resume_requested → planning, etc.)
        store_state = new_norm or state_name
        input_snapshot = build_input_snapshot(task, current_prompt=current_prompt)
        cp_id = get_memory().save_task_checkpoint(
            task.id,
            store_state,
            input_snapshot=input_snapshot,
            tool_snapshot=tool_snapshot or {},
            result_snapshot=result_snapshot or {},
            memory_refs=memory_refs or [],
            side_effect_refs=side_effect_refs or [],
        )
        task.checkpoint_state = store_state
        task.checkpoint_ts = time.strftime("%Y-%m-%d %H:%M:%S")
        return cp_id

    def resume_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task or task.status != TaskStatus.RESUMABLE:
            return False
        if task_id in self._running:
            return False
        cp = get_memory().get_latest_checkpoint(task_id)
        task.log("Resume angefordert")
        # Meta-marker only for logs; pipeline state stays resumable/planning
        task.checkpoint_state = CheckpointState.PLANNING
        task.checkpoint_ts = time.strftime("%Y-%m-%d %H:%M:%S")
        task.resume_checkpoint_id = int(cp.get("checkpoint_id", 0)) if cp else 0
        task.resume_strategy = "from_checkpoint"
        task.status = TaskStatus.QUEUED
        task.provider = task.provider or "auto"
        try:
            self._queue.put_nowait((-task.prioritaet, task.id))
        except Exception:
            pass
        self._notify(task)
        return True

    @staticmethod
    def _checkpoint_json_field(raw: Any, default: Any) -> Any:
        if raw is None:
            return default
        if isinstance(raw, (dict, list)):
            return raw
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return default

    async def _resume_from_checkpoint(self, task: Task) -> str:
        cp = None
        if task.resume_checkpoint_id:
            cp = get_memory().get_checkpoint_by_id(task.resume_checkpoint_id)
        if not cp:
            cp = get_memory().get_latest_checkpoint(task.id)
        if not cp:
            return "none"

        state_name = normalize_state(cp.get("state_name", ""))
        if not is_resumable_state(state_name) and state_name not in {
            CheckpointState.DONE,
            CheckpointState.FAILED,
        }:
            return "none"

        task.log(f"Resume von Checkpoint: {state_name}")
        task.checkpoint_state = "resume_completed"
        task.checkpoint_ts = cp.get("ts", "")

        input_snap = self._checkpoint_json_field(cp.get("input_snapshot"), {})
        result_snap = self._checkpoint_json_field(cp.get("result_snapshot"), {})
        tool_snap = self._checkpoint_json_field(cp.get("tool_snapshot"), {})
        side_effect_refs = self._checkpoint_json_field(cp.get("side_effect_refs"), [])

        task.prompt = input_snap.get("prompt", task.prompt)
        task.iteration = int(input_snap.get("iteration", task.iteration) or 0)
        task.provider = input_snap.get("provider") or task.provider or "none"
        if input_snap.get("provider_used"):
            task.provider_used = input_snap.get("provider_used", "")
        current_prompt = input_snap.get("current_prompt") or task.prompt

        if state_name in {
            CheckpointState.PLANNING,
            CheckpointState.TOOL_PENDING,
            CheckpointState.TOOL_RUNNING,
        }:
            task.status = TaskStatus.RUNNING
            task.progress = max(task.progress, 0.2)
            if current_prompt and current_prompt != task.prompt:
                task.resume_current_prompt = current_prompt
            task.resume_start_iteration = task.iteration
            identifier = tool_snap.get("identifier", "")
            if identifier and not tool_snap.get("pending", True):
                task.resume_used_tool_ids = [identifier]
                tool_output = (
                    result_snap.get("answer_full")
                    or result_snap.get("answer_preview")
                    or ""
                )
                if tool_output or side_effect_refs:
                    task.resume_tool_context = self._tool_context_block(
                        tool_snap.get("tool", identifier),
                        tool_snap.get("kind", ""),
                        result_snap.get("via") or tool_snap.get("kind", ""),
                        {"output": tool_output},
                    )
            return "continue"

        if state_name == CheckpointState.EVALUATING:
            antwort = result_snap.get("answer_full") or result_snap.get("answer_preview") or ""
            prov = result_snap.get("provider") or task.provider_used or "none"
            if antwort:
                task.provider_used = prov
                task.antwort = antwort
                score = self.logic.evaluate(antwort, task.prompt, task.id)
                task.score = score
                if antwort.startswith("[RELAY"):
                    task.status = TaskStatus.FAILED
                    task.fehler = antwort[:200]
                    return "done"
                if score.acceptable:
                    task.status = TaskStatus.DONE
                    return "done"
                iteration = int(input_snap.get("iteration", task.iteration) or 0)
                max_rounds = get_config().logic.max_followup_rounds
                if iteration >= max_rounds or not task.allow_followup:
                    task.status = TaskStatus.FAILED
                    task.fehler = "Resume: Score nicht akzeptabel"
                    return "done"
                decision = self.logic.decide_followup(
                    antwort, task.prompt, score, iteration, prov, task.id,
                )
                if not decision.needed:
                    task.status = TaskStatus.FAILED
                    task.fehler = "Resume: Score nicht akzeptabel"
                    return "done"
                task.status = TaskStatus.RUNNING
                task.resume_start_iteration = iteration + 1
                task.resume_current_prompt = (
                    decision.followup_prompt or current_prompt or task.prompt
                )
                return "continue"

            task.status = TaskStatus.RUNNING
            system = self._build_system(task)
            try:
                antwort, prov = await asyncio.wait_for(
                    self.relay.ask_with_fallback(
                        current_prompt or task.prompt,
                        system,
                        preferred=task.provider,
                        task_id=task.id,
                    ),
                    timeout=8.0,
                )
            except asyncio.TimeoutError:
                antwort, prov = "[RELAY] Alle Provider fehlgeschlagen.", "none"
            task.provider_used = prov
            task.antwort = antwort
            score = self.logic.evaluate(antwort, task.prompt, task.id)
            task.score = score
            if antwort.startswith("[RELAY") or not score.acceptable:
                task.status = TaskStatus.FAILED
            else:
                task.status = TaskStatus.DONE
            return "done"

        if state_name == CheckpointState.LEARNING_COMMIT:
            antwort = result_snap.get("answer_full") or task.antwort
            if antwort:
                task.antwort = antwort
                task.status = TaskStatus.DONE
            else:
                task.status = TaskStatus.FAILED
                task.fehler = "Resume: learning_commit ohne Antwort"
            return "done"

        if state_name == CheckpointState.DONE:
            task.antwort = result_snap.get("answer_full") or task.antwort
            task.status = TaskStatus.DONE
            return "done"

        if state_name == CheckpointState.FAILED:
            task.antwort = result_snap.get("answer_full") or task.antwort
            task.status = TaskStatus.FAILED
            task.fehler = result_snap.get("resume_reason") or task.fehler
            return "done"

        return "none"

    # ── Abfragen ─────────────────────────────────────────────────────────────
    def get_task(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def get_task_graph(self, root_task_id: str) -> dict:
        root = self.get_task(root_task_id)
        if not root:
            return {}
        nodes = {}
        visited = set()

        def _walk(tid: str):
            if tid in visited:
                return
            visited.add(tid)
            t = self.get_task(tid)
            if not t:
                return
            nodes[tid] = t.to_graph_node()
            for stid in t.sub_task_ids:
                _walk(stid)
            for dep_id in t.dependencies:
                _walk(dep_id)

        _walk(root_task_id)
        return {
            "root_id": root_task_id,
            "node_count": len(nodes),
            "nodes": nodes,
        }

    def validate_task_graph(self, root_task_id: str) -> dict[str, Any]:
        """Validates DAG integrity for a task graph (detects cycles, missing dependencies)."""
        graph = self.get_task_graph(root_task_id)
        if not graph:
            return {"is_valid": False, "has_cycle": False, "missing_dependencies": [], "error": "Root task not found"}

        nodes = graph.get("nodes", {})
        missing_deps = []

        for tid, node in nodes.items():
            for dep_id in node.get("dependencies", []):
                if dep_id not in self._tasks:
                    missing_deps.append({"task_id": tid, "missing_dep": dep_id})

        visited = {}
        has_cycle = False
        cycle_path = []

        def _dfs(tid: str, path: list[str]) -> bool:
            nonlocal has_cycle, cycle_path
            visited[tid] = 1
            path.append(tid)

            t = self.get_task(tid)
            if t:
                neighbors = list(t.dependencies) + list(t.sub_task_ids)
                for neighbor in neighbors:
                    if neighbor not in self._tasks:
                        continue
                    if visited.get(neighbor, 0) == 1:
                        has_cycle = True
                        cycle_path = list(path) + [neighbor]
                        return True
                    elif visited.get(neighbor, 0) == 0:
                        if _dfs(neighbor, path):
                            return True

            path.pop()
            visited[tid] = 2
            return False

        for tid in nodes:
            if visited.get(tid, 0) == 0:
                if _dfs(tid, []):
                    break

        is_valid = (not has_cycle) and (len(missing_deps) == 0)
        return {
            "is_valid": is_valid,
            "has_cycle": has_cycle,
            "cycle_path": cycle_path,
            "missing_dependencies": missing_deps,
            "node_count": len(nodes),
        }

    def are_dependencies_satisfied(self, task_id: str) -> bool:
        """Checks if all prerequisite dependency tasks are completed."""
        task = self.get_task(task_id)
        if not task:
            return False
        for dep_id in task.dependencies:
            dep_task = self.get_task(dep_id)
            if not dep_task or dep_task.status not in (TaskStatus.DONE, "completed", "done"):
                return False
        return True

    def get_executable_tasks(self) -> list[Task]:
        """Returns all pending tasks whose dependencies are satisfied."""
        executable = []
        for task in self._tasks.values():
            if task.status in (TaskStatus.QUEUED, "queued", "pending", TaskStatus.RESUMABLE) and self.are_dependencies_satisfied(task.id):
                executable.append(task)
        return executable

    def all_tasks(self, limit: int = 200) -> list[dict]:
        return [
            t.to_dict()
            for t in sorted(self._tasks.values(),
                            key=lambda t: t.erstellt, reverse=True)[:limit]
        ]

    def running_tasks(self) -> list[dict]:
        return [
            t.to_dict() for t in self._tasks.values()
            if t.status in (TaskStatus.RUNNING, TaskStatus.EVALUATING,
                            TaskStatus.FOLLOWUP)
        ]

    @property
    def _queue(self) -> asyncio.PriorityQueue:
        try:
            curr_loop = asyncio.get_running_loop()
        except RuntimeError:
            curr_loop = None

        if self._queue_obj is None or (curr_loop is not None and self._queue_loop != curr_loop):
            self._queue_obj = asyncio.PriorityQueue()
            self._queue_loop = curr_loop

        return self._queue_obj

    @_queue.setter
    def _queue(self, val: Any) -> None:
        self._queue_obj = val

    def queue_size(self) -> int:
        return self._queue.qsize()

    def stats(self) -> dict:
        alle = list(self._tasks.values())
        by_status: dict[str, int] = {}
        for t in alle:
            k = t.status.value
            by_status[k] = by_status.get(k, 0) + 1
        scored = [t.score.total for t in alle if t.score]
        return {
            "total":     len(alle),
            "running":   len(self._running),
            "queue":     self.queue_size(),
            "by_status": by_status,
            "avg_score": round(sum(scored)/len(scored), 2) if scored else 0.0,
        }


_executor: Optional[Executor] = None

def get_executor() -> Executor:
    global _executor
    if _executor is None:
        _executor = Executor()
    return _executor
