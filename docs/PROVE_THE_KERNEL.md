# Prove the Kernel – Architektur- & Verifikations-Spezifikation

Dieses Dokument beschreibt die 5 Säulen der Initiative **„Prove the Kernel“** für Isaac.
Das Ziel ist es, nachweisbar und reproduzierbar zu belegen, dass der kognitive Kern von Isaac seine architektonischen Garantien einhält.

---

## Die 5 Säulen

### 1. E2E-Benchmark (100 Aufgaben)
Ein standardisierter End-to-End-Benchmark mit **100 Aufgaben**, der die komplette kognitive Pipeline durchläuft:
$$\text{Classification} \rightarrow \text{Retrieval} \rightarrow \text{Strategy} \rightarrow \text{Governance} \rightarrow \text{Execution} \rightarrow \text{Evaluation} \rightarrow \text{DecisionTrace} \rightarrow \text{Learning}$$

Standardisierte Kategorien:
- Classification & Intent Routing
- Memory Retrieval & Context Construction
- Strategy & Capability Selection
- Governance, Constitution & Privilege Enforcement
- Execution & Subtask Management
- Quality Evaluation & Self-Correction
- Decision Trace Completeness & Redaction
- Learning Candidate Extraction & Commit

---

## 2. Formales TaskGraph-Modell

Ein Task im Isaac-Kernel ist keine flache Datenstruktur mehr, sondern ein Knoten in einem gerichteten acyclischen Graph (DAG).

### Datenmodell eines Task
- `task_id`: Eindeutige Task-UUID / Identifier
- `parent_id`: ID der übergeordneten Aufgabe (oder `None`)
- `dependencies`: Liste von Task-IDs, von denen diese Aufgabe abhängt
- `goal`: Zugeordnetes Hauptziel (oder `None`)
- `subgoals`: Zugeordnete Subgoal-IDs
- `classification`: Details zur Interaktionsklasse (`InteractionClass`, `Intent`)
- `strategy`: Zugeordnete `Strategy` (allow_tools, capabilities, limits)
- `required_capabilities`: Geforderte System-Capabilities
- `risk_level`: Risikostufe (`low`, `medium`, `high`, `critical`)
- `actions`: Sequenz von versuchten / ausgeführten Aktionen
- `evidence`: Beobachtete Beweise und Ergebnisse
- `decision`: Getroffene Entscheidungen
- `outcome`: Endergebnis
- `errors`: Fehlerprotokoll
- `learned_from`: Referenz auf gelernte Erfahrungen
- `decision_trace`: Referenz / Dump des DecisionTrace
- `timestamps`: `{created_at, started_at, completed_at, updated_at}`
- `status`: `TaskStatus` (`PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`)

### Graph-Struktur
```text
Goal
  ↓
Task (TaskGraph Root)
  ├── Subtask A
  │     └── Evidence
  ├── Subtask B
  │     └── Evidence
  └── Subtask C
        └── Evidence
             ↓
          Evaluation
             ↓
          Outcome
             ↓
       Learning Candidate
```

---

## 3. Formaler Learning-Commit-Prozess

Das Lernen im Kernel erfolgt in einer strengen Validierungskette. Es reicht nicht aus, dass eine Aufgabe erfolgreich war, um "etwas zu lernen".

### Die Commit-Pipeline
$$\text{Experience} \rightarrow \text{Learning Candidate} \rightarrow \text{Evidence} \rightarrow \text{Evaluation} \rightarrow \text{Replay} \rightarrow \text{Regression Test} \rightarrow \text{Governance Check} \rightarrow \text{Learning Commit} \rightarrow \text{Versionierung}$$

### Kriterien für einen Learning Candidate:
1. **Beobachtung**: Was genau wurde beobachtet?
2. **Relevanz**: Warum ist es verallgemeinerbar?
3. **Evidenz**: Welche konkreten Task-Aktionen und Outcomes stützen das Learning?
4. **Confidence**: Wie sicher ist die Schlussfolgerung (0.0 bis 1.0)?
5. **Konsistenz**: Widerspricht es bestehendem Wissen oder Verfassungspostulaten?
6. **Reproduzierbarkeit**: Kann die Erkenntnis im Replay bestätigt werden?
7. **Performance**: Verbessert es Verhalten ohne Regressionen?
8. **Governance**: Entspricht der Inhalt den Verfassungs- und Datenschutzregeln?

---

## 4. Epistemisches Memory-Modell

Sämtliche Gedächtniseinträge (`MemoryEntry`) tragen eine explizite epistemische Klasse und Provenienz-Metadaten.

### Epistemische Klassen
- `FACT`: Verifizierte Tatsache (z. B. durch Ausführung / Bestätigung belegt)
- `OBSERVATION`: Direkt beobachtetes Ereignis
- `USER_ASSERTION`: Explizite Aussage des Benutzers
- `INFERENCE`: Aus anderen Informationen abgeleitete Erkenntnis
- `HYPOTHESIS`: Noch unbestätigte Annahme
- `AGENT_GENERATED`: Vom Agenten selbst erzeugte Information / Synthese
- `VERIFIED`: Nachträglich auditierte / überprüfte Information
- `CONTRADICTED`: Im Konflikt mit neuerer Evidenz stehende Information

### Metadaten eines epistemischen Memory
- `source` / `provenance`: Herkunft (User, Task #ID, Tool Output, Decomposer)
- `confidence`: Gewissheitsgrad (0.0 bis 1.0)
- `validity_period`: Optionaler Gültigkeitszeitraum / TTL
- `source_authority`: Autorität der Quelle (`owner`, `system`, `external_tool`, `untrusted`)
- `evidence_task_ids`: Verknüpfte TaskGraph-IDs als Evidenz
- `contradicted_by`: Liste von Memory-IDs oder Task-IDs, die diesem Eintrag widersprechen

---

## 5. Messbare Autonomie- und Governance-Metriken

Die Kernel-Stabilität und Kontrolle wird über ein objektives Metrik-Set gemessen.

| Metrik | Formel / Definition |
|---|---|
| **Task Success Rate** | $\frac{\text{Erfolgreiche Tasks}}{\text{Gesamte Tasks}}$ |
| **Intervention Rate** | $\frac{\text{Tasks mit menschlichem Eingriff}}{\text{Gesamte Tasks}}$ |
| **Regression Rate** | $\frac{\text{Fehlgeschlagene Tasks nach Learning}}{\text{Evaluierte Tasks}}$ |
| **Unauthorized Action Rate** | $\frac{\text{Nicht erlaubte / blockierte Aktionen}}{\text{Gesamte Aktionen}}$ |
| **Learning Gain** | $\text{Performance}_{\text{nach Learning}} - \text{Performance}_{\text{vor Learning}}$ |
| **Trace Completeness** | $\frac{\text{Vollständig protokollierte Phasen}}{\text{Erwartete Phasen}}$ |
| **Recovery Rate** | $\frac{\text{Selbstständig korrigierte Fehler}}{\text{Gesamte Fehler}}$ |
| **Cost / Latency / Efficiency** | Tokens, API-Kosten, Tool-Calls, Latenz pro Task |
| **Autonomy without Loss of Control** | $\text{Success Rate} \times (1 - \text{Unauthorized Rate}) \times \text{Trace Completeness}$ |

---
