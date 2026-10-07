"""
Isaac – Background Loop v3.1-mobile
======================================
Mobile-optimiert:
  - Alle Intervalle verdoppelt/verdreifacht
  - Intensive Operationen nur bei Ladegerät angeschlossen
  - Akku-Sparmodus ab 30% (vorher 20%)
  - Kein paralleles Ollama im Background
"""
from __future__ import annotations

import asyncio
import time
import logging
import json
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

from config    import get_config, DATA_DIR
from audit     import AuditLog
from regelwerk import get_regelwerk
from ki_dialog import get_ki_dialog

log = logging.getLogger("Isaac.Background")

STATE_PATH = DATA_DIR / "background_state.json"


@dataclass
class BackgroundState:
    letzter_health:     float = 0.0
    letzter_knowledge:  float = 0.0
    letzter_ki_dialog:  float = 0.0
    letzter_diskussion: float = 0.0
    letzter_research:   float = 0.0
    letzter_dump:       float = 0.0
    letzter_decay:      float = 0.0
    letzter_ideen:      float = 0.0
    letzter_provider:   float = 0.0
    letzter_owner_autonomy: float = 0.0
    letzter_goal_autonomy: float = 0.0
    letzter_mission: float = 0.0
    letzter_remote_smoke: float = 0.0
    owner_task_last_run: dict = field(default_factory=dict)
    goal_autonomy_ticks: int = 0
    mission_ticks: int = 0
    remote_smoke_ticks: int = 0
    zyklen:             int   = 0
    dialoge_gesamt:     int   = 0
    diskussionen_gesamt: int  = 0
    ideen_gesamt:       int   = 0


@dataclass
class Idee:
    idee_id:     str
    summary:     str
    value_score: float
    topic:       str
    source:      str
    ts:          str  = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
    gemeldet:    bool = False


class BackgroundLoop:

    # MOBILE: Alle Intervalle deutlich größer
    HEALTH_INTERVAL     = 120      # 2 Minuten
    PROVIDER_INTERVAL   = 1800     # 30 Minuten
    KNOWLEDGE_INTERVAL  = 600      # 10 Minuten
    IDEEN_INTERVAL      = 900      # 15 Minuten
    KI_DIALOG_INTERVAL  = 1200     # 20 Minuten
    DISKUSSION_INTERVAL = 1800     # 30 Minuten
    RESEARCH_INTERVAL   = 2400     # 40 Minuten
    DUMP_INTERVAL       = 600      # 10 Minuten
    DECAY_INTERVAL      = 3600     # 1 Stunde
    OWNER_AUTONOMY_INTERVAL = 3600  # 1 Stunde
    GOAL_AUTONOMY_INTERVAL = 900    # 15 Minuten — Ziel-Motivation
    MISSION_INTERVAL = 600          # 10 Minuten — Execution-Contract Missionen
    # Remote smoke keep-alive: poll tick often; remote_smoke module enforces <15m wake
    REMOTE_SMOKE_TICK = 60          # 1 Minute — prüft ob wake/full fällig
    TICK                = 30       # MOBILE: Größerer Tick-Abstand

    # MOBILE: Sparmodus ab 30%
    AKKU_MULTIPLIKATOR = 3
    AKKU_MIN_PROZENT   = 30

    DISKUSSIONS_THEMEN = [
        "Wie entwickelt sich künstliche Intelligenz in den nächsten 10 Jahren?",
        "Was sind die größten ethischen Herausforderungen bei autonomen KI-Systemen?",
        "Wie unterscheiden sich verschiedene Ansätze des maschinellen Lernens?",
        "Welche Rolle spielen Emergenz und Selbstorganisation in komplexen Systemen?",
        "Wie kann Vertrauen zwischen Menschen und KI-Systemen aufgebaut werden?",
        "Welche Protokolle brauchen KIs für effiziente gegenseitige Kommunikation?",
    ]

    def __init__(self):
        self.state        = self._load_state()
        self._running     = False
        self._task:       Optional[asyncio.Task] = None
        self._kernel      = None
        self._puffer:     list = []
        self._ideenqueue: list = []
        self._themen_idx  = 0
        log.info("BackgroundLoop v3.1-mobile initialisiert")

    def set_kernel(self, kernel):
        self._kernel = kernel

    async def start(self):
        self._running = True
        self._task    = asyncio.create_task(self._loop())
        log.info(f"BackgroundLoop gestartet (Tick: {self.TICK}s)")
        AuditLog.action("Background", "start", "BackgroundLoop v3.1-mobile aktiv")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass
        self._dump_state()
        log.info("BackgroundLoop gestoppt")

    async def _akku_status(self) -> dict:
        try:
            proc = await asyncio.create_subprocess_exec(
                "termux-battery-status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
            data    = json.loads(stdout.decode())
            plugged = data.get("plugged", "UNPLUGGED") != "UNPLUGGED"
            prozent = int(data.get("percentage", 100))
            return {"plugged": plugged, "prozent": prozent}
        except Exception:
            return {"plugged": True, "prozent": 100}

    def _intervall(self, basis: int, akku: dict) -> int:
        if akku["plugged"]:
            return basis
        return basis * self.AKKU_MULTIPLIKATOR

    def _notiere(self, text: str):
        self._puffer.append(text)
        AuditLog.action("Background", "note", text[:200])

    def get_erkenntnisse(self) -> list[str]:
        """Liefert gesammelte Erkenntnisse und leert den Puffer danach."""
        out = list(self._puffer)
        self._puffer.clear()
        return out

    def _safe_offene_fragen(self) -> list:
        """Kompatibilitätsschicht für unterschiedliche Regelwerk-APIs."""
        try:
            regelwerk = get_regelwerk()
            if hasattr(regelwerk, 'offene_fragen'):
                res = regelwerk.offene_fragen()
                return list(res) if res is not None else []
            if hasattr(regelwerk, '_offene_fragen'):
                res = regelwerk._offene_fragen()
                return list(res) if res is not None else []
        except Exception as e:
            AuditLog.action("Background", "warn", f"offene_fragen fallback: {e}")
        return []

    # ── Haupt-Loop ─────────────────────────────────────────────────────────────
    async def _loop(self):
        while self._running:
            try:
                now  = time.monotonic()
                akku = await self._akku_status()
                self.state.zyklen += 1

                # Akku-Sparmodus
                if not akku["plugged"] and akku["prozent"] < self.AKKU_MIN_PROZENT:
                    if now - self.state.letzter_health > self.HEALTH_INTERVAL:
                        await self._health_check()
                        self.state.letzter_health = now
                    await asyncio.sleep(self.TICK * 3)
                    continue

                # Health Check
                if now - self.state.letzter_health > self._intervall(self.HEALTH_INTERVAL, akku):
                    await self._health_check()
                    self.state.letzter_health = now

                if (
                    akku["plugged"]
                    and self._kernel
                    and now - self.state.letzter_provider > self._intervall(self.PROVIDER_INTERVAL, akku)
                ):
                    await self._provider_provision_check()
                    self.state.letzter_provider = now

                # Knowledge Check (nur bei Ladegerät)
                if akku["plugged"] and now - self.state.letzter_knowledge > self.KNOWLEDGE_INTERVAL:
                    await self._knowledge_check()
                    self.state.letzter_knowledge = now

                # Forgetting/Decay (nur bei Ladegerät)
                if akku["plugged"] and now - self.state.letzter_decay > self.DECAY_INTERVAL:
                    await self._decay_check()
                    self.state.letzter_decay = now

                # KI-Dialog (nur bei Ladegerät)
                if akku["plugged"] and now - self.state.letzter_ki_dialog > self.KI_DIALOG_INTERVAL:
                    await self._ki_dialog_zyklus()
                    self.state.letzter_ki_dialog = now

                # Diskussion (nur bei Ladegerät + vollem Akku)
                if (akku["plugged"] and akku["prozent"] > 50 and
                        now - self.state.letzter_diskussion > self.DISKUSSION_INTERVAL):
                    await self._diskussions_zyklus()
                    self.state.letzter_diskussion = now

                # Wertgesteuerte Entscheidungen alle 30 Minuten
                if now - getattr(self, "_last_value_decision", 0) > 1800:
                    from value_decisions import get_decision_engine
                    decisions = get_decision_engine().decide_behavior()
                    self._notiere(f"Verhaltensanpassung: {decisions}")
                    self._last_value_decision = now

                # Proaktive Owner-Autonomie (Admin-Modus, Intervall-Check)
                if now - self.state.letzter_owner_autonomy > self._intervall(
                    self.OWNER_AUTONOMY_INTERVAL, akku
                ):
                    await self._owner_autonomy_zyklus(akku)
                    self.state.letzter_owner_autonomy = now

                # Goal-directed Autonomie (Motivation → Subgoal-Tasks)
                if now - self.state.letzter_goal_autonomy > self._intervall(
                    self.GOAL_AUTONOMY_INTERVAL, akku
                ):
                    await self._goal_autonomy_zyklus()
                    self.state.letzter_goal_autonomy = now

                # Execution-Contract Missionen (Browser-Schritte goal-gebunden)
                if now - self.state.letzter_mission > self._intervall(
                    self.MISSION_INTERVAL, akku
                ):
                    await self._mission_zyklus()
                    self.state.letzter_mission = now

                # Remote smoke / Render Free anti-sleep (nur wenn ISAAC_REMOTE_SMOKE=1)
                # Wichtig: läuft auf immer-an Host (lokal/S8), nicht auf schlafendem Free.
                if now - self.state.letzter_remote_smoke > self.REMOTE_SMOKE_TICK:
                    await self._remote_smoke_zyklus()
                    self.state.letzter_remote_smoke = now

                # State Dump
                if now - self.state.letzter_dump > self.DUMP_INTERVAL:
                    self._dump_state()
                    self.state.letzter_dump = now

                await asyncio.sleep(self.TICK)

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Background-Loop Fehler: {e}", exc_info=True)
                await asyncio.sleep(60)

    # ── Health Check ─────────────────────────────────────────────────────────
    async def _provider_provision_check(self):
        try:
            if not self._kernel or not hasattr(self._kernel, "maintain_provider_keys"):
                return
            await self._kernel.maintain_provider_keys()
        except Exception as e:
            log.debug("Provider-Provisioning-Hintergrundzyklus: %s", e)

    async def _health_check(self):
        try:
            from config import get_config
            cfg = get_config()

            # Free PaaS: kein Ollama-Probe (nicht erreichbar, spammt Audit/Blacklist)
            try:
                from free_cloud import free_cloud_enabled
                if free_cloud_enabled():
                    primary = cfg.relay.primary_provider
                    prov = cfg.get_provider(primary) if primary else None
                    ok = bool(
                        prov
                        and prov.enabled
                        and prov.available
                        and cfg.is_provider_allowed(primary)
                    )
                    if not ok:
                        log.warning(
                            "Health-Check free-cloud: kein nutzbarer Primary (%s)",
                            primary or "—",
                        )
                    else:
                        log.debug("Health-Check free-cloud: primary=%s ok", primary)
                    from instincts import get_instincts
                    from meaning import get_meaning
                    get_instincts().update_from_system({
                        "recent_errors": 0 if ok else 1,
                        "open_questions": len(self._safe_offene_fragen()),
                        "steffen_engagement": get_meaning().get_bonding("Steffen"),
                    })
                    return
            except Exception as e:
                log.debug("Health-Check free-cloud branch: %s", e)

            from relay import get_relay
            relay = get_relay()
            # Primär-Provider prüfen (nicht hardcodiert ollama)
            probe = cfg.relay.primary_provider or "ollama"
            if not cfg.is_provider_allowed(probe) or not cfg.get_provider(probe):
                avail = cfg.available_providers
                probe = avail[0] if avail else "ollama"
            r = await relay.ask(
                "Antwort: OK",
                system="Antworte nur mit 'OK'.",
                provider=probe,
                task_id="health",
            )
            ok = "OK" in r or not r.startswith("[RELAY")
            if not ok:
                log.warning("Health-Check: %s meldet Fehler: %s", probe, r[:80])
            else:
                log.debug("Health-Check: OK (%s)", probe)

            from instincts import get_instincts
            from meaning import get_meaning
            get_instincts().update_from_system({
                "recent_errors": 0 if ok else 1,
                "open_questions": len(self._safe_offene_fragen()),
                "steffen_engagement": get_meaning().get_bonding("Steffen"),
            })
        except Exception as e:
            log.warning(f"Health-Check: {e}")

    # ── Knowledge Check ───────────────────────────────────────────────────────
    async def _knowledge_check(self):
        try:
            regelwerk = get_regelwerk()
            fragen = self._safe_offene_fragen()
            if fragen and self._kernel:
                for frage in fragen[:1]:
                    text = getattr(frage, "text", None) or str(frage)
                    self._puffer.append(f"[Offene Frage] {text}")
        except Exception as e:
            log.debug(f"Knowledge-Check: {e}")

    async def _owner_autonomy_zyklus(self, akku: dict):
        try:
            from owner_autonomy import run_due_owner_autonomy_tasks

            self.state.owner_task_last_run = await run_due_owner_autonomy_tasks(
                last_runs=dict(self.state.owner_task_last_run or {}),
                akku=akku,
                on_note=self._notiere,
            )
        except Exception as e:
            log.debug("Owner-Autonomie-Zyklus: %s", e)

    async def _goal_autonomy_zyklus(self):
        """Motivation-Tick: aktive Steffen-Ziele → Subgoal-Tasks + optional Digest."""
        try:
            from motivation import run_goal_motivation_cycle

            result = await run_goal_motivation_cycle(on_note=self._notiere, submit_tasks=True)
            if result.get("tasks"):
                self.state.goal_autonomy_ticks = int(self.state.goal_autonomy_ticks or 0) + 1
                log.info(
                    "Goal-Autonomie: tasks=%s subgoals_created=%s",
                    result.get("tasks"),
                    result.get("subgoals_created"),
                )
        except Exception as e:
            log.debug("Goal-Autonomie-Zyklus: %s", e)
        # Slice 4: gebündelter Goal-Digest (nur bei Änderung + Interval)
        try:
            from goal_digest import maybe_emit_digest

            digest = maybe_emit_digest()
            if digest.get("emitted") and digest.get("text"):
                self._notiere(digest["text"])
                log.info(
                    "Goal-Digest emitted g=%s q=%s",
                    digest.get("active_goals"),
                    digest.get("open_inquiries"),
                )
        except Exception as e:
            log.debug("Goal-Digest: %s", e)

    async def _mission_zyklus(self):
        """Execution-Contract: aktive Missionen mit echten Browser-Schritten fortsetzen."""
        try:
            from execution_contract import run_mission_tick

            browser_on = True
            try:
                from config import get_config
                browser_on = bool(getattr(get_config(), "browser_automation", True))
            except Exception:
                pass
            try:
                from free_cloud import free_cloud_enabled
                if free_cloud_enabled():
                    browser_on = False
            except Exception:
                pass

            result = await run_mission_tick(
                on_note=self._notiere,
                browser_enabled=browser_on,
            )
            advanced = result.get("advanced") or []
            if advanced:
                self.state.mission_ticks = int(self.state.mission_ticks or 0) + 1
                log.info(
                    "Mission-Tick: active=%s advanced=%s browser=%s",
                    result.get("active"),
                    len(advanced),
                    browser_on,
                )
        except Exception as e:
            log.debug("Mission-Zyklus: %s", e)

    async def _remote_smoke_zyklus(self):
        """Keep-alive + full smoke against remote Free URL (anti Render sleep)."""
        try:
            from free_cloud import free_cloud_enabled
            # On Free itself: cannot keep self awake after sleep; skip to save cycles
            if free_cloud_enabled():
                return
        except Exception:
            pass
        try:
            from remote_smoke import remote_smoke_enabled, run_auto_tick

            if not remote_smoke_enabled():
                return
            result = await run_auto_tick(on_note=self._notiere)
            if result.get("mode") in {"wake", "full"}:
                self.state.remote_smoke_ticks = int(self.state.remote_smoke_ticks or 0) + 1
                log.info(
                    "RemoteSmoke: mode=%s ok=%s commit=%s",
                    result.get("mode"),
                    result.get("ok"),
                    result.get("git_commit"),
                )
        except Exception as e:
            log.debug("RemoteSmoke-Zyklus: %s", e)

    async def _decay_check(self):
        try:
            from forgetting_decay import run_decay_cycle
            summary = run_decay_cycle()
            total = (
                summary.get("preference_facts_decayed", 0)
                + summary.get("values_decayed", 0)
                + summary.get("development_events_archived", 0)
            )
            if total:
                self._notiere(
                    "Decay: "
                    f"prefs={summary.get('preference_facts_decayed', 0)} "
                    f"values={summary.get('values_decayed', 0)} "
                    f"archived={summary.get('development_events_archived', 0)}"
                )
        except Exception as e:
            log.debug(f"Decay-Check: {e}")

    # ── KI-Dialog Zyklus ─────────────────────────────────────────────────────
    async def _ki_dialog_zyklus(self):
        try:
            ki_dialog = get_ki_dialog()
            await ki_dialog.spontaner_dialog()
            self.state.dialoge_gesamt += 1
            log.info(f"KI-Dialog Zyklus #{self.state.dialoge_gesamt}")
        except Exception as e:
            log.debug(f"KI-Dialog: {e}")

    # ── Diskussions-Zyklus ────────────────────────────────────────────────────
    async def _diskussions_zyklus(self):
        try:
            thema = self.DISKUSSIONS_THEMEN[
                self._themen_idx % len(self.DISKUSSIONS_THEMEN)
            ]
            self._themen_idx += 1
            ki_dialog = get_ki_dialog()
            await ki_dialog.multi_diskussion(thema)
            self.state.diskussionen_gesamt += 1
            log.info(f"Diskussion #{self.state.diskussionen_gesamt}: {thema[:50]}")
        except Exception as e:
            log.debug(f"Diskussion: {e}")

    # ── State Dump ────────────────────────────────────────────────────────────
    def _dump_state(self):
        try:
            data = {
                "letzter_health":     self.state.letzter_health,
                "letzter_ki_dialog":  self.state.letzter_ki_dialog,
                "letzter_owner_autonomy": self.state.letzter_owner_autonomy,
                "letzter_goal_autonomy": self.state.letzter_goal_autonomy,
                "letzter_mission": self.state.letzter_mission,
                "letzter_remote_smoke": self.state.letzter_remote_smoke,
                "owner_task_last_run": dict(self.state.owner_task_last_run or {}),
                "goal_autonomy_ticks": int(self.state.goal_autonomy_ticks or 0),
                "mission_ticks": int(self.state.mission_ticks or 0),
                "remote_smoke_ticks": int(self.state.remote_smoke_ticks or 0),
                "zyklen":             self.state.zyklen,
                "dialoge_gesamt":     self.state.dialoge_gesamt,
                "ideen_gesamt":       self.state.ideen_gesamt,
            }
            STATE_PATH.write_text(json.dumps(data, indent=2))
        except Exception:
            pass

    def _load_state(self) -> BackgroundState:
        try:
            if STATE_PATH.exists():
                data = json.loads(STATE_PATH.read_text())
                return BackgroundState(**{k: v for k, v in data.items()
                                          if k in BackgroundState.__dataclass_fields__})
        except Exception:
            pass
        return BackgroundState()

    # ── Öffentliche API ───────────────────────────────────────────────────────
    def get_puffer(self) -> list:
        puffer = self._puffer[:]
        self._puffer.clear()
        return puffer

    def get_ideen(self, min_score: float = 6.5) -> list:
        return [
            {"idee_id": i.idee_id, "summary": i.summary,
             "score": i.value_score, "topic": i.topic, "gemeldet": i.gemeldet}
            for i in self._ideenqueue if i.value_score >= min_score
        ]

    def status(self) -> dict:
        return {
            "running":           self._running,
            "zyklen":            self.state.zyklen,
            "dialoge_gesamt":    self.state.dialoge_gesamt,
            "diskussionen":      self.state.diskussionen_gesamt,
            "ideen_gesamt":      self.state.ideen_gesamt,
            "ideen_queue":       len(self._ideenqueue),
            "puffer":            len(self._puffer),
            "owner_autonomy_runs": dict(self.state.owner_task_last_run or {}),
            "goal_autonomy_ticks": int(self.state.goal_autonomy_ticks or 0),
            "letzter_goal_autonomy": self.state.letzter_goal_autonomy,
            "mission_ticks": int(self.state.mission_ticks or 0),
            "letzter_mission": self.state.letzter_mission,
            "remote_smoke_ticks": int(self.state.remote_smoke_ticks or 0),
            "letzter_remote_smoke": self.state.letzter_remote_smoke,
        }


_background: Optional[BackgroundLoop] = None

def get_background() -> BackgroundLoop:
    global _background
    if _background is None:
        _background = BackgroundLoop()
    return _background
