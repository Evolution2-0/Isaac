"""
Isaac – Browser-Automation (Playwright)
=========================================
Kernstück des Multi-Instanz-Modus.

Jede KI-Instanz = eine URL = ein isolierter Browser-Context.
Isaac navigiert zu den URLs, loggt sich ein, schickt Nachrichten
und liest Antworten aus — wie ein Mensch, aber automatisiert.

Architektur:
  BrowserManager        → verwaltet alle Instanzen
  KIInstance            → eine KI-Chat-Session (ein Browser-Context)
  LoginProfile          → gespeicherte Login-Daten pro Domain
  SiteAdapter           → URL-spezifische Selektoren (erweiterbar)

Instanzen sind vollständig isoliert:
  - Eigene Cookies / Sessions
  - Eigene LocalStorage
  - Kein Cross-Contamination zwischen Instanzen

Auto-Login:
  - Credentials in encrypted local store
  - Pro Domain: Username, Passwort, Login-URL, Selektoren
  - Wird beim Start automatisch durchgeführt
"""
from __future__ import annotations

import asyncio
import json
import time
import logging
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional, Callable

from config  import Level, get_config, DATA_DIR, is_owner_equivalent_mode
from audit   import AuditLog
from privilege import get_gate, isaac_ctx
from secrets_store import get_secrets_store

log = logging.getLogger("Isaac.Browser")

# Credentials-Datei (verschlüsselt gespeichert)
CREDS_PATH = DATA_DIR / "browser_creds.json"

# Browser-Flows zum automatischen API-Key-Provisioning
PROVIDER_PROVISION_SPECS: dict[str, dict[str, Any]] = {
    "openrouter": {
        "secret_ref": "OPENROUTER_API_KEY",
        "keys_url": "https://openrouter.ai/settings/keys",
        "instance_id": "openrouter",
        "label": "OpenRouter",
        "token_prefixes": ("sk-or-", "sk-"),
        "create_labels": [
            "Create Key", "New Key", "Create API Key", "Generate Key", "New Token",
        ],
        "confirm_labels": ["Create", "Generate", "Save", "Submit"],
    },
    "groq": {
        "secret_ref": "GROQ_API_KEY",
        "keys_url": "https://console.groq.com/keys",
        "instance_id": "groq",
        "label": "Groq",
        "token_prefixes": ("gsk_",),
        "create_labels": [
            "Create API Key", "Create Key", "New API Key", "Generate API Key", "New Key",
        ],
        "confirm_labels": ["Create", "Generate", "Save", "Submit", "Create API Key"],
    },
    "openai": {
        "secret_ref": "OPENAI_API_KEY",
        "keys_url": "https://platform.openai.com/api-keys",
        "instance_id": "openai",
        "label": "OpenAI",
        "token_prefixes": ("sk-",),
        "create_labels": [
            "Create new secret key", "Create API key", "Create key", "+ Create new secret key",
        ],
        "confirm_labels": ["Create secret key", "Create", "Save", "Submit"],
    },
    "mistral": {
        "secret_ref": "MISTRAL_API_KEY",
        "keys_url": "https://console.mistral.ai/api-keys/",
        "instance_id": "mistral",
        "label": "Mistral",
        "token_prefixes": (),
        "create_labels": ["Create API key", "Create key", "New API key", "Generate key"],
        "confirm_labels": ["Create", "Generate", "Save", "Submit"],
    },
    "together": {
        "secret_ref": "TOGETHER_API_KEY",
        "keys_url": "https://api.together.xyz/settings/api-keys",
        "instance_id": "together",
        "label": "Together",
        "token_prefixes": (),
        "create_labels": ["Create API key", "Create key", "New API key", "Add key"],
        "confirm_labels": ["Create", "Generate", "Save", "Submit"],
    },
    "perplexity": {
        "secret_ref": "PERPLEXITY_API_KEY",
        "keys_url": "https://www.perplexity.ai/settings/api",
        "instance_id": "perplexity",
        "label": "Perplexity",
        "token_prefixes": ("pplx-",),
        "create_labels": ["Generate", "Create API key", "Create key", "New API key"],
        "confirm_labels": ["Generate", "Create", "Save", "Submit"],
    },
    "huggingface": {
        "secret_ref": "HF_API_KEY",
        "keys_url": "https://huggingface.co/settings/tokens",
        "instance_id": "huggingface",
        "label": "HuggingFace",
        "token_prefixes": ("hf_",),
        "create_labels": ["New token", "Create new token", "Add new token", "Create token"],
        "confirm_labels": ["Create token", "Create", "Save", "Submit"],
    },
    "anthropic": {
        "secret_ref": "ANTHROPIC_API_KEY",
        "keys_url": "https://console.anthropic.com/settings/keys",
        "instance_id": "anthropic",
        "label": "Anthropic",
        "token_prefixes": ("sk-ant-",),
        "create_labels": ["Create Key", "Create API key", "New Key", "Generate Key"],
        "confirm_labels": ["Create", "Generate", "Save", "Submit"],
    },
    "gemini": {
        "secret_ref": "GOOGLE_API_KEY",
        "keys_url": "https://aistudio.google.com/app/apikey",
        "instance_id": "gemini",
        "label": "Gemini",
        "token_prefixes": ("AIza",),
        "create_labels": ["Create API key", "Get API key", "Create key"],
        "confirm_labels": ["Create", "Create API key", "Save", "Submit"],
    },
    "cohere": {
        "secret_ref": "COHERE_API_KEY",
        "keys_url": "https://dashboard.cohere.com/api-keys",
        "instance_id": "cohere",
        "label": "Cohere",
        "token_prefixes": (),
        "create_labels": ["Create API key", "Create key", "Generate API key", "New API key"],
        "confirm_labels": ["Create", "Generate", "Save", "Submit"],
    },
}


def provisionable_provider_ids() -> tuple[str, ...]:
    return tuple(PROVIDER_PROVISION_SPECS.keys())


# ── Login-Profil ───────────────────────────────────────────────────────────────
@dataclass
class LoginProfile:
    domain:        str
    login_url:     str
    username:      str
    password:      str   # Wird verschlüsselt gespeichert
    user_selector: str   = "input[type='email'], input[name='email'], input[name='username']"
    pass_selector: str   = "input[type='password']"
    submit_selector: str = "button[type='submit'], input[type='submit']"
    logged_in_check: str = ""  # CSS-Selector der nach Login sichtbar ist
    extra_steps:   list  = field(default_factory=list)  # Für 2FA etc.


# ── Site-Adapter ──────────────────────────────────────────────────────────────
# Bekannte KI-Chat-Seiten: URL-Pattern → Chat-Selektoren
SITE_ADAPTERS: dict[str, dict] = {
    "chat.openai.com": {
        "input":    "#prompt-textarea",
        "send":     "button[data-testid='send-button']",
        "response": "[data-message-author-role='assistant']:last-child",
        "wait_ms":  3000,
    },
    "claude.ai": {
        "input":    "div[contenteditable='true']",
        "send":     "button[aria-label='Send message']",
        "response": "[data-is-streaming='false']:last-child",
        "wait_ms":  5000,
    },
    "gemini.google.com": {
        "input":    "rich-textarea",
        "send":     "button.send-button",
        "response": "model-response:last-child .response-content",
        "wait_ms":  4000,
    },
    "copilot.microsoft.com": {
        "input":    "#userInput",
        "send":     "button#sendButton",
        "response": ".ac-textBlock:last-child",
        "wait_ms":  5000,
    },
    "huggingface.co": {
        "input":    "textarea",
        "send":     "button[type='submit']",
        "response": ".message.bot:last-child",
        "wait_ms":  4000,
    },
    "poe.com": {
        "input":    "textarea[class*='GrowingTextArea']",
        "send":     "button[class*='SendButton']",
        "response": "[class*='Message_humanMessageBubble']:last-child",
        "wait_ms":  4000,
    },
    "you.com": {
        "input":    "textarea[placeholder*='Ask']",
        "send":     "button[aria-label='Submit']",
        "response": "[data-testid='youchat-text']:last-child",
        "wait_ms":  5000,
    },
    "perplexity.ai": {
        "input":    "textarea",
        "send":     "button[aria-label='Submit']",
        "response": ".prose:last-child",
        "wait_ms":  6000,
    },
    "mistral.ai": {
        "input":    "textarea",
        "send":     "button[type='submit']",
        "response": ".assistant-message:last-child",
        "wait_ms":  4000,
    },
    "groq.com": {
        "input":    "textarea",
        "send":     "button[aria-label='Send']",
        "response": ".message-content:last-child",
        "wait_ms":  3000,
    },
    # Fallback für unbekannte Sites
    "_default": {
        "input":    "textarea, input[type='text']",
        "send":     "button[type='submit'], button:last-child",
        "response": "main p:last-child, .response:last-child, .message:last-child",
        "wait_ms":  5000,
    },
}

SITE_CATALOG_META: dict[str, dict[str, str]] = {
    "chat.openai.com": {
        "site_id": "chatgpt",
        "label": "ChatGPT",
        "url": "https://chat.openai.com",
    },
    "claude.ai": {
        "site_id": "claude",
        "label": "Claude",
        "url": "https://claude.ai",
    },
    "gemini.google.com": {
        "site_id": "gemini",
        "label": "Gemini",
        "url": "https://gemini.google.com",
    },
    "copilot.microsoft.com": {
        "site_id": "copilot",
        "label": "Copilot",
        "url": "https://copilot.microsoft.com",
    },
    "huggingface.co": {
        "site_id": "huggingface",
        "label": "Hugging Face Chat",
        "url": "https://huggingface.co/chat",
    },
    "poe.com": {
        "site_id": "poe",
        "label": "Poe",
        "url": "https://poe.com",
    },
    "you.com": {
        "site_id": "you",
        "label": "You.com",
        "url": "https://you.com",
    },
    "perplexity.ai": {
        "site_id": "perplexity",
        "label": "Perplexity",
        "url": "https://perplexity.ai",
    },
    "mistral.ai": {
        "site_id": "mistral",
        "label": "Mistral",
        "url": "https://mistral.ai",
    },
    "groq.com": {
        "site_id": "groq",
        "label": "Groq",
        "url": "https://groq.com",
    },
}


def get_adapter(url: str) -> dict:
    for domain, adapter in SITE_ADAPTERS.items():
        if domain != "_default" and domain in url:
            return adapter
    return SITE_ADAPTERS["_default"]


# ── KI-Instanz ────────────────────────────────────────────────────────────────
@dataclass
class KIInstance:
    id:         str
    url:        str
    name:       str
    aktiv:      bool  = False
    eingeloggt: bool  = False
    fehler:     int   = 0
    letzter_einsatz: float = 0.0
    antworten:  int   = 0
    avg_latenz: float = 0.0
    context     = None   # playwright BrowserContext
    page        = None   # playwright Page

    def to_dict(self) -> dict:
        return {
            "id":        self.id,
            "url":       self.url,
            "name":      self.name,
            "aktiv":     self.aktiv,
            "eingeloggt": self.eingeloggt,
            "fehler":    self.fehler,
            "antworten": self.antworten,
            "avg_latenz": round(self.avg_latenz, 2),
        }

    def update_latenz(self, sek: float):
        n = self.antworten + 1
        self.avg_latenz = (self.avg_latenz * self.antworten + sek) / n
        self.antworten  = n


# ── Browser-Manager ───────────────────────────────────────────────────────────
class BrowserManager:
    """
    Verwaltet alle KI-Browser-Instanzen.

    Beim Start:
      1. Playwright initialisieren
      2. Für jede konfigurierte URL: Browser-Context erstellen
      3. Auto-Login wenn Credentials vorhanden
      4. Instanzen als "bereit" markieren

    Dispatcher nutzt dann ask_instance() oder broadcast().
    """

    def __init__(self):
        self.cfg        = get_config()
        self._instances: dict[str, KIInstance] = {}
        self._pw        = None    # playwright instance
        self._browser   = None   # chromium browser
        self._creds:    dict[str, LoginProfile] = {}
        self._bereit    = False
        self._lock_obj  = None
        self._load_creds()
        log.info("BrowserManager initialisiert")

    @property
    def _lock(self) -> asyncio.Lock:
        if self._lock_obj is None:
            self._lock_obj = asyncio.Lock()
        return self._lock_obj

    def _browser_allowed(self, url: str = "") -> bool:
        if not self.cfg.browser_automation:
            return False
        if url and not self.cfg.browser_external_sites:
            return False
        return True

    def _constitution_gate_browser(
        self,
        action: str,
        *,
        url: str = "",
        require_owner: bool = False,
    ) -> Optional[str]:
        """Verfassungs-Gate für Browser-Ausführung (outside_effect + optional Owner)."""
        from constitution_override import apply_constitution_gate, build_override_context

        owner = is_owner_equivalent_mode()
        metadata = {
            "outside_effect": True,
            "audit_logged": True,
            "risk": "high",
            "owner_approved": owner,
            "url": (url or "")[:160],
        }
        if require_owner and not owner:
            metadata["privilege_escalation"] = True
        gate = apply_constitution_gate(
            action,
            metadata,
            build_override_context(
                source=f"browser.{action}",
                caller_level=Level.STEFFEN if owner else Level.TASK,
                owner_confirmed=owner,
                override_reason="owner_equivalent_mode" if owner else "",
            ),
        )
        if gate.get("allowed"):
            return None
        blocked = ", ".join(gate.get("blocked_by") or [])
        return f"Verfassung blockiert Browser: {blocked}"

    # ── Startup ───────────────────────────────────────────────────────────────
    async def _ensure_runtime(self):
        if self._browser is not None and self._pw is not None:
            return True
        if not self._browser_allowed():
            log.warning("Browser-Automation durch Runtime-Policy deaktiviert")
            return False
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            log.error(
                "Playwright nicht installiert!\n"
                "Installiere mit: pip install playwright && playwright install chromium"
            )
            return False
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self.cfg.browser.headless,
            slow_mo=self.cfg.browser.slowmo,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        return True

    async def start(self, urls: list[dict]):
        """
        urls = [{"id": "claude1", "url": "https://claude.ai", "name": "Claude #1"}, ...]
        """
        if not await self._ensure_runtime():
            return

        log.info(f"Browser startet mit {len(urls)} Instanzen...")

        # Instanzen sequentiell erstellen (nicht alle gleichzeitig)
        for ui in urls:
            try:
                inst = await self._create_instance(ui)
                self._instances[inst.id] = inst
                await asyncio.sleep(1.5)   # Sanfter Start
            except Exception as e:
                log.error(f"Instanz {ui.get('id')} fehlgeschlagen: {e}")

        self._bereit = True
        log.info(
            f"BrowserManager bereit │ "
            f"{len([i for i in self._instances.values() if i.aktiv])} Instanzen aktiv"
        )

    async def _create_instance(self, ui: dict) -> KIInstance:
        inst = KIInstance(
            id   = ui["id"],
            url  = ui["url"],
            name = ui.get("name", ui["id"]),
        )

        # Isolierter Context (eigene Cookies, Session, Storage)
        inst.context = await self._browser.new_context(
            viewport         = {"width": 1280, "height": 900},
            user_agent       = (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            java_script_enabled = True,
            ignore_https_errors = True,
        )
        inst.page = await inst.context.new_page()

        # Automation-Detection deaktivieren
        await inst.page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = {runtime: {}};
        """)

        # Zur URL navigieren (resilient wait — no networkidle hang)
        log.info(f"Instanz {inst.id}: Lade {inst.url}")
        try:
            await self._safe_goto(inst.page, inst.url, timeout=30000)
            inst.aktiv = True
        except Exception as e:
            log.warning(f"Instanz {inst.id}: Navigation fehlgeschlagen: {e}")
            inst.fehler += 1

        # Auto-Login versuchen
        if inst.aktiv:
            await self._auto_login(inst)

        AuditLog.instance(inst.id, "created", detail=inst.url)
        return inst

    def _normalize_url(self, url: str) -> str:
        raw = (url or "").strip()
        if raw and "://" not in raw:
            raw = f"https://{raw}"
        return raw

    @staticmethod
    def _page_alive(inst: Optional["KIInstance"]) -> bool:
        """True if instance page/context look usable (browser-use-style guard)."""
        if not inst or not getattr(inst, "aktiv", False):
            return False
        page = getattr(inst, "page", None)
        if page is None:
            return False
        try:
            if hasattr(page, "is_closed") and page.is_closed():
                return False
        except Exception:
            return False
        return True

    async def _safe_goto(
        self,
        page,
        url: str,
        *,
        timeout: int = 30000,
    ) -> None:
        """Navigate with resilient wait strategy.

        ``networkidle`` hangs on long-polling sites. Prefer ``domcontentloaded``,
        then ``load``, then ``commit`` (Playwright / browser-use practice).
        """
        target = self._normalize_url(url)
        if not target:
            raise ValueError("leere URL")
        last_err: Optional[Exception] = None
        for wait_until in ("domcontentloaded", "load", "commit"):
            try:
                await page.goto(target, wait_until=wait_until, timeout=timeout)
                return
            except Exception as exc:
                last_err = exc
                log.debug(
                    "goto fallback wait_until=%s url=%s err=%s",
                    wait_until,
                    target[:120],
                    str(exc)[:120],
                )
        if last_err:
            raise last_err
        raise RuntimeError(f"Navigation fehlgeschlagen: {target[:120]}")

    async def _safe_wait_load(self, page, *, timeout: int = 15000) -> None:
        """Wait for load state without requiring networkidle."""
        for state in ("domcontentloaded", "load"):
            try:
                await page.wait_for_load_state(state, timeout=timeout)
                return
            except Exception as exc:
                log.debug("wait_for_load_state %s: %s", state, str(exc)[:100])

    async def _action_with_retry(
        self,
        coro_factory,
        *,
        attempts: int = 2,
        pause_s: float = 0.35,
        label: str = "action",
    ):
        """Retry transient click/fill failures once (bounded)."""
        last_err: Optional[Exception] = None
        n = max(1, int(attempts))
        for i in range(n):
            try:
                return await coro_factory()
            except Exception as exc:
                last_err = exc
                if i + 1 >= n:
                    break
                log.debug("%s retry %s/%s: %s", label, i + 1, n, str(exc)[:100])
                await asyncio.sleep(pause_s)
        assert last_err is not None
        raise last_err

    async def _close_instance_resources(self, inst: KIInstance) -> None:
        try:
            if getattr(inst, "context", None):
                await inst.context.close()
        except Exception as exc:
            log.debug("context close %s: %s", inst.id, exc)
        inst.page = None
        inst.context = None
        inst.aktiv = False

    async def ensure_instance(self, instance_id: str, url: str, name: str = "") -> KIInstance:
        target_url = self._normalize_url(url)
        if not self._browser_allowed(target_url):
            raise PermissionError("Browser-Nutzung ist durch Runtime-Policy deaktiviert")
        if not await self._ensure_runtime():
            raise RuntimeError("Browser-Runtime nicht verfügbar")

        inst = self._instances.get(instance_id)
        if inst and self._page_alive(inst):
            if target_url and inst.url != target_url:
                inst.url = target_url
                await self._safe_goto(inst.page, target_url, timeout=30000)
            return inst

        # Stale/dead instance: drop resources before recreate
        if inst and not self._page_alive(inst):
            await self._close_instance_resources(inst)
            self._instances.pop(instance_id, None)

        inst = await self._create_instance({
            "id": instance_id,
            "url": target_url,
            "name": name or instance_id,
        })
        self._instances[inst.id] = inst
        return inst

    # ── Auto-Login ─────────────────────────────────────────────────────────────
    async def _auto_login(self, inst: KIInstance):
        """Loggt sich automatisch ein wenn Credentials vorhanden."""
        from urllib.parse import urlparse
        domain = urlparse(inst.url).netloc

        cred = self._creds.get(domain) or self._creds.get(
            domain.replace("www.", "")
        )
        if not cred:
            log.debug(f"Keine Credentials für {domain}")
            return

        constitution_block = self._constitution_gate_browser(
            "browser_login",
            url=inst.url or cred.login_url,
            require_owner=True,
        )
        if constitution_block:
            log.warning("Auto-Login übersprungen: %s", constitution_block)
            AuditLog.action(
                "Browser",
                "auto_login_blocked",
                f"{domain}: {constitution_block[:120]}",
                erfolg=False,
            )
            return

        log.info(f"Auto-Login: {inst.id} @ {domain}")
        try:
            # Login-Seite aufrufen
            await self._safe_goto(inst.page, cred.login_url, timeout=20000)
            await asyncio.sleep(1.5)

            # Username eingeben
            try:
                await inst.page.fill(cred.user_selector, cred.username)
                await asyncio.sleep(0.5)
            except Exception:
                log.warning(f"Username-Feld nicht gefunden: {cred.user_selector}")

            # Passwort eingeben
            try:
                await inst.page.fill(cred.pass_selector, cred.password)
                await asyncio.sleep(0.5)
            except Exception:
                log.warning(f"Passwort-Feld nicht gefunden")

            # Absenden
            try:
                await inst.page.click(cred.submit_selector)
                await self._safe_wait_load(inst.page, timeout=15000)
            except Exception:
                log.warning(f"Submit fehlgeschlagen")

            # Erfolg prüfen
            await asyncio.sleep(2)
            if cred.logged_in_check:
                try:
                    await inst.page.wait_for_selector(
                        cred.logged_in_check, timeout=8000
                    )
                    inst.eingeloggt = True
                    log.info(f"✓ Login {inst.id} @ {domain}")
                except Exception:
                    log.warning(f"Login-Check fehlgeschlagen: {inst.id}")
            else:
                # Kein Check-Selector → Annahme: OK wenn kein Error
                inst.eingeloggt = True
                log.info(f"✓ Login {inst.id} @ {domain} (kein Check)")

            # Zurück zur Chat-URL
            if inst.url != cred.login_url:
                await self._safe_goto(inst.page, inst.url, timeout=20000)

            AuditLog.instance(inst.id, "logged_in", detail=domain)

        except Exception as e:
            log.error(f"Auto-Login fehlgeschlagen {inst.id}: {e}")
            AuditLog.error("Browser", f"Login {inst.id}", str(e)[:100])

    # ── Nachricht senden ───────────────────────────────────────────────────────
    async def ask_instance(self, instance_id: str,
                           prompt: str,
                           timeout: float = 60.0) -> str:
        """
        Schickt einen Prompt an eine KI-Instanz per Browser.
        Gibt die Antwort als Text zurück.
        """
        inst = self._instances.get(instance_id)
        if not inst or not self._page_alive(inst):
            return f"[Browser] Instanz {instance_id} nicht verfügbar"

        async with self._lock:
            t0 = time.monotonic()
            try:
                adapter = get_adapter(inst.url)
                page    = inst.page

                # Input-Feld finden und füllen
                await page.wait_for_selector(
                    adapter["input"], timeout=10000
                )

                # Eingabe simulieren (menschlich)
                await page.click(adapter["input"])
                await asyncio.sleep(0.3)

                # Text schrittweise eingeben (weniger detektierbar)
                await page.fill(adapter["input"], "")
                await page.type(adapter["input"], prompt, delay=15)
                await asyncio.sleep(0.5)

                # Senden (Enter oder Button)
                try:
                    await page.click(adapter["send"], timeout=3000)
                except Exception:
                    await page.keyboard.press("Enter")

                # Auf Antwort warten
                await asyncio.sleep(adapter["wait_ms"] / 1000)

                # Antwort lesen
                antwort = await self._extract_response(page, adapter, timeout)

                latenz = time.monotonic() - t0
                inst.update_latenz(latenz)
                inst.letzter_einsatz = time.monotonic()

                AuditLog.instance(instance_id, "answered",
                                  detail=f"{len(antwort.split())} Wörter")
                return antwort

            except Exception as e:
                inst.fehler += 1
                log.error(f"Instanz {instance_id}: {e}")
                AuditLog.error("Browser", f"ask:{instance_id}", str(e)[:100])

                # Neustart versuchen wenn zu viele Fehler
                if inst.fehler >= self.cfg.browser.max_errors_per_instance:
                    await self._restart_instance(inst)
                return f"[Browser-Fehler:{instance_id}] {e}"

    async def _extract_response(self, page, adapter: dict,
                                 timeout: float) -> str:
        """Wartet und extrahiert die KI-Antwort."""
        deadline = time.monotonic() + timeout
        selector = adapter["response"]

        while time.monotonic() < deadline:
            try:
                # Auf Response-Element warten
                el = await page.wait_for_selector(selector, timeout=5000)
                if el:
                    text = await el.inner_text()
                    if text and len(text.strip()) > 10:
                        # Prüfen ob noch gestreamt wird
                        await asyncio.sleep(1.5)
                        text2 = await el.inner_text()
                        if text == text2:   # Stabil → fertig
                            return text.strip()
            except Exception:
                pass
            await asyncio.sleep(1.0)

        # Fallback: letzten sichtbaren Text nehmen
        try:
            text = await page.inner_text("body")
            # Nur letzten Teil (Antwort ist meist am Ende)
            return text[-2000:].strip()
        except Exception:
            return "[Keine Antwort extrahiert]"

    def _extract_candidate_secret(self, text: str, prefixes: tuple[str, ...] = ()) -> str:
        if not text:
            return ""
        if prefixes:
            for prefix in prefixes:
                match = re.search(rf"{re.escape(prefix)}[A-Za-z0-9_\-]{{10,}}", text)
                if match:
                    return match.group(0)
        candidates = re.findall(r"[A-Za-z0-9_\-]{20,}", text)
        if not candidates:
            return ""
        candidates.sort(key=len, reverse=True)
        return candidates[0]

    def _refresh_relay_after_provision(self, provider_id: str):
        try:
            from relay import get_relay

            relay = get_relay()
            relay.refresh_provider_config()
            health = relay._health.get(provider_id)
            if health:
                health.consecutive_failures = 0
                health.last_error = ""
        except Exception as e:
            log.warning("Relay-Refresh nach Provisioning fehlgeschlagen: %s", e)

    def _connect_provisioned_token(self, secret_ref: str, provider_id: str, token: str) -> None:
        get_secrets_store().set_secret(secret_ref, token, kind="browser_import")
        current = self.cfg.providers.get(provider_id)
        if not current:
            return
        try:
            self.cfg.upsert_provider(
                {
                    "provider_id": provider_id,
                    "display_name": current.display_name,
                    "provider_type": current.provider_type,
                    "base_url": current.base_url,
                    "model": current.model,
                    "enabled": True,
                    "is_default": current.is_default or provider_id == self.cfg.relay.primary_provider,
                    "timeout": current.timeout,
                    "rpm": current.rpm,
                    "tpm": current.tpm,
                    "api_key": token,
                    "api_key_ref": secret_ref,
                }
            )
        except Exception:
            current.api_key = token
            if not current.api_key_ref:
                current.api_key_ref = secret_ref
        self.cfg = get_config()
        self._refresh_relay_after_provision(provider_id)

    async def _click_first_label(self, page, labels: list[str], *, timeout: int = 2500, pick: str = "first") -> bool:
        for label in labels:
            try:
                locator = page.get_by_text(label, exact=False)
                target = locator.first if pick == "first" else locator.last
                await target.click(timeout=timeout)
                return True
            except Exception:
                continue
        return False

    async def _extract_tokens_from_page(self, page, prefixes: tuple[str, ...] = ()) -> list[str]:
        candidates: list[str] = []
        selectors = ["code", "input[readonly]", "input[type='text']", "textarea", "[data-token]"]
        for selector in selectors:
            try:
                loc = page.locator(selector)
                count = await loc.count()
                for idx in range(min(count, 8)):
                    node = loc.nth(idx)
                    text = ""
                    try:
                        text = await node.input_value(timeout=500)
                    except Exception:
                        try:
                            text = await node.inner_text(timeout=500)
                        except Exception:
                            text = ""
                    token = self._extract_candidate_secret(text, prefixes=prefixes)
                    if token:
                        candidates.append(token)
            except Exception:
                continue
        return candidates

    _FREE_PROVIDER_IDS = frozenset({
        "ollama", "groq", "openrouter", "huggingface", "together", "perplexity", "mistral",
    })

    def _provision_candidate_allowed(self, provider_id: str) -> bool:
        cfg = self.cfg.providers.get((provider_id or "").strip().lower())
        if not cfg or not cfg.enabled:
            return False
        if getattr(self.cfg, "free_only_providers", False):
            return (provider_id or "").strip().lower() in self._FREE_PROVIDER_IDS
        return True

    def providers_needing_provision(self, provider_ids: Optional[list[str]] = None) -> list[str]:
        targets = provider_ids or []
        if not targets:
            primary = (self.cfg.relay.primary_provider or "").strip()
            if primary:
                targets.append(primary)
            for pid in self.cfg.providers.keys():
                if pid not in targets and self._provision_candidate_allowed(pid):
                    targets.append(pid)

        missing: list[str] = []
        for pid in targets:
            spec = PROVIDER_PROVISION_SPECS.get((pid or "").strip().lower())
            if not spec:
                continue
            cfg = self.cfg.providers.get(pid)
            if not cfg or not self._provision_candidate_allowed(pid):
                continue
            ptype = (cfg.provider_type or "").lower()
            if ptype in {"ollama", "local_ollama"}:
                continue
            if not (cfg.api_key or "").strip():
                missing.append(pid)
        return missing

    async def provision_provider_token(
        self,
        provider_id: str,
        secret_ref: str = "",
        key_name: str = "Isaac",
    ) -> dict[str, Any]:
        pid = (provider_id or "").strip().lower()
        spec = PROVIDER_PROVISION_SPECS.get(pid)
        if not spec:
            return {"ok": False, "error": f"Kein Browser-Provisioning für Provider '{provider_id}'"}

        # Free-Cloud / Browser aus: kein Playwright, keine stillen Privilege-Escalations
        try:
            from free_cloud import free_cloud_enabled
            free = free_cloud_enabled()
        except Exception:
            free = False
        if free or not self.cfg.browser_automation:
            env_key = (spec.get("secret_ref") or "").strip() or f"{pid.upper()}_API_KEY"
            existing = (os.getenv(env_key) or "").strip()
            if not existing and pid == "openrouter":
                existing = (os.getenv("OPENROUTER_API_KEY") or "").strip()
            if not existing and pid == "groq":
                existing = (os.getenv("GROQ_API_KEY") or "").strip()
            if not existing and pid in {"gemini", "google"}:
                existing = (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()
            if existing:
                ref = (secret_ref or env_key).strip() or env_key
                self._connect_provisioned_token(ref, pid, existing)
                return {
                    "ok": True,
                    "provider_id": pid,
                    "secret_ref": ref,
                    "token_preview": existing[:6] + "..." + existing[-4:],
                    "source": "env",
                    "message": f"{pid}-Key aus Environment verbunden (kein Browser nötig).",
                }
            return {
                "ok": False,
                "error": (
                    f"Browser-Token für '{pid}' auf Free-Cloud/ohne Browser deaktiviert "
                    f"(Verfassung: no_silent_privilege_escalation). "
                    f"Setze den Key als Env-Var {env_key} (Render Dashboard → Environment) "
                    f"oder lokal in .env — dann neu deployen/neustarten."
                ),
                "source": "free_cloud_policy",
                "provider_id": pid,
            }

        constitution_block = self._constitution_gate_browser(
            "browser_provision",
            url=str(spec.get("keys_url") or ""),
            require_owner=True,
        )
        if constitution_block:
            AuditLog.action("Browser", "provision_blocked", constitution_block[:160], erfolg=False)
            return {"ok": False, "error": constitution_block, "source": "constitution", "provider_id": pid}

        ref = (secret_ref or spec["secret_ref"]).strip() or spec["secret_ref"]
        keys_url = spec["keys_url"]
        instance_id = spec["instance_id"]
        prefixes = tuple(spec.get("token_prefixes") or ())
        create_labels = list(spec.get("create_labels") or [])
        confirm_labels = list(spec.get("confirm_labels") or ["Create", "Generate", "Save", "Submit"])

        plan = [
            {"action": "goto", "url": keys_url},
            {"action": "wait", "seconds": 1.2},
        ]
        result = await self.run_flow(instance_id, keys_url, plan, name=spec.get("label") or pid)
        if not result.get("ok"):
            return result

        inst = self._instances.get(instance_id)
        if not inst or not inst.page:
            return {"ok": False, "error": f"{spec.get('label', pid)}-Instanz nicht verfügbar"}

        page = inst.page
        clicked = await self._click_first_label(page, create_labels)
        if not clicked:
            return {
                "ok": False,
                "error": f"{spec.get('label', pid)}-Key-Button nicht gefunden (Login nötig?)",
                "current_url": page.url,
                "provider_id": pid,
            }

        try:
            await asyncio.sleep(1.0)
            for selector in ["input[name*=name]", "input[placeholder*=name]", "input[type='text']"]:
                try:
                    await page.locator(selector).first.fill(key_name, timeout=1500)
                    break
                except Exception:
                    continue
            await self._click_first_label(page, confirm_labels, timeout=1500, pick="last")
            await asyncio.sleep(1.5)
        except Exception as e:
            return {
                "ok": False,
                "error": f"{spec.get('label', pid)}-Key-Dialog fehlgeschlagen: {e}",
                "current_url": page.url,
                "provider_id": pid,
            }

        candidates = await self._extract_tokens_from_page(page, prefixes=prefixes)
        if not candidates:
            return {
                "ok": False,
                "error": f"{spec.get('label', pid)}-Token konnte nicht extrahiert werden",
                "current_url": page.url,
                "provider_id": pid,
            }

        token = max(candidates, key=len)
        self._connect_provisioned_token(ref, pid, token)
        AuditLog.action("Browser", "provision_token", f"provider={pid}", level=40)
        return {
            "ok": True,
            "provider_id": pid,
            "secret_ref": ref,
            "current_url": page.url,
            "token_preview": token[:6] + "..." + token[-4:],
        }

    async def provision_groq_token(self, secret_ref: str = "GROQ_API_KEY", key_name: str = "Isaac") -> dict[str, Any]:
        return await self.provision_provider_token("groq", secret_ref=secret_ref, key_name=key_name)

    async def auto_provision_providers(
        self,
        provider_ids: Optional[list[str]] = None,
        *,
        provision_all: Optional[bool] = None,
    ) -> dict[str, Any]:
        if not self.cfg.browser_automation or not self.cfg.browser_external_sites:
            return {"ok": False, "error": "Browser-Automation deaktiviert", "results": []}
        if not getattr(self.cfg, "auto_provision_providers", True):
            return {"ok": False, "error": "Auto-Provisioning deaktiviert", "results": []}

        missing = self.providers_needing_provision(provider_ids)
        if not missing:
            return {"ok": True, "message": "Keine fehlenden Provider-Keys", "results": [], "attempted": []}

        try_all = (
            provision_all
            if provision_all is not None
            else bool(getattr(self.cfg, "auto_provision_all_providers", True))
        )

        results: list[dict[str, Any]] = []
        for pid in missing:
            self.cfg = get_config()
            result = await self.provision_provider_token(pid)
            results.append(result)
            if result.get("ok"):
                log.info("Auto-Provisioning erfolgreich: %s", pid)
                if not try_all:
                    break
            else:
                log.warning("Auto-Provisioning fehlgeschlagen (%s): %s", pid, result.get("error", "unbekannt"))

        ok_count = sum(1 for r in results if r.get("ok"))
        ok = ok_count > 0
        return {
            "ok": ok,
            "results": results,
            "attempted": missing,
            "provisioned": ok_count,
            "failed": len(results) - ok_count,
        }

    def _resolve_target(self, page, action: dict):
        selector = (action.get("selector") or "").strip()
        text = (action.get("text") or "").strip()
        if selector:
            return page.locator(selector).first
        if text:
            return page.get_by_text(text, exact=bool(action.get("exact", False))).first
        raise ValueError("Browser-Aktion benötigt selector oder text")

    async def run_flow(self, instance_id: str, start_url: str, actions: list[dict], name: str = "") -> dict[str, Any]:
        constitution_block = self._constitution_gate_browser(
            "browser_automation",
            url=start_url or "",
            require_owner=False,
        )
        if constitution_block:
            AuditLog.action("Browser", "flow_blocked", constitution_block[:160], erfolg=False)
            return {"ok": False, "error": constitution_block, "source": "constitution"}

        inst = await self.ensure_instance(instance_id, start_url, name=name or instance_id)
        page = inst.page
        memory: dict[str, str] = {}
        steps: list[dict[str, Any]] = []

        for idx, action in enumerate(actions or [], start=1):
            kind = (action.get("action") or "").strip().lower()
            try:
                if kind == "goto":
                    url = self._normalize_url(action.get("url") or start_url)
                    if not self._browser_allowed(url):
                        raise PermissionError("Externe Browser-Ziele sind deaktiviert")
                    await self._safe_goto(page, url, timeout=30000)
                    steps.append({"step": idx, "action": kind, "url": page.url, "ok": True})
                    continue

                if kind == "wait":
                    seconds = max(0.1, float(action.get("seconds", 1.0)))
                    await asyncio.sleep(seconds)
                    steps.append({"step": idx, "action": kind, "seconds": seconds, "ok": True})
                    continue

                if kind == "press":
                    key = (action.get("key") or "Enter").strip() or "Enter"
                    await page.keyboard.press(key)
                    steps.append({"step": idx, "action": kind, "key": key, "ok": True})
                    continue

                target = self._resolve_target(page, action)
                if kind == "click":
                    await self._action_with_retry(
                        lambda t=target: t.click(timeout=10000),
                        label="click",
                    )
                    steps.append({"step": idx, "action": kind, "target": action.get("selector") or action.get("text"), "ok": True})
                    continue

                if kind == "fill":
                    value = str(action.get("value") or "")
                    await self._action_with_retry(
                        lambda t=target, v=value: t.fill(v, timeout=10000),
                        label="fill",
                    )
                    steps.append({"step": idx, "action": kind, "target": action.get("selector") or action.get("text"), "ok": True})
                    continue

                if kind in {"extract_text", "extract_value"}:
                    async def _extract(t=target, k=kind):
                        if k == "extract_value":
                            return await t.input_value(timeout=10000)
                        return await t.inner_text(timeout=10000)

                    value = await self._action_with_retry(_extract, label=kind)
                    slot = (action.get("save_as") or f"value_{idx}").strip()
                    memory[slot] = value.strip()
                    steps.append({"step": idx, "action": kind, "save_as": slot, "length": len(memory[slot]), "ok": True})
                    continue

                if kind == "store_secret":
                    slot = (action.get("from_var") or "").strip()
                    secret_ref = (action.get("ref") or "").strip()
                    if not self.cfg.browser_external_sites:
                        raise PermissionError("Secret-Import via Browser ist deaktiviert")
                    if not slot or slot not in memory:
                        raise ValueError("store_secret benötigt from_var eines extrahierten Werts")
                    if not secret_ref:
                        raise ValueError("store_secret benötigt ref")
                    secret = self._extract_candidate_secret(memory[slot]) or memory[slot]
                    get_secrets_store().set_secret(secret_ref, secret, kind="browser_import")
                    steps.append({"step": idx, "action": kind, "ref": secret_ref, "ok": True})
                    continue

                raise ValueError(f"Nicht unterstützte Browser-Aktion: {kind}")
            except Exception as e:
                steps.append({"step": idx, "action": kind, "ok": False, "error": str(e)})
                return {
                    "ok": False,
                    "instance_id": inst.id,
                    "current_url": page.url,
                    "steps": steps,
                    "memory": memory,
                    "error": str(e),
                }

        return {
            "ok": True,
            "instance_id": inst.id,
            "current_url": page.url,
            "steps": steps,
            "memory": memory,
        }

    async def provision_openrouter_token(self, secret_ref: str = "OPENROUTER_API_KEY", key_name: str = "Isaac") -> dict[str, Any]:
        return await self.provision_provider_token(
            "openrouter",
            secret_ref=secret_ref,
            key_name=key_name,
        )

    # ── Broadcast ─────────────────────────────────────────────────────────────
    async def broadcast(self, prompt: str,
                        instance_ids: Optional[list] = None,
                        stagger_ms: int = 1500) -> dict[str, str]:
        """
        Sendet einen Prompt an mehrere Instanzen.
        Mit konfigurierbarer Latenz zwischen Anfragen (kein Ban-Risiko).
        """
        ids = instance_ids or [
            i.id for i in self._instances.values() if i.aktiv
        ]

        ergebnisse = {}
        tasks      = []

        for i, iid in enumerate(ids):
            # Stagger: jede Anfrage mit Verzögerung starten
            delay = (i * stagger_ms) / 1000.0
            tasks.append(self._delayed_ask(iid, prompt, delay))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for iid, result in zip(ids, results):
            if isinstance(result, Exception):
                ergebnisse[iid] = f"[Fehler: {result}]"
            else:
                ergebnisse[iid] = result

        return ergebnisse

    async def _delayed_ask(self, iid: str, prompt: str,
                           delay: float) -> str:
        await asyncio.sleep(delay)
        return await self.ask_instance(iid, prompt)

    # ── Instanz neustarten ────────────────────────────────────────────────────
    async def _restart_instance(self, inst: KIInstance):
        log.warning(f"Neustart: {inst.id}")
        try:
            await self._close_instance_resources(inst)
            inst.fehler = 0
            if not await self._ensure_runtime():
                raise RuntimeError("Browser-Runtime nicht verfügbar")
            inst.context = await self._browser.new_context(
                viewport={"width": 1280, "height": 900}
            )
            inst.page = await inst.context.new_page()
            await self._safe_goto(inst.page, inst.url, timeout=30000)
            inst.aktiv = True
            await self._auto_login(inst)
            AuditLog.instance(inst.id, "restarted")
        except Exception as e:
            log.error(f"Neustart {inst.id} fehlgeschlagen: {e}")

    # ── Credentials verwalten ─────────────────────────────────────────────────
    def add_credential(self, domain: str, login_url: str,
                       username: str, password: str,
                       logged_in_check: str = "") -> bool:
        """Fügt Login-Daten für eine Domain hinzu."""
        self._creds[domain] = LoginProfile(
            domain          = domain,
            login_url       = login_url,
            username        = username,
            password        = password,
            logged_in_check = logged_in_check,
        )
        self._save_creds()
        log.info(f"Credentials gespeichert: {domain}")
        return True

    def remove_credential(self, domain: str):
        self._creds.pop(domain, None)
        self._save_creds()

    def _load_creds(self):
        if not CREDS_PATH.exists():
            return
        try:
            data = json.loads(CREDS_PATH.read_text())
            for domain, d in data.items():
                self._creds[domain] = LoginProfile(**d)
            log.info(f"Credentials geladen: {len(self._creds)} Domains")
        except Exception as e:
            log.warning(f"Credentials laden: {e}")

    def _save_creds(self):
        try:
            CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {k: asdict(v) for k, v in self._creds.items()}
            CREDS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            # Nur Owner-Zugriff
            import stat
            CREDS_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except Exception as e:
            log.warning(f"Credentials speichern: {e}")

    # ── Status ────────────────────────────────────────────────────────────────
    def add_url(self, url_config: dict):
        """Fügt eine neue URL-Konfiguration hinzu (dynamisch)."""
        # Wird beim nächsten start() oder manuell initiiert
        self._pending_urls = getattr(self, "_pending_urls", [])
        self._pending_urls.append(url_config)

    def site_catalog(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for domain, meta in SITE_CATALOG_META.items():
            existing = next((inst for inst in self._instances.values() if domain in inst.url), None)
            rows.append({
                "site_id": meta["site_id"],
                "label": meta["label"],
                "domain": domain,
                "url": meta["url"],
                "active": bool(existing and existing.aktiv),
                "logged_in": bool(existing and existing.eingeloggt),
                "instance_id": existing.id if existing else "",
            })
        return rows

    async def activate_catalog_site(self, site_id: str) -> dict[str, Any]:
        target = next((meta | {"domain": domain} for domain, meta in SITE_CATALOG_META.items() if meta["site_id"] == site_id), None)
        if not target:
            raise KeyError(f"Unbekannte Browser-Site: {site_id}")
        inst = await self.ensure_instance(target["site_id"], target["url"], name=target["label"])
        return {
            "ok": True,
            "site_id": target["site_id"],
            "instance": inst.to_dict(),
        }

    def list_instances(self) -> list[dict]:
        return [i.to_dict() for i in self._instances.values()]

    def get_active_ids(self) -> list[str]:
        return [i.id for i in self._instances.values() if i.aktiv]

    def stats(self) -> dict:
        inst = list(self._instances.values())
        return {
            "total":     len(inst),
            "aktiv":     sum(1 for i in inst if i.aktiv),
            "eingeloggt": sum(1 for i in inst if i.eingeloggt),
            "bereit":    self._bereit,
            "creds":     len(self._creds),
        }

    async def close(self):
        for inst in self._instances.values():
            try:
                if inst.context:
                    await inst.context.close()
            except Exception:
                pass
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        log.info("BrowserManager geschlossen")


# ── Singleton ─────────────────────────────────────────────────────────────────
_browser_mgr: Optional[BrowserManager] = None

def get_browser() -> BrowserManager:
    global _browser_mgr
    if _browser_mgr is None:
        _browser_mgr = BrowserManager()
    return _browser_mgr
