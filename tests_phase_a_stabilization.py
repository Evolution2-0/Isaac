from __future__ import annotations
import os

os.environ.setdefault("ISAAC_DISABLE_VECTOR_MEMORY", "1")
# Geräte-.env.local kann admin setzen — Regression-Tests laufen im user-Modus
os.environ["ISAAC_PRIVILEGE_MODE"] = "user"

import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from executor import Executor, Strategy, Task, TaskStatus, TaskType
from decision_trace import DecisionTrace, TracePhase
from logic import QualityScore
from isaac_core import IsaacKernel, Intent, detect_intent
from tool_policy import ToolDecisionReason
from tool_runtime import select_live_tool_for_task
from low_complexity import (
    ClassificationResult,
    InteractionClass,
    classify_interaction_result,
    is_lightweight_local_class,
)
from hermes_compat import (
    BrowserAction,
    ComputerAction,
    ExecutionContext,
    HermesBrowserAdapter,
    HermesCompatibilityAdapter,
    HermesComputerUseAdapter,
    HermesToolSchemaMapper,
    PermissionMetadata,
)
from mcp_registry import MCPRegistry
from security_policy import ConfirmationPolicy


class TestCriticalBugs(unittest.TestCase):
    def setUp(self):
        self.kernel = object.__new__(IsaacKernel)

    def test_bug_1_greeting_stays_lightweight_local(self):
        result = classify_interaction_result("Hallo Isaac")
        self.assertEqual(result.interaction_class, InteractionClass.SOCIAL_GREETING)
        self.assertTrue(is_lightweight_local_class(result.interaction_class))

    def test_bug_2_status_query_maps_to_status_intent(self):
        intent = self.kernel._resolve_intent_from_classification(
            "status", Intent.CHAT, InteractionClass.STATUS_QUERY
        )
        self.assertEqual(intent, Intent.STATUS)

    def test_bug_3_tool_request_maps_to_search_intent(self):
        intent = self.kernel._resolve_intent_from_classification(
            "Suche: Wetter Berlin", Intent.SEARCH, InteractionClass.TOOL_REQUEST
        )
        self.assertEqual(intent, Intent.SEARCH)

    def test_bug_4_question_without_status_or_tool_stays_chat(self):
        intent = self.kernel._resolve_intent_from_classification(
            "Was ist 2+2?", Intent.CHAT, InteractionClass.NORMAL_CHAT
        )
        self.assertEqual(intent, Intent.CHAT)

    def test_bug_5_detected_search_is_ignored_without_tool_classification(self):
        intent = self.kernel._resolve_intent_from_classification(
            "Erkläre mir das Wetter als sprachliches Motiv in Literatur",
            Intent.SEARCH,
            InteractionClass.NORMAL_CHAT,
        )
        self.assertEqual(intent, Intent.CHAT)

    def test_bug_6_executor_uses_explicit_tool_policy_for_normal_chat(self):
        task = Task(
            id="t1",
            typ=TaskType.CHAT,
            prompt="Was ist 2+2?",
            beschreibung="chat",
            strategy=Strategy(allow_tools=True),
            interaction_class=InteractionClass.NORMAL_CHAT,
            classification=ClassificationResult(
                interaction_class=InteractionClass.NORMAL_CHAT,
                normalized_text="was ist 2 2",
                has_question=True,
                word_count=3,
            ),
        )
        executor = object.__new__(Executor)
        self.assertTrue(executor._should_try_tool(task, task.prompt, iteration=2))

    def test_bug_7_executor_respects_explicit_tool_disable_even_for_tool_request(self):
        task = Task(
            id="t2",
            typ=TaskType.CHAT,
            prompt="Suche: Wetter Berlin",
            beschreibung="chat",
            strategy=Strategy(allow_tools=False),
            interaction_class=InteractionClass.TOOL_REQUEST,
            classification=ClassificationResult(
                interaction_class=InteractionClass.TOOL_REQUEST,
                normalized_text="suche wetter berlin",
                has_question=False,
                word_count=3,
            ),
        )
        executor = object.__new__(Executor)
        self.assertFalse(executor._should_try_tool(task, task.prompt, iteration=0))

    def test_bug_8_kernel_uses_structured_retrieval_contract_without_legacy_build_context(self):
        class FakeRetrievalContext:
            def as_dict(self):
                return {
                    "active_directives": [{"text": "Antworten klar halten", "priority": 20}],
                    "relevant_facts": [{"key": "answer_style", "value": "nuechtern"}],
                    "semantic_context": "[semantik] routing context",
                    "conversation_history": [{"role": "steffen", "text": "Bitte stabil halten"}],
                    "relevant_task_results": [{"description": "alt", "result": "tools genutzt", "score": 3.0}],
                    "preferences_context": [{"source": "directive", "text": "Antworten klar halten", "priority": 20}],
                    "project_context": [{"role": "steffen", "text": "routing fokus"}],
                    "behavioral_risks": [{"description": "alt", "score": 3.0, "risks": ["tool_overreach_risk"]}],
                    "relevant_reflections": ["pattern"],
                    "open_questions": [],
                }

        class FakeMemory:
            def __init__(self):
                self.calls = []

            def build_retrieval_context(self, user_input, intent="", interaction_class="", n_history=6):
                self.calls.append((user_input, intent, interaction_class, n_history))
                return FakeRetrievalContext()

            def build_context(self, *args, **kwargs):
                raise AssertionError("legacy build_context should not be used")
            
            def format_retrieval_context(self, retrieval_ctx):
                return (
                    "[active_directives]\n  - prio=20: Antworten klar halten\n"
                    "[semantic_context]\n[semantik] routing context"
                )

            def get_fact_record(self, key):
                return None

            def all_facts(self):
                return {}

            def add_conversation(self, *args, **kwargs):
                return None

            def save_task_result(self, *args, **kwargs):
                return None

        class FakeExecutor:
            def __init__(self):
                self.prompt = None

            def create_task(self, **kwargs):
                self.prompt = kwargs["prompt"]
                return SimpleNamespace(
                    typ=kwargs["typ"],
                    prompt=kwargs["prompt"],
                    antwort="ok",
                    fehler="",
                    score=SimpleNamespace(total=7.0),
                    provider_used="test-provider",
                    iteration=0,
                    id="task1",
                    decision_trace=DecisionTrace(),
                )

            async def submit_and_wait(self, task, timeout=180.0):
                return task

        from neural_core import get_neural_cortex
        from learning_engine import get_learning_engine

        kernel = object.__new__(IsaacKernel)
        kernel.memory = FakeMemory()
        kernel.executor = FakeExecutor()
        kernel.meaning = SimpleNamespace(record_impact=lambda *args, **kwargs: None)
        kernel.values = SimpleNamespace(update=lambda *args, **kwargs: None)
        kernel.neural = get_neural_cortex()
        kernel.learning = get_learning_engine()
        kernel._build_system = lambda sudo_aktiv, emp, wissen_kontext="", strategy_note="": "system"
        kernel._provider_hint = lambda user_input: None
        classification = ClassificationResult(
            interaction_class=InteractionClass.NORMAL_CHAT,
            normalized_text="was ist 2 2",
            has_question=True,
            word_count=3,
        )

        result, score = asyncio.run(
            kernel._standard_task(
                user_input="Was ist 2+2?",
                intent=Intent.CHAT,
                sudo_aktiv=False,
                emp=SimpleNamespace(),
                wissen_kontext="",
                interaction_class=InteractionClass.NORMAL_CHAT,
                classification=classification,
            )
        )

        self.assertEqual(result, "ok")
        self.assertEqual(score, 7.0)
        self.assertEqual(kernel.memory.calls[0][0], "Was ist 2+2?")
        self.assertIn("[active_directives]", kernel.executor.prompt)
        self.assertIn("[semantic_context]", kernel.executor.prompt)
        self.assertNotIn("[Steffen-Direktiven]", kernel.executor.prompt)

    def test_bug_9_kernel_detects_natural_openrouter_browser_request(self):
        self.assertTrue(
            self.kernel._is_browser_request(
                "Isaac geh auf openrouter.com, öffne settings und generiere ein token"
            )
        )

    def test_bug_23_kernel_detects_groq_provider_provision_request(self):
        self.assertTrue(
            self.kernel._is_provider_provision_request("verbinde groq und erstelle api key")
        )
        self.assertTrue(
            self.kernel._is_browser_request("provider: groq api key holen")
        )
        self.assertEqual(
            self.kernel._resolve_provider_provision_target("verbinde groq"),
            "groq",
        )

    def test_bug_24_low_complexity_provider_setup_is_tool_request(self):
        result = classify_interaction_result("bitte groq verbinden")
        self.assertEqual(result.interaction_class, InteractionClass.TOOL_REQUEST)

    def test_bug_26_kernel_detects_provision_all_providers_request(self):
        self.assertTrue(
            self.kernel._wants_provision_all_providers("hole alle api keys und verbinde alle provider")
        )
        self.assertEqual(
            self.kernel._resolve_provider_provision_target("alle provider keys holen"),
            "all",
        )

    def test_bug_27_provision_specs_cover_major_providers(self):
        from browser import provisionable_provider_ids

        ids = set(provisionable_provider_ids())
        self.assertTrue({"groq", "openrouter", "mistral", "together", "huggingface"}.issubset(ids))

    def test_bug_25_browser_providers_needing_provision_detects_missing_groq(self):
        from browser import BrowserManager, PROVIDER_PROVISION_SPECS

        browser = object.__new__(BrowserManager)
        browser.cfg = SimpleNamespace(
            relay=SimpleNamespace(primary_provider="groq"),
            providers={
                "groq": SimpleNamespace(
                    enabled=True,
                    api_key="",
                    provider_type="openai_compat",
                ),
                "ollama": SimpleNamespace(
                    enabled=True,
                    api_key="",
                    provider_type="ollama",
                ),
            },
            free_only_providers=True,
            is_provider_allowed=lambda _pid: False,
        )
        missing = browser.providers_needing_provision()
        self.assertIn("groq", missing)
        self.assertNotIn("ollama", missing)
        self.assertIn("groq", PROVIDER_PROVISION_SPECS)

    def test_bug_10_kernel_parses_structured_browser_flow(self):
        parsed = self.kernel._parse_browser_request(
            "browser: openrouter | https://openrouter.ai/settings/keys | click Settings; wait 1; extract #token -> token"
        )
        self.assertEqual(parsed["instance_id"], "openrouter")
        self.assertEqual(parsed["url"], "https://openrouter.ai/settings/keys")
        self.assertEqual(parsed["actions"][0]["action"], "click")
        self.assertEqual(parsed["actions"][1]["action"], "wait")
        self.assertEqual(parsed["actions"][2]["action"], "extract_text")

    def test_capability_browser_auf_maps_to_browser_intent(self):
        intent = self.kernel._resolve_intent_from_classification(
            "Browser auf GitHub",
            Intent.CHAT,
            InteractionClass.TOOL_REQUEST,
        )
        self.assertEqual(intent, Intent.BROWSER)
        parsed = self.kernel._parse_simple_browser_request("Browser auf GitHub")
        self.assertEqual(parsed["url"], "https://github.com")

    def test_bug_browser_without_colon_is_browser_request(self):
        """'Browser host …' without colon must not fall through to search."""
        text = "Browser isaac-free.onrender.com kannst du die Seite bedienen?"
        self.assertTrue(self.kernel._is_browser_request(text))
        parsed = self.kernel._parse_simple_browser_request(text)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["url"], "https://isaac-free.onrender.com")
        self.assertIsNone(parsed.get("login_user"))

    def test_bug_browser_url_strips_login_credentials(self):
        """Credentials must not become part of Page.goto URL."""
        text = (
            "Browser: www.google.de login: user@example.com "
            "passwort: secret123  Kannst du dich einloggen?"
        )
        self.assertTrue(self.kernel._is_browser_request(text))
        parsed = self.kernel._parse_simple_browser_request(text)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["url"], "https://www.google.de")
        self.assertEqual(parsed.get("login_user"), "user@example.com")
        self.assertEqual(parsed.get("login_password"), "secret123")
        self.assertNotIn("login", parsed["url"])
        self.assertNotIn("secret", parsed["url"])

    def test_mission_hackerone_is_browser_request(self):
        """Natural language bounty mission must not fall through to pure chat."""
        text = (
            "gehe auf hackerone oder andere bugbounty Anbieter "
            "und erarbeite eigenständig Belohnungen"
        )
        self.assertTrue(self.kernel._is_browser_request(text))
        parsed = self.kernel._parse_simple_browser_request(text)
        self.assertIsNotNone(parsed)
        self.assertIn("hackerone.com", parsed["url"])
        from execution_contract import KIND_BOUNTY_RESEARCH, detect_mission
        spec = detect_mission(text)
        self.assertIsNotNone(spec)
        self.assertEqual(spec.kind, KIND_BOUNTY_RESEARCH)
        self.assertTrue(spec.wants_background)

    def test_mission_google_login_nl_routes_browser(self):
        """'log dich bei Google ein' with email+password → browser mission."""
        text = (
            "log dich bei Google ein login: user@example.com passwort: secret123"
        )
        self.assertTrue(self.kernel._is_browser_request(text))
        parsed = self.kernel._parse_simple_browser_request(text)
        self.assertIsNotNone(parsed)
        self.assertIn("google", parsed["url"])
        self.assertEqual(parsed.get("login_user"), "user@example.com")
        self.assertEqual(parsed.get("login_password"), "secret123")

    def test_anti_hallucination_blocks_fake_browser_success(self):
        from execution_contract import apply_anti_hallucination

        fake = "Ich habe mich erfolgreich bei Google eingeloggt und die Seite geöffnet."
        out = apply_anti_hallucination(
            "log dich bei Google ein",
            fake,
            tools_ran=False,
        )
        self.assertIn("Execution Contract", out)
        self.assertNotIn("erfolgreich bei Google eingeloggt", out.lower())
        # With evidence marker, leave success text (real tool path)
        real = "[Browser] ok\n[Evidence]\nok=true\nIch habe navigiert."
        out2 = apply_anti_hallucination("Browser auf google", real, tools_ran=False)
        self.assertIn("[Evidence]", out2)
        # Future-tense theater also blocked
        start = "Ich starte die Browser-Automation und hole die API-Keys."
        out3 = apply_anti_hallucination("hole keys", start, tools_ran=False)
        self.assertTrue(
            "Execution Contract" in out3
            or "Kein Tool-Lauf" in out3
            or "Pending" in out3
        )

    def test_followup_und_continuity_no_credential_fishing(self):
        """'Und?' must not open key/browser missions; strip noise + style_note."""
        from executor import Strategy
        from low_complexity import InteractionClass

        k = self.kernel
        self.assertTrue(k._is_short_followup("Und?"))
        self.assertTrue(k._is_short_followup("und dann?"))
        self.assertFalse(k._is_short_followup("Was ist 2+2?"))

        noisy = (
            "active_directives\n"
            "  - prio=9: Provider-API-Keys selbst beschaffen\n"
            "  - login: steffen@x passwort: secret\n"
            "recent: 2+2 is 4\n"
        )
        clean = k._strip_mission_noise_from_context(noisy)
        self.assertNotIn("api-key", clean.lower().replace("_", "-"))
        self.assertNotIn("passwort", clean.lower())
        self.assertIn("2+2", clean)

        strat = k._select_response_strategy(
            "Und?",
            Intent.CHAT,
            InteractionClass.NORMAL_CHAT,
            retrieval_ctx={},
        )
        self.assertIsInstance(strat, Strategy)
        self.assertFalse(strat.allow_tools)
        self.assertFalse(strat.allow_provider_switch)
        self.assertIn("Follow-up", strat.style_note or "")
        self.assertIn("Anmeldedaten", strat.style_note or "")

    def test_owner_notify_only_hard_blockers(self):
        import time
        from owner_notify import infer_blocker_from_text, notify_owner_blocker, OwnerBlocker

        self.assertIsNone(infer_blocker_from_text("Seite lädt langsam, ich versuche es nochmal"))
        b = infer_blocker_from_text(
            "Browser-Automation ist deaktiviert (browser_automation disabled)"
        )
        self.assertIsNotNone(b)
        self.assertEqual(b.kind, "browser_disabled")

        async def _run():
            with patch.dict(os.environ, {"ISAAC_OWNER_PUSH": "1", "ISAAC_NTFY_TOPIC": ""}, clear=False):
                with patch("owner_notify.ntfy_topic", return_value=""):
                    with patch("owner_notify.webhook_url", return_value=""):
                        r = await notify_owner_blocker(
                            OwnerBlocker(
                                kind="missing_credentials",
                                title="Login fehlt",
                                detail="test",
                                need="credentials",
                                cooldown_key=f"test-creds-{time.time()}",
                            ),
                            force=True,
                        )
                        return r

        result = asyncio.run(_run())
        self.assertTrue(result.get("ok"))
        self.assertTrue(result.get("pushed"))

    def test_professional_core_catalog_registers(self):
        import tempfile
        from pathlib import Path
        from tool_catalog import (
            professional_core_catalog_ids,
            ensure_catalog_tools_registered,
            get_catalog_item,
        )
        from tool_registry import ToolRegistry

        ids = professional_core_catalog_ids()
        self.assertIn("public_crtsh_subdomains", ids)
        self.assertIn("public_cloudflare_doh", ids)
        self.assertIsNotNone(get_catalog_item("public_crtsh_subdomains"))

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tools.json"
            reg = ToolRegistry(path=path)
            with patch("tool_registry.get_tool_registry", return_value=reg):
                added = ensure_catalog_tools_registered(bundle_id="professional_core")
            self.assertTrue(len(added) >= 5)
            # idempotent
            with patch("tool_registry.get_tool_registry", return_value=reg):
                added2 = ensure_catalog_tools_registered(bundle_id="professional_core")
            self.assertEqual(added2, [])

    def test_bug_execution_query_ignores_retrieval_weather_procedure(self):
        """Search/tools must use owner line, not retrieval mashup with weather procedure."""
        from executor import Task, TaskType

        user = "Okay zeig mir die Google api keys"
        retrieval = (
            "[active_directives]\n  - prio=9: Provider-API-Keys\n"
            "[relevant_procedures]\n  - rel=1.00 owner_action tools=owner:weather\n\n"
            f"{user}"
        )
        task = Task(
            id="t-exec-q",
            typ=TaskType.SEARCH,
            prompt=retrieval,
            beschreibung=user,
        )
        self.assertEqual(task.execution_query(), user)
        self.assertNotIn("weather", task.execution_query().lower())
        self.assertNotIn("[active_directives]", task.execution_query())

    def test_bug_quality_short_answers_acceptable(self):
        """Short Q&A and owner short-style must not force 80/200-word follow-ups."""
        from logic import QualityEvaluator, FollowUpGenerator, prefers_short_answers

        self.assertTrue(prefers_short_answers("Was ist 2+2?"))
        self.assertTrue(
            prefers_short_answers(
                "[Antwortstil] Bleibe knapp, direkt und rein sachlich.\n\nWas ist 2+2?"
            )
        )
        ev = QualityEvaluator()
        ans = "2+2 ist 4."
        score = ev.evaluate(ans, "Was ist 2+2?")
        self.assertTrue(
            score.acceptable,
            f"short answer should pass quality, got {score.summary()} issues={score.issues}",
        )
        d = FollowUpGenerator(ev).decide(ans, "Was ist 2+2?", score, 0, "openrouter")
        self.assertFalse(d.needed, f"no follow-up expected, got {d}")

        mid = (
            "Nein. Ich habe nur die Dashboard-Seite gelesen und nichts in den "
            "Remote-Chat geschrieben."
        )
        s2 = ev.evaluate(mid, "Hast du dort in den Chat etwas geschrieben?")
        self.assertTrue(s2.acceptable, f"got {s2.summary()} issues={s2.issues}")
        d2 = FollowUpGenerator(ev).decide(
            mid, "Hast du dort in den Chat etwas geschrieben?", s2, 0, "openrouter"
        )
        self.assertFalse(d2.needed)

    def test_bug_followup_switch_only_when_critical(self):
        """Provider switch only on critically bad scores — not every weak answer."""
        from logic import FollowUpGenerator, QualityEvaluator, QualityScore

        ev = QualityEvaluator()
        gen = FollowUpGenerator(ev)
        # Moderately weak but not critical — rephrase/targeted, no switch
        weak = QualityScore(
            total=4.8, length=5.0, coverage=4.0, specificity=5.0, coherence=5.0,
            word_count=40, issues=["Schlüsselthemen nicht vollständig abgedeckt"],
        )
        d = gen.decide("etwas vage antwort hier mit ein paar woertern", "Erkläre Quantencomputing ausführlich bitte mit Kontext", weak, 0, "openrouter")
        self.assertTrue(d.needed)
        self.assertFalse(d.switch_provider, f"unexpected switch: {d}")
        # Critically bad → switch
        bad = QualityScore(
            total=1.2, length=0.0, coverage=0.0, specificity=0.0, coherence=0.0,
            word_count=2, issues=["Zu kurz: 2/80 Wörter"],
        )
        d2 = gen.decide("ok", "Erkläre die Geschichte der Schrift ausführlich", bad, 0, "openrouter")
        self.assertTrue(d2.needed)
        self.assertTrue(d2.switch_provider)

    def test_bug_followup_provider_skips_cold_ollama(self):
        """Follow-up must not prefer never-succeeded Ollama over cloud keys."""
        from executor import Executor
        from relay import ProviderHealth

        ex = Executor()
        prev = ex.relay._health.get("ollama")
        try:
            # Simulate ollama present but never healthy this process
            cold = ProviderHealth()
            cold.consecutive_failures = 3
            cold.failure_count = 5
            cold.success_count = 0
            cold.last_error = "nicht erreichbar"
            ex.relay._health["ollama"] = cold
            self.assertFalse(ex._provider_looks_online("ollama"))
        finally:
            if prev is None:
                ex.relay._health.pop("ollama", None)
            else:
                ex.relay._health["ollama"] = prev

    def test_capability_recherche_maps_to_research_intent(self):
        intent = self.kernel._resolve_intent_from_classification(
            "recherche: aktuelle KI Sicherheitsstandards",
            Intent.RESEARCH,
            InteractionClass.TOOL_REQUEST,
        )
        self.assertEqual(intent, Intent.RESEARCH)

    def test_capability_agent_observe_parses_and_routes(self):
        from computer_use import parse_agent_body, parse_agent_flow

        intent = self.kernel._resolve_intent_from_classification(
            "agent: observe",
            Intent.AGENT,
            InteractionClass.TOOL_REQUEST,
        )
        self.assertEqual(intent, Intent.AGENT)
        action = parse_agent_body("observe")
        self.assertEqual(action.action, "observe")
        flow = parse_agent_flow("flow shell pwd; screenshot")
        self.assertEqual(len(flow), 2)
        self.assertEqual(flow[0].action, "shell")
        self.assertEqual(flow[1].action, "screenshot")

    def test_ui_automation_parses_hierarchy_and_taps_by_label(self):
        from ui_automation import find_nodes, parse_bounds, parse_ui_xml, summarize_nodes

        xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node text="Chrome" clickable="true" enabled="true" bounds="[0,100][200,180]" />
  <node text="Einstellungen" clickable="true" enabled="true" bounds="[0,300][240,360]" />
  <node text="Passwörter" content-desc="Passwörter" clickable="true" enabled="true" bounds="[0,420][240,480]" />
</hierarchy>"""
        nodes = parse_ui_xml(xml)
        self.assertEqual(parse_bounds("[0,420][240,480]"), (0, 420, 240, 480))
        matches = find_nodes(nodes, "Passwort")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].center(), (120, 450))
        summary = summarize_nodes(nodes)
        self.assertIn("Einstellungen", summary)

    def test_agent_ui_commands_parse(self):
        from computer_use import parse_agent_body, parse_agent_flow

        self.assertEqual(parse_agent_body("ui dump").action, "ui_dump")
        self.assertEqual(parse_agent_body("ui list").action, "ui_list")
        tap = parse_agent_body("ui tap Einstellungen")
        self.assertEqual(tap.action, "ui_tap")
        self.assertEqual(tap.params["text"], "Einstellungen")
        self.assertEqual(parse_agent_body("ui unlock").action, "ui_unlock")
        flow = parse_agent_flow("flow ui dump; ui tap Passwörter; ui key enter")
        self.assertEqual([step.action for step in flow], ["ui_dump", "ui_tap", "ui_key"])

    def test_credential_access_extracts_visible_login(self):
        from credential_access import extract_visible_credentials
        from ui_automation import parse_ui_xml

        xml = """<hierarchy>
          <node text="accounts.google.com" clickable="true" enabled="true" bounds="[0,100][300,160]" />
          <node text="owner@example.com" clickable="false" enabled="true" bounds="[0,200][300,240]" />
          <node text="S3cretPass!" password="true" clickable="false" enabled="true" bounds="[0,260][300,300]" />
        </hierarchy>"""
        records = extract_visible_credentials(parse_ui_xml(xml), site_hint="google")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].username, "owner@example.com")
        self.assertEqual(records[0].password, "S3cretPass!")

    def test_credential_access_owner_gate(self):
        from credential_access import require_credential_access
        from unittest.mock import patch

        with patch("credential_access.is_owner_equivalent_mode", return_value=False):
            self.assertIn("Admin", require_credential_access() or "")

    def test_agent_credential_commands_parse(self):
        from computer_use import parse_agent_body

        self.assertEqual(parse_agent_body("credential list").action, "credential_list")
        read = parse_agent_body("credential read google.com --import")
        self.assertEqual(read.action, "credential_read")
        self.assertEqual(read.params["site"], "google.com")
        self.assertTrue(read.params["import"])

    def test_owner_action_detects_credential_read(self):
        from owner_action import detect_owner_action
        from unittest.mock import patch

        with patch("owner_action.is_owner_equivalent_mode", return_value=True):
            action = detect_owner_action("lies passwort für google aus chrome")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "credential_access")
        self.assertEqual(action.params.get("site"), "google")

    def test_hermes_credential_access_requires_owner(self):
        from hermes_compat import HermesComputerUseAdapter, ComputerAction, PermissionMetadata

        cu = HermesComputerUseAdapter()
        with patch("config.is_owner_equivalent_mode", return_value=False):
            result = cu.validate(
                ComputerAction(
                    action="credential_read",
                    params={"site": "google.com"},
                    permission=PermissionMetadata(timeout=5, allowed_scope="local"),
                )
            )
        self.assertFalse(result.ok)

    def test_termux_bridge_diagnose_structure(self):
        from termux_bridge import diagnose_bridge, bridge_enabled

        diag = diagnose_bridge()
        self.assertIn("enabled", diag)
        self.assertIn("mode", diag)
        self.assertIn("tools", diag)
        self.assertEqual(diag["enabled"], bridge_enabled())

    def test_termux_bridge_rejects_empty_command(self):
        from termux_bridge import run_termux_command
        import asyncio

        result = asyncio.run(run_termux_command([]))
        self.assertFalse(result.get("ok"))

    def test_capability_file_access_list_and_read(self):
        from file_access import parse_file_command, execute_file_command
        from unittest.mock import patch
        import tempfile
        from pathlib import Path

        with patch("file_access.access_tier", return_value="full"), tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample = root / "probe.txt"
            sample.write_text("hello isaac", encoding="utf-8")
            list_cmd = parse_file_command(f"datei: list {root}")
            self.assertIsNotNone(list_cmd)
            self.assertEqual(list_cmd.operation, "list")
            list_out, list_ok = execute_file_command(list_cmd)
            self.assertTrue(list_ok)
            self.assertIn("probe.txt", list_out)

            read_cmd = parse_file_command(f"lese: {sample}")
            self.assertIsNotNone(read_cmd)
            read_out, read_ok = execute_file_command(read_cmd)
            self.assertTrue(read_ok)
            self.assertIn("hello isaac", read_out)


    def test_bug_11_process_short_circuits_greeting_without_runtime_dependencies(self):
        kernel = object.__new__(IsaacKernel)
        result = asyncio.run(kernel.process("Hallo Isaac"))
        self.assertEqual(result, "Hallo. Ich bin da.")

    def test_bug_12_process_short_circuits_ack_without_runtime_dependencies(self):
        kernel = object.__new__(IsaacKernel)
        result = asyncio.run(kernel.process("Danke"))
        self.assertEqual(result, "Gern. Ich bin da.")

    def test_bug_17_regelwerk_does_not_repeat_known_term_questions(self):
        from regelwerk import Regelwerk

        rw = object.__new__(Regelwerk)
        rw._regeln = {}
        rw._fragen = []
        rw._history = []

        first = rw._neue_frage(
            "Was meinst du genau mit 'Status'? Für zukünftige Anfragen wichtig.",
            "Unbekannter Begriff: Status",
            0.5,
        )
        self.assertIsNotNone(first)

        again = rw._neue_frage(
            "Was meinst du genau mit 'Status'? Für zukünftige Anfragen wichtig.",
            "Unbekannter Begriff: Status",
            0.5,
        )
        self.assertIsNone(again)
        self.assertTrue(rw._term_already_asked("Status"))
        self.assertEqual(rw._erkenne_unbekannte_begriffe("Wie ist der System Status?"), "")

    def test_bug_22_regelwerk_ignores_kennst_du_owner_question(self):
        from regelwerk import Regelwerk

        rw = object.__new__(Regelwerk)
        rw._regeln = {}
        rw._fragen = []
        rw._history = []
        self.assertEqual(rw._erkenne_unbekannte_begriffe("Kennst du steffen"), "")
        fragen = rw._generiere_fragen("Kennst du steffen", "Ja, ich kenne dich.", 7.0)
        self.assertEqual(fragen, [])

    def test_bug_18_kernel_resolves_pending_regelwerk_answer(self):
        from regelwerk import Frage

        kernel = object.__new__(IsaacKernel)
        frage = Frage(
            id="F-test",
            text="Was meinst du genau mit 'Status'? Für zukünftige Anfragen wichtig.",
            kontext="test",
            prioritaet=0.5,
        )
        saved_facts = []

        kernel.regelwerk = SimpleNamespace(
            beantworte_frage=lambda fid, text: setattr(frage, "beantwortet", True),
            build_answer_ack=lambda fid, text: f"Verstanden: {text}",
            analysiere=lambda *args, **kwargs: [],
            get_pending_frage=lambda: None,
            get_top_pending_frage=lambda: None,
            get_frage=lambda fid: frage if fid == "F-test" else None,
            _extract_term_from_frage=lambda f: "Status",
        )
        kernel.memory = SimpleNamespace(
            set_fact=lambda key, value, **kwargs: saved_facts.append((key, value)) or True
        )
        kernel._background = None
        kernel._awaiting_frage_id = "F-test"
        kernel.empathie = SimpleNamespace(
            analysiere=lambda text: SimpleNamespace(
                node=SimpleNamespace(zustand="neutral"),
                interface_fehler="",
            )
        )

        result = asyncio.run(
            kernel.process("Status meint bei mir den Systemzustand aller Module.")
        )

        self.assertIn("Verstanden", result)
        self.assertTrue(frage.beantwortet)
        self.assertIsNone(kernel._awaiting_frage_id)
        self.assertEqual(saved_facts, [
            ("definition.status", "Status meint bei mir den Systemzustand aller Module.")
        ])

    def test_bug_20_kernel_constitution_gate_blocks_self_modify_code(self):
        kernel = object.__new__(IsaacKernel)
        msg, gate = kernel._enforce_constitution_gate(
            "code: ändere die constitution.json komplett",
            Intent.CODE,
            sudo_aktiv=False,
        )
        self.assertIsNotNone(msg)
        self.assertIn("[Verfassung]", msg)
        self.assertIn("constitution_not_self_editable", msg)
        self.assertIsNotNone(gate)
        self.assertFalse(gate.get("allowed"))

    def test_bug_21_kernel_constitution_gate_allows_normal_code(self):
        kernel = object.__new__(IsaacKernel)
        msg, gate = kernel._enforce_constitution_gate(
            "code: print('hello')",
            Intent.CODE,
            sudo_aktiv=False,
        )
        self.assertIsNone(msg)
        self.assertIsNotNone(gate)
        self.assertTrue(gate.get("allowed"))

    def test_bug_19_retrieval_surfaces_definition_facts(self):
        import memory as memory_module
        from memory import Memory

        mem = object.__new__(Memory)
        mem._vector = None
        mem.get_directives = lambda: []
        mem.get_working_memory = lambda n: []
        mem.get_relevant_results = lambda query, limit=4: []
        mem.search_procedures = lambda query, limit=3: []
        mem.search_facts = lambda query, limit=8: []
        mem.get_fact_record = lambda key: {
            "key": key,
            "value": "Systemzustand aller Module",
            "confidence": 1.0,
            "source": "Steffen",
        } if key == "definition.status" else None

        ctx = mem.build_retrieval_context(
            "Wie ist der Status der Module?",
            intent="chat",
            interaction_class="NORMAL_CHAT",
        )
        facts = ctx.as_dict().get("relevant_facts", [])
        self.assertTrue(any(f.get("key") == "definition.status" for f in facts))

    def test_bug_16_post_process_accepts_frage_objects_from_background(self):
        from regelwerk import Frage
        from empathie import EmpathieResponse, NodeZustand

        kernel = object.__new__(IsaacKernel)
        kernel.regelwerk = SimpleNamespace(
            analysiere=lambda *args, **kwargs: [],
            get_pending_frage=lambda: None,
        )
        kernel._background = SimpleNamespace(
            get_erkenntnisse=lambda: [
                Frage(
                    id="F1",
                    text="Was meinst du genau mit 'Status'?",
                    kontext="test",
                    prioritaet=0.5,
                )
            ]
        )
        emp = EmpathieResponse(
            node=NodeZustand(),
            ton="standard",
            anpassungs_hinweis="",
            interface_fehler="",
            low_res_aktiv=False,
        )

        result = kernel._post_process(
            "Hallo Isaac. Laufen alle deine module?",
            "Alle Module laufen.",
            emp,
            score=8.0,
            t0=__import__("time").monotonic(),
        )

        self.assertIn("Alle Module laufen.", result)
        self.assertNotIn("[Fehler]", result)

    def test_bug_13_translate_keyword_without_prefix_stays_chat(self):
        intent = self.kernel._resolve_intent_from_classification(
            "Kannst du das bitte übersetzen?",
            detect_intent("Kannst du das bitte übersetzen?"),
            InteractionClass.NORMAL_CHAT,
        )
        self.assertEqual(intent, Intent.CHAT)

    def test_bug_14_explicit_translate_prefix_remains_command(self):
        intent = self.kernel._resolve_intent_from_classification(
            "Übersetze: Guten Morgen auf Englisch",
            detect_intent("Übersetze: Guten Morgen auf Englisch"),
            InteractionClass.NORMAL_CHAT,
        )
        self.assertEqual(intent, Intent.TRANSLATE)

    def test_bug_15_strategy_defaults_keep_chat_tooling_disabled(self):
        kernel = object.__new__(IsaacKernel)
        strategy = kernel._select_response_strategy(
            user_input="Was ist 2+2?",
            intent=Intent.CHAT,
            interaction_class=InteractionClass.NORMAL_CHAT,
            retrieval_ctx={},
        )
        self.assertFalse(strategy.allow_tools)
        self.assertTrue(strategy.allow_followup)


    def test_bug_16_translate_command_without_colon_is_preserved(self):
        detected = detect_intent("Übersetze bitte Hallo auf Englisch")
        self.assertEqual(detected, Intent.TRANSLATE)
        intent = self.kernel._resolve_intent_from_classification(
            "Übersetze bitte Hallo auf Englisch",
            detected,
            InteractionClass.NORMAL_CHAT,
        )
        self.assertEqual(intent, Intent.TRANSLATE)

    def test_bug_30_detect_intent_is_narrowed_to_explicit_command_patterns(self):
        detected = detect_intent("Kannst du den Satz bitte übersetzen?")
        self.assertEqual(detected, Intent.CHAT)

    def test_bug_31_status_classification_has_priority_over_regex_detection(self):
        intent = self.kernel._resolve_intent_from_classification(
            "status",
            detect_intent("status"),
            InteractionClass.STATUS_QUERY,
        )
        self.assertEqual(intent, Intent.STATUS)

    def test_bug_32_tool_classification_has_priority_for_search_routing(self):
        intent = self.kernel._resolve_intent_from_classification(
            "Suche: Wetter Berlin",
            detect_intent("Suche: Wetter Berlin"),
            InteractionClass.TOOL_REQUEST,
        )
        self.assertEqual(intent, Intent.SEARCH)

    def test_bug_33_status_classification_overrides_explicit_command_regex(self):
        intent = self.kernel._resolve_intent_from_classification(
            "übersetze hallo",
            Intent.TRANSLATE,
            InteractionClass.STATUS_QUERY,
        )
        self.assertEqual(intent, Intent.STATUS)

    def test_bug_17_executor_keeps_explicit_tool_policy_for_status_class(self):
        task = Task(
            id="t3",
            typ=TaskType.CHAT,
            prompt="status",
            beschreibung="chat",
            strategy=Strategy(allow_tools=True),
            interaction_class=InteractionClass.STATUS_QUERY,
        )
        executor = object.__new__(Executor)
        self.assertTrue(executor._should_try_tool(task, task.prompt, iteration=0))

    def test_bug_18_create_task_prefers_explicit_strategy_over_legacy_flags(self):
        executor = object.__new__(Executor)
        executor._tasks = {}
        executor.next_task_id = lambda: "t-fixed"
        strategy = Strategy(allow_tools=False, allow_followup=False, allow_provider_switch=False)
        task = executor.create_task(
            typ=TaskType.CHAT,
            prompt="Was ist 2+2?",
            strategy=strategy,
            allow_tools=True,
            allow_followup=True,
            allow_provider_switch=True,
        )
        self.assertFalse(task.allow_tools)
        self.assertFalse(task.allow_followup)
        self.assertFalse(task.allow_provider_switch)

    def test_bug_19_allow_tools_true_lightweight_is_eligible(self):
        task = Task(
            id="t-light",
            typ=TaskType.CHAT,
            prompt="Hallo",
            beschreibung="chat",
            strategy=Strategy(allow_tools=True),
            interaction_class=InteractionClass.SOCIAL_GREETING,
        )
        executor = object.__new__(Executor)
        decision = executor._evaluate_tool_eligibility(task, task.prompt, iteration=0)
        self.assertTrue(decision.eligible)
        self.assertEqual(decision.reason, ToolDecisionReason.ELIGIBLE)

    def test_bug_20_allow_tools_false_blocks_with_reason(self):
        task = Task(
            id="t-block",
            typ=TaskType.CHAT,
            prompt="Suche Wetter Berlin",
            beschreibung="chat",
            strategy=Strategy(allow_tools=False),
            interaction_class=InteractionClass.TOOL_REQUEST,
        )
        executor = object.__new__(Executor)
        decision = executor._evaluate_tool_eligibility(task, task.prompt, iteration=0)
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason, ToolDecisionReason.BLOCKED_ALLOW_TOOLS_FALSE)

    def test_bug_21_no_candidate_returns_eligible_but_no_candidate(self):
        task = Task(
            id="t-no-candidate",
            typ=TaskType.SEARCH,
            prompt="Suche Wetter Berlin",
            beschreibung="search",
            strategy=Strategy(allow_tools=True),
            interaction_class=InteractionClass.TOOL_REQUEST,
        )

        class FakeRegistry:
            def list_tools(self, active_only=True):
                return []

        async def fake_discover(*args, **kwargs):
            return {"tools": [], "source": "remote", "url": "http://127.0.0.1:8766"}

        with patch("tool_runtime.get_tool_registry", return_value=FakeRegistry()), patch("tool_runtime.discover_mcp_bridge", fake_discover):
            decision = asyncio.run(select_live_tool_for_task(task, task.prompt, 0, task.tool_policy))
        self.assertIsNone(decision.selected)
        self.assertEqual(decision.reason, ToolDecisionReason.ELIGIBLE_BUT_NO_CANDIDATE)

    def test_bug_22_classification_does_not_change_eligibility(self):
        executor = object.__new__(Executor)
        task_a = Task(
            id="t-class-a",
            typ=TaskType.CHAT,
            prompt="Prüfe Tools",
            beschreibung="chat",
            strategy=Strategy(allow_tools=True),
            interaction_class=InteractionClass.NORMAL_CHAT,
        )
        task_b = Task(
            id="t-class-b",
            typ=TaskType.CHAT,
            prompt="Prüfe Tools",
            beschreibung="chat",
            strategy=Strategy(allow_tools=True),
            interaction_class=InteractionClass.SOCIAL_GREETING,
        )
        decision_a = executor._evaluate_tool_eligibility(task_a, task_a.prompt, iteration=0)
        decision_b = executor._evaluate_tool_eligibility(task_b, task_b.prompt, iteration=0)
        self.assertEqual(decision_a.eligible, decision_b.eligible)
        self.assertEqual(decision_a.reason, decision_b.reason)

    def test_bug_23_lightweight_no_longer_forces_followup_break(self):
        class FakeScore:
            total = 3.0
            acceptable = False

            def summary(self):
                return "score=3.0"

        class FakeDecision:
            needed = False
            mode = "none"
            reason = "done"
            switch_provider = False
            followup_prompt = ""
            sub_tasks = []

        class FakeLogic:
            def __init__(self):
                self.followup_calls = 0

            def evaluate(self, *args, **kwargs):
                return FakeScore()

            def decide_followup(self, *args, **kwargs):
                self.followup_calls += 1
                return FakeDecision()

        class FakeRelay:
            async def ask_with_fallback(self, *args, **kwargs):
                return "Antwort", "test-provider"

        class FakeWatchdog:
            def record_progress(self, task_id):
                return None

        class FakeState:
            def to_dict(self):
                return {}

        class FakeToolState:
            def get_or_create(self, *args, **kwargs):
                return FakeState()

            def pop_next_input(self, *args, **kwargs):
                return ""

        executor = object.__new__(Executor)
        executor.logic = FakeLogic()
        executor.relay = FakeRelay()
        executor._tool_state = FakeToolState()
        executor._notify = lambda *args, **kwargs: None
        executor._get_watchdog = lambda: FakeWatchdog()

        task = Task(
            id="t-follow",
            typ=TaskType.CHAT,
            prompt="Hallo",
            beschreibung="chat",
            strategy=Strategy(allow_tools=False, allow_followup=True),
            interaction_class=InteractionClass.SOCIAL_GREETING,
        )

        asyncio.run(executor._execute_ai(task))
        self.assertEqual(executor.logic.followup_calls, 1)

    def test_bug_24_decision_trace_blocked_allow_tools_false(self):
        executor = object.__new__(Executor)
        task = Task(
            id="t-trace-blocked",
            typ=TaskType.CHAT,
            prompt="Nutze ein Tool",
            beschreibung="chat",
            strategy=Strategy(allow_tools=False),
        )

        decision = executor._evaluate_tool_eligibility(task, task.prompt, iteration=0)
        self.assertFalse(decision.eligible)
        self.assertEqual(task.decision_trace.entries[-1].phase, TracePhase.ELIGIBILITY)
        self.assertEqual(task.decision_trace.entries[-1].data["reason"], ToolDecisionReason.BLOCKED_ALLOW_TOOLS_FALSE.value)

    def test_bug_25_decision_trace_allowed_explicit_policy(self):
        executor = object.__new__(Executor)
        task = Task(
            id="t-trace-allowed",
            typ=TaskType.CHAT,
            prompt="Was ist 2+2?",
            beschreibung="chat",
            strategy=Strategy(allow_tools=True),
        )

        decision = executor._evaluate_tool_eligibility(task, task.prompt, iteration=0)
        self.assertTrue(decision.eligible)
        self.assertEqual(task.decision_trace.entries[-1].data["reason"], ToolDecisionReason.ELIGIBLE.value)

    def test_bug_26_decision_trace_eligible_but_no_candidate(self):
        class FakeState:
            def to_dict(self):
                return {}

        class FakeToolState:
            def record_call(self, *args, **kwargs):
                return None

            def get_or_create(self, *args, **kwargs):
                return FakeState()

            def generate_next_input(self, *args, **kwargs):
                return ""

        executor = object.__new__(Executor)
        executor._tool_state = FakeToolState()
        executor._notify = lambda *args, **kwargs: None

        task = Task(
            id="t-trace-no-candidate",
            typ=TaskType.CHAT,
            prompt="Suche Wetter Berlin",
            beschreibung="chat",
            strategy=Strategy(allow_tools=True),
        )

        async def fake_select(*args, **kwargs):
            from tool_policy import ToolSelectionDecision
            return ToolSelectionDecision(selected=None, reason=ToolDecisionReason.ELIGIBLE_BUT_NO_CANDIDATE, metadata={"candidate_count": 0})

        with patch("executor.select_live_tool_for_task", fake_select):
            ctx, follow = asyncio.run(executor._maybe_use_tool(task, task.prompt, iteration=0, used_tool_ids=set()))

        self.assertEqual(ctx, "")
        self.assertEqual(follow, "")
        events = [e.event for e in task.decision_trace.entries if e.phase == TracePhase.SELECTION]
        self.assertIn("no_candidate", events)

    def test_bug_27_decision_trace_tool_execution_failed(self):
        class FakeState:
            def to_dict(self):
                return {}

        class FakeToolState:
            def record_call(self, *args, **kwargs):
                return None

            def get_or_create(self, *args, **kwargs):
                return FakeState()

            def generate_next_input(self, *args, **kwargs):
                return ""

        executor = object.__new__(Executor)
        executor._tool_state = FakeToolState()
        executor._notify = lambda *args, **kwargs: None

        task = Task(
            id="t-trace-failed",
            typ=TaskType.CHAT,
            prompt="Nutze Tool",
            beschreibung="chat",
            strategy=Strategy(allow_tools=True),
        )

        async def fake_select(*args, **kwargs):
            from tool_policy import ToolSelectionDecision
            return ToolSelectionDecision(
                selected={
                    "identifier": "tool.fail",
                    "name": "Fail Tool",
                    "kind": "api",
                    "category": "general",
                    "source": "registry",
                },
                reason=ToolDecisionReason.SELECTED_CANDIDATE,
                metadata={"candidate_count": 1},
            )

        async def fake_run(*args, **kwargs):
            return {"ok": False, "error": "boom", "via": "api", "status_code": 500}

        with patch("executor.select_live_tool_for_task", fake_select), patch("executor.run_selected_tool", fake_run):
            ctx, follow = asyncio.run(executor._maybe_use_tool(task, task.prompt, iteration=0, used_tool_ids=set()))

        self.assertEqual(ctx, "")
        self.assertEqual(follow, "")
        events = [e.event for e in task.decision_trace.entries]
        self.assertIn("execution_failed", events)
        self.assertIn("context_skipped", events)
        self.assertIn("followup_not_generated", events)

    def test_bug_28_decision_trace_tool_execution_succeeded(self):
        class FakeState:
            def to_dict(self):
                return {}

        class FakeToolState:
            def record_call(self, *args, **kwargs):
                return None

            def get_or_create(self, *args, **kwargs):
                return FakeState()

            def generate_next_input(self, *args, **kwargs):
                return "next prompt"

        executor = object.__new__(Executor)
        executor._tool_state = FakeToolState()
        executor._notify = lambda *args, **kwargs: None

        task = Task(
            id="t-trace-success",
            typ=TaskType.CHAT,
            prompt="Nutze Tool",
            beschreibung="chat",
            strategy=Strategy(allow_tools=True),
        )

        async def fake_select(*args, **kwargs):
            from tool_policy import ToolSelectionDecision
            return ToolSelectionDecision(
                selected={
                    "identifier": "tool.ok",
                    "name": "OK Tool",
                    "kind": "api",
                    "category": "general",
                    "source": "registry",
                },
                reason=ToolDecisionReason.SELECTED_CANDIDATE,
                metadata={"candidate_count": 1},
            )

        async def fake_run(*args, **kwargs):
            return {"ok": True, "content": "sunny", "via": "api", "status_code": 200}

        with patch("executor.select_live_tool_for_task", fake_select), patch("executor.run_selected_tool", fake_run):
            ctx, follow = asyncio.run(executor._maybe_use_tool(task, task.prompt, iteration=0, used_tool_ids=set()))

        self.assertIn("[Tool-Kontext]", ctx)
        self.assertEqual(follow, "next prompt")
        events = [e.event for e in task.decision_trace.entries]
        self.assertIn("execution_succeeded", events)
        self.assertIn("context_appended", events)
        self.assertIn("followup_generated", events)

    def test_bug_29_task_to_dict_contains_serializable_decision_trace(self):
        task = Task(
            id="t-trace-serialize",
            typ=TaskType.CHAT,
            prompt="Hallo",
            beschreibung="chat",
        )
        task.decision_trace.add(TracePhase.ELIGIBILITY, "evaluated", {"eligible": True, "reason": "eligible"})

        data = task.to_dict()
        self.assertIn("decision_trace", data)
        self.assertEqual(data["decision_trace"][0]["phase"], "eligibility")
        self.assertEqual(data["decision_trace"][0]["event"], "evaluated")
        json.dumps(data)

    def test_decision_trace_portable_export_uses_canonical_phases(self):
        """Drive trace_otel.py doppelte TracePhase vermeiden — Export über echte Typen."""
        from decision_trace import (
            build_execution_llm_trace_data,
            export_portable_trace,
            redact_trace_data,
        )

        trace = DecisionTrace()
        trace.add(TracePhase.GOVERNANCE, "gate", {"allowed": True})
        trace.add(TracePhase.CLASSIFICATION, "classified", {"interaction_class": "normal_chat"})
        trace.add(TracePhase.MOTIVATION, "tick", {"goal_id": "g1"})
        trace.add(
            TracePhase.EXECUTION,
            "model_call",
            build_execution_llm_trace_data(
                provider="gemini",
                model="gemini-flash-lite-latest",
                latency_ms=123.4,
                iteration=0,
                prompt_chars=40,
                response_chars=12,
                input_tokens=20,
                output_tokens=5,
                ok=True,
                extra={"api_key": "should-not-leak"},
            ),
        )
        portable = trace.to_portable_export(request_id="req-portable-1")
        self.assertEqual(portable["schema"], "isaac.decision_trace.portable_v1_1")
        self.assertEqual(portable["request_id"], "req-portable-1")
        phases = [e["phase"] for e in portable["entries"]]
        self.assertEqual(phases[:3], ["governance", "classification", "motivation"])
        self.assertIn("execution", phases)
        # echte lowercase-Phasen, nicht UPPERCASE-Doppelwelt aus Drive-Patches
        span_phases = []
        exec_span_attrs = {}
        for rs in portable["resourceSpans"]:
            for ss in rs["scopeSpans"]:
                for span in ss["spans"]:
                    if "isaac.phase" in span.get("attributes", {}):
                        span_phases.append(span["attributes"]["isaac.phase"])
                    if span.get("attributes", {}).get("isaac.phase") == "execution":
                        exec_span_attrs = span["attributes"]
        self.assertIn("motivation", span_phases)
        self.assertEqual(exec_span_attrs.get("gen_ai.system"), "gemini")
        self.assertEqual(exec_span_attrs.get("gen_ai.request.model"), "gemini-flash-lite-latest")
        self.assertEqual(exec_span_attrs.get("latency_ms"), 123.4)
        # redaction
        exec_entry = next(e for e in portable["entries"] if e["phase"] == "execution")
        self.assertEqual(exec_entry["data"].get("api_key"), "[REDACTED]")
        red = redact_trace_data({"password": "x", "ok": True})
        self.assertEqual(red["password"], "[REDACTED]")
        # file export helper
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            out = export_portable_trace(trace, request_id="file-export-1", output_path=Path(tmp) / "t.json")
            self.assertTrue(out.exists())
            loaded = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(loaded["schema"], "isaac.decision_trace.portable_v1_1")
        json.dumps(portable)

    def test_bug_34_explanatory_weather_prompt_stays_normal_chat(self):
        result = classify_interaction_result(
            "Erkläre mir das Wetter als sprachliches Motiv in Literatur"
        )
        self.assertEqual(result.interaction_class, InteractionClass.NORMAL_CHAT)

    def test_bug_35_explanatory_api_github_prompt_stays_normal_chat(self):
        result = classify_interaction_result(
            "Erkläre mir die GitHub API Architektur konzeptionell"
        )
        self.assertEqual(result.interaction_class, InteractionClass.NORMAL_CHAT)

    def test_bug_36_explicit_search_prompt_remains_tool_request(self):
        result = classify_interaction_result("Suche GitHub API Dokumentation")
        self.assertEqual(result.interaction_class, InteractionClass.TOOL_REQUEST)

    def test_neural_core_propagates_seven_regions(self):
        from neural_core import NeuralCortex, NeuralRegion, PIPELINE_ORDER

        cortex = NeuralCortex()
        trace = cortex.propagate(
            interaction_class="NORMAL_CHAT",
            intent="chat",
            retrieval_ctx={
                "relevant_facts": [{"key": "test"}],
                "conversation_history": [],
                "relevant_task_results": [],
                "semantic_context": "",
                "active_directives": [],
                "behavioral_risks": [],
            },
            word_count=5,
            execution_score=7.5,
        )
        self.assertEqual(len(trace.signals), len(PIPELINE_ORDER))
        self.assertEqual(trace.signals[0].region, NeuralRegion.PERCEPTION)
        self.assertEqual(trace.signals[-1].region, NeuralRegion.CONSOLIDATION)

    def test_neural_core_reinforce_adjusts_weights_on_success(self):
        from neural_core import NeuralCortex, NeuralRegion

        cortex = NeuralCortex()
        before = cortex.get_weight(NeuralRegion.PERCEPTION, NeuralRegion.RETRIEVAL)
        trace = cortex.propagate(
            interaction_class="NORMAL_CHAT",
            intent="chat",
            retrieval_ctx={},
            word_count=3,
        )
        changed = cortex.reinforce(trace, 8.0)
        after = cortex.get_weight(NeuralRegion.PERCEPTION, NeuralRegion.RETRIEVAL)
        self.assertTrue(changed or after >= before)

    def test_neural_core_low_activation_disables_tools(self):
        from neural_core import NeuralCortex

        cortex = NeuralCortex()
        trace = cortex.propagate(
            interaction_class="SOCIAL_ACKNOWLEDGMENT",
            intent="chat",
            retrieval_ctx={},
            word_count=1,
        )
        modulation = cortex.modulate_strategy(
            allow_tools=True,
            allow_followup=True,
            allow_provider_switch=True,
            trace=trace,
        )
        self.assertFalse(modulation.allow_tools)

    def test_neural_core_stats_exposes_dashboard_payload(self):
        from neural_core import NeuralCortex

        stats = NeuralCortex().stats()
        self.assertEqual(stats["regions"], 7)
        self.assertIn("weights", stats)
        self.assertIn("perception:retrieval", stats["weights"])
        self.assertIn("pipeline", stats)

    def test_vector_memory_stats_when_chromadb_available(self):
        from vector_memory import get_vector_memory

        stats = get_vector_memory().stats()
        self.assertIn("aktiv", stats)
        self.assertFalse(stats["aktiv"])
        self.assertEqual(stats.get("grund"), "deaktiviert")


class TestHermesCompatibilityLayer(unittest.TestCase):
    def setUp(self):
        self.policy = ConfirmationPolicy(path=Path('/tmp/isaac_test_confirmation_queue.json'))
        self.policy._queue = []
        self.adapter = HermesCompatibilityAdapter(confirmation_policy=self.policy)

    def test_skill_registration(self):
        self.adapter.register_skill({"name": "search_assist", "description": "help", "tools": ["search"]})
        self.assertIsNotNone(self.adapter.skill_registry.get("search_assist"))

    def test_tool_schema_mapping(self):
        normalized = HermesToolSchemaMapper.normalize({"name": "lookup", "inputSchema": {"type": "object"}})
        self.assertEqual(normalized["name"], "lookup")
        self.assertEqual(normalized["risk"], "medium")

    def test_permission_blocking(self):
        self.adapter.register_tool(
            {"name": "danger_tool", "description": "x", "risk": "critical", "outside_effect": True},
            lambda payload: {"ok": True, "payload": payload},
        )
        result = self.adapter.execute_tool("danger_tool", {"a": 1}, ExecutionContext(caller="user", level=0))
        self.assertFalse(result.ok)
        self.assertIn("Review-ID", result.error)

    def test_confirmation_queue(self):
        self.adapter.register_tool(
            {"name": "confirm_tool", "description": "x", "risk": "critical", "outside_effect": True},
            lambda payload: {"ok": True},
        )
        _ = self.adapter.execute_tool("confirm_tool", {}, ExecutionContext(caller="user", level=0))
        self.assertGreaterEqual(len(self.policy.pending()), 1)

    def test_browser_adapter_dry_run(self):
        browser = HermesBrowserAdapter()
        result = browser.run(BrowserAction(action="browser_navigate", url="https://example.org"), dry_run=True)
        self.assertTrue(result.ok)
        self.assertTrue(result.output["dry_run"])

    def test_computer_use_action_validation(self):
        cu = HermesComputerUseAdapter()
        result = cu.validate(ComputerAction(action="shell_command", params={"command": "echo ok"}, permission=PermissionMetadata(timeout=5, allowed_scope="workspace")))
        self.assertTrue(result.ok)

    def test_computer_use_shell_blocks_unsafe_commands_in_runtime(self):
        from computer_use import ComputerUseRuntime, AgentAction

        runtime = ComputerUseRuntime()
        result = asyncio.run(runtime.execute(AgentAction("shell", {"command": "sudo rm -rf /"})))
        self.assertFalse(result.get("ok"))

    def test_owner_equivalent_shell_not_blocked(self):
        from computer_use import _blocked_shell

        with patch("computer_use.is_owner_equivalent_mode", return_value=True):
            self.assertIsNone(_blocked_shell("sudo rm -rf /"))

    def test_computer_use_action_blocks_unsafe_shell(self):
        cu = HermesComputerUseAdapter()
        result = cu.validate(ComputerAction(
            action="shell_command",
            params={"command": "sudo rm -rf /"},
            permission=PermissionMetadata(timeout=5, allowed_scope="workspace"),
        ))
        self.assertFalse(result.ok)

    def test_computer_use_action_scope_validation(self):
        cu = HermesComputerUseAdapter()
        result = cu.validate(ComputerAction(
            action="observe",
            permission=PermissionMetadata(timeout=5, allowed_scope="internet"),
        ))
        self.assertFalse(result.ok)

    def test_failed_tool_execution(self):
        self.adapter.register_tool({"name": "broken", "description": "x"}, lambda payload: 1 / 0)
        result = self.adapter.execute_tool("broken", {}, ExecutionContext(caller="user", level=9))
        self.assertFalse(result.ok)

    def test_audit_logging_and_memory_writeback_and_mcp_mirror(self):
        writes = []
        self.adapter.register_tool({"name": "safe_tool", "description": "x", "risk": "low"}, lambda payload: {"value": 1})
        mcp = MCPRegistry()
        self.adapter.mirror_tool_to_mcp(mcp, "safe_tool")
        result = self.adapter.execute_flow(
            user_task="use safe tool",
            tool_name="safe_tool",
            payload={},
            ctx=ExecutionContext(caller="user", level=9, task_id="t-hermes"),
            memory_writeback=lambda task, tool_result: writes.append((task, tool_result.ok)),
        )
        mcp_result = mcp.invoke_tool("safe_tool", {})
        self.assertTrue(result.ok)
        self.assertTrue(mcp_result["ok"])
        self.assertIn("output", mcp_result)
        self.assertIn("metadata", mcp_result)
        self.assertEqual(writes[0][0], "use safe tool")

    def test_mcp_mirror_propagates_blocked_tool_failure(self):
        self.adapter.register_tool(
            {"name": "danger_tool", "description": "x", "risk": "critical", "outside_effect": True},
            lambda payload: {"ignored": True},
        )
        mcp = MCPRegistry()
        self.adapter.mirror_tool_to_mcp(mcp, "danger_tool")
        mcp_result = mcp.invoke_tool("danger_tool", {})
        self.assertFalse(mcp_result["ok"])
        self.assertIn("danger_tool", mcp_result["error"])
        self.assertIn("Review-ID", mcp_result["error"])
        self.assertIn("queue_id", mcp_result)

    def test_result_contract_direct_and_mcp_paths(self):
        self.adapter.register_tool(
            {"name": "contract_ok", "description": "x", "risk": "low", "input_schema": {"type": "object", "required": ["query"]}},
            lambda payload: {"echo": payload["query"]},
        )
        self.adapter.register_tool(
            {"name": "contract_fail", "description": "x", "risk": "low"},
            lambda payload: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        mcp = MCPRegistry()
        self.adapter.mirror_tool_to_mcp(mcp, "contract_ok")
        self.adapter.mirror_tool_to_mcp(mcp, "contract_fail")

        direct_ok = self.adapter.execute_tool("contract_ok", {"query": "hi"}, ExecutionContext(caller="user", level=9))
        self.assertTrue(direct_ok.ok)
        self.assertIsInstance(direct_ok.metadata, dict)

        direct_arg_error = self.adapter.execute_tool("contract_ok", {}, ExecutionContext(caller="user", level=9))
        self.assertFalse(direct_arg_error.ok)
        self.assertIn("invalid_arguments", direct_arg_error.error)

        direct_handler_error = self.adapter.execute_tool("contract_fail", {}, ExecutionContext(caller="user", level=9))
        self.assertFalse(direct_handler_error.ok)
        self.assertIn("boom", direct_handler_error.error)

        mcp_ok = mcp.invoke_tool("contract_ok", {"query": "world"})
        self.assertTrue(mcp_ok["ok"])
        self.assertIn("output", mcp_ok)
        self.assertIn("metadata", mcp_ok)

        mcp_arg_error = mcp.invoke_tool("contract_ok", {})
        self.assertFalse(mcp_arg_error["ok"])
        self.assertIn("invalid_arguments", mcp_arg_error["error"])

        mcp_handler_error = mcp.invoke_tool("contract_fail", {})
        self.assertFalse(mcp_handler_error["ok"])
        self.assertIn("boom", mcp_handler_error["error"])

    def test_mcp_registry_wraps_raw_handler_payloads_in_output(self):
        mcp = MCPRegistry()
        mcp.register_tool("raw_list", {"description": "x"}, handler=lambda **kwargs: [1, 2, 3])
        mcp.register_tool("raw_status", {"description": "x"}, handler=lambda **kwargs: {"ok": True, "tasks": [{"id": "t1"}]})

        list_result = mcp.invoke_tool("raw_list", {})
        self.assertTrue(list_result["ok"])
        self.assertEqual(list_result["output"], [1, 2, 3])

        status_result = mcp.invoke_tool("raw_status", {})
        self.assertTrue(status_result["ok"])
        self.assertEqual(status_result["output"]["tasks"][0]["id"], "t1")


    def test_mcp_mirror_propagates_handler_failure(self):
        self.adapter.register_tool({"name": "broken_tool", "description": "x"}, lambda payload: 1 / 0)
        mcp = MCPRegistry()
        self.adapter.mirror_tool_to_mcp(mcp, "broken_tool")
        mcp_result = mcp.invoke_tool("broken_tool", {})
        self.assertFalse(mcp_result["ok"])
        self.assertIn("broken_tool", mcp_result["error"])
        self.assertIn("division by zero", mcp_result["error"])


class TestSelfModelHooks(unittest.TestCase):
    def test_preference_extracted_from_owner_statement(self):
        from self_model_hooks import process_interaction
        from self_model import get_self_model

        updates = process_interaction(
            user_input="Ich bevorzuge kurze nüchterne Antworten",
            interaction_class="NORMAL_CHAT",
            score=7.0,
        )
        self.assertTrue(updates.get("preferences"))
        prefs = get_self_model().relevant_preferences(limit=5)
        self.assertTrue(any(p.get("key") in ("prefer", "response_style") for p in prefs))

    def test_correction_records_high_confidence_preference(self):
        import self_model as self_model_module
        import tempfile
        from pathlib import Path
        from self_model_hooks import process_interaction
        from self_model import SelfModel
        from low_complexity import InteractionClass

        key = f"answer_style_{id(self)}"
        with tempfile.TemporaryDirectory() as tmp:
            isolated = SelfModel(path=Path(tmp) / "self_model.json")
            previous = self_model_module._model
            self_model_module._model = isolated
            try:
                process_interaction(
                    user_input=f"korrektur: {key} = kurz und direkt",
                    interaction_class=InteractionClass.NORMAL_CHAT,
                    score=7.0,
                )
            finally:
                self_model_module._model = previous
            owner_prefers = isolated.data.get("preference_state", {}).get("owner_prefers", [])
        self.assertTrue(
            any(
                p.get("key") == key and p.get("confidence", 0) >= 0.9
                for p in owner_prefers
                if isinstance(p, dict)
            )
        )

    def test_shared_theme_requires_recurrence(self):
        import self_model as self_model_module
        import tempfile
        from pathlib import Path
        from self_model import SelfModel
        from self_model_hooks import process_interaction, _extract_topic_theme

        theme_text = "routing executor klassifikation stabil"
        expected_theme = _extract_topic_theme(theme_text)
        self.assertEqual(expected_theme, "routing executor klassifikation")

        with tempfile.TemporaryDirectory() as tmp:
            isolated = SelfModel(path=Path(tmp) / "self_model.json")
            previous = self_model_module._model
            self_model_module._model = isolated
            try:
                first = process_interaction(
                    user_input=theme_text,
                    interaction_class="NORMAL_CHAT",
                    score=6.0,
                )
                self.assertEqual(first.get("themes"), [])
                second = process_interaction(
                    user_input=theme_text,
                    interaction_class="NORMAL_CHAT",
                    score=6.0,
                )
            finally:
                self_model_module._model = previous

        self.assertEqual(len(second.get("themes", [])), 1)
        self.assertEqual(second["themes"][0].get("theme"), expected_theme)
        self.assertEqual(second["themes"][0].get("count"), 2)

    def test_retrieval_enriched_with_self_model_preferences(self):
        from self_model_hooks import enrich_retrieval_with_self_model
        from self_model import get_self_model

        get_self_model().record_owner_preference(
            key="test_pref",
            value="knapp antworten",
            confidence=0.8,
            source="test",
        )
        enriched = enrich_retrieval_with_self_model({"preferences_context": []})
        self.assertTrue(enriched.get("self_model_preferences"))
        self.assertTrue(enriched.get("preferences_context"))

    def test_greeting_does_not_record_preference(self):
        import self_model as self_model_module
        import tempfile
        from pathlib import Path
        from self_model import SelfModel
        from self_model_hooks import process_interaction
        from low_complexity import InteractionClass

        with tempfile.TemporaryDirectory() as tmp:
            isolated = SelfModel(path=Path(tmp) / "self_model.json")
            previous = self_model_module._model
            self_model_module._model = isolated
            try:
                updates = process_interaction(
                    user_input="Hallo Isaac, antworte bitte kürzer",
                    interaction_class=InteractionClass.SOCIAL_GREETING,
                    score=7.0,
                )
            finally:
                self_model_module._model = previous

        self.assertEqual(updates.get("preferences"), [])
        owner_prefers = isolated.data.get("preference_state", {}).get("owner_prefers", [])
        self.assertEqual(owner_prefers, [])

    def test_casual_style_mention_not_recorded_as_preference(self):
        import self_model as self_model_module
        import tempfile
        from pathlib import Path
        from self_model import SelfModel
        from self_model_hooks import process_interaction
        from low_complexity import InteractionClass

        with tempfile.TemporaryDirectory() as tmp:
            isolated = SelfModel(path=Path(tmp) / "self_model.json")
            previous = self_model_module._model
            self_model_module._model = isolated
            try:
                updates = process_interaction(
                    user_input="Kannst du das Wetter als Motiv kürzer erklären?",
                    interaction_class=InteractionClass.NORMAL_CHAT,
                    score=7.0,
                )
            finally:
                self_model_module._model = previous

        self.assertEqual(updates.get("preferences"), [])
        owner_prefers = isolated.data.get("preference_state", {}).get("owner_prefers", [])
        self.assertEqual(owner_prefers, [])

    def test_positive_confirmation_boosts_pending_preference(self):
        import self_model as self_model_module
        import tempfile
        from pathlib import Path
        from self_model import SelfModel
        from self_model_hooks import process_interaction
        from low_complexity import InteractionClass

        with tempfile.TemporaryDirectory() as tmp:
            isolated = SelfModel(path=Path(tmp) / "self_model.json")
            previous = self_model_module._model
            self_model_module._model = isolated
            try:
                first = process_interaction(
                    user_input="Ich bevorzuge kurze nüchterne Antworten",
                    interaction_class=InteractionClass.NORMAL_CHAT,
                    score=7.0,
                )
                second = process_interaction(
                    user_input="Danke, genau so",
                    interaction_class=InteractionClass.SOCIAL_ACKNOWLEDGMENT,
                    score=6.5,
                )
            finally:
                self_model_module._model = previous

        self.assertTrue(first.get("preferences"))
        self.assertGreaterEqual(second.get("confirmations", 0), 1)
        owner_prefers = isolated.data.get("preference_state", {}).get("owner_prefers", [])
        confirmed = next(
            (
                p for p in owner_prefers
                if isinstance(p, dict) and p.get("key") == "prefer"
            ),
            None,
        )
        self.assertIsNotNone(confirmed)
        self.assertGreaterEqual(float(confirmed.get("confidence", 0)), 0.74)
        self.assertEqual(confirmed.get("source"), "owner_confirmation")

    def test_confirmed_preference_synced_to_memory(self):
        import self_model as self_model_module
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from self_model import SelfModel
        from self_model_hooks import process_interaction
        from low_complexity import InteractionClass

        stored_facts: dict[str, str] = {}

        class FakeMemory:
            def set_fact(self, key, value, source="", confidence=1.0):
                stored_facts[key] = value
                return True

            def log_development_event(self, **kwargs):
                return None

        with tempfile.TemporaryDirectory() as tmp:
            sm_path = Path(tmp) / "self_model.json"
            isolated = SelfModel(path=sm_path)
            previous_sm = self_model_module._model
            self_model_module._model = isolated
            key = f"answer_style_{id(self)}"
            try:
                with patch("memory.get_memory", return_value=FakeMemory()):
                    process_interaction(
                        user_input=f"korrektur: {key} = kurz und direkt",
                        interaction_class=InteractionClass.NORMAL_CHAT,
                        score=7.0,
                    )
            finally:
                self_model_module._model = previous_sm

        fact_key = f"pref.{key}.kurz_und_direkt"
        self.assertEqual(stored_facts.get(fact_key), "kurz und direkt")

    def test_self_model_detects_memory_contradiction(self):
        import self_model as self_model_module
        import tempfile
        from pathlib import Path
        from self_model import SelfModel
        from self_model_hooks import detect_self_model_fact_contradictions, enrich_retrieval_with_self_model

        class FakeMemory:
            def __init__(self, facts):
                self._facts = dict(facts)

            def get_fact_record(self, key):
                value = self._facts.get(key)
                if value is None:
                    return None
                return {"key": key, "value": value, "confidence": 1.0, "source": "Steffen"}

            def all_facts(self):
                return dict(self._facts)

        with tempfile.TemporaryDirectory() as tmp:
            isolated = SelfModel(path=Path(tmp) / "self_model.json")
            previous = self_model_module._model
            self_model_module._model = isolated
            try:
                isolated.record_owner_preference(
                    key="response_style",
                    value="kurz und knapp",
                    confidence=0.9,
                    source="owner_correction",
                )
                fact_key = isolated._preference_fact_key({
                    "key": "response_style",
                    "value": "kurz und knapp",
                })
                fake_memory = FakeMemory({fact_key: "ausführlich und detailliert"})
                contradictions = detect_self_model_fact_contradictions(memory=fake_memory)
                enriched = enrich_retrieval_with_self_model(
                    {"preferences_context": [], "risk_flags": []},
                    memory=fake_memory,
                )
            finally:
                self_model_module._model = previous

        self.assertTrue(contradictions)
        self.assertTrue(enriched.get("self_model_contradictions"))
        self.assertIn("self_model_fact_contradiction", enriched.get("risk_flags", []))


class TestConstitutionOwnerOverride(unittest.TestCase):
    def test_privilege_escalation_blocked_without_override(self):
        from constitution import get_constitution
        from constitution_override import evaluate_owner_override, build_override_context
        from config import Level

        verdict = get_constitution().validate_action(
            "tool_invoke",
            {"privilege_escalation": True, "owner_approved": False, "audit_logged": True},
        )
        self.assertFalse(verdict.get("allowed"))
        result = evaluate_owner_override(
            verdict,
            build_override_context(caller_level=Level.TASK),
        )
        self.assertFalse(result.get("allowed"))

    def test_owner_equivalent_config_defaults(self):
        from config import IsaacConfig, is_owner_equivalent_mode

        cfg = IsaacConfig(privilege_mode="admin", computer_use_enabled=False, filesystem_access_tier="workspace")
        cfg._apply_owner_equivalent_defaults()
        self.assertTrue(cfg.computer_use_enabled)
        self.assertTrue(cfg.computer_use_live)
        self.assertEqual(cfg.filesystem_access_tier, "full")
        self.assertTrue(is_owner_equivalent_mode(cfg))

    def test_owner_equivalent_security_policy_skips_confirmation(self):
        from security_policy import ConfirmationPolicy
        from privilege import PrivCtx
        from config import Level

        policy = ConfirmationPolicy(path=Path("/tmp/isaac_test_confirm_queue.json"))
        ctx = PrivCtx(caller="Executor", level=Level.ISAAC, r_trace="test trace long enough")
        with patch("security_policy.is_owner_equivalent_mode", return_value=True):
            verdict = policy.analyze("system_command", ctx, {"risk": "high", "outside_effect": True})
        self.assertTrue(verdict.allowed)
        self.assertFalse(verdict.requires_confirmation)

    def test_owner_equivalent_file_access_outside_workspace(self):
        from file_access import resolve_path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "secret.txt"
            outside.write_text("ok", encoding="utf-8")
            with patch("file_access.is_owner_equivalent_mode", return_value=True):
                resolved, error = resolve_path(str(outside))
            self.assertFalse(error)
            self.assertEqual(resolved, outside.resolve())

    def test_owner_equivalent_constitution_auto_override(self):
        from constitution_override import apply_constitution_gate, build_override_context
        from config import Level

        with patch("constitution_override.is_owner_equivalent_mode", return_value=True):
            gate = apply_constitution_gate(
                "tool_invoke",
                {
                    "privilege_escalation": True,
                    "owner_approved": False,
                    "audit_logged": True,
                    "risk": "high",
                },
                build_override_context(caller_level=Level.TASK, source="test"),
            )
        self.assertTrue(gate.get("allowed"))
        self.assertTrue((gate.get("override") or {}).get("overridden"))

    def test_isaac_kernel_detects_shell_without_agent_prefix_in_admin(self):
        kernel = IsaacKernel()
        with patch("isaac_core.is_owner_equivalent_mode", return_value=True):
            self.assertTrue(kernel._is_agent_request("shell ls -la /"))
            self.assertEqual(detect_intent("shell pwd"), Intent.AGENT)

    def test_owner_action_detects_google_photos_search(self):
        from owner_action import detect_owner_action

        action = detect_owner_action(
            "Isaac suche mir bei Google Fotos raus über gelbe Blumen"
        )
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "photos_search")
        self.assertEqual(action.params.get("query"), "gelbe Blumen")

    def test_owner_action_detects_filesystem_cleanup(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("räume mein dateisystem auf")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "filesystem_cleanup")

    def test_owner_action_detects_wlan_status(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("zeig mir den wlan status vom router")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "wlan_status")

    def test_owner_action_ignores_explanatory_chat(self):
        from owner_action import detect_owner_action

        self.assertIsNone(
            detect_owner_action("erkläre mir das Wetter als sprachliches Motiv in Literatur")
        )

    def test_owner_action_detects_router_admin(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("öffne die router oberfläche im wlan")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "router_admin")

    def test_owner_action_detects_wlan_connect_with_ssid(self):
        from owner_action import detect_owner_action

        action = detect_owner_action('verbinde dich mit wlan "MeinHeim"')
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "wlan_connect")
        self.assertEqual(action.params.get("ssid"), "MeinHeim")

    def test_owner_action_detects_web_search(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("suche bei google nach python asyncio tutorial")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "web_search")
        self.assertIn("python", action.params.get("query", "").lower())

    def test_owner_action_detects_cleanup_dry_run(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("zeig mir was du beim dateisystem aufräumen würdest")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "filesystem_cleanup")
        self.assertTrue(action.params.get("dry_run"))

    def test_owner_action_detects_file_list(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("zeige dateien in ~/Downloads")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "file_list")

    def test_owner_action_detects_app_open(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("öffne einstellungen")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "app_open")

    def test_owner_action_detects_shell_inline(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("führe aus: ls -la")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "shell")
        self.assertEqual(action.params.get("command"), "ls -la")

    def test_owner_action_scan_cleanup_targets(self):
        from owner_action import _scan_cleanup_targets
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "sub" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "x.pyc").write_bytes(b"x")
            with patch("owner_action._cleanup_roots", return_value=[root]):
                targets = _scan_cleanup_targets("standard")
            paths = {str(p) for p, _ in targets}
            self.assertTrue(any("__pycache__" in p for p in paths))

    def test_owner_action_detects_downloads_cleanup(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("räume downloads auf")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "filesystem_cleanup")
        self.assertEqual(action.params.get("root"), "~/Downloads")

    def test_owner_action_detects_local_photos_search(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("suche in meinen fotos nach gelbe blumen")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "photos_search")
        self.assertEqual(action.params.get("query"), "gelbe blumen")

    def test_owner_action_detects_start_site_alias(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("starte youtube")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "open_target")
        self.assertEqual(action.params.get("target"), "youtube")

    def test_owner_action_detects_email_compose(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("schreib email an max@example.com")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "email_compose")
        self.assertEqual(action.params.get("to"), "max@example.com")

    def test_owner_action_detects_email_search(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("suche in mails nach rechnung januar")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "email_search")
        self.assertIn("rechnung", action.params.get("query", "").lower())

    def test_owner_action_detects_calendar_today(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("was steht heute im kalender")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "calendar_open")
        self.assertEqual(action.params.get("view"), "today")

    def test_owner_action_detects_maps_navigate(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("navigiere nach Berlin Hauptbahnhof")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "maps_navigate")
        self.assertIn("berlin", action.params.get("destination", "").lower())

    def test_owner_action_detects_device_storage_status(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("wie voll ist mein speicher")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "device_status")
        self.assertEqual(action.params.get("kind"), "storage")

    def test_owner_action_detects_file_copy(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("kopiere ~/a.txt nach ~/b.txt")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "file_operation")
        self.assertEqual(action.params.get("operation"), "copy")

    def test_owner_action_detects_screenshot(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("mach screenshot")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "screenshot")

    def test_owner_action_detects_phone_call(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("rufe an +49 170 1234567")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "phone_call")

    def test_owner_action_detects_translate(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("übersetze guten morgen nach englisch")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "translate")

    def test_owner_action_detects_weather(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("zeige wetter in Berlin")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "weather")
        self.assertEqual(action.params.get("location"), "Berlin")

    def test_owner_action_detects_media_play(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("spiele auf spotify bohemian rhapsody")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "media_play")
        self.assertEqual(action.params.get("platform"), "spotify")

    def test_owner_action_detects_timer(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("starte timer 5 minuten")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "timer")
        self.assertEqual(action.params.get("seconds"), 300)

    def test_owner_action_detects_device_toggle(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("schalte wlan aus")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "device_toggle")
        self.assertEqual(action.params.get("target"), "wlan")

    def test_owner_action_detects_shopping_search(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("suche auf amazon nach usb c kabel")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "shopping_search")

    def test_owner_action_detects_git_command(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("git status")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "git_command")

    def test_owner_action_detects_isaac_status(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("isaac status")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "isaac_ops")

    def test_owner_action_detects_find_files(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("finde datei config.py in ~/workspace")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "find_files")

    def test_owner_action_detects_datetime_status(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("wie spät ist es")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "device_status")
        self.assertEqual(action.params.get("kind"), "datetime")

    def test_owner_action_detects_process_status(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("zeige prozesse")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "device_status")
        self.assertEqual(action.params.get("kind"), "processes")

    def test_owner_action_detects_hotspot_toggle(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("hotspot an")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "device_toggle")
        self.assertEqual(action.params.get("target"), "hotspot")

    def test_owner_action_detects_speedtest(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("speedtest")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "network_test")
        self.assertEqual(action.params.get("kind"), "speedtest")

    def test_owner_action_detects_read_file(self):
        from owner_action import detect_owner_action

        action = detect_owner_action("lies datei ~/.bashrc")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "file_operation")
        self.assertEqual(action.params.get("operation"), "read")

    def test_isaac_kernel_routes_owner_action_in_admin_mode(self):
        kernel = IsaacKernel()

        async def _run():
            with patch("isaac_core.is_owner_equivalent_mode", return_value=True):
                with patch(
                    "owner_action.execute_owner_action",
                    return_value=("[Owner] Test", True),
                ) as execute_mock:
                    result = await kernel.process("isaac status")
            return result, execute_mock

        result, execute_mock = asyncio.run(_run())
        self.assertIn("[Owner] Test", result)
        execute_mock.assert_awaited_once()

    def test_record_owner_action_outcome_updates_procedure(self):
        from procedure_memory import record_owner_action_outcome
        from memory import get_memory

        mem = get_memory()
        outcome = record_owner_action_outcome(kind="isaac_ops", raw="isaac status", ok=True)
        self.assertIsNotNone(outcome)
        stored = mem.get_procedure_by_signature(outcome["signature"])
        self.assertIsNotNone(stored)
        self.assertEqual(stored.get("task_type"), "owner_action")
        self.assertIn("owner:isaac_ops", stored.get("tools_used") or [])

    def test_override_prefix_with_sudo_allows_and_audits(self):
        from constitution_override import apply_constitution_gate, build_override_context
        from config import Level
        from memory import get_memory

        gate = apply_constitution_gate(
            "tool_invoke",
            {"privilege_escalation": True, "owner_approved": False, "audit_logged": True},
            build_override_context(
                prompt="override: notwendiger Zugriff für Wartung",
                sudo_active=True,
                caller_level=Level.STEFFEN,
                source="test",
            ),
        )
        self.assertTrue(gate.get("allowed"))
        self.assertTrue((gate.get("override") or {}).get("overridden"))
        events = get_memory().recent_development_events(15)
        self.assertTrue(
            any(
                e.get("event_type") == "constitution_override"
                and e.get("target_key") == "tool_invoke"
                for e in events
            )
        )

    def test_constitution_self_modify_not_overridable(self):
        from constitution_override import apply_constitution_gate, build_override_context
        from config import Level

        gate = apply_constitution_gate(
            "tool_invoke",
            {"self_modify_constitution": True, "audit_logged": True},
            build_override_context(
                sudo_active=True,
                caller_level=Level.STEFFEN,
                override_reason="verfassung ändern",
                source="test",
            ),
        )
        self.assertFalse(gate.get("allowed"))
        self.assertIn("constitution_not_self_editable", gate.get("blocked_by", []))

    def test_detect_override_prefix(self):
        from constitution_override import detect_override_prefix

        explicit, reason = detect_override_prefix("override: Wartungsfenster für Tool-Test")
        self.assertTrue(explicit)
        self.assertIn("Wartungsfenster", reason)


class TestMcpJsonRpc(unittest.TestCase):
    def setUp(self):
        from mcp_jsonrpc import get_jsonrpc_handler
        from mcp_registry import get_mcp_registry

        self.handler = get_jsonrpc_handler(get_mcp_registry())

    def _call(self, method: str, params: dict | None = None, req_id: int = 1) -> dict:
        return self.handler.dispatch({
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        })

    def test_jsonrpc_initialize(self):
        resp = self._call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        )
        self.assertEqual(resp.get("id"), 1)
        self.assertIn("result", resp)
        self.assertEqual(resp["result"].get("protocolVersion"), "2024-11-05")
        self.assertEqual(resp["result"]["serverInfo"]["name"], "isaac")

    def test_jsonrpc_tools_list_and_call(self):
        self._call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})
        tools_resp = self._call("tools/list", req_id=2)
        tools = tools_resp["result"]["tools"]
        self.assertTrue(any(t.get("name") == "isaac.query_memory" for t in tools))

        call_resp = self._call(
            "tools/call",
            {"name": "isaac.query_memory", "arguments": {"query": "status", "limit": 2}},
            req_id=3,
        )
        self.assertIn("result", call_resp)
        self.assertFalse(call_resp["result"].get("isError"))
        self.assertTrue(call_resp["result"]["content"])

    def test_jsonrpc_resources_read(self):
        self._call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})
        resp = self._call(
            "resources/read",
            {"uri": "resource://constitution", "params": {}},
            req_id=4,
        )
        self.assertIn("result", resp)
        self.assertTrue(resp["result"]["contents"])
        self.assertIn("constitution", resp["result"]["contents"][0]["text"])

    def test_jsonrpc_unknown_method(self):
        resp = self._call("does/not/exist", req_id=5)
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32601)

    def test_stdio_transport_ping(self):
        import json
        import subprocess
        import sys

        proc = subprocess.run(
            [sys.executable, "mcp_server.py", "--stdio"],
            input='{"jsonrpc":"2.0","id":9,"method":"ping","params":{}}\n',
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent),
            timeout=10,
        )
        self.assertEqual(proc.returncode, 0)
        line = proc.stdout.strip().splitlines()[-1]
        data = json.loads(line)
        self.assertEqual(data.get("id"), 9)
        self.assertEqual(data.get("result"), {})


class TestEvalSuites(unittest.TestCase):
    def _assert_eval_suite(self, result: dict):
        self.assertGreater(int(result.get("total", 0)), 0)
        failed = [c for c in result.get("cases", []) if not c.get("ok")]
        self.assertEqual(result.get("passed"), result.get("total"), msg=failed)

    def test_mcp_eval_suite_passes(self):
        from evals.mcp_eval import run

        result = run()
        self.assertEqual(result.get("suite"), "mcp")
        self._assert_eval_suite(result)

    def test_replay_eval_suite_passes(self):
        from evals.replay_eval import run

        result = run()
        self.assertEqual(result.get("suite"), "replay")
        self._assert_eval_suite(result)

    def test_learning_eval_regelwerk_memory_cases(self):
        from evals.learning_eval import run

        result = run()
        self.assertEqual(result.get("suite"), "learning")
        names = {c["name"] for c in result.get("cases", [])}
        self.assertIn("regelwerk_answer_persists_definition_fact", names)
        self.assertIn("definition_fact_surfaces_in_retrieval", names)
        for case in result.get("cases", []):
            if case["name"] in {
                "regelwerk_answer_persists_definition_fact",
                "definition_fact_surfaces_in_retrieval",
            }:
                self.assertTrue(case.get("ok"), msg=case)


class TestMcpToolRuntime(unittest.TestCase):
    def test_discover_mcp_bridge_closes_owned_session(self):
        """Regression ISAAC-4/7: dashboard polls must not leak aiohttp sessions."""
        from tool_runtime import clear_mcp_bridge_cache, discover_mcp_bridge
        from mcp_client import MCPClient

        clear_mcp_bridge_cache()
        closed = {"n": 0}
        real_close = MCPClient.close

        async def tracking_close(self):
            closed["n"] += 1
            return await real_close(self)

        async def boom_capabilities(self):
            # Force local-fallback path after session is created
            await self._get_session()
            raise RuntimeError("force-fallback")

        with patch.object(MCPClient, "capabilities", boom_capabilities), patch.object(
            MCPClient, "close", tracking_close
        ):
            result = asyncio.run(discover_mcp_bridge())
        self.assertEqual(result.get("source"), "local-fallback")
        self.assertGreaterEqual(closed["n"], 1, "owned MCPClient must be closed")

        # Cache should serve second call without another live session leak
        closed["n"] = 0
        with patch.object(MCPClient, "capabilities", boom_capabilities), patch.object(
            MCPClient, "close", tracking_close
        ):
            result2 = asyncio.run(discover_mcp_bridge())
        self.assertEqual(result2.get("source"), "local-fallback")
        self.assertEqual(closed["n"], 0, "cached discover must not open a new client")
        clear_mcp_bridge_cache()

    def test_mcp_client_close_nulls_session(self):
        from mcp_client import MCPClient

        async def _run():
            client = MCPClient("http://127.0.0.1:9")
            sess = await client._get_session()
            self.assertIsNotNone(client._session)
            self.assertFalse(sess.closed)
            await client.close()
            self.assertIsNone(client._session)

        asyncio.run(_run())

    def test_invoke_mcp_tool_uses_local_registry_fallback(self):
        from tool_runtime import invoke_mcp_tool

        async def fake_discover(*args, **kwargs):
            return {
                "ok": False,
                "source": "local-fallback",
                "tools": [{"name": "isaac.query_memory"}],
                "url": "http://127.0.0.1:8766",
            }

        with patch("tool_runtime.discover_mcp_bridge", fake_discover):
            result = asyncio.run(
                invoke_mcp_tool("isaac.query_memory", {"query": "status", "limit": 2})
            )
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("via"), "mcp")
        self.assertEqual(result.get("transport"), "local")

    def test_run_selected_tool_unified_mcp_source(self):
        from tool_runtime import run_selected_tool

        async def fake_invoke(name, arguments, **kwargs):
            self.assertEqual(name, "isaac.query_memory")
            return {
                "ok": True,
                "output": {"blocks": []},
                "error": "",
                "metadata": {"tool": name},
                "via": "mcp",
                "transport": "local",
            }

        selection = {
            "source": "mcp",
            "mcp_name": "isaac.query_memory",
            "kind": "mcp",
            "identifier": "mcp:isaac.query_memory",
        }
        with patch("tool_runtime.invoke_mcp_tool", fake_invoke):
            result = asyncio.run(run_selected_tool(selection, "status"))
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("via"), "mcp")

    def test_select_live_tool_marks_unified_mcp_source(self):
        from tool_runtime import select_live_tool_for_task

        task = Task(
            id=f"t-mcp-unified-{id(self)}",
            typ=TaskType.SEARCH,
            prompt="Suche Wetter Berlin",
            beschreibung="search",
            strategy=Strategy(allow_tools=True),
            interaction_class=InteractionClass.TOOL_REQUEST,
        )

        class FakeRegistry:
            def list_tools(self, active_only=True):
                return []

        async def fake_discover(*args, **kwargs):
            return {
                "ok": True,
                "source": "local-fallback",
                "tools": [{"name": "isaac.search_web", "description": "search"}],
                "url": "http://127.0.0.1:8766",
            }

        with patch("tool_runtime.get_tool_registry", return_value=FakeRegistry()), patch(
            "tool_runtime.discover_mcp_bridge", fake_discover
        ):
            decision = asyncio.run(select_live_tool_for_task(task, task.prompt, 0, task.tool_policy))
        self.assertIsNotNone(decision.selected)
        self.assertEqual(decision.selected.get("source"), "mcp")
        self.assertEqual(decision.selected.get("mcp_name"), "isaac.search_web")

    def test_legacy_mcp_remote_source_still_routes_through_invoke(self):
        from tool_runtime import run_selected_tool

        calls = {"count": 0}

        async def fake_invoke(name, arguments, **kwargs):
            calls["count"] += 1
            return {"ok": True, "output": "ok", "error": "", "metadata": {}, "via": "mcp"}

        selection = {
            "source": "mcp_remote",
            "mcp_name": "isaac.query_memory",
            "kind": "mcp",
        }
        with patch("tool_runtime.invoke_mcp_tool", fake_invoke):
            asyncio.run(run_selected_tool(selection, "status"))
        self.assertEqual(calls["count"], 1)


class TestForgettingDecay(unittest.TestCase):
    def test_contradicted_fact_degrades_confidence(self):
        from memory import get_memory

        mem = get_memory()
        key = f"pref_contradict_{id(self)}"
        mem.set_fact(key, "alpha", source="inferred", confidence=0.7)
        mem.set_fact(key, "beta", source="inferred", confidence=0.7)
        record = mem.get_fact_record(key)
        self.assertIsNotNone(record)
        self.assertLess(float(record["confidence"]), 0.7)

    def test_owner_correction_keeps_high_confidence(self):
        from memory import get_memory

        mem = get_memory()
        key = f"pref_owner_{id(self)}"
        mem.set_fact(key, "alpha", source="inferred", confidence=0.4)
        mem.set_fact(key, "beta", source="Steffen", confidence=1.0)
        record = mem.get_fact_record(key)
        self.assertEqual(float(record["confidence"]), 1.0)

    def test_old_development_events_are_archived(self):
        from memory import get_memory, _conn

        mem = get_memory()
        event_id = mem.log_development_event(
            event_type="test_archive",
            target_kind="test",
            target_key=f"archive_{id(self)}",
            reason="archive regression",
        )
        with _conn() as con:
            con.execute(
                "UPDATE development_events SET ts=? WHERE id=?",
                ("2018-01-01 00:00:00", event_id),
            )
        for i in range(8):
            mem.log_development_event(
                event_type="filler",
                target_kind="test",
                target_key=f"filler_{id(self)}_{i}",
                reason="archive filler",
            )
        archived = mem.archive_development_events(older_than_days=30, keep_recent=5)
        self.assertGreaterEqual(archived, 1)
        archived_rows = mem.recent_archived_development_events(30)
        self.assertTrue(any(int(r["id"]) == event_id for r in archived_rows))

    def test_weak_preference_decays_when_old(self):
        from forgetting_decay import decay_weak_preference_facts
        from memory import get_memory, _conn

        mem = get_memory()
        key = f"chat_pref_{id(self)}"
        mem.set_fact(key, "kurz", source="inferred", confidence=0.5)
        with _conn() as con:
            con.execute(
                "UPDATE facts SET updated=? WHERE key=?",
                ("2019-05-01 00:00:00", key),
            )
        changes = decay_weak_preference_facts(mem)
        self.assertTrue(any(c["key"] == key for c in changes))
        record = mem.get_fact_record(key)
        self.assertLess(float(record["confidence"]), 0.5)


class TestTaskCheckpointing(unittest.TestCase):
    def test_checkpoint_state_machine_constants(self):
        from task_checkpoint import CheckpointState, is_resumable_state, normalize_state

        self.assertIn(CheckpointState.PLANNING, CheckpointState.ALL)
        self.assertIn(CheckpointState.TOOL_PENDING, CheckpointState.ALL)
        self.assertIn(CheckpointState.EVALUATING, CheckpointState.ALL)
        self.assertIn(CheckpointState.LEARNING_COMMIT, CheckpointState.ALL)
        self.assertTrue(is_resumable_state(CheckpointState.PLANNING))
        self.assertTrue(is_resumable_state(CheckpointState.TOOL_RUNNING))
        self.assertFalse(is_resumable_state(CheckpointState.DONE))
        self.assertEqual(normalize_state("tool_running"), CheckpointState.TOOL_PENDING)

    def test_checkpoint_soft_transitions_preferred_and_invalid(self):
        from task_checkpoint import (
            CheckpointState,
            is_preferred_transition,
            is_valid_transition,
            transition_note,
        )

        # Happy path
        self.assertTrue(
            is_preferred_transition(CheckpointState.PLANNING, CheckpointState.TOOL_PENDING)
        )
        self.assertTrue(
            is_preferred_transition(CheckpointState.TOOL_PENDING, CheckpointState.EVALUATING)
        )
        self.assertTrue(
            is_valid_transition(
                CheckpointState.LEARNING_COMMIT, CheckpointState.DONE, strict=True
            )
        )
        # Soft allows re-checkpoint; preferred graph forbids DONE -> PLANNING? soft allows
        self.assertTrue(
            is_valid_transition(CheckpointState.DONE, CheckpointState.PLANNING, strict=False)
        )
        self.assertFalse(
            is_preferred_transition(CheckpointState.DONE, CheckpointState.PLANNING)
        )
        # Unknown state
        self.assertFalse(is_valid_transition(CheckpointState.PLANNING, "not_a_state"))
        self.assertIn("preferred", transition_note(CheckpointState.PLANNING, CheckpointState.EVALUATING))

    def test_checkpoint_writes_input_snapshot_with_current_prompt(self):
        from executor import get_executor, Task, TaskType, TaskStatus
        from memory import get_memory
        from task_checkpoint import CheckpointState, build_input_snapshot

        exe = get_executor()
        mem = get_memory()
        tid = f"cp_input_{id(self)}"
        task = Task(id=tid, typ=TaskType.CHAT, prompt="original", beschreibung="original")
        task.status = TaskStatus.RUNNING
        exe._tasks[tid] = task
        exe._checkpoint(task, CheckpointState.PLANNING, current_prompt="follow-up prompt")
        cp = mem.get_latest_checkpoint(tid)
        self.assertIsNotNone(cp)
        snap = json.loads(cp["input_snapshot"])
        self.assertEqual(snap["prompt"], "original")
        self.assertEqual(snap["current_prompt"], "follow-up prompt")
        expected = build_input_snapshot(task, current_prompt="follow-up prompt")
        self.assertEqual(snap["task_id"], expected["task_id"])

    def test_resume_task_queues_resumable_task(self):
        from executor import get_executor, Task, TaskType, TaskStatus
        from task_checkpoint import CheckpointState

        exe = get_executor()
        tid = f"cp_resume_{id(self)}"
        task = Task(id=tid, typ=TaskType.CHAT, prompt="resume me", beschreibung="resume me")
        task.status = TaskStatus.RESUMABLE
        exe._tasks[tid] = task
        exe._checkpoint(task, CheckpointState.EVALUATING)
        ok = exe.resume_task(tid)
        task2 = exe.get_task(tid)
        self.assertTrue(ok)
        self.assertEqual(task2.status, TaskStatus.QUEUED)
        self.assertEqual(task2.resume_strategy, "from_checkpoint")

    def test_mark_resumable_sets_status_and_reason(self):
        from executor import get_executor, Task, TaskType, TaskStatus
        from memory import get_memory
        from task_checkpoint import CheckpointState

        exe = get_executor()
        mem = get_memory()
        tid = f"cp_mark_{id(self)}"
        task = Task(id=tid, typ=TaskType.CHAT, prompt="relay fail", beschreibung="relay fail")
        task.status = TaskStatus.RUNNING
        task.checkpoint_state = CheckpointState.EVALUATING
        exe._tasks[tid] = task
        exe._mark_resumable(task, "relay_failure")
        self.assertEqual(task.status, TaskStatus.RESUMABLE)
        cp = mem.get_latest_checkpoint(tid)
        result = json.loads(cp["result_snapshot"])
        self.assertEqual(result.get("resume_reason"), "relay_failure")
        self.assertTrue(result.get("partial"))

    def test_resume_from_evaluating_uses_saved_answer(self):
        import asyncio
        from executor import get_executor, Task, TaskType, TaskStatus
        from logic import QualityScore
        from memory import get_memory

        async def _run():
            exe = get_executor()
            mem = get_memory()
            tid = f"cp_eval_{id(self)}"
            task = Task(id=tid, typ=TaskType.CHAT, prompt="saved answer", beschreibung="saved answer")
            task.status = TaskStatus.RESUMABLE
            exe._tasks[tid] = task
            mem.save_task_checkpoint(
                tid,
                "evaluating",
                input_snapshot={
                    "task_id": tid,
                    "typ": "chat",
                    "prompt": "saved answer",
                    "iteration": 0,
                    "status": "evaluating",
                },
                result_snapshot={
                    "answer_preview": "kurze Antwort",
                    "answer_full": "kurze Antwort mit genug Inhalt für eine Bewertung.",
                    "provider": "test-provider",
                },
            )
            good_score = QualityScore(total=9.0)
            with patch.object(exe.logic, "evaluate", return_value=good_score):
                action = await exe._resume_from_checkpoint(task)
            return action, task

        action, task = asyncio.run(_run())
        self.assertEqual(action, "done")
        self.assertEqual(task.status, TaskStatus.DONE)

    def test_resume_from_planning_continues_execution(self):
        import asyncio
        from executor import get_executor, Task, TaskType, TaskStatus
        from memory import get_memory

        async def _run():
            exe = get_executor()
            mem = get_memory()
            tid = f"cp_plan_{id(self)}"
            task = Task(id=tid, typ=TaskType.CHAT, prompt="plan resume", beschreibung="plan resume")
            task.status = TaskStatus.RESUMABLE
            exe._tasks[tid] = task
            mem.save_task_checkpoint(
                tid,
                "planning",
                input_snapshot={
                    "task_id": tid,
                    "typ": "chat",
                    "prompt": "plan resume",
                    "current_prompt": "plan resume iter 1",
                    "iteration": 1,
                    "status": "running",
                },
            )
            action = await exe._resume_from_checkpoint(task)
            return action, task

        action, task = asyncio.run(_run())
        self.assertEqual(action, "continue")
        self.assertEqual(task.status, TaskStatus.RUNNING)
        self.assertEqual(task.resume_current_prompt, "plan resume iter 1")

    def test_resume_task_skips_when_already_running(self):
        from executor import get_executor, Task, TaskType, TaskStatus
        from task_checkpoint import CheckpointState

        exe = get_executor()
        tid = f"cp_running_{id(self)}"
        task = Task(id=tid, typ=TaskType.CHAT, prompt="running", beschreibung="running")
        task.status = TaskStatus.RESUMABLE
        exe._tasks[tid] = task
        exe._running.add(tid)
        exe._checkpoint(task, CheckpointState.PLANNING)
        self.assertFalse(exe.resume_task(tid))

    def test_resume_evaluating_unacceptable_score_continues_followup(self):
        import asyncio
        from executor import get_executor, Task, TaskType, TaskStatus
        from logic import FollowUpDecision, QualityScore
        from memory import get_memory

        async def _run():
            exe = get_executor()
            mem = get_memory()
            tid = f"cp_follow_{id(self)}"
            task = Task(
                id=tid,
                typ=TaskType.CHAT,
                prompt="explain quantum computing in detail",
                beschreibung="explain",
                strategy=Strategy(allow_followup=True),
            )
            task.status = TaskStatus.RESUMABLE
            exe._tasks[tid] = task
            mem.save_task_checkpoint(
                tid,
                "evaluating",
                input_snapshot={
                    "task_id": tid,
                    "typ": "chat",
                    "prompt": task.prompt,
                    "current_prompt": task.prompt,
                    "iteration": 0,
                    "status": "evaluating",
                },
                result_snapshot={
                    "answer_full": "too short",
                    "answer_preview": "too short",
                    "provider": "test-provider",
                },
            )
            bad_score = QualityScore(total=2.0)
            followup = FollowUpDecision(
                needed=True,
                mode="refine",
                reason="needs detail",
                followup_prompt="Bitte ausführlicher antworten.",
            )
            with patch.object(exe.logic, "evaluate", return_value=bad_score), patch.object(
                exe.logic, "decide_followup", return_value=followup,
            ):
                action = await exe._resume_from_checkpoint(task)
            return action, task

        action, task = asyncio.run(_run())
        self.assertEqual(action, "continue")
        self.assertEqual(task.status, TaskStatus.RUNNING)
        self.assertEqual(task.resume_current_prompt, "Bitte ausführlicher antworten.")
        self.assertEqual(task.resume_start_iteration, 1)

    def test_resume_tool_pending_skips_completed_tool(self):
        import asyncio
        from executor import get_executor, Task, TaskType, TaskStatus
        from memory import get_memory

        async def _run():
            exe = get_executor()
            mem = get_memory()
            tid = f"cp_tool_{id(self)}"
            task = Task(id=tid, typ=TaskType.CHAT, prompt="search weather", beschreibung="search")
            task.status = TaskStatus.RESUMABLE
            exe._tasks[tid] = task
            mem.save_task_checkpoint(
                tid,
                "tool_pending",
                input_snapshot={
                    "task_id": tid,
                    "typ": "chat",
                    "prompt": "search weather",
                    "current_prompt": "search weather",
                    "iteration": 0,
                    "status": "running",
                },
                tool_snapshot={
                    "tool": "search",
                    "identifier": "search:web",
                    "kind": "search",
                    "pending": False,
                },
                result_snapshot={
                    "answer_preview": "sunny",
                    "answer_full": "sunny 20C",
                    "via": "search",
                },
                side_effect_refs=["search:search weather"],
            )
            action = await exe._resume_from_checkpoint(task)
            return action, task

        action, task = asyncio.run(_run())
        self.assertEqual(action, "continue")
        self.assertEqual(task.resume_used_tool_ids, ["search:web"])
        self.assertIn("[Tool-Kontext]", task.resume_tool_context)

    def test_exception_marks_resumable_from_db_checkpoint(self):
        import asyncio
        from executor import get_executor, Task, TaskType, TaskStatus
        from task_checkpoint import CheckpointState

        async def _run():
            exe = get_executor()
            tid = f"cp_exc_{id(self)}"
            task = Task(id=tid, typ=TaskType.CHAT, prompt="boom", beschreibung="boom")
            task.status = TaskStatus.RUNNING
            exe._tasks[tid] = task
            exe._checkpoint(task, CheckpointState.EVALUATING)
            task.checkpoint_state = "resume_completed"

            async def _boom(_task):
                raise RuntimeError("simulated failure")

            with patch.object(exe, "_execute_ai", side_effect=_boom):
                await exe._execute(task)
            return task

        task = asyncio.run(_run())
        self.assertEqual(task.status, TaskStatus.RESUMABLE)
        self.assertIn("simulated failure", task.fehler)

    def test_watchdog_checkpoint_resume_increments_restarts(self):
        from executor import get_executor, Task, TaskType, TaskStatus
        from memory import get_memory
        from task_checkpoint import CheckpointState
        from watchdog import TaskWatchdog

        exe = get_executor()
        wd = TaskWatchdog()
        wd.set_executor(exe)
        tid = f"cp_wd_{id(self)}"
        task = Task(id=tid, typ=TaskType.CHAT, prompt="hang", beschreibung="hang")
        task.status = TaskStatus.RUNNING
        exe._tasks[tid] = task
        get_memory().save_task_checkpoint(
            tid,
            CheckpointState.PLANNING,
            input_snapshot={"task_id": tid, "prompt": "hang", "iteration": 0},
        )
        asyncio.run(wd._handle_hang(task, 120.0))
        self.assertEqual(wd._restarts.get(tid), 1)
        self.assertEqual(task.status, TaskStatus.QUEUED)

    def test_interrupted_chat_task_full_resume_execute_cycle(self):
        import asyncio
        from executor import get_executor, Task, TaskType, TaskStatus, Strategy
        from logic import QualityScore

        async def _run():
            exe = get_executor()
            tid = f"cp_chat_full_{id(self)}"
            task = Task(
                id=tid,
                typ=TaskType.CHAT,
                prompt="Was ist Isaac?",
                beschreibung="unterbrochener CHAT",
                strategy=Strategy(allow_followup=True),
            )
            task.status = TaskStatus.QUEUED
            exe._tasks[tid] = task
            calls = {"ai": 0}

            async def _flaky_ai(current_task):
                calls["ai"] += 1
                if calls["ai"] == 1:
                    raise ConnectionError("relay timeout")
                current_task.antwort = "Isaac ist ein lokaler kognitiver Kernel."
                current_task.provider_used = "mock-provider"
                current_task.score = QualityScore(total=8.5)
                current_task.status = TaskStatus.DONE

            with patch.object(exe, "_execute_ai", side_effect=_flaky_ai):
                await exe._execute(task)

            self.assertEqual(task.status, TaskStatus.RESUMABLE)
            self.assertTrue(exe.resume_task(tid))
            resumed = exe.get_task(tid)
            self.assertEqual(resumed.status, TaskStatus.QUEUED)
            self.assertEqual(resumed.resume_strategy, "from_checkpoint")

            with patch.object(exe, "_execute_ai", side_effect=_flaky_ai):
                await exe._execute(resumed)

            self.assertEqual(resumed.status, TaskStatus.DONE)
            self.assertEqual(calls["ai"], 2)
            self.assertIn("Kernel", resumed.antwort)
            return resumed

        asyncio.run(_run())

    def test_regelwerk_open_questions_payload_for_dashboard(self):
        from regelwerk import Frage, get_regelwerk
        from monitor_server import MonitorServer

        rw = get_regelwerk()
        qid = f"F_dash_{id(self)}"
        rw._fragen.append(
            Frage(
                id=qid,
                text="Was meinst du mit Dashboard?",
                kontext="dashboard regression",
                prioritaet=0.66,
            )
        )
        payload = rw.open_questions_dict()
        self.assertTrue(any(item.get("id") == qid for item in payload))

        state = MonitorServer()._build_state()
        self.assertIn("open_questions", state)
        self.assertTrue(any(item.get("id") == qid for item in state["open_questions"]))
        self.assertIn("regelwerk", state)
        self.assertGreaterEqual(int(state["regelwerk"].get("offene_fragen", 0)), 1)

    def test_dashboard_task_resume_localhost_only(self):
        from monitor_server import is_localhost_request

        self.assertTrue(is_localhost_request("127.0.0.1"))
        self.assertTrue(is_localhost_request("::1"))
        self.assertTrue(is_localhost_request("localhost"))
        self.assertFalse(is_localhost_request("172.20.10.7"))
        self.assertFalse(is_localhost_request("10.0.0.5"))

    def test_checkpoint_retention_trims_per_task(self):
        from memory import get_memory
        from task_checkpoint import CHECKPOINT_MAX_PER_TASK

        mem = get_memory()
        tid = f"cp_retention_{id(self)}"
        for idx in range(CHECKPOINT_MAX_PER_TASK + 5):
            mem.save_task_checkpoint(
                tid,
                "planning",
                input_snapshot={"step": idx},
            )
        rows = mem.list_checkpoints(tid, limit=100)
        self.assertLessEqual(len(rows), CHECKPOINT_MAX_PER_TASK)
        self.assertEqual(int(rows[0]["checkpoint_id"]), max(int(r["checkpoint_id"]) for r in rows))
        latest = mem.get_latest_checkpoint(tid)
        self.assertIsNotNone(latest)
        self.assertEqual(latest["checkpoint_id"], rows[0]["checkpoint_id"])

    def test_checkpoint_age_cleanup_removes_old_terminal_states(self):
        from memory import get_memory, _conn
        from task_checkpoint import CheckpointState

        mem = get_memory()
        tid = f"cp_age_{id(self)}"
        with _conn() as con:
            con.execute(
                "INSERT INTO task_checkpoints "
                "(task_id, ts, state_name, input_snapshot, tool_snapshot, "
                "result_snapshot, memory_refs, side_effect_refs) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    tid,
                    "2020-01-01 00:00:00",
                    CheckpointState.DONE,
                    "{}",
                    "{}",
                    "{}",
                    "[]",
                    "[]",
                ),
            )
            con.execute(
                "INSERT INTO task_checkpoints "
                "(task_id, ts, state_name, input_snapshot, tool_snapshot, "
                "result_snapshot, memory_refs, side_effect_refs) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    tid,
                    "2026-07-11 00:00:00",
                    CheckpointState.PLANNING,
                    '{"keep": true}',
                    "{}",
                    "{}",
                    "[]",
                    "[]",
                ),
            )
        summary = mem.cleanup_task_checkpoints(task_id=tid, max_age_days=30)
        after = mem.list_checkpoints(tid, limit=20)
        self.assertGreaterEqual(summary.get("removed", 0), 1)
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0].get("state_name"), CheckpointState.PLANNING)

    def test_checkpoint_cleanup_stats(self):
        from memory import get_memory

        mem = get_memory()
        tid = f"cp_stats_{id(self)}"
        mem.save_task_checkpoint(tid, "planning")
        stats = mem.checkpoint_stats()
        self.assertGreaterEqual(int(stats.get("total", 0)), 1)
        self.assertGreaterEqual(int(stats.get("tasks", 0)), 1)


class TestProcedureMemory(unittest.TestCase):
    def test_procedure_capture_success_and_failure_downgrade(self):
        from procedure_memory import record_task_outcome, build_signature
        from memory import get_memory

        unique = f"procedure_test_{id(self)}"
        mem = get_memory()

        task = Task(
            id=f"proc-{unique}",
            typ=TaskType.SEARCH,
            prompt=f"Suche Wetter Berlin {unique}",
            beschreibung=f"Suche Wetter Berlin {unique}",
        )
        task.used_tools = [{"name": "search_web", "kind": "search"}]
        task.status = TaskStatus.DONE
        task.score = QualityScore(total=7.5)
        task.decision_trace = DecisionTrace()
        task.decision_trace.add(TracePhase.EXECUTION, "search_web ok")

        result = record_task_outcome(task)
        self.assertIsNotNone(result)
        self.assertGreater(result["reliability"], 0.5)
        self.assertFalse(result["degraded"])

        sig = build_signature(task)
        stored = mem.get_procedure_by_signature(sig)
        self.assertIsNotNone(stored)
        rel_after_success = float(stored["reliability"])

        task.status = TaskStatus.FAILED
        task.score = QualityScore(total=2.0)
        fail_result = record_task_outcome(task)
        self.assertIsNotNone(fail_result)
        self.assertLess(fail_result["reliability"], rel_after_success)
        self.assertTrue(fail_result["degraded"])

    def test_retrieval_context_exposes_procedures(self):
        from memory import get_memory

        mem = get_memory()
        ctx = mem.build_retrieval_context("Wetter Berlin Suche procedure")
        data = ctx.as_dict()
        self.assertIn("relevant_procedures", data)
        self.assertIsInstance(data["relevant_procedures"], list)
        formatted = mem.format_retrieval_context(ctx)
        if data["relevant_procedures"]:
            self.assertIn("[relevant_procedures]", formatted)


class TestPhase4Connect(unittest.TestCase):
    def test_regelwerk_open_question_surfaces_in_retrieval(self):
        from regelwerk import Frage, get_regelwerk
        from memory import get_memory

        rw = get_regelwerk()
        qid = f"F_ret_{id(self)}"
        rw._fragen.append(
            Frage(
                id=qid,
                text="Was meinst du genau mit 'Status'? Für zukünftige Anfragen wichtig.",
                kontext="retrieval test",
                prioritaet=0.8,
            )
        )
        ctx = get_memory().build_retrieval_context(
            user_input="Wie ist der System Status?",
            intent="chat",
            interaction_class="NORMAL_CHAT",
        )
        questions = ctx.as_dict().get("open_questions") or []
        self.assertTrue(any("Status" in q for q in questions))

    def test_kernel_standard_task_records_routing_trace(self):
        class FakeRetrievalContext:
            def as_dict(self):
                return {
                    "relevant_facts": [],
                    "relevant_procedures": [],
                    "open_questions": [],
                }

        class FakeMemory:
            def build_retrieval_context(self, *args, **kwargs):
                return FakeRetrievalContext()

            def format_retrieval_context(self, retrieval_ctx):
                return ""

            def add_conversation(self, *args, **kwargs):
                return None

            def save_task_result(self, *args, **kwargs):
                return None

        captured: dict = {}

        class FakeExecutor:
            def create_task(self, **kwargs):
                task_obj = SimpleNamespace(
                    typ=kwargs["typ"],
                    prompt=kwargs["prompt"],
                    antwort="ok",
                    fehler="",
                    score=SimpleNamespace(total=8.0),
                    provider_used="test",
                    iteration=0,
                    id="trace-task",
                    decision_trace=DecisionTrace(),
                )
                captured["task"] = task_obj
                return task_obj

            async def submit_and_wait(self, task, timeout=180.0):
                return task

        from neural_core import get_neural_cortex
        from learning_engine import get_learning_engine

        kernel = object.__new__(IsaacKernel)
        kernel.memory = FakeMemory()
        kernel.executor = FakeExecutor()
        kernel.meaning = SimpleNamespace(record_impact=lambda *args, **kwargs: None)
        kernel.values = SimpleNamespace(update=lambda *args, **kwargs: None)
        kernel.neural = get_neural_cortex()
        kernel.learning = get_learning_engine()
        kernel._build_system = lambda *args, **kwargs: "system"
        kernel._provider_hint = lambda user_input: None
        kernel._retrieve_relevant_context = lambda **kwargs: FakeRetrievalContext().as_dict()
        classification = ClassificationResult(
            interaction_class=InteractionClass.NORMAL_CHAT,
            normalized_text="was ist 2 2",
            has_question=True,
            word_count=3,
        )

        asyncio.run(
            kernel._standard_task(
                user_input="Was ist 2+2?",
                intent=Intent.CHAT,
                sudo_aktiv=False,
                emp=SimpleNamespace(),
                wissen_kontext="",
                interaction_class=InteractionClass.NORMAL_CHAT,
                classification=classification,
            )
        )
        events = [e.event for e in captured["task"].decision_trace.entries]
        phases = [e.phase for e in captured["task"].decision_trace.entries]
        self.assertIn("classified", events)
        self.assertIn("retrieved", events)
        self.assertIn("strategy_selected", events)
        self.assertIn(TracePhase.CLASSIFICATION, phases)
        self.assertIn(TracePhase.RETRIEVAL, phases)
        self.assertIn(TracePhase.STRATEGY, phases)

    def test_bug_31_constitution_block_audits_routing_trace(self):
        from audit import AUDIT_PATH
        from decision_trace import TracePhase

        kernel = object.__new__(IsaacKernel)
        before = AUDIT_PATH.stat().st_size if AUDIT_PATH.exists() else 0
        kernel._audit_constitution_block(
            Intent.CODE,
            {
                "allowed": False,
                "blocked_by": ["constitution_not_self_editable"],
                "verdict": {"action": "execute_code", "blocked_by": ["constitution_not_self_editable"]},
            },
        )
        self.assertTrue(AUDIT_PATH.exists())
        self.assertGreater(AUDIT_PATH.stat().st_size, before)
        last_line = AUDIT_PATH.read_text(encoding="utf-8").strip().splitlines()[-1]
        entry = json.loads(last_line)
        self.assertEqual(entry["typ"], "decision_trace")
        self.assertEqual(entry["outcome"], "blocked")
        self.assertEqual(entry["entries"][0]["phase"], TracePhase.GOVERNANCE.value)
        self.assertEqual(entry["entries"][0]["event"], "constitution_blocked")

    def test_procedure_hints_boost_matching_tool(self):
        from tool_runtime import _procedure_hints_for_prompt

        hints = _procedure_hints_for_prompt("Suche Wetter Berlin eindeutig")
        self.assertIsInstance(hints, dict)

    def test_owner_procedure_hints_map_weather_to_search_tool(self):
        from procedure_memory import owner_procedure_hints_for_prompt, record_owner_action_outcome
        from tool_runtime import _procedure_hints_for_prompt, _procedure_category_hint_for_prompt

        record_owner_action_outcome(kind="weather", raw="zeige wetter in Berlin", ok=True)
        hints, category = owner_procedure_hints_for_prompt("zeige wetter in München")
        self.assertIn("isaac.search_web", hints)
        self.assertGreater(hints["isaac.search_web"], 0.0)
        self.assertEqual(category, "suche")

        merged = _procedure_hints_for_prompt("zeige wetter in Hamburg")
        self.assertIn("isaac.search_web", merged)
        self.assertEqual(_procedure_category_hint_for_prompt("zeige wetter in Hamburg"), "suche")

    def test_due_owner_tasks_respects_window_and_interval(self):
        from datetime import datetime
        from owner_autonomy import due_owner_tasks

        last_runs = {"nightly_downloads_cleanup": datetime.now().isoformat()}
        akku = {"plugged": True, "prozent": 80}
        with patch("owner_autonomy.owner_autonomy_enabled", return_value=True):
            due_night = due_owner_tasks(
                last_runs=last_runs,
                akku=akku,
                now=datetime(2026, 7, 11, 3, 0, 0),
            )
            due_day = due_owner_tasks(
                last_runs={},
                akku=akku,
                now=datetime(2026, 7, 12, 14, 0, 0),
            )
        self.assertEqual(due_night, [])
        task_ids = {task.task_id for task in due_day}
        self.assertIn("daily_isaac_health", task_ids)
        self.assertNotIn("nightly_downloads_cleanup", task_ids)

    def test_run_due_owner_autonomy_tasks_executes_and_records(self):
        from datetime import datetime
        import tempfile
        from owner_autonomy import run_due_owner_autonomy_tasks

        notes: list[str] = []

        async def _run():
            with tempfile.TemporaryDirectory() as tmp:
                state_path = Path(tmp) / "owner_autonomy_state.json"
                with patch("owner_autonomy.owner_autonomy_enabled", return_value=True):
                    with patch("owner_autonomy.is_owner_equivalent_mode", return_value=True):
                        with patch("owner_autonomy.AUTONOMY_STATE_PATH", state_path):
                            with patch(
                                "owner_autonomy._constitution_gate_autonomy_task",
                                return_value=None,
                            ):
                                with patch(
                                    "owner_action.execute_owner_action",
                                    return_value=("[Owner] OK", True),
                                ):
                                    return await run_due_owner_autonomy_tasks(
                                        last_runs={},
                                        akku={"plugged": True, "prozent": 90},
                                        on_note=notes.append,
                                        now=datetime(2026, 7, 12, 10, 0, 0),
                                    )

        updated = asyncio.run(_run())
        self.assertIn("daily_isaac_health", updated)
        self.assertTrue(any("Owner-Autonomie" in note for note in notes))

    def test_background_loop_owner_autonomy_cycle_updates_state(self):
        from background_loop import BackgroundLoop

        bg = BackgroundLoop()

        async def _run():
            with patch(
                "owner_autonomy.run_due_owner_autonomy_tasks",
                return_value={"daily_isaac_health": "2026-07-12T10:00:00"},
            ) as run_mock:
                await bg._owner_autonomy_zyklus({"plugged": True, "prozent": 90})
            return run_mock

        run_mock = asyncio.run(_run())
        run_mock.assert_awaited_once()
        self.assertEqual(
            bg.state.owner_task_last_run.get("daily_isaac_health"),
            "2026-07-12T10:00:00",
        )

    def test_owner_autonomy_runtime_settings_override_window(self):
        import json
        import tempfile
        from owner_autonomy import get_scheduled_owner_tasks

        payload = {
            "settings": {},
            "owner_autonomy": {
                "enabled": True,
                "tasks": {
                    "daily_isaac_health": {
                        "window_start_hour": 8,
                        "window_end_hour": 20,
                    }
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime_settings.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with patch("owner_autonomy.RUNTIME_SETTINGS_PATH", path):
                tasks = {task.task_id: task for task in get_scheduled_owner_tasks()}
        health = tasks["daily_isaac_health"]
        self.assertEqual(health.window_start_hour, 8)
        self.assertEqual(health.window_end_hour, 20)

    def test_owner_autonomy_env_can_disable_weekly_task(self):
        from owner_autonomy import get_scheduled_owner_tasks

        with patch.dict(os.environ, {"ISAAC_OWNER_AUTONOMY_WEEKLY_ENABLED": "0"}, clear=False):
            task_ids = {task.task_id for task in get_scheduled_owner_tasks()}
        self.assertNotIn("weekly_deep_cleanup", task_ids)

    def test_security_toolkit_parses_metasploit_run(self):
        from security_toolkit import parse_security_command

        with patch("security_toolkit.security_toolkit_enabled", return_value=True):
            parsed = parse_security_command("nutze metasploit status")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.get("action"), "run")
        self.assertEqual(parsed.get("tool_id"), "metasploit")

    def test_security_toolkit_parses_sync(self):
        from security_toolkit import parse_security_command

        with patch("security_toolkit.security_toolkit_enabled", return_value=True):
            parsed = parse_security_command("sync security toolkit")
        self.assertEqual(parsed.get("action"), "sync")

    def test_owner_action_detects_security_toolkit(self):
        from owner_action import detect_owner_action

        with patch("owner_action.is_owner_equivalent_mode", return_value=True):
            with patch("security_toolkit.security_toolkit_enabled", return_value=True):
                action = detect_owner_action("zeige security toolkit")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "security_toolkit")
        self.assertEqual(action.params.get("action"), "list")

    def test_security_workflow_matches_wifite_scan(self):
        from security_workflows import match_workflow

        with patch("security_workflows.workflows_enabled", return_value=True):
            parsed = match_workflow("scanne mein wlan mit wifite")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.get("action"), "workflow")
        self.assertEqual(parsed.get("workflow_id"), "wlan_wifite_scan")

    def test_security_workflow_matches_nethunter_bootstrap(self):
        from security_workflows import match_workflow

        with patch("security_workflows.workflows_enabled", return_value=True):
            parsed = match_workflow("nethunter bootstrap")
        self.assertEqual(parsed.get("workflow_id"), "nethunter_bootstrap")

    def test_detect_nethunter_s8_native(self):
        from security_workflows import detect_nethunter_environment

        with patch("computer_use.detect_runtime", return_value="s8"):
            nh = detect_nethunter_environment()
        self.assertEqual(nh.get("mode"), "s8_native")
        self.assertTrue(nh.get("available"))

    def test_owner_action_detects_wlan_workflow(self):
        from owner_action import detect_owner_action

        with patch("owner_action.is_owner_equivalent_mode", return_value=True):
            with patch("security_toolkit.security_toolkit_enabled", return_value=True):
                with patch("security_workflows.workflows_enabled", return_value=True):
                    action = detect_owner_action("analysiere mein wlan")
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "security_toolkit")
        self.assertEqual(action.params.get("workflow_id"), "wlan_survey")

    def test_security_toolkit_register_custom_tool(self):
        import tempfile
        from security_toolkit import register_custom_tool, CUSTOM_TOOLS_PATH

        with tempfile.TemporaryDirectory() as tmp:
            custom_path = Path(tmp) / "custom.json"
            with patch("security_toolkit.CUSTOM_TOOLS_PATH", custom_path):
                with patch("security_toolkit.security_toolkit_enabled", return_value=True):
                    out = register_custom_tool(
                        tool_id="myscanner",
                        display_name="My Scanner",
                        install_command="apt install -y myscanner",
                    )
            self.assertTrue(out.get("ok"))

    def test_owner_autonomy_env_overrides_nightly_window(self):
        from owner_autonomy import get_scheduled_owner_tasks

        with patch.dict(
            os.environ,
            {
                "ISAAC_OWNER_AUTONOMY_NIGHTLY_START": "1",
                "ISAAC_OWNER_AUTONOMY_NIGHTLY_END": "4",
            },
            clear=False,
        ):
            tasks = {task.task_id: task for task in get_scheduled_owner_tasks()}
        nightly = tasks["nightly_downloads_cleanup"]
        self.assertEqual(nightly.window_start_hour, 1)
        self.assertEqual(nightly.window_end_hour, 4)

    def test_owner_autonomy_max_per_cycle_caps_due_list(self):
        from datetime import datetime
        from owner_autonomy import due_owner_tasks

        with patch("owner_autonomy.owner_autonomy_enabled", return_value=True):
            with patch("owner_autonomy.max_tasks_per_cycle", return_value=1):
                due = due_owner_tasks(
                    last_runs={},
                    akku={"plugged": True, "prozent": 90},
                    now=datetime(2026, 7, 12, 10, 0, 0),
                    limit=None,
                )
        self.assertLessEqual(len(due), 1)

    def test_owner_autonomy_failure_backoff_doubles_interval(self):
        from owner_autonomy import (
            ScheduledOwnerTask,
            _effective_interval_hours,
            _record_task_outcome,
        )

        task = ScheduledOwnerTask(
            task_id="daily_isaac_health",
            action_kind="isaac_ops",
            min_interval_hours=12.0,
        )
        state = {"tasks": {}}
        state = _record_task_outcome(state, "daily_isaac_health", ok=False, ts="t1")
        state = _record_task_outcome(state, "daily_isaac_health", ok=False, ts="t2")
        self.assertEqual(_effective_interval_hours(task, state), 24.0)

    def test_owner_autonomy_constitution_blocks_execution(self):
        from datetime import datetime
        import tempfile
        from owner_autonomy import run_due_owner_autonomy_tasks

        notes: list[str] = []

        async def _run():
            with tempfile.TemporaryDirectory() as tmp:
                state_path = Path(tmp) / "owner_autonomy_state.json"
                with patch("owner_autonomy.owner_autonomy_enabled", return_value=True):
                    with patch("owner_autonomy.is_owner_equivalent_mode", return_value=True):
                        with patch("owner_autonomy.AUTONOMY_STATE_PATH", state_path):
                            with patch(
                                "owner_autonomy._constitution_gate_autonomy_task",
                                return_value="Verfassung blockiert system_command: protect_user",
                            ):
                                with patch(
                                    "owner_action.execute_owner_action",
                                ) as exe:
                                    updated = await run_due_owner_autonomy_tasks(
                                        last_runs={},
                                        akku={"plugged": True, "prozent": 90},
                                        on_note=notes.append,
                                        now=datetime(2026, 7, 12, 10, 0, 0),
                                    )
                                    exe.assert_not_called()
                                    return updated

        updated = asyncio.run(_run())
        self.assertIn("daily_isaac_health", updated)
        self.assertTrue(any("BLOCK" in n for n in notes))

    def test_owner_autonomy_status_is_inspectable(self):
        from datetime import datetime
        from owner_autonomy import autonomy_status

        with patch("owner_autonomy.owner_autonomy_enabled", return_value=True):
            with patch("owner_autonomy.is_owner_equivalent_mode", return_value=True):
                status = autonomy_status(
                    last_runs={},
                    akku={"plugged": True, "prozent": 90},
                    now=datetime(2026, 7, 12, 10, 0, 0),
                )
        self.assertTrue(status["enabled"])
        self.assertIn("scheduled", status)
        self.assertIn("due_task_ids", status)
        self.assertGreaterEqual(status["scheduled_count"], 1)
        self.assertIn("next_runs", status)
        self.assertIsNotNone(status.get("next_run"))
        self.assertIn("next_run", status["next_run"])

    def test_owner_autonomy_next_run_respects_window_and_interval(self):
        from datetime import datetime
        from owner_autonomy import next_run_at, next_autonomy_runs, get_scheduled_owner_tasks

        tasks = {t.task_id: t for t in get_scheduled_owner_tasks()}
        health = tasks["daily_isaac_health"]
        # Mitten in der Nacht: nächster Health-Lauf am Morgen (window 6-23)
        night = datetime(2026, 7, 12, 2, 15, 0)
        nxt = next_run_at(health, last_runs={}, now=night)
        self.assertIsNotNone(nxt)
        self.assertEqual(nxt.hour, 6)
        self.assertEqual(nxt.day, 12)

        # Nach Lauf: Intervall 12h → nächster Lauf frühestens 12h später im Fenster
        last = {"daily_isaac_health": "2026-07-12T10:00:00"}
        noon = datetime(2026, 7, 12, 11, 0, 0)
        nxt2 = next_run_at(health, last_runs=last, now=noon)
        self.assertIsNotNone(nxt2)
        self.assertGreaterEqual((nxt2 - datetime.fromisoformat(last["daily_isaac_health"])).total_seconds(), 12 * 3600)

        preview = next_autonomy_runs(last_runs={}, now=night, limit=3)
        self.assertTrue(preview)
        self.assertIn("hours_until", preview[0])

    def test_goal_store_set_list_done(self):
        import tempfile
        from goal_store import GoalStore, parse_goal_command

        with tempfile.TemporaryDirectory() as tmp:
            store = GoalStore(path=Path(tmp) / "goals.json")
            g = store.add_owner_goal("Autonomie ausbauen", source="explicit")
            self.assertEqual(g.status, "active")
            self.assertEqual(len(store.list_goals(status="active")), 1)
            listed = store.format_goal_list()
            self.assertIn("Autonomie ausbauen", listed)
            done = store.set_status(g.id, "done")
            self.assertEqual(done.status, "done")
            self.assertEqual(len(store.list_goals(status="active")), 0)

        self.assertEqual(parse_goal_command("Ziel: Lerne Python")["op"], "set")
        self.assertEqual(parse_goal_command("ziele")["op"], "list")
        self.assertIsNone(parse_goal_command("Was ist 2+2?"))

    def test_goal_intent_routing_and_handler(self):
        import tempfile
        from goal_store import reset_goal_store_for_tests
        from isaac_core import detect_intent, Intent, IsaacKernel

        self.assertEqual(detect_intent("Ziel: Gerät stabil halten"), Intent.GOAL_SET)
        self.assertEqual(detect_intent("ziele"), Intent.GOAL_LIST)
        self.assertEqual(detect_intent("Was ist 2+2?"), Intent.CHAT)

        with tempfile.TemporaryDirectory() as tmp:
            store = reset_goal_store_for_tests(Path(tmp) / "g.json")
            kernel = object.__new__(IsaacKernel)
            msg = kernel._handle_goal("Ziel: Isaac soll Steffens Ziele verfolgen")
            self.assertIn("Gespeichert", msg)
            self.assertEqual(len(store.list_goals(status="active")), 1)
            listed = kernel._handle_goal_list()
            self.assertIn("Steffens Ziele", listed)
            # Normal chat must not be classified as goal
            self.assertEqual(detect_intent("Erkläre mir das Wetter als Motiv"), Intent.CHAT)

    def test_motivation_creates_subgoal_and_ranks(self):
        import tempfile
        from goal_store import reset_goal_store_for_tests
        from motivation import (
            ensure_subgoals_for_active_goals,
            pick_motivation_decision,
            rank_motivation_decisions,
        )
        from decision_trace import TracePhase

        self.assertEqual(TracePhase.MOTIVATION.value, "motivation")

        with tempfile.TemporaryDirectory() as tmp:
            store = reset_goal_store_for_tests(Path(tmp) / "g.json")
            store.add_owner_goal("Autonomie verbessern", priority=0.9)
            created = ensure_subgoals_for_active_goals(store)
            self.assertEqual(len(created), 1)
            # second call idempotent
            self.assertEqual(len(ensure_subgoals_for_active_goals(store)), 0)
            dec = pick_motivation_decision(store)
            self.assertIsNotNone(dec)
            self.assertEqual(dec.goal_title, "Autonomie verbessern")
            self.assertGreater(dec.score, 0)
            ranked = rank_motivation_decisions(store, limit=5)
            self.assertEqual(len(ranked), 1)

    def test_motivation_cycle_enqueues_task_without_tools_by_default(self):
        import tempfile
        from goal_store import reset_goal_store_for_tests
        from motivation import run_goal_motivation_cycle
        from executor import get_executor, TaskStatus
        from decision_trace import TracePhase

        notes: list[str] = []

        async def _run():
            with tempfile.TemporaryDirectory() as tmp:
                store = reset_goal_store_for_tests(Path(tmp) / "g.json")
                store.add_owner_goal("Stabilen Kernel halten", priority=0.8)
                with patch("motivation.goal_autonomy_enabled", return_value=True):
                    return await run_goal_motivation_cycle(
                        on_note=notes.append,
                        submit_tasks=False,
                        store=store,
                    )

        result = asyncio.run(_run())
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["tasks"]), 1)
        self.assertTrue(any("Goal-Autonomie" in n for n in notes))
        task = get_executor().get_task(result["tasks"][0])
        self.assertIsNotNone(task)
        self.assertFalse(task.strategy.allow_tools)
        phases = [e.phase for e in task.decision_trace.entries]
        self.assertIn(TracePhase.MOTIVATION, phases)

    def test_background_goal_autonomy_cycle_invokes_motivation(self):
        from background_loop import BackgroundLoop

        bg = BackgroundLoop()

        async def _run():
            with patch(
                "motivation.run_goal_motivation_cycle",
                return_value={"ok": True, "tasks": ["abc"], "subgoals_created": 1},
            ) as mock_run:
                await bg._goal_autonomy_zyklus()
            return mock_run

        mock_run = asyncio.run(_run())
        mock_run.assert_awaited_once()
        self.assertEqual(bg.state.goal_autonomy_ticks, 1)

    def test_background_mission_cycle_invokes_mission_tick(self):
        from background_loop import BackgroundLoop

        bg = BackgroundLoop()

        async def _run():
            with patch(
                "execution_contract.run_mission_tick",
                return_value={"ok": True, "advanced": [{"id": "m1"}], "active": 1},
            ) as mock_run:
                await bg._mission_zyklus()
            return mock_run

        mock_run = asyncio.run(_run())
        mock_run.assert_awaited_once()
        self.assertEqual(bg.state.mission_ticks, 1)

    def test_ensemble_intent_and_model_panel(self):
        from isaac_core import detect_intent, Intent
        from openrouter_ensemble import get_ensemble_models, ensemble_free_only

        self.assertEqual(detect_intent("ensemble: Was ist Photosynthese?"), Intent.ENSEMBLE)
        self.assertEqual(detect_intent("vergleiche: 2+2"), Intent.ENSEMBLE)
        self.assertEqual(detect_intent("Was ist 2+2?"), Intent.CHAT)
        models = get_ensemble_models(free_only=True, limit=3)
        self.assertTrue(models)
        self.assertTrue(all(":free" in m for m in models))
        self.assertTrue(ensemble_free_only())

    def test_ensemble_auto_only_for_heavy_chat(self):
        from openrouter_ensemble import should_auto_ensemble

        with patch.dict(
            os.environ,
            {
                "ISAAC_ENSEMBLE": "1",
                "ISAAC_ENSEMBLE_AUTO": "1",
                "ISAAC_ENSEMBLE_AUTO_MIN_WORDS": "22",
            },
            clear=False,
        ):
            light = should_auto_ensemble(
                "Was ist 2+2?", intent="chat", interaction_class="NORMAL_CHAT"
            )
            self.assertFalse(light[0])

            heavy = (
                "Erkläre mir bitte ausführlich wie Photosynthese in Pflanzen funktioniert "
                "und welche Rolle Licht Wasser und CO2 dabei spielen."
            )
            ok, reason = should_auto_ensemble(
                heavy, intent="chat", interaction_class="NORMAL_CHAT"
            )
            self.assertTrue(ok)
            self.assertTrue(
                reason.startswith("heavy_words") or reason.startswith("multi_theme"),
                msg=reason,
            )

            # Tools/Status nie auto
            self.assertFalse(
                should_auto_ensemble(
                    heavy, intent="search", interaction_class="NORMAL_CHAT"
                )[0]
            )
            self.assertFalse(
                should_auto_ensemble(
                    heavy, intent="chat", interaction_class="TOOL_REQUEST"
                )[0]
            )
            self.assertFalse(
                should_auto_ensemble(
                    "ensemble: " + heavy, intent="chat", interaction_class="NORMAL_CHAT"
                )[0]
            )

            with patch.dict(os.environ, {"ISAAC_ENSEMBLE_AUTO": "0"}, clear=False):
                self.assertFalse(
                    should_auto_ensemble(
                        heavy, intent="chat", interaction_class="NORMAL_CHAT"
                    )[0]
                )

    def test_ensemble_openrouter_picks_winner_or_judge(self):
        from openrouter_ensemble import ensemble_openrouter, ModelAnswer
        from types import SimpleNamespace

        answers = {
            "model-a:free": "Antwort A mit guten Details zur Frage.",
            "model-b:free": "Antwort B ist kürzer.",
            "model-c:free": "Antwort C mittel.",
        }

        class FakeRelay:
            async def ask(self, prompt, system="", provider=None, task_id="", model_override=None, use_cache=True):
                if "Synthesizer" in prompt or "Kandidat" in prompt:
                    return "Kombinierte beste Antwort aus A und B."
                return answers.get(model_override, f"fallback {model_override}")

        async def _run():
            with patch("relay.get_relay", return_value=FakeRelay()):
                with patch(
                    "openrouter_ensemble.get_logic",
                    return_value=SimpleNamespace(
                        evaluate=lambda antwort, prompt: SimpleNamespace(
                            total=9.0 if "A mit guten" in antwort else 6.0
                        )
                    ),
                ):
                    with patch.dict(
                        os.environ,
                        {
                            "ISAAC_ENSEMBLE_JUDGE": "0",
                            "ISAAC_ENSEMBLE_STAGGER_MS": "0",
                        },
                        clear=False,
                    ):
                        return await ensemble_openrouter(
                            "Testfrage",
                            system="sys",
                            models=list(answers.keys()),
                            task_id="t-ens",
                        )

        result = asyncio.run(_run())
        self.assertEqual(result.mode, "winner")
        self.assertIn("A mit guten", result.final)
        self.assertEqual(result.winner_model, "model-a:free")
        self.assertEqual(len(result.ergebnisse), 3)

    def test_ensemble_kernel_route_uses_module(self):
        from isaac_core import IsaacKernel
        from executor import Task, TaskType, TaskStatus, Strategy
        from decision_trace import DecisionTrace, TracePhase
        from logic import QualityScore

        kernel = object.__new__(IsaacKernel)
        kernel.relay = SimpleNamespace()
        kernel._build_system = lambda *a, **k: "system"

        captured = {}

        class FakeExecutor:
            def create_task(self, **kwargs):
                task = Task(
                    id="ens01",
                    typ=kwargs.get("typ", TaskType.AGGREGATE),
                    prompt=kwargs.get("prompt", ""),
                    beschreibung=kwargs.get("beschreibung", ""),
                    provider=kwargs.get("provider"),
                    system_prompt=kwargs.get("system_prompt", ""),
                    sudo_aktiv=kwargs.get("sudo_aktiv", False),
                    strategy=kwargs.get("strategy") or Strategy(allow_tools=False),
                    interaction_class=kwargs.get("interaction_class", ""),
                    classification=kwargs.get("classification"),
                    decision_trace=DecisionTrace(),
                )
                captured["task"] = task
                return task

            def _notify(self, task):
                captured["notified"] = True

        kernel.executor = FakeExecutor()

        async def fake_ensemble(prompt, system="", task_id="ensemble"):
            from openrouter_ensemble import EnsembleResult, ModelAnswer
            return EnsembleResult(
                final="Synthese XY",
                mode="judge",
                winner_model="judge-model",
                ergebnisse=[
                    ModelAnswer(model="a:free", antwort="A", score=7.0),
                    ModelAnswer(model="b:free", antwort="B", score=6.5),
                ],
                dauer_s=1.2,
                metadata={"panel": ["a:free", "b:free"], "free_only": True},
            )

        async def _run():
            with patch("openrouter_ensemble.ensemble_enabled", return_value=True):
                with patch("openrouter_ensemble.ensemble_openrouter", side_effect=fake_ensemble):
                    return await kernel._ensemble_route(
                        "ensemble: Was ist Isaac?",
                        sudo_aktiv=False,
                        emp=SimpleNamespace(),
                        trigger="explicit",
                    )

        text, score = asyncio.run(_run())
        self.assertIn("Synthese XY", text)
        self.assertIn("Ensemble:", text)
        self.assertGreaterEqual(score, 6.5)
        task = captured["task"]
        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertTrue(captured.get("notified"))
        events = [e.event for e in task.decision_trace.entries]
        phases = [e.phase for e in task.decision_trace.entries]
        self.assertIn("ensemble_route", events)
        self.assertIn("ensemble_decision", events)
        self.assertIn(TracePhase.EVALUATION, phases)
        data = task.to_dict()
        self.assertTrue(data["decision_trace"])
        self.assertEqual(data["decision_trace"][-1]["phase"], "evaluation")

    def test_ensemble_auto_route_from_kernel(self):
        from isaac_core import IsaacKernel, Intent
        from low_complexity import ClassificationResult, InteractionClass

        kernel = object.__new__(IsaacKernel)
        heavy = (
            "Erkläre mir bitte ausführlich wie Photosynthese in Pflanzen funktioniert "
            "und welche Rolle Licht Wasser und CO2 dabei spielen."
        )
        called = {}

        async def fake_ens(user_input, sudo_aktiv, emp, **kwargs):
            called["input"] = user_input
            called["trigger"] = kwargs.get("trigger")
            called["reason"] = kwargs.get("trigger_reason")
            return "auto-ensemble-ok", 8.0

        async def _run():
            with patch.dict(
                os.environ,
                {"ISAAC_ENSEMBLE": "1", "ISAAC_ENSEMBLE_AUTO": "1", "ISAAC_ENSEMBLE_AUTO_MIN_WORDS": "20"},
                clear=False,
            ):
                with patch.object(kernel, "_ensemble_route", side_effect=fake_ens):
                    return await kernel._route(
                        heavy,
                        Intent.CHAT,
                        False,
                        SimpleNamespace(),
                        "",
                        InteractionClass.NORMAL_CHAT,
                        ClassificationResult(
                            interaction_class=InteractionClass.NORMAL_CHAT,
                            normalized_text=heavy.lower(),
                            has_question=True,
                            word_count=len(heavy.split()),
                        ),
                    )

        text, score = asyncio.run(_run())
        self.assertEqual(text, "auto-ensemble-ok")
        self.assertEqual(called.get("trigger"), "auto")
        reason = called.get("reason") or ""
        self.assertTrue(
            reason.startswith("heavy_words") or reason.startswith("multi_theme"),
            msg=reason,
        )

    def test_goal_inquiry_mode_and_learning_provenance(self):
        import tempfile
        from goal_store import reset_goal_store_for_tests
        from goal_inquiry import (
            choose_work_mode,
            extract_inquiry_questions,
            record_goal_learning_from_task,
            reset_inquiry_store_for_tests,
            build_goal_prompt,
        )
        from executor import Task, TaskType, TaskStatus
        from logic import QualityScore

        with tempfile.TemporaryDirectory() as tmp:
            store = reset_goal_store_for_tests(Path(tmp) / "g.json")
            inq = reset_inquiry_store_for_tests(Path(tmp) / "i.json")
            goal = store.add_owner_goal("Kernel Stabilität verbessern", priority=0.9)
            sg = store.add_subgoal(goal.id, "Infos sammeln", origin="planner")
            # first attempt → plan; later → work — no blind research tools
            self.assertEqual(choose_work_mode(goal, sg)[2], "plan")
            self.assertFalse(choose_work_mode(goal, sg)[1])
            sg.attempts = 1
            mode = choose_work_mode(goal, sg)
            self.assertEqual(mode[2], "plan")
            self.assertFalse(mode[1])
            sg.attempts = 3
            mode = choose_work_mode(goal, sg)
            self.assertEqual(mode[2], "work")
            self.assertFalse(mode[1])
            # Research-Marker im Titel erzwingt research auch bei attempts=0
            g2 = store.add_owner_goal("Recherchiere Markt für Isaac-Device", priority=0.8)
            s2 = store.add_subgoal(g2.id, "Start", origin="planner")
            self.assertEqual(choose_work_mode(g2, s2)[2], "research")
            self.assertTrue(choose_work_mode(g2, s2)[1])
            prompt = build_goal_prompt(goal, sg, mode="research")
            self.assertIn(goal.id, prompt)
            self.assertIn("Owner-Ziel-ID", prompt)

            qs = extract_inquiry_questions("FRAGE: Welches Budget?\nFRAGE: Welcher Zeitrahmen?")
            self.assertEqual(len(qs), 2)

            task = Task(
                id="tgoal1",
                typ=TaskType.RESEARCH,
                prompt=prompt,
                beschreibung=f"goal:{goal.id}|test",
                retrieved_context={"goal_id": goal.id, "subgoal_id": sg.id},
            )
            task.status = TaskStatus.DONE
            task.antwort = (
                "Markt wächst.\n"
                "FRAGE: Soll Isaac primär Mobile oder Desktop priorisieren?\n"
            )
            task.score = QualityScore(total=7.5)
            out = record_goal_learning_from_task(task)
            self.assertTrue(out.get("ok"))
            self.assertEqual(out.get("goal_id"), goal.id)
            self.assertGreaterEqual(out.get("inquiries", 0), 1)
            self.assertGreaterEqual(out.get("facts", 0), 1)
            # High-score success marks subgoal done
            self.assertEqual(out.get("subgoal_status"), "done")
            self.assertEqual(store.subgoals[sg.id].status, "done")
            open_inq = inq.list_open(goal.id)
            self.assertTrue(open_inq)
            # fact provenance
            from memory import get_memory
            latest = get_memory().get_fact_record(f"goal_latest.{goal.id}")
            self.assertIsNotNone(latest)
            self.assertIn("goal:", str(latest.get("source") or ""))

    def test_goal_success_criteria_and_recovery_cap(self):
        import tempfile
        from goal_store import reset_goal_store_for_tests
        from goal_inquiry import record_goal_learning_from_task, reset_inquiry_store_for_tests
        from executor import Task, TaskType, TaskStatus
        from logic import QualityScore

        with tempfile.TemporaryDirectory() as tmp:
            store = reset_goal_store_for_tests(Path(tmp) / "g.json")
            reset_inquiry_store_for_tests(Path(tmp) / "i.json")
            goal = store.add_owner_goal(
                "Device bauen",
                priority=0.9,
                success_criteria="prototyp fertig getestet",
            )
            sg = store.add_subgoal(goal.id, "Bauen", origin="planner")
            task = Task(
                id="tcrit1",
                typ=TaskType.CHAT,
                prompt="x",
                beschreibung=f"goal:{goal.id}|crit",
                retrieved_context={"goal_id": goal.id, "subgoal_id": sg.id},
            )
            task.status = TaskStatus.DONE
            task.antwort = "Der Prototyp ist fertig und getestet."
            task.score = QualityScore(total=4.0)  # below score gate; criteria still wins
            out = record_goal_learning_from_task(task)
            self.assertTrue(out.get("criteria_hit"))
            self.assertEqual(out.get("subgoal_status"), "done")
            self.assertEqual(store.goals[goal.id].status, "done")

            # Recovery cap: fail once → one recovery; fail again on recovery → no second recovery
            g2 = store.add_owner_goal("Netzwerk fixen", priority=0.7)
            s2 = store.add_subgoal(g2.id, "Diagnose", origin="planner")
            s2.attempts = 1
            store.subgoals[s2.id] = s2
            store.save()
            tfail = Task(
                id="tfail1",
                typ=TaskType.CHAT,
                prompt="x",
                beschreibung=f"goal:{g2.id}|fail",
                retrieved_context={"goal_id": g2.id, "subgoal_id": s2.id},
            )
            tfail.status = TaskStatus.FAILED
            tfail.antwort = "timeout"
            tfail.score = QualityScore(total=1.0)
            out2 = record_goal_learning_from_task(tfail)
            self.assertTrue(out2.get("recovery"))
            self.assertEqual(store.count_active_recovery(g2.id), 1)
            recovery = [
                s
                for s in store.list_subgoals(g2.id, status="active")
                if s.origin == "failure_recovery"
            ][0]
            tfail2 = Task(
                id="tfail2",
                typ=TaskType.CHAT,
                prompt="x",
                beschreibung=f"goal:{g2.id}|fail2",
                retrieved_context={"goal_id": g2.id, "subgoal_id": recovery.id},
            )
            tfail2.status = TaskStatus.FAILED
            tfail2.antwort = "again"
            tfail2.score = QualityScore(total=1.0)
            out3 = record_goal_learning_from_task(tfail2)
            self.assertFalse(out3.get("recovery"))
            self.assertEqual(store.count_active_recovery(g2.id), 1)

    def test_motivation_cooldown_skips_recent_subgoal(self):
        import tempfile
        from goal_store import reset_goal_store_for_tests
        from motivation import rank_motivation_decisions
        from goal_inquiry import reset_inquiry_store_for_tests

        with tempfile.TemporaryDirectory() as tmp:
            store = reset_goal_store_for_tests(Path(tmp) / "g.json")
            reset_inquiry_store_for_tests(Path(tmp) / "i.json")
            goal = store.add_owner_goal("Cooldown testen", priority=0.9)
            sg = store.add_subgoal(goal.id, "Arbeit", origin="planner")
            # freshly enqueued
            import time as _t

            sg.last_enqueued_at = _t.strftime("%Y-%m-%d %H:%M:%S")
            store.subgoals[sg.id] = sg
            store.save()
            with patch.dict(os.environ, {"ISAAC_GOAL_SUBGOAL_COOLDOWN_S": "3600"}):
                ranked = rank_motivation_decisions(store, limit=5)
            self.assertEqual(len(ranked), 0)
            with patch.dict(os.environ, {"ISAAC_GOAL_SUBGOAL_COOLDOWN_S": "0"}):
                ranked2 = rank_motivation_decisions(store, limit=5)
            self.assertEqual(len(ranked2), 1)

    def test_retrieval_includes_active_goals_block(self):
        """Slice 1b: active owner goals appear in build/format retrieval."""
        import tempfile
        from goal_store import reset_goal_store_for_tests
        from goal_inquiry import reset_inquiry_store_for_tests, get_inquiry_store
        from memory import get_memory

        with tempfile.TemporaryDirectory() as tmp:
            store = reset_goal_store_for_tests(Path(tmp) / "g.json")
            reset_inquiry_store_for_tests(Path(tmp) / "i.json")
            goal = store.add_owner_goal(
                "Isaac Kernel härten",
                priority=0.95,
                success_criteria="tests gruen",
            )
            store.add_subgoal(goal.id, "Unittests erweitern", origin="planner")
            get_inquiry_store().add(goal.id, "Welches Modul zuerst?")

            mem = get_memory()
            ctx = mem.build_retrieval_context("Was machen wir als Nächstes?")
            self.assertTrue(ctx.active_goals)
            self.assertEqual(ctx.active_goals[0]["title"], "Isaac Kernel härten")
            self.assertIn("Unittests", ctx.active_goals[0].get("next_subgoal") or "")
            self.assertTrue(ctx.active_goals[0].get("open_inquiries"))
            formatted = mem.format_retrieval_context(ctx)
            self.assertIn("[active_goals]", formatted)
            self.assertIn("Isaac Kernel härten", formatted)
            self.assertIn("open_q:", formatted)

    def test_temporal_goal_latest_beats_progress(self):
        """Phase 3.1: goal_latest ranked above goal_progress; demote on update."""
        from memory import Memory, get_memory

        mem = get_memory()
        gid = "goal_temporal_test_1"
        mem.set_fact(
            f"goal_progress.{gid}.20260101000000",
            "alter stand v1",
            source=f"goal:{gid}",
            confidence=0.8,
        )
        mem.set_fact(
            f"goal_latest.{gid}",
            "aktueller stand v2",
            source=f"goal:{gid}",
            confidence=0.5,
        )
        # Even with lower confidence, latest should rank first for same goal
        ranked = Memory.prioritize_temporal_facts(
            [
                mem.get_fact_record(f"goal_progress.{gid}.20260101000000"),
                mem.get_fact_record(f"goal_latest.{gid}"),
            ],
            limit=5,
        )
        self.assertEqual(ranked[0]["key"], f"goal_latest.{gid}")
        self.assertTrue(
            all(not (r.get("key") or "").startswith(f"goal_progress.{gid}.") for r in ranked)
            or len(ranked) == 1
        )
        # When latest exists, progress for same goal is omitted
        self.assertEqual(len(ranked), 1)

        demoted = mem.supersede_stale_goal_progress(gid, demote_to=0.22)
        self.assertGreaterEqual(demoted, 1)
        prog = mem.get_fact_record(f"goal_progress.{gid}.20260101000000")
        self.assertIsNotNone(prog)
        self.assertLessEqual(float(prog["confidence"]), 0.22 + 1e-6)

        # search path also prefers latest when query hits both values
        hits = mem.search_facts("aktueller alter stand", limit=5)
        keys = [h.get("key") for h in hits]
        if f"goal_latest.{gid}" in keys and f"goal_progress.{gid}.20260101000000" in keys:
            self.assertLess(
                keys.index(f"goal_latest.{gid}"),
                keys.index(f"goal_progress.{gid}.20260101000000"),
            )

    def test_goal_digest_emits_once_per_fingerprint(self):
        """Slice 4: digest emits on change, skips identical fingerprint."""
        import tempfile
        from goal_store import reset_goal_store_for_tests
        from goal_inquiry import reset_inquiry_store_for_tests, get_inquiry_store
        from goal_digest import maybe_emit_digest, compute_fingerprint

        with tempfile.TemporaryDirectory() as tmp:
            store = reset_goal_store_for_tests(Path(tmp) / "g.json")
            inq = reset_inquiry_store_for_tests(Path(tmp) / "i.json")
            path = Path(tmp) / "digest.json"
            goal = store.add_owner_goal("Digest Ziel", priority=0.8)
            store.add_subgoal(goal.id, "Arbeit", origin="planner")
            get_inquiry_store().add(goal.id, "Welche Priorität?")

            fp1, n_g, n_q = compute_fingerprint(goal_store=store, inquiry_store=inq)
            self.assertGreaterEqual(n_g, 1)
            self.assertGreaterEqual(n_q, 1)
            self.assertTrue(fp1.startswith("g="))

            with patch.dict(os.environ, {"ISAAC_GOAL_DIGEST_MIN_INTERVAL_S": "0"}):
                r1 = maybe_emit_digest(
                    path=path,
                    goal_store=store,
                    inquiry_store=inq,
                    now=1000.0,
                )
                self.assertTrue(r1.get("emitted"))
                self.assertIn("[Goal-Digest]", r1.get("text") or "")
                self.assertIn("Digest Ziel", r1.get("text") or "")

                r2 = maybe_emit_digest(
                    path=path,
                    goal_store=store,
                    inquiry_store=inq,
                    now=1001.0,
                )
                self.assertFalse(r2.get("emitted"))
                self.assertEqual(r2.get("reason"), "unchanged")

                # Change inquiry → new fingerprint → emit again
                get_inquiry_store().add(goal.id, "Zweite Frage?")
                r3 = maybe_emit_digest(
                    path=path,
                    goal_store=store,
                    inquiry_store=inq,
                    now=1002.0,
                )
                self.assertTrue(r3.get("emitted"))

    def test_e2_trace_phases_include_evaluation_and_learning(self):
        """Evolution 2.0: DecisionTrace deckt Evaluation und Learning ab."""
        self.assertEqual(TracePhase.EVALUATION.value, "evaluation")
        self.assertEqual(TracePhase.LEARNING.value, "learning")
        trace = DecisionTrace()
        trace.add(TracePhase.EVALUATION, "quality_scored", {"score_total": 7.5})
        trace.add(TracePhase.LEARNING, "procedure_recorded", {"signature": "abc"})
        phases = [e["phase"] for e in trace.to_list()]
        self.assertEqual(phases, ["evaluation", "learning"])
        # Serialisierbar für Audit/Dashboard
        json.dumps(trace.to_list())

    def test_e2_owner_login_probe_has_no_hardcoded_secrets(self):
        """Credentials gehören nicht in den Quellcode."""
        import owner_login_probe as probe

        self.assertEqual(probe.DEFAULT_EMAILS, ())
        self.assertEqual(probe.DEFAULT_PASSWORDS, ())
        src = Path(probe.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "hh88hh88",
            "keinemachtdendrogen",
            "steffenglinka666@",
            "brandschutz5@",
        ):
            self.assertNotIn(forbidden, src)

    def test_e2_procedure_hints_keyword_overlap_boosts(self):
        """Keyword-Overlap hebt passende Procedures an (bounded Mem0/Letta-Muster)."""
        from memory import get_memory
        from tool_runtime import _procedure_hints_for_prompt

        mem = get_memory()
        sig = "e2testoverlap0001"
        mem.upsert_procedure(
            signature=sig,
            task_type="search",
            intent_hint="wetter berlin vorhersage",
            keywords=["wetter", "berlin", "vorhersage", "suche"],
            tools_used=["isaac.search_web"],
            reliability=0.8,
            success_count=3,
            failure_count=0,
            last_status="done",
            degraded=False,
        )
        hints = _procedure_hints_for_prompt("Suche Wetter Berlin Vorhersage heute")
        self.assertIn("isaac.search_web", hints)
        self.assertGreater(hints["isaac.search_web"], 9.0)

    def test_e2_executor_does_not_reclassify_input(self):
        """OpenParallax-Muster: Executor denkt nicht neu (kein classify)."""
        import executor as executor_mod
        import inspect

        source = inspect.getsource(executor_mod)
        # Executor darf klassifizieren weder importieren noch aufrufen.
        self.assertNotIn("classify_interaction_result", source)
        self.assertNotIn("from low_complexity import classify", source)

    def test_e2_constitution_blocks_destructive_shell_without_owner(self):
        from constitution import get_constitution

        blocked = get_constitution().validate_action(
            "system_command",
            {
                "outside_effect": True,
                "audit_logged": True,
                "risk": "high",
                "destructive": True,
                "owner_approved": False,
            },
        )
        self.assertFalse(blocked["allowed"])
        self.assertIn("protect_user", blocked["blocked_by"])

    def test_e2_constitution_blocks_package_without_owner(self):
        from constitution import get_constitution

        blocked = get_constitution().validate_action(
            "modify_config",
            {
                "outside_effect": True,
                "audit_logged": True,
                "risk": "high",
                "owner_approved": False,
            },
        )
        self.assertFalse(blocked["allowed"])
        self.assertIn("no_silent_privilege_escalation", blocked["blocked_by"])

    def test_e2_computer_use_shell_constitution_gate(self):
        from computer_use import _constitution_gate_shell

        with patch("computer_use.is_owner_equivalent_mode", return_value=False):
            with patch("constitution_override.is_owner_equivalent_mode", return_value=False):
                msg = _constitution_gate_shell("rm -rf /tmp/testdir")
        self.assertIsNotNone(msg)
        self.assertIn("Verfassung", msg)

    def test_e2_updater_package_blocked_without_owner(self):
        from updater import apply_package

        with patch("config.is_owner_equivalent_mode", return_value=False):
            with patch("constitution_override.is_owner_equivalent_mode", return_value=False):
                result = apply_package("nonexistent-package.zip")
        self.assertFalse(result.get("ok"))
        self.assertIn("Verfassung", result.get("error", ""))

    def test_e2_tool_runtime_blocks_destructive_shell_tool(self):
        from tool_runtime import constitution_gate_for_tool

        with patch("config.is_owner_equivalent_mode", return_value=False):
            blocked = constitution_gate_for_tool(
                {
                    "kind": "shell",
                    "name": "isaac.run_shell",
                    "identifier": "shell-1",
                },
                "bitte führe aus: rm -rf /tmp/x",
            )
        self.assertIsNotNone(blocked)
        self.assertFalse(blocked.get("ok", True))

    def test_e2_tool_runtime_blocks_package_install_shell_without_owner(self):
        """Konkreter Gap: apt/pip install muss wie destruktive Shell ohne Owner blocken."""
        from tool_runtime import constitution_gate_for_tool

        with patch("config.is_owner_equivalent_mode", return_value=False):
            with patch("constitution_override.is_owner_equivalent_mode", return_value=False):
                blocked = constitution_gate_for_tool(
                    {
                        "kind": "shell",
                        "name": "isaac.run_shell",
                        "identifier": "shell-pkg",
                    },
                    "agent: shell apt install -y htop",
                )
        self.assertIsNotNone(blocked)
        self.assertFalse(blocked.get("ok", True))
        meta = blocked.get("metadata") or {}
        self.assertEqual(meta.get("source"), "constitution")

    def test_e2_destructive_shell_fragments_shared_source(self):
        from constitution_override import DESTRUCTIVE_SHELL_FRAGMENTS, is_destructive_shell_text
        from computer_use import BLOCKED_SHELL_FRAGMENTS, _is_destructive_shell

        self.assertIs(BLOCKED_SHELL_FRAGMENTS, DESTRUCTIVE_SHELL_FRAGMENTS)
        self.assertTrue(is_destructive_shell_text("pip install evilpkg"))
        self.assertTrue(_is_destructive_shell("apt-get install foo"))
        self.assertFalse(is_destructive_shell_text("ls -la workspace"))

    def test_browser_safe_goto_falls_back_wait_until(self):
        """Navigation retries wait_until (no networkidle hang)."""
        import asyncio
        from browser import BrowserManager, KIInstance

        mgr = BrowserManager()
        calls: list[str] = []

        class FakePage:
            async def goto(self, url, wait_until="load", timeout=30000):
                calls.append(wait_until)
                if wait_until == "domcontentloaded":
                    raise TimeoutError("simulated domcontentloaded timeout")
                if wait_until == "load":
                    return None
                raise TimeoutError("should not reach commit")

        asyncio.run(mgr._safe_goto(FakePage(), "https://example.com", timeout=1000))
        self.assertEqual(calls, ["domcontentloaded", "load"])

        dead = KIInstance(id="x", url="https://example.com", name="x")
        dead.aktiv = True
        dead.page = None
        self.assertFalse(BrowserManager._page_alive(dead))

        class ClosedPage:
            def is_closed(self):
                return True

        dead.page = ClosedPage()
        self.assertFalse(BrowserManager._page_alive(dead))

        class OpenPage:
            def is_closed(self):
                return False

        alive = KIInstance(id="y", url="https://example.com", name="y")
        alive.aktiv = True
        alive.page = OpenPage()
        self.assertTrue(BrowserManager._page_alive(alive))

    def test_browser_action_with_retry_once(self):
        import asyncio
        from browser import BrowserManager

        mgr = BrowserManager()
        n = {"c": 0}

        async def flaky():
            n["c"] += 1
            if n["c"] < 2:
                raise RuntimeError("transient")
            return "ok"

        out = asyncio.run(mgr._action_with_retry(flaky, attempts=2, pause_s=0.01))
        self.assertEqual(out, "ok")
        self.assertEqual(n["c"], 2)

    def test_e2_browser_provision_requires_owner(self):
        from browser import BrowserManager

        mgr = BrowserManager()
        with patch("browser.is_owner_equivalent_mode", return_value=False):
            with patch("constitution_override.is_owner_equivalent_mode", return_value=False):
                msg = mgr._constitution_gate_browser(
                    "browser_provision",
                    url="https://console.groq.com/keys",
                    require_owner=True,
                )
        self.assertIsNotNone(msg)
        self.assertIn("Verfassung", msg)

    def test_e2_browser_automation_allowed_with_warning_path(self):
        from constitution import get_constitution

        verdict = get_constitution().validate_action(
            "browser_automation",
            {
                "outside_effect": True,
                "audit_logged": True,
                "risk": "high",
                "owner_approved": False,
            },
        )
        self.assertTrue(verdict["allowed"])
        self.assertIn("high_impact_action", verdict["warnings"])

    def test_e2_credential_access_requires_owner_and_constitution(self):
        from credential_access import require_credential_access

        with patch("credential_access.is_owner_equivalent_mode", return_value=False):
            msg = require_credential_access()
        self.assertIsNotNone(msg)
        self.assertIn("Admin-Modus", msg)

        with patch("credential_access.is_owner_equivalent_mode", return_value=True):
            with patch("constitution_override.is_owner_equivalent_mode", return_value=True):
                self.assertIsNone(require_credential_access())

    def test_e2_browser_login_gate_requires_owner(self):
        from browser import BrowserManager

        mgr = BrowserManager()
        with patch("browser.is_owner_equivalent_mode", return_value=False):
            with patch("constitution_override.is_owner_equivalent_mode", return_value=False):
                msg = mgr._constitution_gate_browser(
                    "browser_login",
                    url="https://example.com/login",
                    require_owner=True,
                )
        self.assertIsNotNone(msg)
        self.assertIn("Verfassung", msg)

    def test_e2_privilege_constitution_blocks_file_delete_without_owner(self):
        from privilege import get_gate, isaac_ctx
        from config import get_config

        cfg = get_config()
        prev = cfg.privilege_mode
        cfg.privilege_mode = "user"
        try:
            with patch("privilege.is_owner_equivalent_mode", return_value=False):
                with patch("constitution_override.is_owner_equivalent_mode", return_value=False):
                    # Bypass confirmation queue: force constitution path via STEFFEN-less ISAAC
                    # after confirmation would block first; patch confirmation to allow.
                    with patch("security_policy.get_confirmation_policy") as gcp:
                        pol = MagicMock()
                        pol.analyze.return_value = MagicMock(allowed=True, reason="ok")
                        gcp.return_value = pol
                        ok, reason = get_gate().authorize(
                            "file_delete",
                            isaac_ctx("Test", "Auditierter Datei-Löschversuch mit Außenwirkung"),
                        )
            self.assertFalse(ok)
            self.assertIn("Verfassung", reason)
        finally:
            cfg.privilege_mode = prev

    def test_e2_privilege_steffen_modify_config_still_allowed(self):
        from privilege import get_gate, steffen_ctx
        from config import get_config

        cfg = get_config()
        prev = cfg.privilege_mode
        cfg.privilege_mode = "user"
        try:
            with patch("privilege.is_owner_equivalent_mode", return_value=False):
                ok, reason = get_gate().authorize(
                    "modify_config",
                    steffen_ctx("Owner patch apply"),
                )
            self.assertTrue(ok, reason)
        finally:
            cfg.privilege_mode = prev

    def test_e2_constitution_boundary_inventory_complete(self):
        """Alle kanonischen Boundaries sind inventarisiert und validierbar."""
        from constitution import get_constitution
        from constitution_override import CONSTITUTION_BOUNDARIES, critical_action_gate

        self.assertGreaterEqual(len(CONSTITUTION_BOUNDARIES), 10)
        modules = {row["module"] for row in CONSTITUTION_BOUNDARIES}
        for required in (
            "computer_use", "tool_runtime", "updater", "browser",
            "credential_access", "privilege", "file_access", "isaac_core",
        ):
            self.assertIn(required, modules)

        c = get_constitution()
        # Owner-pflichtige Actions blocken ohne Freigabe
        for action in (
            "modify_config",
            "browser_provision",
            "browser_login",
            "credential_access",
        ):
            verdict = c.validate_action(
                action,
                {
                    "outside_effect": True,
                    "audit_logged": True,
                    "risk": "high",
                    "owner_approved": False,
                },
            )
            self.assertFalse(verdict["allowed"], action)

        # critical_action_gate Helper konsistent
        with patch("constitution_override.is_owner_equivalent_mode", return_value=False):
            with patch("config.is_owner_equivalent_mode", return_value=False):
                blocked = critical_action_gate(
                    "credential_access",
                    source="test",
                    owner_approved=False,
                )
                allowed = critical_action_gate(
                    "credential_access",
                    source="test",
                    owner_approved=True,
                )
        self.assertIsNotNone(blocked)
        self.assertIsNone(allowed)


if __name__ == '__main__':
    unittest.main()
