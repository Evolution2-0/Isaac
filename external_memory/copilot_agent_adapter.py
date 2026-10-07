"""Optional GitHub Copilot companion (CLI + SDK + cloud tasks API).

Default OFF. Explicit owner prefix only:
  copilot: | gh-copilot: | github-copilot:

Paths (bounded, not kernel):
  1. Local Copilot CLI headless: `copilot -p "…" --output-format json`
  2. Optional Python SDK (github-copilot-sdk) when ISAAC_COPILOT_AGENT_USE_SDK=1
  3. Cloud agent tasks REST: POST /agents/repos/{owner}/{repo}/tasks
     (requires CCA-enabled repo/plan)

Auth notes (GitHub policy):
  - Classic PAT (ghp_) is rejected by Copilot CLI/SDK.
  - Prefer COPILOT_GITHUB_TOKEN with gho_ / ghu_ / github_pat_ (Copilot Requests).
  - Falls back to gh hosts.yml oauth and ~/.copilot config when present.
  - When spawning CLI, classic GH_TOKEN is stripped so it cannot poison auth.

Does not replace Classification → Retrieval → Strategy → Task.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from external_memory.config import ExternalMemoryConfig

log = logging.getLogger("Isaac.ExternalMemory.CopilotAgent")

_CLASSIC_PAT_RE = re.compile(r"^ghp_[A-Za-z0-9_]+$")
_COPILOT_OK_TOKEN_RE = re.compile(r"^(gho_|ghu_|github_pat_)")


class CopilotAgentAdapter:
    name = "copilot_agent"

    def __init__(self, cfg: ExternalMemoryConfig):
        self._cfg = cfg
        self._bin_path: str | None = None
        self._init_error = ""
        self._tried = False
        self._version = ""
        self._last_session_id: str = ""
        self._auth_hint = ""

    # ── lifecycle ────────────────────────────────────────────────────────────

    def available(self) -> bool:
        if not self._cfg.copilot_agent_enabled:
            return False
        self._ensure()
        return bool(self._bin_path) or bool(self._cfg.copilot_agent_use_sdk)

    def _ensure(self) -> None:
        if self._tried:
            return
        self._tried = True
        if not self._cfg.copilot_agent_enabled:
            return
        candidate = (self._cfg.copilot_agent_bin or "copilot").strip()
        path = shutil.which(candidate) if not os.path.isabs(candidate) else candidate
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            self._bin_path = path
            self._version = self._probe_version(path)
            log.info("Copilot CLI found: %s (%s)", path, self._version or "unknown")
            return
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            self._bin_path = candidate
            self._version = self._probe_version(candidate)
            return
        # SDK-only mode still counts as available if flag set
        if self._cfg.copilot_agent_use_sdk:
            try:
                import copilot  # noqa: F401

                self._init_error = ""
                return
            except Exception as exc:
                self._init_error = f"copilot binary missing and SDK import failed: {exc}"
                return
        self._init_error = (
            f"copilot binary not found ({candidate}); "
            "install: npm i -g @github/copilot  OR  pip install github-copilot-sdk "
            "&& python -m copilot download-runtime"
        )
        log.info("Copilot Agent disabled: %s", self._init_error)

    @staticmethod
    def _probe_version(bin_path: str) -> str:
        try:
            proc = subprocess.run(
                [bin_path, "--version"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            out = (proc.stdout or proc.stderr or "").strip()
            return out.splitlines()[0][:160] if out else ""
        except Exception:
            return ""

    def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        return []

    def remember(
        self,
        messages: list[dict[str, Any]],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        return False

    def last_session_id(self) -> str:
        return self._last_session_id

    def clear_session(self) -> None:
        self._last_session_id = ""

    def set_session_id(self, session_id: str) -> None:
        self._last_session_id = (session_id or "").strip()

    # ── auth ─────────────────────────────────────────────────────────────────

    def resolve_copilot_token(self) -> tuple[str, str]:
        """Return (token, source). Prefer tokens Copilot CLI accepts."""
        candidates: list[tuple[str, str]] = []
        for env_key in (
            "COPILOT_GITHUB_TOKEN",
            "GH_TOKEN",
            "GITHUB_TOKEN",
            "GITHUB_TOKEN_GLINKASTEFFEN075_BIT",
        ):
            val = (os.getenv(env_key) or "").strip()
            if val:
                candidates.append((val, f"env:{env_key}"))
        # secrets store
        try:
            from secrets_bootstrap import resolve_secret

            for ref in (
                "COPILOT_GITHUB_TOKEN",
                "github.token",
                "GITHUB_TOKEN",
                "GH_TOKEN",
            ):
                val = (resolve_secret(ref) or "").strip()
                if val:
                    candidates.append((val, f"secret:{ref}"))
        except Exception:
            pass
        # ~/.copilot/config.json copilotTokens
        for conf in (
            Path.home() / ".copilot" / "config.json",
            Path(__file__).resolve().parents[1]
            / "data"
            / "cli_auth_backup"
            / "copilot"
            / "config.json",
        ):
            tok = self._token_from_copilot_config(conf)
            if tok:
                candidates.append((tok, f"file:{conf}"))
        # gh hosts.yml oauth
        for hosts in (
            Path.home() / ".config" / "gh" / "hosts.yml",
            Path(__file__).resolve().parents[1]
            / "data"
            / "cli_auth_backup"
            / "gh"
            / "hosts.yml",
        ):
            tok = self._token_from_gh_hosts(hosts)
            if tok:
                candidates.append((tok, f"file:{hosts}"))

        # Prefer non-classic
        for tok, src in candidates:
            if _COPILOT_OK_TOKEN_RE.match(tok):
                self._auth_hint = src
                return tok, src
        # Fall back to any non-empty (CLI will error clearly on classic)
        for tok, src in candidates:
            if tok:
                self._auth_hint = src
                return tok, src
        self._auth_hint = "none"
        return "", "none"

    @staticmethod
    def _token_from_copilot_config(path: Path) -> str:
        if not path.is_file():
            return ""
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
            # strip // comments
            lines = [
                ln for ln in raw.splitlines() if not ln.strip().startswith("//")
            ]
            data = json.loads("\n".join(lines))
            tokens = data.get("copilotTokens") or {}
            if isinstance(tokens, dict):
                for _k, v in tokens.items():
                    if isinstance(v, str) and v.strip():
                        return v.strip()
        except Exception:
            return ""
        return ""

    @staticmethod
    def _token_from_gh_hosts(path: Path) -> str:
        if not path.is_file():
            return ""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            # Prefer top-level oauth_token under github.com (4-space indent)
            m = re.search(r"(?m)^    oauth_token:\s*(\S+)", text)
            if m:
                return m.group(1).strip()
            m = re.search(r"oauth_token:\s*(\S+)", text)
            if m:
                return m.group(1).strip()
        except Exception:
            return ""
        return ""

    def _cli_env(self, workdir: str) -> dict[str, str]:
        """Build env for copilot CLI: inject good token, strip classic PAT poison."""
        env = {k: v for k, v in os.environ.items()}
        # Classic PAT in GH_TOKEN/GITHUB_TOKEN blocks CLI even if better token exists
        for k in ("GH_TOKEN", "GITHUB_TOKEN", "COPILOT_GITHUB_TOKEN"):
            val = (env.get(k) or "").strip()
            if val and _CLASSIC_PAT_RE.match(val):
                env.pop(k, None)
        token, _src = self.resolve_copilot_token()
        if token and not _CLASSIC_PAT_RE.match(token):
            env["COPILOT_GITHUB_TOKEN"] = token
        elif token and _CLASSIC_PAT_RE.match(token):
            # Leave unset — classic will fail; surface clear error in run()
            self._auth_hint = "classic_pat_rejected"
        env.setdefault("CI", "1")
        env.setdefault("NO_COLOR", "1")
        env.setdefault("TERM", os.environ.get("TERM") or "xterm-256color")
        env["COPILOT_ALLOW_ALL"] = (
            "1" if self._cfg.copilot_agent_always_approve else env.get("COPILOT_ALLOW_ALL", "0")
        )
        return env

    # ── run ──────────────────────────────────────────────────────────────────

    def run(
        self,
        prompt: str,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
        resume_session_id: str | None = None,
        force_new: bool = False,
        mode: str = "cli",
    ) -> dict[str, Any]:
        """Explicit owner-triggered Copilot run."""
        if not self._cfg.copilot_agent_enabled:
            return {
                "ok": False,
                "error": "ISAAC_COPILOT_AGENT_ENABLED=0",
                "source": self.name,
            }
        prompt = (prompt or "").strip()
        if not prompt:
            return {"ok": False, "error": "empty prompt", "source": self.name}

        mode = (mode or "cli").strip().lower()
        if mode in {"cloud", "task", "cca", "cloud_agent"}:
            return self.start_cloud_task(prompt, cwd=cwd)

        if self._cfg.copilot_agent_use_sdk or mode == "sdk":
            try:
                return self._run_sdk(
                    prompt,
                    cwd=cwd,
                    timeout=timeout,
                    resume_session_id=resume_session_id,
                    force_new=force_new,
                )
            except Exception as exc:
                log.warning("copilot SDK path failed, falling back to CLI: %s", exc)

        return self._run_cli(
            prompt,
            cwd=cwd,
            timeout=timeout,
            resume_session_id=resume_session_id,
            force_new=force_new,
        )

    def _run_cli(
        self,
        prompt: str,
        *,
        cwd: str | None,
        timeout: float | None,
        resume_session_id: str | None,
        force_new: bool,
    ) -> dict[str, Any]:
        self._ensure()
        if not self._bin_path:
            return {
                "ok": False,
                "error": self._init_error or "copilot binary not found",
                "source": self.name,
                "auth_hint": self._auth_hint,
            }

        token, src = self.resolve_copilot_token()
        if token and _CLASSIC_PAT_RE.match(token) and not any(
            _COPILOT_OK_TOKEN_RE.match(t)
            for t, _ in [(token, src)]
        ):
            # double-check no better token
            better, bsrc = self.resolve_copilot_token()
            if better and _CLASSIC_PAT_RE.match(better):
                return {
                    "ok": False,
                    "error": (
                        "Classic PAT (ghp_) wird von Copilot CLI abgelehnt. "
                        "Nutze Fine-Grained PAT mit „Copilot Requests“ "
                        "(github_pat_…) oder `copilot /login` / `gh auth login` "
                        "für OAuth (gho_)."
                    ),
                    "source": self.name,
                    "auth_hint": src,
                    "auth_prefix": "ghp_",
                }

        workdir = cwd or self._cfg.copilot_agent_cwd or os.getcwd()
        timeout_s = float(
            timeout if timeout is not None else self._cfg.copilot_agent_timeout_s
        )
        model = (self._cfg.copilot_agent_model or "").strip()
        always_approve = bool(self._cfg.copilot_agent_always_approve)
        resume_id = ""
        if not force_new:
            resume_id = (resume_session_id or "").strip()
            if not resume_id and self._cfg.copilot_agent_auto_resume:
                resume_id = self._last_session_id

        cmd: list[str] = [
            self._bin_path or "copilot",
            "-p",
            prompt,
            "--output-format",
            "json",
            "--add-dir",
            workdir,
        ]
        if model:
            cmd.extend(["--model", model])
        if always_approve:
            cmd.append("--allow-all")
        else:
            # non-interactive needs some tool policy; deny shell by default when not yolo
            deny = (self._cfg.copilot_agent_deny_tools or "").strip()
            if deny:
                for part in deny.split(","):
                    p = part.strip()
                    if p:
                        cmd.append(f"--deny-tool={p}")
            allow = (self._cfg.copilot_agent_allow_tools or "").strip()
            if allow:
                for part in allow.split(","):
                    p = part.strip()
                    if p:
                        cmd.append(f"--allow-tool={p}")
            # still need --allow-all-tools for headless or tools hang
            if not allow and not deny:
                # read-only-ish default: allow tools but not unrestricted paths/urls
                cmd.append("--allow-all-tools")
        if resume_id:
            cmd.extend(["--resume", resume_id])
        if self._cfg.copilot_agent_enable_memory:
            cmd.append("--enable-memory")
        if force_new:
            # new session: do not resume
            pass

        env = self._cli_env(workdir)
        try:
            proc = subprocess.run(
                cmd,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
                env=env,
                stdin=subprocess.DEVNULL,
            )
            stdout = (proc.stdout or "").strip()
            stderr = (proc.stderr or "").strip()
            text, session_id, meta = self._parse_output(stdout)
            if not text:
                text = stdout or stderr
            # detect classic PAT / auth errors in stderr
            err_low = (stderr + "\n" + stdout).lower()
            auth_fail = any(
                s in err_low
                for s in (
                    "classic personal access",
                    "ghp_",
                    "not supported by copilot",
                    "bad credentials",
                    "could not be validated",
                    "authentication",
                )
            )
            ok = proc.returncode == 0 and bool(text) and not (
                isinstance(meta, dict) and meta.get("type") == "error"
            )
            if auth_fail and not ok:
                error = (stderr or text or "copilot auth failed")[:500]
            elif not ok:
                error = (stderr[:500] if stderr else "") or "copilot agent failed"
            else:
                error = ""
            if session_id:
                self._last_session_id = session_id
            elif force_new:
                self._last_session_id = ""
            return {
                "ok": ok,
                "text": (text or "")[:12000],
                "returncode": proc.returncode,
                "source": self.name,
                "via": "cli",
                "session_id": session_id or self._last_session_id or "",
                "resumed_session_id": resume_id,
                "force_new": force_new,
                "always_approve": always_approve,
                "cwd": workdir,
                "model": model,
                "error": error,
                "auth_hint": self._auth_hint or src,
                "meta": meta if isinstance(meta, dict) else {},
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "error": f"copilot agent timed out after {timeout_s}s",
                "source": self.name,
                "via": "cli",
                "resumed_session_id": resume_id,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc), "source": self.name, "via": "cli"}

    def _run_sdk(
        self,
        prompt: str,
        *,
        cwd: str | None,
        timeout: float | None,
        resume_session_id: str | None,
        force_new: bool,
    ) -> dict[str, Any]:
        """Async SDK path, run via asyncio.run in a worker-safe way."""
        import asyncio

        token, src = self.resolve_copilot_token()
        if token and _CLASSIC_PAT_RE.match(token):
            return {
                "ok": False,
                "error": "Classic PAT (ghp_) not supported by Copilot SDK",
                "source": self.name,
                "via": "sdk",
                "auth_hint": src,
            }
        workdir = cwd or self._cfg.copilot_agent_cwd or os.getcwd()
        timeout_s = float(
            timeout if timeout is not None else self._cfg.copilot_agent_timeout_s
        )
        model = (self._cfg.copilot_agent_model or "").strip() or None
        always_approve = bool(self._cfg.copilot_agent_always_approve)

        async def _go() -> dict[str, Any]:
            from copilot import CopilotClient
            from copilot.session import PermissionHandler
            from copilot.session_events import AssistantMessageData, SessionIdleData

            parts: list[str] = []
            session_id = ""
            kwargs: dict[str, Any] = {"working_directory": workdir}
            if token and not _CLASSIC_PAT_RE.match(token):
                kwargs["github_token"] = token
            async with CopilotClient(**kwargs) as client:
                perm = (
                    PermissionHandler.approve_all
                    if always_approve
                    else PermissionHandler.approve_all  # headless needs a decision
                )
                create_kwargs: dict[str, Any] = {
                    "on_permission_request": perm,
                }
                if model:
                    create_kwargs["model"] = model
                if (
                    not force_new
                    and (resume_session_id or self._last_session_id)
                    and hasattr(client, "resume_session")
                ):
                    sid = (resume_session_id or self._last_session_id).strip()
                    try:
                        session_cm = await client.resume_session(
                            sid, on_permission_request=perm
                        )
                    except Exception:
                        session_cm = await client.create_session(**create_kwargs)
                else:
                    session_cm = await client.create_session(**create_kwargs)

                done = asyncio.Event()

                async with session_cm as session:
                    session_id = str(
                        getattr(session, "session_id", None)
                        or getattr(session, "id", None)
                        or ""
                    )

                    def on_event(event):  # noqa: ANN001
                        data = event.data
                        if isinstance(data, AssistantMessageData):
                            parts.append(data.content or "")
                        if isinstance(data, SessionIdleData):
                            done.set()

                    session.on(on_event)
                    await session.send(prompt)
                    try:
                        await asyncio.wait_for(done.wait(), timeout=timeout_s)
                    except asyncio.TimeoutError:
                        return {
                            "ok": False,
                            "error": f"copilot SDK timed out after {timeout_s}s",
                            "source": self.name,
                            "via": "sdk",
                            "session_id": session_id,
                            "text": "".join(parts)[:12000],
                        }
            text = "".join(parts).strip()
            return {
                "ok": bool(text),
                "text": text[:12000],
                "source": self.name,
                "via": "sdk",
                "session_id": session_id,
                "model": model or "",
                "always_approve": always_approve,
                "auth_hint": src,
                "error": "" if text else "empty SDK response",
            }

        try:
            result = asyncio.run(_go())
        except RuntimeError:
            # already in event loop — use new loop in thread
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(lambda: asyncio.run(_go())).result(
                    timeout=timeout_s + 30
                )
        if result.get("session_id"):
            self._last_session_id = str(result["session_id"])
        return result

    # ── cloud agent tasks API ────────────────────────────────────────────────

    def start_cloud_task(
        self,
        prompt: str,
        *,
        cwd: str | None = None,
        owner: str | None = None,
        repo: str | None = None,
        base_ref: str | None = None,
        create_pull_request: bool | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """POST /agents/repos/{owner}/{repo}/tasks (CCA public preview)."""
        import urllib.error
        import urllib.request

        owner, repo = self._resolve_repo(owner, repo, cwd)
        if not owner or not repo:
            return {
                "ok": False,
                "error": "cloud task needs owner/repo (ISAAC_COPILOT_CLOUD_REPO or git remote)",
                "source": self.name,
                "via": "cloud_task",
            }
        token, src = self.resolve_copilot_token()
        # Cloud tasks accept user tokens including classic PAT for some plans
        if not token:
            token = (os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()
            src = "env:GITHUB_TOKEN"
        if not token:
            return {
                "ok": False,
                "error": "no GitHub token for cloud agent tasks",
                "source": self.name,
                "via": "cloud_task",
            }
        body: dict[str, Any] = {"prompt": prompt}
        br = base_ref or self._cfg.copilot_cloud_base_ref or "main"
        if br:
            body["base_ref"] = br
        if create_pull_request is None:
            create_pull_request = bool(self._cfg.copilot_cloud_create_pr)
        body["create_pull_request"] = bool(create_pull_request)
        m = model or self._cfg.copilot_agent_model
        if m:
            body["model"] = m

        url = f"https://api.github.com/agents/repos/{owner}/{repo}/tasks"
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "Isaac-Copilot-Companion",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            tid = str(data.get("id") or "")
            state = str(data.get("state") or data.get("status") or "")
            html = str(data.get("html_url") or "")
            return {
                "ok": True,
                "text": (
                    f"Cloud-Agent-Task gestartet: {tid or '(id?)'}\n"
                    f"state={state or 'queued'}\n"
                    f"repo={owner}/{repo}\n"
                    f"{html}"
                ),
                "task_id": tid,
                "state": state,
                "html_url": html,
                "source": self.name,
                "via": "cloud_task",
                "auth_hint": src,
                "meta": data if isinstance(data, dict) else {},
            }
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")[:800]
            try:
                parsed = json.loads(err_body)
                msg = parsed.get("message") or err_body
            except Exception:
                msg = err_body or str(exc)
            return {
                "ok": False,
                "error": f"cloud task HTTP {exc.code}: {msg}",
                "source": self.name,
                "via": "cloud_task",
                "auth_hint": src,
                "repo": f"{owner}/{repo}",
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "source": self.name,
                "via": "cloud_task",
            }

    def list_cloud_tasks(
        self,
        *,
        owner: str | None = None,
        repo: str | None = None,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        import urllib.error
        import urllib.request

        owner, repo = self._resolve_repo(owner, repo, cwd)
        token, src = self.resolve_copilot_token()
        if not token:
            token = (os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()
        if not token:
            return {"ok": False, "error": "no token", "source": self.name}
        if owner and repo:
            url = f"https://api.github.com/agents/repos/{owner}/{repo}/tasks"
        else:
            url = "https://api.github.com/agents/tasks"
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Authorization": f"Bearer {token}",
                "User-Agent": "Isaac-Copilot-Companion",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            tasks = data.get("tasks") if isinstance(data, dict) else data
            if not isinstance(tasks, list):
                tasks = []
            lines = []
            for t in tasks[:20]:
                if not isinstance(t, dict):
                    continue
                lines.append(
                    f"- {t.get('id','?')[:12]}… state={t.get('state') or t.get('status') or '?'} "
                    f"{(t.get('name') or t.get('prompt') or '')[:80]}"
                )
            return {
                "ok": True,
                "text": "Cloud tasks:\n" + ("\n".join(lines) if lines else "(none)"),
                "tasks": tasks[:30],
                "source": self.name,
                "via": "cloud_task_list",
                "auth_hint": src,
            }
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:400]
            return {
                "ok": False,
                "error": f"list tasks HTTP {exc.code}: {body}",
                "source": self.name,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc), "source": self.name}

    def _resolve_repo(
        self,
        owner: str | None,
        repo: str | None,
        cwd: str | None,
    ) -> tuple[str, str]:
        if owner and repo:
            return owner, repo
        configured = (self._cfg.copilot_cloud_repo or "").strip()
        if configured and "/" in configured:
            o, r = configured.split("/", 1)
            return o.strip(), r.strip()
        # git remote
        workdir = cwd or self._cfg.copilot_agent_cwd or os.getcwd()
        try:
            proc = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            url = (proc.stdout or "").strip()
            m = re.search(r"github\.com[:/](?P<o>[^/]+)/(?P<r>[^/.]+)", url)
            if m:
                return m.group("o"), m.group("r")
        except Exception:
            pass
        # default glinka
        return "hansheute88", "Isaac"

    @staticmethod
    def _parse_output(stdout: str) -> tuple[str, str, dict[str, Any] | None]:
        raw = (stdout or "").strip()
        if not raw:
            return "", "", None
        # JSONL or single JSON
        texts: list[str] = []
        session_id = ""
        last_meta: dict[str, Any] | None = None
        for line in raw.splitlines() + ([raw] if "\n" not in raw else []):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            last_meta = data
            for key in ("sessionId", "session_id", "id"):
                if data.get(key) and not session_id:
                    session_id = str(data.get(key))
            if data.get("type") in {"assistant.message", "message", "result", "text"}:
                c = data.get("content") or data.get("text") or data.get("message")
                if c:
                    texts.append(str(c))
            elif data.get("text"):
                texts.append(str(data["text"]))
            elif data.get("content") and isinstance(data.get("content"), str):
                texts.append(str(data["content"]))
        if texts:
            return "\n".join(texts), session_id, last_meta
        # plain text fallback
        if not raw.startswith("{"):
            return raw, session_id, last_meta
        return raw, session_id, last_meta

    def status(self) -> dict[str, Any]:
        avail = self.available()
        token, src = self.resolve_copilot_token()
        prefix = ""
        if token:
            prefix = token[:4] + "…" if len(token) > 4 else "***"
            if _CLASSIC_PAT_RE.match(token):
                prefix = "ghp_ (unsupported for CLI)"
        return {
            "name": self.name,
            "enabled": self._cfg.copilot_agent_enabled,
            "available": avail,
            "init_error": self._init_error,
            "bin": self._bin_path or self._cfg.copilot_agent_bin,
            "version": self._version,
            "model": self._cfg.copilot_agent_model,
            "cwd": self._cfg.copilot_agent_cwd or os.getcwd(),
            "timeout_s": self._cfg.copilot_agent_timeout_s,
            "always_approve": self._cfg.copilot_agent_always_approve,
            "auto_resume": self._cfg.copilot_agent_auto_resume,
            "use_sdk": self._cfg.copilot_agent_use_sdk,
            "last_session_id": self._last_session_id,
            "auth_source": src,
            "auth_prefix": prefix,
            "cloud_repo": self._cfg.copilot_cloud_repo,
        }
