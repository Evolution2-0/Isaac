"""
Isaac – Privilege System
========================
STEFFEN (100) > ISAAC (70) > TASK (40) > GUEST (10)

Kein Modul kann sich selbst höhere Rechte geben.
Steffen-Direktiven werden persistent gespeichert und haben
immer Vorrang vor Isaac-eigenen Entscheidungen.

Jede privilegierte Aktion benötigt:
  1. Einen Caller (wer ruft an?)
  2. Einen R-Trace (warum?)
  3. Das benötigte Recht
"""
from __future__ import annotations

import time
import functools
import logging
from dataclasses import dataclass
from typing import Callable, Optional, Any

from config import get_config, Level, is_owner_equivalent_mode

log = logging.getLogger("Isaac.Privilege")


# ── Rechte-Definitionen ────────────────────────────────────────────────────────
RECHTE: dict[str, int] = {
    # Aktion                     : Mindest-Level
    "read_memory":               Level.GUEST,
    "read_audit":                Level.GUEST,
    "chat_response":             Level.TASK,
    "write_memory":              Level.TASK,
    "internet_search":           Level.TASK,
    "browser_navigate":          Level.TASK,
    "send_to_instance":          Level.TASK,
    "file_read":                 Level.ISAAC,
    "file_write":                Level.ISAAC,
    "file_delete":               Level.ISAAC,
    "spawn_instance":            Level.ISAAC,
    "kill_instance":             Level.ISAAC,
    "execute_code":              Level.ISAAC,
    "system_command":            Level.ISAAC,
    "modify_config":             Level.STEFFEN,
    "pause_isaac":               Level.STEFFEN,
    "resume_isaac":              Level.STEFFEN,
    "wipe_memory":               Level.STEFFEN,
    "override_directive":        Level.STEFFEN,
    "grant_privilege":           Level.STEFFEN,
}


# ── Privilege-Context ──────────────────────────────────────────────────────────
@dataclass
class PrivCtx:
    """Kontext einer privilegierten Aktion."""
    caller:    str       # z.B. "Kernel", "Executor", "LogicModule"
    level:     int       # Level des Callers
    r_trace:   str       # Begründung
    timestamp: str = ""  # wird automatisch gesetzt

    def __post_init__(self):
        self.timestamp = time.strftime("%Y-%m-%d %H:%M:%S")


# ── Steffen-Direktive ──────────────────────────────────────────────────────────
@dataclass
class Directive:
    """Eine permanente Anweisung von Steffen."""
    id:          str
    text:        str
    timestamp:   str
    active:      bool = True
    priority:    int  = 10   # Höher = wichtiger bei Konflikten


# ── Privilege-Gate ────────────────────────────────────────────────────────────
class PrivilegeGate:
    """
    Zentrale Autorisierungsinstanz.
    Wird einmal instantiiert und als Singleton verwendet.
    Isaac kann dieses Objekt nicht ersetzen oder umgehen.
    """

    def __init__(self):
        self._directives: list[Directive] = []
        self._paused:     bool            = False
        self._audit_fn:   Optional[Callable] = None   # wird von audit.py gesetzt
        log.info(f"PrivilegeGate aktiv │ Owner: {get_config().owner_name}")
        if is_owner_equivalent_mode():
            log.info("OWNER_EQUIVALENT mode aktiv — volle Geräteäquivalenz (Audit bleibt)")

    def set_audit_fn(self, fn: Callable):
        """Audit-Hook registrieren (von audit.py aufgerufen)."""
        self._audit_fn = fn

    # ── Autorisierung ─────────────────────────────────────────────────────────
    def authorize(self, aktion: str, ctx: PrivCtx) -> tuple[bool, str]:
        """
        Gibt (erlaubt, grund) zurück.
        Wird von @require_privilege aufgerufen.
        
        Im Admin-Modus: Alle Aktionen sind sofort erlaubt (logging bleibt aktiv).
        """
        # Admin-Modus: Alle Aktionen direkt erlaubt (aber trotzdem gelogged)
        if get_config().privilege_mode == "admin":
            self._log_decision(aktion, ctx, True, "ADMIN_MODE (vorautorisiert)")
            return True, "ADMIN_MODE"
        
        # Paused-Check (nur Steffen darf noch agieren)
        if self._paused and ctx.level < Level.STEFFEN:
            grund = "Isaac ist pausiert. Nur Steffen-Level erlaubt."
            self._log_decision(aktion, ctx, False, grund)
            return False, grund

        # Aktion bekannt?
        benötigt = RECHTE.get(aktion)
        if benötigt is None:
            # Unbekannte Aktion → Default: ISAAC-Level nötig
            benötigt = Level.ISAAC
            log.warning(f"Unbekannte Aktion '{aktion}' → ISAAC-Level erforderlich")

        # Level-Check
        if ctx.level < benötigt:
            level_name = self._level_name(ctx.level)
            need_name  = self._level_name(benötigt)
            grund = (f"'{ctx.caller}' hat Level {level_name} ({ctx.level}), "
                     f"benötigt {need_name} ({benötigt}) für '{aktion}'")
            self._log_decision(aktion, ctx, False, grund)
            return False, grund

        # R-Trace-Check
        if len(ctx.r_trace.strip()) < 15:
            grund = f"R-Trace zu kurz für Aktion '{aktion}': '{ctx.r_trace}'"
            self._log_decision(aktion, ctx, False, grund)
            return False, grund

        # Direktiven-Check
        block_direktive = self._check_direktiven(aktion, ctx)
        if block_direktive:
            self._log_decision(aktion, ctx, False, block_direktive)
            return False, block_direktive

        if ctx.level < Level.STEFFEN and aktion in {
            "execute_code", "system_command", "file_delete", "wipe_memory", "modify_config",
        }:
            from security_policy import get_confirmation_policy

            metadata = {
                "risk": "high" if aktion in {"execute_code", "system_command", "wipe_memory"} else "medium",
                "outside_effect": aktion in {"execute_code", "system_command", "file_delete", "wipe_memory"},
            }
            verdict = get_confirmation_policy().analyze(aktion, ctx, metadata)
            if not verdict.allowed:
                self._log_decision(aktion, ctx, False, verdict.reason)
                return False, verdict.reason

        # Constitution-Kopplung für kritische Aktionen (nach Level/Confirmation).
        if aktion in {
            "execute_code", "system_command", "file_delete", "wipe_memory", "modify_config",
        }:
            constitution_block = self._constitution_gate_action(aktion, ctx)
            if constitution_block:
                self._log_decision(aktion, ctx, False, constitution_block)
                return False, constitution_block

        self._log_decision(aktion, ctx, True, "OK")
        return True, "OK"

    def _constitution_gate_action(self, aktion: str, ctx: PrivCtx) -> Optional[str]:
        """Mappt Privilege-Aktionen auf Verfassungs-Urteile (Owner/STEFFEN freigeben)."""
        try:
            from constitution_override import apply_constitution_gate, build_override_context
        except Exception as exc:
            log.debug("Constitution-Gate Import: %s", exc)
            return None

        owner = is_owner_equivalent_mode() or ctx.level >= Level.STEFFEN
        action_map = {
            "execute_code": "execute_code",
            "system_command": "system_command",
            "file_delete": "file_delete",
            "wipe_memory": "modify_config",
            "modify_config": "modify_config",
        }
        action = action_map.get(aktion, aktion)
        metadata = {
            "outside_effect": True,
            "audit_logged": True,
            "risk": "high",
            "owner_approved": owner,
            "destructive": aktion in {"file_delete", "wipe_memory", "system_command"},
        }
        # system_command ohne destruktives Flag nur high_impact warning — destructive
        # hier nur wenn Aktion selbst destruktiv ist (nicht jeder shell-call über privilege).
        if aktion == "system_command":
            metadata["destructive"] = False
        gate = apply_constitution_gate(
            action,
            metadata,
            build_override_context(
                source="privilege.authorize",
                caller_level=int(ctx.level),
                owner_confirmed=owner,
                sudo_active=False,
                override_reason="owner_or_steffen" if owner else "",
            ),
        )
        if gate.get("allowed"):
            return None
        blocked = ", ".join(gate.get("blocked_by") or [])
        return f"Verfassung blockiert '{aktion}': {blocked}"

    def require(self, aktion: str, ctx: PrivCtx) -> None:
        """Wie authorize(), wirft aber Exception wenn verweigert."""
        ok, grund = self.authorize(aktion, ctx)
        if not ok:
            raise PermissionError(f"[PRIV] {grund}")

    # ── Direktiven ─────────────────────────────────────────────────────────────
    def add_directive(self, text: str, priority: int = 10) -> Directive:
        """Steffen fügt eine permanente Direktive hinzu."""
        d = Directive(
            id=f"DIR-{int(time.time())}",
            text=text,
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
            priority=priority,
        )
        self._directives.append(d)
        log.info(f"Direktive hinzugefügt: [{d.id}] {text[:80]}")
        return d

    def seed_directive(self, directive_id: str, text: str,
                       priority: int = 10) -> Directive:
        """Fügt eine Direktive mit stabiler ID hinzu (idempotent, kein Duplikat)."""
        for d in self._directives:
            if d.id == directive_id:
                return d
        d = Directive(
            id=directive_id,
            text=text,
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
            priority=priority,
        )
        self._directives.append(d)
        log.info(f"Direktive hinzugefügt: [{d.id}] {text[:80]}")
        return d

    def revoke_directive(self, directive_id: str) -> bool:
        for d in self._directives:
            if d.id == directive_id:
                d.active = False
                log.info(f"Direktive widerrufen: {directive_id}")
                return True
        return False

    def active_directives(self) -> list[Directive]:
        return [d for d in self._directives if d.active]

    def directives_as_context(self) -> str:
        """Gibt aktive Direktiven als lesbaren Kontext-Block zurück."""
        aktive = self.active_directives()
        if not aktive:
            return ""
        lines = ["[Steffen-Direktiven]"]
        for d in sorted(aktive, key=lambda x: x.priority, reverse=True):
            lines.append(f"  [{d.priority}] {d.text}")
        return "\n".join(lines)

    # ── Pause / Resume ─────────────────────────────────────────────────────────
    def pause(self, ctx: PrivCtx):
        ok, grund = self.authorize("pause_isaac", ctx)
        if ok:
            self._paused = True
            log.warning(f"ISAAC PAUSIERT von {ctx.caller}")

    def resume(self, ctx: PrivCtx):
        ok, grund = self.authorize("resume_isaac", ctx)
        if ok:
            self._paused = False
            log.info(f"Isaac resumed von {ctx.caller}")

    @property
    def is_paused(self) -> bool:
        return self._paused

    # ── Hilfsmethoden ──────────────────────────────────────────────────────────
    def _check_direktiven(self, aktion: str, ctx: PrivCtx) -> Optional[str]:
        """Prüft ob eine aktive Direktive die Aktion blockiert."""
        for d in self.active_directives():
            # Einfaches Keyword-Matching (erweiterbar)
            text_lower = d.text.lower()
            if "keine" in text_lower and aktion.replace("_", " ") in text_lower:
                return f"Direktive [{d.id}] blockiert: {d.text[:80]}"
        return None

    def _log_decision(self, aktion: str, ctx: PrivCtx, erlaubt: bool, grund: str):
        if self._audit_fn:
            self._audit_fn({
                "typ":     "privilege",
                "aktion":  aktion,
                "caller":  ctx.caller,
                "level":   ctx.level,
                "erlaubt": erlaubt,
                "grund":   grund,
                "trace":   ctx.r_trace[:100],
            })

    @staticmethod
    def _level_name(level: int) -> str:
        if level >= Level.STEFFEN: return "STEFFEN"
        if level >= Level.ISAAC:   return "ISAAC"
        if level >= Level.TASK:    return "TASK"
        return "GUEST"

    def status_dict(self) -> dict:
        return {
            "paused":     self._paused,
            "directives": len(self.active_directives()),
            "owner":      get_config().owner_name,
        }


# ── Decorator ─────────────────────────────────────────────────────────────────
def require_privilege(aktion: str, caller: str, level: int, trace: str):
    """
    Decorator für privilegierte Methoden.

    @require_privilege("file_write", "Executor", Level.ISAAC, "Task schreibt Ergebnis")
    def speichere_ergebnis(self, ...): ...
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            gate = get_gate()
            ctx  = PrivCtx(caller=caller, level=level, r_trace=trace)
            gate.require(aktion, ctx)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ── Singleton ──────────────────────────────────────────────────────────────────
_gate: Optional[PrivilegeGate] = None

def get_gate() -> PrivilegeGate:
    global _gate
    if _gate is None:
        _gate = PrivilegeGate()
    return _gate


# ── Convenience: Steffen-Context ───────────────────────────────────────────────
def steffen_ctx(trace: str) -> PrivCtx:
    return PrivCtx(caller=get_config().owner_name,
                   level=Level.STEFFEN, r_trace=trace)

def isaac_ctx(caller: str, trace: str) -> PrivCtx:
    return PrivCtx(caller=caller, level=Level.ISAAC, r_trace=trace)

def task_ctx(task_id: str, trace: str) -> PrivCtx:
    return PrivCtx(caller=f"Task:{task_id}", level=Level.TASK, r_trace=trace)
