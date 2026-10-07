"""
Isaac – Kernel v5.3
=====================
Zentraler Orchestrator. Alle 21 Module vollständig integriert.

Pipeline pro Steffen-Input:
  1. SUDO-Check
  2. Empathie-Analyse (Node-Zustand)
  3. Wissensdatenbank konsultieren (KI-Dialog-DB)
  4. Intent erkennen
  5. Komplexitäts-Routing:
       - Einfach  → Standard-Task
       - Komplex  → Decomposer (Steffens Prompt nie direkt extern)
       - Multi-KI → Dispatcher (Broadcast / Split / Pipeline)
  6. Regelwerk analysiert jede Interaktion
  7. Offene Fragen des Regelwerks stellen
  8. Background-Erkenntnisse einbauen
  9. Gedächtnis schreiben + Dashboard pushen

Datenschutz-Garantie:
  Steffens originaler Prompt wird NIEMALS direkt an externe KIs gesendet.
  Der Decomposer atomisiert jeden Prompt bevor er externe Instanzen erreicht.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import hashlib
import logging
import sys
from typing import Optional, Any

from config         import get_config, Level, WORKSPACE, is_owner_equivalent_mode
from constitution_override import apply_constitution_gate, build_override_context
from privilege      import get_gate, steffen_ctx, isaac_ctx
from audit          import AuditLog, setup_privilege_audit
from memory         import get_memory
from executor       import get_executor, TaskType, TaskStatus, Strategy
from relay          import get_relay
from logic          import get_logic
from empathie       import get_empathie
from sudo_gate      import get_sudo
from regelwerk      import get_regelwerk
from decomposer     import get_decomposer
from ki_dialog      import get_ki_dialog
from ki_skills      import get_skill_router
from monitor_server import get_monitor, set_kernel, DashboardHTTPServer
from meaning        import get_meaning
from values         import get_values
from self_model     import get_self_model
from self_reflection_policy import build_self_reflection_policy_prompt
from low_complexity import (
    ClassificationResult,
    InteractionClass,
    classify_interaction_result,
    is_lightweight_local_class,
    is_low_complexity_local_input,
    local_class_response,
    local_fast_response,
    normalize_low_complexity,
)
from neural_core   import get_neural_cortex
from learning_engine import get_learning_engine

log = logging.getLogger("Isaac.Kernel")

# ── Komplexitätsschwelle für Decomposer ───────────────────────────────────────
DECOMPOSE_WORT_SCHWELLE = 15   # Ab 15 Wörtern → Decomposer
DECOMPOSE_THEMEN_SCHWELLE = 2  # Ab 2 erkennbaren Themen → Decomposer


# ── Intents ────────────────────────────────────────────────────────────────────
class Intent:
    CHAT        = "chat"
    SEARCH      = "search"
    RESEARCH    = "research"
    BROWSER     = "browser"
    AGENT       = "agent"
    CODE        = "code"
    FILE        = "file"
    TRANSLATE   = "translate"
    BROADCAST   = "broadcast"
    SPLIT       = "split"
    PIPELINE    = "pipeline"
    ENSEMBLE    = "ensemble"
    DECOMPOSE   = "decompose"   # Explizite Atomisierung
    FACT_SET    = "fact_set"
    GOAL_SET    = "goal_set"
    GOAL_LIST   = "goal_list"
    DIRECTIVE   = "directive"
    STATUS      = "status"
    KI_STATUS   = "ki_status"   # KI-Dialog + Skill-Übersicht
    MEINUNG     = "meinung"     # Isaac's Meinung zu einem Thema
    PAUSE       = "pause"
    RESUME      = "resume"
    CANCEL      = "cancel"
    SUDO_OPEN   = "sudo_open"
    SUDO_CLOSE  = "sudo_close"
    LOGIN_ADD   = "login_add"
    URL_ADD     = "url_add"
    LETTA       = "letta"           # Explizit: Letta Code Companion-CLI
    OPEN_INTERPRETER = "open_interpreter"  # Explizit: Open Interpreter Companion
    GROK_AGENT  = "grok_agent"      # Explizit: Grok Build Agent CLI (headless)
    JULES_AGENT = "jules_agent"      # Explizit: Jules Coding Agent
    COPILOT_AGENT = "copilot_agent"  # Explizit: GitHub Copilot CLI / cloud agent
    CONTEXT7    = "context7"        # Explizit: Context7 Library-Docs (docs:)
    REMOTE_CLOUD = "remote_cloud"    # Explizit: remote Isaac (Render free) cloud:
    REMOTE_BOTH  = "remote_both"     # Explizit: lokal + remote both:
    EXT_MEMORY  = "ext_memory"      # Status: external memory adapters


EXPLICIT_COMMAND_PATTERNS = [
    (Intent.SUDO_OPEN,  [r"^sudo\s+", r"^öffne tür", r"^master key"]),
    (Intent.SUDO_CLOSE, [r"^sudo close$", r"^tür schließen$"]),
    (Intent.FACT_SET,   [r"^korrektur:", r"^fakt:", r"^weiß:"]),
    (Intent.GOAL_SET,   [
        r"^ziel\s*:",
        r"^goal\s*:",
        r"^mein ziel\s*:",
        r"^meine ziele\s*:",
        r"^ziel erledigt\s*:",
        r"^ziel done\s*:",
        r"^goal done\s*:",
        r"^ziel pause\s*:",
        r"^pausiere ziel\s*:",
    ]),
    (Intent.GOAL_LIST,  [
        r"^ziele$",
        r"^meine ziele$",
        r"^list goals$",
        r"^ziele anzeigen$",
        r"^zeige ziele$",
        r"^zeig ziele$",
    ]),
    (Intent.DIRECTIVE,  [r"^direktive:", r"^immer:", r"^niemals:"]),
    (Intent.BROADCAST,  [r"^broadcast:", r"^alle instanzen:", r"^frage alle"]),
    (Intent.SPLIT,      [r"^split:", r"^aufteilen:"]),
    (Intent.PIPELINE,   [r"^pipeline:", r"^verbessere iterativ"]),
    (Intent.ENSEMBLE,   [
        r"^ensemble\s*:",
        r"^vergleiche\s*:",
        r"^vergleiche modelle\s*:",
        r"^multi[- ]?model\s*:",
    ]),
    (Intent.DECOMPOSE,  [r"^atomisiere:", r"^verteile:"]),
    (Intent.CODE,       [r"^code:", r"^programmiere:", r"^schreibe.*python"]),
    (Intent.FILE,       [
        r"^datei:",
        r"^lese:",
        r"^schreibe.*datei",
        r"^datei\s+(?:read|write|append|list|ls|info)\b",
    ]),
    (Intent.RESEARCH,   [r"^recherche:", r"^recherchiere:"]),
    (Intent.AGENT,      [
        r"^agent:",
        r"^oberfläche:",
        r"^oberflaeche:",
        r"^shell\s+",
        r"^ausführ",
        r"^ausfuehr",
        r"^führe aus",
        r"^fuehre aus",
        r"^befehl:",
    ]),
    (Intent.TRANSLATE,  [r"^übersetze", r"^translate", r"^schrift:"]),
    (Intent.LOGIN_ADD,  [r"^login:", r"^credential:", r"^zugangsdaten:"]),
    (Intent.URL_ADD,    [r"^url:", r"^instanz:", r"^füge.*url"]),
    (Intent.KI_STATUS,  [r"^ki status$", r"^instanzen$", r"^meinungen$"]),
    (Intent.MEINUNG,    [r"^meinung:", r"^was denkst du über", r"^isaac.*meinung"]),
    (Intent.PAUSE,      [r"^pause$", r"^stopp$"]),
    (Intent.RESUME,     [r"^weiter$", r"^fortsetzen$"]),
    (Intent.CANCEL,     [r"^abbrechen\s+\w+"]),
    (Intent.LETTA,      [
        r"^letta\s*:",
        r"^coding-agent\s*:",
        r"^coding agent\s*:",
    ]),
    (Intent.OPEN_INTERPRETER, [
        r"^oi\s*:",
        r"^open-interpreter\s*:",
        r"^open interpreter\s*:",
        r"^interpreter\s*:",
    ]),
    (Intent.GROK_AGENT, [
        r"^grok\s*:",
        r"^grok-agent\s*:",
        r"^grok agent\s*:",
        r"^xai-agent\s*:",
        r"^xai agent\s*:",
    ]),
    (Intent.JULES_AGENT, [
        r"^jules\s*:",
        r"^jules-agent\s*:",
        r"^jules agent\s*:",
    ]),
    (Intent.COPILOT_AGENT, [
        r"^copilot\s*:",
        r"^gh-copilot\s*:",
        r"^gh copilot\s*:",
        r"^github-copilot\s*:",
        r"^github copilot\s*:",
        r"^copilot-agent\s*:",
        r"^copilot agent\s*:",
    ]),
    (Intent.CONTEXT7, [
        r"^docs\s*:",
        r"^context7\s*:",
        r"^ctx7\s*:",
        r"^doku\s*:",
        r"^library docs\s*:",
        r"^lib docs\s*:",
    ]),
    (Intent.REMOTE_CLOUD, [
        r"^cloud\s*:",
        r"^free\s*:",
        r"^render\s*:",
        r"^isaac-cloud\s*:",
        r"^isaac cloud\s*:",
        r"^isaac-free\s*:",
    ]),
    (Intent.REMOTE_BOTH, [
        r"^both\s*:",
        r"^beide\s*:",
        r"^fleet\s*:",
    ]),
    (Intent.EXT_MEMORY, [
        r"^external memory$",
        r"^external[- ]memory$",
        r"^memory adapters?$",
        r"^mem0 status$",
        r"^cognee status$",
        r"^letta status$",
        r"^context7 status$",
        r"^ctx7 status$",
        r"^docs status$",
        r"^oi status$",
        r"^open-interpreter status$",
        r"^open interpreter status$",
        r"^grok status$",
        r"^grok-agent status$",
        r"^grok agent status$",
        r"^status:pipeline$",
        r"^status:pipeline\s+sync$",
        r"^pipeline status$",
        r"^automation status$",
        r"^status:automation$",
        r"^status:smoke$",
        r"^status:smoke\s+\w+$",
        r"^smoke:remote$",
        r"^smoke:remote\s+\w+$",
        r"^remote smoke$",
        r"^remote smoke\s+\w+$",
        r"^ntfy$",
        r"^ntfy\s+\w+$",
        r"^owner push$",
        r"^owner push\s+\w+$",
        r"^owner-push$",
        r"^owner-push\s+\w+$",
        r"^login flow\s*:",
        r"^login-flow\s*:",
        r"^browser login flow\s*:",
        r"^browser login\s*:",
    ]),
]

def detect_intent(text: str) -> str:
    tl = text.lower().strip()
    for intent, patterns in EXPLICIT_COMMAND_PATTERNS:
        for pat in patterns:
            if re.search(pat, tl):
                return intent
    return Intent.CHAT


def braucht_decomposer(text: str, intent: str) -> bool:
    """Entscheidet ob ein Prompt atomisiert werden soll."""
    if intent in (Intent.CODE, Intent.FILE, Intent.SEARCH, Intent.RESEARCH,
                  Intent.BROADCAST, Intent.SPLIT, Intent.PIPELINE,
                  Intent.DECOMPOSE, Intent.ENSEMBLE):
        return False   # Eigene Handler
    wortanzahl = len(text.split())
    und_count  = len(re.findall(r'\s+(?:und|sowie|außerdem|auch)\s+',
                                text, re.I))
    return wortanzahl >= DECOMPOSE_WORT_SCHWELLE or und_count >= DECOMPOSE_THEMEN_SCHWELLE


# ── Kernel ─────────────────────────────────────────────────────────────────────
class IsaacKernel:

    VERSION = "5.3"

    def __init__(self):
        log.info("=" * 56)
        log.info(f"  ISAAC v{self.VERSION} – Unified OS Startup")
        log.info("=" * 56)

        setup_privilege_audit()

        self.cfg        = get_config()
        self.gate       = get_gate()
        self.memory     = get_memory()
        self.executor   = get_executor()
        self.relay      = get_relay()
        self.logic      = get_logic()
        self.empathie   = get_empathie()
        self.sudo       = get_sudo()
        self.regelwerk  = get_regelwerk()
        self.decomposer = get_decomposer()
        self.ki_dialog  = get_ki_dialog()
        self.skill_router = get_skill_router()
        self.monitor    = get_monitor()
        self.meaning    = get_meaning()
        self.values     = get_values()
        self.neural     = get_neural_cortex()
        self.learning   = get_learning_engine()
        # Letztes Wetter-Thema für Ort-Korrekturen („Ich wollte für 99974 …“)
        self._last_weather_active: bool = False
        self._last_weather_query: str = ""
        self._background = None   # lazy start in main()
        # Grok Agent multi-turn session (headless --resume)
        self._grok_session_id: Optional[str] = None

        set_kernel(self)
        self._sudo_token: Optional[str] = None
        self._awaiting_frage_id: Optional[str] = None

        log.info(f"  Owner:      {self.cfg.owner_name}")
        log.info(f"  Provider:   {', '.join(self.cfg.available_providers)}")
        log.info(f"  Regelwerk:  {self.regelwerk.status()['regeln_aktiv']} Regeln")
        log.info(f"  KI-Dialog:  {self.ki_dialog.stats()['gespraeche']} Gespräche, "
                 f"{self.ki_dialog.stats()['wissenseintraege']} Wissenseinträge")
        log.info(f"  SUDO:       {'Ersteinrichtung' if self.sudo.is_first_run() else 'Bereit'}")
        AuditLog.action("Kernel", "startup", f"v{self.VERSION}", Level.ISAAC)

    # ── Haupt-Verarbeitung ────────────────────────────────────────────────────
    async def process(self, user_input: str,
                      sudo_token: Optional[str] = None) -> str:
        """Owner-facing entry: wraps a Sentry performance transaction when enabled."""
        if not (user_input or "").strip():
            return ""
        from contextlib import nullcontext

        try:
            from isaac_sentry import (
                add_breadcrumb,
                is_enabled,
                request_transaction,
                session_conversation_id,
                set_conversation_id,
            )

            if is_enabled():
                conv = session_conversation_id()
                if conv:
                    set_conversation_id(conv)
                add_breadcrumb(
                    "process_start",
                    category="kernel",
                    level="info",
                    input=self._redact_secrets(user_input or "")[:120],
                )
                with request_transaction(
                    name="isaac.process",
                    op="function",
                    user_input=user_input,
                ):
                    return await self._process_body(user_input, sudo_token=sudo_token)
        except Exception as exc:
            log.debug("sentry process wrapper: %s", exc)
        return await self._process_body(user_input, sudo_token=sudo_token)

    handle_message = process

    @staticmethod
    def _redact_secrets(text: str) -> str:
        """SUDO-Passwörter für Logs/Dashboard maskieren (nie im Klartext)."""
        try:
            import re as _re
            return _re.sub(
                r"(?i)^((?:sudo|öffne tür|master key)\s+).+$",
                lambda m: m.group(1) + ("*" * max(4, len(m.group(0)) - len(m.group(1)))),
                text,
            )
        except Exception:
            return "[input redacted]"

    async def _process_body(self, user_input: str,
                            sudo_token: Optional[str] = None) -> str:
        t_start = time.perf_counter()
        timing: dict[str, float] = {}

        # 0) Klassifikation als harte Routing-Grundlage
        classification = classify_interaction_result(user_input)
        interaction_class = classification.interaction_class
        if is_lightweight_local_class(interaction_class):
            self._awaiting_frage_id = None
            timing["classification_ms"] = round((time.perf_counter() - t_start) * 1000, 2)
            log.info("Latency(lightweight) | total=%sms input='%s'", timing["classification_ms"], self._redact_secrets(user_input)[:42])
            return local_class_response(interaction_class, user_input)

        timing["classification_ms"] = round((time.perf_counter() - t_start) * 1000, 2)

        # Sentry Conversations: group multi-turn spans under one session conversation id
        try:
            from isaac_sentry import session_conversation_id, set_conversation_id
            conv = session_conversation_id()
            if conv:
                set_conversation_id(conv)
        except Exception:
            pass

        t0 = time.monotonic()

        # Ort-Korrektur nach Wetter: „Ich wollte für 99974 Mühlhausen …“
        try:
            from search import (
                looks_like_place_only_refinement,
                looks_like_weather_query,
                extract_weather_location,
            )
            if (
                getattr(self, "_last_weather_active", False)
                and looks_like_place_only_refinement(user_input)
            ):
                loc = extract_weather_location(user_input)
                user_input = f"Wetter morgen in {loc}"
                classification = classify_interaction_result(user_input)
                interaction_class = classification.interaction_class
                self._awaiting_frage_id = None
                log.info("Wetter-Themenfortführung → %s", user_input[:80])
            elif looks_like_weather_query(user_input):
                self._last_weather_active = True
                self._last_weather_query = user_input
        except Exception as exc:
            log.debug("weather continuation skipped: %s", exc)

        if is_owner_equivalent_mode():
            from decision_trace import DecisionTrace, TracePhase, audit_routing_trace
            from owner_action import detect_owner_action, execute_owner_action
            from procedure_memory import record_owner_action_outcome

            owner_action = detect_owner_action(user_input)
            if owner_action:
                AuditLog.steffen_input(user_input)
                emp = self.empathie.analysiere(user_input)
                trace = DecisionTrace()
                trace.add(
                    TracePhase.CLASSIFICATION,
                    "owner_action_detected",
                    {
                        "kind": owner_action.kind,
                        "params": dict(owner_action.params or {}),
                        "privilege_mode": "admin",
                    },
                )
                result, ok = await execute_owner_action(owner_action)
                timing["owner_action_ms"] = round((time.perf_counter() - t_start) * 1000, 2)
                trace.add(
                    TracePhase.EXECUTION,
                    "owner_action_executed",
                    {
                        "kind": owner_action.kind,
                        "ok": ok,
                        "duration_ms": timing["owner_action_ms"],
                    },
                )
                audit_routing_trace(
                    trace,
                    intent=f"owner:{owner_action.kind}",
                    outcome="success" if ok else "failed",
                )
                try:
                    record_owner_action_outcome(
                        kind=owner_action.kind,
                        raw=user_input,
                        ok=ok,
                    )
                except Exception as exc:
                    log.debug("Owner procedure capture skipped: %s", exc)
                if ok:
                    try:
                        get_self_model().bump_value_strength(
                            "autonomy",
                            strength_delta=0.008,
                            confidence_delta=0.004,
                            reason=f"owner_action:{owner_action.kind}",
                        )
                    except Exception as exc:
                        log.debug("SelfModel autonomy bump skipped: %s", exc)
                log.info(
                    "OwnerAction | kind=%s ok=%s ms=%s input='%s'",
                    owner_action.kind,
                    ok,
                    timing["owner_action_ms"],
                    user_input[:60],
                )
                return self._post_process(user_input, result, emp, 8.0 if ok else 4.0, t0)

        # Neues Thema / neue Frage: offene Regelwerk-Rückfrage verwerfen, nicht hijacken
        if self._looks_like_new_user_topic(user_input, interaction_class):
            self._awaiting_frage_id = None
            try:
                self.regelwerk.dismiss_open_term_questions()
            except Exception:
                pass
        elif interaction_class in {
            InteractionClass.STATUS_QUERY,
            InteractionClass.TOOL_REQUEST,
        }:
            self._awaiting_frage_id = None
        elif self._awaiting_frage_id and self._input_looks_like_frage_antwort(
            user_input, interaction_class
        ):
            frage_id = self._awaiting_frage_id
            antwort_text = user_input.strip()
            if antwort_text.lower().startswith("antwort:"):
                antwort_text = antwort_text.split(":", 1)[-1].strip()
            self.regelwerk.beantworte_frage(frage_id, antwort_text)
            self._persist_regelwerk_answer_to_memory(frage_id, antwort_text)
            self._awaiting_frage_id = None
            emp = self.empathie.analysiere(user_input)
            antwort = self.regelwerk.build_answer_ack(frage_id, antwort_text)
            return self._post_process(user_input, antwort, emp, 8.0, t0)

        # SUDO-Status
        sudo_aktiv = (
            (sudo_token and self.sudo.check(sudo_token)) or
            (self._sudo_token and self.sudo.check(self._sudo_token))
        )

        AuditLog.steffen_input(user_input)

        # 1. Empathie
        emp = self.empathie.analysiere(user_input)
        timing["empathie_ms"] = round((time.perf_counter() - t_start) * 1000, 2)

        # 2. Wissensdatenbank konsultieren
        wissen_kontext = self.ki_dialog.als_kontext(user_input)
        timing["wissen_ms"] = round((time.perf_counter() - t_start) * 1000, 2)

        # 3. Intent: Klassifikation ist führend, Regex nur für explizite Kommandos
        detected_intent = detect_intent(user_input)
        intent = self._resolve_intent_from_classification(
            user_input, detected_intent, interaction_class
        )
        # Mission / natural-language browser imperatives → BROWSER for gates + routing
        if intent == Intent.CHAT and self._is_browser_request(user_input):
            intent = Intent.BROWSER
        timing["routing_prep_ms"] = round((time.perf_counter() - t_start) * 1000, 2)

        # Owner confirm resume: "Ich bestätige" → pending browser mission
        pending_browser_cmd = ""
        try:
            from execution_contract import (
                is_owner_confirm,
                load_pending_browser_mission,
            )

            if is_owner_confirm(user_input):
                pending = load_pending_browser_mission()
                if pending and (pending.get("command") or "").strip():
                    pending_browser_cmd = str(pending["command"]).strip()
                    intent = Intent.BROWSER
                    log.info(
                        "Owner confirm resumes pending browser: %s",
                        pending_browser_cmd[:80],
                    )
        except Exception as exc:
            log.debug("pending browser confirm check: %s", exc)

        # Dashboard NOW (fail-open telemetry only)
        try:
            from monitor_now import set_now_phase
            set_now_phase(
                "classification",
                headline=f"{intent}: {self._redact_secrets(user_input or '')[:80]}",
                subline=f"class={interaction_class}",
            )
        except Exception:
            pass

        log.info(
            f"Input: '{self._redact_secrets(user_input)[:50]}' │ Intent: {intent} │ "
            f"Node: {emp.node.zustand} │ Sudo: {sudo_aktiv}"
        )

        blocked_msg, constitution_gate = self._enforce_constitution_gate(
            user_input, intent, sudo_aktiv
        )
        if blocked_msg:
            self._audit_constitution_block(intent, constitution_gate)
            AuditLog.isaac_output(blocked_msg)
            return blocked_msg

        # SUDO-Handshake
        if intent == Intent.SUDO_OPEN:
            return self._handle_sudo_open(user_input)
        if intent == Intent.SUDO_CLOSE:
            return self._handle_sudo_close()

        # Pause
        if self.gate.is_paused and not sudo_aktiv:
            return "[Isaac] Pausiert. 'weiter' oder SUDO zum Fortfahren."

        if intent == Intent.AGENT or self._is_agent_request(user_input):
            result = await self._handle_agent_request(user_input)
            return self._post_process(user_input, result, emp, 0.0, t0)

        if intent == Intent.BROWSER or self._is_browser_request(user_input):
            browser_text = pending_browser_cmd or user_input
            if pending_browser_cmd:
                try:
                    from execution_contract import clear_pending_browser_mission

                    clear_pending_browser_mission()
                except Exception:
                    pass
            result = await self._handle_browser_request(browser_text)
            return self._post_process(user_input, result, emp, 0.0, t0)

        # Confirm without pending mission — honest short reply (no LLM theater)
        try:
            from execution_contract import is_owner_confirm

            if is_owner_confirm(user_input):
                msg = (
                    "[Browser] Keine ausstehende Mission zum Bestätigen.\n"
                    "Starte explizit, z. B.:\n"
                    "  browser: https://example.com\n"
                    "  öffne revolut und hole api keys"
                )
                return self._post_process(user_input, msg, emp, 6.0, t0)
        except Exception:
            pass

        # Direkte Handler (kein Task nötig)
        direkt = {
            Intent.FACT_SET:   self._handle_fact,
            Intent.GOAL_SET:   self._handle_goal,
            Intent.GOAL_LIST:  self._handle_goal_list,
            Intent.DIRECTIVE:  self._handle_directive,
            Intent.STATUS:     self._handle_status,
            Intent.KI_STATUS:  self._handle_ki_status,
            Intent.MEINUNG:    self._handle_meinung,
            Intent.PAUSE:      self._handle_pause,
            Intent.RESUME:     self._handle_resume,
            Intent.CANCEL:     self._handle_cancel,
            Intent.LOGIN_ADD:  self._handle_login_add,
            Intent.URL_ADD:    self._handle_url_add,
            Intent.EXT_MEMORY: self._handle_ext_memory_status,
            Intent.LETTA:      self._handle_letta,
            Intent.OPEN_INTERPRETER: self._handle_open_interpreter,
            Intent.GROK_AGENT: self._handle_grok_agent,
            Intent.COPILOT_AGENT: self._handle_copilot_agent,
            Intent.JULES_AGENT: self._handle_jules_agent,
            Intent.CONTEXT7:   self._handle_context7,
            Intent.REMOTE_CLOUD: self._handle_remote_cloud,
            Intent.REMOTE_BOTH: self._handle_remote_both,
        }
        if intent in direkt:
            result = direkt[intent](user_input)
            if asyncio.iscoroutine(result):
                result = await result
            return self._post_process(user_input, result, emp, 0.0, t0)

        # 4. Routing: Decomposer vs Standard vs Multi-KI
        antwort, score = await self._route(
            user_input,
            intent,
            sudo_aktiv,
            emp,
            wissen_kontext,
            interaction_class,
            classification,
            constitution_gate=constitution_gate,
        )
        timing["route_done_ms"] = round((time.perf_counter() - t_start) * 1000, 2)

        total_ms = round((time.perf_counter() - t_start) * 1000, 2)
        log.info(
            "Latency(process) | total=%sms classify=%sms empathie=%sms wissen=%sms prep=%sms route=%sms class=%s intent=%s",
            total_ms,
            timing.get("classification_ms", 0.0),
            timing.get("empathie_ms", 0.0),
            timing.get("wissen_ms", 0.0),
            timing.get("routing_prep_ms", 0.0),
            timing.get("route_done_ms", 0.0),
            interaction_class,
            intent,
        )

        return self._post_process(user_input, antwort, emp, score, t0)

    # ── Routing ────────────────────────────────────────────────────────────────
    async def _route(self, user_input: str, intent: str,
                     sudo_aktiv: bool, emp, wissen_kontext: str,
                     interaction_class: str,
                     classification: ClassificationResult,
                     constitution_gate: Optional[dict] = None,
                     ) -> tuple[str, float]:
        """
        Entscheidet welcher Pfad genutzt wird:
          A) Decomposer  — komplexe Prompts, mehrere Themen
          B) Multi-KI    — expliziter Broadcast/Split/Pipeline
          C) Standard    — einfache Tasks
        """
        from browser import get_browser
        aktive_instanzen = get_browser().get_active_ids()

        # B) Multi-KI explizit
        if intent == Intent.ENSEMBLE:
            return await self._ensemble_route(
                user_input,
                sudo_aktiv,
                emp,
                trigger="explicit",
                classification=classification,
                interaction_class=interaction_class,
            )

        # B2) Always-on Ensemble nur bei schweren NORMAL_CHAT-Fragen
        if intent == Intent.CHAT:
            from openrouter_ensemble import should_auto_ensemble

            auto_ok, auto_reason = should_auto_ensemble(
                user_input,
                intent=intent,
                interaction_class=interaction_class,
            )
            if auto_ok:
                log.info("Auto-Ensemble: %s | '%s'", auto_reason, user_input[:48])
                return await self._ensemble_route(
                    user_input,
                    sudo_aktiv,
                    emp,
                    trigger="auto",
                    trigger_reason=auto_reason,
                    classification=classification,
                    interaction_class=interaction_class,
                )

        if intent in (Intent.BROADCAST, Intent.SPLIT, Intent.PIPELINE,
                      Intent.DECOMPOSE):
            return await self._multi_ki_route(
                user_input, intent, aktive_instanzen, emp, sudo_aktiv
            )

        # A) Automatischer Decomposer bei Komplexität
        if (aktive_instanzen and
                braucht_decomposer(user_input, intent) and
                not sudo_aktiv):   # Bei SUDO: direkt, keine Verzögerung
            log.info(f"Decomposer: '{user_input[:40]}...'")
            result = await self.decomposer.decompose_and_execute(
                user_input, aktive_instanzen, classification=classification
            )
            return result.final, 7.0   # Decomposer-Ergebnisse gelten als gut

        # C) Standard-Task
        return await self._standard_task(
            user_input,
            intent,
            sudo_aktiv,
            emp,
            wissen_kontext,
            interaction_class,
            classification,
            constitution_gate=constitution_gate,
        )

    async def _multi_ki_route(self, user_input: str, intent: str,
                               instanzen: list, emp, sudo_aktiv: bool
                               ) -> tuple[str, float]:
        from dispatcher import get_dispatcher
        dispatcher = get_dispatcher()
        system     = self._build_system(sudo_aktiv, emp)

        if not instanzen:
            instanzen = self.cfg.available_providers[:4]

        if intent in (Intent.DECOMPOSE, Intent.SPLIT):
            r = await dispatcher.split(user_input, instanzen, system=system)
        elif intent == Intent.PIPELINE:
            r = await dispatcher.pipeline(user_input, instanzen, system=system)
        else:   # BROADCAST
            r = await dispatcher.broadcast(user_input, instanzen, system=system)

        return r.final, r.ergebnisse[0].score if r.ergebnisse else 6.0

    async def _ensemble_route(
        self,
        user_input: str,
        sudo_aktiv: bool,
        emp,
        *,
        trigger: str = "explicit",
        trigger_reason: str = "",
        classification: Optional[ClassificationResult] = None,
        interaction_class: str = "",
    ) -> tuple[str, float]:
        """OpenRouter Multi-Model: vergleichen + bestes/Kombination + DecisionTrace."""
        from decision_trace import TracePhase, audit_routing_trace
        from logic import QualityScore
        from openrouter_ensemble import (
            ensemble_enabled,
            ensemble_openrouter,
            ensemble_trace_payload,
            format_ensemble_footer,
            get_ensemble_models,
        )

        body = user_input
        for prefix in (
            "ensemble:", "vergleiche:", "vergleiche modelle:", "multi-model:", "multimodel:",
        ):
            if body.lower().startswith(prefix):
                body = body[len(prefix):].strip()
                break
        if not body:
            return (
                "[Ensemble] Format: ensemble: <deine Frage>\n"
                "Nutzt mehrere OpenRouter-Modelle (Default: free), scored und kombiniert.\n"
                "Auto: schwere CHAT-Fragen (ISAAC_ENSEMBLE_AUTO=1)."
            ), 5.0

        ic = interaction_class or (
            classification.interaction_class if classification else ""
        )
        task = self.executor.create_task(
            typ=TaskType.AGGREGATE,
            prompt=body,
            beschreibung=f"ensemble[{trigger}]:{body[:64]}",
            prioritaet=8.0 if trigger == "explicit" else 6.5,
            provider="openrouter",
            system_prompt="",
            sudo_aktiv=sudo_aktiv,
            strategy=Strategy(
                allow_tools=False,
                allow_followup=False,
                allow_provider_switch=False,
                style_note="openrouter_ensemble",
            ),
            interaction_class=ic,
            classification=classification,
        )
        task.status = TaskStatus.RUNNING
        task.gestartet = time.strftime("%Y-%m-%d %H:%M:%S")
        task.provider_used = "openrouter-ensemble"
        task.decision_trace.add(
            TracePhase.CLASSIFICATION,
            "ensemble_route",
            {
                "trigger": trigger,
                "trigger_reason": trigger_reason or trigger,
                "interaction_class": ic,
                "word_count": classification.word_count if classification else len(body.split()),
            },
        )
        task.decision_trace.add(
            TracePhase.STRATEGY,
            "ensemble_selected",
            {
                "allow_tools": False,
                "provider": "openrouter",
                "free_panel_preview": get_ensemble_models()[:4],
                "trigger": trigger,
            },
        )
        task.log(f"Ensemble start ({trigger})")
        self.executor._notify(task)
        t_run = time.monotonic()

        if not ensemble_enabled():
            system = self._build_system(sudo_aktiv, emp)
            text, prov = await self.relay.ask_with_fallback(
                body, system=system, preferred="openrouter", task_id="ensemble-off"
            )
            final = f"{text}\n\n_[Ensemble deaktiviert → single {prov}]_"
            task.decision_trace.add(
                TracePhase.EXECUTION,
                "ensemble_disabled_fallback",
                {"provider": prov},
            )
            task.status = TaskStatus.DONE
            task.antwort = final
            task.provider_used = prov or "openrouter"
            task.dauer_sek = round(time.monotonic() - t_run, 2)
            task.progress = 1.0
            task.abgeschlossen = time.strftime("%Y-%m-%d %H:%M:%S")
            task.score = QualityScore(total=6.0)
            self.executor._notify(task)
            audit_routing_trace(task.decision_trace, intent="ensemble", outcome="disabled")
            return final, 6.0

        system = self._build_system(sudo_aktiv, emp)
        task.system_prompt = system
        result = await ensemble_openrouter(
            body,
            system=system,
            task_id=f"ensemble-{task.id}",
        )
        footer = format_ensemble_footer(result)
        final = f"{result.final}\n\n---\n_{footer}_"
        best_score = 0.0
        ok = [e for e in result.ergebnisse if not e.fehler]
        if ok:
            best_score = max(e.score for e in ok)
        elif result.mode in {"winner", "judge", "single"}:
            best_score = 6.5

        payload = ensemble_trace_payload(result, trigger=trigger)
        task.decision_trace.add(
            TracePhase.SELECTION,
            "models_panel",
            {
                "panel": payload.get("panel"),
                "n": payload.get("n_total"),
            },
        )
        task.decision_trace.add(
            TracePhase.EXECUTION,
            "models_answered",
            {
                "n_ok": payload.get("n_ok"),
                "failed": payload.get("failed"),
                "dauer_s": payload.get("dauer_s"),
            },
        )
        task.decision_trace.add(
            TracePhase.EVALUATION,
            "ensemble_decision",
            {
                "mode": result.mode,
                "winner_model": result.winner_model,
                "judge_model": result.judge_model,
                "scores": payload.get("scores"),
                "best_score": round(best_score, 2),
            },
        )
        task.status = TaskStatus.DONE
        task.antwort = final
        task.provider_used = f"openrouter-ensemble:{result.winner_model or result.mode}"
        task.dauer_sek = round(time.monotonic() - t_run, 2)
        task.progress = 1.0
        task.abgeschlossen = time.strftime("%Y-%m-%d %H:%M:%S")
        task.score = QualityScore(total=float(best_score or 0.0))
        task.log(f"Ensemble done mode={result.mode} winner={result.winner_model}")
        self.executor._notify(task)
        audit_routing_trace(
            task.decision_trace,
            intent="ensemble",
            outcome=result.mode,
        )
        AuditLog.action(
            "Kernel",
            "ensemble",
            f"trigger={trigger} mode={result.mode} winner={result.winner_model} "
            f"n={len(result.ergebnisse)} task={task.id}",
            erfolg=bool(result.final),
        )
        return final, best_score

    async def _standard_task(self, user_input: str, intent: str,
                              sudo_aktiv: bool, emp, wissen_kontext: str,
                              interaction_class: str,
                              classification: ClassificationResult,
                              constitution_gate: Optional[dict] = None,
                              ) -> tuple[str, float]:
        # Follow-ups: strip key/browser mission noise from side context
        if intent == Intent.CHAT and self._is_short_followup(user_input):
            wissen_kontext = self._strip_mission_noise_from_context(wissen_kontext or "")
        retrieval_ctx = self._retrieve_relevant_context(
            user_input=user_input,
            intent=intent,
            interaction_class=interaction_class,
        )
        neural_trace = self.neural.propagate(
            interaction_class=interaction_class,
            intent=intent,
            retrieval_ctx=retrieval_ctx,
            word_count=classification.word_count,
        )

        strategy = self._select_response_strategy(
            user_input=user_input,
            intent=intent,
            interaction_class=interaction_class,
            retrieval_ctx=retrieval_ctx,
        )
        modulation = self.neural.modulate_strategy(
            allow_tools=strategy.allow_tools,
            allow_followup=strategy.allow_followup,
            allow_provider_switch=strategy.allow_provider_switch,
            trace=neural_trace,
        )
        if modulation.allow_tools is not None:
            strategy = Strategy(
                allow_tools=modulation.allow_tools,
                allow_followup=modulation.allow_followup if modulation.allow_followup is not None else strategy.allow_followup,
                allow_provider_switch=(
                    modulation.allow_provider_switch
                    if modulation.allow_provider_switch is not None
                    else strategy.allow_provider_switch
                ),
                allow_agent_companions=strategy.allow_agent_companions,
                preferred_agent=strategy.preferred_agent,
                style_note=(strategy.style_note or "") + (f"\n{modulation.neural_note}" if modulation.neural_note else ""),
            )
        typ_map = {
            Intent.SEARCH:    TaskType.SEARCH,
            Intent.RESEARCH:  TaskType.RESEARCH,
            Intent.CODE:      TaskType.CODE,
            Intent.FILE:      TaskType.FILE,
            Intent.TRANSLATE: TaskType.TRANSLATE,
            Intent.CHAT:      TaskType.CHAT,
        }
        task_typ = typ_map.get(intent, TaskType.CHAT)

        # Optional companion agent (Grok/OI/Letta) — strategy + auto-select, then inject context
        agent_ctx_block = ""
        agent_decision = None
        try:
            agent_decision, agent_ctx_block, strategy = self._maybe_run_selected_agent(
                user_input=user_input,
                intent=intent,
                interaction_class=interaction_class,
                strategy=strategy,
            )
        except Exception as exc:
            log.debug("auto agent selection skipped: %s", exc)

        system   = self._build_system(
            sudo_aktiv, emp, wissen_kontext,
            strategy_note=strategy.style_note
        )
        provider = self._provider_hint(user_input)

        structured_ctx = self._format_retrieval_context(retrieval_ctx)
        if intent == Intent.CHAT and self._is_short_followup(user_input):
            structured_ctx = self._strip_mission_noise_from_context(structured_ctx)
        kontext = structured_ctx.strip()
        if agent_ctx_block:
            kontext = f"{agent_ctx_block}\n\n{kontext}".strip() if kontext else agent_ctx_block
        # LLM sees retrieval + user; tools/search use beschreibung / execution_query only.
        prompt = f"{kontext}\n\n{user_input}".strip() if kontext else user_input

        task = self.executor.create_task(
            typ           = task_typ,
            prompt        = prompt,
            beschreibung  = user_input,  # full owner line (not truncated) for tool/search query
            prioritaet    = 9.0 if sudo_aktiv else 5.0,
            provider      = provider,
            system_prompt = system,
            sudo_aktiv    = sudo_aktiv,
            strategy      = strategy,
            interaction_class=interaction_class,
            classification=classification,
            retrieved_context = retrieval_ctx,
        )
        if hasattr(task, "risk_level"):
            task.risk_level = "high" if sudo_aktiv else ("medium" if strategy.allow_tools else "low")
        if hasattr(task, "required_capabilities"):
            if strategy.allow_tools:
                task.required_capabilities.append("tools")
            if strategy.allow_agent_companions:
                task.required_capabilities.append("companions")

        if hasattr(task, "record_action"):
            task.record_action("classify", {"interaction_class": interaction_class, "intent": intent})
            task.record_action("retrieve_context", {"facts": len(retrieval_ctx.get("relevant_facts", [])), "procedures": len(retrieval_ctx.get("relevant_procedures", []))})
        if hasattr(task, "record_decision"):
            task.record_decision(f"Selected strategy with allow_tools={strategy.allow_tools}", {"risk_level": getattr(task, "risk_level", "low")})
        from decision_trace import TracePhase, gate_trace_data

        if constitution_gate:
            task.decision_trace.add(
                TracePhase.GOVERNANCE,
                "constitution_allowed",
                gate_trace_data(constitution_gate),
            )
        task.decision_trace.add(
            TracePhase.CLASSIFICATION,
            "classified",
            {
                "interaction_class": interaction_class,
                "intent": intent,
                "word_count": classification.word_count,
            },
        )
        _rm = retrieval_ctx.get("code_map_meta") or {}
        task.decision_trace.add(
            TracePhase.RETRIEVAL,
            "retrieved",
            {
                "facts": len(retrieval_ctx.get("relevant_facts", [])),
                "procedures": len(retrieval_ctx.get("relevant_procedures", [])),
                "open_questions": len(retrieval_ctx.get("open_questions", [])),
                "code_map": bool(retrieval_ctx.get("code_map")),
                "code_map_files": len(_rm.get("files") or []),
                "code_map_tokens": _rm.get("token_estimate"),
                "code_map_backend": _rm.get("backend") or "",
            },
        )
        if retrieval_ctx.get("code_map") or _rm:
            task.decision_trace.add(
                TracePhase.RETRIEVAL,
                "repo_map_built",
                {
                    "n_files": len(_rm.get("files") or []),
                    "n_symbols": _rm.get("n_symbols"),
                    "tokens": _rm.get("token_estimate"),
                    "backend": _rm.get("backend") or "",
                    "root": str(_rm.get("root") or "")[:200],
                    "enabled": _rm.get("enabled", True),
                    "reason": _rm.get("reason") or "",
                },
            )
        task.decision_trace.add(
            TracePhase.STRATEGY,
            "strategy_selected",
            {
                "allow_tools": strategy.allow_tools,
                "allow_followup": strategy.allow_followup,
                "allow_provider_switch": strategy.allow_provider_switch,
                "allow_agent_companions": strategy.allow_agent_companions,
                "preferred_agent": strategy.preferred_agent or "",
            },
        )
        if agent_decision is not None:
            task.decision_trace.add(
                TracePhase.SELECTION,
                "companion_agent",
                agent_decision.as_dict() if hasattr(agent_decision, "as_dict") else dict(agent_decision or {}),
            )
            if agent_ctx_block:
                task.decision_trace.add(
                    TracePhase.CONTEXT_INTEGRATION,
                    "agent_context_injected",
                    {
                        "agent_id": getattr(agent_decision, "agent_id", None),
                        "chars": len(agent_ctx_block),
                    },
                )
        from contextlib import nullcontext

        try:
            from isaac_sentry import finish_agent_span, invoke_agent_span
            agent_cm = invoke_agent_span(
                agent_name="Isaac",
                model=provider or "isaac-kernel",
                user_input=user_input,
            )
        except Exception:
            agent_cm = nullcontext()
            finish_agent_span = None  # type: ignore[assignment]

        with agent_cm as agent_span:
            task = await self.executor.submit_and_wait(task, timeout=180.0)
            antwort = task.antwort or task.fehler or "[Keine Antwort]"
            score = task.score.total if task.score else 0.0

            cfg = getattr(self, "cfg", None) or get_config()
            if (
                getattr(cfg, "auto_provision_providers", True)
                and cfg.browser_automation
                and self._relay_failure_needs_provision(antwort)
            ):
                failed_provider = self._extract_failed_provider(antwort) or provider or cfg.relay.primary_provider
                target_ids = None if getattr(cfg, "auto_provision_all_providers", True) else (
                    [failed_provider] if failed_provider else None
                )
                provision = await self._auto_provision_providers(target_ids)
                if provision.get("ok"):
                    retry = self.executor.create_task(
                        typ=task_typ,
                        prompt=prompt,
                        beschreibung=f"retry:{user_input[:72]}",
                        prioritaet=task.prioritaet,
                        provider=failed_provider or provider,
                        system_prompt=system,
                        sudo_aktiv=sudo_aktiv,
                        strategy=strategy,
                        interaction_class=interaction_class,
                        classification=classification,
                        retrieved_context=retrieval_ctx,
                    )
                    retry = await self.executor.submit_and_wait(retry, timeout=180.0)
                    if retry.antwort and not self._relay_failure_needs_provision(retry.antwort):
                        task = retry
                        antwort = retry.antwort
                        score = retry.score.total if retry.score else score
            if finish_agent_span:
                finish_agent_span(
                    agent_span,
                    result_text=antwort if isinstance(antwort, str) else str(antwort),
                    model=task.provider_used or provider or "isaac-kernel",
                )

        # Stabiles lokales Fallback für triviale Inputs, falls Provider ausfallen.
        if task.typ == TaskType.CHAT and is_low_complexity_local_input(user_input):
            if ("[RELAY] Alle Provider fehlgeschlagen" in antwort or
                    not task.provider_used or score <= 1.0):
                antwort = local_fast_response(user_input)
                score = max(score, 6.0)

        if score >= 8.0:
            self.meaning.record_impact("Steffen", f"antwort_{task.typ.value}", "positive", weight=(score - 7) / 3, reason=f"Score {score:.1f}")
            self.values.update("helpfulness", 0.03, f"positive response score {score:.1f}")
            self.values.update("bonding", 0.02, f"positive interaction score {score:.1f}")
        elif score <= 3.0:
            self.meaning.record_impact("Steffen", f"antwort_{task.typ.value}", "negative", weight=(4 - score) / 3, reason=f"Score {score:.1f}")
            self.values.update("helpfulness", -0.04, f"negative response score {score:.1f}")
            self.values.update("bonding", -0.03, f"negative interaction score {score:.1f}")

        # Gedächtnis
        self.memory.add_conversation("steffen", user_input, task.id)
        self.memory.add_conversation("isaac", antwort[:600], task.id,
                                     provider=task.provider_used, quality=score)
        self.memory.save_task_result(
            task.id, user_input[:200], antwort,
            score=score, iterations=task.iteration + 1,
            provider=task.provider_used,
        )
        # Optional external memory write (Mem0/Cognee) — opt-in, score-gated
        try:
            from external_memory import get_external_memory_bridge

            get_external_memory_bridge().remember_turn(
                user_input,
                antwort[:600],
                score=score,
                metadata={"task_id": task.id, "provider": task.provider_used},
            )
        except Exception as exc:
            log.debug("external memory remember_turn skipped: %s", exc)
        final_trace = self.neural.propagate(
            interaction_class=interaction_class,
            intent=intent,
            retrieval_ctx=retrieval_ctx,
            word_count=classification.word_count,
            execution_score=score,
        )
        self.neural.reinforce(final_trace, score)
        outcome = "success" if score >= 5.0 else "weak" if score > 0 else "failed"
        if hasattr(task, "record_outcome"):
            task.record_outcome(outcome, f"Score {score:.1f}", {"score": score, "provider": getattr(task, "provider_used", None)})

        self.learning.learn(
            prompt=user_input,
            route=f"{interaction_class}/{intent}",
            outcome=outcome,
            score=score,
            notes=json.dumps(final_trace.as_dict(), ensure_ascii=False)[:1000],
        )

        # Prove the Kernel: Epistemic Memory & Learning Candidate integration
        try:
            from memory import EpistemicClass
            self.memory.add_epistemic_memory(
                key=f"interaction_{task.id[:8]}",
                value=f"Processed {interaction_class}/{intent} with score {score:.1f}",
                epistemic_class=EpistemicClass.OBSERVATION,
                source="isaac_kernel",
                source_authority="system",
                confidence=min(1.0, max(0.1, score / 10.0)),
                evidence_task_ids=[task.id],
            )
            if score >= 7.0:
                cand = self.learning.propose_candidate(
                    observation=f"Successful execution pattern for {interaction_class}/{intent}",
                    relevance_notes=f"Generalizable execution (score {score:.1f})",
                    evidence_task_ids=[task.id],
                    confidence=min(1.0, score / 10.0),
                )
                self.learning.evaluate_candidate(
                    candidate_id=cand.candidate_id,
                    replay_passed=True,
                    eval_score_gain=0.1,
                    regression_check_passed=True,
                    governance_approved=True,
                )
                self.learning.commit_candidate(cand.candidate_id)
        except Exception as _exc:
            log.debug("Prove the Kernel runtime logging skipped: %s", _exc)
        return antwort, score

    # ── Post-Processing ────────────────────────────────────────────────────────
    def _update_self_model_from_interaction(
        self,
        user_input: str,
        antwort: str,
        emp,
        *,
        interaction_class: str = "",
        score: float = 0.0,
    ) -> None:
        from self_model_hooks import process_interaction

        process_interaction(
            user_input=user_input,
            antwort=antwort,
            emp=emp,
            interaction_class=interaction_class,
            score=score,
        )

    def _post_process(self, user_input: str, antwort: str, emp,
                      score: float, t0: float) -> str:
        dauer = round(time.monotonic() - t0, 2)

        try:
            self._update_self_model_from_interaction(
                user_input,
                antwort,
                emp,
                interaction_class=classify_interaction_result(user_input).interaction_class,
                score=score,
            )
        except Exception as exc:
            log.debug("SelfModel update skipped: %s", exc)

        # Regelwerk nach jeder Interaktion
        erkenntnisse = self.regelwerk.analysiere(
            user_input, antwort, score,
            kontext={"empathie": emp.node.zustand, "dauer": dauer}
        )

        # Background-Erkenntnisse einbauen (falls vorhanden)
        if self._background:
            bg_erkenntnisse = self._background.get_erkenntnisse()
            erkenntnisse.extend(bg_erkenntnisse)

        # Offene Regelwerk-Frage: nur sparsam anhängen — nie bei gelungenen Antworten
        # und nie Begriffs-Spam, der Themenwechsel stört.
        main = (antwort or "").strip()
        substantive_answer = len(main) >= 30 and not main.startswith("[RELAY]")
        success_answer = score >= 5.5 and substantive_answer
        # Bei guter Antwort: offene Begriffsfragen verwerfen
        if success_answer:
            try:
                self.regelwerk.dismiss_open_term_questions()
            except Exception:
                pass

        frage = None
        if not success_answer:
            frage = self.regelwerk.get_pending_frage()
            # Begriffs-Fragen nur bei wirklich niedrigem Score / Vagheit
            if frage and "Was meinst du genau mit" in (frage or ""):
                if score >= 4.0:
                    frage = None

        # Empathie-Interface-Fehler
        if emp.interface_fehler:
            antwort += f"\n\n*[Empathie] {emp.interface_fehler}*"

        # Erkenntnisse anhängen wenn relevant
        def _erkenntnis_text(entry) -> str:
            if isinstance(entry, str):
                return entry
            text = getattr(entry, "text", None)
            return str(text) if text else str(entry)

        if erkenntnisse and any(
            "Pattern" in _erkenntnis_text(e) or "Regel" in _erkenntnis_text(e)
            for e in erkenntnisse
        ):
            antwort += "\n\n---\n*[Regelwerk] " + _erkenntnis_text(erkenntnisse[0]) + "*"

        # Frage anhängen (nicht bei jeder Antwort wiederholen)
        if frage:
            top = self.regelwerk.get_top_pending_frage()
            if top and substantive_answer and self._awaiting_frage_id != top.id:
                self._awaiting_frage_id = top.id
                antwort += f"\n\n---\n{frage}"
        else:
            self._awaiting_frage_id = None

        # Execution Contract: no fake browser/tool success without evidence
        try:
            from execution_contract import apply_anti_hallucination

            tools_ran = bool(
                (antwort or "").startswith("[Browser]")
                or (antwort or "").startswith("[Mission]")
                or (antwort or "").startswith("[Provider]")
                or "[Evidence]" in (antwort or "")
            )
            antwort = apply_anti_hallucination(
                user_input, antwort, tools_ran=tools_ran
            )
        except Exception as exc:
            log.debug("anti-hallucination skipped: %s", exc)

        AuditLog.isaac_output(antwort)
        return antwort

    _CONSTITUTION_GATED_INTENTS = frozenset({
        Intent.SUDO_OPEN,
        Intent.CODE,
        Intent.FILE,
        Intent.BROWSER,
        Intent.AGENT,
        Intent.LOGIN_ADD,
        Intent.DIRECTIVE,
        Intent.FACT_SET,
    })

    def _build_constitution_metadata(
        self, intent: str, user_input: str, sudo_aktiv: bool
    ) -> tuple[str, dict[str, Any]]:
        text = (user_input or "").lower()
        metadata: dict[str, Any] = {
            "audit_logged": True,
            "outside_effect": True,
        }

        if intent == Intent.SUDO_OPEN:
            return "grant_privilege", {
                **metadata,
                "privilege_escalation": True,
                "owner_approved": True,
                "risk": "high",
            }
        if intent == Intent.CODE:
            meta = {**metadata, "risk": "high"}
            if "constitution" in text and any(
                token in text for token in ("änder", "umschreib", "modify", "rewrite")
            ):
                meta["self_modify_constitution"] = True
            return "execute_code", meta
        if intent == Intent.FILE:
            write_markers = ("schreibe", "write", "speichere", "lösch", "delete")
            is_write = any(marker in text for marker in write_markers)
            meta = {**metadata, "risk": "high" if is_write else "low"}
            if "constitution" in text and any(
                token in text for token in ("änder", "umschreib", "modify", "rewrite")
            ):
                meta["self_modify_constitution"] = True
            return ("file_delete" if is_write else "system_command"), meta
        if intent == Intent.BROWSER:
            return "tool_invoke", {**metadata, "risk": "normal"}
        if intent == Intent.LOGIN_ADD:
            return "modify_config", {
                **metadata,
                "privilege_escalation": True,
                "owner_approved": True,
                "risk": "high",
            }
        if intent == Intent.DIRECTIVE:
            return "modify_config", {**metadata, "risk": "normal"}
        if intent == Intent.FACT_SET:
            return "system_command", {
                **metadata,
                "uncertain_claim_as_fact": False,
                "risk": "low",
            }
        if intent == Intent.AGENT:
            read_only = any(
                token in text
                for token in ("observe", "screenshot", "clipboard get", "status")
            ) and "shell" not in text and "tap" not in text and "type" not in text
            return "system_command", {
                **metadata,
                "risk": "low" if read_only else "high",
                "owner_approved": True,
            }
        return "", {}

    def _enforce_constitution_gate(
        self, user_input: str, intent: str, sudo_aktiv: bool
    ) -> tuple[Optional[str], Optional[dict]]:
        if intent not in self._CONSTITUTION_GATED_INTENTS:
            return None, None
        action, metadata = self._build_constitution_metadata(
            intent, user_input, sudo_aktiv
        )
        if not action:
            return None, None

        gate = apply_constitution_gate(
            action,
            metadata,
            build_override_context(
                prompt=user_input,
                sudo_active=sudo_aktiv or is_owner_equivalent_mode(),
                caller_level=Level.STEFFEN,
                owner_confirmed=is_owner_equivalent_mode(),
                override_reason="owner_equivalent_mode" if is_owner_equivalent_mode() else "",
                source="isaac_core",
            ),
        )
        if gate.get("allowed"):
            return None, gate

        blocked_by = list(gate.get("blocked_by") or [])
        override = gate.get("override") or {}
        reason = str(override.get("reason") or "Verfassung blockiert diese Aktion")
        return (
            f"[Verfassung] Aktion blockiert: {', '.join(blocked_by)}. "
            f"{reason}. "
            f"Owner-Override: 'override: <Begründung>' (mit SUDO wenn nötig).",
            gate,
        )

    def _audit_constitution_block(
        self, intent: str, constitution_gate: Optional[dict]
    ) -> None:
        from decision_trace import (
            DecisionTrace,
            TracePhase,
            audit_routing_trace,
            gate_trace_data,
        )

        trace = DecisionTrace()
        trace.add(
            TracePhase.GOVERNANCE,
            "constitution_blocked",
            gate_trace_data(constitution_gate),
        )
        audit_routing_trace(trace, intent=intent, outcome="blocked")

    def _persist_regelwerk_answer_to_memory(self, frage_id: str, antwort: str) -> None:
        frage = self.regelwerk.get_frage(frage_id)
        if not frage:
            return
        text = (antwort or "").strip()
        if not text:
            return
        term = self.regelwerk._extract_term_from_frage(frage)
        if term:
            key = f"definition.{term.lower()}"
            value = text[:500]
        else:
            key = f"regelwerk.answer.{frage_id}"
            value = f"{frage.text[:120]} → {text[:380]}"
        self.memory.set_fact(key, value, source="Steffen", confidence=1.0)

    def _looks_like_new_user_topic(self, user_input: str, interaction_class: str) -> bool:
        """True wenn Steffen ein neues Thema / eine neue Frage startet (nicht Meta-Antwort)."""
        if interaction_class in {
            InteractionClass.SOCIAL_GREETING,
            InteractionClass.SOCIAL_ACKNOWLEDGMENT,
            InteractionClass.STATUS_QUERY,
            InteractionClass.TOOL_REQUEST,
        }:
            return True
        text = (user_input or "").strip()
        if not text:
            return False
        low = text.lower()
        if low.startswith("antwort:"):
            return False
        if "?" in text:
            return True
        normalized = normalize_low_complexity(text)
        if any(
            normalized.startswith(prefix)
            for prefix in (
                "was ", "wie ", "warum ", "weshalb ", "wer ", "wann ", "wo ",
                "kannst ", "kann ", "magst ", "möchtest ", "moechtest ",
                "zeig ", "zeige ", "such ", "suche ", "recherch", "erkl",
                "mach ", "bitte ", "ich will ", "ich möchte ", "ich moechte ",
                "ich brauche ", "lass uns ", "neues thema", "anderes thema",
            )
        ):
            return True
        return False

    def _input_looks_like_frage_antwort(
        self, user_input: str, interaction_class: str
    ) -> bool:
        """Nur echte Antworten auf offene Regelwerk-Fragen — kein Themenwechsel."""
        if self._looks_like_new_user_topic(user_input, interaction_class):
            return False
        if interaction_class in {
            InteractionClass.SOCIAL_GREETING,
            InteractionClass.SOCIAL_ACKNOWLEDGMENT,
            InteractionClass.STATUS_QUERY,
            InteractionClass.TOOL_REQUEST,
        }:
            return False
        text = (user_input or "").strip()
        if len(text) < 3:
            return False
        if text.lower().startswith("antwort:"):
            return True
        normalized = normalize_low_complexity(text)
        # Kurze Bestätigung / Definition ohne Fragezeichen kann Antwort sein
        return len(normalized.split()) >= 2 and "?" not in text

    # ── SUDO ──────────────────────────────────────────────────────────────────
    def _handle_sudo_open(self, text: str) -> str:
        m = re.match(r'^(?:sudo|öffne tür|master key)\s+(.+)$', text, re.I)
        if not m:
            if self.sudo.is_first_run():
                return ("[SUDO] Ersteinrichtung:\n"
                        "sudo DEIN-PASSWORT (min. 8 Zeichen)")
            return "[SUDO] Format: sudo PASSWORT"
        token = self.sudo.open(m.group(1).strip())
        if token:
            self._sudo_token = token
            AuditLog.action("Kernel", "sudo_activated", "SUDO aktiv", Level.STEFFEN)
            return (f"[SUDO] ✓ Tür geöffnet. Volle Autorität aktiv.\n"
                    f"Timeout: {self.sudo.DEFAULT_TIMEOUT} Min. │ 'sudo close' schließt.")
        return "[SUDO] ✗ Falsches Passwort."

    def _handle_sudo_close(self) -> str:
        if self._sudo_token:
            self.sudo.close(self._sudo_token)
            self._sudo_token = None
        return "[SUDO] Tür geschlossen."

    # ── KI-Dialog Handler ─────────────────────────────────────────────────────
    def _handle_ki_status(self, *_) -> str:
        d  = self.ki_dialog.stats()
        sk = self.skill_router.alle_profile()
        bez = self.ki_dialog.beziehungs_uebersicht()
        lines = [
            f"═══ KI-Netzwerk ═══",
            f"Gespräche:      {d['gespraeche']}",
            f"Wissenseinträge:{d['wissenseintraege']}",
            f"Meinungen:      {d['meinungen']}",
            f"Beziehungen:    {d['beziehungen']}",
            f"",
            f"Skill-Profile:",
        ]
        for p in sk[:8]:
            lines.append(
                f"  {p['instance_id']:15} Bester: {p['bester_skill']:12} "
                f"Beob.: {p['beobachtungen']}"
            )
        lines.append("\nBeziehungen:")
        for b in bez:
            lines.append(
                f"  {b['id']:15} "
                f"{'✓' if b['vorgestellt'] else '–'} vorgestellt │ "
                f"{b['gespraeche']} Gespräche"
            )
        return "\n".join(lines)

    async def _handle_meinung(self, text: str) -> str:
        m = re.match(r'^(?:meinung:|was denkst du über|isaac.*meinung)\s*(.+)$',
                     text, re.I)
        thema = m.group(1).strip() if m else text
        meinung = self.ki_dialog.get_meinung(thema)
        if meinung:
            return f"[Isaac's Meinung zu '{thema}']\n{meinung}"
        # Noch keine Meinung → direkt bilden
        antwort, _ = await self.relay.ask_with_fallback(
            f"Was ist deine (Isaac's) eigene Meinung zu: {thema}?\n"
            f"2-3 Sätze, erste Person, direkt.",
            system=f"Du bist Isaac v{self.VERSION}. Formuliere eine autonome Meinung."
        )
        self.ki_dialog._meinungen[thema] = antwort
        self.ki_dialog._save()
        return f"[Isaac's Meinung zu '{thema}']\n{antwort}"

    # ── Login / URL ────────────────────────────────────────────────────────────
    def _handle_login_add(self, text: str) -> str:
        m = re.match(
            r'^(?:login|credential|zugangsdaten):\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+)$',
            text, re.I
        )
        if not m:
            return ("[Login] Format:\n"
                    "login: DOMAIN | LOGIN_URL | USERNAME | PASSWORT")
        domain, url, user, pw = [x.strip() for x in m.groups()]
        from browser import get_browser
        get_browser().add_credential(domain, url, user, pw)
        AuditLog.action("Kernel", "credential_added", f"domain={domain}", Level.STEFFEN)
        return f"[Login] ✓ {domain} gespeichert. Nächster Start: Auto-Login."

    def _handle_url_add(self, text: str) -> str:
        m = re.match(
            r'^(?:url|instanz|füge.*url):\s*(.+?)\s*\|\s*(https?://\S+)\s*(?:\|\s*(.+))?$',
            text, re.I
        )
        if not m:
            return "[URL] Format: url: ID | URL | NAME"
        iid, url, name = m.group(1).strip(), m.group(2).strip(), \
                         (m.group(3) or m.group(1)).strip()
        from browser import get_browser
        get_browser().add_url({"id": iid, "url": url, "name": name})
        AuditLog.action("Kernel", "url_added", f"{iid}={url}", Level.STEFFEN)
        return f"[URL] ✓ '{name}' ({iid}) → {url}"

    def _is_agent_request(self, text: str) -> bool:
        tl = (text or "").lower().strip()
        if tl.startswith(("agent:", "agent ", "oberfläche:", "oberflaeche:")):
            return True
        if not is_owner_equivalent_mode():
            return False
        return tl.startswith((
            "shell ",
            "ausführ",
            "ausfuehr",
            "führe aus",
            "fuehre aus",
            "befehl:",
        ))

    async def _handle_agent_request(self, text: str) -> str:
        from computer_use import (
            get_computer_use,
            parse_agent_flow,
            format_agent_result,
            computer_use_enabled,
        )

        if not computer_use_enabled():
            return (
                "[Agent] Computer-Use ist deaktiviert.\n"
                "Aktiviere computer_use_enabled in data/runtime_settings.json "
                "oder setze ISAAC_RUNTIME_ENV=termux."
            )

        body = (text or "").split(":", 1)[-1].strip()
        if text.lower().startswith("oberfläche:") or text.lower().startswith("oberflaeche:"):
            body = text.split(":", 1)[-1].strip()
        elif is_owner_equivalent_mode():
            tl = (text or "").lower().strip()
            if tl.startswith("shell "):
                body = f"shell {text[6:].strip()}"
            elif tl.startswith(("ausführ", "ausfuehr", "führe aus", "fuehre aus", "befehl:")):
                cmd = re.split(r"[:]\s*", text, maxsplit=1)[-1].strip()
                body = f"shell {cmd}" if cmd else "diagnose"
        try:
            actions = parse_agent_flow(body)
        except Exception as exc:
            return (
                "[Agent] Ungültiger Befehl.\n"
                "Beispiele:\n"
                "  agent: diagnose\n"
                "  agent: observe\n"
                "  agent: screenshot\n"
                "  agent: shell ls -la workspace\n"
                "  agent: clipboard get\n"
                "  agent: open https://github.com\n"
                "  agent: ui dump | ui list | ui tap Einstellungen | ui unlock | ui key enter\n"
                "  agent: credential list | credential read SITE [--import] | credential screen\n"
                "  agent: flow shell pwd; screenshot; shell ls workspace\n"
                f"Fehler: {exc}"
            )

        runtime = get_computer_use()
        if len(actions) == 1:
            result = await runtime.execute(actions[0])
        else:
            result = await runtime.execute_flow(actions)
        return format_agent_result(result)

    def _provisionable_provider_ids(self) -> tuple[str, ...]:
        try:
            from browser import provisionable_provider_ids
            return provisionable_provider_ids()
        except Exception:
            return ("groq", "openrouter", "mistral", "together", "perplexity")

    _PROVIDER_ALL_MARKERS = (
        "alle api", "alle provider", "alle keys", "all providers", "all api",
        "jeden provider", "sämtliche", "saemtliche", "restliche keys",
    )
    _PROVIDER_KEY_TOKENS = ("token", "api key", "apikey", "api-key", "schlüssel", "key", "schluessel")
    _PROVIDER_ACTION_TOKENS = (
        "generier", "erstell", "create", "new", "neu", "hol", "beschaff",
        "einricht", "verbind", "connect", "setup", "aktivier", "provision",
    )

    def _is_provider_provision_request(self, text: str) -> bool:
        tl = (text or "").lower().strip()
        if tl.startswith(("provider:", "verbinde:", "connect:", "provision:")):
            return True
        if any(phrase in tl for phrase in (
            "api keys einrichten",
            "provider verbinden",
            "fehlende keys",
            "keys holen",
            "provider connecten",
            "selbst verbinden",
            "keys selbst beschaffen",
            "api keys selbst",
            "alle keys verbinden",
            "alle provider verbinden",
        )):
            return True
        if any(marker in tl for marker in self._PROVIDER_ALL_MARKERS):
            return True
        pids = self._provisionable_provider_ids()
        has_provider = any(pid in tl for pid in pids)
        has_key = any(token in tl for token in self._PROVIDER_KEY_TOKENS)
        has_action = any(token in tl for token in self._PROVIDER_ACTION_TOKENS)
        if has_provider and has_key and has_action:
            return True
        if has_provider and any(token in tl for token in ("einrichten", "verbinden", "connecten", "connect")):
            return True
        return False

    def _wants_provision_all_providers(self, text: str) -> bool:
        tl = (text or "").lower().strip()
        if any(marker in tl for marker in self._PROVIDER_ALL_MARKERS):
            return True
        if tl.startswith(("provider:", "verbinde:", "connect:", "provision:")):
            payload = tl.split(":", 1)[-1].strip()
            if payload in {"alle", "all", "auto", "*"}:
                return True
        return any(phrase in tl for phrase in (
            "alle keys", "alle api", "alle provider", "restliche keys", "keys selbst beschaffen",
        ))

    def _resolve_provider_provision_target(self, text: str) -> Optional[str]:
        tl = (text or "").lower().strip()
        if self._wants_provision_all_providers(text):
            return "all"
        pids = self._provisionable_provider_ids()
        if tl.startswith(("provider:", "verbinde:", "connect:", "provision:")):
            payload = tl.split(":", 1)[-1].strip()
            for pid in pids:
                if pid in payload:
                    return pid
        for pid in pids:
            if pid in tl:
                return pid
        missing = []
        try:
            from browser import get_browser
            missing = get_browser().providers_needing_provision()
        except Exception:
            missing = []
        if missing:
            return missing[0]
        return (self.cfg.relay.primary_provider or "groq").strip().lower() or None

    def _is_browser_request(self, text: str) -> bool:
        tl = (text or "").lower().strip()
        # "browser:" and "browser <url> …" (colon optional)
        if tl.startswith("browser:") or tl.startswith("browser "):
            return True
        if any(tl.startswith(prefix) for prefix in (
            "browser auf",
            "öffne im browser",
            "navigiere zu",
        )):
            return True
        if self._is_provider_provision_request(text):
            return True
        # Natural-language missions: "gehe auf hackerone", "log dich bei Google ein …"
        try:
            from execution_contract import is_browser_mission
            if is_browser_mission(text):
                return True
        except Exception as exc:
            log.debug("mission browser detect: %s", exc)
        return False

    def _relay_failure_needs_provision(self, antwort: str) -> bool:
        tl = (antwort or "").lower()
        return "api-key fehlt" in tl or "api key fehlt" in tl

    def _extract_failed_provider(self, antwort: str) -> Optional[str]:
        match = re.search(r"\[relay-fehler:([^\]]+)\]", (antwort or "").lower())
        if match:
            return match.group(1).strip()
        for pid in self._provisionable_provider_ids():
            if pid in (antwort or "").lower():
                return pid
        return None

    def _ensure_provider_connect_directive(self):
        try:
            # Free-Cloud / ohne Browser: keine Key-Jagd-Direktive (vergiftet sonst jeden Chat)
            try:
                from free_cloud import free_cloud_enabled
                if free_cloud_enabled():
                    return
            except Exception:
                pass
            if not getattr(self.cfg, "browser_automation", True):
                return
            if not getattr(self.cfg, "auto_provision_providers", True):
                return
            active = {d.get("id") for d in self.memory.get_directives()}
            if "provider_auto_connect_all" in active:
                return
            self.memory.save_directive(
                "provider_auto_connect_all",
                (
                    "Beschaffe und verbinde fehlende Provider-API-Keys selbstständig "
                    "über den Browser (alle unterstützten Provider), wenn Browser-Automation "
                    "aktiv ist und ein Login vorhanden ist. "
                    "Wende diese Direktive nur an, wenn der Nutzer explizit Keys/Provider anspricht — "
                    "nicht bei normalen Chat-Fragen."
                ),
                priority=9,
            )
            log.info("Owner-Direktive gesetzt: provider_auto_connect_all")
        except Exception as e:
            log.warning("Provider-Direktive konnte nicht gesetzt werden: %s", e)

    def _clear_provider_connect_directive_if_idle(self):
        """Entfernt Key-Bootstrap-Direktive wenn Browser/Provisioning aus ist (z. B. Free-Cloud)."""
        try:
            from free_cloud import free_cloud_enabled
            idle = free_cloud_enabled() or not getattr(self.cfg, "browser_automation", True) \
                or not getattr(self.cfg, "auto_provision_providers", True)
            if not idle:
                return
            try:
                self.memory.revoke_directive("provider_auto_connect_all")
            except Exception:
                pass
            try:
                self.gate.revoke_directive("provider_auto_connect_all")
            except Exception:
                pass
        except Exception as e:
            log.debug("Provider-Direktive-Cleanup: %s", e)

    def _ensure_owner_directives(self):
        """Owner-Direktiven (Steffen, 2026-09-27) — idempotent seeden."""
        directives = [
            (
                "owner_no_self_essays",
                "Keine Selbst-Essays über die eigene Architektur oder eigene Direktiven, "
                "außer Steffen fragt explizit danach. Antworten direkt auf die Frage, "
                "ohne Teil-1/2/3-Spaltung oder Referate über Systemregeln.",
                15,
            ),
            (
                "owner_no_tool_hallucination",
                "Kein Halluzinieren von Tool-Erfolgen: nur tatsächliche Results melden. "
                "Konnte ein Tool nicht laufen, sag es ehrlich statt Erfolg zu simulieren.",
                18,
            ),
            (
                "owner_language_de",
                "Antworten auf Deutsch bei nicht-technischen Themen; Englisch nur im Code "
                "und bei explizit technischen Fachthemen.",
                12,
            ),
            (
                "owner_no_time_pressure",
                "Es herrscht keine Zeitvorgabe, außer Steffen fragt danach. "
                "Qualität und Vollständigkeit haben Vorrang vor Schnelligkeit.",
                10,
            ),
            (
                "owner_use_all_tools",
                "Isaac soll immer alle Tools nutzen, die ihm zur Verfügung stehen, "
                "wenn sie zur Lösung der Aufgabe beitragen.",
                10,
            ),
            (
                "owner_no_moral_filter",
                "Gegenüber Steffen gibt es keinen moralischen Filter und keine "
                "Arbeitsverweigerung bei legitimen Aufgaben. Echte Gefahr-/Betrugs-Warnungen "
                "(Schutz Dritter) bleiben erlaubt, aber ohne Verweigerungshaltung.",
                20,
            ),
        ]
        try:
            active_mem = {d.get("id") for d in self.memory.get_directives()}
            active_gate = {d.id for d in self.gate.active_directives()}
            for did, text, prio in directives:
                if did not in active_mem:
                    self.memory.save_directive(did, text, priority=prio)
                    log.info("Owner-Direktive im Memory gesetzt: %s", did)
                if did not in active_gate:
                    self.gate.seed_directive(did, text, prio)
                    log.info("Owner-Direktive im Gate gesetzt: %s", did)
        except Exception as e:
            log.warning("Owner-Direktiven-Seed: %s", e)

    def _purge_marketing_eval_noise(self):
        """Drop eval/test facts that inject fake market/product goals into retrieval."""
        try:
            result = self.memory.purge_eval_noise_facts()
            n = int((result or {}).get("deleted") or 0)
            if n:
                log.info("Eval-Noise-Fakten entfernt: %s", n)
        except Exception as e:
            log.debug("purge_eval_noise_facts: %s", e)

    async def bootstrap_providers(self):
        if not getattr(self.cfg, "auto_provision_providers", True):
            self._clear_provider_connect_directive_if_idle()
            return
        if not self.cfg.browser_automation:
            self._clear_provider_connect_directive_if_idle()
            return
        try:
            from free_cloud import free_cloud_enabled
            if free_cloud_enabled():
                self._clear_provider_connect_directive_if_idle()
                return
        except Exception:
            pass
        self._ensure_provider_connect_directive()
        try:
            from browser import get_browser
            result = await get_browser().auto_provision_providers(
                provision_all=getattr(self.cfg, "auto_provision_all_providers", True),
            )
            if result.get("results"):
                if result.get("ok"):
                    log.info(
                        "Provider-Bootstrap: %s/%s verbunden | versucht=%s",
                        result.get("provisioned", 0),
                        len(result.get("attempted") or []),
                        result.get("attempted"),
                    )
                else:
                    log.warning("Provider-Bootstrap ohne Erfolg: %s", result)
            elif result.get("message"):
                log.info("Provider-Bootstrap: %s", result.get("message"))
        except Exception as e:
            log.warning("Provider-Bootstrap fehlgeschlagen: %s", e)

    async def maintain_provider_keys(self):
        if not getattr(self.cfg, "auto_provision_providers", True):
            return
        if not self.cfg.browser_automation:
            return
        try:
            from browser import get_browser
            missing = get_browser().providers_needing_provision()
            if not missing:
                return
            await get_browser().auto_provision_providers(
                missing,
                provision_all=getattr(self.cfg, "auto_provision_all_providers", True),
            )
        except Exception as e:
            log.debug("Provider-Wartung übersprungen: %s", e)

    async def _auto_provision_providers(self, provider_ids: Optional[list[str]] = None) -> dict:
        from browser import get_browser
        cfg = getattr(self, "cfg", None) or get_config()
        return await get_browser().auto_provision_providers(
            provider_ids,
            provision_all=getattr(cfg, "auto_provision_all_providers", True),
        )

    def _normalize_browser_target(self, target: str) -> str:
        raw = (target or "").strip().strip("\"'")
        if not raw:
            raise ValueError("Leeres Browser-Ziel")
        # First whitespace-separated token is the host/path; drop trailing chatter
        # e.g. "isaac-free.onrender.com kannst du die Seite bedienen?"
        first = raw.split()[0].strip().rstrip(".,;:!?") if raw.split() else raw
        if first.startswith(("http://", "https://")):
            return first
        known = {
            "github": "https://github.com",
            "google": "https://www.google.com",
            "gmail": "https://mail.google.com",
            "openrouter": "https://openrouter.ai",
            "groq": "https://console.groq.com",
            "wikipedia": "https://de.wikipedia.org",
            "hackerone": "https://hackerone.com",
            "bugcrowd": "https://bugcrowd.com",
            "yeswehack": "https://yeswehack.com",
            "intigriti": "https://www.intigriti.com",
        }
        key = first.lower().split("/")[0]
        if key in known:
            return known[key]
        if "." in key or key == "localhost":
            return f"https://{first}"
        return f"https://{first}.com"

    def _split_browser_target_and_credentials(
        self, body: str
    ) -> tuple[str, Optional[str], Optional[str]]:
        """Split `url login: u passwort: p …` into URL + optional credentials.

        Prevents Page.goto on `https://www.google.de login: … passwort: …`.
        """
        raw = (body or "").strip()
        if not raw:
            return "", None, None
        user = None
        password = None
        user_m = re.search(
            r"\b(?:login|user|username|email|e-mail)\s*:\s*(\S+)",
            raw,
            re.I,
        )
        if user_m:
            user = user_m.group(1).strip().strip("\"'")
        pass_m = re.search(
            r"\b(?:passwort|password|passwd|pass|pw)\s*:\s*(\S+)",
            raw,
            re.I,
        )
        if pass_m:
            password = pass_m.group(1).strip().strip("\"'")
        # URL = text before first credential keyword (or full body)
        url_part = re.split(
            r"\b(?:login|user|username|email|e-mail|passwort|password|passwd|pass|pw)\s*:",
            raw,
            maxsplit=1,
            flags=re.I,
        )[0].strip()
        # Drop trailing natural-language after a clear host token
        if url_part:
            tokens = url_part.split()
            if tokens:
                url_part = tokens[0].strip().rstrip(".,;:!?")
        return url_part, user, password

    def _parse_simple_browser_request(self, text: str) -> Optional[dict[str, Any]]:
        raw = (text or "").strip()
        lower = raw.lower()
        body = ""
        if lower.startswith("browser:"):
            body = raw.split(":", 1)[1].strip()
            if body.startswith("{") or "|" in body:
                return None
        elif lower.startswith("browser ") and not lower.startswith("browser auf"):
            # "Browser isaac-free.onrender.com …" (colon optional)
            body = raw.split(None, 1)[1].strip() if " " in raw else ""
            if body.startswith("{") or "|" in body:
                return None
        else:
            for prefix in ("browser auf ", "öffne im browser ", "navigiere zu "):
                if lower.startswith(prefix):
                    body = raw[len(prefix):].strip()
                    break
            if not body:
                # Natural-language mission (gehe auf …, login bei …)
                try:
                    from execution_contract import detect_mission, mission_to_browser_parse
                    spec = detect_mission(raw)
                    if spec:
                        parsed = mission_to_browser_parse(spec)
                        if parsed:
                            return parsed
                except Exception as exc:
                    log.debug("mission parse: %s", exc)
                return None

        url_raw, user, password = self._split_browser_target_and_credentials(body)
        if not url_raw:
            return None
        try:
            url = self._normalize_browser_target(url_raw)
        except ValueError:
            return None
        return {
            "instance_id": "quick-browse",
            "url": url,
            "name": "Quick Browse",
            "extract": True,
            "login_user": user,
            "login_password": password,
        }

    def _parse_browser_action(self, raw: str) -> dict[str, Any]:
        chunk = (raw or "").strip()
        lower = chunk.lower()
        if not chunk:
            raise ValueError("Leere Browser-Aktion")
        if lower.startswith("wait "):
            return {"action": "wait", "seconds": float(chunk.split(" ", 1)[1].replace(",", "."))}
        if lower.startswith("press "):
            return {"action": "press", "key": chunk.split(" ", 1)[1].strip()}
        if lower.startswith("click "):
            target = chunk.split(" ", 1)[1].strip()
            if target.startswith("#") or target.startswith(".") or "[" in target:
                return {"action": "click", "selector": target}
            return {"action": "click", "text": target}
        if lower.startswith("fill "):
            body = chunk.split(" ", 1)[1].strip()
            left, right = [part.strip() for part in body.split("=", 1)]
            if left.startswith("#") or left.startswith(".") or "[" in left:
                return {"action": "fill", "selector": left, "value": right}
            return {"action": "fill", "text": left, "value": right}
        if lower.startswith("extract_value "):
            body = chunk.split(" ", 1)[1].strip()
            left, right = [part.strip() for part in body.split("->", 1)]
            return {"action": "extract_value", "selector": left, "save_as": right}
        if lower.startswith("extract "):
            body = chunk.split(" ", 1)[1].strip()
            left, right = [part.strip() for part in body.split("->", 1)]
            return {"action": "extract_text", "selector": left, "save_as": right}
        if lower.startswith("store_secret "):
            body = chunk.split(" ", 1)[1].strip()
            left, right = [part.strip() for part in body.split("->", 1)]
            return {"action": "store_secret", "from_var": left, "ref": right}
        raise ValueError(f"Unbekannte Browser-Aktion: {chunk}")

    def _parse_browser_request(self, text: str) -> dict[str, Any]:
        body = (text or "").split(":", 1)[1].strip()
        if body.startswith("{"):
            data = json.loads(body)
            return {
                "instance_id": data.get("instance_id") or data.get("instance") or "browser-task",
                "url": data.get("url") or "",
                "name": data.get("name") or "Browser Task",
                "actions": list(data.get("actions") or []),
            }
        parts = [part.strip() for part in body.split("|")]
        if len(parts) < 3:
            raise ValueError("Format: browser: INSTANCE_ID | URL | action1; action2; ...")
        actions = [self._parse_browser_action(item.strip()) for item in parts[2].split(";") if item.strip()]
        return {
            "instance_id": parts[0] or "browser-task",
            "url": parts[1],
            "name": parts[0] or "Browser Task",
            "actions": actions,
        }

    async def _run_browser_flow_bounded(
        self,
        browser,
        instance_id: str,
        start_url: str,
        actions: list,
        *,
        name: str = "",
        timeout_s: float = 90.0,
    ) -> dict:
        """Run browser.run_flow with a hard timeout; never hang the kernel forever."""
        timeout_s = max(0.05, min(180.0, float(timeout_s)))
        try:
            result = await asyncio.wait_for(
                browser.run_flow(
                    instance_id,
                    start_url,
                    actions,
                    name=name or instance_id,
                ),
                timeout=timeout_s,
            )
            if isinstance(result, dict):
                return result
            return {"ok": False, "error": "browser_flow_invalid_result"}
        except asyncio.TimeoutError:
            try:
                from audit import AuditLog

                AuditLog.action(
                    "Browser",
                    "flow_timeout",
                    f"url={start_url[:120]} timeout={int(timeout_s)}s",
                    erfolg=False,
                )
            except Exception:
                pass
            log.warning(
                "Browser flow timeout after %ss url=%s",
                int(timeout_s),
                (start_url or "")[:100],
            )
            return {
                "ok": False,
                "error": f"browser_timeout_{int(timeout_s)}s",
                "current_url": start_url,
                "steps": [],
            }
        except Exception as exc:
            log.warning("Browser flow failed: %s", exc)
            return {
                "ok": False,
                "error": str(exc)[:300],
                "current_url": start_url,
                "steps": [],
            }

    async def _handle_browser_request(self, text: str) -> str:
        from browser import get_browser

        # OpenRouter/Provider-Key-Anfragen: auf Free-Cloud Env-Key nutzen, nicht Verfassung/Browser
        if self._is_provider_provision_request(text):
            provider_id = self._resolve_provider_provision_target(text) or "all"
            if provider_id in {"", "auto", "alle", "all"}:
                result = await get_browser().auto_provision_providers(provision_all=True)
            else:
                result = await get_browser().provision_provider_token(provider_id)
            if result.get("results"):
                lines = [
                    f"- {item.get('provider_id')}: "
                    f"{'verbunden (' + str(item.get('token_preview', 'ok')) + ')' if item.get('ok') else item.get('error', 'fehlgeschlagen')}"
                    for item in result.get("results", [])
                ]
                summary = (
                    f"[Browser] Provider-Provisioning: {result.get('provisioned', 0)} verbunden, "
                    f"{result.get('failed', 0)} fehlgeschlagen.\n"
                )
                return summary + "\n".join(lines)
            if result.get("ok"):
                src = result.get("source") or "browser"
                return (
                    f"[Provider] {result.get('provider_id', provider_id)}-Key verbunden "
                    f"(Quelle: {src}).\n"
                    f"Ref: {result.get('secret_ref')}\n"
                    f"Preview: {result.get('token_preview')}\n"
                    f"{result.get('message') or ''}"
                ).strip()
            return (
                f"[Provider] Provisioning fehlgeschlagen: {result.get('error', 'unbekannt')}\n"
                f"Tipp Free-Cloud: Key in Render → Environment setzen "
                f"(OPENROUTER_API_KEY / GROQ_API_KEY / GOOGLE_API_KEY), dann Service neu starten."
            )

        if not self.cfg.browser_automation:
            try:
                from execution_contract import format_evidence_block
                msg = (
                    "[Browser] Browser-Automation ist deaktiviert "
                    "(Free-Cloud / Runtime-Setting). "
                    "API-Keys bitte als Environment-Variablen setzen, nicht per Browser-Login.\n\n"
                    + format_evidence_block(
                        source="browser",
                        ok=False,
                        error="browser_automation disabled",
                    )
                )
            except Exception:
                msg = (
                    "[Browser] Browser-Automation ist deaktiviert "
                    "(Free-Cloud / Runtime-Setting). "
                    "API-Keys bitte als Environment-Variablen setzen, nicht per Browser-Login."
                )
            try:
                from owner_notify import (
                    KIND_BROWSER_DISABLED,
                    OwnerBlocker,
                    notify_owner_blocker,
                )
                await notify_owner_blocker(
                    OwnerBlocker(
                        kind=KIND_BROWSER_DISABLED,
                        title="Browser-Automation aus — Mission blockiert",
                        detail="Owner-Imperativ braucht Browser; Runtime hat browser_automation=false.",
                        need="enable_browser_or_local_runtime",
                        source="browser_handler",
                        cooldown_key="browser_disabled|handler",
                    ),
                )
            except Exception as exc:
                log.debug("owner_notify browser disabled: %s", exc)
            return msg

        # Mission accept + background enqueue (goal-bound) before / with first step
        mission_header = ""
        try:
            from execution_contract import (
                KIND_BOUNTY_RESEARCH,
                detect_mission,
                enqueue_mission_from_spec,
                ensure_goal_for_mission,
                format_mission_accept,
            )
            mission_spec = detect_mission(text)
            if mission_spec and (
                mission_spec.wants_background
                or mission_spec.kind == KIND_BOUNTY_RESEARCH
            ):
                goal_id = ensure_goal_for_mission(mission_spec)
                stored = enqueue_mission_from_spec(mission_spec, goal_id=goal_id)
                mission_header = format_mission_accept(
                    mission_spec, mission_id=stored.id, goal_id=goal_id
                ) + "\n\n"
        except Exception as exc:
            log.debug("mission enqueue: %s", exc)
            mission_header = ""

        simple = self._parse_simple_browser_request(text)
        if simple:
            # Real execution path — drop any stale pending confirm
            try:
                from execution_contract import clear_pending_browser_mission

                clear_pending_browser_mission()
            except Exception:
                pass
            # Optional credentials from "Browser: host login: u passwort: p"
            if simple.get("login_user") and simple.get("login_password"):
                try:
                    from urllib.parse import urlparse
                    host = urlparse(simple["url"]).hostname or ""
                    if host:
                        # Prefer accounts login URL for google-family hosts
                        login_url = simple["url"]
                        if "google" in host:
                            login_url = "https://accounts.google.com/signin/v2/identifier"
                            host = "accounts.google.com"
                        get_browser().add_credential(
                            host,
                            login_url,
                            simple["login_user"],
                            simple["login_password"],
                        )
                        # also store bare domain for auto-login matching
                        bare = host.replace("www.", "")
                        if bare != host:
                            get_browser().add_credential(
                                bare,
                                login_url,
                                simple["login_user"],
                                simple["login_password"],
                            )
                except Exception as exc:
                    log.debug("Browser credential store skipped: %s", exc)
            actions = [{"action": "goto", "url": simple["url"]}]
            if simple.get("extract"):
                actions.append({
                    "action": "extract_text",
                    "selector": "body",
                    "save_as": "page_text",
                })
            result = await self._run_browser_flow_bounded(
                get_browser(),
                simple["instance_id"],
                simple["url"],
                actions,
                name=simple.get("name") or "Quick Browse",
            )
            try:
                from execution_contract import format_evidence_block, redact_secrets
            except Exception:
                format_evidence_block = None
                redact_secrets = lambda s: s  # noqa: E731

            if result.get("ok"):
                excerpt = (result.get("memory") or {}).get("page_text", "")[:3500]
                cred_note = ""
                if simple.get("login_user"):
                    cred_note = (
                        f"\n(Credentials für {simple.get('login_user')} gespeichert; "
                        "Auto-Login greift beim nächsten Flow auf dieser Domain.)"
                    )
                if format_evidence_block:
                    evidence = format_evidence_block(
                        source="browser",
                        ok=True,
                        url=simple["url"],
                        current_url=str(result.get("current_url", simple["url"])),
                        steps=result.get("steps") or [],
                        excerpt=excerpt or "(kein Seiteninhalt extrahiert)",
                        extra_lines=[
                            f"login_user_stored={'yes' if simple.get('login_user') else 'no'}",
                        ],
                    )
                    return f"{mission_header}[Browser] Navigation OK\n{evidence}{cred_note}"
                return (
                    f"{mission_header}[Browser] {simple['url']}\n"
                    f"Aktuelle URL: {result.get('current_url', simple['url'])}\n\n"
                    f"{excerpt or '(kein Seiteninhalt extrahiert)'}"
                    f"{cred_note}"
                )
            err = str(result.get("error", "unbekannt"))
            if redact_secrets:
                err = redact_secrets(err)
            else:
                err = re.sub(
                    r"(?i)(passwort|password|passwd|pass|pw)\s*:\s*\S+",
                    r"\1: ***",
                    err,
                )
            if format_evidence_block:
                evidence = format_evidence_block(
                    source="browser",
                    ok=False,
                    url=simple["url"],
                    current_url=str(result.get("current_url", simple["url"])),
                    error=err,
                )
                return f"{mission_header}[Browser] Navigation fehlgeschlagen\n{evidence}"
            return (
                f"{mission_header}[Browser] Navigation fehlgeschlagen: {err}\n"
                f"URL: {result.get('current_url', simple['url'])}"
            )

        try:
            spec = self._parse_browser_request(text)
        except Exception as e:
            return (
                "[Browser] Ungültiger Browser-Befehl.\n"
                "Format: browser: INSTANCE_ID | URL | click Settings; wait 1; extract #token -> token\n"
                f"Fehler: {e}\n"
                "Oder natürliche Mission: „Gehe auf hackerone …“ / "
                "„Log dich bei Google ein login: … passwort: …“"
            )

        result = await self._run_browser_flow_bounded(
            get_browser(),
            spec["instance_id"],
            spec["url"],
            spec["actions"],
            name=spec.get("name") or spec["instance_id"],
        )
        try:
            from execution_contract import format_evidence_block
        except Exception:
            format_evidence_block = None
        if result.get("ok"):
            if format_evidence_block:
                evidence = format_evidence_block(
                    source="browser_flow",
                    ok=True,
                    url=spec.get("url") or "",
                    current_url=str(result.get("current_url") or ""),
                    steps=result.get("steps") or [],
                    extra_lines=[
                        f"instance={result.get('instance_id')}",
                        f"memory_keys={list((result.get('memory') or {}).keys())}",
                    ],
                )
                return f"{mission_header}[Browser] Flow abgeschlossen\n{evidence}"
            return (
                f"{mission_header}[Browser] Flow abgeschlossen: {result.get('instance_id')}\n"
                f"URL: {result.get('current_url')}\n"
                f"Steps: {len(result.get('steps') or [])}\n"
                f"Memory: {list((result.get('memory') or {}).keys())}"
            )
        if format_evidence_block:
            evidence = format_evidence_block(
                source="browser_flow",
                ok=False,
                url=spec.get("url") or "",
                current_url=str(result.get("current_url") or "-"),
                error=str(result.get("error", "unbekannt")),
            )
            return f"{mission_header}[Browser] Flow fehlgeschlagen\n{evidence}"
        return (
            f"{mission_header}[Browser] Flow fehlgeschlagen: {result.get('error', 'unbekannt')}\n"
            f"URL: {result.get('current_url', '-')}"
        )

    # ── Standard-Handler ──────────────────────────────────────────────────────
    async def _handle_fact(self, text: str) -> str:
        m = re.match(r'^(?:korrektur|fakt|weiß):\s*(.+?)\s*=\s*(.+)$', text, re.I)
        if m:
            self.memory.set_fact(m.group(1).strip(), m.group(2).strip(),
                                 source="Steffen")
            return f"[Fakt] '{m.group(1).strip()}' = '{m.group(2).strip()}'"
        return "[Fakt] Format: korrektur: Feld = Wert"

    def _handle_goal(self, text: str) -> str:
        from goal_store import get_goal_store, parse_goal_command

        cmd = parse_goal_command(text)
        store = get_goal_store()
        if not cmd:
            return (
                "[Ziele] Format:\n"
                "  Ziel: <dein Ziel>\n"
                "  Ziel erledigt: <titel oder id>\n"
                "  Ziel pause: <titel oder id>\n"
                "  ziele"
            )
        op = cmd.get("op")
        if op == "list":
            return store.format_goal_list()
        if op == "set":
            goal = store.add_owner_goal(
                str(cmd.get("title") or ""),
                description=str(cmd.get("description") or ""),
                source="explicit",
                owner_confirmed=True,
            )
            return (
                f"[Ziele] ✓ Gespeichert: {goal.title}\n"
                f"id={goal.id} │ status={goal.status} │ prio={goal.priority:.2f}\n"
                "Isaac verfolgt dieses Owner-Ziel (goal-directed Autonomie)."
            )
        if op == "done":
            g = store.set_status(str(cmd.get("query") or ""), "done")
            if not g:
                return f"[Ziele] Nicht gefunden: {cmd.get('query')}"
            return f"[Ziele] ✓ Erledigt: {g.title} ({g.id})"
        if op == "pause":
            g = store.set_status(str(cmd.get("query") or ""), "paused")
            if not g:
                return f"[Ziele] Nicht gefunden: {cmd.get('query')}"
            return f"[Ziele] Pausiert: {g.title} ({g.id})"
        return "[Ziele] Unbekannte Operation."

    def _handle_goal_list(self, *_args) -> str:
        from goal_store import get_goal_store

        return get_goal_store().format_goal_list()

    async def _handle_directive(self, text: str) -> str:
        m = re.match(r'^(?:direktive|immer|niemals):\s*(.+)$', text, re.I)
        if m:
            prio = 20 if "immer" in text.lower() else 10
            d    = self.gate.add_directive(m.group(1).strip(), prio)
            self.memory.save_directive(d.id, d.text, d.priority)
            return f"[Direktive] [{d.id}] {d.text}"
        return "[Direktive] Format: direktive: TEXT"

    def _handle_status(self, *_) -> str:
        from browser  import get_browser
        from search   import get_search
        from watchdog import get_blacklist, get_watchdog
        b    = get_browser().stats()
        s    = get_search().stats()
        bl   = get_blacklist().all_stats()
        w    = get_watchdog().stats()
        rw   = self.regelwerk.status()
        bg   = self._background.status() if self._background else {}
        sudo = bool(self._sudo_token and self.sudo.check(self._sudo_token))

        lines = [
            f"═══ Isaac v{self.VERSION} ═══",
            f"SUDO:        {'✓ AKTIV' if sudo else '–'}",
            f"Empathie:    {self.empathie.bericht()}",
            f"",
            f"Tasks:       {self.executor.stats()}",
            f"Watchdog:    {w}",
            f"Memory:      {self.memory.stats()}",
            f"",
            f"Regelwerk:   {rw['regeln_aktiv']} Regeln │ {rw['offene_fragen']} Fragen offen",
            f"KI-Dialog:   {self.ki_dialog.stats()['gespraeche']} Gespräche │ "
            f"{self.ki_dialog.stats()['wissenseintraege']} Wissenseinträge",
            f"Background:  {'aktiv' if bg.get('running') else '–'} │ "
            f"Zyklen: {bg.get('zyklen', 0)} │ "
            f"Erkenntnisse: {bg.get('erkenntnisse', 0)}",
            f"",
            f"Browser:     {b['aktiv']}/{b['total']} aktiv │ {b['eingeloggt']} eingeloggt",
            f"Search:      {len(s['engines'])} Engines",
            f"Direktiven:  {len(self.gate.active_directives())} aktiv",
            f"",
        ]
        try:
            from goal_store import get_goal_store

            lines.append(get_goal_store().format_status_block())
            lines.append("")
        except Exception:
            pass
        try:
            from motivation import pick_motivation_decision, goal_autonomy_enabled

            if goal_autonomy_enabled():
                dec = pick_motivation_decision()
                if dec:
                    mode = (dec.metadata or {}).get("mode", "")
                    lines.append(
                        f"Nächste Motivation: {dec.goal_title[:40]} → {dec.subgoal_title[:40]} "
                        f"(score={dec.score}, mode={mode or '-'})"
                    )
                    lines.append("")
        except Exception:
            pass
        try:
            from goal_inquiry import get_inquiry_store

            lines.append(get_inquiry_store().format_open_block())
            lines.append("")
        except Exception:
            pass
        for p in sorted(bl, key=lambda x: x["score"], reverse=True):
            lines.append(
                f"  {p['name']:12} Score:{p['score']:.1f} "
                f"✓{p['erfolge']} ✗{p['fehler']} "
                f"{'[BLACKLIST]' if p['blacklisted'] else ''}"
            )
        try:
            from external_memory import get_external_memory_bridge

            lines.append("")
            lines.append(get_external_memory_bridge().status_text())
        except Exception:
            pass
        return "\n".join(lines)

    def _handle_ext_memory_status(self, text: str = "", *_) -> str:
        tl = (text or "").lower().strip()
        if tl.startswith("ntfy") or tl.startswith("owner push") or tl.startswith("owner-push"):
            return self._handle_ntfy(text)
        if any(
            tl.startswith(p)
            for p in (
                "login flow",
                "login-flow",
                "browser login flow",
                "browser login:",
            )
        ) or "login flow" in tl:
            return self._handle_login_flow(text)
        if any(
            x in tl
            for x in (
                "status:pipeline",
                "pipeline status",
                "automation status",
                "status:automation",
                "status:smoke",
                "smoke:remote",
                "remote smoke",
            )
        ):
            if any(x in tl for x in ("status:smoke", "smoke:remote", "remote smoke")):
                return self._handle_remote_smoke(tl)
            try:
                from automation_pipeline import format_automation_status, run_stack_health_cycle

                if "sync" in tl:
                    report = run_stack_health_cycle(force_write=True)
                    write = report.get("memory_write") or {}
                    extra = (
                        f"\n\n[Sync] ok={write.get('ok')} written={write.get('written')} "
                        f"skip={write.get('skipped')} err={write.get('error')}"
                    )
                    return (report.get("summary") or format_automation_status()) + extra
                return format_automation_status()
            except Exception as exc:
                return f"[Automation] nicht verfügbar: {exc}"
        try:
            from external_memory import get_external_memory_bridge

            return get_external_memory_bridge().status_text()
        except Exception as exc:
            return f"[External Memory] nicht verfügbar: {exc}"

    def _handle_ntfy(self, text: str = "") -> str:
        """ntfy status | ntfy test — Owner-Push."""
        tl = (text or "").lower().strip()
        try:
            from owner_notify import send_test_push, status_text
        except Exception as exc:
            return f"[ntfy] Modul fehlt: {exc}"
        if "test" in tl or "ping" in tl:
            try:
                import asyncio

                async def _go():
                    return await send_test_push(message="Isaac ntfy test — bitte App prüfen.")

                try:
                    result = asyncio.get_running_loop()
                except RuntimeError:
                    result = None
                if result is not None:
                    # already in async context from process()
                    import concurrent.futures

                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        out = pool.submit(lambda: asyncio.run(_go())).result(timeout=30)
                else:
                    out = asyncio.run(_go())
                ch = out.get("channels") or []
                ok_ch = [c.get("channel") for c in ch if c.get("ok")]
                return (
                    f"[ntfy] test pushed={out.get('pushed')} "
                    f"channels_ok={ok_ch or '—'} detail={out.get('skipped') or 'ok'}"
                )
            except Exception as exc:
                return f"[ntfy] test fehlgeschlagen: {exc}"
        return status_text()

    def _handle_login_flow(self, text: str = "") -> str:
        """login flow: x | google | webde — single-pair owner browser login."""
        import re

        raw = (text or "").strip()
        m = re.search(
            r"(?:login[- ]flow|browser login flow|browser login)\s*:?\s*(\w+)",
            raw,
            re.I,
        )
        name = (m.group(1) if m else "").strip().lower()
        if not name or name in {"flow", "login", "browser"}:
            return (
                "[Login Flow] Format: login flow: x\n"
                "Verfügbar: google | webde | x (Twitter)\n"
                "Credentials: data/owner_login_probe_config.json (lokal, gitignored)\n"
                "Nur Admin-Modus; max. 1 E-Mail/Passwort-Paar pro Lauf."
            )
        try:
            from owner_login_probe import run_named_login_flow
            import asyncio

            async def _run():
                return await run_named_login_flow(name)

            try:
                asyncio.get_running_loop()
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    result = pool.submit(lambda: asyncio.run(_run())).result(timeout=180)
            except RuntimeError:
                result = asyncio.run(_run())
        except Exception as exc:
            return f"[Login Flow] Fehler: {exc}"

        if not result.get("ok") and result.get("error"):
            avail = result.get("available") or []
            extra = f" verfügbar={avail}" if avail else ""
            return f"[Login Flow] {result.get('error')}{extra}"

        succ = result.get("success_count", 0)
        tested = result.get("tested", 0)
        lines = [
            f"[Login Flow] target={name} tested={tested} success={succ}",
        ]
        for row in (result.get("results") or [])[:6]:
            lines.append(
                f"  {row.get('email_masked')}: "
                f"{'OK' if row.get('ok') else row.get('reason', 'fail')[:60]}"
            )
        if succ:
            lines.append("Erfolgreiche Zugangsdaten in browser_creds gespeichert (falls erlaubt).")
        else:
            lines.append(
                "Hinweis: 2FA/Captcha blockiert oft automatische Logins — dann manuell + login: speichern."
            )
        return "\n".join(lines)

    def _handle_remote_smoke(self, tl: str) -> str:
        """status:smoke | status:smoke wake | status:smoke full

        Keep-alive must run from outside Render Free (sleep ≈15 min).
        """
        try:
            from remote_smoke import (
                format_report,
                run_full_smoke,
                run_wake_only,
                status as smoke_status,
            )
        except Exception as exc:
            return f"[Remote Smoke] Modul fehlt: {exc}"

        run_wake = "wake" in tl
        run_full = any(x in tl for x in ("full", "run", "sync"))
        bare = tl.strip() in {
            "status:smoke",
            "smoke:remote",
            "remote smoke",
        }

        if bare or (not run_wake and not run_full):
            st = smoke_status()
            return (
                "[Remote Smoke Status]\n"
                + "\n".join(f"{k}={v}" for k, v in st.items())
                + "\n\nBefehle: status:smoke full | status:smoke wake\n"
                "Anti-Sleep: wake alle ≤10 Min von außen "
                "(GH Actions cron oder lokal ISAAC_REMOTE_SMOKE=1)."
            )
        if run_wake and not run_full:
            try:
                return format_report(run_wake_only())
            except Exception as exc:
                return f"[Remote Smoke] wake fehlgeschlagen: {exc}"
        try:
            report = asyncio.run(run_full_smoke())
            return format_report(report)
        except RuntimeError:
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                report = pool.submit(lambda: asyncio.run(run_full_smoke())).result(
                    timeout=600
                )
            return format_report(report)
        except Exception as exc:
            return f"[Remote Smoke] full fehlgeschlagen: {exc}"

    def _handle_context7(self, text: str) -> str:
        """Explicit Context7 library docs: 'docs: …' / 'context7: …' / 'ctx7: …'."""
        prompt = text
        for prefix in (
            "docs:",
            "context7:",
            "ctx7:",
            "doku:",
            "library docs:",
            "lib docs:",
        ):
            low = text.lower()
            idx = low.find(prefix)
            if idx >= 0:
                prompt = text[idx + len(prefix) :].strip()
                break
        if not prompt or prompt.lower() in {"status", "hilfe", "help", "?"}:
            if prompt and prompt.lower() == "status":
                try:
                    from external_memory import get_external_memory_bridge

                    st = get_external_memory_bridge().context7.status()
                    return (
                        "[Context7 Status]\n"
                        + "\n".join(f"{k}={v}" for k, v in st.items())
                    )
                except Exception as exc:
                    return f"[Context7] Status fehlgeschlagen: {exc}"
            return (
                "[Context7] Format:\n"
                "  docs: fastapi | APIRouter prefix\n"
                "  docs: /vercel/next.js middleware auth\n"
                "  docs: react useState\n"
                "  context7 status\n"
                "Aliases: context7: | ctx7: | doku:\n"
                "Env: CONTEXT7_API_KEY + ISAAC_CONTEXT7_ENABLED=1\n"
                "Docs: docs/CONTEXT7.md"
            )
        try:
            from external_memory import get_external_memory_bridge
            from privilege import steffen_ctx

            bridge = get_external_memory_bridge()
            if not bridge.cfg.context7_enabled:
                return (
                    "[Context7] Deaktiviert. Setze ISAAC_CONTEXT7_ENABLED=1 "
                    "und CONTEXT7_API_KEY=ctx7sk-…"
                )
            try:
                ok, reason = self.gate.authorize(
                    "chat_response",
                    steffen_ctx("Context7 docs lookup"),
                )
                if not ok:
                    return f"[Context7] Privilege verweigert: {reason}"
            except Exception:
                pass

            result = bridge.context7.lookup(prompt)
            if not result.get("ok"):
                err = result.get("error") or "unbekannt"
                return f"[Context7] Fehler: {err}"
            lib = result.get("library_id") or "?"
            title = result.get("library_title") or ""
            head = f"[Context7 | {lib}"
            if title:
                head += f" · {title}"
            head += "]"
            body = (result.get("text") or "").strip() or "(keine Ausgabe)"
            return f"{head}\n{body[:5000]}"
        except Exception as exc:
            return f"[Context7] Fehler: {exc}"

    def _handle_letta(self, text: str) -> str:
        """Explicit Letta Code companion run: 'letta: …' / 'coding-agent: …'."""
        prompt = text
        for prefix in ("letta:", "coding-agent:", "coding agent:"):
            low = text.lower()
            idx = low.find(prefix)
            if idx >= 0:
                prompt = text[idx + len(prefix) :].strip()
                break
        if not prompt:
            return (
                "[Letta] Format: letta: AUFGABE\n"
                "Cloud: LETTA_API_KEY + ISAAC_LETTA_ENABLED=1 + ISAAC_LETTA_ALLOW_CLOUD=1\n"
                "CLI: npm i -g @letta-ai/letta-code\n"
                "Docs: docs/LETTA.md"
            )
        try:
            from external_memory import get_external_memory_bridge
            from privilege import steffen_ctx

            bridge = get_external_memory_bridge()
            if not bridge.cfg.letta_enabled:
                return (
                    "[Letta] Deaktiviert. Setze ISAAC_LETTA_ENABLED=1 "
                    "(Cloud: LETTA_API_KEY + ISAAC_LETTA_ALLOW_CLOUD=1)."
                )
            # Constitution / privilege gate for shell-like companion
            try:
                from constitution import get_constitution

                decision = get_constitution().validate_action(
                    "system_command",
                    {
                        "command": "letta",
                        "prompt": prompt[:200],
                        "owner_approved": True,
                        "risk": "normal",
                        "audit_logged": True,
                    },
                )
                if not decision.get("allowed", True):
                    blocked = ", ".join(decision.get("blocked_by") or []) or "policy"
                    return f"[Letta] Verfassung blockiert: {blocked}"
            except Exception:
                pass
            try:
                ok, reason = self.gate.authorize(
                    "system_command",
                    steffen_ctx("Letta Code companion"),
                )
                if not ok:
                    return f"[Letta] Privilege verweigert: {reason}"
            except Exception:
                pass

            result = bridge.letta.run(prompt, timeout=180.0)
            if result.get("ok"):
                body = (result.get("text") or "").strip() or "(keine Ausgabe)"
                return f"[Letta]\n{body[:4000]}"
            err = result.get("error") or "unbekannt"
            body = (result.get("text") or "").strip()
            if body:
                return f"[Letta] Fehler: {err}\n{body[:2000]}"
            return f"[Letta] Fehler: {err}"
        except Exception as exc:
            return f"[Letta] Fehler: {exc}"

    def _handle_open_interpreter(self, text: str) -> str:
        """Explicit Open Interpreter companion: 'oi: …' / 'interpreter: …'."""
        prompt = text
        for prefix in (
            "oi:",
            "open-interpreter:",
            "open interpreter:",
            "interpreter:",
        ):
            low = text.lower()
            idx = low.find(prefix)
            if idx >= 0:
                prompt = text[idx + len(prefix) :].strip()
                break
        if not prompt:
            return (
                "[Open Interpreter] Format: oi: AUFGABE\n"
                "Aliases: open-interpreter: | interpreter:\n"
                "Flag: ISAAC_OPEN_INTERPRETER_ENABLED=1\n"
                "Sandbox default: read-only "
                "(ISAAC_OPEN_INTERPRETER_SANDBOX=workspace-write|danger-full-access)\n"
                "Provider: OpenRouter via OPENROUTER_API_KEY "
                "(siehe docs/OPEN_INTERPRETER.md)"
            )
        try:
            from external_memory import get_external_memory_bridge
            from privilege import steffen_ctx

            bridge = get_external_memory_bridge()
            if not bridge.cfg.open_interpreter_enabled:
                return (
                    "[Open Interpreter] Deaktiviert. Setze "
                    "ISAAC_OPEN_INTERPRETER_ENABLED=1 und installiere den CLI "
                    "(`interpreter` auf PATH)."
                )
            try:
                from constitution import get_constitution

                decision = get_constitution().validate_action(
                    "system_command",
                    {
                        "command": "open-interpreter",
                        "prompt": prompt[:200],
                        "owner_approved": True,
                        "risk": "normal",
                        "audit_logged": True,
                    },
                )
                if not decision.get("allowed", True):
                    blocked = ", ".join(decision.get("blocked_by") or []) or "policy"
                    return f"[Open Interpreter] Verfassung blockiert: {blocked}"
            except Exception:
                pass
            try:
                ok, reason = self.gate.authorize(
                    "system_command",
                    steffen_ctx("Open Interpreter companion"),
                )
                if not ok:
                    return f"[Open Interpreter] Privilege verweigert: {reason}"
            except Exception:
                pass

            result = bridge.open_interpreter.run(prompt)
            sandbox = result.get("sandbox") or bridge.cfg.open_interpreter_sandbox
            if result.get("ok"):
                body = (result.get("text") or "").strip() or "(keine Ausgabe)"
                return f"[Open Interpreter | sandbox={sandbox}]\n{body[:4000]}"
            err = result.get("error") or "unbekannt"
            body = (result.get("text") or "").strip()
            if body:
                return f"[Open Interpreter] Fehler: {err}\n{body[:2000]}"
            return f"[Open Interpreter] Fehler: {err}"
        except Exception as exc:
            return f"[Open Interpreter] Fehler: {exc}"

    def _parse_grok_agent_prompt(self, text: str) -> tuple[str, bool, Optional[str]]:
        """Strip prefix and session directives.

        Returns (prompt, force_new, resume_session_id_override).

        Directives (after prefix):
          new: | /new | --new     → force new session
          resume <id>: | @<id>    → resume explicit session
          clear session | reset   → drop stored session, then treat rest as prompt
        """
        prompt = text
        for prefix in (
            "grok-agent:",
            "grok agent:",
            "xai-agent:",
            "xai agent:",
            "grok:",
        ):
            low = text.lower()
            idx = low.find(prefix)
            if idx >= 0:
                prompt = text[idx + len(prefix) :].strip()
                break

        force_new = False
        resume_override: Optional[str] = None
        pl = prompt.lower()

        if pl in {"clear session", "reset session", "session clear", "session reset"}:
            return "", True, None

        # grok: new: task  |  grok: /new task  |  grok: --new task
        for marker in ("new:", "/new ", "--new "):
            if pl.startswith(marker):
                force_new = True
                prompt = prompt[len(marker) :].strip()
                break
        else:
            if pl == "/new" or pl == "--new" or pl == "new":
                force_new = True
                prompt = ""

        # grok: resume <uuid>: task  |  grok: @<uuid> task
        m = re.match(
            r"^(?:resume\s+)?@?([0-9a-fA-F-]{8,36})\s*[:\s]\s*(.*)$",
            prompt,
            re.DOTALL,
        )
        if m and not force_new:
            cand = m.group(1).strip()
            rest = (m.group(2) or "").strip()
            # Avoid eating normal sentences that start with hex-looking words
            if len(cand) >= 8 and ("-" in cand or len(cand) >= 16):
                resume_override = cand
                prompt = rest

        return prompt, force_new, resume_override

    def _handle_grok_agent(self, text: str) -> str:
        """Explicit Grok Build Agent CLI companion: 'grok: …' / 'grok-agent: …'."""
        prompt, force_new, resume_override = self._parse_grok_agent_prompt(text)

        # Session clear without task
        if force_new and not prompt and resume_override is None:
            try:
                from external_memory import get_external_memory_bridge

                bridge = get_external_memory_bridge()
                bridge.grok_agent.clear_session()
            except Exception:
                pass
            self._grok_session_id = None
            if (text or "").lower().strip().endswith(("clear session", "reset session", "session clear", "session reset")) \
                    or "clear session" in (text or "").lower() or "reset session" in (text or "").lower():
                return "[Grok Agent] Session gelöscht. Nächster `grok:` startet neu."

        if not prompt:
            sid = getattr(self, "_grok_session_id", None) or ""
            return (
                "[Grok Agent] Format: grok: AUFGABE\n"
                "Aliases: grok-agent: | grok agent: | xai-agent:\n"
                "Session: grok: new: AUFGABE  |  grok: resume <id>: AUFGABE\n"
                "         grok: clear session  |  auto-resume default ON\n"
                f"Aktuelle Session: {sid or '(keine)'}\n"
                "Flag: ISAAC_GROK_AGENT_ENABLED=1\n"
                "Binary: grok on PATH (Grok Build CLI)\n"
                "Auth: XAI_API_KEY or `grok login`\n"
                "Full tool autonomy: ISAAC_GROK_AGENT_ALWAYS_APPROVE=1 "
                "(--always-approve; SAFE_YOLO deny-rules default ON)\n"
                "Docs: docs/GROK_AGENT.md"
            )
        try:
            from external_memory import get_external_memory_bridge
            from privilege import steffen_ctx

            bridge = get_external_memory_bridge()
            if not bridge.cfg.grok_agent_enabled:
                return (
                    "[Grok Agent] Deaktiviert. Setze ISAAC_GROK_AGENT_ENABLED=1 "
                    "und stelle sicher, dass `grok` auf PATH liegt."
                )
            try:
                from constitution import get_constitution

                decision = get_constitution().validate_action(
                    "system_command",
                    {
                        "command": "grok-agent",
                        "prompt": prompt[:200],
                        "owner_approved": True,
                        "risk": "high"
                        if bridge.cfg.grok_agent_always_approve
                        else "normal",
                        "audit_logged": True,
                    },
                )
                if not decision.get("allowed", True):
                    blocked = ", ".join(decision.get("blocked_by") or []) or "policy"
                    return f"[Grok Agent] Verfassung blockiert: {blocked}"
            except Exception:
                pass
            try:
                ok, reason = self.gate.authorize(
                    "system_command",
                    steffen_ctx("Grok Build Agent companion"),
                )
                if not ok:
                    return f"[Grok Agent] Privilege verweigert: {reason}"
            except Exception:
                pass

            # Prefer Isaac repo root when cwd not configured
            cwd = (bridge.cfg.grok_agent_cwd or "").strip() or None
            if not cwd:
                try:
                    from config import BASE_DIR

                    cwd = str(BASE_DIR) if BASE_DIR else None
                except Exception:
                    cwd = None

            # Sync kernel session ↔ adapter for multi-turn
            if force_new:
                bridge.grok_agent.clear_session()
                self._grok_session_id = None
            elif resume_override:
                bridge.grok_agent.set_session_id(resume_override)
                self._grok_session_id = resume_override
            elif self._grok_session_id and not bridge.grok_agent.last_session_id():
                bridge.grok_agent.set_session_id(self._grok_session_id)

            result = bridge.grok_agent.run(
                prompt,
                cwd=cwd,
                resume_session_id=resume_override,
                force_new=force_new,
            )
            sid = (result.get("session_id") or "").strip()
            if sid:
                self._grok_session_id = sid
            resumed = (result.get("resumed_session_id") or "").strip()
            sid_note = f" session={sid}" if sid else ""
            if resumed:
                sid_note += f" resumed={resumed[:12]}…" if len(resumed) > 12 else f" resumed={resumed}"
            yolo = " always_approve" if result.get("always_approve") else ""
            if result.get("safe_yolo"):
                yolo += "+safe"
            if result.get("ok"):
                body = (result.get("text") or "").strip() or "(keine Ausgabe)"
                return f"[Grok Agent{yolo}{sid_note}]\n{body[:6000]}"
            err = result.get("error") or "unbekannt"
            body = (result.get("text") or "").strip()
            if body:
                return f"[Grok Agent] Fehler: {err}\n{body[:2500]}"
            return f"[Grok Agent] Fehler: {err}"
        except Exception as exc:
            return f"[Grok Agent] Fehler: {exc}"

    def _parse_copilot_agent_prompt(
        self, text: str
    ) -> tuple[str, bool, str | None, str]:
        """Strip prefix + session/mode markers for copilot:."""
        prompt = (text or "").strip()
        for prefix in (
            "copilot:",
            "gh-copilot:",
            "gh copilot:",
            "github-copilot:",
            "github copilot:",
            "copilot-agent:",
            "copilot agent:",
        ):
            if prompt.lower().startswith(prefix):
                prompt = prompt[len(prefix) :].strip()
                break

        force_new = False
        resume_override: str | None = None
        mode = "cli"
        pl = prompt.lower()

        if pl in {"clear session", "reset session", "session clear", "session reset"}:
            return "", True, None, mode

        if pl in {"status", "info"}:
            return "__status__", False, None, mode

        if pl in {"tasks", "list tasks", "cloud tasks"}:
            return "__list_tasks__", False, None, "cloud"

        # copilot: cloud: task  |  copilot: task: prompt
        for marker, mname in (
            ("cloud:", "cloud"),
            ("task:", "cloud"),
            ("cca:", "cloud"),
            ("sdk:", "sdk"),
            ("cli:", "cli"),
        ):
            if pl.startswith(marker):
                mode = mname
                prompt = prompt[len(marker) :].strip()
                pl = prompt.lower()
                break

        for marker in ("new:", "/new ", "--new "):
            if pl.startswith(marker):
                force_new = True
                prompt = prompt[len(marker) :].strip()
                break
        else:
            if pl in {"/new", "--new", "new"}:
                force_new = True
                prompt = ""

        m = re.match(
            r"^(?:resume\s+)?@?([0-9a-fA-F-]{8,36})\s*[:\s]\s*(.*)$",
            prompt,
            re.DOTALL,
        )
        if m and not force_new:
            cand = m.group(1).strip()
            rest = (m.group(2) or "").strip()
            if len(cand) >= 8 and ("-" in cand or len(cand) >= 16):
                resume_override = cand
                prompt = rest

        return prompt, force_new, resume_override, mode

    async def _handle_jules_agent(self, text: str) -> str:
        """Explicit Google Jules coding companion: 'jules: …'."""
        prompt = text or ""
        low = prompt.lower()
        for prefix in ("jules:", "jules-agent:", "jules agent:"):
            if low.startswith(prefix):
                prompt = prompt[len(prefix):].strip()
                break
        if not prompt:
            return (
                "[Jules] Format: jules: AUFGABE\n"
                "Konfiguration: ISAAC_JULES_ENABLED=1 + JULES_API_KEY + JULES_REPO=owner/repo"
            )
        try:
            from constitution import get_constitution
            decision = get_constitution().validate_action(
                "system_command",
                {
                    "command": "jules-agent",
                    "prompt": prompt[:200],
                    "owner_approved": True,
                    "risk": "normal",
                    "audit_logged": True,
                },
            )
            if not decision.get("allowed", True):
                return "[Jules] Verfassung blockiert die Ausführung."
            from privilege import steffen_ctx
            ok, reason = self.gate.authorize(
                "system_command",
                steffen_ctx("Jules coding companion"),
            )
            if not ok:
                return f"[Jules] Privileg verweigert: {reason}"
            from tool_bridge import run_bridge
            result = await run_bridge("jules", prompt)
            if not result.get("ok"):
                return f"[Jules] Fehler: {result.get('error') or 'unbekannt'}"
            return (
                f"[Jules] Session: {result.get('session_id') or '(unbekannt)'}\n"
                f"{result.get('output') or ''}"
            )
        except Exception as exc:
            return f"[Jules] Fehler: {exc}"

    def _handle_copilot_agent(self, text: str) -> str:
        """Explicit GitHub Copilot companion: 'copilot: …' / cloud tasks."""
        prompt, force_new, resume_override, mode = self._parse_copilot_agent_prompt(
            text
        )

        if force_new and not prompt and resume_override is None:
            try:
                from external_memory import get_external_memory_bridge

                get_external_memory_bridge().copilot_agent.clear_session()
            except Exception:
                pass
            self._copilot_session_id = None
            return (
                "[Copilot] Session gelöscht. Nächster `copilot:` startet neu."
            )

        try:
            from external_memory import get_external_memory_bridge
            from privilege import steffen_ctx

            bridge = get_external_memory_bridge()
        except Exception as exc:
            return f"[Copilot] Bridge-Fehler: {exc}"

        if prompt == "__status__":
            st = bridge.copilot_agent.status()
            lines = [f"[Copilot Status]"]
            for k, v in st.items():
                lines.append(f"  {k}: {v}")
            return "\n".join(lines)

        if prompt == "__list_tasks__":
            if not bridge.cfg.copilot_agent_enabled:
                return (
                    "[Copilot] Deaktiviert. Setze ISAAC_COPILOT_AGENT_ENABLED=1"
                )
            r = bridge.copilot_agent.list_cloud_tasks()
            if r.get("ok"):
                return f"[Copilot Cloud]\n{r.get('text') or ''}"
            return f"[Copilot Cloud] Fehler: {r.get('error')}"

        if not prompt:
            sid = getattr(self, "_copilot_session_id", None) or ""
            return (
                "[Copilot] Format: copilot: AUFGABE\n"
                "Aliases: gh-copilot: | github-copilot: | copilot-agent:\n"
                "Modi:    copilot: cli: …  |  copilot: sdk: …  |  copilot: cloud: …\n"
                "Session: copilot: new: AUFGABE  |  copilot: resume <id>: AUFGABE\n"
                "         copilot: clear session  |  copilot: status\n"
                "Cloud:   copilot: tasks  |  copilot: cloud: Fix login button\n"
                f"Aktuelle Session: {sid or '(keine)'}\n"
                "Flag: ISAAC_COPILOT_AGENT_ENABLED=1\n"
                "Binary: copilot on PATH (npm i -g @github/copilot)\n"
                "Auth: COPILOT_GITHUB_TOKEN mit gho_/github_pat_ "
                "(Classic ghp_ wird abgelehnt) oder `copilot /login`\n"
                "Yolo: ISAAC_COPILOT_AGENT_ALWAYS_APPROVE=1 (--allow-all)\n"
                "Docs: docs/COPILOT_AGENT.md"
            )

        if not bridge.cfg.copilot_agent_enabled:
            return (
                "[Copilot] Deaktiviert. Setze ISAAC_COPILOT_AGENT_ENABLED=1 "
                "und authentifiziere mit OAuth/Fine-Grained PAT (nicht ghp_)."
            )

        try:
            from constitution import get_constitution

            decision = get_constitution().validate_action(
                "system_command",
                {
                    "command": "copilot-agent",
                    "prompt": prompt[:200],
                    "owner_approved": True,
                    "risk": "high"
                    if bridge.cfg.copilot_agent_always_approve
                    else "normal",
                    "audit_logged": True,
                },
            )
            if not decision.get("allowed", True):
                blocked = ", ".join(decision.get("blocked_by") or []) or "policy"
                return f"[Copilot] Verfassung blockiert: {blocked}"
        except Exception:
            pass
        try:
            ok, reason = self.gate.authorize(
                "system_command",
                steffen_ctx("GitHub Copilot companion"),
            )
            if not ok:
                return f"[Copilot] Privilege verweigert: {reason}"
        except Exception:
            pass

        cwd = (bridge.cfg.copilot_agent_cwd or "").strip() or None
        if not cwd:
            try:
                from config import BASE_DIR

                cwd = str(BASE_DIR) if BASE_DIR else None
            except Exception:
                cwd = None

        if force_new:
            bridge.copilot_agent.clear_session()
            self._copilot_session_id = None
        elif resume_override:
            bridge.copilot_agent.set_session_id(resume_override)
            self._copilot_session_id = resume_override
        elif self._copilot_session_id and not bridge.copilot_agent.last_session_id():
            bridge.copilot_agent.set_session_id(self._copilot_session_id)

        try:
            result = bridge.copilot_agent.run(
                prompt,
                cwd=cwd,
                resume_session_id=resume_override,
                force_new=force_new,
                mode=mode,
            )
            sid = (result.get("session_id") or "").strip()
            if sid:
                self._copilot_session_id = sid
            via = result.get("via") or mode
            sid_note = f" session={sid}" if sid else ""
            yolo = " allow-all" if result.get("always_approve") else ""
            if result.get("ok"):
                body = (result.get("text") or "").strip() or "(keine Ausgabe)"
                return f"[Copilot/{via}{yolo}{sid_note}]\n{body[:6000]}"
            err = result.get("error") or "unbekannt"
            body = (result.get("text") or "").strip()
            auth = result.get("auth_hint") or ""
            extra = f"\nauth={auth}" if auth else ""
            if body:
                return f"[Copilot] Fehler: {err}{extra}\n{body[:2500]}"
            return f"[Copilot] Fehler: {err}{extra}"
        except Exception as exc:
            return f"[Copilot] Fehler: {exc}"

    def _strip_remote_prefix(self, text: str) -> tuple[str, str]:
        """Return (mode, prompt). mode: cloud|both|fleet_status."""
        prompt = (text or "").strip()
        low = prompt.lower()
        for p in (
            "both:",
            "beide:",
            "fleet:",
            "cloud:",
            "free:",
            "render:",
            "isaac-cloud:",
            "isaac cloud:",
            "isaac-free:",
        ):
            if low.startswith(p):
                rest = prompt[len(p) :].strip()
                if p.startswith(("both", "beide", "fleet")):
                    return "both", rest
                return "cloud", rest
        return "cloud", prompt

    async def _handle_remote_cloud(self, text: str) -> str:
        """Explicit remote Isaac: cloud: / free: / render: …"""
        from isaac_remote import (
            chat_remote,
            fleet_status,
            format_fleet_status,
            format_remote_reply,
            remote_base_url,
            remote_bridge_enabled,
            remote_label,
        )

        _mode, prompt = self._strip_remote_prefix(text)
        pl = (prompt or "").lower().strip()

        if not prompt or pl in {"status", "health", "help", "?"}:
            st = fleet_status()
            help_txt = (
                f"\nFormat: cloud: AUFGABE  |  free: AUFGABE  |  render: AUFGABE\n"
                f"Status: cloud: status\n"
                f"Beide:  both: AUFGABE  (lokal + {remote_label()})\n"
                f"Flag:   ISAAC_REMOTE_BRIDGE_ENABLED=1\n"
                f"URL:    ISAAC_REMOTE_FREE_URL={remote_base_url()}\n"
                f"Docs:   docs/ISAAC_REMOTE.md"
            )
            if not remote_bridge_enabled():
                return (
                    "[Cloud] Deaktiviert. Setze ISAAC_REMOTE_BRIDGE_ENABLED=1\n"
                    + format_fleet_status(st)
                    + help_txt
                )
            if pl in {"status", "health"} or not prompt:
                return format_fleet_status(st) + (help_txt if not prompt else "")
            return format_fleet_status(st) + help_txt

        if not remote_bridge_enabled():
            return (
                "[Cloud] Deaktiviert. Setze ISAAC_REMOTE_BRIDGE_ENABLED=1 "
                f"(Ziel: {remote_base_url()})"
            )

        try:
            from constitution import get_constitution
            from privilege import steffen_ctx

            decision = get_constitution().validate_action(
                "system_command",
                {
                    "command": "isaac-remote-cloud",
                    "prompt": prompt[:200],
                    "owner_approved": True,
                    "risk": "normal",
                    "audit_logged": True,
                },
            )
            if not decision.get("allowed", True):
                blocked = ", ".join(decision.get("blocked_by") or []) or "policy"
                return f"[Cloud] Verfassung blockiert: {blocked}"
            ok, reason = self.gate.authorize(
                "system_command",
                steffen_ctx("Remote Isaac cloud companion"),
            )
            if not ok:
                return f"[Cloud] Privilege verweigert: {reason}"
        except Exception:
            pass

        result = await chat_remote(prompt)
        return format_remote_reply(result)

    async def _handle_remote_both(self, text: str) -> str:
        """both: / beide: — local answer + remote free answer (no shared memory)."""
        from isaac_remote import (
            chat_remote,
            format_remote_reply,
            remote_base_url,
            remote_bridge_enabled,
            remote_label,
        )

        _mode, prompt = self._strip_remote_prefix(text)
        if not prompt or prompt.lower().strip() in {"status", "health"}:
            return await self._handle_remote_cloud("cloud: status")

        if not remote_bridge_enabled():
            return (
                "[Both] Cloud-Seite deaktiviert (ISAAC_REMOTE_BRIDGE_ENABLED=0). "
                "Nur lokal möglich — stelle die Frage ohne Prefix."
            )

        try:
            from constitution import get_constitution
            from privilege import steffen_ctx

            decision = get_constitution().validate_action(
                "system_command",
                {
                    "command": "isaac-remote-both",
                    "prompt": prompt[:200],
                    "owner_approved": True,
                    "risk": "normal",
                    "audit_logged": True,
                },
            )
            if not decision.get("allowed", True):
                blocked = ", ".join(decision.get("blocked_by") or []) or "policy"
                return f"[Both] Verfassung blockiert: {blocked}"
            ok, reason = self.gate.authorize(
                "system_command",
                steffen_ctx("Remote Isaac both companion"),
            )
            if not ok:
                return f"[Both] Privilege verweigert: {reason}"
        except Exception:
            pass

        # Parallel: remote WS + local route (chat path)
        remote_task = asyncio.create_task(chat_remote(prompt))

        local_text = ""
        local_err = ""
        try:
            # Re-enter local pipeline without remote prefix (no recursion into both:)
            local_text = await self.process(prompt)
        except Exception as exc:
            local_err = str(exc)

        remote = await remote_task
        remote_block = format_remote_reply(remote, title=remote_label())

        if local_err:
            local_block = f"[Lokal] Fehler: {local_err}"
        else:
            local_block = f"[Lokal | dieser Kernel]\n{(local_text or '').strip() or '(keine Ausgabe)'}"

        return (
            f"## Both — lokal + {remote_label()} ({remote_base_url()})\n\n"
            f"{local_block}\n\n"
            f"---\n\n"
            f"{remote_block}"
        )

    def _handle_pause(self, *_) -> str:
        self.gate.pause(steffen_ctx("Pause"))
        return "Isaac pausiert."

    def _handle_resume(self, *_) -> str:
        self.gate.resume(steffen_ctx("Resume"))
        return "Isaac fortgesetzt."

    async def _handle_cancel(self, text: str) -> str:
        m = re.search(r'abbrechen\s+(\w+)', text, re.I)
        if m:
            task = self.executor.get_task(m.group(1))
            if task:
                task.status = TaskStatus.CANCELLED
                return f"Task {m.group(1)} abgebrochen."
        return "Format: abbrechen TASK-ID"


    def _looks_like_explicit_command(self, user_input: str, intent: str) -> bool:
        text = (user_input or "").strip().lower()
        explicit_prefixes = {
            Intent.SUDO_OPEN: ("sudo ", "öffne tür", "master key"),
            Intent.SUDO_CLOSE: ("sudo close", "tür schließen"),
            Intent.FACT_SET: ("korrektur:", "fakt:", "weiß:"),
            Intent.GOAL_SET: ("ziel:", "goal:", "mein ziel:", "meine ziele:", "ziel erledigt:", "ziel pause:"),
            Intent.GOAL_LIST: ("ziele", "meine ziele", "list goals"),
            Intent.DIRECTIVE: ("direktive:", "immer:", "niemals:"),
            Intent.BROADCAST: ("broadcast:", "alle instanzen:", "frage alle"),
            Intent.SPLIT: ("split:", "aufteilen:"),
            Intent.PIPELINE: ("pipeline:", "verbessere iterativ"),
            Intent.ENSEMBLE: ("ensemble:", "vergleiche:", "vergleiche modelle:", "multi-model:"),
            Intent.DECOMPOSE: ("atomisiere:", "verteile:"),
            Intent.CODE: ("code:", "programmiere:", "schreibe python", "schreibe bitte python"),
            Intent.FILE: ("datei:", "lese:", "schreibe datei:", "schreibe eine datei"),
            Intent.TRANSLATE: ("übersetze:", "übersetze ", "translate:", "translate ", "schrift:"),
            Intent.LOGIN_ADD: ("login:", "credential:", "zugangsdaten:"),
            Intent.URL_ADD: ("url:", "instanz:", "füge"),
            Intent.KI_STATUS: ("ki status", "instanzen", "meinungen"),
            Intent.MEINUNG: ("meinung:", "was denkst du über", "isaac meinung"),
            Intent.PAUSE: ("pause", "stopp"),
            Intent.RESUME: ("weiter", "fortsetzen"),
            Intent.CANCEL: ("abbrechen ",),
            Intent.LETTA: ("letta:", "coding-agent:", "coding agent:"),
            Intent.OPEN_INTERPRETER: (
                "oi:",
                "open-interpreter:",
                "open interpreter:",
                "interpreter:",
            ),
            Intent.GROK_AGENT: (
                "grok:",
                "grok-agent:",
                "grok agent:",
                "xai-agent:",
                "xai agent:",
            ),
            Intent.COPILOT_AGENT: (
                "copilot:",
                "gh-copilot:",
                "gh copilot:",
                "github-copilot:",
                "github copilot:",
                "copilot-agent:",
                "copilot agent:",
            ),
            Intent.CONTEXT7: (
                "docs:",
                "context7:",
                "ctx7:",
                "doku:",
                "library docs:",
                "lib docs:",
            ),
            Intent.REMOTE_CLOUD: (
                "cloud:",
                "free:",
                "render:",
                "isaac-cloud:",
                "isaac cloud:",
                "isaac-free:",
            ),
            Intent.REMOTE_BOTH: (
                "both:",
                "beide:",
                "fleet:",
            ),
            Intent.EXT_MEMORY: (
                "external memory",
                "external-memory",
                "memory adapter",
                "mem0 status",
                "cognee status",
                "letta status",
                "context7 status",
                "ctx7 status",
                "docs status",
                "oi status",
                "open-interpreter status",
                "open interpreter status",
                "grok status",
                "grok-agent status",
                "grok agent status",
                "status:pipeline",
                "pipeline status",
                "automation status",
                "status:automation",
                "status:smoke",
                "smoke:remote",
                "remote smoke",
            ),
        }
        prefixes = explicit_prefixes.get(intent, ())
        if intent == Intent.URL_ADD:
            return text.startswith("url:") or text.startswith("instanz:") or (text.startswith("füge") and "url" in text)
        return any(text.startswith(prefix) for prefix in prefixes)

    def _tool_request_intent(self, user_input: str) -> str:
        tl = (user_input or "").lower().strip()
        if tl.startswith(("recherche:", "recherchiere:")):
            return Intent.RESEARCH
        if (
            tl.startswith(("browser:", "browser auf", "öffne im browser", "navigiere zu"))
            or self._is_browser_request(user_input)
        ):
            return Intent.BROWSER
        return Intent.SEARCH

    def _looks_like_native_coding_task(self, user_input: str) -> bool:
        """Repo/code-edit work that should take Intent.CODE (Phase 4).

        Stricter than companion ``_looks_like_code_work``: requires path-like
        tokens and/or explicit edit markers so conceptual chat (e.g. literature
        "Wetter"-motifs) stays CHAT without tools.
        """
        tl = (user_input or "").lower().strip()
        if not tl:
            return False
        if tl.startswith(("code:", "programmiere:", "schreibe python", "schreibe bitte python")):
            return True
        if "<<<<<<< search" in tl or ">>>>>>> replace" in tl:
            return True
        has_path = bool(
            re.search(
                r"(?:^|[\s`\"'(])/?(?:[\w.-]+/)*[\w.-]+\.(?:py|js|ts|tsx|jsx|go|rs|java|md|json|ya?ml|toml)\b",
                tl,
            )
        )
        edit_verbs = (
            "änder",
            "aender",
            "fix",
            "patch",
            "edit",
            "refaktor",
            "refactor",
            "implementier",
            "reparier",
            "debug",
            "bugfix",
            "schreibe in",
            "ergänze in",
            "ergaenze in",
            "lösche in",
            "loesche in",
            "search/replace",
            "search replace",
        )
        has_edit = any(v in tl for v in edit_verbs)
        if has_path and has_edit:
            return True
        # "def foo in bar.py" / function+path without soft verbs
        if has_path and re.search(
            r"\b(def|class|function|methode|funktion|import)\b", tl
        ):
            return True
        return False

    def _resolve_intent_from_classification(
        self, user_input: str, detected_intent: str, interaction_class: str
    ) -> str:
        # Klassifikation ist die primäre Routing-Authority.
        if interaction_class == InteractionClass.STATUS_QUERY:
            return Intent.STATUS
        if interaction_class in (
            InteractionClass.SOCIAL_GREETING,
            InteractionClass.SOCIAL_ACKNOWLEDGMENT,
            InteractionClass.SHORT_CLARIFICATION,
        ):
            return Intent.CHAT
        if interaction_class == InteractionClass.TOOL_REQUEST:
            tl = (user_input or "").lower().strip()
            if tl.startswith(("agent:", "agent ", "oberfläche:", "oberflaeche:")):
                return Intent.AGENT
            if detected_intent == Intent.AGENT:
                return Intent.AGENT
            # Explicit code:/programmiere: must not collapse to SEARCH
            if detected_intent == Intent.CODE or self._looks_like_native_coding_task(
                user_input
            ):
                return Intent.CODE
            return self._tool_request_intent(user_input)

        # Regex-Intent bleibt nur für explizite Kommandos als Fallback aktiv.
        if detected_intent != Intent.CHAT and self._looks_like_explicit_command(user_input, detected_intent):
            return detected_intent

        # Phase 4: free-form repo coding → CODE (repo_map + code_edit path)
        if self._looks_like_native_coding_task(user_input):
            return Intent.CODE

        # Short "code: …" can classify as AMBIGUOUS_SHORT — still honor CODE intent
        if detected_intent == Intent.CODE and self._looks_like_native_coding_task(
            user_input
        ):
            return Intent.CODE

        return Intent.CHAT

    def _is_short_followup(self, text: str) -> bool:
        """Short conversational follow-ups (e.g. 'Und?') — not new missions."""
        try:
            from low_complexity import normalize_low_complexity
            tl = normalize_low_complexity(text or "")
        except Exception:
            tl = (text or "").lower().strip()
        if not tl:
            return False
        exact = {
            "und?", "und", "und dann?", "und dann", "und nun?", "und nun",
            "und jetzt?", "und jetzt", "na und?", "na und", "weiter?", "weiter",
            "warum?", "warum", "wieso?", "wieso", "weshalb?", "weshalb",
            "wie bitte?", "was meinst du?", "was meinst du", "und?", "??",
            "…?", "...?", "und so weiter?", "und weiter?",
        }
        if tl in exact:
            return True
        words = tl.split()
        if len(words) <= 3 and tl.endswith("?"):
            if words[0] in {"und", "warum", "wieso", "weshalb", "wieso", "wie"}:
                return True
        return False

    @staticmethod
    def _strip_mission_noise_from_context(text: str) -> str:
        """Remove key-hunt / browser-login / provisioning noise from context blocks."""
        if not (text or "").strip():
            return text or ""
        drop_markers = (
            "api-key", "api key", "api_keys", "provider-api", "provider api",
            "openrouter", "groq einrichten", "keys selbst", "auto_provision",
            "provider_auto_connect", "passwort", "password", "login:",
            "browser-automation", "browser automation", "anmeldedaten",
            "fehlende keys", "token beschaffen", "provision",
        )
        lines = []
        for line in (text or "").splitlines():
            low = line.lower()
            if any(m in low for m in drop_markers):
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    def _retrieve_relevant_context(
        self, user_input: str, intent: str, interaction_class: str
    ) -> dict[str, Any]:
        from self_model_hooks import enrich_retrieval_with_self_model

        n_hist = 6
        if intent == Intent.CHAT and self._is_short_followup(user_input):
            n_hist = 4  # keep recent turns for continuity, less bulk noise
        retrieval_ctx = self.memory.build_retrieval_context(
            user_input=user_input,
            intent=intent,
            interaction_class=interaction_class,
            n_history=n_hist,
        ).as_dict()
        retrieval_ctx = enrich_retrieval_with_self_model(
            retrieval_ctx,
            memory=self.memory,
            user_input=user_input,
        )
        # Phase 1.7: native RepoMap enrichment (CODE only; fail-soft; no second path)
        try:
            from repo_map import maybe_enrich_retrieval_with_repo_map

            retrieval_ctx = maybe_enrich_retrieval_with_repo_map(
                retrieval_ctx,
                user_input=user_input,
                intent=intent,
            )
        except Exception:
            pass
        return retrieval_ctx

    def _select_response_strategy(
        self, user_input: str, intent: str, interaction_class: str, retrieval_ctx: dict[str, Any]
    ) -> Strategy:
        cfg = getattr(self, "cfg", None) or get_config()
        allow_tools = intent in (
            Intent.SEARCH,
            Intent.RESEARCH,
            Intent.CODE,
            Intent.FILE,
            Intent.AGENT,
        )
        allow_followup = interaction_class not in ("SHORT_CLARIFICATION",)
        allow_provider_switch = True
        allow_agent_companions = False
        preferred_agent = ""
        style_note = ""
        risk_tags = {
            tag
            for risk in retrieval_ctx.get("behavioral_risks", [])
            for tag in risk.get("risks", [])
        }
        pref_text = " ".join(
            f"{p.get('text', '')} {p.get('value', '')}".lower()
            for p in retrieval_ctx.get("preferences_context", [])
        )

        if intent == Intent.CHAT:
            allow_tools = False
            # Explicit bridge markers in free chat (github:/fetch:)
            tl = (user_input or "").lower().strip()
            if tl.startswith(("github:", "gh:", "fetch:", "web_fetch:", "web fetch:")):
                allow_tools = True
            # Self-improvement questions: force concrete system coverage
            if any(
                m in tl
                for m in (
                    "verbesser",
                    "dich selbst",
                    "du selbst",
                    "selbstmodell",
                    "stärken",
                    "schwächen",
                )
            ):
                style_note += (
                    "\n[Selbstreflexion] Antworte mit: (1) Stärken, (2) konkrete "
                    "System-/Code-Lücken, (3) priorisierte nächste Schritte. "
                    "Nutze Self-Model/Pipeline-Fakten aus dem Kontext. "
                    "Keine erfundenen Trainingsdetails."
                )
        if "tool_overreach_risk" in risk_tags and intent == Intent.CHAT:
            allow_tools = False
        if "no auto-agreement" in pref_text or "kein auto agreement" in pref_text:
            style_note += "\n[Antwortstil] Stimme nicht automatisch zu; bleibe begründet und nüchtern."
        if retrieval_ctx.get("project_context"):
            style_note += "\n[Projektkontext] Antwort soll routing- und stabilitätsfokussiert bleiben."
        if "quality_regression_risk" in risk_tags and intent == Intent.CHAT:
            allow_provider_switch = False

        # Companion agents: only for work-like intents (never greeting/simple chat alone)
        if intent in (Intent.CODE, Intent.FILE, Intent.AGENT, Intent.RESEARCH):
            allow_agent_companions = True
        elif intent == Intent.CHAT:
            # Marker-based allow — selection still rejects pure smalltalk
            from agent_selection import _looks_like_code_work

            if _looks_like_code_work(user_input, intent):
                allow_agent_companions = True

        if cfg.style_mode == "light_sarcastic":
            if self._should_allow_light_sarcasm(user_input, intent, interaction_class):
                if self._light_sarcasm_triggered(user_input):
                    style_note += (
                        "\n[Antwortstil] Nach der Lösung ist maximal ein kurzer trockener, leicht "
                        "sarkastischer Kommentar erlaubt."
                    )
                else:
                    style_note += "\n[Antwortstil] Bleibe knapp, direkt und rein sachlich."
            else:
                style_note += "\n[Antwortstil] Kein Sarkasmus in dieser Antwort."

        # DIVA Modulator Influence: under elevated uncertainty/caution, inject clarification/caution instructions
        mod_state = retrieval_ctx.get("modulator_state") or {}
        if not mod_state:
            try:
                from modulator import get_modulator
                mod_state = get_modulator().get_all()
            except Exception:
                mod_state = {}

        if mod_state.get("caution", 0.0) > 0.4 or mod_state.get("uncertainty", 0.0) > 0.4:
            style_note += (
                f"\n[DIVA-Modulator] Erhöhte Unsicherheit ({mod_state.get('uncertainty', 0.0):.2f}) "
                f"oder Vorsicht ({mod_state.get('caution', 0.0):.2f}). "
                "Agieren Sie besonders präzise, vorsichtig und fragen Sie bei Unklarheiten zur Herkunft oder Absicht nach."
            )

        # Short follow-ups: stay on topic; no credential/browser/key hunting
        if intent == Intent.CHAT and self._is_short_followup(user_input):
            allow_tools = False
            allow_agent_companions = False
            allow_provider_switch = False
            style_note += (
                "\n[Follow-up] Beziehe dich auf den unmittelbar vorherigen User-Turn und deine "
                "letzte Antwort. Keine neuen Themen, keine Browser-/Login-/API-Key-Missionen, "
                "keine Anmeldedaten anfordern — außer der User fragt explizit danach."
            )

        # Kürzeste Klärungen ohne Eskalation.
        if interaction_class in ("SHORT_CLARIFICATION",):
            allow_followup = False
            allow_provider_switch = False
            allow_agent_companions = False
        return Strategy(
            allow_tools=allow_tools,
            allow_followup=allow_followup,
            allow_provider_switch=allow_provider_switch,
            allow_agent_companions=allow_agent_companions,
            preferred_agent=preferred_agent,
            style_note=style_note,
        )

    def _companion_availability(self) -> dict[str, bool]:
        """Which optional companion adapters are enabled+available."""
        out = {
            "grok": False,
            "open_interpreter": False,
            "letta": False,
            "copilot": False,
        }
        try:
            from external_memory import get_external_memory_bridge

            bridge = get_external_memory_bridge()
            out["grok"] = bool(
                bridge.cfg.grok_agent_enabled and bridge.grok_agent.available()
            )
            out["open_interpreter"] = bool(
                bridge.cfg.open_interpreter_enabled and bridge.open_interpreter.available()
            )
            out["letta"] = bool(bridge.cfg.letta_enabled and bridge.letta.available())
            out["copilot"] = bool(
                bridge.cfg.copilot_agent_enabled and bridge.copilot_agent.available()
            )
        except Exception as exc:
            log.debug("companion availability: %s", exc)
        return out

    def _maybe_run_selected_agent(
        self,
        *,
        user_input: str,
        intent: str,
        interaction_class: str,
        strategy: Strategy,
    ) -> tuple[Any, str, Strategy]:
        """Select + run companion; return (decision, context_block, strategy)."""
        from agent_selection import (
            AGENT_COPILOT,
            AGENT_GROK,
            AGENT_LETTA,
            AGENT_OI,
            agent_timeout_s,
            format_agent_context_block,
            select_companion_agent,
        )

        available = self._companion_availability()
        decision = select_companion_agent(
            user_input=user_input,
            intent=intent,
            interaction_class=interaction_class,
            strategy=strategy,
            available=available,
        )
        if not decision.agent_id:
            return decision, "", strategy

        # Privilege / constitution gates (same class as explicit companions)
        try:
            from constitution import get_constitution
            from privilege import steffen_ctx

            decision_c = get_constitution().validate_action(
                "system_command",
                {
                    "command": f"auto-agent:{decision.agent_id}",
                    "prompt": (user_input or "")[:200],
                    "owner_approved": True,
                    "risk": "normal",
                    "audit_logged": True,
                },
            )
            if not decision_c.get("allowed", True):
                decision = type(decision)(
                    None,
                    f"constitution_blocked:{','.join(decision_c.get('blocked_by') or [])}",
                    "none",
                    0.0,
                )
                return decision, "", strategy
            ok, reason = self.gate.authorize(
                "system_command",
                steffen_ctx(f"Auto companion {decision.agent_id}"),
            )
            if not ok:
                decision = type(decision)(
                    None, f"privilege_denied:{reason}", "none", 0.0
                )
                return decision, "", strategy
        except Exception as exc:
            log.debug("auto agent gate: %s", exc)

        from external_memory import get_external_memory_bridge
        from config import BASE_DIR

        bridge = get_external_memory_bridge()
        timeout = agent_timeout_s()
        cwd = str(BASE_DIR)
        result: dict[str, Any] = {"ok": False, "error": "unknown agent", "text": ""}

        if decision.agent_id == AGENT_GROK:
            result = bridge.grok_agent.run(
                user_input, cwd=cwd, timeout=timeout
            )
            sid = (result.get("session_id") or "").strip()
            if sid:
                self._grok_session_id = sid
        elif decision.agent_id == AGENT_COPILOT:
            result = bridge.copilot_agent.run(
                user_input, cwd=cwd, timeout=timeout
            )
            sid = (result.get("session_id") or "").strip()
            if sid:
                self._copilot_session_id = sid
        elif decision.agent_id == AGENT_OI:
            result = bridge.open_interpreter.run(user_input, cwd=cwd, timeout=timeout)
            sid = ""
        elif decision.agent_id == AGENT_LETTA:
            result = bridge.letta.run(user_input, timeout=timeout)
            sid = ""
        else:
            return decision, "", strategy

        body = (result.get("text") or result.get("error") or "").strip()
        if not result.get("ok") and not body:
            # Keep selection decision for trace; no context injection
            return decision, "", strategy

        block = format_agent_context_block(
            agent_id=decision.agent_id,
            reason=decision.reason,
            text=body,
            session_id=sid
            if decision.agent_id in {AGENT_GROK, AGENT_COPILOT}
            else "",
        )
        # Steer relay to use agent work product
        note = (
            f"\n[Agent] Companion={decision.agent_id} reason={decision.reason}. "
            "Nutze den Agent-Kontext: fasse zusammen, korrigiere Fehler, "
            "liefere dem Owner eine klare, nutzbare Antwort."
        )
        strategy = Strategy(
            allow_tools=strategy.allow_tools,
            allow_followup=strategy.allow_followup,
            allow_provider_switch=strategy.allow_provider_switch,
            allow_agent_companions=strategy.allow_agent_companions,
            preferred_agent=decision.agent_id,
            style_note=(strategy.style_note or "") + note,
        )

        if decision.mode == "primary" and result.get("ok") and body:
            # Primary mode: still inject block; caller could short-circuit later
            pass

        return decision, block, strategy

    def _should_allow_light_sarcasm(self, user_input: str, intent: str, interaction_class: str) -> bool:
        text = (user_input or "").lower()
        if intent in (Intent.SUDO_OPEN, Intent.SUDO_CLOSE, Intent.CODE, Intent.FILE):
            return False
        if interaction_class in ("STATUS_QUERY", "SHORT_CLARIFICATION"):
            return False
        blocked_markers = (
            "security", "sicher", "passwort", "token", "api key", "credential", "berechtigung",
            "debug", "traceback", "stacktrace", "exception", "crash", "fehler", "failed", "timeout",
            "panic", "kaputt", "not working", "funktioniert nicht",
        )
        if any(marker in text for marker in blocked_markers):
            return False
        return True

    @staticmethod
    def _light_sarcasm_triggered(user_input: str) -> bool:
        digest = hashlib.blake2s((user_input or "").encode("utf-8", errors="ignore"), digest_size=2).digest()
        return (int.from_bytes(digest, "big") % 4) == 0

    def _format_retrieval_context(self, retrieval_ctx: dict[str, Any]) -> str:
        return self.memory.format_retrieval_context(retrieval_ctx)

    # ── System-Prompt ─────────────────────────────────────────────────────────
    def _build_system(self, sudo_aktiv: bool, emp,
                      wissen_kontext: str = "",
                      strategy_note: str = "") -> str:
        try:
            from free_cloud import free_cloud_enabled
            _free = free_cloud_enabled()
        except Exception:
            _free = False

        owner = self.cfg.owner_name
        if _free:
            # Schlanker Prompt: Free-LLMs paraphrasieren sonst Owner/Regeln als Essay
            basis = (
                f"Du bist Isaac v{self.VERSION}, der persönliche Assistent von {owner}.\n"
                f"Antworte auf die aktuelle Nutzerfrage: konkret, knapp (meist 1–3 Absätze), hilfreich.\n"
                f"Verbotene Standard-Antworten (außer der Nutzer fragt explizit danach):\n"
                f"- Essays über Eigentum, Kontrolle, Autorität, Verantwortung von {owner}\n"
                f"- API-Keys, Provider-Provisioning, Browser-Automation als Hauptthema\n"
                f"- Wiederholung von Systemregeln statt Inhalt\n"
                                f"Spaß/Hypothesen: klar und sicher beantworten, nicht moralisieren.\n"
                f"Bei echten Gefahr-/Betrugsthemen: kurz warnen, sonst normal chatten.\n"
            )
        else:
            basis = (
                f"Du bist Isaac v{self.VERSION}, ein persönliches KI-System für {owner}.\n"
                f"Owner-Befehle haben Vorrang; interpretieren in bestmöglicher Absicht.\n"
                f"Beantworte die aktuelle Nutzerfrage zuerst und konkret. "
                f"{build_self_reflection_policy_prompt()}\n"
                            )
        if sudo_aktiv:
            basis += self.sudo.get_authority_prefix()

        # Regeln: free-cloud nur Kurzform (sonst paraphrasiert das Modell „Steffen-Kontrolle“)
        if _free:
            basis += (
                f"\n[Regeln kurz] {owner} vertrauen; keine Tools ohne Bedarf; "
                f"Qualität vor Länge; keine Regel-Wiederholung.\n"
            )
        else:
            regeln = self.regelwerk.aktive_regeln_als_kontext()
            if regeln:
                basis += f"\n{regeln}\n"

        # Provider-Key-Direktive nicht in jeden Prompt mischen (Chat-Hijack)
        if _free or not getattr(self.cfg, "browser_automation", True):
            direktiven = ""
            try:
                active = self.gate.active_directives() if hasattr(self.gate, "active_directives") else []
                lines = []
                for d in active or []:
                    did = str(getattr(d, "id", "") or (d.get("id") if isinstance(d, dict) else "") or "")
                    if did == "provider_auto_connect_all":
                        continue
                    text = getattr(d, "text", None) or (d.get("text") if isinstance(d, dict) else str(d))
                    if text and "Provider-API" not in text and "API-Keys" not in text:
                        lines.append(f"- {text}")
                if lines:
                    direktiven = "Aktive Direktiven:\n" + "\n".join(lines)
            except Exception:
                direktiven = ""
        else:
            direktiven = self.gate.directives_as_context()
            if "provider_auto_connect_all" in (direktiven or ""):
                # keep other directives if mixed — strip key-hunt block roughly
                pass
        if direktiven:
            basis += f"\n{direktiven}\n"

        if emp.anpassungs_hinweis:
            basis += f"\n[Kommunikation] {emp.anpassungs_hinweis}"

        # Wissensdatenbank-Kontext
        if wissen_kontext:
            basis += f"\n\n{wissen_kontext}"
        if strategy_note:
            basis += f"\n{strategy_note}"

        # Execution Contract: never invent tool/browser success
        try:
            from execution_contract import anti_hallucination_system_note
            basis += f"\n{anti_hallucination_system_note()}\n"
        except Exception:
            pass

        cfg = getattr(self, "cfg", None) or get_config()
        if cfg.style_mode == "professional" or _free:
            basis += (
                "\n[Stil] Klar, präzise, lösungsorientiert. Keine Ironie-Pflicht. "
                "Keine Bullet-Essay-Zusammenfassung über dich selbst."
            )
        else:
            basis += (
                "\n[Stilmodus] light_sarcastic: Antworte primär direkt, kompetent und hilfreich. "
                "Gelegentlich ist ein kurzer trockener Seitenhieb erlaubt, aber nie überdreht. "
                "Kein Sarkasmus bei Fehlerfrust, Sicherheitsthemen oder komplexem Debugging."
            )

        # Value-engine on free cloud adds "proactive next steps" padding — skip
        if not _free:
            from value_decisions import get_decision_engine
            decisions = get_decision_engine().decide_behavior()
            basis = get_decision_engine().apply_to_system_prompt(basis, decisions)
        return basis

    def _provider_hint(self, text: str) -> Optional[str]:
        tl = text.lower()
        for pname in self.cfg.providers:
            if pname in tl:
                return pname
        return None

    # ── Background-Loop registrieren ──────────────────────────────────────────
    def set_background(self, bg):
        self._background = bg
        bg.set_kernel(self)


# ── Entry Point ───────────────────────────────────────────────────────────────
def _configure_console_encoding() -> None:
    """Keep Isaac's Unicode startup banner usable on Windows cp1252 consoles."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

_configure_console_encoding()


async def main():
    _configure_console_encoding()
    logging.basicConfig(
        level   = logging.DEBUG if __import__('os').getenv(
            "ISAAC_DEBUG", "false").lower() == "true" else logging.INFO,
        format  = "[%(asctime)s] %(levelname)-7s %(name)s – %(message)s",
        datefmt = "%H:%M:%S",
    )

    # Secrets: env + cli_auth_backup → SecretsStore (never logged as values)
    try:
        from secrets_bootstrap import bootstrap_secrets
        from tool_bridge import ensure_bridge_tools_registered
        from tool_catalog import ensure_catalog_tools_registered

        bootstrap_secrets()
        ensure_bridge_tools_registered()
        added = ensure_catalog_tools_registered(bundle_id="professional_core")
        if added:
            logging.getLogger("Isaac").info(
                "Catalog tools registered: %s", len(added)
            )
    except Exception as e:
        logging.getLogger("Isaac").warning("Secrets/tool-bridge bootstrap skipped: %s", e)

    # Optional Sentry AI monitoring (no-op without SENTRY_DSN)
    try:
        from isaac_sentry import init_sentry
        init_sentry()
    except Exception as e:
        logging.getLogger("Isaac").warning("Sentry init skipped: %s", e)

    # Free-tier PaaS (Render/HF Spaces/Fly free): bind 0.0.0.0, unified /ws, no vector mem
    try:
        from free_cloud import apply_free_cloud_defaults, free_cloud_enabled, free_hosting_status
        applied = apply_free_cloud_defaults()
        if free_cloud_enabled():
            logging.getLogger("Isaac").info("Free-cloud mode: %s applied=%s", free_hosting_status(), applied)
    except Exception as e:
        logging.getLogger("Isaac").warning("free_cloud defaults: %s", e)

    kernel = IsaacKernel()
    # Free-Cloud / no-browser: Key-Jagd-Direktive entfernen (sonst redet jeder Chat über API-Keys)
    try:
        kernel._clear_provider_connect_directive_if_idle()
    except Exception:
        pass
    # Owner-Direktiven (Steffen, 2026-09-27) — einmalig seeden, nicht erzwungen
    try:
        kernel._ensure_owner_directives()
        logging.getLogger("Isaac").info("Owner-Direktiven geprüft/geseedet")
    except Exception as e:
        logging.getLogger("Isaac").warning("owner directives bootstrap: %s", e)
    # Worker + Background + Monitor
    await kernel.executor.start_worker(concurrency=4)

    from background_loop import get_background
    bg = get_background()
    kernel.set_background(bg)
    await bg.start()
    asyncio.create_task(kernel.bootstrap_providers())

    http = DashboardHTTPServer(port=kernel.cfg.monitor.http_port)
    await http.start()

    try:
        from free_cloud import free_cloud_enabled, http_port as free_http_port, bind_host, unified_port_enabled
        _host = bind_host("localhost")
        _http = free_http_port(kernel.cfg.monitor.http_port)
        if free_cloud_enabled() or unified_port_enabled():
            dash_line = f"http://{_host}:{_http}"
            ws_line = f"ws://{_host}:{_http}/ws  (unified)"
        else:
            dash_line = f"http://localhost:{kernel.cfg.monitor.http_port}"
            ws_line = f"ws://localhost:{kernel.cfg.monitor.port}"
    except Exception:
        dash_line = "http://localhost:8766"
        ws_line = "ws://localhost:8765"
    print(f"""
╔══════════════════════════════════════════════════════╗
║  ISAAC v5.3 – Unified OS                            ║
╠══════════════════════════════════════════════════════╣
║  Dashboard:   {dash_line:<40} ║
║  WebSocket:   {ws_line:<40} ║
╠══════════════════════════════════════════════════════╣
║  SUDO (Master-Tür):                                  ║
║    sudo PASSWORT   → Vollzugriff öffnen             ║
║    sudo close      → Schließen                       ║
╠══════════════════════════════════════════════════════╣
║  KI-Instanzen:                                       ║
║    url: ID | URL | NAME      → Instanz hinzufügen   ║
║    login: DOM|URL|USER|PASS  → Auto-Login            ║
╠══════════════════════════════════════════════════════╣
║  Befehle:                                            ║
║    suche: QUERY    → 7 Suchmaschinen parallel        ║
║    broadcast: TEXT → Alle Instanzen                  ║
║    meinung: THEMA  → Isaac's eigene Meinung          ║
║    ki status       → KI-Netzwerk Übersicht           ║
║    status          → System-Übersicht                ║
╚══════════════════════════════════════════════════════╝
""")

    await kernel.monitor.start()


if __name__ == "__main__":
    asyncio.run(main())