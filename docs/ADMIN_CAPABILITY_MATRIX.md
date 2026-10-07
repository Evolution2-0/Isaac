# Admin-Modus — Capability-Matrix (ehrlich)

Referenz: Was Isaac mit `ISAAC_PRIVILEGE_MODE=admin` **kann**, **teilweise kann** und **nicht kann**.
Stand: Isaac v5.3+ | S8-Linux-Root + Termux-Variante.

---

## Aktivierung prüfen

```bash
grep ISAAC_PRIVILEGE_MODE .env .env.local 2>/dev/null
python3 -c "from config import is_owner_equivalent_mode; from computer_use import detect_runtime; print('admin=', is_owner_equivalent_mode(), 'runtime=', detect_runtime())"
```

Erwartung auf deinem S8 (Linux-Root): `admin= True`, `runtime= s8`.

---

## Legende

| Symbol | Bedeutung |
|--------|-----------|
| ✅ | Zuverlässig im Admin-Modus |
| ⚠️ | Möglich, aber mit Einschränkungen / Erkennung nötig |
| ❌ | Nicht verfügbar oder architektonisch blockiert |
| 🔒 | Immer blockiert (auch Admin) |

---

## 1. Governance & Sicherheit

| Capability | Admin | User-Modus | Anmerkung |
|------------|-------|------------|-----------|
| PrivilegeGate (alle Rechte) | ✅ | ❌ | `privilege.py` → `ADMIN_MODE` |
| SUDO-Passwort | ✅ (entfällt) | ⚠️ | `sudo_gate` nicht nötig |
| Bestätigungsdialoge | ✅ (entfällt) | ⚠️ | `security_policy.py` |
| Verfassungs-Override | ✅ (meist) | ❌ | Auto-Override im Admin |
| Verfassung selbst ändern | 🔒 | 🔒 | `constitution_not_self_editable` |
| Audit-Logging | ✅ (immer an) | ✅ | `data/audit.jsonl` |
| Pause-Gate | ✅ (bypass) | ⚠️ | Admin vorautorisiert |

**Fazit:** Admin = volle Ausführungsfreiheit innerhalb der Architektur, **nicht** ohne Governance-Spuren.

---

## 2. Ausführungswege

| Weg | Admin | Beschreibung |
|-----|-------|--------------|
| Owner-Action (Natursprache) | ✅ | Imperative Befehle → `owner_action.py` |
| Explizite Prefixe (`Suche:`, `führe aus:`) | ✅ | Kernel-Intent-Pfade |
| Normaler Chat | ⚠️ | LLM-Antwort, **keine** opportunistischen Tools |
| Proaktive Tasks (Background) | ✅ | `owner_autonomy.py`, konfigurierbar |
| Computer-Use / Agent | ✅ | `computer_use.py`, Shell-Fragment-Block **aus** im Admin |

**Wichtig:** Nicht jeder Satz wird Befehl. „Erkläre mir …" bleibt Chat.

---

## 3. S8-Linux-Root (dein Setup)

| Capability | S8 | Termux | Anmerkung |
|------------|-----|--------|-----------|
| Shell (`führe aus: …`) | ✅ | ✅ | Prozess-Kontext von Isaac, nicht magischer OS-Root |
| Dateizugriff `full` | ✅ | ⚠️ | Termux: `termux-setup-storage` nötig |
| WLAN-Status (`iwgetid`, `ip`) | ✅ | ⚠️ | Termux: `termux-wifi-*` |
| WLAN-Scan | ⚠️ | ⚠️ | S8: Shell-Tools; Termux: API |
| WLAN Auto-Join | ⚠️ | ⚠️ | Oft nur gespeicherte Netze / Settings öffnen |
| Akku / Speicher | ✅ | ⚠️ | Termux: `termux-battery-status` |
| GPS / Standort | ⚠️ | ⚠️ | Termux-API oder Browser-Fallback |
| Screenshot | ⚠️ | ⚠️ | Runtime-abhängig |
| Android-Intents (Apps öffnen) | ⚠️ | ✅ | Termux: `am start`; S8: oft Browser/URL |
| TTS / Notification / Torch | ❌/⚠️ | ✅ | Primär Termux-API |

---

## 4. Owner-Actions (Natursprache) & Autarke Root-Fähigkeiten

Vollständige Befehlsliste: [OWNER_COMMANDS.md](OWNER_COMMANDS.md)

| Kategorie | Status | Implementierung & Steuerung |
|-----------|--------|-----------------------------|
| Dateien (lesen/schreiben/kopieren/löschen) | ✅ | `file_access.py` & `owner_action.py` (Cleanup schützt `.git`, `.env`, `isaac.db`) |
| Shell & Root-Befehle | ✅ | `computer_use.py` (Im Admin-Modus entfällt Shell-Fragment-Sperre) |
| Apps & Pakete installieren | ✅ | `updater.py` & `owner_action.py` (`installiere paket ...`, `pip`, `apt`, `pkg`) |
| Eigenen Code umschreiben (Self-Coding) | ✅ | `code_edit.py` (Aider SEARCH/REPLACE), `repo_map.py` & `git_ops.py` |
| Web-Suche & Browser-Navigation | ✅ | `search.py` & `browser.py` (Playwright-Interaktion, Formulare, Scrape) |
| Webseiten/Apps erstellen & servieren | ✅ | Local File Creation + HTTP/Dashboard Binding (`workspace/`, `web/`) |
| WLAN / Router | ⚠️ | Plattform + gespeicherte Credentials (`owner_action.py`) |
| Gmail / Kalender / Fotos | ⚠️ | Browser-Login des Owners nötig |
| Akku / Speicher / Prozesse | ✅ | Native Shell & OS Metrics |
| Isaac-Ops (`isaac status`) | ✅ | Kernel Control & Background Loops |

---

## 5. Proaktive Autonomie & Selbst-Umschreiben (Autarkie)

| Task | Standard | Konfiguration |
|------|----------|---------------|
| Nightly Downloads-Cleanup | ✅ | `runtime_settings.json` → `owner_autonomy` |
| Weekly Deep-Cleanup (So) | ✅ | `.env`: `ISAAC_OWNER_AUTONOMY_WEEKLY_*` |
| Daily Isaac-Health | ✅ | Zeitfenster konfigurierbar |
| Gesamt deaktivieren | — | `ISAAC_OWNER_AUTONOMY=0` |

Läuft nur bei `ISAAC_PRIVILEGE_MODE=admin`.

---

## 6. Was Isaac **nicht** ist

| Erwartung | Realität |
|-----------|----------|
| „Wie ich am Handy — jede App, jeder Tap" | ❌ Kein vollwertiger UI-Automatisierungs-Ersatz für alle Apps |
| „Jeder Satz = Aktion" | ❌ Chat-Pfad bleibt für Erklärungen/Dialog |
| „Ohne jegliche Regeln" | ❌ Verfassung, Audit, Erkennungsmuster, Plattform-Limits |
| „Cloud-Admin überall" | ❌ Lokal/trusted device only |
| „Selbst-Verfassung umschreiben" | 🔒 Immer blockiert |

---

## 7. Du vs. Isaac (Admin)

```
Du (Owner)                 Isaac (Admin-Modus)
─────────────────────────────────────────────────
Voller OS-/UI-Zugriff      Definierte Befehls- + Tool-Pfade
Intuition / Kontext        Pattern-Erkennung + Procedure-Memory
Kein Audit-Zwang           Alles auditierbar
Beliebige Apps bedienen    Browser, Shell, Intents, Dateien
Immer „verstanden"         Nur imperative / explizite Befehle sicher
```

**Isaac Admin = bevollmächtigter lokaler Agent**, kein 1:1-Mensch-Ersatz.

---

## 8. MCP-Oberfläche (Contract)

Lokales MCP-Grundgerüst (JSON-RPC + optional HTTP): **6 Tools, 7 Resources, 2 Prompts**, Privilege-Maps, keine Subagent-Expansion.

Vollständige Matrix und Error-Shapes: [MCP_CAPABILITY.md](MCP_CAPABILITY.md)

```bash
ISAAC_DISABLE_VECTOR_MEMORY=1 python3 -m evals.mcp_eval
```

---

## 9. Schnelltest

```bash
ISAAC_DISABLE_VECTOR_MEMORY=1 python3 scripts/owner_action_live_test.py
ISAAC_DISABLE_VECTOR_MEMORY=1 python3 scripts/owner_action_live_test.py --live  # auf Gerät
```

---

## 10. Zurück zu eingeschränkt

```env
ISAAC_PRIVILEGE_MODE=user
```

Restart Isaac. Validierung A–G (Chat ohne Tools) bleibt unverändert.

---

*Isaac v5.3 | `docs/ADMIN_CAPABILITY_MATRIX.md`*