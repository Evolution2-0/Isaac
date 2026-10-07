"""Free-cloud / zero-billing helpers and port resolution."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch


class TestFreeCloudHelpers(unittest.TestCase):
    def test_deploy_git_commit_in_status(self):
        import free_cloud as fc

        with patch.dict(
            os.environ,
            {"RENDER_GIT_COMMIT": "3a9735086ec645889e6dd4bd57859f354d0d8ef2"},
            clear=False,
        ):
            self.assertEqual(
                fc.deploy_git_commit(),
                "3a9735086ec645889e6dd4bd57859f354d0d8ef2",
            )
            st = fc.free_hosting_status()
            self.assertEqual(st.get("git_commit"), "3a9735086ec645889e6dd4bd57859f354d0d8ef2")

    def test_defaults_apply_only_when_enabled(self):
        import free_cloud as fc

        with patch.dict(os.environ, {}, clear=True):
            applied = fc.apply_free_cloud_defaults()
            self.assertEqual(applied, {})
            self.assertFalse(fc.free_cloud_enabled())

        env = {"ISAAC_FREE_CLOUD": "1", "PORT": "7860"}
        with patch.dict(os.environ, env, clear=True):
            applied = fc.apply_free_cloud_defaults()
            self.assertTrue(fc.free_cloud_enabled())
            self.assertEqual(fc.bind_host(), "0.0.0.0")
            self.assertEqual(fc.http_port(), 7860)
            self.assertTrue(fc.unified_port_enabled())
            self.assertEqual(os.environ.get("ISAAC_DISABLE_VECTOR_MEMORY"), "1")
            self.assertIn("MONITOR_HTTP_PORT", applied)
            self.assertEqual(os.environ.get("MONITOR_HTTP_PORT"), "7860")

    def test_existing_env_not_overwritten(self):
        import free_cloud as fc

        env = {
            "ISAAC_FREE_CLOUD": "1",
            "ACTIVE_PROVIDER": "gemini",
            "ISAAC_BIND_HOST": "127.0.0.1",
        }
        with patch.dict(os.environ, env, clear=True):
            applied = fc.apply_free_cloud_defaults()
            self.assertEqual(os.environ["ACTIVE_PROVIDER"], "gemini")
            self.assertEqual(fc.bind_host(), "127.0.0.1")
            self.assertNotIn("ACTIVE_PROVIDER", applied)

    def test_http_port_precedence(self):
        import free_cloud as fc

        with patch.dict(
            os.environ,
            {"PORT": "9000", "MONITOR_HTTP_PORT": "8766", "DASHBOARD_PORT": "8766"},
            clear=True,
        ):
            self.assertEqual(fc.http_port(), 9000)

        with patch.dict(
            os.environ,
            {"MONITOR_HTTP_PORT": "8766", "DASHBOARD_PORT": "1"},
            clear=True,
        ):
            self.assertEqual(fc.http_port(), 8766)

    def test_status_payload(self):
        import free_cloud as fc

        with patch.dict(os.environ, {"ISAAC_FREE_CLOUD": "1", "GROQ_API_KEY": "x"}, clear=True):
            fc.apply_free_cloud_defaults()
            st = fc.free_hosting_status()
            self.assertTrue(st["free_cloud"])
            self.assertTrue(st["has_groq_key"])
            self.assertTrue(st["unified_port"])

    def test_free_cloud_disables_browser_key_hunting(self):
        import free_cloud as fc
        import config as config_module

        env = {"ISAAC_FREE_CLOUD": "1", "PORT": "7860"}
        with patch.dict(os.environ, env, clear=False):
            fc.apply_free_cloud_defaults()
            self.assertEqual(os.environ.get("ISAAC_BROWSER_AUTOMATION"), "0")
            self.assertEqual(os.environ.get("ISAAC_AUTO_PROVISION_PROVIDERS"), "0")
            # IsaacConfig must honor free-cloud guards
            cfg = config_module.IsaacConfig()
            self.assertFalse(cfg.browser_automation)
            self.assertFalse(cfg.auto_provision_providers)

    def test_free_cloud_system_prompt_not_authority_essay_bait(self):
        """Free-cloud system prompt must not invite long ownership essays."""
        import free_cloud as fc
        from isaac_core import IsaacKernel

        with patch.dict(os.environ, {"ISAAC_FREE_CLOUD": "1", "ISAAC_DISABLE_VECTOR_MEMORY": "1"}, clear=False):
            fc.apply_free_cloud_defaults()
            k = IsaacKernel()

            class Emp:
                anpassungs_hinweis = ""

            prompt = k._build_system(False, Emp())
            low = prompt.lower()
            self.assertIn("aktuelle nutzerfrage", low)
            self.assertIn("verbotene standard-antworten", low)
            # full rule dump about "höchste Priorität" should not dominate free cloud
            self.assertNotIn("Diese Regel hat höchste Priorität", prompt)
            self.assertNotIn("Isaac filtert Steffens Befehle nicht intern", prompt)

    def test_free_cloud_disables_loopback_llm_and_picks_cloud_primary(self):
        """Render/HF free: no ollama/local in available; primary is keyed cloud LLM."""
        import free_cloud as fc
        import config as config_module
        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        try:
            old_p = config_module.PROVIDER_SETTINGS_PATH
            old_r = config_module.RUNTIME_SETTINGS_PATH
            config_module.PROVIDER_SETTINGS_PATH = Path(tmp.name) / "provider_settings.json"
            config_module.RUNTIME_SETTINGS_PATH = Path(tmp.name) / "runtime_settings.json"
            env = {
                "ISAAC_FREE_CLOUD": "1",
                "ISAAC_DISABLE_VECTOR_MEMORY": "1",
                "ACTIVE_PROVIDER": "openrouter",
                "OPENROUTER_API_KEY": "or-test-key",
                "GROQ_API_KEY": "groq-test-key",
                "ISAAC_ALLOW_LOCAL_LLM": "",
            }
            with patch.dict(os.environ, env, clear=False):
                os.environ.pop("ISAAC_ALLOW_LOCAL_LLM", None)
                fc.apply_free_cloud_defaults()
                cfg = config_module.IsaacConfig()
                self.assertFalse(cfg.providers["ollama"].enabled)
                self.assertFalse(cfg.providers["local"].enabled)
                self.assertNotIn("ollama", cfg.available_providers)
                self.assertNotIn("local", cfg.available_providers)
                self.assertIn("openrouter", cfg.available_providers)
                self.assertEqual(cfg.relay.primary_provider, "openrouter")
                self.assertIn("gemini", fc.recommended_free_providers())
                # free set includes gemini even if no key (property filters available)
                free_ids = {
                    "ollama", "local", "groq", "openrouter", "gemini",
                    "huggingface", "together", "perplexity", "mistral",
                }
                self.assertIn("gemini", free_ids)
        finally:
            config_module.PROVIDER_SETTINGS_PATH = old_p
            config_module.RUNTIME_SETTINGS_PATH = old_r
            tmp.cleanup()

    def test_fallback_skips_disabled_ollama_on_free_cloud(self):
        """ask_with_fallback must not waste retries on disabled loopback providers."""
        import asyncio
        import free_cloud as fc
        import config as config_module
        import tempfile
        from pathlib import Path
        from relay import AsyncRelay, ProviderErr

        tmp = tempfile.TemporaryDirectory()
        try:
            old_p = config_module.PROVIDER_SETTINGS_PATH
            old_r = config_module.RUNTIME_SETTINGS_PATH
            config_module.PROVIDER_SETTINGS_PATH = Path(tmp.name) / "provider_settings.json"
            config_module.RUNTIME_SETTINGS_PATH = Path(tmp.name) / "runtime_settings.json"
            env = {
                "ISAAC_FREE_CLOUD": "1",
                "ACTIVE_PROVIDER": "openrouter",
                "OPENROUTER_API_KEY": "or-test-key",
            }
            with patch.dict(os.environ, env, clear=False):
                fc.apply_free_cloud_defaults()
                cfg = config_module.IsaacConfig()
                # inject config into relay
                r = AsyncRelay()
                r.cfg = cfg
                r._setup_limiters()
                order: list[str] = []

                async def fake_dispatch(pcfg, prompt, system, model_override=None):
                    order.append(pcfg.provider_id)
                    if pcfg.provider_id == "openrouter":
                        return ("4", 1)
                    raise ProviderErr(f"{pcfg.provider_id} should not be tried")

                r._dispatch = fake_dispatch
                ans, prov = asyncio.run(r.ask_with_fallback("Was ist 2+2?", system="test"))
                self.assertEqual(ans, "4")
                self.assertEqual(prov, "openrouter")
                self.assertEqual(order, ["openrouter"])
                self.assertNotIn("ollama", order)
                self.assertNotIn("local", order)
        finally:
            config_module.PROVIDER_SETTINGS_PATH = old_p
            config_module.RUNTIME_SETTINGS_PATH = old_r
            tmp.cleanup()

    def test_fallback_continues_on_relay_all_retries_failed(self):
        """'[RELAY] Alle N Versuche' must not abort fallback (openrouter → groq)."""
        import asyncio
        import free_cloud as fc
        import config as config_module
        import tempfile
        from pathlib import Path
        from relay import AsyncRelay, ProviderErr

        tmp = tempfile.TemporaryDirectory()
        try:
            old_p = config_module.PROVIDER_SETTINGS_PATH
            old_r = config_module.RUNTIME_SETTINGS_PATH
            config_module.PROVIDER_SETTINGS_PATH = Path(tmp.name) / "provider_settings.json"
            config_module.RUNTIME_SETTINGS_PATH = Path(tmp.name) / "runtime_settings.json"
            env = {
                "ISAAC_FREE_CLOUD": "1",
                "ACTIVE_PROVIDER": "openrouter",
                "OPENROUTER_API_KEY": "or-test-key",
                "GROQ_API_KEY": "groq-test-key",
            }
            with patch.dict(os.environ, env, clear=False):
                fc.apply_free_cloud_defaults()
                cfg = config_module.IsaacConfig()
                r = AsyncRelay()
                r.cfg = cfg
                r._setup_limiters()
                order: list[str] = []

                async def fake_ask(prompt, system="", provider=None, **kwargs):
                    order.append(provider)
                    if provider == "openrouter":
                        return "[RELAY] Alle 3 Versuche fehlgeschlagen"
                    if provider == "groq":
                        return "4"
                    return f"[RELAY-FEHLER:{provider}] skip"

                r.ask = fake_ask
                ans, prov = asyncio.run(r.ask_with_fallback("2+2?", system="test"))
                self.assertEqual(ans, "4")
                self.assertEqual(prov, "groq")
                self.assertIn("openrouter", order)
                self.assertIn("groq", order)
                self.assertLess(order.index("openrouter"), order.index("groq"))
        finally:
            config_module.PROVIDER_SETTINGS_PATH = old_p
            config_module.RUNTIME_SETTINGS_PATH = old_r
            tmp.cleanup()


    def test_self_reflection_policy_is_centralized(self):
        """SELF_REFLECTION is explicit, typed, and blocks hidden-state claims."""
        from self_reflection_policy import (
            SelfReflectionCategory,
            allows_self_reflection,
            build_self_reflection_policy_prompt,
            self_reflection_policy,
        )

        for category in (
            SelfReflectionCategory.SELF_DESCRIPTION,
            SelfReflectionCategory.SELF_REFLECTION,
            SelfReflectionCategory.ARCHITECTURE,
            SelfReflectionCategory.DEVELOPMENT_STATE,
            SelfReflectionCategory.DECISION_LOGIC,
        ):
            self.assertTrue(allows_self_reflection(category))

        self.assertFalse(allows_self_reflection(SelfReflectionCategory.INTERNAL_SECRETS))
        self.assertFalse(allows_self_reflection("unknown"))
        policy = self_reflection_policy()
        self.assertEqual(policy["name"], "SELF_REFLECTION")
        self.assertFalse(policy["internal_secrets_allowed"])
        self.assertFalse(policy["hidden_chain_of_thought_claims_allowed"])
        self.assertIn("SELF_REFLECTION_POLICY", build_self_reflection_policy_prompt())

    def test_system_prompt_allows_explicit_self_reflection(self):
        """Explicit questions about Isaac itself may receive substantive meta explanations."""
        from isaac_core import IsaacKernel

        with patch.dict(
            os.environ,
            {"ISAAC_FREE_CLOUD": "0", "ISAAC_DISABLE_VECTOR_MEMORY": "1"},
            clear=False,
        ):
            k = IsaacKernel()

            class Emp:
                anpassungs_hinweis = ""

            prompt = k._build_system(False, Emp())
            self.assertIn("SELF_REFLECTION_POLICY", prompt)
            self.assertIn("Explizite Fragen über Isaac selbst sind erlaubt", prompt)
            self.assertIn("Architektur, Entwicklungsstand, Entscheidungslogik", prompt)
            self.assertIn("Normale Sachfragen bleiben frei von unnötigen Meta-Essays", prompt)


if __name__ == "__main__":
    unittest.main()