from __future__ import annotations

"""Isaac – Computer Use / Oberflächen-Agent
Live-Steuerung der lokalen Umgebung (Termux, Linux-Desktop) über explizite Aktionen.
"""

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from config import BASE_DIR, WORKSPACE, get_config, is_owner_equivalent_mode
from audit import AuditLog

log = logging.getLogger("Isaac.ComputerUse")

SHELL_TIMEOUT_SEC = 45.0
TERMUX_API_HINT = (
    "Termux-Berechtigung fehlt. Fix:\n"
    "  1) pkg install termux-api\n"
    "  2) App „Termux:API“ installieren (F-Droid/Play Store)\n"
    "  3) Termux:API einmal öffnen und Berechtigungen erlauben\n"
    "  4) termux-setup-storage (für Speicher-Zugriff)\n"
    "  5) Isaac neu starten, dann: agent: diagnose"
)
S8_TERMUX_BRIDGE_HINT = (
    "S8-Chroot → Termux-Brücke fehlt. Fix:\n"
    "  1) In Termux: pkg install termux-api openssh tsu\n"
    "  2) Termux:API-App installieren und Berechtigungen erlauben\n"
    "  3) bash scripts/setup_termux_bridge.sh\n"
    "  4) Isaac neu starten, dann: agent: diagnose"
)
# Alias auf kanonische ROT-Liste (inkl. Package-Install) — eine Quelle der Wahrheit.
from constitution_override import DESTRUCTIVE_SHELL_FRAGMENTS as BLOCKED_SHELL_FRAGMENTS
from constitution_override import is_destructive_shell_text


@dataclass(frozen=True)
class AgentAction:
    action: str
    params: dict[str, Any] = field(default_factory=dict)


def _is_s8_linux_root() -> bool:
    """Samsung S8+ mit nativem Linux-Root auf USERDATA (NetHunter/Linux Deploy)."""
    try:
        mounts = Path("/proc/mounts").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "by-name/USERDATA" in mounts and Path("/sys/class/net/wlan0").exists()


def detect_runtime() -> str:
    env = (os.getenv("ISAAC_RUNTIME_ENV") or "").strip().lower()
    if env in {"termux", "s8", "android"}:
        if Path("/data/data/com.termux").exists() or env == "termux":
            return "termux"
        if _is_s8_linux_root() or env == "s8":
            return "s8"
    if Path("/data/data/com.termux").exists():
        return "termux"
    if _is_s8_linux_root():
        return "s8"
    if os.name == "nt":
        return "windows_desktop"
    if os.getenv("DISPLAY") or _command_exists("xdotool"):
        return "linux_desktop"
    return "generic"


def computer_use_enabled() -> bool:
    cfg = get_config()
    if is_owner_equivalent_mode(cfg):
        return True
    explicit = getattr(cfg, "computer_use_enabled", None)
    if explicit is not None:
        return bool(explicit)
    return detect_runtime() in {"termux", "s8", "linux_desktop", "windows_desktop"}


def computer_use_live() -> bool:
    cfg = get_config()
    return bool(getattr(cfg, "computer_use_live", True))


def _command_exists(name: str) -> bool:
    from shutil import which
    return which(name) is not None


def _shell_cwd() -> str:
    if detect_runtime() == "termux":
        return str(BASE_DIR)
    return str(WORKSPACE)


def _permission_hint(stderr: str) -> str:
    text = (stderr or "").lower()
    if "permission denied" in text or "not permitted" in text:
        return TERMUX_API_HINT
    return ""


def screenshot_dir() -> Path:
    candidates = [
        WORKSPACE / "screenshots",
        Path.home() / ".isaac" / "screenshots",
        Path(os.environ.get("PREFIX", "")) / "tmp" / "isaac_screenshots" if os.environ.get("PREFIX") else None,
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return candidate
        except OSError:
            continue
    fallback = Path.home() / f"isaac_shot_{int(time.time())}.png"
    fallback.parent.mkdir(parents=True, exist_ok=True)
    return fallback.parent


def _is_destructive_shell(command: str) -> bool:
    return is_destructive_shell_text(command)


def _blocked_shell(command: str) -> Optional[str]:
    lowered = (command or "").strip().lower()
    if not lowered:
        return "Leerer Befehl"
    if is_owner_equivalent_mode():
        return None
    for fragment in BLOCKED_SHELL_FRAGMENTS:
        if fragment in lowered:
            return f"Blockiert: unsicherer Shell-Fragment ({fragment.strip()})"
    return None


def _constitution_gate_shell(command: str) -> Optional[str]:
    """Verfassungs-Gate für Shell: destruktive Befehle ohne Owner blockieren."""
    from constitution_override import critical_action_gate

    msg = critical_action_gate(
        "system_command",
        source="computer_use.shell",
        owner_approved=is_owner_equivalent_mode(),
        destructive=_is_destructive_shell(command),
        risk="high",
    )
    if not msg:
        return None
    return msg.replace("Verfassung blockiert system_command:", "Verfassung blockiert Shell:")


def parse_agent_body(body: str) -> AgentAction:
    text = (body or "").strip()
    lower = text.lower()
    if not text:
        raise ValueError("Leerer Agent-Befehl")

    if lower in {"observe", "status", "diagnose", "capabilities"}:
        return AgentAction(lower if lower != "capabilities" else "diagnose")

    if lower.startswith("screenshot"):
        target = text.split(maxsplit=1)[1].strip() if " " in text else ""
        return AgentAction("screenshot", {"path": target})

    if lower.startswith("shell "):
        return AgentAction("shell", {"command": text[6:].strip()})

    if lower.startswith("clipboard get"):
        return AgentAction("clipboard_get")
    if lower.startswith("clipboard set "):
        return AgentAction("clipboard_set", {"text": text[len("clipboard set "):]})

    if lower.startswith("open "):
        return AgentAction("open", {"target": text[5:].strip()})

    if lower.startswith("tap "):
        parts = text.split()
        if len(parts) < 3:
            raise ValueError("Format: agent: tap X Y")
        return AgentAction("tap", {"x": int(parts[1]), "y": int(parts[2])})

    if lower.startswith("type "):
        return AgentAction("type_text", {"text": text[5:]})

    if lower.startswith("swipe "):
        parts = text.split()
        if len(parts) < 5:
            raise ValueError("Format: agent: swipe X1 Y1 X2 Y2 [ms]")
        ms = int(parts[5]) if len(parts) > 5 else 300
        return AgentAction(
            "swipe",
            {"x1": int(parts[1]), "y1": int(parts[2]), "x2": int(parts[3]), "y2": int(parts[4]), "ms": ms},
        )

    if lower.startswith("wait "):
        return AgentAction("wait", {"seconds": float(text.split(maxsplit=1)[1].replace(",", "."))})

    if lower.startswith("ui dump"):
        return AgentAction("ui_dump")
    if lower.startswith("ui list"):
        return AgentAction("ui_list")
    if lower.startswith("ui tap "):
        target = text[len("ui tap "):].strip()
        if not target:
            raise ValueError("Format: agent: ui tap Einstellungen")
        return AgentAction("ui_tap", {"text": target})
    if lower == "ui unlock":
        return AgentAction("ui_unlock")
    if lower.startswith("ui key "):
        code = text[len("ui key "):].strip()
        if not code:
            raise ValueError("Format: agent: ui key enter")
        return AgentAction("ui_key", {"code": code})

    if lower.startswith("credential list"):
        return AgentAction("credential_list")
    if lower.startswith("credential screen"):
        site = text[len("credential screen"):].strip()
        return AgentAction("credential_screen", {"site": site})
    if lower.startswith("credential read "):
        body = text[len("credential read "):].strip()
        import_flag = False
        if " --import" in body.lower():
            import_flag = True
            body = re.sub(r"\s+--import\s*$", "", body, flags=re.I).strip()
        if not body:
            raise ValueError("Format: agent: credential read google.com [--import]")
        return AgentAction("credential_read", {"site": body, "import": import_flag})
    if lower.startswith("credential internal "):
        site = text[len("credential internal "):].strip()
        if not site:
            raise ValueError("Format: agent: credential internal DOMAIN")
        return AgentAction("credential_internal", {"site": site})

    raise ValueError(
        "Unbekannter Agent-Befehl. Beispiele: observe, screenshot, shell ls -la, "
        "clipboard get, clipboard set Text, open https://…, tap 400 900, type Hallo, "
        "ui dump, ui list, ui tap Einstellungen, ui unlock, ui key enter, "
        "credential list, credential read google.com, credential screen"
    )


def parse_agent_flow(body: str) -> list[AgentAction]:
    if ";" not in body and not body.lower().startswith("flow "):
        return [parse_agent_body(body)]
    raw = body[5:].strip() if body.lower().startswith("flow ") else body
    actions: list[AgentAction] = []
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if chunk:
            actions.append(parse_agent_body(chunk))
    return actions


class ComputerUseRuntime:
    def __init__(self):
        self.runtime = detect_runtime()
        screenshot_dir()

    def _uses_termux_bridge(self) -> bool:
        return self.runtime == "s8"

    async def _termux_bridge_available(self) -> bool:
        if not self._uses_termux_bridge():
            return False
        try:
            from termux_bridge import bridge_available
            return bridge_available()
        except Exception:
            return False

    async def execute(self, action: AgentAction, *, dry_run: bool = False) -> dict[str, Any]:
        if not computer_use_enabled():
            return {"ok": False, "error": "Computer-Use ist deaktiviert (computer_use_enabled=false)"}
        if dry_run or not computer_use_live():
            return {
                "ok": True,
                "dry_run": True,
                "action": action.action,
                "runtime": self.runtime,
                "params": action.params,
            }

        handlers = {
            "observe": self._observe,
            "diagnose": self._diagnose,
            "screenshot": self._screenshot,
            "shell": self._shell,
            "clipboard_get": self._clipboard_get,
            "clipboard_set": self._clipboard_set,
            "open": self._open_target,
            "tap": self._tap,
            "type_text": self._type_text,
            "swipe": self._swipe,
            "wait": self._wait,
            "ui_dump": self._ui_dump,
            "ui_list": self._ui_list,
            "ui_tap": self._ui_tap,
            "ui_unlock": self._ui_unlock,
            "ui_key": self._ui_key,
            "credential_list": self._credential_list,
            "credential_screen": self._credential_screen,
            "credential_read": self._credential_read,
            "credential_internal": self._credential_internal,
        }
        handler = handlers.get(action.action)
        if not handler:
            return {"ok": False, "error": f"Unbekannte Aktion: {action.action}"}
        try:
            result = await handler(action.params)
            result.setdefault("action", action.action)
            result.setdefault("runtime", self.runtime)
            AuditLog.action("ComputerUse", action.action, json.dumps(result, ensure_ascii=False)[:240])
            return result
        except Exception as exc:
            log.warning("ComputerUse %s failed: %s", action.action, exc)
            return {"ok": False, "error": str(exc), "action": action.action, "runtime": self.runtime}

    async def execute_flow(self, actions: list[AgentAction]) -> dict[str, Any]:
        steps: list[dict[str, Any]] = []
        memory: dict[str, Any] = {}
        for idx, action in enumerate(actions, start=1):
            result = await self.execute(action)
            steps.append({"step": idx, **result})
            if not result.get("ok"):
                return {"ok": False, "steps": steps, "memory": memory, "error": result.get("error", "failed")}
            if action.action == "screenshot" and result.get("path"):
                memory["last_screenshot"] = result["path"]
            if action.action == "shell" and result.get("stdout"):
                memory["last_shell_stdout"] = result["stdout"][:2000]
        return {"ok": True, "steps": steps, "memory": memory}

    async def _observe(self, params: dict[str, Any]) -> dict[str, Any]:
        shell = await self._shell({"command": "pwd && ls -la"})
        battery = await self._termux_battery()
        shot = await self._screenshot({})
        warnings: list[str] = []
        if not shot.get("ok"):
            warnings.append(shot.get("error", "Screenshot fehlgeschlagen"))
            if shot.get("hint"):
                warnings.append(shot["hint"])
        return {
            "ok": True,
            "runtime": self.runtime,
            "cwd": shell.get("stdout", "").splitlines()[:1],
            "listing": shell.get("stdout", "")[:1500],
            "battery": battery,
            "screenshot": shot.get("path", ""),
            "screenshot_ok": bool(shot.get("ok")),
            "warnings": warnings,
            "capabilities": self._capabilities(),
        }

    async def _diagnose(self, params: dict[str, Any]) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        bridge_diag: dict[str, Any] = {}
        if self._uses_termux_bridge():
            try:
                from termux_bridge import diagnose_bridge
                bridge_diag = diagnose_bridge()
                for name, available in (bridge_diag.get("tools") or {}).items():
                    checks.append({"tool": name, "available": bool(available), "via": "termux_bridge"})
            except Exception as exc:
                bridge_diag = {"error": str(exc)}
        for name in (
            "termux-screenshot",
            "termux-clipboard-get",
            "termux-open-url",
            "termux-battery-status",
            "input",
        ):
            if any(row.get("tool") == name for row in checks):
                continue
            checks.append({"tool": name, "available": _command_exists(name)})

        dir_ok = False
        dir_path = ""
        dir_error = ""
        try:
            shot_dir = screenshot_dir()
            dir_path = str(shot_dir)
            dir_ok = shot_dir.exists()
        except OSError as exc:
            dir_error = str(exc)

        probe = await self._shell({"command": "pwd && whoami"})
        shot = await self._screenshot({})
        hint = ""
        if self.runtime == "termux":
            hint = TERMUX_API_HINT
        elif self._uses_termux_bridge() and not shot.get("ok"):
            hint = S8_TERMUX_BRIDGE_HINT
        return {
            "ok": True,
            "runtime": self.runtime,
            "computer_use_enabled": computer_use_enabled(),
            "computer_use_live": computer_use_live(),
            "shell_cwd": _shell_cwd(),
            "screenshot_dir": dir_path,
            "screenshot_dir_ok": dir_ok,
            "screenshot_dir_error": dir_error,
            "tool_checks": checks,
            "termux_bridge": bridge_diag,
            "shell_probe": probe,
            "screenshot_probe": shot,
            "hint": hint,
        }

    async def _screenshot(self, params: dict[str, Any]) -> dict[str, Any]:
        target = (params.get("path") or "").strip()
        if not target:
            target = str(screenshot_dir() / f"isaac_{int(time.time())}.png")
        path = Path(target)
        if not path.is_absolute():
            path = (WORKSPACE / path).resolve()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {"ok": False, "error": f"Screenshot-Pfad nicht beschreibbar: {exc}"}

        if self.runtime == "windows_desktop":
            from windows_desktop import screenshot
            return await screenshot(str(path))
        if self.runtime == "termux" and _command_exists("termux-screenshot"):
            proc = await asyncio.create_subprocess_exec(
                "termux-screenshot",
                "-o",
                str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
            if proc.returncode != 0:
                err = (stderr or b"").decode(errors="replace").strip() or "termux-screenshot fehlgeschlagen"
                hint = _permission_hint(err)
                return {"ok": False, "error": err, "hint": hint or TERMUX_API_HINT}
        elif await self._termux_bridge_available():
            from shutil import copy2
            from termux_bridge import bridge_work_dir, run_termux_command

            remote = bridge_work_dir() / f"isaac_{int(time.time())}.png"
            result = await run_termux_command(["termux-screenshot", "-o", str(remote)], timeout=25.0)
            if not result.get("ok"):
                return {
                    "ok": False,
                    "error": result.get("error", "termux-screenshot via Brücke fehlgeschlagen"),
                    "hint": S8_TERMUX_BRIDGE_HINT,
                    "via": result.get("via"),
                }
            if remote.exists():
                copy2(remote, path)
            elif not path.exists():
                return {"ok": False, "error": f"Screenshot nicht lesbar: {remote}", "hint": S8_TERMUX_BRIDGE_HINT}
        elif _command_exists("scrot"):
            proc = await asyncio.create_subprocess_exec(
                "scrot",
                str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=20)
        elif _command_exists("import"):
            proc = await asyncio.create_subprocess_exec(
                "import",
                "-window",
                "root",
                str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=20)
        else:
            return {
                "ok": False,
                "error": (
                    "Kein Screenshot-Tool. Termux: pkg install termux-api + Termux:API-App. "
                    "Linux: scrot oder imagemagick."
                ),
                "hint": TERMUX_API_HINT if self.runtime == "termux" else S8_TERMUX_BRIDGE_HINT,
            }

        if not path.exists():
            return {"ok": False, "error": "Screenshot-Datei wurde nicht erstellt", "path": str(path)}
        return {"ok": True, "path": str(path), "bytes": path.stat().st_size}

    async def _shell(self, params: dict[str, Any]) -> dict[str, Any]:
        command = (params.get("command") or "").strip()
        blocked = _blocked_shell(command)
        if blocked:
            return {"ok": False, "error": blocked}
        constitution_block = _constitution_gate_shell(command)
        if constitution_block:
            AuditLog.action(
                "ComputerUse",
                "shell_constitution_block",
                constitution_block[:160],
                erfolg=False,
            )
            return {"ok": False, "error": constitution_block, "source": "constitution"}
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_shell_cwd(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=SHELL_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return {"ok": False, "error": f"Timeout ({int(SHELL_TIMEOUT_SEC)}s)", "command": command}
        out = (stdout.decode(errors="replace") or "").strip()
        err = (stderr.decode(errors="replace") or "").strip()
        return {
            "ok": proc.returncode == 0,
            "command": command,
            "stdout": out[:4000],
            "stderr": err[:1000],
            "exit_code": proc.returncode,
        }

    async def _clipboard_get(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.runtime == "windows_desktop":
            from windows_desktop import clipboard_get
            return await clipboard_get()
        if await self._termux_bridge_available():
            from termux_bridge import run_termux_command
            result = await run_termux_command(["termux-clipboard-get"], timeout=12.0)
            if result.get("ok"):
                return {"ok": True, "text": (result.get("stdout") or "")[:4000], "via": result.get("via")}
        if self.runtime == "termux" and _command_exists("termux-clipboard-get"):
            proc = await asyncio.create_subprocess_exec(
                "termux-clipboard-get",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                err = (stderr or b"clipboard-get failed").decode(errors="replace")
                return {"ok": False, "error": err, "hint": _permission_hint(err) or TERMUX_API_HINT}
            text = stdout.decode(errors="replace")
            return {"ok": True, "text": text[:4000]}
        if _command_exists("xclip"):
            proc = await asyncio.create_subprocess_exec(
                "xclip",
                "-o",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            return {"ok": True, "text": stdout.decode(errors="replace")[:4000]}
        return {"ok": False, "error": "Clipboard nicht verfügbar (termux-api oder xclip)"}

    async def _clipboard_set(self, params: dict[str, Any]) -> dict[str, Any]:
        text = str(params.get("text") or "")
        if self.runtime == "windows_desktop":
            from windows_desktop import clipboard_set
            return await clipboard_set(text)
        if await self._termux_bridge_available():
            from termux_bridge import run_termux_command
            result = await run_termux_command(["termux-clipboard-set", text], timeout=12.0)
            if result.get("ok"):
                return {"ok": True, "length": len(text), "via": result.get("via")}
        if self.runtime == "termux" and _command_exists("termux-clipboard-set"):
            proc = await asyncio.create_subprocess_exec(
                "termux-clipboard-set",
                text,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                raise RuntimeError((stderr or b"clipboard-set failed").decode(errors="replace"))
            return {"ok": True, "length": len(text)}
        if _command_exists("xclip"):
            proc = await asyncio.create_subprocess_exec(
                "xclip",
                "-selection",
                "clipboard",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(input=text.encode()), timeout=10)
            return {"ok": True, "length": len(text)}
        return {"ok": False, "error": "Clipboard nicht verfügbar (termux-api oder xclip)"}

    async def _open_target(self, params: dict[str, Any]) -> dict[str, Any]:
        target = (params.get("target") or "").strip().strip("\"'")
        if not target:
            return {"ok": False, "error": "Leeres Ziel"}
        if self.runtime == "windows_desktop":
            from windows_desktop import open_target
            return await open_target(target)
        if await self._termux_bridge_available():
            from termux_bridge import run_termux_command
            if target.startswith(("http://", "https://")):
                result = await run_termux_command(["termux-open-url", target], timeout=15.0)
                if result.get("ok"):
                    return {"ok": True, "opened": target, "via": result.get("via") or "termux_bridge"}
        if self.runtime == "termux":
            if target.startswith(("http://", "https://")) and _command_exists("termux-open-url"):
                proc = await asyncio.create_subprocess_exec(
                    "termux-open-url",
                    target,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=15)
                return {"ok": True, "opened": target, "via": "termux-open-url"}
            if _command_exists("termux-open"):
                proc = await asyncio.create_subprocess_exec(
                    "termux-open",
                    target,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=15)
                return {"ok": True, "opened": target, "via": "termux-open"}
        if target.startswith(("http://", "https://")) and _command_exists("xdg-open"):
            proc = await asyncio.create_subprocess_exec(
                "xdg-open",
                target,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=15)
            return {"ok": True, "opened": target, "via": "xdg-open"}
        return {"ok": False, "error": "Öffnen nicht verfügbar (termux-open / xdg-open)"}

    async def _tap(self, params: dict[str, Any]) -> dict[str, Any]:
        x, y = int(params.get("x", 0)), int(params.get("y", 0))
        if self.runtime == "windows_desktop":
            from windows_desktop import tap
            return await tap(x, y)
        if await self._termux_bridge_available():
            from termux_bridge import run_android_input
            result = await run_android_input(["tap", str(x), str(y)], timeout=12.0)
            if result.get("ok"):
                return {"ok": True, "x": x, "y": y, "via": result.get("via")}
        if _command_exists("input"):
            proc = await asyncio.create_subprocess_exec(
                "input",
                "tap",
                str(x),
                str(y),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                raise RuntimeError((stderr or b"tap failed").decode(errors="replace"))
            return {"ok": True, "x": x, "y": y, "via": "input"}
        if _command_exists("xdotool"):
            proc = await asyncio.create_subprocess_exec(
                "xdotool",
                "mousemove",
                str(x),
                str(y),
                "click",
                "1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            return {"ok": True, "x": x, "y": y, "via": "xdotool"}
        return {
            "ok": False,
            "error": "Tap nicht verfügbar. Android: adb/input im PATH. Desktop: xdotool.",
        }

    async def _type_text(self, params: dict[str, Any]) -> dict[str, Any]:
        text = str(params.get("text") or "")
        if self.runtime == "windows_desktop":
            from windows_desktop import type_text
            return await type_text(text)
        if await self._termux_bridge_available():
            from termux_bridge import run_android_input
            result = await run_android_input(["text", text.replace(" ", "%s")], timeout=20.0)
            if result.get("ok"):
                return {"ok": True, "length": len(text), "via": result.get("via")}
        if _command_exists("xdotool"):
            proc = await asyncio.create_subprocess_exec(
                "xdotool",
                "type",
                "--delay",
                "12",
                "--",
                text,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=20)
            return {"ok": True, "length": len(text), "via": "xdotool"}
        if _command_exists("input"):
            proc = await asyncio.create_subprocess_exec(
                "input",
                "text",
                text.replace(" ", "%s"),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
            if proc.returncode != 0:
                raise RuntimeError((stderr or b"type failed").decode(errors="replace"))
            return {"ok": True, "length": len(text), "via": "input"}
        # Termux fallback: clipboard + Hinweis
        clip = await self._clipboard_set({"text": text})
        if clip.get("ok"):
            return {
                "ok": True,
                "length": len(text),
                "via": "clipboard_set",
                "note": "Text in Zwischenablage — manuell einfügen falls kein input/xdotool.",
            }
        return {"ok": False, "error": "Texteingabe nicht verfügbar (xdotool/input/clipboard)"}

    async def _swipe(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.runtime == "windows_desktop":
            from windows_desktop import swipe
            return await swipe(int(params.get("x1",0)), int(params.get("y1",0)), int(params.get("x2",0)), int(params.get("y2",0)), int(params.get("ms",300)))
        if await self._termux_bridge_available():
            from termux_bridge import run_android_input
            result = await run_android_input(
                [
                    "swipe",
                    str(params.get("x1")),
                    str(params.get("y1")),
                    str(params.get("x2")),
                    str(params.get("y2")),
                    str(params.get("ms", 300)),
                ],
                timeout=15.0,
            )
            if result.get("ok"):
                return {"ok": True, **params, "via": result.get("via")}
        if not _command_exists("input"):
            return {"ok": False, "error": "Swipe benötigt Android input-Befehl (adb)"}
        proc = await asyncio.create_subprocess_exec(
            "input",
            "swipe",
            str(params.get("x1")),
            str(params.get("y1")),
            str(params.get("x2")),
            str(params.get("y2")),
            str(params.get("ms", 300)),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            raise RuntimeError((stderr or b"swipe failed").decode(errors="replace"))
        return {"ok": True, **params, "via": "input"}

    async def _wait(self, params: dict[str, Any]) -> dict[str, Any]:
        seconds = max(0.1, float(params.get("seconds", 1.0)))
        await asyncio.sleep(seconds)
        return {"ok": True, "seconds": seconds}

    async def _capture_ui_nodes(self) -> tuple[list[Any], str]:
        from ui_automation import UI_DUMP_COMMANDS, extract_ui_dump, parse_ui_xml

        if await self._termux_bridge_available():
            from termux_bridge import run_ui_dump
            result = await run_ui_dump(timeout=35.0)
            xml_text = extract_ui_dump(result.get("stdout", ""))
            if result.get("ok") and xml_text:
                return parse_ui_xml(xml_text), f"termux_bridge:{result.get('via', 'ui_dump')}"

        errors: list[str] = []
        for command in UI_DUMP_COMMANDS:
            result = await self._shell({"command": command})
            xml_text = extract_ui_dump(result.get("stdout", ""))
            if not xml_text:
                err = (result.get("stderr") or result.get("stdout") or "leer")[:200]
                errors.append(f"{command.split('&&')[0].strip()}: {err}")
                continue
            try:
                return parse_ui_xml(xml_text), command.split("&&")[0].strip()
            except Exception as exc:
                errors.append(f"parse: {exc}")
        raise RuntimeError(
            "UI-Dump nicht verfügbar (uiautomator/adb). "
            + " | ".join(errors[:3])
        )

    async def _ui_dump(self, params: dict[str, Any]) -> dict[str, Any]:
        from ui_automation import audit_ui_action, summarize_nodes

        try:
            nodes, via = await self._capture_ui_nodes()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        audit_ui_action("ui_dump", f"nodes={len(nodes)} via={via}")
        return {
            "ok": True,
            "via": via,
            "node_count": len(nodes),
            "summary": summarize_nodes(nodes),
        }

    async def _ui_list(self, params: dict[str, Any]) -> dict[str, Any]:
        from ui_automation import audit_ui_action, list_interactive_labels

        try:
            nodes, via = await self._capture_ui_nodes()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        labels = list_interactive_labels(nodes)
        audit_ui_action("ui_list", f"labels={len(labels)} via={via}")
        return {"ok": True, "via": via, "labels": labels, "count": len(labels)}

    async def _ui_tap(self, params: dict[str, Any]) -> dict[str, Any]:
        from ui_automation import audit_ui_action, find_nodes

        query = (params.get("text") or "").strip()
        if not query:
            return {"ok": False, "error": "ui tap benötigt Text/Label"}
        try:
            nodes, via = await self._capture_ui_nodes()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        matches = find_nodes(nodes, query)
        if not matches:
            return {"ok": False, "error": f"Kein klickbares Element für '{query}'", "via": via}
        target = matches[0]
        x, y = target.center()
        tap = await self._tap({"x": x, "y": y})
        if not tap.get("ok"):
            return tap
        audit_ui_action("ui_tap", f"label={target.label[:80]} x={x} y={y}")
        return {
            "ok": True,
            "via": via,
            "label": target.label,
            "x": x,
            "y": y,
            "matches": len(matches),
        }

    async def _ui_key(self, params: dict[str, Any]) -> dict[str, Any]:
        from ui_automation import android_keycode, audit_ui_action

        if self.runtime == "windows_desktop":
            from windows_desktop import key
            result = await key(str(params.get("code") or ""))
            if result.get("ok"):
                audit_ui_action("ui_key", f"code={params.get('code', '')}")
            return result

        if not _command_exists("input") and not await self._termux_bridge_available():
            return {"ok": False, "error": "ui key benötigt Android input-Befehl"}
        try:
            code = android_keycode(str(params.get("code") or ""))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if await self._termux_bridge_available():
            from termux_bridge import run_android_input
            result = await run_android_input(["keyevent", str(code)], timeout=10.0)
            if not result.get("ok"):
                return {"ok": False, "error": result.get("error", "keyevent failed")}
            audit_ui_action("ui_key", f"code={code}")
            return {"ok": True, "code": code, "via": result.get("via")}
        proc = await asyncio.create_subprocess_exec(
            "input",
            "keyevent",
            str(code),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode != 0:
            return {"ok": False, "error": (stderr or b"keyevent failed").decode(errors="replace")}
        audit_ui_action("ui_key", f"code={code}")
        return {"ok": True, "code": code}

    def _credential_gate(self) -> Optional[dict[str, Any]]:
        from credential_access import require_credential_access

        err = require_credential_access()
        if err:
            return {"ok": False, "error": err}
        return None

    async def _credential_list(self, params: dict[str, Any]) -> dict[str, Any]:
        from credential_access import audit_credential_access, list_internal_credentials, list_visible_sites

        blocked = self._credential_gate()
        if blocked:
            return blocked
        internal = list_internal_credentials()
        visible_sites: list[str] = []
        try:
            nodes, _via = await self._capture_ui_nodes()
            visible_sites = list_visible_sites(nodes)
        except Exception:
            visible_sites = []
        audit_credential_access("list", f"internal={len(internal)} visible={len(visible_sites)}")
        return {"ok": True, "internal": internal, "visible_sites": visible_sites}

    async def _credential_screen(self, params: dict[str, Any]) -> dict[str, Any]:
        from credential_access import audit_credential_access, extract_visible_credentials

        blocked = self._credential_gate()
        if blocked:
            return blocked
        site_hint = (params.get("site") or "").strip()
        try:
            nodes, via = await self._capture_ui_nodes()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        records = extract_visible_credentials(nodes, site_hint=site_hint)
        if not records:
            return {"ok": False, "error": "Keine sichtbaren Credentials auf dem Bildschirm", "via": via}
        audit_credential_access("screen", f"site={records[0].site or '-'} via={via}")
        return {
            "ok": True,
            "via": via,
            "credentials": [row.to_dict(reveal=True) for row in records],
        }

    async def _credential_internal(self, params: dict[str, Any]) -> dict[str, Any]:
        from credential_access import audit_credential_access, read_internal_credential

        blocked = self._credential_gate()
        if blocked:
            return blocked
        site = (params.get("site") or "").strip()
        record = read_internal_credential(site)
        if not record:
            return {"ok": False, "error": f"Kein interner Credential-Eintrag für '{site}'"}
        audit_credential_access("internal", f"site={record.site} source={record.source}")
        return {"ok": True, "credential": record.to_dict(reveal=True)}

    async def _credential_read(self, params: dict[str, Any]) -> dict[str, Any]:
        from credential_access import (
            audit_credential_access,
            extract_visible_credentials,
            import_credential,
            pick_show_password_label,
            read_internal_credential,
        )
        from ui_automation import find_nodes

        blocked = self._credential_gate()
        if blocked:
            return blocked
        site = (params.get("site") or "").strip()
        if not site:
            return {"ok": False, "error": "credential read benötigt Site/Domain"}
        do_import = bool(params.get("import"))

        internal = read_internal_credential(site)
        if internal and internal.password:
            cred = internal
            if do_import:
                import_credential(cred)
            audit_credential_access("read_internal", f"site={cred.site}")
            return {"ok": True, "source": cred.source, "credential": cred.to_dict(reveal=True), "imported": do_import}

        try:
            nodes, via = await self._capture_ui_nodes()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        site_nodes = find_nodes(nodes, site, clickable_only=True)
        if site_nodes:
            x, y = site_nodes[0].center()
            tap = await self._tap({"x": x, "y": y})
            if not tap.get("ok"):
                return tap
            await asyncio.sleep(1.0)
            try:
                nodes, via = await self._capture_ui_nodes()
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

        show_label = pick_show_password_label(nodes)
        if show_label:
            show_tap = await self._ui_tap({"text": show_label})
            if show_tap.get("ok"):
                await asyncio.sleep(0.4)
                await self._ui_unlock({})
                await asyncio.sleep(0.8)
                try:
                    nodes, via = await self._capture_ui_nodes()
                except Exception as exc:
                    return {"ok": False, "error": str(exc)}

        records = extract_visible_credentials(nodes, site_hint=site)
        if not records or not records[0].password:
            visible = [n.label for n in find_nodes(nodes, site, clickable_only=False)[:6]]
            return {
                "ok": False,
                "error": (
                    f"Credential für '{site}' nicht sichtbar. "
                    "Öffne Chrome → Passwörter, scrolle zum Eintrag oder nutze credential screen."
                ),
                "via": via,
                "hints": visible,
            }
        cred = records[0]
        if do_import:
            import_credential(cred)
        audit_credential_access("read_ui", f"site={cred.site or site}")
        return {
            "ok": True,
            "source": cred.source,
            "credential": cred.to_dict(reveal=True),
            "imported": do_import,
            "via": via,
        }

    async def _ui_unlock(self, params: dict[str, Any]) -> dict[str, Any]:
        from ui_automation import DEVICE_PIN_SECRET_REF, audit_ui_action, load_device_pin, pin_keycodes

        try:
            pin = load_device_pin()
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if not pin:
            return {
                "ok": False,
                "error": (
                    f"Geräte-PIN fehlt. Hinterlege Secret-Ref {DEVICE_PIN_SECRET_REF} "
                    "lokal (Dashboard/Secrets) — niemals im Chat."
                ),
            }
        use_bridge = await self._termux_bridge_available()
        if not _command_exists("input") and not use_bridge:
            return {"ok": False, "error": "ui unlock benötigt Android input-Befehl"}
        for code in pin_keycodes(pin):
            if use_bridge:
                from termux_bridge import run_android_input
                result = await run_android_input(["keyevent", str(code)], timeout=8.0)
                if not result.get("ok"):
                    return {"ok": False, "error": result.get("error", "unlock keyevent failed")}
            else:
                proc = await asyncio.create_subprocess_exec(
                    "input",
                    "keyevent",
                    str(code),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=8)
                if proc.returncode != 0:
                    return {"ok": False, "error": (stderr or b"unlock keyevent failed").decode(errors="replace")}
            await asyncio.sleep(0.12)
        audit_ui_action("ui_unlock", f"ref={DEVICE_PIN_SECRET_REF} digits={len(pin)}")
        return {"ok": True, "digits": len(pin), "ref": DEVICE_PIN_SECRET_REF}

    async def _termux_battery(self) -> dict[str, Any]:
        if await self._termux_bridge_available():
            from termux_bridge import run_termux_command
            result = await run_termux_command(["termux-battery-status"], timeout=8.0)
            if result.get("ok"):
                try:
                    return json.loads(result.get("stdout") or "{}")
                except Exception:
                    return {}
        if not _command_exists("termux-battery-status"):
            return {}
        proc = await asyncio.create_subprocess_exec(
            "termux-battery-status",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        try:
            return json.loads(stdout.decode())
        except Exception:
            return {}

    def _capabilities(self) -> list[str]:
        caps = ["shell"]
        for name, key in (
            ("termux-screenshot", "screenshot"),
            ("termux-clipboard-get", "clipboard"),
            ("termux-open-url", "open_url"),
            ("input", "tap_type_swipe"),
            ("uiautomator", "ui_dump"),
            ("adb", "ui_dump"),
            ("xdotool", "desktop_input"),
            ("scrot", "screenshot"),
        ):
            if _command_exists(name):
                caps.append(key)
        if _command_exists("input"):
            caps.append("ui_tap")
            caps.append("ui_unlock")
        try:
            from termux_bridge import bridge_available
            if bridge_available():
                caps.append("termux_bridge")
        except Exception:
            pass
        return sorted(set(caps))


_runtime: Optional[ComputerUseRuntime] = None


def get_computer_use() -> ComputerUseRuntime:
    global _runtime
    if _runtime is None:
        _runtime = ComputerUseRuntime()
    return _runtime


def format_agent_result(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return f"[Agent] Fehler: {result.get('error', 'unbekannt')}"
    if result.get("dry_run"):
        return f"[Agent] Dry-Run: {result.get('action')} ({result.get('runtime')})"
    if "steps" in result:
        lines = [f"[Agent] Flow OK ({len(result.get('steps', []))} Schritte)"]
        for step in result.get("steps", [])[:8]:
            lines.append(f"  - Schritt {step.get('step')}: {step.get('action')} → {'ok' if step.get('ok') else 'fail'}")
        mem = result.get("memory") or {}
        if mem.get("last_screenshot"):
            lines.append(f"Screenshot: {mem['last_screenshot']}")
        if mem.get("last_shell_stdout"):
            lines.append(f"Shell:\n{mem['last_shell_stdout'][:1200]}")
        return "\n".join(lines)
    if result.get("action") in {"observe", "diagnose"}:
        lines = [f"[Agent] {result.get('action', 'observe').title()} ({result.get('runtime')})"]
        if result.get("action") == "diagnose":
            lines.append(f"enabled={result.get('computer_use_enabled')} live={result.get('computer_use_live')}")
            lines.append(f"shell_cwd={result.get('shell_cwd', '-')}")
            lines.append(f"screenshot_dir={result.get('screenshot_dir', '-')} ok={result.get('screenshot_dir_ok')}")
            for check in result.get("tool_checks", []):
                mark = "OK" if check.get("available") else "—"
                lines.append(f"  [{mark}] {check.get('tool')}")
            probe = result.get("screenshot_probe") or {}
            if probe:
                lines.append(f"screenshot_test: {'OK' if probe.get('ok') else probe.get('error', 'fail')}")
            bridge = result.get("termux_bridge") or {}
            if bridge:
                lines.append(f"termux_bridge: mode={bridge.get('mode', '-')} enabled={bridge.get('enabled')}")
            if result.get("hint"):
                lines.append(str(result["hint"]))
            return "\n".join(lines)
        lines.append(f"Capabilities: {', '.join(result.get('capabilities', []))}")
        lines.append(f"Battery: {json.dumps(result.get('battery', {}), ensure_ascii=False)}")
        lines.append(f"Screenshot: {result.get('screenshot', '-')} ({'ok' if result.get('screenshot_ok') else 'skip'})")
        for warning in result.get("warnings") or []:
            lines.append(f"Hinweis: {warning}")
        lines.append(f"CWD/Listing:\n{(result.get('listing') or '')[:1500]}")
        return "\n".join(lines)
    if not result.get("ok") and result.get("hint"):
        return f"[Agent] Fehler: {result.get('error', 'unbekannt')}\n\n{result['hint']}"
    if result.get("path"):
        return f"[Agent] Screenshot: {result['path']} ({result.get('bytes', 0)} bytes)"
    if "stdout" in result:
        status = "OK" if result.get("ok") else f"Exit {result.get('exit_code')}"
        parts = [f"[Agent] Shell {status}: {result.get('command', '')}"]
        if result.get("stdout"):
            parts.append(result["stdout"][:3500])
        if result.get("stderr"):
            parts.append(f"stderr: {result['stderr'][:500]}")
        return "\n".join(parts)
    if result.get("text") is not None:
        return f"[Agent] Clipboard:\n{result.get('text', '')[:3500]}"
    if result.get("summary"):
        lines = [
            f"[Agent] UI-Dump ({result.get('node_count', 0)} Elemente, via {result.get('via', '-')})",
            str(result.get("summary", "")),
        ]
        return "\n".join(lines)
    if result.get("labels") is not None:
        labels = result.get("labels") or []
        lines = [f"[Agent] UI-Liste ({len(labels)} klickbar, via {result.get('via', '-')})"]
        lines.extend(f"  - {label}" for label in labels[:30])
        return "\n".join(lines)
    if result.get("label") and result.get("x") is not None:
        return (
            f"[Agent] UI-Tap OK: '{result.get('label')}' "
            f"@ {result.get('x')},{result.get('y')} "
            f"(Treffer: {result.get('matches', 1)})"
        )
    if result.get("digits") and result.get("ref"):
        return f"[Agent] Gerät entsperrt ({result.get('digits')} Ziffern via {result.get('ref')})"
    if result.get("credentials"):
        lines = ["[Agent] Sichtbare Credentials:"]
        for row in result.get("credentials", [])[:5]:
            lines.append(
                f"  - {row.get('site') or '-'} | {row.get('username') or '-'} | {row.get('password') or '-'}"
            )
        return "\n".join(lines)
    if result.get("credential"):
        row = result.get("credential") or {}
        imported = " (importiert)" if result.get("imported") else ""
        return (
            f"[Agent] Credential{imported} [{result.get('source', '-')}] "
            f"{row.get('site') or '-'} | {row.get('username') or '-'} | {row.get('password') or '-'}"
        )
    if result.get("internal") is not None:
        lines = ["[Agent] Credential-Inventar:"]
        for row in (result.get("internal") or [])[:20]:
            lines.append(f"  intern: {row.get('site')} ({row.get('username') or row.get('source')})")
        for site in (result.get("visible_sites") or [])[:20]:
            lines.append(f"  bildschirm: {site}")
        return "\n".join(lines)
    return f"[Agent] OK: {json.dumps(result, ensure_ascii=False)[:2000]}"