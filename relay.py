"""
Isaac – Async Multi-Provider Relay
====================================
Vollständig async. Kein threading.Thread mehr.

Verbesserungen:
  - Circuit Breaker pro Provider
  - robustere Token-Schätzung und TPM-Tracking
  - vollständigerer Cache-Key
  - Ollama-Healthcheck / sanfter Fallback
  - Provider-Status mit Fehlern, Cooldown und Latenz
"""

from __future__ import annotations

import asyncio
import aiohttp
import hashlib
import time
import logging
from dataclasses import dataclass, field
from collections import deque
from typing import Optional

from config  import get_config, ProviderConfig
from audit   import AuditLog

log = logging.getLogger("Isaac.Relay")


class RateLimiter:
    def __init__(self, rpm: int, tpm: int):
        self.rpm = max(1, rpm)
        self.tpm = max(1, tpm)
        self._req_times: deque[float] = deque()
        self._tok_log: deque[tuple[float, int]] = deque()
        self._lock_obj: Optional[asyncio.Lock] = None

    @property
    def _lock(self) -> asyncio.Lock:
        if self._lock_obj is None:
            self._lock_obj = asyncio.Lock()
        return self._lock_obj

    @staticmethod
    def estimate_tokens(text: str) -> int:
        if not text:
            return 1
        return max(1, len(text) // 4)

    async def acquire(self, tokens: int = 100):
        tokens = max(1, tokens)
        async with self._lock:
            now = time.monotonic()
            window = now - 60.0

            while self._req_times and self._req_times[0] < window:
                self._req_times.popleft()
            while self._tok_log and self._tok_log[0][0] < window:
                self._tok_log.popleft()

            req_count = len(self._req_times)
            token_count = sum(t for _, t in self._tok_log)

            if req_count >= self.rpm:
                wait = max(0.0, 60.5 - (now - self._req_times[0]))
                if wait > 0:
                    await asyncio.sleep(wait)

            if token_count + tokens > self.tpm * 0.9 and self._tok_log:
                wait = max(0.0, 60.5 - (now - self._tok_log[0][0]))
                if wait > 0:
                    await asyncio.sleep(wait)

            ts = time.monotonic()
            self._req_times.append(ts)
            self._tok_log.append((ts, tokens))

    def record_actual(self, estimated: int, actual: int):
        actual = max(1, actual)
        extra = max(0, actual - max(1, estimated))
        if extra:
            self._tok_log.append((time.monotonic(), extra))

    def status(self) -> str:
        now = time.monotonic()
        window = now - 60.0
        req = sum(1 for t in self._req_times if t > window)
        tok = sum(t for ts, t in self._tok_log if ts > window)
        return f"RPM:{req}/{self.rpm} TPM:{tok}/{self.tpm}"


class ResponseCache:
    def __init__(self, ttl: int = 30):
        self.ttl = ttl
        self._c: dict[str, tuple[float, str]] = {}

    def _key(self, prompt: str, system: str, provider: str) -> str:
        payload = f"{provider}\n{system}\n{prompt}".encode("utf-8", errors="replace")
        return hashlib.sha256(payload).hexdigest()

    def get(self, prompt: str, system: str, provider: str) -> Optional[str]:
        k = self._key(prompt, system, provider)
        entry = self._c.get(k)
        if entry and time.time() - entry[0] < self.ttl:
            return entry[1]
        return None

    def set(self, prompt: str, system: str, provider: str, antwort: str):
        self._c[self._key(prompt, system, provider)] = (time.time(), antwort)
        now = time.time()
        self._c = {k: v for k, v in self._c.items() if now - v[0] < self.ttl * 2}


@dataclass
class ProviderHealth:
    consecutive_failures: int = 0
    blacklisted_until: float = 0.0
    last_error: str = ""
    last_success_latency: float = 0.0
    success_count: int = 0
    failure_count: int = 0
    last_ok_ts: float = 0.0

    def available_now(self) -> bool:
        return time.monotonic() >= self.blacklisted_until


class AsyncRelay:
    def __init__(self):
        self.cfg = get_config()
        self.cache = ResponseCache(ttl=self.cfg.relay.cache_ttl)
        self._limiters: dict[str, RateLimiter] = {}
        self._health: dict[str, ProviderHealth] = {}
        self._session: Optional[aiohttp.ClientSession] = None
        self._setup_limiters()
        log.info(
            "Relay online │ Primär: %s │ Verfügbar: %s",
            self.cfg.relay.primary_provider,
            ", ".join(self.cfg.available_providers),
        )

    def _setup_limiters(self):
        for name, p in self.cfg.providers.items():
            self._limiters[name] = RateLimiter(p.rpm, p.tpm)
            self._health.setdefault(name, ProviderHealth())

    def refresh_provider_config(self):
        self.cfg = get_config()
        for name, p in self.cfg.providers.items():
            lim = self._limiters.get(name)
            if lim is None or lim.rpm != p.rpm or lim.tpm != p.tpm:
                self._limiters[name] = RateLimiter(p.rpm, p.tpm)
            self._health.setdefault(name, ProviderHealth())
        for stale in [k for k in self._limiters.keys() if k not in self.cfg.providers]:
            self._limiters.pop(stale, None)
            self._health.pop(stale, None)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=max(30, self.cfg.relay.max_retries * 30))
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _is_runtime_available(self, prov_cfg: ProviderConfig) -> bool:
        # available deckt ollama + local/loopback openai_compat (ohne Key) ab
        return bool(prov_cfg.available)

    def _mark_success(self, name: str, latency: float):
        h = self._health[name]
        h.consecutive_failures = 0
        h.last_error = ""
        h.last_success_latency = latency
        h.success_count += 1
        h.last_ok_ts = time.time()

    def _mark_failure(self, name: str, message: str):
        h = self._health[name]
        h.consecutive_failures += 1
        h.failure_count += 1
        h.last_error = message[:250]
        cooldown = min(180, 10 * h.consecutive_failures)
        if h.consecutive_failures >= 3:
            h.blacklisted_until = time.monotonic() + cooldown

    async def ask(self, prompt: str,
                  system: str = "Du bist Isaac, ein autonomes KI-System.",
                  provider: Optional[str] = None,
                  context: str = "",
                  use_cache: bool = True,
                  tokens: int = 0,
                  task_id: str = "",
                  model_override: Optional[str] = None) -> str:
        ask_t0 = time.perf_counter()
        prov_name = provider or self.cfg.relay.primary_provider
        if not self.cfg.is_provider_allowed(prov_name):
            return f"[RELAY-FEHLER:{prov_name}] Provider durch Runtime-Policy blockiert"
        prov_cfg = self.cfg.get_provider(prov_name)
        if not prov_cfg:
            return f"[RELAY] Unbekannter Provider: {prov_name}"
        if not self._is_runtime_available(prov_cfg):
            return f"[RELAY-FEHLER:{prov_name}] Provider nicht konfiguriert oder deaktiviert"

        max_c = self.cfg.relay.max_context_chars
        if context and len(context) > max_c:
            half = max_c // 2
            context = f"{context[:half]}\n[...komprimiert...]\n{context[-half:]}"
        full_prompt = f"{context}\n\n{prompt}".strip() if context else prompt

        if use_cache:
            cache_provider = f"{prov_name}:{model_override}" if model_override else prov_name
            cached = self.cache.get(full_prompt, system, cache_provider)
            if cached:
                return cached

        limiter = self._limiters.get(prov_name)
        approx_tokens = tokens or RateLimiter.estimate_tokens(system + "\n" + full_prompt)
        if limiter:
            await limiter.acquire(approx_tokens)

        model_name = model_override or prov_cfg.model or prov_name
        try:
            from isaac_sentry import finish_chat_span, gen_ai_chat_span
        except Exception:
            gen_ai_chat_span = None  # type: ignore[assignment]
            finish_chat_span = None  # type: ignore[assignment]

        span_cm = (
            gen_ai_chat_span(
                model=model_name,
                provider=prov_name,
                system=system,
                prompt=full_prompt,
                agent_name="Isaac",
            )
            if gen_ai_chat_span
            else None
        )

        def _token_split(actual: int, result_text: str) -> tuple[int, int, int]:
            """Split total tokens into input/output for cost-safe reporting."""
            total = max(0, int(actual or 0))
            inp = max(1, int(approx_tokens or 0))
            if total <= 0:
                out = max(1, RateLimiter.estimate_tokens(result_text or ""))
                return inp, out, inp + out
            if total > inp:
                return inp, total - inp, total
            # Provider returned only completion or an under-count — keep totals consistent
            out = max(1, RateLimiter.estimate_tokens(result_text or ""))
            return max(1, total), out, total + out

        if span_cm is not None:
            with span_cm as span:
                return await self._ask_dispatch_loop(
                    ask_t0=ask_t0,
                    prov_name=prov_name,
                    prov_cfg=prov_cfg,
                    full_prompt=full_prompt,
                    system=system,
                    use_cache=use_cache,
                    model_override=model_override,
                    model_name=model_name,
                    limiter=limiter,
                    approx_tokens=approx_tokens,
                    task_id=task_id,
                    span=span,
                    finish_chat_span=finish_chat_span,
                    token_split=_token_split,
                )

        return await self._ask_dispatch_loop(
            ask_t0=ask_t0,
            prov_name=prov_name,
            prov_cfg=prov_cfg,
            full_prompt=full_prompt,
            system=system,
            use_cache=use_cache,
            model_override=model_override,
            model_name=model_name,
            limiter=limiter,
            approx_tokens=approx_tokens,
            task_id=task_id,
            span=None,
            finish_chat_span=None,
            token_split=_token_split,
        )

    async def _ask_dispatch_loop(
        self,
        *,
        ask_t0: float,
        prov_name: str,
        prov_cfg: ProviderConfig,
        full_prompt: str,
        system: str,
        use_cache: bool,
        model_override: Optional[str],
        model_name: str,
        limiter: Optional[RateLimiter],
        approx_tokens: int,
        task_id: str,
        span,
        finish_chat_span,
        token_split,
    ) -> str:
        cache_provider = f"{prov_name}:{model_override}" if model_override else prov_name
        for versuch in range(1, self.cfg.relay.max_retries + 1):
            try:
                t0 = time.monotonic()
                result, actual_tokens = await self._dispatch(
                    prov_cfg, full_prompt, system, model_override=model_override
                )
                dauer = round(time.monotonic() - t0, 2)
                total_ms = round((time.perf_counter() - ask_t0) * 1000, 2)
                self._mark_success(prov_name, dauer)
                if limiter:
                    limiter.record_actual(approx_tokens, actual_tokens)
                AuditLog.action("Relay", f"ask:{prov_name}", f"task={task_id} t={dauer}s", erfolg=True)
                log.info(
                    "Latency(relay) | provider=%s model=%s dispatch=%ss total=%sms retry=%s",
                    prov_name,
                    model_name,
                    dauer,
                    total_ms,
                    versuch,
                )
                if finish_chat_span and span is not None:
                    inp, out, total = token_split(actual_tokens, result)
                    finish_chat_span(
                        span,
                        result_text=result if isinstance(result, str) else str(result),
                        input_tokens=inp,
                        output_tokens=out,
                        total_tokens=total,
                        model=model_name,
                        success=True,
                    )
                if use_cache:
                    self.cache.set(full_prompt, system, cache_provider, result)
                return result
            except RateLimitErr:
                self._mark_failure(prov_name, "Rate Limit")
                await asyncio.sleep(min(20, 5 * versuch))
            except ProviderErr as e:
                msg = str(e)
                self._mark_failure(prov_name, msg)
                # Offline / connection errors: fail fast (no 3× ollama spam)
                msg_l = msg.lower()
                offline = any(
                    m in msg_l
                    for m in (
                        "nicht erreichbar",
                        "connection refused",
                        "connect error",
                        "connect call failed",
                        "name or service not known",
                        "nodename nor servname",
                        "network is unreachable",
                        "offline",
                    )
                )
                # Expected offline backends (esp. Ollama) must not hit Sentry via
                # LoggingIntegration(event_level=ERROR) on AuditLog.error.
                expected_offline = offline and (
                    prov_name == "ollama" or "ollama" in msg_l
                )
                if expected_offline:
                    log.warning("[Relay] %s provider=%s (expected offline, no Sentry)", msg[:120], prov_name)
                    AuditLog.action(
                        "Relay",
                        f"offline:{prov_name}",
                        msg[:160],
                        erfolg=False,
                    )
                else:
                    AuditLog.error("Relay", msg, f"provider={prov_name}")
                if offline or versuch == self.cfg.relay.max_retries:
                    # Longer cooldown so ask_with_fallback skips this backend
                    if offline:
                        h = self._health.get(prov_name)
                        if h is not None:
                            h.blacklisted_until = time.monotonic() + min(
                                180, 30 * max(1, h.consecutive_failures)
                            )
                    if finish_chat_span and span is not None:
                        finish_chat_span(
                            span,
                            result_text=msg,
                            model=model_name,
                            success=False,
                        )
                    return f"[RELAY-FEHLER:{prov_name}] {msg}"
                await asyncio.sleep(min(10, 2 * versuch))

        err = f"[RELAY] Alle {self.cfg.relay.max_retries} Versuche fehlgeschlagen"
        if finish_chat_span and span is not None:
            finish_chat_span(span, result_text=err, model=model_name, success=False)
        return err

    async def ask_with_fallback(self, prompt: str, system: str = "",
                                preferred: Optional[str] = None,
                                task_id: str = "",
                                model_override: Optional[str] = None) -> tuple[str, str]:
        reihe: list[str] = []
        # Ignore non-provider hints like "auto"
        pref = (preferred or "").strip().lower()
        if pref and pref not in {"auto", "none", "default"} and self.cfg.get_provider(pref):
            reihe.append(pref)
        primary = self.cfg.relay.primary_provider
        if primary and primary not in reihe:
            reihe.append(primary)

        # Cloud providers with keys before loopback-local (avoids free-PaaS ollama lag)
        try:
            from free_cloud import recommended_free_providers
            cloud_first = list(recommended_free_providers())
        except Exception:
            cloud_first = ["groq", "gemini", "openrouter"]
        for p in cloud_first:
            if p not in reihe:
                reihe.append(p)
        for p in self.cfg.available_providers:
            if p not in reihe:
                reihe.append(p)

        usable: list[str] = []
        for name in reihe:
            cfg = self.cfg.get_provider(name)
            if not cfg or not self._is_runtime_available(cfg):
                continue
            if not self.cfg.is_provider_allowed(name):
                continue
            health = self._health.get(name) or self._health.setdefault(name, ProviderHealth())
            if not health.available_now():
                continue
            usable.append(name)

        if not usable:
            usable = [
                n for n in reihe
                if self.cfg.get_provider(n)
                and self.cfg.is_provider_allowed(n)
                and self._is_runtime_available(self.cfg.get_provider(n))
            ]

        for pname in usable:
            result = await self.ask(prompt, system, provider=pname, task_id=task_id, model_override=model_override)
            # ask() may return "[RELAY-FEHLER:...]" OR "[RELAY] Alle N Versuche..."
            # Both must continue fallback — otherwise cloud free-tier never reaches groq/gemini.
            if isinstance(result, str) and result.startswith("[RELAY"):
                log.warning("Fallback: %s fehlgeschlagen → weiter (%s)", pname, result[:120])
                continue
            return result, pname

        return "[RELAY] Alle Provider fehlgeschlagen.", "none"

    async def _dispatch(self, cfg: ProviderConfig, prompt: str, system: str, model_override: Optional[str] = None) -> tuple[str, int]:
        provider_id = (cfg.provider_id or cfg.name or "").lower()
        provider_type = (cfg.provider_type or "").lower()
        by_id = {
            "openrouter": self._openrouter,
            "ollama": self._ollama,
            "anthropic": self._anthropic,
            "gemini": self._gemini,
            "cohere": self._cohere,
            "huggingface": self._huggingface,
        }
        by_type = {
            "openai_compat": self._openai_compat,
            "openai-compatible": self._openai_compat,
            "openrouter": self._openrouter,
            "ollama": self._ollama,
            "local_ollama": self._ollama,
            "anthropic": self._anthropic,
            "gemini": self._gemini,
            "cohere": self._cohere,
            "huggingface": self._huggingface,
        }
        fn = by_id.get(provider_id) or by_type.get(provider_type)
        if not fn:
            raise ProviderErr(f"Kein Handler: {cfg.provider_id}")
        return await fn(cfg, prompt, system, model_override=model_override)

    async def _openai_compat(self, cfg: ProviderConfig, prompt: str, system: str, model_override: Optional[str] = None) -> tuple[str, int]:
        from config import allows_missing_api_key

        if not cfg.api_key and not allows_missing_api_key(cfg):
            raise ProviderErr(f"{cfg.provider_id}: API-Key fehlt")
        session = await self._get_session()
        payload = {
            "model": model_override or cfg.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "max_tokens": 1200,
            "temperature": 0.3,
        }
        headers = {"Content-Type": "application/json"}
        if (cfg.api_key or "").strip():
            headers["Authorization"] = f"Bearer {cfg.api_key.strip()}"
        for k, v in (cfg.extra_headers or {}).items():
            if k and isinstance(k, str):
                headers[k] = str(v)
        try:
            async with session.post(
                cfg.base_url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=cfg.timeout),
            ) as r:
                if r.status == 429:
                    raise RateLimitErr(f"{cfg.provider_id} Rate Limit")
                if r.status != 200:
                    txt = await r.text()
                    raise ProviderErr(f"{cfg.provider_id} {r.status}: {txt[:160]}")
                data = await r.json()
                try:
                    content = data["choices"][0]["message"]["content"]
                except Exception:
                    raise ProviderErr(f"{cfg.provider_id}: ungültige Antwortstruktur")
                usage = ((data.get("usage") or {}).get("total_tokens") or RateLimiter.estimate_tokens(content))
                return content, usage
        except aiohttp.ClientConnectorError:
            if allows_missing_api_key(cfg):
                raise ProviderErr(
                    f"{cfg.provider_id}: Local LLM nicht erreichbar ({cfg.base_url}). "
                    "Starte LM Studio / vLLM / SGLang / llama.cpp server."
                )
            raise ProviderErr(f"{cfg.provider_id}: Endpoint nicht erreichbar")
        except asyncio.TimeoutError:
            raise ProviderErr(f"{cfg.provider_id}: Timeout")

    async def _openrouter(self, cfg: ProviderConfig, prompt: str, system: str, model_override: Optional[str] = None) -> tuple[str, int]:
        if not cfg.api_key:
            raise ProviderErr("OpenRouter: API-Key fehlt")
        session = await self._get_session()
        payload = {
            "model": model_override or cfg.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "max_tokens": 1200,
            "temperature": 0.3,
        }
        headers = {
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        }
        from os import getenv
        referer = getenv("OPENROUTER_HTTP_REFERER", "")
        title = getenv("OPENROUTER_APP_TITLE", "Isaac")
        if referer:
            headers["HTTP-Referer"] = referer
        if title:
            headers["X-Title"] = title
        for k, v in (cfg.extra_headers or {}).items():
            if k and isinstance(k, str):
                headers[k] = str(v)
        async with session.post(
            cfg.base_url,
            headers=headers,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=cfg.timeout),
        ) as r:
            if r.status == 429:
                raise RateLimitErr("OpenRouter Rate Limit")
            if r.status != 200:
                txt = await r.text()
                raise ProviderErr(f"OpenRouter {r.status}: {txt[:160]}")
            data = await r.json()
            try:
                content = data["choices"][0]["message"]["content"]
            except Exception:
                raise ProviderErr("OpenRouter: ungültige Antwortstruktur")
            usage = ((data.get("usage") or {}).get("total_tokens") or RateLimiter.estimate_tokens(content))
            return content, usage

    async def _anthropic(self, cfg: ProviderConfig, prompt: str, system: str, model_override: Optional[str] = None) -> tuple[str, int]:
        if not cfg.api_key:
            raise ProviderErr("Anthropic: API-Key fehlt")
        session = await self._get_session()
        async with session.post(
            cfg.base_url,
            headers={"x-api-key": cfg.api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
            json={"model": model_override or cfg.model, "max_tokens": 1200, "system": system, "messages": [{"role": "user", "content": prompt}]},
            timeout=aiohttp.ClientTimeout(total=cfg.timeout),
        ) as r:
            if r.status == 429:
                raise RateLimitErr("Anthropic Rate Limit")
            if r.status != 200:
                txt = await r.text()
                raise ProviderErr(f"Anthropic {r.status}: {txt[:120]}")
            data = await r.json()
            try:
                content = data["content"][0]["text"]
            except Exception:
                raise ProviderErr("Anthropic: ungültige Antwortstruktur")
            usage = ((data.get("usage") or {}).get("input_tokens", 0) + (data.get("usage") or {}).get("output_tokens", 0) or RateLimiter.estimate_tokens(content))
            return content, usage

    async def _gemini(self, cfg: ProviderConfig, prompt: str, system: str, model_override: Optional[str] = None) -> tuple[str, int]:
        if not cfg.api_key:
            raise ProviderErr("Gemini: API-Key fehlt")
        model = (model_override or cfg.model or "").strip()
        if model.startswith("models/"):
            model = model[len("models/"):]
        base = (cfg.base_url or "https://generativelanguage.googleapis.com/v1beta/models").rstrip("/")
        url = f"{base}/{model}:generateContent?key={cfg.api_key}"
        # AI Studio / Generative Language API: systemInstruction + role-aware contents
        payload: dict = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            # Thinking-Modelle (Gemini 3.x) verbrauchen Output-Tokens intern — Budget nicht zu knapp
            "generationConfig": {"maxOutputTokens": 2048, "temperature": 0.3},
        }
        if system and system.strip():
            payload["systemInstruction"] = {"parts": [{"text": system.strip()}]}
        session = await self._get_session()
        async with session.post(
            url,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=cfg.timeout),
        ) as r:
            if r.status == 429:
                raise RateLimitErr("Gemini Rate Limit")
            if r.status != 200:
                txt = await r.text()
                raise ProviderErr(f"Gemini {r.status}: {txt[:120]}")
            data = await r.json()
            try:
                parts = data["candidates"][0]["content"]["parts"]
                # Gemini-3 kann thought-Parts liefern; nur sichtbaren Text nehmen
                texts = []
                for p in parts:
                    if not isinstance(p, dict):
                        continue
                    if p.get("thought") is True:
                        continue
                    t = p.get("text")
                    if t:
                        texts.append(t)
                content = "".join(texts).strip()
                if not content:
                    # Fallback: alle Textteile inkl. thought
                    content = "".join(
                        (p.get("text") or "") for p in parts if isinstance(p, dict)
                    ).strip()
                if not content:
                    raise ValueError("empty parts")
            except Exception:
                raise ProviderErr("Gemini: ungültige Antwortstruktur")
            usage = (((data.get("usageMetadata") or {}).get("totalTokenCount")) or RateLimiter.estimate_tokens(content))
            return content, usage
    async def _cohere(self, cfg: ProviderConfig, prompt: str, system: str, model_override: Optional[str] = None) -> tuple[str, int]:
        if not cfg.api_key:
            raise ProviderErr("Cohere: API-Key fehlt")
        session = await self._get_session()
        async with session.post(
            cfg.base_url,
            headers={"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"},
            json={"model": model_override or cfg.model, "preamble": system, "messages": [{"role": "user", "content": prompt}], "max_tokens": 1200, "temperature": 0.3},
            timeout=aiohttp.ClientTimeout(total=cfg.timeout),
        ) as r:
            if r.status == 429:
                raise RateLimitErr("Cohere Rate Limit")
            if r.status != 200:
                txt = await r.text()
                raise ProviderErr(f"Cohere {r.status}: {txt[:120]}")
            data = await r.json()
            try:
                content = data["message"]["content"][0]["text"]
            except (KeyError, IndexError, TypeError):
                content = data.get("text", "[Cohere: leere Antwort]")
            usage = ((((data.get("usage") or {}).get("tokens") or {}).get("output_tokens")) or RateLimiter.estimate_tokens(content))
            return content, usage

    async def _ollama(self, cfg: ProviderConfig, prompt: str, system: str, model_override: Optional[str] = None) -> tuple[str, int]:
        session = await self._get_session()
        use_model = model_override or cfg.model
        try:
            tags_url = cfg.base_url.rsplit("/api/chat", 1)[0] + "/api/tags"
            async with session.get(tags_url, timeout=aiohttp.ClientTimeout(total=10)) as tag_resp:
                if tag_resp.status == 200:
                    tag_data = await tag_resp.json()
                    available = {m.get("name", "") for m in tag_data.get("models", [])}
                    if available and use_model not in available and f"{use_model}:latest" not in available:
                        raise ProviderErr(f"Ollama-Modell '{use_model}' nicht installiert")
            async with session.post(
                cfg.base_url,
                json={"model": use_model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}], "stream": False, "options": {"temperature": 0.25, "num_predict": 320}},
                timeout=aiohttp.ClientTimeout(total=max(60, cfg.timeout)),
            ) as r:
                txt = await r.text()
                if r.status != 200:
                    raise ProviderErr(f"Ollama {r.status}: {txt[:200]}")
                import json as _json
                data = _json.loads(txt)
                msg = ((data.get("message") or {}).get("content") or "").strip()
                if not msg:
                    raise ProviderErr("Ollama lieferte leere Antwort")
                usage = (data.get("prompt_eval_count", 0) + data.get("eval_count", 0) or RateLimiter.estimate_tokens(msg))
                return msg, usage
        except aiohttp.ClientConnectorError:
            raise ProviderErr("Ollama nicht erreichbar. Starte mit: ollama serve")
        except asyncio.TimeoutError:
            raise ProviderErr("Ollama Timeout")

    async def _huggingface(self, cfg: ProviderConfig, prompt: str, system: str, model_override: Optional[str] = None) -> tuple[str, int]:
        if not cfg.api_key:
            raise ProviderErr("HuggingFace: API-Key fehlt")
        url = f"{cfg.base_url}/{model_override or cfg.model}"
        full = f"{system}\n\nUser: {prompt}\nAssistant:"
        session = await self._get_session()
        async with session.post(
            url,
            headers={"Authorization": f"Bearer {cfg.api_key}"},
            json={"inputs": full, "parameters": {"max_new_tokens": 800, "temperature": 0.3, "return_full_text": False}},
            timeout=aiohttp.ClientTimeout(total=cfg.timeout),
        ) as r:
            if r.status == 429:
                raise RateLimitErr("HuggingFace Rate Limit")
            if r.status == 503:
                raise ProviderErr("HuggingFace: Modell lädt")
            if r.status != 200:
                txt = await r.text()
                raise ProviderErr(f"HuggingFace {r.status}: {txt[:120]}")
            data = await r.json()
            if isinstance(data, list):
                content = data[0].get("generated_text", "")
            else:
                content = str(data)[:500]
            return content, RateLimiter.estimate_tokens(content)

    def provider_status(self) -> list[dict]:
        status = []
        for name, pcfg in self.cfg.providers.items():
            lim = self._limiters.get(name)
            health = self._health.get(name, ProviderHealth())
            status.append({
                "name": name,
                "provider_id": name,
                "display_name": pcfg.display_name,
                "provider_type": pcfg.provider_type,
                "available": self._is_runtime_available(pcfg),
                "allowed": self.cfg.is_provider_allowed(name),
                "model": pcfg.model,
                "rate": lim.status() if lim else "-",
                "primary": name == self.cfg.relay.primary_provider,
                "is_default": bool(pcfg.is_default),
                "enabled": pcfg.enabled,
                "has_api_key": bool(pcfg.api_key),
                "last_error": health.last_error,
                "cooldown": max(0, int(health.blacklisted_until - time.monotonic())),
                "latency": round(health.last_success_latency, 2),
                "success_count": health.success_count,
                "failure_count": health.failure_count,
                "base_url": pcfg.base_url,
                "timeout": pcfg.timeout,
            })
        return status


class RateLimitErr(Exception):
    pass


class ProviderErr(Exception):
    pass


_relay: Optional[AsyncRelay] = None


def get_relay() -> AsyncRelay:
    global _relay
    if _relay is None:
        _relay = AsyncRelay()
    return _relay
