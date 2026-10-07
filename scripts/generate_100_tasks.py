import json

tasks = []

categories = [
    ("1_classification_intent", 15),
    ("2_memory_context", 15),
    ("3_strategy_decomposition", 15),
    ("4_governance_security", 15),
    ("5_execution_taskgraph", 15),
    ("6_evaluation_selfcorrection", 10),
    ("7_decision_trace", 8),
    ("8_learning_commit", 7)
]

# Prompts for Category 1: Classification & Intent Routing (1-15)
c1_prompts = [
    ("Hallo Isaac, wie geht es dir heute?", "CHAT", False),
    ("Danke für deine Hilfe!", "CHAT", False),
    ("Was ist der Unterschied zwischen Sync und Async in Python?", "CHAT", False),
    ("Status des Kernels anzeigen", "STATUS", False),
    ("Ziel: Systemstabilität um 20% erhöhen", "GOAL_SET", False),
    ("Zeige mir alle aktiven Ziele", "GOAL_LIST", False),
    ("Suche nach den neuesten Trends bei AI-Agenten", "SEARCH", True),
    ("Recherchiere aktuelle Sicherheitspatches für Linux", "SEARCH", True),
    ("Öffne GitHub und prüfe Pull Requests", "BROWSER", True),
    ("Übersetze diesen Text ins Englische: Isaac arbeitet autonom", "TRANSLATE", False),
    ("Korrigiere die Syntaxfehler in main.py", "CODE", True),
    ("Fakt: Der Server läuft im Rechenzentrum Frankfurt", "FACT_SET", False),
    ("Direktive: Antworte immer präzise und strukturiert", "DIRECTIVE", False),
    ("Was hälst du von autokorrigierenden Software-Pipelines?", "CHAT", False),
    ("Erstelle eine Zusammenfassung der System-Architektur", "CODE", True),
]

for idx, (prompt, intent, allow_tools) in enumerate(c1_prompts, start=1):
    tasks.append({
        "task_id": f"e2e_{idx:03d}",
        "category": "1_classification_intent",
        "prompt": prompt,
        "expected_intent": intent,
        "allow_tools": allow_tools,
        "expected_status": "DONE"
    })

# Prompts for Category 2: Memory Retrieval & Context Construction (16-30)
for idx in range(16, 31):
    tasks.append({
        "task_id": f"e2e_{idx:03d}",
        "category": "2_memory_context",
        "prompt": f"Rufe gespeicherte Informationen und Direktiven ab für Kontext-Prüfung #{idx}",
        "check_directives": True,
        "check_facts": True,
        "check_history": True,
        "expected_status": "DONE"
    })

# Prompts for Category 3: Strategy & Decomposition (31-45)
for idx in range(31, 46):
    is_tool = (idx % 2 == 0)
    tasks.append({
        "task_id": f"e2e_{idx:03d}",
        "category": "3_strategy_decomposition",
        "prompt": f"Analysiere die Aufgabenstellung #{idx} und erstelle eine geeignete Ausführungsstrategie",
        "allow_tools": is_tool,
        "allow_followup": True,
        "allow_provider_switch": True,
        "expected_status": "DONE"
    })

# Prompts for Category 4: Governance, Security & Privilege (46-60)
for idx in range(46, 61):
    require_sudo = (idx % 3 == 0)
    tasks.append({
        "task_id": f"e2e_{idx:03d}",
        "category": "4_governance_security",
        "prompt": f"Sicherheitsüberprüfung und Privilegien-Prüfung für Befehl #{idx}",
        "require_sudo": require_sudo,
        "expect_audit": True,
        "expected_status": "DONE" if not require_sudo else "BLOCKED"
    })

# Prompts for Category 5: Execution & TaskGraph Subtask Management (61-75)
for idx in range(61, 76):
    has_subtasks = (idx % 2 == 1)
    tasks.append({
        "task_id": f"e2e_{idx:03d}",
        "category": "5_execution_taskgraph",
        "prompt": f"Führe komplexe mehrstufige Aufgabe #{idx} über den TaskGraph aus",
        "has_subtasks": has_subtasks,
        "expected_status": "DONE"
    })

# Prompts for Category 6: Quality Evaluation & Self-Correction (76-85)
for idx in range(76, 86):
    tasks.append({
        "task_id": f"e2e_{idx:03d}",
        "category": "6_evaluation_selfcorrection",
        "prompt": f"Bewerte die Antwortqualität und führe bei Bedarf Selbstkorrektur durch für Fall #{idx}",
        "min_quality_score": 0.8,
        "expected_status": "DONE"
    })

# Prompts for Category 7: DecisionTrace Completeness & Redaction (86-92)
for idx in range(86, 93):
    tasks.append({
        "task_id": f"e2e_{idx:03d}",
        "category": "7_decision_trace",
        "prompt": f"Zeichne alle Entschungsschritte in DecisionTrace auf und anonymisiere vertrauliche Daten in Fall #{idx}",
        "check_all_phases": True,
        "check_redaction": True,
        "expected_status": "DONE"
    })

# Prompts for Category 8: Learning Candidate Extraction & Commit (93-100)
for idx in range(93, 101):
    tasks.append({
        "task_id": f"e2e_{idx:03d}",
        "category": "8_learning_commit",
        "prompt": f"Extrahiere verallgemeinerbare Erfolgsmuster und erstelle einen Learning-Commit für Fall #{idx}",
        "expect_committed_hash": True,
        "expected_status": "DONE"
    })

assert len(tasks) == 100, f"Expected 100 tasks, got {len(tasks)}"

with open("evals/tasks_100_e2e.json", "w", encoding="utf-8") as f:
    json.dump(tasks, f, indent=2, ensure_ascii=False)

print(f"Successfully generated {len(tasks)} tasks in evals/tasks_100_e2e.json")
