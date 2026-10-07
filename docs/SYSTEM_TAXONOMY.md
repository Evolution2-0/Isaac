# Isaac System-Taxonomie & Fähigkeitsebenen

Dieses Dokument definiert die **harte Abgrenzung** zwischen kognitiven Kernfähigkeiten und dem umgebenden Systemapparat.

Ohne diese Unterscheidung entsteht die Gefahr, dass Fortschritte in Erreichbarkeit, Oberfläche oder Infrastruktur fälschlicherweise als kognitiver Fortschritt oder autonome Kernel-Intelligenz bewertet werden.

---

## Die 5 Hauptkategorien (A–E)

| Kategorie | Bezeichnung | Scope & Verantwortung | Primäre Module & Komponenten |
|-----------|-------------|-----------------------|------------------------------|
| **A** | **Echte Kernel-Fähigkeit** | Kognitive Architektur, Pipeline, Reasoning, Gedächtnisstrukturen, Goal-Autonomie, Verfassung, Ethik & Selbstmodell. | `isaac_core.py`, `executor.py`, `memory.py`, `vector_memory.py`, `goal_store.py`, `decomposer.py`, `logic.py`, `constitution.py`, `owner_autonomy.py`, `learning_engine.py` |
| **B** | **Infrastruktur** | Laufzeitumgebung, Cloud-Deployments, Tool-Ausführung, MCP-Protokoll, System- & Hardware-Schnittstellen, Secrets & Konfiguration. | `deploy/`, `render.yaml`, `tool_runtime.py`, `mcp_client.py`, `mcp_server.py`, `termux_bridge.py`, `relay.py`, `secrets_store.py`, `config.py`, `code_edit.py`, `git_ops.py` |
| **C** | **Observability** | Audit-Trail, Sentry-Integration, Telemetrie, Watchdog, Decision-Trace-Logging, Systemüberwachung. | `audit.py`, `isaac_sentry.py`, `watchdog.py`, `decision_trace.py`, `monitor_api.py`, `scripts/check_deploy_sync.py` |
| **D** | **UI** | Benutzeroberflächen, Dashboard HTML, Web Frontend, WebSocket/HTTP Event-Server, interaktive Dialogschnittstellen. | `dashboard.html`, `web/`, `monitor_server.py`, `browser_chat.py`, `ki_dialog.py`, `launch_dashboard.sh`, `vercel.json` |
| **E** | **Demo / Simulation** | Eval-Harness, Unittests, Integrationstests, Smoke-Tests, Mock-Umgebungen & Sicherheits-Harness. | `evals/`, `tests/`, `tests_*.py`, `remote_smoke.py`, `bug_bounty.py`, `sanity_check.py` |

---

## Modul-Kartierung (Vollständig)

### Kategorie A: Echte Kernel-Fähigkeit (Cognitive Architecture)
- **Kognition & Pipeline:** `isaac_core.py`, `low_complexity.py`, `decomposer.py`, `decomposer_adaptive.py`, `dispatcher.py`
- **Ausführungslogik:** `executor.py`, `execution_contract.py`, `result_contract.py`
- **Gedächtnis & Wissen:** `memory.py`, `vector_memory.py`, `procedure_memory.py`, `external_memory/`, `forgetting_decay.py`, `meaning.py`, `values.py`
- **Ziel- & Autonomie-Engine:** `goal_store.py`, `goal_inquiry.py`, `goal_digest.py`, `owner_autonomy.py`, `owner_action.py`, `motivation.py`
- **Ethik & Governance:** `constitution.py`, `constitution_override.py`, `privilege.py`, `sudo_gate.py`, `security_policy.py`, `value_decisions.py`
- **Selbstmodell & Lernen:** `self_model.py`, `self_model_hooks.py`, `learning_engine.py`, `learning_policy.py`, `instincts.py`, `neural_core.py`, `reflection.py`

### Kategorie B: Infrastruktur (Platform & Tooling)
- **Tooling & Dispatch:** `tool_registry.py`, `tool_runtime.py`, `tool_policy.py`, `tool_catalog.py`, `tool_bridge.py`, `task_tool_state.py`
- **MCP Protocol:** `mcp_client.py`, `mcp_server.py`, `mcp_registry.py`, `mcp_jsonrpc.py`
- **Bridge & Cloud:** `relay.py`, `free_cloud.py`, `isaac_remote.py`, `openrouter_ensemble.py`, `agent_selection.py`
- **Native Coding Tools:** `repo_map.py`, `code_edit.py`, `git_ops.py`
- **System / Device Interfaces:** `termux_bridge.py`, `s8_remote/`, `android_apps.py`, `computer_use.py`, `browser.py`, `chrome_tabs.py`, `chrome_secrets.py`, `file_access.py`, `credential_access.py`
- **Konfiguration & Deployment:** `config.py`, `secrets_store.py`, `secrets_bootstrap.py`, `deploy/`, `render.yaml`, `install.sh`

### Kategorie C: Observability (Monitoring & Auditing)
- `audit.py` (Protokollierung sensiver Operationen)
- `isaac_sentry.py` (AI Transaction & Span Tracking)
- `watchdog.py` (Prozessüberwachung & Health)
- `decision_trace.py` (Kausale Entscheidungs-Spuren)
- `monitor_api.py`, `monitor_now.py` (Health- & Status-Endpoints)
- `scripts/check_deploy_sync.py` (Deployment-Synchronisationsprüfung)

### Kategorie D: UI (User Interface & Presentation)
- `dashboard.html` (Lokales Web-Dashboard UI)
- `web/` (Frontend-Dateien / Next.js Vercel App)
- `monitor_server.py` (Dashboard HTTP/WebSocket Stream-Server)
- `browser_chat.py`, `ki_dialog.py` (Chat-Oberflächen)
- `launch_dashboard.sh` (UI-Startskript)

### Kategorie E: Demo / Simulation / Testing
- `evals/` (`eval_runner.py` Benchmark Suite)
- `tests/` (`tests_*.py` Unittests & Integrationstests)
- `remote_smoke.py` (Smoke-Test Skripte)
- `bug_bounty.py` (Security & Red-Teaming Harness)
- `sanity_check.py` (Import- & Syntax-Validierung)

---

## Fallstudie & Bewertung von PR #14

**PR #14 ("Configure Vercel dashboard deploy"):**
- **Inhalt:** `vercel.json`, `.vercelignore`, Health Endpoint, WebSocket Fallback auf Render.
- **Klassifizierung:** **Kategorie D (UI)** & **Kategorie B (Infrastruktur)**.
- **Bewertung:** PR #14 verbessert die Erreichbarkeit und Bereitstellung der öffentlichen Oberfläche. Er verändert **nicht** die kognitive Architektur, das Reasoning, die Ziel-Autonomie oder den Kernel.
- **Regel:** Fortentwicklung an Oberflächen (Kategorie D) oder Deploys (Kategorie B) darf niemals als Fortschritt der Kern-Autonomie (Kategorie A) deklariert werden.

---

## Anwendungsregeln für Pull Requests & Roadmaps

1. **Jede PR und welches Feature muss kategorisiert werden (A, B, C, D oder E).**
2. **Kategorie A hat Vorrang bei der Bewertung kognitiver Fortschritte.**
3. **Infrastruktur- und UI-Commits sind dienend und dürfen keinen autonomen Kernfortschritt vortäuschen.**
