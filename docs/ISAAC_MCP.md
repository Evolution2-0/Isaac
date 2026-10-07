# Isaac MCP

## Zweck

'isaac_mcp.py' ist die kontrollierte Außenkante zwischen MCP-Clients und Isaacs
bestehender 'MCPRegistry'.

MCP ist **Infrastruktur**, nicht Governance. Ein MCP-Client erhält keine
Isaac- oder Owner-Rechte dadurch, dass er das MCP-Protokoll sprechen kann.

Runtime-Reihenfolge:

1. Transport
2. Authentifizierung
3. MCP-Exposure-Policy
4. Isaac Privilege Gate
5. Constitution-Gate
6. Registry-Handler
7. Result Contract + Audit

## Autonomy Control Plane

Isaac exposes `isaac.autonomy_evaluate` as a governed, read-only decision tool. It composes the existing governance primitives without executing the requested action:

`intent -> memory -> goal -> permission -> safety -> confirmation -> proposal -> separate executor -> audit`

The result always contains `execution_authorized: false` at this boundary. A high-risk action can create a persistent confirmation review through the existing ConfirmationPolicy. The executor is deliberately outside the MCP registry.

Remote MCP callers are clamped to TASK privilege and cannot supply an owner override. The control plane therefore acts as a single decision point rather than a second executor.

## Exponierte Werkzeuge

| Tool | Scope | Funktion |
|---|---|---|
| 'isaac.task_status' | read | Taskstatus lesen |
| 'isaac.audit_recent' | read | Audit-Tail lesen |
| 'isaac.query_memory' | read | Retrieval-Kontext lesen |
| 'isaac.goal_list' | read | Owner-Ziele lesen |
| 'isaac.goal_get' | read | Ziel + Subgoals lesen |
| 'isaac.permission_check' | read | Privilege-Gate prüfen |
| 'isaac.safety_check' | read | Constitution prüfen |
| 'isaac.action_request' | read | Aktion vorschlagen, niemals ausführen |
| 'isaac.autonomy_evaluate' | read | Vollständige Autonomieprüfung ohne Ausführung |
| 'isaac.search_web' | read | Websuche |
| 'isaac.start_task' | write | Task anlegen |
| 'isaac.goal_update' | write | Zielstatus ändern |
| 'isaac.run_browser_action' | write | explizite Browser-Aktion |
| 'isaac.notification_send' | write | Benachrichtigungsabsicht auditieren |

'isaac.action_request' ist bewusst read-scoped: es erzeugt keinen ausgeführten
Side Effect. Es liefert einen strukturierten Vorschlag für die Isaac-Governance.

## Sicherheit

### HTTP

Wenn 'ISAAC_MCP_API_KEY' gesetzt ist, muss jeder externe Request

'Authorization: Bearer <key>'

tragen.

Ist kein Schlüssel gesetzt, akzeptiert Isaac HTTP-MCP **nur Loopback**. Dadurch
wird ein versehentlich öffentlich erreichbares '/api/mcp' nicht automatisch
zu einer offenen Remote-Schnittstelle.

### Privilegien

Externe MCP-Clients laufen auf 'TASK'-Level. Sie können weder per MCP-Argument
noch per JSON-RPC 'STEFFEN' oder 'ISAAC' vortäuschen.

'owner_override' und 'override_reason' werden für externe MCP-Caller abgewiesen.

### Writes

Schreibende MCP-Tools sind standardmäßig deaktiviert. Für eine absichtlich
freigegebene Integration:

'ISAAC_MCP_ALLOW_WRITE=1'

Zusätzlich bleiben Isaacs vorhandenes Privilege-Gate und Constitution-Gate
aktiv.

### Allowlist

Die exponierten Tools können weiter eingeschränkt werden:

'ISAAC_MCP_ALLOWED_TOOLS=isaac.query_memory,isaac.goal_list,isaac.goal_get'

Leer bedeutet: alle von der Isaac-MCP-Policy freigegebenen Tools.

### Limits

'ISAAC_MCP_MAX_BODY_BYTES=262144'
'ISAAC_MCP_MAX_ARGUMENT_BYTES=65536'
'ISAAC_MCP_RATE_LIMIT=60'

Der Rate-Limiter ist pro Prozess. Bei mehreren Workern gehört ein zusätzlicher
Gateway-/Reverse-Proxy-Limiter davor.

## Linear / Custom URL

Für einen externen MCP-Client wird die öffentliche Isaac-Adresse als Custom
MCP URL verwendet. Für eine öffentliche URL muss 'ISAAC_MCP_API_KEY' auf dem
Server gesetzt werden.

**Niemals API-Schlüssel in Git, 'mcp_config.json', Logs oder Tool-Argumente
commiten.** Der Schlüssel gehört ausschließlich in die Secret-/Environment-
Konfiguration des Hosts.

## Transport

Unterstützt werden:

- HTTP REST unter '/api/mcp/*'
- JSON-RPC 2.0 unter '/api/mcp/jsonrpc'
- stdio über 'python mcp_server.py --stdio'

Der JSON-RPC-Dispatcher unterstützt 'initialize', 'ping', 'tools/list',
'tools/call', 'resources/list', 'resources/read', 'prompts/list' und
'prompts/get'.

## Architekturgrenze

Der MCP-Server entscheidet nicht selbstständig über Isaac-Autonomie.

Die verbindliche Kette bleibt:

'Intent → Retrieval/Memory → Strategy/Permission → Task → Executor → Evaluation → Memory'

MCP liefert dabei nur eine standardisierte externe Werkzeug-/Kontextgrenze.
