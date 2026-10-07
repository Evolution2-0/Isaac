from __future__ import annotations
import json
import os

os.environ["ISAAC_PRIVILEGE_MODE"] = "user"

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config as config_module


class TestProviderConfiguration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.old_provider_path = config_module.PROVIDER_SETTINGS_PATH
        self.old_runtime_path = config_module.RUNTIME_SETTINGS_PATH
        config_module.PROVIDER_SETTINGS_PATH = self.tmp_path / "provider_settings.json"
        config_module.RUNTIME_SETTINGS_PATH = self.tmp_path / "runtime_settings.json"

    def tearDown(self):
        config_module.PROVIDER_SETTINGS_PATH = self.old_provider_path
        config_module.RUNTIME_SETTINGS_PATH = self.old_runtime_path
        self.tmp.cleanup()

    def test_default_provider_is_ollama_qwen25_15b(self):
        with patch.dict("os.environ", {}, clear=True):
            defaults = config_module._provider_defaults_from_env()
        self.assertIn("ollama", defaults)
        self.assertEqual(defaults["ollama"].model, "qwen2.5:1.5b")
        self.assertEqual(defaults["ollama"].provider_type, "ollama")

    def test_gemini_defaults_ai_studio_model_and_key_fallback(self):
        env = {
            "GEMINI_API_KEY": "test-gemini-key",
            "GOOGLE_API_KEY": "",
        }
        with patch.dict("os.environ", env, clear=False):
            os.environ.pop("GEMINI_MODEL", None)
            defaults = config_module._provider_defaults_from_env()
        gemini = defaults["gemini"]
        self.assertEqual(gemini.provider_type, "gemini")
        self.assertEqual(gemini.model, "gemini-flash-lite-latest")
        self.assertEqual(gemini.api_key, "test-gemini-key")
        self.assertIn("generativelanguage.googleapis.com", gemini.base_url)

    def test_local_openai_compat_provider_default_no_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            defaults = config_module._provider_defaults_from_env()
        self.assertIn("local", defaults)
        local = defaults["local"]
        self.assertEqual(local.provider_type, "openai_compat")
        self.assertIn("127.0.0.1", local.base_url)
        self.assertIn("/v1/chat/completions", local.base_url)
        self.assertEqual(local.api_key, "")
        self.assertTrue(local.available)
        self.assertTrue(config_module.allows_missing_api_key(local))

        cloud = config_module.ProviderConfig(
            provider_id="openai",
            display_name="OpenAI",
            provider_type="openai_compat",
            api_key="",
            base_url="https://api.openai.com/v1/chat/completions",
            model="gpt-4o-mini",
            rpm=60,
            tpm=60_000,
            enabled=True,
        )
        self.assertFalse(cloud.available)
        self.assertFalse(config_module.allows_missing_api_key(cloud))

    def test_local_llm_env_overrides(self):
        env = {
            "LOCAL_LLM_BASE_URL": "http://localhost:8080/v1/chat/completions",
            "LOCAL_LLM_MODEL": "my-gguf",
            "LOCAL_LLM_API_KEY": "sk-local",
            "LOCAL_LLM_ENABLED": "1",
        }
        with patch.dict("os.environ", env, clear=False):
            defaults = config_module._provider_defaults_from_env()
        local = defaults["local"]
        self.assertEqual(local.base_url, "http://localhost:8080/v1/chat/completions")
        self.assertEqual(local.model, "my-gguf")
        self.assertEqual(local.api_key, "sk-local")
        self.assertTrue(local.available)

    def test_local_llm_vllm_and_sglang_shaped_urls(self):
        """Documented vLLM (~8000) / SGLang (~30000) recipes: full completions path, no key."""
        cases = (
            (
                "http://127.0.0.1:8000/v1/chat/completions",
                "my-vllm-model",
            ),
            (
                "http://127.0.0.1:30000/v1/chat/completions",
                "my-sglang-model",
            ),
        )
        for base_url, model in cases:
            with self.subTest(base_url=base_url):
                env = {
                    "LOCAL_LLM_BASE_URL": base_url,
                    "LOCAL_LLM_MODEL": model,
                    "LOCAL_LLM_API_KEY": "",
                    "LOCAL_LLM_ENABLED": "1",
                }
                with patch.dict("os.environ", env, clear=False):
                    defaults = config_module._provider_defaults_from_env()
                local = defaults["local"]
                self.assertEqual(local.provider_type, "openai_compat")
                self.assertEqual(local.base_url, base_url)
                self.assertTrue(local.base_url.endswith("/v1/chat/completions"))
                self.assertEqual(local.model, model)
                self.assertEqual(local.api_key, "")
                self.assertTrue(local.available)
                self.assertTrue(config_module.allows_missing_api_key(local))
                self.assertTrue(config_module.is_loopback_provider(local))

    def test_upsert_default_keeps_single_default(self):
        cfg = config_module.IsaacConfig()
        provider = cfg.upsert_provider(
            {
                "provider_id": "custom-openai",
                "display_name": "Custom OpenAI",
                "provider_type": "openai_compat",
                "base_url": "https://example.invalid/v1/chat/completions",
                "model": "gpt-test",
                "enabled": True,
                "is_default": True,
                "timeout": 30,
                "rpm": 10,
                "tpm": 20000,
            }
        )
        self.assertEqual(provider["provider_id"], "custom-openai")
        defaults = [p.provider_id for p in cfg.providers.values() if p.is_default]
        self.assertEqual(defaults, ["custom-openai"])
        self.assertEqual(cfg.relay.primary_provider, "custom-openai")

    def test_active_provider_env_sets_primary_provider(self):
        with patch.dict("os.environ", {"ACTIVE_PROVIDER": "groq", "GROQ_API_KEY": "test-groq"}, clear=False):
            cfg = config_module.IsaacConfig()
        self.assertEqual(cfg.relay.primary_provider, "groq")
        self.assertTrue(cfg.providers["groq"].is_default)

    def test_gemini_is_free_provider(self):
        cfg = config_module.IsaacConfig()
        # free_providers filters by available; ensure set membership via free_only allowlist path
        cfg.free_only_providers = True
        cfg.providers["gemini"].api_key = "g-test"
        self.assertIn("gemini", cfg.free_providers)
        self.assertTrue(cfg.is_provider_allowed("gemini"))

    def test_provider_settings_file_created_on_upsert(self):
        cfg = config_module.IsaacConfig()
        cfg.upsert_provider(
            {
                "provider_id": "persist-test",
                "display_name": "Persist Test",
                "provider_type": "ollama",
                "base_url": "http://127.0.0.1:11434/api/chat",
                "model": "qwen2.5:1.5b",
                "enabled": True,
                "is_default": False,
            }
        )
        self.assertTrue(config_module.PROVIDER_SETTINGS_PATH.exists())
        payload = json.loads(config_module.PROVIDER_SETTINGS_PATH.read_text(encoding="utf-8"))
        self.assertIn("providers", payload)
        self.assertTrue(any(p.get("provider_id") == "persist-test" for p in payload["providers"]))

    def test_provider_persistence_roundtrip(self):
        cfg = config_module.IsaacConfig()
        cfg.upsert_provider(
            {
                "provider_id": "backup-ollama",
                "display_name": "Backup Ollama",
                "provider_type": "ollama",
                "base_url": "http://127.0.0.1:11434/api/chat",
                "model": "qwen2.5:1.5b",
                "enabled": True,
                "is_default": False,
                "timeout": 120,
                "rpm": 500,
                "tpm": 300000,
            }
        )

        reloaded = config_module.IsaacConfig()
        self.assertIn("backup-ollama", reloaded.providers)
        self.assertEqual(reloaded.providers["backup-ollama"].provider_type, "ollama")
        self.assertEqual(reloaded.providers["backup-ollama"].model, "qwen2.5:1.5b")


if __name__ == "__main__":
    unittest.main()
