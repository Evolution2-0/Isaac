"""Optional Letta adapter (CLI companion + Cloud memory API).

Roles (bounded, not kernel):
  * Cloud REST (`https://api.letta.com`) — archival/core memory search + optional write
  * Local context files under `.letta/` — read-only snippets
  * Explicit `letta:` run — Cloud messages if credits allow, else CLI

Never replaces `memory.py` / kernel orchestration.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from config import DATA_DIR
from external_memory.config import ExternalMemoryConfig

log = logging.getLogger("Isaac.ExternalMemory.Letta")

_LETTA_CONTEXT_GLOBS = (
    "MEMORY.md",
    "memory.md",
    "AGENTS.md",
    "CONTEXT.md",
)

_STATE_PATH = DATA_DIR / "letta_state.json"
_DEFAULT_TIMEOUT = 20.0


class LettaAdapter:
    name = "letta"

    def __init__(self, cfg: ExternalMemoryConfig):
        self._cfg = cfg
        self._bin_path: str | None = None
        self._init_error = ""
        self._tried = False
        self._version = ""
        self._mode = ""  # "cloud" | "cli" | "local_files" | ""
        self._agent_id = (cfg.letta_agent_id or "").strip()
        self._cloud_ok = False

    # ── availability ──────────────────────────────────────────────────────

    def available(self) -> bool:
        if not self._cfg.letta_enabled:
            return False
        self._ensure()
        if self._cloud_ok:
            return True
        if self._bin_path:
            return True
        # enabled but only local file search — still useful for retrieval
        return self._mode == "local_files" or bool(self._cfg.letta_enabled)

    def _cloud_configured(self) -> bool:
        return bool(self._cfg.letta_api_key) and bool(self._cfg.letta_base_url)

    def _ensure(self) -> None:
        if self._tried:
            return
        self._tried = True
        if not self._cfg.letta_enabled:
            return

        # Prefer Cloud when key present
        if self._cloud_configured():
            if not self._cfg.letta_allow_cloud:
                self._init_error = (
                    "LETTA_API_KEY set but ISAAC_LETTA_ALLOW_CLOUD is off"
                )
                log.info("Letta cloud blocked: %s", self._init_error)
            else:
                os.environ.setdefault("LETTA_API_KEY", self._cfg.letta_api_key)
                os.environ.setdefault("LETTA_BASE_URL", self._cfg.letta_base_url)
                try:
                    health = self._http("GET", "/v1/health", timeout=8.0)
                    if health.get("status") == "ok" or health.get("version"):
                        self._cloud_ok = True
                        self._mode = "cloud"
                        self._agent_id = self._resolve_agent_id()
                        log.info(
                            "Letta adapter online (cloud=%s agent=%s)",
                            self._cfg.letta_base_url,
                            (self._agent_id or "")[:40] or "—",
                        )
                    else:
                        self._init_error = f"Letta health unexpected: {health!r}"[:200]
                except Exception as exc:
                    self._init_error = f"Letta cloud unreachable: {exc}"[:240]
                    log.info("Letta cloud init failed: %s", self._init_error)

        # CLI companion (optional, parallel)
        candidate = (self._cfg.letta_bin or "letta").strip()
        path = shutil.which(candidate) if not os.path.isabs(candidate) else candidate
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            self._bin_path = path
            self._version = self._probe_version(path)
            if not self._mode:
                self._mode = "cli"
            log.info("Letta CLI found: %s (%s)", path, self._version or "unknown")
        elif os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            self._bin_path = candidate
            self._version = self._probe_version(candidate)
            if not self._mode:
                self._mode = "cli"

        if not self._cloud_ok and not self._bin_path:
            if not self._init_error:
                self._init_error = (
                    "no Letta cloud key and no letta binary; "
                    "set LETTA_API_KEY + ISAAC_LETTA_ALLOW_CLOUD=1 "
                    "or install: npm i -g @letta-ai/letta-code"
                )
            # still allow local file snippets
            self._mode = self._mode or "local_files"
            log.info("Letta partial: %s", self._init_error)

    @staticmethod
    def _probe_version(bin_path: str) -> str:
        try:
            proc = subprocess.run(
                [bin_path, "--version"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            out = (proc.stdout or proc.stderr or "").strip()
            return out.splitlines()[0][:120] if out else ""
        except Exception:
            return ""

    # ── HTTP helpers ──────────────────────────────────────────────────────

    def _http(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> Any:
        url = f"{self._cfg.letta_base_url.rstrip('/')}{path}"
        data = None
        headers = {
            "Authorization": f"Bearer {self._cfg.letta_api_key}",
            "Accept": "application/json",
            # Cloudflare Error 1010 blocks default Python-urllib UA
            "User-Agent": "Isaac-LettaAdapter/5.3 (+https://github.com/hansheute88/isaac)",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                if not raw.strip():
                    return {}
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            err_body = ""
            try:
                err_body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            detail: Any
            try:
                detail = json.loads(err_body) if err_body else {"error": str(exc)}
            except Exception:
                detail = {"error": err_body[:400] or str(exc)}
            raise RuntimeError(
                f"HTTP {exc.code}: {detail.get('error') or detail.get('reason_text') or detail}"
            ) from exc

    def _load_state(self) -> dict[str, Any]:
        try:
            if _STATE_PATH.is_file():
                return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save_state(self, data: dict[str, Any]) -> None:
        try:
            _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            prev = self._load_state()
            prev.update(data)
            _STATE_PATH.write_text(
                json.dumps(prev, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception as exc:
            log.debug("letta state save failed: %s", exc)

    def _resolve_agent_id(self) -> str:
        if self._agent_id:
            return self._agent_id
        state = self._load_state()
        cached = str(state.get("agent_id") or "").strip()
        if cached:
            self._agent_id = cached
            return cached

        want = (self._cfg.letta_agent_name or "isaac").strip().lower()
        try:
            agents = self._http("GET", "/v1/agents/", timeout=15.0)
            items = agents if isinstance(agents, list) else (agents.get("agents") or [])
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip().lower()
                aid = str(item.get("id") or "").strip()
                if aid and name in {want, "isaac", "isaac-probe", "isaac-companion"}:
                    # prefer exact want name
                    if name == want or name == "isaac":
                        self._agent_id = aid
                        self._save_state({"agent_id": aid, "agent_name": name})
                        return aid
            # second pass: any isaac-*
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip().lower()
                aid = str(item.get("id") or "").strip()
                if aid and name.startswith("isaac"):
                    self._agent_id = aid
                    self._save_state({"agent_id": aid, "agent_name": name})
                    return aid
        except Exception as exc:
            log.debug("list agents failed: %s", exc)

        # Create companion agent (no tools — memory companion only)
        try:
            created = self._http(
                "POST",
                "/v1/agents/",
                body={
                    "name": self._cfg.letta_agent_name or "isaac",
                    "model": self._cfg.letta_model,
                    "embedding": self._cfg.letta_embedding,
                    "description": "Isaac external memory companion (bounded, not kernel)",
                    "memory_blocks": [
                        {
                            "label": "persona",
                            "value": (
                                "You are a bounded memory companion for Isaac, "
                                "a local cognitive kernel. You store owner facts "
                                "and preferences. You do not replace Isaac's kernel."
                            ),
                        },
                        {
                            "label": "human",
                            "value": f"Owner is {self._cfg.owner_id}.",
                        },
                    ],
                },
                timeout=45.0,
            )
            aid = str((created or {}).get("id") or "").strip()
            if aid:
                self._agent_id = aid
                self._save_state(
                    {
                        "agent_id": aid,
                        "agent_name": self._cfg.letta_agent_name or "isaac",
                    }
                )
                log.info("Letta agent created: %s", aid)
                return aid
        except Exception as exc:
            log.info("Letta agent create failed (memory search may still work later): %s", exc)
        return ""

    # ── search ────────────────────────────────────────────────────────────

    def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        """Read-only retrieval: local files + Cloud archival/core memory."""
        if not self._cfg.letta_enabled or not (query or "").strip():
            return []
        self._ensure()
        hits: list[dict[str, Any]] = []
        hits.extend(self._search_local_files(query, limit=limit))
        if self._cloud_ok:
            hits.extend(self._search_cloud(query, limit=limit))
        # de-dupe by text prefix
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for h in hits:
            key = (h.get("text") or "")[:160]
            if key in seen:
                continue
            seen.add(key)
            out.append(h)
            if len(out) >= limit:
                break
        return out

    def _search_local_files(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        roots = [Path.cwd() / ".letta", Path.cwd()]
        q = query.lower()
        seen: set[str] = set()
        for root in roots:
            if not root.is_dir():
                continue
            for name in _LETTA_CONTEXT_GLOBS:
                path = root / name
                key = str(path.resolve()) if path.exists() else ""
                if not key or key in seen:
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                seen.add(key)
                snippet = text.strip()[:400]
                if not snippet:
                    continue
                score = 0.4
                if any(tok in text.lower() for tok in q.split() if len(tok) >= 4):
                    score = 0.7
                hits.append(
                    {
                        "text": f"[letta:{name}] {snippet}",
                        "source": self.name,
                        "kind": "file",
                        "label": name,
                        "score": score,
                        "path": str(path),
                    }
                )
                if len(hits) >= limit:
                    return hits
        return hits

    def _search_cloud(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        agent_id = self._agent_id or self._resolve_agent_id()
        # 1) semantic passages search
        try:
            body: dict[str, Any] = {"query": query, "limit": max(1, min(limit, 20))}
            if agent_id:
                body["agent_id"] = agent_id
            data = self._http("POST", "/v1/passages/search", body=body, timeout=15.0)
            items = data if isinstance(data, list) else (data.get("results") or data.get("passages") or [])
            for item in items:
                if not isinstance(item, dict):
                    continue
                passage = item.get("passage") if isinstance(item.get("passage"), dict) else item
                text = str((passage or {}).get("text") or "").strip()
                if not text:
                    continue
                score = item.get("score")
                try:
                    score_f = float(score) if score is not None else 0.75
                except (TypeError, ValueError):
                    score_f = 0.75
                hits.append(
                    {
                        "text": f"[letta:archival] {text[:500]}",
                        "source": self.name,
                        "kind": "archival",
                        "label": "archival",
                        "score": min(0.95, max(0.3, score_f if score_f <= 1 else score_f / 100.0)),
                    }
                )
                if len(hits) >= limit:
                    return hits
        except Exception as exc:
            log.debug("letta passages search failed: %s", exc)

        # 2) core-memory blocks (cheap, always relevant for owner facts)
        if agent_id:
            try:
                core = self._http(
                    "GET",
                    f"/v1/agents/{agent_id}/core-memory",
                    timeout=12.0,
                )
                blocks = []
                if isinstance(core, dict):
                    blocks = core.get("blocks") or []
                    if not blocks and isinstance(core.get("memory"), dict):
                        blocks = (core["memory"].get("blocks") or [])
                q = query.lower()
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    label = str(block.get("label") or "block")
                    value = str(block.get("value") or "").strip()
                    if not value:
                        continue
                    score = 0.45
                    if any(tok in value.lower() for tok in q.split() if len(tok) >= 3):
                        score = 0.72
                    hits.append(
                        {
                            "text": f"[letta:core:{label}] {value[:400]}",
                            "source": self.name,
                            "kind": "core",
                            "label": label,
                            "score": score,
                        }
                    )
                    if len(hits) >= limit:
                        break
            except Exception as exc:
                log.debug("letta core-memory failed: %s", exc)
        return hits

    # ── remember ──────────────────────────────────────────────────────────

    def remember(
        self,
        messages: list[dict[str, Any]],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Write turn into Cloud archival memory when write + cloud enabled."""
        if not self._cfg.letta_enabled or not self._cfg.write_enabled:
            return False
        self._ensure()
        if not self._cloud_ok:
            return False
        agent_id = self._agent_id or self._resolve_agent_id()
        if not agent_id:
            return False
        parts: list[str] = []
        for m in messages or []:
            if not isinstance(m, dict):
                continue
            role = str(m.get("role") or "user")
            content = str(m.get("content") or m.get("text") or "").strip()
            if content:
                parts.append(f"{role}: {content}")
        text = "\n".join(parts).strip()
        if not text:
            return False
        text = text[:4000]
        tags = ["isaac"]
        if metadata:
            if metadata.get("goal_id"):
                tags.append(f"goal:{metadata.get('goal_id')}")
            if metadata.get("source"):
                tags.append(str(metadata.get("source"))[:40])
        try:
            self._http(
                "POST",
                f"/v1/agents/{agent_id}/archival-memory",
                body={"text": text, "tags": tags},
                timeout=max(5.0, float(self._cfg.write_timeout_s or 10.0)),
            )
            return True
        except Exception as exc:
            log.debug("letta remember failed: %s", exc)
            return False

    # ── explicit run ──────────────────────────────────────────────────────

    def run(
        self,
        prompt: str,
        *,
        cwd: str | None = None,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        """Explicit owner-triggered companion run (Cloud messages, else CLI)."""
        self._ensure()
        prompt = (prompt or "").strip()
        if not prompt:
            return {"ok": False, "error": "empty prompt", "source": self.name}

        if self._cloud_ok:
            cloud = self._run_cloud(prompt, timeout=timeout)
            if cloud.get("ok"):
                return cloud
            err_l = str(cloud.get("error") or "").lower()
            # Billing/credits: fail-soft with clear cloud error (no CLI hang)
            if any(x in err_l for x in ("credit", "402", "rate limited", "not-enough")):
                log.info("Letta cloud run blocked by credits/rate-limit")
                return cloud
            if not self._bin_path:
                return cloud
            log.info("Letta cloud run failed (%s); trying CLI", err_l[:80])

        if not self._bin_path:
            return {
                "ok": False,
                "error": self._init_error or "Letta CLI/cloud not available",
                "source": self.name,
            }
        return self._run_cli(prompt, cwd=cwd, timeout=timeout)

    def _run_cloud(self, prompt: str, *, timeout: float) -> dict[str, Any]:
        agent_id = self._agent_id or self._resolve_agent_id()
        if not agent_id:
            return {
                "ok": False,
                "error": "no Letta agent_id (create failed or LETTA_AGENT_ID unset)",
                "source": self.name,
                "mode": "cloud",
            }
        try:
            data = self._http(
                "POST",
                f"/v1/agents/{agent_id}/messages",
                body={"input": prompt},
                timeout=max(15.0, float(timeout)),
            )
            text = self._extract_assistant_text(data)
            return {
                "ok": True,
                "text": (text or "")[:8000],
                "source": self.name,
                "mode": "cloud",
                "agent_id": agent_id,
                "error": "",
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc)[:500],
                "source": self.name,
                "mode": "cloud",
                "agent_id": agent_id,
            }

    @staticmethod
    def _extract_assistant_text(data: Any) -> str:
        if data is None:
            return ""
        if isinstance(data, str):
            return data
        if not isinstance(data, dict):
            return str(data)[:4000]
        # common shapes: {messages: [...]}, {content: ...}
        messages = data.get("messages")
        if isinstance(messages, list):
            parts: list[str] = []
            for m in messages:
                if not isinstance(m, dict):
                    continue
                role = str(m.get("role") or m.get("message_type") or "")
                if role and role not in {
                    "assistant",
                    "assistant_message",
                    "letta_message",
                    "",
                }:
                    # keep assistant + tool-less final replies
                    if "assistant" not in role and role not in {"agent", "system"}:
                        if role in {"user", "tool", "function"}:
                            continue
                content = m.get("content")
                if isinstance(content, str) and content.strip():
                    parts.append(content.strip())
                elif isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") in (None, "text"):
                            t = str(c.get("text") or "").strip()
                            if t:
                                parts.append(t)
                        elif isinstance(c, str) and c.strip():
                            parts.append(c.strip())
                text = m.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
            if parts:
                return "\n".join(parts)
        for key in ("content", "text", "response", "output"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return json.dumps(data, ensure_ascii=False)[:2000]

    def _run_cli(
        self,
        prompt: str,
        *,
        cwd: str | None,
        timeout: float,
    ) -> dict[str, Any]:
        workdir = cwd or os.getcwd()
        env = {
            **os.environ,
            "CI": "1",
            "NO_COLOR": "1",
        }
        if self._cfg.letta_api_key:
            env.setdefault("LETTA_API_KEY", self._cfg.letta_api_key)
        try:
            proc = subprocess.run(
                [self._bin_path, "-p", prompt],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=env,
            )
            stdout = (proc.stdout or "").strip()
            stderr = (proc.stderr or "").strip()
            if proc.returncode != 0 and (
                "unknown" in stderr.lower() or "unrecognized" in stderr.lower()
            ):
                proc = subprocess.run(
                    [self._bin_path, prompt],
                    cwd=workdir,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                    env=env,
                )
                stdout = (proc.stdout or "").strip()
                stderr = (proc.stderr or "").strip()
            text = stdout or stderr
            return {
                "ok": proc.returncode == 0,
                "text": text[:8000],
                "returncode": proc.returncode,
                "source": self.name,
                "mode": "cli",
                "error": "" if proc.returncode == 0 else (stderr[:500] or "letta failed"),
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "error": f"letta timed out after {timeout}s",
                "source": self.name,
                "mode": "cli",
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc), "source": self.name, "mode": "cli"}

    def status(self) -> dict[str, Any]:
        avail = self.available()
        return {
            "name": self.name,
            "enabled": self._cfg.letta_enabled,
            "available": avail,
            "mode": self._mode or ("cloud" if self._cloud_ok else ""),
            "cloud_ok": self._cloud_ok,
            "allow_cloud": self._cfg.letta_allow_cloud,
            "api_key_set": bool(self._cfg.letta_api_key),
            "base_url": self._cfg.letta_base_url if self._cfg.letta_allow_cloud else "",
            "agent_id": self._agent_id or "",
            "init_error": self._init_error,
            "bin": self._bin_path or self._cfg.letta_bin,
            "version": self._version,
            "write_enabled": self._cfg.write_enabled,
        }
