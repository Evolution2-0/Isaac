"""Isaac – authorized Bug-Bounty runner (Owner/Admin only).

Hard rules:
  * Only programs listed in ``data/bug_bounty_programs.json`` (owner allowlist).
  * Only in-scope hosts / URL patterns.
  * Passive-destructive by default (passive recon + safe HTTP checks).
  * Every finding needs Evidence (command/output or HTTP observation).
  * No mass-internet scanning, no out-of-scope targets.

Results: ``workspace/bug_bounty/<program_id>/<timestamp>/``
"""

from __future__ import annotations

import json
import logging
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from audit import AuditLog
from config import DATA_DIR, WORKSPACE, is_owner_equivalent_mode

log = logging.getLogger("Isaac.BugBounty")

PROGRAMS_PATH = DATA_DIR / "bug_bounty_programs.json"
# Example lives in repo root (data/ is gitignored)
PROGRAMS_EXAMPLE = Path(__file__).resolve().parent / "bug_bounty_programs.example.json"
REPORT_ROOT = WORKSPACE / "bug_bounty"

_UA = "Isaac-BugBounty/5.3 (+authorized-owner-research; hansheute88/isaac)"


@dataclass
class Finding:
    title: str
    severity: str  # info|low|medium|high|critical|none
    host: str
    category: str
    summary: str
    evidence: list[str] = field(default_factory=list)
    reproduction: list[str] = field(default_factory=list)
    tested: bool = True
    confidence: float = 0.5
    recommendation: str = ""


def _owner_ok() -> Optional[str]:
    if is_owner_equivalent_mode():
        return None
    return "Bug-Bounty nur im Admin-Modus (ISAAC_PRIVILEGE_MODE=admin)."


def ensure_example_programs() -> None:
    """Write example allowlist if neither config nor example exists."""
    if PROGRAMS_PATH.exists() or PROGRAMS_EXAMPLE.exists():
        return
    example = {
        "version": 1,
        "note": (
            "Nur Programme eintragen, an denen du teilnimmst und deren Scope du "
            "gelesen hast. Isaac greift keine Targets außerhalb dieser Liste an."
        ),
        "programs": [
            {
                "id": "example-public-demo",
                "platform": "manual",
                "name": "Example (ersetze mich)",
                "program_url": "https://example.com",
                "enabled": False,
                "authorized": False,
                "in_scope_hosts": ["example.com", "www.example.com"],
                "in_scope_url_prefixes": ["https://example.com/", "https://www.example.com/"],
                "out_of_scope": ["blog.example.com"],
                "max_hosts": 5,
                "allow_active": False,
                "notes": "Setze authorized=true und enabled=true nach Scope-Lesen.",
            }
        ],
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROGRAMS_EXAMPLE.write_text(
        json.dumps(example, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_programs() -> list[dict[str, Any]]:
    ensure_example_programs()
    path = PROGRAMS_PATH if PROGRAMS_PATH.exists() else PROGRAMS_EXAMPLE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("bug bounty programs load failed: %s", exc)
        return []
    programs = data.get("programs") if isinstance(data, dict) else data
    if not isinstance(programs, list):
        return []
    return [p for p in programs if isinstance(p, dict)]


def list_programs(*, only_enabled: bool = False) -> list[dict[str, Any]]:
    rows = []
    for p in load_programs():
        if only_enabled and not p.get("enabled"):
            continue
        rows.append({
            "id": p.get("id"),
            "name": p.get("name"),
            "platform": p.get("platform"),
            "enabled": bool(p.get("enabled")),
            "authorized": bool(p.get("authorized")),
            "hosts": list(p.get("in_scope_hosts") or [])[:12],
            "allow_active": bool(p.get("allow_active")),
        })
    return rows


def get_program(program_id: str) -> Optional[dict[str, Any]]:
    pid = (program_id or "").strip().lower()
    for p in load_programs():
        if str(p.get("id", "")).lower() == pid:
            return p
        if str(p.get("name", "")).lower() == pid:
            return p
    return None


def _host_in_scope(host: str, program: dict[str, Any]) -> bool:
    h = (host or "").lower().strip().rstrip(".")
    if not h:
        return False
    oos = {x.lower() for x in (program.get("out_of_scope") or []) if x}
    if h in oos:
        return False
    for pat in program.get("in_scope_hosts") or []:
        pat = str(pat).lower().strip()
        if not pat:
            continue
        if pat.startswith("*."):
            root = pat[2:]
            if h == root or h.endswith("." + root):
                return True
        elif h == pat or h.endswith("." + pat):
            return True
    return False


def _http_get(url: str, *, timeout: float = 15.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": _UA, "Accept": "*/*"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(64_000)
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return {
                "ok": True,
                "url": resp.geturl(),
                "status": getattr(resp, "status", 200),
                "headers": headers,
                "body_preview": body[:2000].decode("utf-8", errors="replace"),
                "body_len": len(body),
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(4000) if hasattr(exc, "read") else b""
        return {
            "ok": False,
            "url": url,
            "status": exc.code,
            "headers": {k.lower(): v for k, v in (exc.headers or {}).items()},
            "body_preview": body[:1000].decode("utf-8", errors="replace"),
            "error": str(exc),
        }
    except Exception as exc:
        return {"ok": False, "url": url, "status": 0, "error": str(exc)[:200], "headers": {}}


def _dns_a(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        ips = sorted({i[4][0] for i in infos})
        return ips[:8]
    except Exception:
        return []


def _tls_info(host: str) -> dict[str, Any]:
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
                return {
                    "ok": True,
                    "version": ssock.version(),
                    "cipher": ssock.cipher(),
                    "subject": cert.get("subject") if cert else None,
                    "notAfter": cert.get("notAfter") if cert else None,
                }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def _crtsh_subdomains(domain: str, *, limit: int = 30) -> list[str]:
    """Public CT lookup — only returns names under domain."""
    q = urllib.parse.quote(f"%.{domain}")
    url = f"https://crt.sh/?q={q}&output=json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        rows = json.loads(raw) if raw.strip() else []
    except Exception as exc:
        log.debug("crt.sh failed: %s", exc)
        return []
    names: set[str] = set()
    for row in rows if isinstance(rows, list) else []:
        nv = str(row.get("name_value") or "")
        for part in nv.split("\n"):
            name = part.strip().lower().lstrip("*.")
            if name.endswith("." + domain) or name == domain:
                names.add(name)
        if len(names) >= limit * 3:
            break
    return sorted(names)[:limit]


def _check_security_headers(host: str, http: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    headers = http.get("headers") or {}
    missing = []
    for h in (
        "strict-transport-security",
        "content-security-policy",
        "x-content-type-options",
        "x-frame-options",
        "referrer-policy",
        "permissions-policy",
    ):
        if h not in headers:
            missing.append(h)
    if missing:
        findings.append(
            Finding(
                title=f"Security headers incomplete on {host}",
                severity="info",
                host=host,
                category="misconfiguration",
                summary=(
                    "Einige empfohlene Security-Header fehlen. "
                    "Oft nur informative Severity — je nach Programm-Scope."
                ),
                evidence=[
                    f"URL: {http.get('url')}",
                    f"status: {http.get('status')}",
                    f"missing: {', '.join(missing)}",
                    f"present: {', '.join(sorted(headers.keys())[:20])}",
                ],
                reproduction=[
                    f"curl -sI https://{host}/ | grep -iE 'strict-transport|content-security|x-frame|x-content|referrer|permissions'",
                ],
                tested=True,
                confidence=0.7,
                recommendation="Prüfe Programm-Policy: Header-only Findings oft out-of-scope.",
            )
        )
    # cookies without Secure/HttpOnly flags (if Set-Cookie present)
    sc = headers.get("set-cookie") or ""
    if sc and "httponly" not in sc.lower():
        findings.append(
            Finding(
                title=f"Set-Cookie without HttpOnly observed on {host}",
                severity="low",
                host=host,
                category="session",
                summary="Mindestens ein Set-Cookie ohne HttpOnly-Flag im Response.",
                evidence=[f"Set-Cookie: {sc[:300]}"],
                reproduction=[f"curl -sI https://{host}/ | grep -i set-cookie"],
                tested=True,
                confidence=0.55,
                recommendation="Verifizieren, welche Cookies betroffen sind; Session-Cookies priorisieren.",
            )
        )
    return findings


def _check_cors(host: str) -> list[Finding]:
    """Single safe Origin probe."""
    url = f"https://{host}/"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _UA,
            "Origin": "https://evil.example",
            "Accept": "*/*",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            acao = (resp.headers.get("Access-Control-Allow-Origin") or "").strip()
            acac = (resp.headers.get("Access-Control-Allow-Credentials") or "").strip()
            if acao == "*" or acao == "https://evil.example":
                sev = "medium" if acac.lower() == "true" and acao != "*" else "low"
                return [
                    Finding(
                        title=f"Permissive CORS on {host}",
                        severity=sev,
                        host=host,
                        category="cors",
                        summary=f"ACAO={acao!r} ACAC={acac!r}",
                        evidence=[
                            f"Request Origin: https://evil.example",
                            f"Access-Control-Allow-Origin: {acao}",
                            f"Access-Control-Allow-Credentials: {acac}",
                            f"status: {getattr(resp, 'status', 200)}",
                        ],
                        reproduction=[
                            f"curl -sI -H 'Origin: https://evil.example' https://{host}/ "
                            f"| grep -i access-control",
                        ],
                        tested=True,
                        confidence=0.65,
                        recommendation="Prüfe, ob sensible Endpoints betroffen sind.",
                    )
                ]
    except Exception as exc:
        log.debug("cors check %s: %s", host, exc)
    return []


def run_program_scan(
    program_id: str,
    *,
    max_hosts: Optional[int] = None,
) -> dict[str, Any]:
    blocked = _owner_ok()
    if blocked:
        return {"ok": False, "error": blocked}

    program = get_program(program_id)
    if not program:
        return {
            "ok": False,
            "error": f"Unbekanntes Programm '{program_id}'. "
            f"Konfiguriere {PROGRAMS_PATH} (siehe example).",
        }
    if not program.get("authorized"):
        return {
            "ok": False,
            "error": (
                f"Programm '{program.get('id')}' ist nicht als authorized markiert. "
                "Setze authorized=true erst nach Scope-Lesen und Teilnahme."
            ),
        }
    if not program.get("enabled", True):
        return {"ok": False, "error": f"Programm '{program.get('id')}' ist disabled."}

    hosts = [h for h in (program.get("in_scope_hosts") or []) if isinstance(h, str)]
    # expand via crt.sh for root domains (no wildcards only)
    roots = [h for h in hosts if h.count(".") >= 1 and not h.startswith("*.")]
    limit = int(max_hosts or program.get("max_hosts") or 8)
    limit = max(1, min(25, limit))

    discovered: list[str] = []
    for root in roots[:3]:
        # only one label roots like example.com
        if root.count(".") == 1 or root.startswith("www."):
            base = root[4:] if root.startswith("www.") else root
            discovered.extend(_crtsh_subdomains(base, limit=limit))
    all_hosts: list[str] = []
    for h in hosts + discovered:
        h = h.lower().lstrip("*.")
        if _host_in_scope(h, program) and h not in all_hosts:
            all_hosts.append(h)
    all_hosts = all_hosts[:limit]

    findings: list[Finding] = []
    host_reports: list[dict[str, Any]] = []
    started = time.time()

    for host in all_hosts:
        ips = _dns_a(host)
        tls = _tls_info(host) if ips else {"ok": False, "error": "no_dns"}
        http = _http_get(f"https://{host}/")
        host_findings = []
        if http.get("status") or http.get("ok"):
            host_findings.extend(_check_security_headers(host, http))
            host_findings.extend(_check_cors(host))
        if not ips:
            host_findings.append(
                Finding(
                    title=f"No DNS A/AAAA for in-scope host {host}",
                    severity="info",
                    host=host,
                    category="recon",
                    summary="Host in Scope, aber aktuell nicht auflösbar.",
                    evidence=["dns lookup empty"],
                    reproduction=[f"dig +short {host} A"],
                    tested=True,
                    confidence=0.8,
                )
            )
        findings.extend(host_findings)
        host_reports.append({
            "host": host,
            "ips": ips,
            "tls": tls,
            "http_status": http.get("status"),
            "http_url": http.get("url"),
            "findings": len(host_findings),
        })

    # program-level info finding if nothing material
    material = [f for f in findings if f.severity not in {"info", "none"}]
    if not material:
        findings.append(
            Finding(
                title="No material issues in passive scan",
                severity="none",
                host=all_hosts[0] if all_hosts else "n/a",
                category="summary",
                summary=(
                    "Passive Checks abgeschlossen. Keine medium+ Findings. "
                    "Das ist ein getestetes Negativ-Ergebnis, kein 'alles safe'."
                ),
                evidence=[
                    f"hosts_scanned={len(all_hosts)}",
                    f"info_findings={sum(1 for f in findings if f.severity == 'info')}",
                ],
                reproduction=["Siehe host_reports.json im Report-Ordner"],
                tested=True,
                confidence=0.9,
                recommendation="Nächster Schritt: gezielte manuelle Auth-Flows in-scope.",
            )
        )

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = REPORT_ROOT / str(program.get("id")) / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "ok": True,
        "program_id": program.get("id"),
        "program_name": program.get("name"),
        "platform": program.get("platform"),
        "program_url": program.get("program_url"),
        "started_unix": started,
        "duration_s": round(time.time() - started, 2),
        "mode": "passive",
        "allow_active": bool(program.get("allow_active")),
        "hosts": all_hosts,
        "host_reports": host_reports,
        "findings": [asdict(f) for f in findings],
        "counts": {
            "hosts": len(all_hosts),
            "findings": len(findings),
            "material": len(material),
        },
        "policy": {
            "authorized_only": True,
            "out_of_scope_respected": True,
            "destructive": False,
        },
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "report.md").write_text(format_report_md(report), encoding="utf-8")
    (out_dir / "host_reports.json").write_text(
        json.dumps(host_reports, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    AuditLog.action(
        "BugBounty",
        "scan",
        f"program={program.get('id')} hosts={len(all_hosts)} findings={len(findings)}",
        erfolg=True,
    )
    report["report_dir"] = str(out_dir)
    return report


def format_report_md(report: dict[str, Any]) -> str:
    lines = [
        f"# Bug-Bounty Report — {report.get('program_name') or report.get('program_id')}",
        "",
        f"- program_id: `{report.get('program_id')}`",
        f"- platform: {report.get('platform')}",
        f"- mode: {report.get('mode')} (destructive=false)",
        f"- hosts: {report.get('counts', {}).get('hosts')}",
        f"- findings: {report.get('counts', {}).get('findings')} "
        f"(material={report.get('counts', {}).get('material')})",
        f"- duration_s: {report.get('duration_s')}",
        "",
        "## Findings",
        "",
    ]
    for i, f in enumerate(report.get("findings") or [], 1):
        lines.append(f"### {i}. [{f.get('severity')}] {f.get('title')}")
        lines.append("")
        lines.append(f"- host: `{f.get('host')}`")
        lines.append(f"- category: {f.get('category')}")
        lines.append(f"- tested: {f.get('tested')} · confidence: {f.get('confidence')}")
        lines.append(f"- summary: {f.get('summary')}")
        if f.get("recommendation"):
            lines.append(f"- recommendation: {f.get('recommendation')}")
        lines.append("- evidence:")
        for e in f.get("evidence") or []:
            lines.append(f"  - `{e}`")
        lines.append("- reproduction:")
        for e in f.get("reproduction") or []:
            lines.append(f"  - `{e}`")
        lines.append("")
    lines.append("## Policy")
    lines.append("")
    lines.append("Nur autorisierte In-Scope-Hosts. Kein Claim ohne Evidence.")
    lines.append("")
    return "\n".join(lines)


def format_programs_report() -> str:
    rows = list_programs()
    if not rows:
        return (
            "[BugBounty] Keine Programme konfiguriert.\n"
            f"Lege `{PROGRAMS_PATH}` an (Vorlage: `{PROGRAMS_EXAMPLE}`)."
        )
    lines = ["[BugBounty] Programme", ""]
    for r in rows:
        flag = "ON " if r["enabled"] and r["authorized"] else "OFF"
        lines.append(
            f"  {flag} {r['id']} — {r['name']} ({r['platform']}) "
            f"auth={r['authorized']} hosts={','.join(r['hosts'][:4]) or '—'}"
        )
    lines.append("")
    lines.append("Scan: bug bounty scan <program_id>")
    return "\n".join(lines)


def format_scan_report(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return f"[BugBounty] {result.get('error', 'Fehler')}"
    lines = [
        f"[BugBounty] {result.get('program_name')} ({result.get('program_id')})",
        f"hosts={result.get('counts', {}).get('hosts')} "
        f"findings={result.get('counts', {}).get('findings')} "
        f"material={result.get('counts', {}).get('material')} "
        f"mode={result.get('mode')}",
        f"report: {result.get('report_dir')}",
        "",
    ]
    for f in (result.get("findings") or [])[:12]:
        lines.append(f"  [{f.get('severity')}] {f.get('title')}")
        if f.get("summary"):
            lines.append(f"    {f.get('summary')[:160]}")
    lines.append("")
    lines.append("Vollreport: report.md im Report-Ordner")
    return "\n".join(lines)
