"""Optional Mem0 adapter — Platform API (preferred) + local OSS fallback.

Modes:
  * platform — ``MemoryClient`` / REST when ``MEM0_API_KEY`` is set
  * local    — ``Memory.from_config`` (Ollama+Chroma or OpenAI embeddings)

Never replaces ``memory.py`` / kernel orchestration.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

from external_memory.config import ExternalMemoryConfig

log = logging.getLogger("Isaac.ExternalMemory.Mem0")

_DEFAULT_PLATFORM = "https://api.mem0.ai"


class Mem0Adapter:
    name = "mem0"

    def __init__(self, cfg: ExternalMemoryConfig):
        self._cfg = cfg
        self._memory: Any = None  # Memory | MemoryClient | None
        self._mode = ""  # "platform" | "local" | ""
        self._init_error = ""
        self._tried = False
        self._use_rest = False  # platform without SDK

    def available(self) -> bool:
        if not self._cfg.mem0_enabled:
            return False
        self._ensure()
        return self._memory is not None or self._use_rest

    def _api_key(self) -> str:
        return (getattr(self._cfg, "mem0_api_key", None) or "").strip() or (
            os.getenv("MEM0_API_KEY") or os.getenv("ISAAC_MEM0_API_KEY") or ""
        ).strip()

    def _platform_base(self) -> str:
        return (
            getattr(self._cfg, "mem0_base_url", None)
            or os.getenv("MEM0_BASE_URL")
            or os.getenv("ISAAC_MEM0_BASE_URL")
            or _DEFAULT_PLATFORM
        ).strip().rstrip("/") or _DEFAULT_PLATFORM

    def _ensure(self) -> None:
        if self._tried:
            return
        self._tried = True
        if not self._cfg.mem0_enabled:
            return

        api_key = self._api_key()
        prefer_platform = bool(api_key) and bool(self._cfg.mem0_allow_cloud)

        if prefer_platform:
            if self._init_platform(api_key):
                return
            # fall through to local if platform fails

        self._init_local()

    def _init_platform(self, api_key: str) -> bool:
        # 1) SDK MemoryClient
        try:
            from mem0 import MemoryClient  # type: ignore

            self._memory = MemoryClient(api_key=api_key)
            self._mode = "platform"
            self._use_rest = False
            # light health: empty search should not raise hard
            try:
                self._memory.search(
                    "health",
                    user_id=self._cfg.owner_id,
                    limit=1,
                )
            except TypeError:
                try:
                    self._memory.search("health", {"user_id": self._cfg.owner_id})
                except Exception:
                    pass
            except Exception as exc:
                # Some accounts return empty; connection errors matter
                if "401" in str(exc) or "Unauthorized" in str(exc):
                    self._init_error = f"Mem0 platform auth failed: {exc}"[:200]
                    self._memory = None
                    return False
            log.info(
                "Mem0 adapter online (platform user_id=%s)",
                self._cfg.owner_id,
            )
            return True
        except ImportError:
            pass
        except Exception as exc:
            self._init_error = f"MemoryClient init: {exc}"[:200]
            log.info("Mem0 MemoryClient failed: %s", exc)

        # 2) REST fallback
        try:
            self._http_platform("GET", "/v1/ping", api_key=api_key, timeout=8.0)
            self._memory = None
            self._use_rest = True
            self._mode = "platform"
            log.info("Mem0 adapter online (platform REST user_id=%s)", self._cfg.owner_id)
            return True
        except Exception as exc:
            # ping may 404; try search endpoint shape
            try:
                self._http_platform(
                    "POST",
                    "/v1/memories/search/",
                    api_key=api_key,
                    body={"query": "health", "user_id": self._cfg.owner_id, "limit": 1},
                    timeout=10.0,
                )
                self._use_rest = True
                self._mode = "platform"
                log.info("Mem0 adapter online (platform REST search)")
                return True
            except Exception as exc2:
                self._init_error = f"Mem0 platform unreachable: {exc2}"[:240]
                log.info("Mem0 platform failed: %s", self._init_error)
                return False

    def _init_local(self) -> None:
        try:
            from mem0 import Memory  # type: ignore
        except ImportError as exc:
            if not self._init_error:
                self._init_error = f"mem0ai not installed: {exc}"
            log.info("Mem0 local disabled (package missing): %s", exc)
            return
        try:
            self._cfg.mem0_dir.mkdir(parents=True, exist_ok=True)
            config = self._build_local_config()
            if config is None:
                if not self._init_error:
                    self._init_error = "no local backend (need Ollama or OPENAI_API_KEY)"
                log.warning("Mem0 enabled but no usable local backend")
                return
            self._memory = Memory.from_config(config)
            self._mode = "local"
            self._use_rest = False
            log.info("Mem0 adapter online (local user_id=%s)", self._cfg.owner_id)
        except Exception as exc:
            self._init_error = str(exc)[:240]
            self._memory = None
            log.warning("Mem0 local init failed: %s", exc)

    def _build_local_config(self) -> dict[str, Any] | None:
        """Ollama+Chroma local; optional OpenAI embeddings when forced."""
        chroma_path = str(self._cfg.mem0_dir / "chroma")
        history_path = str(self._cfg.mem0_dir / "history.db")
        openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        force_openai = (
            os.getenv("ISAAC_MEM0_FORCE_CLOUD", "").strip().lower()
            in {"1", "true", "yes", "on"}
            and bool(openai_key)
        )

        if not force_openai:
            base = self._cfg.ollama_host.rstrip("/")
            return {
                "vector_store": {
                    "provider": "chroma",
                    "config": {
                        "collection_name": "isaac_mem0",
                        "path": chroma_path,
                    },
                },
                "llm": {
                    "provider": "ollama",
                    "config": {
                        "model": self._cfg.ollama_llm,
                        "ollama_base_url": base,
                        "temperature": 0.1,
                    },
                },
                "embedder": {
                    "provider": "ollama",
                    "config": {
                        "model": self._cfg.ollama_embed,
                        "ollama_base_url": base,
                    },
                },
                "history_db_path": history_path,
                "version": "v1.1",
            }

        if openai_key:
            return {
                "vector_store": {
                    "provider": "chroma",
                    "config": {
                        "collection_name": "isaac_mem0",
                        "path": chroma_path,
                    },
                },
                "llm": {
                    "provider": "openai",
                    "config": {"model": "gpt-4o-mini", "temperature": 0.1},
                },
                "embedder": {
                    "provider": "openai",
                    "config": {"model": "text-embedding-3-small"},
                },
                "history_db_path": history_path,
                "version": "v1.1",
            }
        return None

    def _http_platform(
        self,
        method: str,
        path: str,
        *,
        api_key: str,
        body: dict[str, Any] | None = None,
        timeout: float = 15.0,
    ) -> Any:
        url = f"{self._platform_base()}{path}"
        data = None
        headers = {
            "Authorization": f"Token {api_key}",
            "Accept": "application/json",
            "User-Agent": "Isaac-Mem0Adapter/5.3 (+https://github.com/hansheute88/isaac)",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        # Also try Bearer if Token style fails at call site
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                if not raw.strip():
                    return {}
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403} and "Token " in headers.get("Authorization", ""):
                headers["Authorization"] = f"Bearer {api_key}"
                req = urllib.request.Request(url, data=data, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                    return json.loads(raw) if raw.strip() else {}
            raise

    def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        if not self.available() or not (query or "").strip():
            return []
        q = query.strip()
        uid = self._cfg.owner_id

        if self._mode == "platform" and self._use_rest:
            return self._search_rest(q, limit=limit)

        if self._memory is None:
            return []

        try:
            if self._mode == "platform":
                try:
                    raw = self._memory.search(q, user_id=uid, limit=limit)
                except TypeError:
                    raw = self._memory.search(q, {"user_id": uid})
            else:
                try:
                    raw = self._memory.search(q, user_id=uid, limit=limit)
                except TypeError:
                    raw = self._memory.search(q, filters={"user_id": uid})
            return self._normalize_search(raw, limit=limit)
        except Exception as exc:
            log.debug("Mem0 search failed: %s", exc)
            if self._mode == "platform" and self._api_key():
                try:
                    return self._search_rest(q, limit=limit)
                except Exception as exc2:
                    log.debug("Mem0 REST search failed: %s", exc2)
            return []

    def _search_rest(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        api_key = self._api_key()
        if not api_key:
            return []
        for path in ("/v1/memories/search/", "/v1/memories/search"):
            try:
                raw = self._http_platform(
                    "POST",
                    path,
                    api_key=api_key,
                    body={
                        "query": query,
                        "user_id": self._cfg.owner_id,
                        "limit": limit,
                    },
                    timeout=12.0,
                )
                return self._normalize_search(raw, limit=limit)
            except Exception:
                continue
        return []

    def _normalize_search(self, raw: Any, *, limit: int) -> list[dict[str, Any]]:
        items: list[Any]
        if isinstance(raw, dict):
            items = raw.get("results") or raw.get("memories") or []
        elif isinstance(raw, list):
            items = raw
        else:
            items = []
        out: list[dict[str, Any]] = []
        for item in items[:limit]:
            if isinstance(item, dict):
                text = (
                    item.get("memory")
                    or item.get("text")
                    or item.get("data")
                    or ""
                )
                if isinstance(text, dict):
                    text = text.get("memory") or text.get("text") or str(text)
                score = item.get("score")
                try:
                    score_f = float(score) if score is not None else None
                except (TypeError, ValueError):
                    score_f = None
                if str(text).strip():
                    hit: dict[str, Any] = {
                        "text": str(text).strip()[:400],
                        "source": self.name,
                        "mode": self._mode or "unknown",
                    }
                    if score_f is not None:
                        hit["score"] = score_f
                    out.append(hit)
            elif item:
                out.append(
                    {
                        "text": str(item)[:400],
                        "source": self.name,
                        "mode": self._mode or "unknown",
                    }
                )
        return out

    def remember(
        self,
        messages: list[dict[str, Any]],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if not self.available() or not messages:
            return False
        normalized = []
        for m in messages:
            role = (m.get("role") or "user").strip()
            if role in {"steffen", "owner", "user"}:
                role = "user"
            elif role in {"isaac", "assistant", "ai"}:
                role = "assistant"
            content = (m.get("content") or m.get("text") or "").strip()
            if content:
                normalized.append({"role": role, "content": content[:2000]})
        if not normalized:
            return False

        if self._mode == "platform" and self._use_rest:
            return self._remember_rest(normalized, metadata=metadata)

        if self._memory is None:
            return False
        try:
            kwargs: dict[str, Any] = {"user_id": self._cfg.owner_id}
            if metadata:
                kwargs["metadata"] = metadata
            self._memory.add(normalized, **kwargs)
            return True
        except Exception as exc:
            log.debug("Mem0 remember failed: %s", exc)
            if self._mode == "platform" and self._api_key():
                return self._remember_rest(normalized, metadata=metadata)
            return False

    def _remember_rest(
        self,
        messages: list[dict[str, str]],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        api_key = self._api_key()
        if not api_key:
            return False
        body: dict[str, Any] = {
            "messages": messages,
            "user_id": self._cfg.owner_id,
        }
        if metadata:
            body["metadata"] = metadata
        for path in ("/v1/memories/", "/v1/memories"):
            try:
                self._http_platform("POST", path, api_key=api_key, body=body, timeout=15.0)
                return True
            except Exception as exc:
                log.debug("Mem0 REST add %s failed: %s", path, exc)
        return False

    def status(self) -> dict[str, Any]:
        self._ensure() if self._cfg.mem0_enabled else None
        return {
            "name": self.name,
            "enabled": self._cfg.mem0_enabled,
            "available": self.available(),
            "mode": self._mode or ("off" if not self._cfg.mem0_enabled else "init"),
            "platform_key": bool(self._api_key()),
            "allow_cloud": self._cfg.mem0_allow_cloud,
            "init_error": self._init_error,
            "user_id": self._cfg.owner_id,
            "data_dir": str(self._cfg.mem0_dir),
            "base_url": self._platform_base() if self._mode == "platform" else "",
        }
