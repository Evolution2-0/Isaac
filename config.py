from __future__ import annotations

import json
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Optional

try:
    from dotenv import load_dotenv

    load_dotenv()
    # Gerätespezifisch (gitignored): Admin-Modus, S8-Runtime, lokale Keys
    # override=False: Unittests können user-Modus erzwingen; .env.local füllt nur Lücken
    load_dotenv(Path(__file__).parent / ".env.local", override=False)
except Exception:
    pass

BASE_DIR = Path(__file__).parent

# Vercel's deployment filesystem is read-only. Keep local behavior unchanged
# while routing ephemeral serverless state to the writable /tmp tree.
if os.getenv("VERCEL"):
    RUNTIME_DIR = Path(os.getenv("TMPDIR", "/tmp")) / "isaac"
    DATA_DIR = RUNTIME_DIR / "data"
    LOG_DIR = RUNTIME_DIR / "logs"
    WORKSPACE = RUNTIME_DIR / "workspace"
else:
    RUNTIME_DIR = BASE_DIR
    DATA_DIR = BASE_DIR / "data"
    LOG_DIR = BASE_DIR / "logs"
    WORKSPACE = BASE_DIR / "workspace"
DB_PATH = DATA_DIR / "isaac.db"
AUDIT_PATH = DATA_DIR / "audit.jsonl"
RUNTIME_SETTINGS_PATH = DATA_DIR / "runtime_settings.json"
PROVIDER_SETTINGS_PATH = DATA_DIR / "provider_settings.json"

for d in [DATA_DIR, LOG_DIR, WORKSPACE]:
    d.mkdir(parents=True, exist_ok=True)


class Level:
    STEFFEN = 100
    ISAAC = 70
    TASK = 40
    GUEST = 10


@dataclass
class ProviderConfig:
    provider_id: str
    display_name: str
    provider_type: str
    api_key: str
    base_url: str
    model: str
    rpm: int
    tpm: int
    timeout: int = 60
    enabled: bool = True
    is_default: bool = False
    api_key_ref: str = ""
    extra_headers: dict[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.provider_id

    @property
    def default_model(self) -> str:
        return self.model

    @default_model.setter
    def default_model(self, value: str):
        self.model = value

    @property
    def available(self) -> bool:
        if not self.enabled:
            return False
        if allows_missing_api_key(self):
            return True
        return bool(self.api_key)


def allows_missing_api_key(cfg: "ProviderConfig") -> bool:
    """Ollama und lokale OpenAI-Compat-Endpunkte brauchen keinen API-Key."""
    ptype = (cfg.provider_type or "").lower().strip()
    if ptype in {"ollama", "local_ollama"}:
        return True
    pid = (cfg.provider_id or "").lower().strip()
    if pid in {"local", "local_llm", "local-openai", "local_openai"}:
        return True
    if ptype in {"openai_compat", "openai-compatible"}:
        host = _url_host(cfg.base_url)
        if host in {"127.0.0.1", "localhost", "::1"}:
            return True
    return False


def is_loopback_provider(cfg: "ProviderConfig") -> bool:
    """True for Ollama/local endpoints bound to loopback (unusable on free PaaS)."""
    ptype = (cfg.provider_type or "").lower().strip()
    pid = (cfg.provider_id or "").lower().strip()
    host = _url_host(cfg.base_url)
    if host in {"127.0.0.1", "localhost", "::1"}:
        return True
    if ptype in {"ollama", "local_ollama"} and not host:
        return True
    if pid in {"ollama", "local", "local_llm", "local-openai", "local_openai"} and not host:
        return True
    return False


def _url_host(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(raw if "://" in raw else f"http://{raw}")
        return (parsed.hostname or "").lower()
    except Exception:
        return ""


def _normalize_provider_id(value: str) -> str:
    raw = (value or "").strip().lower()
    normalized = "".join(ch if (ch.isalnum() or ch in {"-", "_"}) else "-" for ch in raw)
    while "--" in normalized:
        normalized = normalized.replace("--", "-")
    return normalized.strip("-_")


def _default_provider_ref(provider_id: str) -> str:
    return f"provider_{provider_id}_api_key"


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except Exception:
        return default


def _provider_defaults_from_env() -> dict[str, ProviderConfig]:
    return {
        "ollama": ProviderConfig(
            provider_id="ollama",
            display_name="Local Ollama",
            provider_type="ollama",
            api_key="",
            base_url=(os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")) + "/api/chat",
            model=os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b"),
            rpm=999,
            tpm=999_999,
            timeout=_int(os.getenv("OLLAMA_TIMEOUT", "180"), 180),
            enabled=True,
            is_default=True,
        ),
        # OpenAI-kompatibel lokal (LM Studio, vLLM, llama.cpp server, LocalAI, …)
        "local": ProviderConfig(
            provider_id="local",
            display_name="Local OpenAI API",
            provider_type="openai_compat",
            api_key=os.getenv("LOCAL_LLM_API_KEY", ""),
            base_url=os.getenv(
                "LOCAL_LLM_BASE_URL",
                "http://127.0.0.1:1234/v1/chat/completions",
            ).rstrip("/"),
            model=os.getenv("LOCAL_LLM_MODEL", "local-model"),
            rpm=999,
            tpm=999_999,
            timeout=_int(os.getenv("LOCAL_LLM_TIMEOUT", "180"), 180),
            enabled=_bool(os.getenv("LOCAL_LLM_ENABLED", "1"), True),
            is_default=False,
        ),
        "openrouter": ProviderConfig(
            provider_id="openrouter",
            display_name="OpenRouter",
            provider_type="openai_compat",
            api_key=os.getenv("OPENROUTER_API_KEY", ""),
            base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions"),
            model=os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.2-3b-instruct:free"),
            rpm=20,
            tpm=50_000,
            timeout=60,
        ),
        "groq": ProviderConfig(
            provider_id="groq",
            display_name="Groq",
            provider_type="openai_compat",
            api_key=os.getenv("GROQ_API_KEY", ""),
            base_url=os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1/chat/completions"),
            model=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
            rpm=30,
            tpm=6_000,
            timeout=60,
        ),
        "openai": ProviderConfig(
            provider_id="openai",
            display_name="OpenAI",
            provider_type="openai_compat",
            api_key=os.getenv("OPENAI_API_KEY", ""),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1/chat/completions"),
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            rpm=20,
            tpm=40_000,
            timeout=60,
        ),
        "mistral": ProviderConfig(
            provider_id="mistral",
            display_name="Mistral",
            provider_type="openai_compat",
            api_key=os.getenv("MISTRAL_API_KEY", ""),
            base_url=os.getenv("MISTRAL_BASE_URL", "https://api.mistral.ai/v1/chat/completions"),
            model=os.getenv("MISTRAL_MODEL", "open-mistral-7b"),
            rpm=20,
            tpm=50_000,
            timeout=60,
        ),
        "together": ProviderConfig(
            provider_id="together",
            display_name="Together",
            provider_type="openai_compat",
            api_key=os.getenv("TOGETHER_API_KEY", ""),
            base_url=os.getenv("TOGETHER_BASE_URL", "https://api.together.xyz/v1/chat/completions"),
            model=os.getenv("TOGETHER_MODEL", "meta-llama/Llama-3-8b-chat-hf"),
            rpm=60,
            tpm=100_000,
            timeout=60,
        ),
        "perplexity": ProviderConfig(
            provider_id="perplexity",
            display_name="Perplexity",
            provider_type="openai_compat",
            api_key=os.getenv("PERPLEXITY_API_KEY", ""),
            base_url=os.getenv("PERPLEXITY_BASE_URL", "https://api.perplexity.ai/chat/completions"),
            model=os.getenv("PERPLEXITY_MODEL", "llama-3.1-sonar-small-128k-online"),
            rpm=20,
            tpm=50_000,
            timeout=60,
        ),
        "anthropic": ProviderConfig(
            provider_id="anthropic",
            display_name="Anthropic",
            provider_type="anthropic",
            api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            base_url=os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1/messages"),
            model=os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
            rpm=50,
            tpm=100_000,
            timeout=60,
        ),
        "gemini": ProviderConfig(
            provider_id="gemini",
            display_name="Gemini (AI Studio)",
            provider_type="gemini",
            # GOOGLE_API_KEY (AI Studio) oder GEMINI_API_KEY — beide akzeptiert
            api_key=(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or ""),
            base_url=os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/models"),
            # Lite-latest: stabil für AI-Studio-Keys; 2.5-flash oft gesperrt, 3-flash braucht hohes Token-Budget
            model=os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest"),
            rpm=15,
            tpm=32_000,
            timeout=60,
        ),
        "cohere": ProviderConfig(
            provider_id="cohere",
            display_name="Cohere",
            provider_type="cohere",
            api_key=os.getenv("COHERE_API_KEY", ""),
            base_url=os.getenv("COHERE_BASE_URL", "https://api.cohere.ai/v2/chat"),
            model=os.getenv("COHERE_MODEL", "command-r"),
            rpm=20,
            tpm=100_000,
            timeout=60,
        ),
        "huggingface": ProviderConfig(
            provider_id="huggingface",
            display_name="HuggingFace",
            provider_type="huggingface",
            api_key=os.getenv("HF_API_KEY", ""),
            base_url=os.getenv("HF_BASE_URL", "https://api-inference.huggingface.co/models"),
            model=os.getenv("HF_MODEL", "mistralai/Mistral-7B-Instruct-v0.3"),
            rpm=10,
            tpm=20_000,
            timeout=90,
        ),
    }


def _provider_to_payload(cfg: ProviderConfig) -> dict[str, Any]:
    return {
        "provider_id": cfg.provider_id,
        "display_name": cfg.display_name,
        "provider_type": cfg.provider_type,
        "base_url": cfg.base_url,
        "model": cfg.model,
        "enabled": cfg.enabled,
        "is_default": cfg.is_default,
        "timeout": cfg.timeout,
        "rpm": cfg.rpm,
        "tpm": cfg.tpm,
        "extra_headers": dict(cfg.extra_headers or {}),
        "api_key_ref": cfg.api_key_ref,
    }


@dataclass
class LogicConfig:
    min_quality_score: float = 5.5
    max_followup_rounds: int = 1
    max_task_iterations: int = 8
    min_word_count: int = 80
    min_topic_coverage: float = 0.6
    weight_length: float = 0.20
    weight_coverage: float = 0.35
    weight_specificity: float = 0.25
    weight_coherence: float = 0.20
    instance_switch_score: float = 7.2
    default_followup_mode: str = "targeted"
    causal_neg_threshold: float = 0.70
    qualia_penalty: float = 2.0


@dataclass
class BrowserConfig:
    max_instances: int = 12
    page_timeout: int = 30
    headless: bool = True
    auto_restart: bool = True
    max_errors_per_instance: int = 5
    slowmo: int = 0


@dataclass
class MonitorConfig:
    host: str = (os.getenv("ISAAC_BIND_HOST", "") or "").strip() or "localhost"
    port: int = int(os.getenv("MONITOR_PORT", "8765"))
    http_port: int = int(os.getenv("DASHBOARD_PORT", "8766"))
    push_interval: float = 0.5
    max_connections: int = 10
    task_history_limit: int = 500
    log_history_limit: int = 1000


@dataclass
class MemoryConfig:
    max_working_memory: int = 50
    max_facts: int = 10_000
    fact_similarity_threshold: float = 0.85


def _active_provider_from_env() -> str:
    return _normalize_provider_id(os.getenv("ACTIVE_PROVIDER", "ollama")) or "ollama"


@dataclass
class RelayConfig:
    primary_provider: str = field(default_factory=_active_provider_from_env)
    min_interval: float = float(os.getenv("MIN_REQUEST_INTERVAL", "0.15"))
    max_retries: int = int(os.getenv("MAX_RETRIES", "3"))
    cache_ttl: int = 30
    max_context_chars: int = int(os.getenv("MAX_CONTEXT_CHARS", "3000"))
    strip_user_pii: bool = True


@dataclass
class IdeenConfig:
    scan_interval: int = 420
    max_queue: int = 20
    min_notify_score: float = 6.5
    interesse_topics: list = None

    def __post_init__(self):
        if self.interesse_topics is None:
            self.interesse_topics = [
                "KI-Sprachprotokolle zwischen Modellen",
                "ARM-GPU Beschleunigung llama.cpp",
                "autonome Agenten Architektur",
                "Python async optimierung",
                "Rust WASM performance",
            ]


@dataclass
class IsaacConfig:
    providers: dict[str, ProviderConfig] = field(default_factory=_provider_defaults_from_env)
    logic: LogicConfig = field(default_factory=LogicConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    relay: RelayConfig = field(default_factory=RelayConfig)
    ideen: IdeenConfig = field(default_factory=IdeenConfig)
    filesystem_full_access: bool = True
    filesystem_access_tier: str = "full"  # workspace | project | full
    browser_automation: bool = True
    browser_external_sites: bool = True
    auto_provision_providers: bool = True
    auto_provision_all_providers: bool = True
    computer_use_enabled: bool = False
    computer_use_live: bool = True
    free_only_providers: bool = False
    multi_tool_mode: bool = True
    style_mode: str = os.getenv("ISAAC_STYLE_MODE", "light_sarcastic")
    owner_name: str = os.getenv("ISAAC_OWNER", "Steffen")
    privilege_mode: str = os.getenv("ISAAC_PRIVILEGE_MODE", "user")  # "user" | "admin"

    def __post_init__(self):
        self._load_provider_settings()
        self._load_runtime_settings()
        self._apply_owner_equivalent_defaults()
        self._apply_env_runtime_overrides()
        self._apply_free_cloud_runtime_guards()
        self._normalize_defaults()

    def _apply_env_runtime_overrides(self):
        """Explizite Env-Flags überschreiben runtime_settings (z. B. Free-Cloud)."""
        mapping = {
            "ISAAC_BROWSER_AUTOMATION": "browser_automation",
            "ISAAC_BROWSER_EXTERNAL_SITES": "browser_external_sites",
            "ISAAC_AUTO_PROVISION_PROVIDERS": "auto_provision_providers",
            "ISAAC_AUTO_PROVISION_ALL_PROVIDERS": "auto_provision_all_providers",
            "ISAAC_COMPUTER_USE_ENABLED": "computer_use_enabled",
            "ISAAC_COMPUTER_USE_LIVE": "computer_use_live",
            "ISAAC_FREE_ONLY_PROVIDERS": "free_only_providers",
        }
        for env_key, attr in mapping.items():
            raw = os.getenv(env_key)
            if raw is None or str(raw).strip() == "":
                continue
            setattr(self, attr, _bool(raw, getattr(self, attr)))

    def _apply_free_cloud_runtime_guards(self):
        """Free PaaS: kein Browser-Key-Bootstrap, kein Admin-Override."""
        try:
            from free_cloud import free_cloud_enabled
            if not free_cloud_enabled():
                return
        except Exception:
            return
        self.browser_automation = False
        self.browser_external_sites = False
        self.auto_provision_providers = False
        self.auto_provision_all_providers = False
        self.computer_use_enabled = False
        # user-Mode: verhindert erneutes Force-Enable von Browser im Owner-Pfad
        if (self.privilege_mode or "").lower() == "admin":
            # Free public host: kein stiller Admin
            self.privilege_mode = "user"

        # Loopback-LLMs (Ollama / local OpenAI) sind auf Free-PaaS nicht erreichbar.
        # Ohne Disable landen sie in available_providers und blockieren den Fallback.
        allow_local = _bool(os.getenv("ISAAC_ALLOW_LOCAL_LLM"), False)
        if not allow_local:
            for pid, prov in self.providers.items():
                if is_loopback_provider(prov):
                    prov.enabled = False

        # free_only: Cloud-Demo soll nicht versehentlich Paid-Only-Primary ohne Key halten
        if not self.free_only_providers:
            # Env/runtime may already set it; free cloud defaults to free-only when unset
            if os.getenv("ISAAC_FREE_ONLY_PROVIDERS") is None:
                self.free_only_providers = True

        # Faster provider failover online (free models often 429 / empty)
        if self.relay.max_retries > 2:
            self.relay.max_retries = 2

    def _apply_owner_equivalent_defaults(self):
        """Admin-Modus = Owner-Äquivalenz auf vertrauenswürdigen Geräten."""
        if not is_owner_equivalent_mode(self):
            return
        # Free public hosts must not force browser/key hunting (breaks normal chat)
        try:
            from free_cloud import free_cloud_enabled
            if free_cloud_enabled():
                return
        except Exception:
            pass
        self.filesystem_access_tier = "full"
        self.filesystem_full_access = True
        self.computer_use_enabled = True
        self.computer_use_live = True
        self.browser_automation = True
        self.browser_external_sites = True

    @property
    def available_providers(self) -> list[str]:
        return [n for n, p in self.providers.items() if p.available and self.is_provider_allowed(n)]

    def get_provider(self, name: str) -> Optional[ProviderConfig]:
        if not name:
            return None
        return self.providers.get(name)

    @property
    def free_providers(self) -> list[str]:
        free = {
            "ollama",
            "local",
            "groq",
            "openrouter",
            "gemini",  # AI Studio free quota — documented free-cloud option
            "huggingface",
            "together",
            "perplexity",
            "mistral",
        }
        return [p.provider_id for p in self.providers.values() if p.provider_id in free and p.available]

    def is_provider_allowed(self, name: str) -> bool:
        if not name:
            return False
        provider = self.providers.get(name)
        if not provider:
            return False
        if self.free_only_providers and name not in self.free_providers:
            return False
        return bool(provider.enabled)

    def runtime_settings(self) -> dict[str, Any]:
        return {
            "filesystem_full_access": bool(self.filesystem_full_access),
            "filesystem_access_tier": str(self.filesystem_access_tier or "full"),
            "browser_automation": bool(self.browser_automation),
            "browser_external_sites": bool(self.browser_external_sites),
            "auto_provision_providers": bool(self.auto_provision_providers),
            "auto_provision_all_providers": bool(self.auto_provision_all_providers),
            "computer_use_enabled": bool(self.computer_use_enabled),
            "computer_use_live": bool(self.computer_use_live),
            "free_only_providers": bool(self.free_only_providers),
            "multi_tool_mode": bool(self.multi_tool_mode),
            "style_mode": self.style_mode,
        }

    def update_runtime_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        changed: list[str] = []
        for key, value in (patch or {}).items():
            if not hasattr(self, key):
                continue
            current = getattr(self, key)
            if key == "style_mode":
                mode = str(value or "").strip().lower()
                if mode not in {"professional", "light_sarcastic"}:
                    continue
                if current != mode:
                    setattr(self, key, mode)
                    changed.append(key)
                continue
            if isinstance(current, bool):
                normalized = bool(value)
                if current != normalized:
                    setattr(self, key, normalized)
                    changed.append(key)
                continue
            if key == "filesystem_access_tier":
                tier = str(value or "").strip().lower()
                if tier not in {"workspace", "project", "full"}:
                    continue
                if current != tier:
                    setattr(self, key, tier)
                    changed.append(key)
        if changed:
            self._save_runtime_settings()
        return {"changed": changed, "settings": self.runtime_settings()}

    def list_provider_configs(self) -> list[dict[str, Any]]:
        return [self._provider_public_view(self.providers[k]) for k in sorted(self.providers.keys())]

    def upsert_provider(self, payload: dict[str, Any]) -> dict[str, Any]:
        provider_id = _normalize_provider_id(payload.get("provider_id") or payload.get("name") or "")
        if not provider_id:
            raise ValueError("provider_id fehlt")

        current = self.providers.get(provider_id)
        provider_type = (payload.get("provider_type") or (current.provider_type if current else "openai_compat")).strip().lower()
        if provider_type not in {"ollama", "openai_compat", "anthropic", "gemini", "cohere", "huggingface"}:
            raise ValueError("provider_type ungültig")

        display_name = (payload.get("display_name") or payload.get("name") or (current.display_name if current else provider_id)).strip()
        base_url = (payload.get("base_url") or (current.base_url if current else "")).strip()
        model = (payload.get("model") or (current.model if current else "")).strip()
        timeout = _int(payload.get("timeout"), current.timeout if current else 60)
        rpm = _int(payload.get("rpm"), current.rpm if current else 60)
        tpm = _int(payload.get("tpm"), current.tpm if current else 60_000)
        enabled = _bool(payload.get("enabled"), current.enabled if current else True)
        is_default = _bool(payload.get("is_default"), current.is_default if current else False)

        if not base_url:
            raise ValueError("base_url fehlt")
        if not model:
            raise ValueError("model fehlt")

        api_key = (payload.get("api_key") or "").strip()
        clear_api_key = _bool(payload.get("clear_api_key"), False)
        api_key_ref = (payload.get("api_key_ref") or (current.api_key_ref if current else "")).strip()
        if api_key and not api_key_ref:
            api_key_ref = _default_provider_ref(provider_id)

        extra_headers = payload.get("extra_headers")
        if not isinstance(extra_headers, dict):
            extra_headers = dict(current.extra_headers if current else {})

        cfg = ProviderConfig(
            provider_id=provider_id,
            display_name=display_name,
            provider_type=provider_type,
            api_key=current.api_key if current else "",
            base_url=base_url,
            model=model,
            timeout=timeout,
            rpm=rpm,
            tpm=tpm,
            enabled=enabled,
            is_default=is_default,
            api_key_ref=api_key_ref,
            extra_headers={str(k): str(v) for k, v in extra_headers.items()},
        )

        if clear_api_key:
            cfg.api_key = ""
            cfg.api_key_ref = ""

        if api_key:
            self._save_secret(cfg.api_key_ref, api_key)
            cfg.api_key = api_key
        elif cfg.api_key_ref:
            cfg.api_key = self._load_secret(cfg.api_key_ref)

        self.providers[provider_id] = cfg
        self._normalize_defaults(preferred_default=provider_id if is_default else None)
        self._save_provider_settings()
        return self._provider_public_view(self.providers[provider_id])

    def delete_provider(self, provider_id: str) -> bool:
        pid = _normalize_provider_id(provider_id)
        if not pid:
            return False
        if pid not in self.providers:
            return False
        was_default = self.providers[pid].is_default
        del self.providers[pid]
        self._normalize_defaults(preferred_default=None if not was_default else "ollama")
        self._save_provider_settings()
        return True

    def set_default_provider(self, provider_id: str) -> dict[str, Any]:
        pid = _normalize_provider_id(provider_id)
        if pid not in self.providers:
            raise ValueError("Unbekannter Provider")
        self._normalize_defaults(preferred_default=pid)
        self._save_provider_settings()
        return self._provider_public_view(self.providers[pid])

    def _provider_public_view(self, cfg: ProviderConfig) -> dict[str, Any]:
        key_mask = ""
        if cfg.api_key:
            if len(cfg.api_key) <= 8:
                key_mask = "*" * len(cfg.api_key)
            else:
                key_mask = cfg.api_key[:4] + "*" * max(3, len(cfg.api_key) - 8) + cfg.api_key[-4:]
        return {
            "provider_id": cfg.provider_id,
            "display_name": cfg.display_name,
            "provider_type": cfg.provider_type,
            "base_url": cfg.base_url,
            "model": cfg.model,
            "enabled": cfg.enabled,
            "is_default": cfg.is_default,
            "timeout": cfg.timeout,
            "rpm": cfg.rpm,
            "tpm": cfg.tpm,
            "extra_headers": dict(cfg.extra_headers or {}),
            "has_api_key": bool(cfg.api_key),
            "api_key_masked": key_mask,
        }

    def _normalize_defaults(self, preferred_default: Optional[str] = None):
        if not self.providers:
            defaults = _provider_defaults_from_env()
            self.providers.update(defaults)

        free_allow = {
            "ollama", "local", "groq", "openrouter", "gemini",
            "huggingface", "together", "perplexity", "mistral",
        }

        def _enabled(pid: str) -> bool:
            cfg = self.providers.get(pid)
            return bool(cfg and cfg.enabled)

        def _runtime_ready(pid: str) -> bool:
            """Enabled + free_only allow + configured enough to call."""
            cfg = self.providers.get(pid)
            if not cfg or not cfg.enabled:
                return False
            if self.free_only_providers and pid not in free_allow:
                return False
            return bool(cfg.available)

        def _first_runtime_ready() -> str:
            try:
                from free_cloud import recommended_free_providers
                candidates = list(recommended_free_providers())
            except Exception:
                candidates = ["groq", "gemini", "openrouter"]
            candidates.extend(["ollama", "local"])
            seen: set[str] = set()
            for pid in candidates:
                if pid in seen:
                    continue
                seen.add(pid)
                if pid in self.providers and _runtime_ready(pid):
                    return pid
            for pid in self.providers:
                if _runtime_ready(pid):
                    return pid
            return ""

        # 1) Explicit preferred (upsert / set_default) — honor even without key
        requested = _normalize_provider_id(preferred_default or "")
        if requested in self.providers and _enabled(requested):
            valid_default = requested
        else:
            valid_default = ""

        # 2) ACTIVE_PROVIDER env — replace if disabled/unusable loopback (free cloud)
        if not valid_default:
            env_default = _normalize_provider_id(os.getenv("ACTIVE_PROVIDER", ""))
            if env_default in self.providers and _enabled(env_default):
                if _runtime_ready(env_default):
                    valid_default = env_default
                else:
                    valid_default = _first_runtime_ready() or env_default
            elif env_default in self.providers and not _enabled(env_default):
                # e.g. ACTIVE_PROVIDER=ollama on free cloud after loopback disable
                valid_default = _first_runtime_ready()

        # 3) Existing is_default flag
        if not valid_default:
            for pid, cfg in self.providers.items():
                if cfg.is_default and _enabled(pid):
                    valid_default = pid if _runtime_ready(pid) else (_first_runtime_ready() or pid)
                    break

        # 4) Auto runtime-ready, then any enabled
        if not valid_default:
            valid_default = _first_runtime_ready()
        if not valid_default:
            for pid, cfg in self.providers.items():
                if cfg.enabled:
                    valid_default = pid
                    break
            if not valid_default:
                valid_default = next(iter(self.providers.keys()))

        for pid, cfg in self.providers.items():
            cfg.is_default = pid == valid_default

        self.relay.primary_provider = valid_default

    def _load_provider_settings(self):
        if not PROVIDER_SETTINGS_PATH.exists():
            self._resolve_provider_secrets()
            self._normalize_defaults()
            return

        try:
            raw = json.loads(PROVIDER_SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            self._resolve_provider_secrets()
            self._normalize_defaults()
            return

        entries = raw.get("providers") if isinstance(raw, dict) else None
        if not isinstance(entries, list):
            self._resolve_provider_secrets()
            self._normalize_defaults()
            return

        loaded: dict[str, ProviderConfig] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            provider_id = _normalize_provider_id(entry.get("provider_id") or entry.get("name") or "")
            if not provider_id:
                continue
            base = self.providers.get(provider_id)
            try:
                cfg = ProviderConfig(
                    provider_id=provider_id,
                    display_name=(entry.get("display_name") or entry.get("name") or (base.display_name if base else provider_id)).strip(),
                    provider_type=(entry.get("provider_type") or (base.provider_type if base else "openai_compat")).strip().lower(),
                    api_key="",
                    base_url=(entry.get("base_url") or (base.base_url if base else "")).strip(),
                    model=(entry.get("model") or entry.get("default_model") or (base.model if base else "")).strip(),
                    rpm=_int(entry.get("rpm"), base.rpm if base else 60),
                    tpm=_int(entry.get("tpm"), base.tpm if base else 60_000),
                    timeout=_int(entry.get("timeout"), base.timeout if base else 60),
                    enabled=_bool(entry.get("enabled"), base.enabled if base else True),
                    is_default=_bool(entry.get("is_default"), False),
                    api_key_ref=(entry.get("api_key_ref") or (base.api_key_ref if base else "")).strip(),
                    extra_headers=entry.get("extra_headers") if isinstance(entry.get("extra_headers"), dict) else dict(base.extra_headers if base else {}),
                )
            except Exception:
                continue

            legacy_key = (entry.get("api_key") or "").strip()
            if legacy_key:
                if not cfg.api_key_ref:
                    cfg.api_key_ref = _default_provider_ref(provider_id)
                self._save_secret(cfg.api_key_ref, legacy_key)

            cfg.api_key = self._load_secret(cfg.api_key_ref) if cfg.api_key_ref else ""
            loaded[provider_id] = cfg

        if loaded:
            self.providers.update(loaded)

        self._resolve_provider_secrets()

        primary = _normalize_provider_id((raw.get("primary_provider") or "") if isinstance(raw, dict) else "")
        self._normalize_defaults(preferred_default=primary or None)

    def _resolve_provider_secrets(self):
        for provider in self.providers.values():
            if provider.api_key:
                continue
            if provider.api_key_ref:
                provider.api_key = self._load_secret(provider.api_key_ref)
            elif provider.api_key and not provider.api_key_ref:
                provider.api_key_ref = _default_provider_ref(provider.provider_id)

    def _save_provider_settings(self):
        payload = {
            "primary_provider": self.relay.primary_provider,
            "providers": [_provider_to_payload(self.providers[k]) for k in sorted(self.providers.keys())],
        }
        PROVIDER_SETTINGS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_runtime_settings(self):
        if not RUNTIME_SETTINGS_PATH.exists():
            return
        try:
            raw = json.loads(RUNTIME_SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return
        self.update_runtime_settings(raw.get("settings") or {})

    def _save_runtime_settings(self):
        payload: dict[str, Any] = {"settings": self.runtime_settings()}
        if RUNTIME_SETTINGS_PATH.exists():
            try:
                existing = json.loads(RUNTIME_SETTINGS_PATH.read_text(encoding="utf-8"))
                if isinstance(existing.get("owner_autonomy"), dict):
                    payload["owner_autonomy"] = existing["owner_autonomy"]
            except Exception:
                pass
        RUNTIME_SETTINGS_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _load_secret(ref: str) -> str:
        if not ref:
            return ""
        try:
            from secrets_store import get_secrets_store

            return (get_secrets_store().get_secret(ref) or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _save_secret(ref: str, value: str):
        if not ref:
            return
        try:
            from secrets_store import get_secrets_store

            get_secrets_store().set_secret(ref, value.strip(), kind="provider_api_key")
        except Exception:
            return


def is_owner_equivalent_mode(cfg: Optional["IsaacConfig"] = None) -> bool:
    """True wenn ISAAC_PRIVILEGE_MODE=admin (volle Geräteäquivalenz für Owner)."""
    target = cfg if cfg is not None else (_config if _config is not None else None)
    if target is None:
        return str(os.getenv("ISAAC_PRIVILEGE_MODE", "user") or "user").strip().lower() == "admin"
    return str(getattr(target, "privilege_mode", "user") or "user").strip().lower() == "admin"


_config: Optional[IsaacConfig] = None


def get_config() -> IsaacConfig:
    global _config
    if _config is None:
        _config = IsaacConfig()
    return _config
