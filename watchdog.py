"""
Isaac – Watchdog + Adaptives Provider-Blacklisting
=====================================================
Zwei Aufgaben:

1. WATCHDOG
   Überwacht alle laufenden Tasks.
   Erkennt hängende Tasks (kein Progress-Update in N Sekunden).
   Bricht sie ab und plant sie neu.

2. PROVIDER-BLACKLIST
   Trackt Fehlerquoten pro Provider.
   Blacklistet Provider temporär bei zu vielen Fehlern.
   Erholt sich automatisch nach Cooldown-Zeit.
   Bevorzugt zuverlässige Provider in der Fallback-Kette.
"""
from __future__ import annotations

import asyncio
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

from config import get_config
from audit  import AuditLog

log = logging.getLogger("Isaac.Watchdog")


# ── Provider-Statistik ────────────────────────────────────────────────────────
@dataclass
class ProviderStats:
    name:           str
    erfolge:        int   = 0
    fehler:         int   = 0
    timeouts:       int   = 0
    letzter_fehler: float = 0.0    # time.monotonic()
    blacklisted:    bool  = False
    blacklist_bis:  float = 0.0    # time.monotonic()
    avg_latenz:     float = 0.0    # Sekunden
    _latenz_n:      int   = 0

    @property
    def fehlerquote(self) -> float:
        total = self.erfolge + self.fehler
        return self.fehler / total if total > 0 else 0.0

    @property
    def verfuegbar(self) -> bool:
        if not self.blacklisted:
            return True
        # Blacklist abgelaufen?
        if time.monotonic() > self.blacklist_bis:
            self.blacklisted = False
            log.info(f"Provider {self.name}: Blacklist abgelaufen → wiederhergestellt")
            return True
        return False

    def update_latenz(self, sek: float):
        self._latenz_n += 1
        # Exponentieller gleitender Durchschnitt
        alpha = 2 / (self._latenz_n + 1)
        self.avg_latenz = alpha * sek + (1 - alpha) * self.avg_latenz

    def score(self) -> float:
        """
        Ranking-Score für Fallback-Reihenfolge.
        Höher = bevorzugter.
        Berücksichtigt: Fehlerquote, Latenz, Verfügbarkeit.
        """
        if not self.verfuegbar:
            return -1.0
        basis = 10.0
        basis -= self.fehlerquote * 5.0
        basis -= min(3.0, self.avg_latenz * 0.3)
        basis += min(2.0, self.erfolge * 0.01)
        return max(0.0, basis)

    def to_dict(self) -> dict:
        return {
            "name":        self.name,
            "erfolge":     self.erfolge,
            "fehler":      self.fehler,
            "timeouts":    self.timeouts,
            "fehlerquote": round(self.fehlerquote, 3),
            "blacklisted": self.blacklisted,
            "avg_latenz":  round(self.avg_latenz, 2),
            "score":       round(self.score(), 2),
            "verfuegbar":  self.verfuegbar,
        }


# ── Provider-Blacklist ────────────────────────────────────────────────────────
class ProviderBlacklist:
    """
    Adaptives Blacklisting.
    Lernt welche Provider zuverlässig sind.
    """

    # Schwellwerte
    MAX_FEHLERQUOTE  = 0.5    # Ab 50% Fehler → Blacklist
    MIN_ANFRAGEN     = 5      # Mindest-Anfragen bevor Blacklist greift
    BLACKLIST_BASIS  = 60.0   # Sekunden initiale Sperrzeit
    BLACKLIST_MAX    = 3600.0 # Maximal 1 Stunde
    FEHLER_COOLDOWN  = 300.0  # 5 Min ohne Fehler → Rate sinkt

    def __init__(self):
        self._stats: dict[str, ProviderStats] = {}
        self._init_providers()

    def _init_providers(self):
        for name in get_config().providers:
            self._stats[name] = ProviderStats(name=name)

    def record_success(self, provider: str, latenz: float):
        s = self._get(provider)
        s.erfolge += 1
        s.update_latenz(latenz)
        # Fehlerquote bessert sich → Blacklist früher aufheben
        if s.blacklisted and s.fehlerquote < 0.3:
            s.blacklisted   = False
            s.blacklist_bis = 0.0
            log.info(f"Provider {provider}: Früh-Rehabilitation (Fehlerquote ok)")

    def record_failure(self, provider: str, is_timeout: bool = False):
        s = self._get(provider)
        s.fehler += 1
        s.letzter_fehler = time.monotonic()
        if is_timeout:
            s.timeouts += 1

        total = s.erfolge + s.fehler
        if (total >= self.MIN_ANFRAGEN and
                s.fehlerquote >= self.MAX_FEHLERQUOTE and
                not s.blacklisted):
            # Exponentiell steigende Sperrzeit
            stufe = min(s.fehler // self.MIN_ANFRAGEN, 6)
            dauer = min(self.BLACKLIST_BASIS * (2 ** stufe),
                        self.BLACKLIST_MAX)
            s.blacklisted   = True
            s.blacklist_bis = time.monotonic() + dauer
            log.warning(
                f"Provider {provider} blacklisted für "
                f"{int(dauer)}s (Fehlerquote: {s.fehlerquote:.1%})"
            )
            AuditLog.action(
                "Watchdog", f"blacklist:{provider}",
                f"quote={s.fehlerquote:.1%} dauer={int(dauer)}s"
            )

    def ranked_providers(self, preferred: Optional[str] = None) -> list[str]:
        """
        Gibt Provider in optimierter Reihenfolge zurück.
        Preferred kommt zuerst (wenn verfügbar).
        Danach nach Score sortiert.
        """
        verfuegbar = [
            s for s in self._stats.values()
            if s.verfuegbar
        ]
        sorted_provs = sorted(verfuegbar, key=lambda s: s.score(), reverse=True)
        names = [s.name for s in sorted_provs]

        if preferred and preferred in names:
            names.remove(preferred)
            names.insert(0, preferred)

        return names

    def is_available(self, provider: str) -> bool:
        return self._get(provider).verfuegbar

    def _get(self, name: str) -> ProviderStats:
        if name not in self._stats:
            self._stats[name] = ProviderStats(name=name)
        return self._stats[name]

    def all_stats(self) -> list[dict]:
        return [s.to_dict() for s in self._stats.values()]

    def best_available(self) -> Optional[str]:
        ranked = self.ranked_providers()
        return ranked[0] if ranked else None


# ── Task-Watchdog ─────────────────────────────────────────────────────────────
class TaskWatchdog:
    """
    Überwacht laufende Tasks.
    Erkennt hängende Tasks und bricht sie ab.
    Plant kritische Tasks automatisch neu.
    """

    HANG_TIMEOUT    = 300.0   # Sekunden ohne Progress → Task hängt (Ollama auf Mobile braucht länger)
    CHECK_INTERVAL  = 15.0    # Wie oft prüfen
    MAX_RESTARTS    = 2       # Maximale Neustart-Versuche

    def __init__(self):
        self._executor    = None   # Wird später gesetzt (circular import vermeiden)
        self._restarts:   dict[str, int] = {}   # task_id → Anzahl Neustarts
        self._last_progress: dict[str, float] = {}  # task_id → time.monotonic()
        self._running     = False
        self._task:       Optional[asyncio.Task] = None
        log.info("Watchdog initialisiert")

    def set_executor(self, executor):
        self._executor = executor

    async def start(self):
        self._running = True
        self._task    = asyncio.create_task(self._loop())
        log.info(f"Watchdog gestartet (Check alle {self.CHECK_INTERVAL}s)")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self):
        while self._running:
            try:
                await asyncio.sleep(self.CHECK_INTERVAL)
                await self._check()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Watchdog-Fehler: {e}")

    async def _check(self):
        if not self._executor:
            return

        now = time.monotonic()
        running = self._executor.running_tasks()

        for task_dict in running:
            task_id = task_dict.get("id")
            if not task_id:
                continue

            progress = task_dict.get("progress", 0.0)
            status   = task_dict.get("status", "")

            # Progress tracken
            last = self._last_progress.get(task_id)
            current_progress = progress

            if last is None:
                self._last_progress[task_id] = now
                continue

            # Progress hat sich nicht verändert?
            task_obj = self._executor.get_task(task_id)
            if not task_obj:
                continue

            letzte_aktivitaet = self._last_progress.get(task_id, now)
            if current_progress != getattr(task_obj, '_last_watchdog_progress', -1):
                task_obj._last_watchdog_progress = current_progress
                self._last_progress[task_id]     = now
                continue

            # Kein Progress seit HANG_TIMEOUT?
            inaktiv = now - letzte_aktivitaet
            if inaktiv > self.HANG_TIMEOUT:
                await self._handle_hang(task_obj, inaktiv)

    async def _handle_hang(self, task, inaktiv: float):
        from executor import TaskStatus
        from memory import get_memory
        from task_checkpoint import is_resumable_state, normalize_state

        task_id  = task.id
        restarts = self._restarts.get(task_id, 0)

        log.warning(
            f"Task {task_id} hängt seit {int(inaktiv)}s "
            f"(Restarts: {restarts}/{self.MAX_RESTARTS})"
        )
        AuditLog.action(
            "Watchdog", "hang_detected", task_id,
            erfolg=False
        )

        cp = get_memory().get_latest_checkpoint(task_id)
        cp_state = normalize_state((cp or {}).get("state_name", ""))
        if cp and is_resumable_state(cp_state) and self._executor:
            self._restarts[task_id] = restarts + 1
            if task_id in self._executor._running:
                task.watchdog_resume_pending = True
                task.status = TaskStatus.CANCELLED
                task.fehler = f"Watchdog: hängt seit {int(inaktiv)}s"
                task.log(
                    f"Watchdog: hängende Ausführung abbrechen, "
                    f"Resume von Checkpoint ({cp_state}) folgt"
                )
                self._last_progress[task_id] = time.monotonic()
                log.info(f"Task {task_id} Abbruch für Checkpoint-Resume angefordert")
                AuditLog.action("Watchdog", "task_abort_for_resume", task_id)
                return
            task.status = TaskStatus.RESUMABLE
            task.fehler = f"Watchdog: hängt seit {int(inaktiv)}s"
            task.log(f"Watchdog: Resume von Checkpoint ({cp_state})")
            self._last_progress[task_id] = time.monotonic()
            if self._executor.resume_task(task_id):
                log.info(f"Task {task_id} aus Checkpoint fortgesetzt")
                AuditLog.action("Watchdog", "task_resumed_from_checkpoint", task_id)
                return

        if restarts < self.MAX_RESTARTS:
            # Task neustarten
            task.status   = TaskStatus.QUEUED
            task.progress = 0.0
            task.log(f"Watchdog: Neustart nach {int(inaktiv)}s Inaktivität")
            self._restarts[task_id] = restarts + 1
            self._last_progress[task_id] = time.monotonic()

            # Provider wechseln beim Neustart
            from relay import get_relay
            ranked = get_blacklist().ranked_providers(task.provider)
            if ranked and ranked[0] != task.provider:
                task.provider = ranked[0]
                task.log(f"Watchdog: Provider → {task.provider}")

            await self._executor.submit(task)
            log.info(f"Task {task_id} neugestartet")
            AuditLog.action("Watchdog", "task_restarted", task_id)
        else:
            # Aufgeben
            task.status = TaskStatus.FAILED
            task.fehler = f"Watchdog: Max Restarts ({self.MAX_RESTARTS}) erreicht"
            task.log(task.fehler)
            log.error(f"Task {task_id} aufgegeben nach {self.MAX_RESTARTS} Neustarts")
            AuditLog.error("Watchdog", "task_abandoned", task_id)

    def record_progress(self, task_id: str):
        """Wird vom Executor aufgerufen wenn ein Task Fortschritt macht."""
        self._last_progress[task_id] = time.monotonic()

    def stats(self) -> dict:
        return {
            "running":  self._running,
            "restarts": sum(self._restarts.values()),
            "hung_tasks": len([
                t for t, ts in self._last_progress.items()
                if time.monotonic() - ts > self.HANG_TIMEOUT
            ]),
        }


# ── Singletons ─────────────────────────────────────────────────────────────────
_blacklist: Optional[ProviderBlacklist] = None
_watchdog:  Optional[TaskWatchdog]      = None

def get_blacklist() -> ProviderBlacklist:
    global _blacklist
    if _blacklist is None:
        _blacklist = ProviderBlacklist()
    return _blacklist

def get_watchdog() -> TaskWatchdog:
    global _watchdog
    if _watchdog is None:
        _watchdog = TaskWatchdog()
    return _watchdog
