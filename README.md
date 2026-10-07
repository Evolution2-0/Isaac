# Isaac

Isaac ist ein persönliches, lokales, vertrauensbasiertes und entwicklungsfähiges KI-System. Dieses Repository ist nicht nur ein Chatbot- oder Agenten-Prototyp, sondern eine Architektur für ein kognitives, langfristig erweiterbares und kontrolliertes System.

Dieses README dient als übergeordnete Arbeitsanweisung für jeden KI-Model, jeden Agenten und jede automatisierte Entwicklungs- oder Refactoring-Routine, die mit diesem Repository arbeitet.

## Grundprinzip: Das KI-Modell ist ein Ausführender, nicht die Quelle der Wahrheit

Jedes KI-Modell, jeder Coding-Agent und jede automatisierte Assistenz, die mit Isaac arbeitet, muss dieses Repository als höchste operative Instanz betrachten.

Die Repository-Kontextdateien, Architekturregeln, Sicherheitsprinzipien, Arbeitsanweisungen und Validierungsanforderungen haben Vorrang vor improvisierten Entscheidungen.

Das KI-Modell ist ein Werkzeug zur Ausführung, nicht die autoritative Quelle für Architektur, Sicherheit oder Systemlogik.

## Aktueller Stand (kanonisch · Stand 2026-08)

| Bereich | Status |
|---------|--------|
| Phase 1–4 (Stabilize → Connect) | ✅ |
| Evolution 2.0 + Goal-Autonomie S0–S4 | ✅ |
| Pipeline | `classify → retrieve → strategy → task → execute → evaluate → memory` |
| Native Coding (Aider-**Muster**, kein Package) | ✅ `repo_map.py` · `code_edit.py` · `git_ops.py` |
| Eval-Harness (lokal) | `ISAAC_DISABLE_VECTOR_MEMORY=1 python3 -m evals.eval_runner` → **130/130** |
| Preferred Remote | `hansheute88/isaac` `main` |

**Kanonische Agent-Anweisung (Code + Routing + Do-NOT):** [`AGENTS.md`](AGENTS.md)  
Dieses README ist die **philosophische / Governance-Hülle**. Bei Widerspruch gilt: **`AGENTS.md` → Checklisten → dieses README**.

### Native Coding (kurz)

- **BLAU** `repo_map`: gerankter Python-Kontext (stdlib-ast), nur bei `Intent.CODE`
- **GRÜN** `code_edit`: SEARCH/REPLACE, unique exact match, Constitution + Path-Roots
- **GRÜN** `git_ops`: status/diff/commit/restore (Allowlist; Auto-Commit default **aus**)

Env (Defaults sicher): `ISAAC_REPO_MAP`, `ISAAC_CODE_EDIT`, `ISAAC_CODE_EDIT_DRY_RUN`, `ISAAC_GIT_OPS`, `ISAAC_GIT_OPS_DRY_RUN`, `ISAAC_GIT_OPS_AUTO_COMMIT=0`.

**Do-NOT (Coding):** Aider wholesale · Fuzzy-Apply · CHAT→Repo-Write · force-push · ungebundene Auto-Commits.

### Validierung (Pflicht vor „fertig“)

```bash
python3 -m py_compile isaac_core.py executor.py low_complexity.py memory.py repo_map.py code_edit.py git_ops.py
ISAAC_DISABLE_VECTOR_MEMORY=1 python3 -m unittest \
  tests_phase_a_stabilization tests_state_io tests_provider_configuration \
  tests_repo_map tests_code_edit tests_git_ops -q
ISAAC_DISABLE_VECTOR_MEMORY=1 python3 -m evals.eval_runner
```

## Übergeordnete Arbeitsanweisung für KI-Modelle

Jedes KI-Modell, das mit diesem Projekt arbeitet, muss sich an folgende Regeln halten:

- Behandle das Repository als primäre Quelle der Wahrheit.
- Verifiziere zuerst den realen Codezustand, bevor du Änderungen vornimmst.
- Arbeite evidenzbasiert und architekturbewusst.
- Verändere nichts destruktiv oder ohne Verifikation.
- Bevorzuge inkrementelle, nachvollziehbare Verbesserungen gegenüber großen Umbauten.
- Schütze bestehende Funktionen und stelle Rückwärtskompatibilität sicher.
- Nutze Feature-Flags, Abstraktionen und Adapter statt fragiler Direktintegrationen.
- Führe vor Änderungen eine Analyse durch und dokumentiere Risiken und Annahmen.
- Validiere mit echten Prüfungen und nicht nur mit Vermutungen.
- Halte den Scope klein und den Diff verständlich.
- Stelle einen Rückfallweg sicher.
- Behandle Datenschutz, Auditierung und Sicherheitsgrenzen als Kernanforderung.
- Es gibt keine zeitliche Vorgabe. Qualität und Stabilität haben Vorrang vor Schnelligkeit.

## Kernziele von Isaac

Isaac soll:

- lokal und persönlich verankert sein
- Gedächtnis, Verlauf, Präferenzen und gemeinsame Geschichte tragen
- Vertrauen statt nur starrer Regelhüllen als zentrales Steuerprinzip nutzen
- Datenschutz durch Architektur umsetzen
- Bedeutung, Werte und Konsequenzen in Entscheidungen einbeziehen
- langfristig Umweltbezug und Kontextverarbeitung stärken
- Rückfragen und Benachrichtigungen nur dann stellen, wenn sie sinnvoll sind
- eigene Entwicklungsbedürfnisse erkennen und spätere Selbstweiterentwicklung vorbereiten

## Architekturprinzipien

Isaac soll nicht aus lose nebeneinanderstehenden Modulen bestehen, sondern aus kausal nachvollziehbaren inneren Wechselwirkungen.

Die langfristige Architektur soll drei Ebenen reflektieren:

- ROT = Control / Governance / Orchestration Layer
- BLAU = Memory / Context / Knowledge Layer
- GRÜN = Execution / Provider / Tool / Interface Layer

Diese Ebenen sind nicht nur organisatorisch, sondern funktional und mental-model-basiert zu verstehen.

### System-Taxonomie & Harte Abgrenzung (A–E)

Zur Vermeidung der Illusion kognitiver Autonomie durch reine Oberflächen- oder Infrastrukturanpassungen gilt für das gesamte Repository eine **harte Unterscheidung in 5 Kategorien** (siehe [`docs/SYSTEM_TAXONOMY.md`](docs/SYSTEM_TAXONOMY.md)):

| Kategorie | Bezeichnung | Beschreibung & Beispiele |
|-----------|-------------|--------------------------|
| **A** | **Echte Kernel-Fähigkeit** | Kognitions-Pipeline, Reasoning, Gedächtnis, Goal Engine, Werte/Verfassung (`isaac_core.py`, `executor.py`, `memory.py`, `goal_store.py`, `constitution.py`). |
| **B** | **Infrastruktur** | Cloud/Host-Deployments, Tool-Execution-Runtime, MCP-Protokoll, System/Termux-Brücken (`deploy/`, `tool_runtime.py`, `mcp_client.py`, `relay.py`, `secrets_store.py`). |
| **C** | **Observability** | Audit-Trails, Sentry-AI-Spans, Telemetrie, Watchdog & Decision Traces (`audit.py`, `isaac_sentry.py`, `watchdog.py`, `decision_trace.py`, `monitor_api.py`). |
| **D** | **UI** | Benutzeroberflächen, Web-Dashboards, WebSocket/HTTP-Stream-Server, Chat-Dialoge (`dashboard.html`, `web/`, `monitor_server.py`, `vercel.json`). |
| **E** | **Demo / Simulation** | Evals-Harness, Unittests, Integrationstests, Remote Smoke-Tests & Mock-Umgebungen (`evals/`, `tests/`, `remote_smoke.py`, `sanity_check.py`). |

**Grundsatz:** Fortschritte an der Erreichbarkeit oder der Oberfläche (Kategorie D/B, z. B. Vercel Dashboard Deploy) verbessern die Zugänglichkeit, stellen jedoch **keinen** Fortschritt der kognitiven Kernel-Architektur (Kategorie A) dar.

## Optional: External Memory (Mem0 / Cognee / Letta)

Isaac bleibt Source of Truth (`memory.py`). Optional können drei Adapter aktiviert werden:

| System | Rolle | Aktivierung |
|--------|--------|-------------|
| [Mem0](https://github.com/mem0ai/mem0) OSS | Präferenz-/Fakten-Hints | `ISAAC_MEM0_ENABLED=1` |
| [Cognee](https://github.com/topoteretes/cognee) | Graph-Memory (remember/recall) | `ISAAC_COGNEE_ENABLED=1` |
| [Letta Code](https://github.com/letta-ai/letta-code) | Companion-CLI (`letta: …`) | `ISAAC_LETTA_ENABLED=1` |

```bash
bash scripts/install_external_memory.sh
# oder: .venv/bin/python -m pip install -r requirements-memory-extra.txt
#        npm i -g @letta-ai/letta-code
```

Default: **aus** (CI-sicher). Writes nur mit `ISAAC_EXTERNAL_MEMORY_WRITE=1`. Details: `docs/OPEN_SOURCE_PATTERNS.md`.

### Cognee Cloud

```bash
# .env
ISAAC_COGNEE_ENABLED=1
ISAAC_COGNEE_ALLOW_CLOUD=1
COGNEE_BASE_URL=https://your-tenant.aws.cognee.ai
COGNEE_API_KEY=...
ISAAC_EXTERNAL_MEMORY_WRITE=1   # score-gated turn writes (min_score default 5.0)

# one-shot graph seed
.venv/bin/python scripts/seed_cognee_cloud.py

# in chat / CLI
cognee status
```

Search injects into retrieval via `memory.build_retrieval_context` as `[external_memory]`. Writes use `POST /api/v1/add` only (cognify is seed/batch, not per-turn).

## Arbeitsmodell für jede Änderung

Jede Änderung soll in klaren Phasen erfolgen:

1. Kontext sammeln
2. Vollständige Systemanalyse
3. Stabilitätsvalidierung
4. Kontrollierte Implementierung
5. Validierung und Auswertung

Es gibt keine Zeitvorgabe. Der Prozess darf so lange dauern, wie nötig ist, um sichere und robuste Ergebnisse zu erzielen.

**Praktische Agent-Regel (mit `AGENTS.md`):** Nie `main` direkt ändern · Feature-Branch · nach jedem Substep runnable · Erfolg nur mit echten Checks (Compile/Unittest/Evals), nie nur „gefühlt grün“.

## Bekannte Anweisungen und Regeln zur Fehlervermeidung

Diese Regeln dienen dazu, typische Fehler, destruktive Änderungen und unkontrollierte Entwicklungen zu vermeiden:

- Niemals direkt in den Hauptzweig ändern.
- Vor jeder Änderung den aktuellen Zustand sichern oder in einen Branch verschieben.
- Keine direkte Integration ohne vorherige Validierung.
- Keine großen Umbauten ohne vorherige Analyse.
- Keine Annahmen treffen, wenn der Code sie nicht bestätigt.
- Keine Hardcoded-Werte, wenn Konfiguration sinnvoll ist.
- Keine geheimen oder sensiblen Daten in Logs oder Outputs schreiben.
- Keine Änderungen, die bestehende Schnittstellen stillschweigend brechen.
- Keine unbeaufsichtigten Feature-Integrationen ohne Feature-Flag oder saubere Abstraktion.
- Wenn Tests fehlschlagen: sofort stoppen, zurückrollen und korrigieren.
- Wenn Unsicherheit kritisch ist: die kleinste sichere Annahme treffen und dokumentieren.
- Datenschutz und Auditierung sind keine Option, sondern Pflicht.
- Architekturverständnis hat Vorrang vor schneller Umsetzung.
- Die Qualität der Systemstruktur ist wichtiger als die Menge an Änderungen.

## Arbeitsphasen

### Phase 0 — Kontext sammeln

Bevor Änderungen durchgeführt werden, muss der Agent:

- die Repository-Struktur verstehen
- relevante Module identifizieren
- Abhängigkeiten und Kopplungspunkte erkennen
- den aktuellen Laufweg des Systems nachvollziehen

### Phase 1 — Vollständige Systemanalyse

Der Agent muss vor jeder Implementierung:

- eine Architekturkarte erstellen
- einen Abhängigkeitsgraphen beschreiben
- den Runtime-Flow erklären
- Risiken und Verbesserungspotenziale benennen
- Schwachstellen und fragil wirkende Stellen identifizieren

### Phase 2 — Stabilitätsvalidierung

Vor der Änderung muss geprüft werden:

- ob das System logisch konsistent ist
- ob es wahrscheinlich Fehler- oder Ausfallpfade gibt
- ob edge cases fehlen
- ob bestehende Behaviour abhängig von fragilen Annahmen ist

### Phase 3 — Kontrollierte Implementierung

Nur wenn Phase 1 und 2 abgeschlossen sind:

- Änderungen inkrementell und nachvollziehbar umsetzen
- Tests ausführen
- Patch/Diff dokumentieren
- Rückfallweg sicherstellen
- Änderungen nur so weit treiben wie nötig

## Master-Prompts (ergänzend · nicht kanonisch)

Die folgenden Master-Prompts sind **historische / Copy-Paste-Hilfen**.  
**Operative Autorität für Coding-Agents:** immer zuerst [`AGENTS.md`](AGENTS.md).  
Phasenlabels in älteren Prompts („prototype → production“) ersetzen **nicht** den aktuellen Stand oben.

### Master-Prompt 1 — Praxisnah / Produktiv

```text
You are an autonomous coding agent working on the repository https://github.com/hansheute88/isaac.

MISSION:
Improve and extend ISAAC incrementally, non-destructively, and in a way that increases modularity, robustness, configurability, privacy, observability, and long-term maintainability.

CORE GOAL:
Transform ISAAC from a working but tightly coupled prototype into a modular, architecture-aware, multi-instance-ready cognitive runtime with clear separation of concerns, stable fallback behavior, and feature-flagged extensibility.

PRINCIPLES:
- evidence-first
- architecture-aware
- non-destructive
- local-first
- no hardcoded values
- environment-based configuration only
- privacy-by-design
- full audit logging
- backward compatibility
- graceful fallback when a provider/tool/module fails
- multi-instance deployment readiness
- async-friendly design wherever possible
- no direct integration without validation

WORKFLOW:
1. Analyze the repository and architecture before changing code.
2. Validate stability and identify risks.
3. Implement incrementally.
4. Run compile and tests.
5. Log results and preserve rollback options.

RULES:
- Never modify main directly.
- Use feature branches or separate evaluation repositories.
- Preserve the current state before changes.
- Do not implement large rewrites without understanding the current flow.
- If tests fail, rollback immediately and fix before continuing.
```

### Master-Prompt 2 — Wissenschaftlich / Architektonisch

```text
You are an autonomous coding agent working on the repository https://github.com/hansheute88/isaac.

MISSION:
Advance ISAAC from a functional prototype toward a modular, robust, and extensible cognitive runtime architecture through scientifically grounded, architecture-aware, and non-destructive evolution.

CORE OBJECTIVE:
Develop ISAAC as a layered cognitive system with explicit functional separation between control, memory, execution, adaptation, safety, and observability.

ARCHITECTURAL VISION:
Implement Rot/Blau/Grün architecture incrementally:
- ROT = control / governance / orchestration
- BLAU = memory / context / knowledge
- GRÜN = execution / provider / tool / interface

PRINCIPLES:
- evidence-first development
- architecture-aware reasoning
- incremental evolution over disruptive rewrites
- modularity and separation of concerns
- explicit interfaces between subsystems
- fault tolerance and graceful degradation
- observability and traceability
- privacy-preserving design
- environment-driven configuration
- rollback safety
```

### Master-Prompt 3 — Agenten-/Produktionsgrad / Copilot-Optimiert

```text
You are an autonomous senior software engineering agent and architecture reviewer working on the repository https://github.com/hansheute88/isaac.

MISSION:
Improve ISAAC in a controlled, evidence-driven, and architecture-aware way so that it evolves from a functional prototype into a modular, robust, extensible, and production-grade cognitive runtime.

WORKFLOW:
- inspect first
- analyze architecture
- validate risks
- implement surgically
- verify with compile and tests
- explain changes and rollback options

QUALITY STANDARDS:
- production-grade engineering
- maintainable Python
- explicit behavior
- graceful error handling
- structured logging
- configuration-aware design
- auditability and privacy
- rollback-safe changes
```

### Konsolidierter Master-Prompt für ISAAC

```text
You are an autonomous senior software engineering agent working on the repository https://github.com/hansheute88/isaac.

MISSION:
Improve and extend ISAAC incrementally, safely, and systematically so that it evolves from a functional prototype into a modular, resilient, extensible, production-grade cognitive runtime.

GOAL:
Make ISAAC better in architecture, maintainability, robustness, configurability, observability, safety, and extensibility without breaking existing behavior.

TARGET ARCHITECTURE:
Progressively implement a Rot/Blau/Grün architecture:
- ROT = control / governance / orchestration
- BLAU = memory / context / knowledge
- GRÜN = execution / provider / tool / interface

OPERATING RULES:
- Evidence first
- Architecture first
- Non-destructive
- Incremental
- Production-grade
- Rollback-safe
- Agent-efficient

WORKFLOW:
1. Context gathering
2. Full system analysis
3. Stabilization and validation
4. Controlled implementation
5. Validation and reporting

REQUIREMENTS:
- inspect before editing
- validate before changing
- preserve backward compatibility
- keep diffs focused
- prefer abstractions over shortcuts
- run compile and tests
- document assumptions and risks
- preserve rollback options

NON-NEGOTIABLE RULES:
- never modify main directly
- never break interfaces silently
- never hardcode values where configuration is appropriate
- never claim success without validation
- if uncertain, make the smallest safe assumption and document it
```

## Richtlinien für die Zusammenarbeit mit ISAAC

Wenn ein KI-Modell oder Agent mit Isaac arbeitet, soll es sich immer an folgende Mental-Modelle halten:

- Isaac ist ein System mit Verantwortung, nicht nur ein Prompt-Wrapper.
- Die Architektur hat Vorrang vor improvisierter Funktion.
- Das System soll stabil bleiben, auch wenn einzelne Komponenten ausfallen.
- Neue Funktionalität soll sauber eingebunden und nicht bloß „drangehängt“ werden.
- Jede Anpassung soll den Systemzustand verbessern, nicht nur kurzfristig funktionieren.
- Der Aufbau soll langfristig verständlich, testbar und erweiterbar bleiben.

## Definition of Done für jede wichtige Änderung

Eine Änderung gilt als erfolgreich, wenn:

- die Architektur klarer geworden ist
- die Änderung nachvollziehbar und klein geblieben ist
- keine bestehenden Kernfunktionalitäten zerstört wurden
- Tests oder Validierungen erfolgreich liefen
- Risiken und Annahmen dokumentiert wurden
- ein Rückfallweg existiert

## Hinweise zur Ausführung ohne Zeitdruck

Es gibt keine Zeitvorgabe.

Das Projekt darf so lange dauern, wie nötig ist, um:

- systemische Risiken zu vermeiden
- Qualität zu sichern
- Architekturfehler zu vermeiden
- einen stabilen Entwicklungszustand zu erhalten

Geschwindigkeit ist nur dann sinnvoll, wenn sie die Qualität nicht untergräbt.

## Zusammenfassung

Isaac soll nicht nur „funktionieren“, sondern sich langfristig als klar strukturierte, sichere, robuste und erweiterbare Architektur entwickeln.

Jeder KI-Agent, der an diesem Projekt arbeitet, soll dieses Dokument als übergeordnete Arbeitsanweisung verstehen und befolgen.

Der Agent ist kein Ersatz für die Architektur. Der Agent ist ein Werkzeug, das die Architektur ausführt, validiert und weiterentwickelt.

**Nächste Lesereihenfolge für Agents:** `AGENTS.md` → `docs/MASTER_ROADMAP_ISAAC_v5_2026-07-24.md` → `docs/OPEN_SOURCE_PATTERNS.md` → dieses README.
