"""Isaac multi-system automation status + ops memory sync (Stage 0–1).

Connects inspectable status for:
  Isaac · Render · Cognee · Letta · GitHub agents · Sentry

Does not replace the kernel pipeline. Heavy GitHub auto-PR is Stage 3
and stays behind kill-switches (ISAAC_GH_AUTO_PR).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from audit import AuditLog
from config import DATA_DIR

log = logging.getLogger("Isaac.AutomationPipeline")


def _free_cloud() -> bool:
    try:
        from free_cloud import free_cloud_enabled

        return bool(free_cloud_enabled())
    except Exception:
        return False

STATE_PATH = DATA_DIR / "automation_state.json"
DEFAULT_RENDER_URL = "https://isaac-free.onrender.com"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def auto_pipeline_enabled() -> bool:
    """Master switch for periodic ops→memory automation (default off)."""
    return _env_bool("ISAAC_AUTO_PIPELINE", False)


def load_automation_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"runs": {}, "updated": ""}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"runs": {}, "updated": ""}
    except Exception:
        return {"runs": {}, "updated": ""}


def save_automation_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(state or {})
    payload["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    STATE_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        STATE_PATH.chmod(0o600)
    except OSError:
        pass


def _http_json(url: str, *, headers: Optional[dict[str, str]] = None, timeout: float = 12.0) -> tuple[bool, Any]:
    req = urllib.request.Request(url, headers=headers or {"Accept": "application/json", "User-Agent": "Isaac-Automation/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if not raw.strip():
                return True, None
            try:
                return True, json.loads(raw)
            except json.JSONDecodeError:
                return True, raw[:500]
    except Exception as exc:
        return False, str(exc)[:200]


def _probe_render() -> dict[str, Any]:
    base = (
        os.getenv("ISAAC_REMOTE_FREE_URL")
        or os.getenv("RENDER_EXTERNAL_URL")
        or DEFAULT_RENDER_URL
    ).rstrip("/")
    ok, data = _http_json(f"{base}/healthz", timeout=10.0)
    out: dict[str, Any] = {
        "ok": ok and isinstance(data, dict) and bool(data.get("ok")),
        "url": base,
        "error": None if ok else str(data),
    }
    if isinstance(data, dict):
        out["git_commit"] = (data.get("git_commit") or "")[:12] or None
        out["git_branch"] = data.get("git_branch")
        out["active_provider"] = data.get("active_provider")
        out["free_cloud"] = data.get("free_cloud")
        out["keys"] = {
            "groq": data.get("has_groq_key"),
            "gemini": data.get("has_gemini_key"),
            "openrouter": data.get("has_openrouter_key"),
        }
    return out


def _probe_cognee() -> dict[str, Any]:
    try:
        from external_memory import get_external_memory_bridge

        bridge = get_external_memory_bridge()
        st = bridge.cognee.status()
        health_ok = False
        health_err = None
        if st.get("available") and st.get("mode") == "cloud" and st.get("cloud_url"):
            ok, data = _http_json(
                st["cloud_url"].rstrip("/") + "/health",
                headers={
                    "Accept": "application/json",
                    "X-Api-Key": os.getenv("COGNEE_API_KEY") or os.getenv("ISAAC_COGNEE_API_KEY") or "",
                },
                timeout=10.0,
            )
            health_ok = ok and isinstance(data, dict) and data.get("status") == "healthy"
            if not ok:
                health_err = str(data)[:120]
        return {
            "ok": bool(st.get("available")) and (health_ok or st.get("mode") == "local"),
            "enabled": bool(st.get("enabled")),
            "mode": st.get("mode"),
            "available": bool(st.get("available")),
            "cloud_url": st.get("cloud_url"),
            "cloud_key_set": bool(st.get("cloud_key_set")),
            "health_ok": health_ok,
            "init_error": st.get("init_error") or health_err,
            "write_enabled": bool(bridge.cfg.write_enabled),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:160]}


def _probe_letta() -> dict[str, Any]:
    try:
        from external_memory import get_external_memory_bridge

        bridge = get_external_memory_bridge()
        enabled = bool(bridge.cfg.letta_enabled)
        bin_name = (bridge.cfg.letta_bin or "letta").strip()
        path = shutil.which(bin_name) if not os.path.isabs(bin_name) else bin_name
        exists = bool(path and os.path.isfile(path) and os.access(path, os.X_OK))
        return {
            "ok": enabled and exists,
            "enabled": enabled,
            "binary": path or bin_name,
            "binary_found": exists,
            "note": None if exists else "install: npm i -g @letta-ai/letta-code",
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:160]}


def _probe_github() -> dict[str, Any]:
    token = (
        os.getenv("COPILOT_GITHUB_TOKEN")
        or os.getenv("GH_TOKEN")
        or os.getenv("GITHUB_TOKEN")
        or ""
    ).strip()
    out: dict[str, Any] = {
        "ok": False,
        "token_set": bool(token),
        "token_kind": None,
        "login": None,
        "copilot_enabled": _env_bool("ISAAC_COPILOT_AGENT_ENABLED", False),
        "cloud_repo": (os.getenv("ISAAC_COPILOT_CLOUD_REPO") or "").strip() or None,
        "auto_pr": _env_bool("ISAAC_GH_AUTO_PR", False),
        "auto_merge": _env_bool("ISAAC_GH_AUTO_MERGE", False),
        "allowlist": (
            os.getenv("ISAAC_GH_REPO_ALLOWLIST") or "hansheute88/isaac,sc0rp0815/Isaac,sco0rp/IsaacNew"
        ).strip(),
    }
    if not token:
        out["error"] = "no GITHUB_TOKEN / GH_TOKEN / COPILOT_GITHUB_TOKEN"
        return out
    if token.startswith("ghp_"):
        out["token_kind"] = "classic_pat"
    elif token.startswith("github_pat_"):
        out["token_kind"] = "fine_grained"
    elif token.startswith("gho_") or token.startswith("ghu_"):
        out["token_kind"] = "oauth"
    else:
        out["token_kind"] = "other"
    ok, data = _http_json(
        "https://api.github.com/user",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "Isaac-Automation/1.0",
        },
        timeout=10.0,
    )
    if ok and isinstance(data, dict) and data.get("login"):
        out["ok"] = True
        out["login"] = data.get("login")
    else:
        out["error"] = str(data)[:160] if not ok else "unexpected /user payload"
    return out


def _probe_sentry() -> dict[str, Any]:
    dsn_set = bool((os.getenv("SENTRY_DSN") or "").strip())
    token = (os.getenv("SENTRY_AUTH_TOKEN") or "").strip()
    org = (os.getenv("SENTRY_ORG") or "evo20").strip()
    base = (os.getenv("SENTRY_URL") or "https://de.sentry.io").rstrip("/") + "/api/0"
    out: dict[str, Any] = {
        "ok": dsn_set,
        "dsn_set": dsn_set,
        "auth_token_set": bool(token),
        "org": org,
        "unresolved_preview": [],
        "unresolved_count_sample": 0,
    }
    if not token:
        out["note"] = "SENTRY_AUTH_TOKEN missing — ingest only"
        return out
    ok, data = _http_json(
        f"{base}/projects/{org}/isaac/issues/?query=is:unresolved&sort=freq&limit=5",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=15.0,
    )
    if not ok or not isinstance(data, list):
        out["ok"] = dsn_set  # still ok if DSN works for ingest
        out["error"] = str(data)[:160]
        return out
    out["unresolved_count_sample"] = len(data)
    for iss in data[:5]:
        meta = iss.get("metadata") or {}
        title = meta.get("title") or meta.get("value") or iss.get("title") or "?"
        out["unresolved_preview"].append(
            {
                "shortId": iss.get("shortId"),
                "title": str(title)[:100],
                "count": iss.get("count"),
                "level": iss.get("level"),
            }
        )
    out["ok"] = True
    return out


def _probe_isaac_local() -> dict[str, Any]:
    return {
        "ok": True,
        "free_cloud": _free_cloud(),
        "privilege_admin": False,
        "auto_pipeline": auto_pipeline_enabled(),
        "agent_auto_select": _env_bool("ISAAC_AGENT_AUTO_SELECT", False),
    }


def _probe_local_llm() -> dict[str, Any]:
    """Probe Ollama-native or OpenAI-compat local server (iPad app / laptop)."""
    provider = (os.getenv("ACTIVE_PROVIDER") or "").strip().lower()
    ollama_host = (os.getenv("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
    local_url = (os.getenv("LOCAL_LLM_BASE_URL") or "").strip()
    out: dict[str, Any] = {
        "ok": False,
        "active_provider": provider or None,
        "mode": None,
        "endpoint": None,
        "models_hint": None,
        "error": None,
    }
    # Prefer explicit local/ollama provider; still probe common localhost if set
    if provider == "ollama" or (not provider and ollama_host):
        out["mode"] = "ollama"
        out["endpoint"] = ollama_host
        ok, data = _http_json(f"{ollama_host}/api/tags", timeout=5.0)
        if ok and isinstance(data, dict):
            models = data.get("models") or []
            names = []
            for m in models[:5]:
                if isinstance(m, dict):
                    names.append(str(m.get("name") or m.get("model") or "")[:40])
            out["ok"] = True
            out["models_hint"] = names or ["(tags ok, empty list)"]
            out["configured_model"] = (os.getenv("OLLAMA_MODEL") or "").strip() or None
            return out
        out["error"] = str(data)[:120] if not ok else "unexpected /api/tags"
        if provider == "ollama":
            return out
    if provider == "local" or local_url:
        out["mode"] = "openai_compat"
        base = local_url
        if not base:
            out["error"] = "LOCAL_LLM_BASE_URL empty"
            return out
        out["endpoint"] = base
        # derive /v1/models from completions URL when possible
        models_url = base
        if models_url.endswith("/chat/completions"):
            models_url = models_url[: -len("/chat/completions")] + "/models"
        elif "/v1/" in models_url and not models_url.rstrip("/").endswith("models"):
            # leave as-is; try stripping path after v1
            idx = models_url.find("/v1/")
            if idx >= 0:
                models_url = models_url[: idx + 4] + "models"
        headers = {"Accept": "application/json"}
        key = (os.getenv("LOCAL_LLM_API_KEY") or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        ok, data = _http_json(models_url, headers=headers, timeout=5.0)
        if ok:
            out["ok"] = True
            out["configured_model"] = (os.getenv("LOCAL_LLM_MODEL") or "").strip() or None
            if isinstance(data, dict) and isinstance(data.get("data"), list):
                out["models_hint"] = [
                    str((m or {}).get("id") or "")[:40]
                    for m in data.get("data")[:5]
                    if m
                ]
            return out
        out["error"] = str(data)[:120] if not ok else "models probe failed"
        return out
    out["mode"] = "cloud_or_unset"
    out["error"] = "ACTIVE_PROVIDER is not ollama/local (no local probe required)"
    out["ok"] = True  # not a failure when using cloud providers
    return out


def build_automation_status() -> dict[str, Any]:
    """Inspectable multi-system readiness (Stage 0)."""
    try:
        from config import is_owner_equivalent_mode

        admin = is_owner_equivalent_mode()
    except Exception:
        admin = False

    local = _probe_isaac_local()
    local["privilege_admin"] = admin
    local["free_cloud"] = _free_cloud()

    status = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "auto_pipeline": auto_pipeline_enabled(),
        "isaac": local,
        "render": _probe_render(),
        "cognee": _probe_cognee(),
        "letta": _probe_letta(),
        "github": _probe_github(),
        "sentry": _probe_sentry(),
        "local_llm": _probe_local_llm(),
        "flags": {
            "ISAAC_AUTO_PIPELINE": auto_pipeline_enabled(),
            "ISAAC_AUTO_REDEPLOY": _env_bool("ISAAC_AUTO_REDEPLOY", False),
            "ISAAC_GH_AUTO_PR": _env_bool("ISAAC_GH_AUTO_PR", False),
            "ISAAC_GH_AUTO_MERGE": _env_bool("ISAAC_GH_AUTO_MERGE", False),
            "ISAAC_COGNEE_ENABLED": _env_bool("ISAAC_COGNEE_ENABLED", False),
            "ISAAC_LETTA_ENABLED": _env_bool("ISAAC_LETTA_ENABLED", False),
            "ISAAC_COPILOT_AGENT_ENABLED": _env_bool("ISAAC_COPILOT_AGENT_ENABLED", False),
            "ISAAC_AGENT_AUTO_SELECT": _env_bool("ISAAC_AGENT_AUTO_SELECT", False),
            "ACTIVE_PROVIDER": (os.getenv("ACTIVE_PROVIDER") or "").strip() or None,
        },
    }
    # overall readiness: core path
    llm = status.get("local_llm") or {}
    local_llm_ready = bool(llm.get("ok")) and llm.get("mode") in {"ollama", "openai_compat"}
    # If cloud provider active, local_llm probe may be n/a
    if llm.get("mode") == "cloud_or_unset":
        local_llm_ready = False
    status["ready"] = {
        "memory_loop": bool((status["cognee"] or {}).get("ok")),
        "ops_loop": bool((status["render"] or {}).get("ok")) or not _free_cloud(),
        "github_agent": bool((status["github"] or {}).get("ok"))
        and bool(status["flags"].get("ISAAC_COPILOT_AGENT_ENABLED")),
        "aggressive_pr": bool(status["flags"].get("ISAAC_GH_AUTO_PR"))
        and bool((status["github"] or {}).get("ok")),
        "local_llm": local_llm_ready,
    }
    return status


def format_automation_status(status: Optional[dict[str, Any]] = None) -> str:
    st = status or build_automation_status()
    lines = [
        "[Automation Pipeline]",
        f"auto_pipeline={st.get('auto_pipeline')}  ts={st.get('ts')}",
        "",
    ]
    ren = st.get("render") or {}
    lines.append(
        f"Render:  {'OK' if ren.get('ok') else 'FAIL'}  "
        f"commit={ren.get('git_commit') or '-'}  provider={ren.get('active_provider') or '-'}  "
        f"url={ren.get('url') or '-'}"
    )
    if ren.get("error"):
        lines.append(f"         error={ren.get('error')}")

    cog = st.get("cognee") or {}
    lines.append(
        f"Cognee:  {'OK' if cog.get('ok') else 'FAIL'}  mode={cog.get('mode') or '-'}  "
        f"write={cog.get('write_enabled')}  health={cog.get('health_ok')}"
    )
    if cog.get("init_error"):
        lines.append(f"         {cog.get('init_error')}")

    let = st.get("letta") or {}
    lines.append(
        f"Letta:   {'OK' if let.get('ok') else 'OFF/FAIL'}  enabled={let.get('enabled')}  "
        f"bin={let.get('binary') or '-'}"
    )
    if let.get("note"):
        lines.append(f"         {let.get('note')}")

    gh = st.get("github") or {}
    lines.append(
        f"GitHub:  {'OK' if gh.get('ok') else 'FAIL'}  login={gh.get('login') or '-'}  "
        f"token={gh.get('token_kind') or 'none'}  copilot={gh.get('copilot_enabled')}  "
        f"auto_pr={gh.get('auto_pr')}"
    )
    if gh.get("cloud_repo"):
        lines.append(f"         cloud_repo={gh.get('cloud_repo')}  allowlist={gh.get('allowlist')}")
    if gh.get("error"):
        lines.append(f"         error={gh.get('error')}")

    sen = st.get("sentry") or {}
    lines.append(
        f"Sentry:  {'OK' if sen.get('ok') else 'PARTIAL/FAIL'}  dsn={sen.get('dsn_set')}  "
        f"auth={sen.get('auth_token_set')}  unresolved~={sen.get('unresolved_count_sample')}"
    )
    for row in (sen.get("unresolved_preview") or [])[:3]:
        lines.append(
            f"         • {row.get('shortId')}: {row.get('title')} (n={row.get('count')})"
        )

    llm = st.get("local_llm") or {}
    mode = llm.get("mode") or "-"
    if mode == "cloud_or_unset":
        lines.append(
            f"LocalLLM: n/a (ACTIVE_PROVIDER={llm.get('active_provider') or 'unset'}; "
            "use ollama|local for on-device)"
        )
    else:
        lines.append(
            f"LocalLLM: {'OK' if llm.get('ok') else 'FAIL'}  mode={mode}  "
            f"endpoint={llm.get('endpoint') or '-'}  model={llm.get('configured_model') or '-'}"
        )
        if llm.get("models_hint"):
            lines.append(f"         models={', '.join(llm.get('models_hint') or [])}")
        if llm.get("error") and not llm.get("ok"):
            lines.append(f"         error={llm.get('error')}")

    ready = st.get("ready") or {}
    lines.append("")
    lines.append(
        "Ready:  "
        f"memory={ready.get('memory_loop')}  ops={ready.get('ops_loop')}  "
        f"gh_agent={ready.get('github_agent')}  aggressive_pr={ready.get('aggressive_pr')}  "
        f"local_llm={ready.get('local_llm')}"
    )
    lines.append("")
    lines.append(
        "Flags: AUTO_PIPELINE / GH_AUTO_PR / COPILOT / LETTA / COGNEE — "
        "see docs/AUTOMATION_PIPELINE.md"
    )
    return "\n".join(lines)


def build_ops_snapshot_text(status: Optional[dict[str, Any]] = None) -> str:
    """Compact text for Cognee remember (ops memory)."""
    st = status or build_automation_status()
    ren = st.get("render") or {}
    cog = st.get("cognee") or {}
    sen = st.get("sentry") or {}
    gh = st.get("github") or {}
    env = "render" if _free_cloud() else "local"
    parts = [
        f"isaac_ops snapshot env={env} ts={st.get('ts')}",
        f"render_ok={ren.get('ok')} commit={ren.get('git_commit')} provider={ren.get('active_provider')}",
        f"cognee_ok={cog.get('ok')} mode={cog.get('mode')} health={cog.get('health_ok')}",
        f"github_ok={gh.get('ok')} login={gh.get('login')} auto_pr={gh.get('auto_pr')}",
        f"sentry_dsn={sen.get('dsn_set')} unresolved_sample={sen.get('unresolved_count_sample')}",
    ]
    for row in (sen.get("unresolved_preview") or [])[:5]:
        parts.append(
            f"sentry_issue {row.get('shortId')} n={row.get('count')} {row.get('title')}"
        )
    return "\n".join(parts)


def write_ops_snapshot_to_memory(
    status: Optional[dict[str, Any]] = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Stage 1: persist ops snapshot to Cognee.

    ``force=True`` for explicit owner commands; autonomy uses
    ``ISAAC_AUTO_PIPELINE=1`` without force.
    """
    st = status or build_automation_status()
    text = build_ops_snapshot_text(st)
    result: dict[str, Any] = {"ok": False, "written": [], "text_preview": text[:200]}
    if not force and not auto_pipeline_enabled():
        result["skipped"] = "ISAAC_AUTO_PIPELINE=0 (enable or pass force=True)"
        return result
    try:
        from external_memory import get_external_memory_bridge

        bridge = get_external_memory_bridge()
        if not bridge.cfg.write_enabled:
            result["skipped"] = "ISAAC_EXTERNAL_MEMORY_WRITE=0"
            return result
        if not bridge.cognee.available():
            result["skipped"] = "cognee not available"
            return result
        ok = bridge.cognee.remember(
            [
                {"role": "system", "content": text[:4000]},
            ],
            metadata={"source": "isaac_ops", "kind": "automation_snapshot"},
        )
        if ok:
            result["ok"] = True
            result["written"].append("cognee")
        else:
            result["error"] = "cognee.remember returned False"
    except Exception as exc:
        result["error"] = str(exc)[:200]
        log.debug("ops snapshot write failed: %s", exc)

    state = load_automation_state()
    runs = dict(state.get("runs") or {})
    runs["last_ops_snapshot"] = {
        "ts": st.get("ts"),
        "ok": result.get("ok"),
        "written": list(result.get("written") or []),
        "skipped": result.get("skipped"),
    }
    state["runs"] = runs
    try:
        save_automation_state(state)
    except Exception:
        pass

    AuditLog.action(
        "AutomationPipeline",
        "ops_snapshot",
        f"ok={result.get('ok')} written={result.get('written')} skip={result.get('skipped')}",
        erfolg=bool(result.get("ok")),
    )
    return result


def run_stack_health_cycle(*, force_write: bool = False) -> dict[str, Any]:
    """Stage 1–2: collect status, optionally write to Cognee, return report."""
    st = build_automation_status()
    write: dict[str, Any]
    if force_write or auto_pipeline_enabled():
        write = write_ops_snapshot_to_memory(st, force=force_write or auto_pipeline_enabled())
    else:
        write = {"skipped": "ISAAC_AUTO_PIPELINE=0"}
    return {
        "status": st,
        "memory_write": write,
        "summary": format_automation_status(st),
    }
