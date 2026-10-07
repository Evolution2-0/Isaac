"""
Isaac – Gedächtnis (SQLite)
============================
Persistentes, durchsuchbares Gedächtnis auf drei Ebenen:

  1. Working Memory    – letzte N Konversationen (RAM + DB)
  2. Faktenspeicher    – Steffen-Wissen, Korrekturen, Kontext
  3. Direktiven-Store  – Steffen-Direktiven (permanent)

SQLite ist atomar: kein Datenverlust bei Absturz.
FTS5 für Volltextsuche.
"""
from __future__ import annotations

import sqlite3
import json
import time
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, Any
from contextlib import contextmanager
from enum import Enum


class EpistemicClass(Enum):
    FACT            = "FACT"
    OBSERVATION     = "OBSERVATION"
    USER_ASSERTION  = "USER_ASSERTION"
    INFERENCE       = "INFERENCE"
    HYPOTHESIS      = "HYPOTHESIS"
    AGENT_GENERATED = "AGENT_GENERATED"
    VERIFIED        = "VERIFIED"
    CONTRADICTED    = "CONTRADICTED"
    UNCERTAINTY_EVENT = "UNCERTAINTY_EVENT"


@dataclass
class EpistemicMemoryEntry:
    memory_id: str
    ts: str
    key: str
    value: str
    epistemic_class: EpistemicClass = EpistemicClass.OBSERVATION
    source: str = ""
    source_authority: str = "system"
    confidence: float = 0.5
    validity_start: str = ""
    validity_end: str = ""
    evidence_task_ids: list[str] = field(default_factory=list)
    contradicted_by: list[str] = field(default_factory=list)
    relations: list[dict] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["epistemic_class"] = self.epistemic_class.value if isinstance(self.epistemic_class, EpistemicClass) else str(self.epistemic_class)
        return d

from config import DB_PATH, get_config
from audit  import AuditLog

log = logging.getLogger("Isaac.Memory")

MIN_RETRIEVAL_CONFIDENCE = 0.12
PREFERENCE_KEY_MARKERS = ("pref", "stil", "antwort", "tool", "chat", "agreement")
OWNER_SOURCES = frozenset({"steffen", "owner"})


@dataclass(frozen=True)
class RetrievalContext:
    query: str
    active_directives: list[dict]
    relevant_facts: list[dict]
    semantic_context: str
    conversation_history: list[dict]
    relevant_task_results: list[dict]
    preferences_context: list[dict]
    project_context: list[dict]
    behavioral_risks: list[dict]
    relevant_reflections: list[str]
    open_questions: list[str]
    relevant_procedures: list[dict]
    # Goal-Autonomie Slice 1b: short owner-goal surface for prompt composition
    active_goals: list[dict] = field(default_factory=list)
    # DIVA runtime modulator snapshot
    modulator_state: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "active_directives": self.active_directives,
            "relevant_facts": self.relevant_facts,
            "semantic_context": self.semantic_context,
            "conversation_history": self.conversation_history,
            "relevant_task_results": self.relevant_task_results,
            "preferences_context": self.preferences_context,
            "project_context": self.project_context,
            "behavioral_risks": self.behavioral_risks,
            "relevant_reflections": self.relevant_reflections,
            "open_questions": self.open_questions,
            "relevant_procedures": self.relevant_procedures,
            "active_goals": self.active_goals,
            "modulator_state": self.modulator_state,
        }


# ── Schema ─────────────────────────────────────────────────────────────────────
SCHEMA = """
-- Konversations-Verlauf
CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    role        TEXT    NOT NULL,   -- 'steffen' | 'isaac' | 'instance'
    text        TEXT    NOT NULL,
    task_id     TEXT    DEFAULT '',
    provider    TEXT    DEFAULT '',
    quality     REAL    DEFAULT 0.0,
    metadata    TEXT    DEFAULT '{}'
);

-- Fakten (Langzeit-Wissen)
CREATE TABLE IF NOT EXISTS facts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    key         TEXT    NOT NULL UNIQUE,
    value       TEXT    NOT NULL,
    source      TEXT    DEFAULT '',
    confidence  REAL    DEFAULT 1.0,
    updated     TEXT    NOT NULL
);

-- Steffen-Direktiven
CREATE TABLE IF NOT EXISTS directives (
    id          TEXT    PRIMARY KEY,
    ts          TEXT    NOT NULL,
    text        TEXT    NOT NULL,
    priority    INTEGER DEFAULT 10,
    active      INTEGER DEFAULT 1
);

-- Task-Ergebnisse (komprimiert, für Referenz)
CREATE TABLE IF NOT EXISTS task_results (
    id          TEXT    PRIMARY KEY,
    ts          TEXT    NOT NULL,
    description TEXT    NOT NULL,
    result      TEXT    NOT NULL,
    score       REAL    DEFAULT 0.0,
    iterations  INTEGER DEFAULT 1,
    provider    TEXT    DEFAULT '',
    tags        TEXT    DEFAULT '[]'
);

-- Entwicklungslog (Lern- und Wertänderungen)
CREATE TABLE IF NOT EXISTS development_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                  TEXT    NOT NULL,
    event_type          TEXT    NOT NULL,
    target_kind         TEXT    NOT NULL,
    target_key          TEXT    NOT NULL,
    delta               REAL    DEFAULT 0.0,
    confidence_before   REAL    DEFAULT 0.0,
    confidence_after    REAL    DEFAULT 0.0,
    evidence_refs       TEXT    DEFAULT '[]',
    contradiction_refs  TEXT    DEFAULT '[]',
    reason              TEXT    DEFAULT '',
    requires_review     INTEGER DEFAULT 0,
    reviewed_by_owner   INTEGER DEFAULT 0,
    metadata            TEXT    DEFAULT '{}'
);

-- Wiederverwendbare Task-Verfahren (Skill-/Procedure-Memory)
CREATE TABLE IF NOT EXISTS task_procedures (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    signature           TEXT    NOT NULL UNIQUE,
    ts                  TEXT    NOT NULL,
    task_type           TEXT    NOT NULL,
    intent_hint         TEXT    DEFAULT '',
    keywords            TEXT    DEFAULT '[]',
    tools_used          TEXT    DEFAULT '[]',
    trace_summary       TEXT    DEFAULT '',
    reliability         REAL    DEFAULT 0.5,
    success_count       INTEGER DEFAULT 0,
    failure_count       INTEGER DEFAULT 0,
    last_task_id        TEXT    DEFAULT '',
    last_score          REAL    DEFAULT 0.0,
    last_status         TEXT    DEFAULT '',
    degraded            INTEGER DEFAULT 0,
    metadata            TEXT    DEFAULT '{}'
);

-- Archivierte Entwicklungs-Events (Forgetting/Decay)
CREATE TABLE IF NOT EXISTS development_events_archive (
    id                  INTEGER PRIMARY KEY,
    ts                  TEXT    NOT NULL,
    event_type          TEXT    NOT NULL,
    target_kind         TEXT    NOT NULL,
    target_key          TEXT    NOT NULL,
    delta               REAL    DEFAULT 0.0,
    confidence_before   REAL    DEFAULT 0.0,
    confidence_after    REAL    DEFAULT 0.0,
    evidence_refs       TEXT    DEFAULT '[]',
    contradiction_refs  TEXT    DEFAULT '[]',
    reason              TEXT    DEFAULT '',
    requires_review     INTEGER DEFAULT 0,
    reviewed_by_owner   INTEGER DEFAULT 0,
    metadata            TEXT    DEFAULT '{}',
    archived_at         TEXT    NOT NULL
);

-- Task-Checkpoints für Resume
CREATE TABLE IF NOT EXISTS task_checkpoints (
    checkpoint_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id             TEXT    NOT NULL,
    ts                  TEXT    NOT NULL,
    state_name          TEXT    NOT NULL,
    input_snapshot      TEXT    DEFAULT '{}',
    tool_snapshot       TEXT    DEFAULT '{}',
    result_snapshot     TEXT    DEFAULT '{}',
    memory_refs         TEXT    DEFAULT '[]',
    side_effect_refs    TEXT    DEFAULT '[]'
);

-- FTS für Konversationen
CREATE VIRTUAL TABLE IF NOT EXISTS conv_fts USING fts5(
    text, content='conversations', content_rowid='id'
);

-- FTS für Fakten
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    key, value, content='facts', content_rowid='id'
);

-- Epistemisches Memory (Pillar 4 - Prove the Kernel)
CREATE TABLE IF NOT EXISTS epistemic_memories (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id           TEXT    UNIQUE NOT NULL,
    ts                  TEXT    NOT NULL,
    key                 TEXT    NOT NULL,
    value               TEXT    NOT NULL,
    epistemic_class     TEXT    NOT NULL DEFAULT 'OBSERVATION',
    source              TEXT    DEFAULT '',
    source_authority    TEXT    DEFAULT 'system',
    confidence          REAL    DEFAULT 0.5,
    validity_start      TEXT    DEFAULT '',
    validity_end        TEXT    DEFAULT '',
    evidence_task_ids   TEXT    DEFAULT '[]',
    contradicted_by     TEXT    DEFAULT '[]',
    relations           TEXT    DEFAULT '[]',
    metadata            TEXT    DEFAULT '{}'
);

-- Trigger für FTS-Sync
CREATE TRIGGER IF NOT EXISTS conv_ai AFTER INSERT ON conversations BEGIN
    INSERT INTO conv_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, key, value) VALUES (new.id, new.key, new.value);
END;
CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, key, value)
        VALUES('delete', old.id, old.key, old.value);
    INSERT INTO facts_fts(rowid, key, value) VALUES (new.id, new.key, new.value);
END;
"""

# ── Verbindung ────────────────────────────────────────────────────────────────
@contextmanager
def _conn():
    """Thread-safe SQLite-Verbindung mit WAL-Mode."""
    con = sqlite3.connect(str(DB_PATH), check_same_thread=False,
                          timeout=10.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db():
    with _conn() as con:
        con.executescript(SCHEMA)
    log.info(f"DB initialisiert: {DB_PATH}")


# ── Memory-Klasse ──────────────────────────────────────────────────────────────
class Memory:
    """
    Isaacs Gedächtnis.
    Alle Schreib-Operationen erzeugen Audit-Einträge.
    """

    def __init__(self):
        init_db()
        self._working: list[dict] = []
        self._load_working_memory()
        # Vektor-Gedächtnis (semantisch, optional)
        from vector_memory import get_vector_memory
        self._vector = get_vector_memory()
        log.info(
            f"Memory online │ Working: {len(self._working)} Einträge │ "
            f"Vector: {'aktiv' if self._vector.aktiv else 'inaktiv (pip install chromadb)'}"
        )

    # ── Konversation ──────────────────────────────────────────────────────────
    def add_conversation(self, role: str, text: str,
                         task_id: str = "", provider: str = "",
                         quality: float = 0.0, metadata: dict = None,
                         stimmung: str = "neutral"):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        meta = json.dumps(metadata or {})
        with _conn() as con:
            cur = con.execute(
                "INSERT INTO conversations (ts, role, text, task_id, "
                "provider, quality, metadata) VALUES (?,?,?,?,?,?,?)",
                (ts, role, text, task_id, provider, quality, meta)
            )
            conv_id = str(cur.lastrowid)
        entry = {"ts": ts, "role": role, "text": text,
                 "task_id": task_id, "provider": provider,
                 "quality": quality}
        self._working.append(entry)
        cfg = get_config()
        if len(self._working) > cfg.memory.max_working_memory:
            self._working = self._working[-cfg.memory.max_working_memory:]
        AuditLog.memory_write("Memory", "conversation", role)
        # Semantisch speichern
        self._vector.speichere_konversation(
            conv_id   = conv_id,
            text      = text,
            role      = role,
            ts        = ts,
            stimmung  = stimmung,
            qualitaet = quality,
        )

    def get_working_memory(self, n: int = 10) -> list[dict]:
        """Gibt die letzten n Konversations-Einträge zurück."""
        return self._working[-n:]

    def search_conversations(self, query: str, limit: int = 10) -> list[dict]:
        with _conn() as con:
            rows = con.execute(
                """SELECT c.* FROM conversations c
                   JOIN conv_fts ON conv_fts.rowid = c.id
                   WHERE conv_fts MATCH ?
                   ORDER BY c.id DESC LIMIT ?""",
                (query, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Fakten ────────────────────────────────────────────────────────────────
    @staticmethod
    def _is_preference_key(key: str) -> bool:
        lowered = (key or "").lower()
        return any(marker in lowered for marker in PREFERENCE_KEY_MARKERS)

    @staticmethod
    def _is_owner_source(source: str) -> bool:
        return (source or "").strip().lower() in OWNER_SOURCES

    def get_fact_record(self, key: str) -> Optional[dict]:
        with _conn() as con:
            row = con.execute("SELECT * FROM facts WHERE key=?", (key,)).fetchone()
        return dict(row) if row else None

    def update_fact_confidence(self, key: str, confidence: float) -> bool:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with _conn() as con:
            cur = con.execute(
                "UPDATE facts SET confidence=?, updated=? WHERE key=?",
                (float(confidence), ts, key),
            )
        return cur.rowcount > 0

    def set_fact(self, key: str, value: str, source: str = "",
                 confidence: float = 1.0) -> bool:
        """Schreibt oder aktualisiert eine Tatsache."""
        from learning_policy import bounded_update

        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        owner_confirmed = self._is_owner_source(source)
        if owner_confirmed:
            confidence = 1.0

        with _conn() as con:
            existing = con.execute(
                "SELECT id, value, confidence, source FROM facts WHERE key=?", (key,)
            ).fetchone()
            if existing:
                old_value = (existing["value"] or "").strip()
                old_conf = float(existing["confidence"] or 1.0)
                new_conf = float(confidence)
                if (
                    old_value
                    and old_value != value.strip()
                    and not owner_confirmed
                ):
                    delta = bounded_update(
                        "preference",
                        -1.0,
                        evidence_strength=0.8,
                        repetition=1.0,
                    )
                    new_conf = max(MIN_RETRIEVAL_CONFIDENCE, min(new_conf, old_conf + delta))
                    try:
                        self.log_development_event(
                            event_type="fact_contradiction",
                            target_kind="fact",
                            target_key=key,
                            delta=round(new_conf - old_conf, 4),
                            confidence_before=old_conf,
                            confidence_after=new_conf,
                            contradiction_refs=[old_value[:120]],
                            reason=f"Widerspruch: '{old_value[:80]}' → '{value[:80]}'",
                            requires_review=True,
                            metadata={"source": source, "old_value": old_value[:120]},
                        )
                    except Exception as e:
                        log.debug(f"Contradiction-Log: {e}")
                con.execute(
                    "UPDATE facts SET value=?, source=?, "
                    "confidence=?, updated=? WHERE key=?",
                    (value, source, new_conf, ts, key)
                )
            else:
                if self._is_preference_key(key) and not owner_confirmed:
                    confidence = min(float(confidence), 0.65)
                con.execute(
                    "INSERT INTO facts (ts, key, value, source, "
                    "confidence, updated) VALUES (?,?,?,?,?,?)",
                    (ts, key, value, source, confidence, ts)
                )
        AuditLog.memory_write("Memory", "fact", key)
        return True

    def get_fact(self, key: str) -> Optional[str]:
        with _conn() as con:
            row = con.execute(
                "SELECT value FROM facts WHERE key=?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def list_decay_candidate_facts(self, limit: int = 100) -> list[dict]:
        with _conn() as con:
            rows = con.execute(
                "SELECT * FROM facts ORDER BY updated ASC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        candidates = []
        for row in rows:
            fact = dict(row)
            key = fact.get("key", "")
            conf = float(fact.get("confidence") or 0.0)
            if not self._is_preference_key(key):
                continue
            if conf > 0.75:
                continue
            if self._is_owner_source(fact.get("source", "")) and conf >= 0.9:
                continue
            candidates.append(fact)
        return candidates

    def search_facts(self, query: str, limit: int = 10, memory_bias_strength: float = 0.0) -> list[dict]:
        epistemic_bias_results = []
        if memory_bias_strength > 0.0:
            try:
                ep_entries = self.search_epistemic_memories(query=query, epistemic_class="UNCERTAINTY_EVENT", limit=int(limit * memory_bias_strength) + 1)
                for ep in ep_entries:
                    epistemic_bias_results.append({
                        "id": ep.memory_id,
                        "key": ep.key,
                        "value": f"[DIVA Uncertainty] {ep.value}",
                        "category": "diva_uncertainty",
                        "updated": ep.ts,
                        "confidence": ep.confidence,
                    })
            except Exception:
                pass
        terms = [t for t in re.findall(r"[\w]+", (query or "").lower()) if len(t) >= 2]
        if not terms:
            return []
        fts_query = " OR ".join(terms[:8])
        # Temporal bias (Graphiti-inspired, no graph DB):
        # goal_latest.* before goal_progress.*; then confidence.
        order_sql = (
            "ORDER BY "
            "CASE "
            "WHEN f.key LIKE 'goal_latest.%' THEN 0 "
            "WHEN f.key LIKE 'goal_progress.%' THEN 2 "
            "ELSE 1 END, "
            "f.confidence DESC"
        )
        with _conn() as con:
            try:
                rows = con.execute(
                    f"""SELECT f.* FROM facts f
                       JOIN facts_fts ON facts_fts.rowid = f.id
                       WHERE facts_fts MATCH ?
                       {order_sql} LIMIT ?""",
                    (fts_query, max(limit * 2, limit)),
                ).fetchall()
            except sqlite3.OperationalError:
                like = f"%{' '.join(terms[:3])}%"
                rows = con.execute(
                    f"""SELECT f.* FROM facts f
                       WHERE f.key LIKE ? OR f.value LIKE ?
                       {order_sql} LIMIT ?""",
                    (like, like, max(limit * 2, limit)),
                ).fetchall()
        facts = [
            dict(r) for r in rows
            if float(r["confidence"] or 0.0) >= MIN_RETRIEVAL_CONFIDENCE
        ]
        return (epistemic_bias_results + self.prioritize_temporal_facts(facts, limit=limit))[:limit]

    @staticmethod
    def prioritize_temporal_facts(
        facts: list[dict],
        *,
        limit: int = 10,
    ) -> list[dict]:
        """Prefer goal_latest over goal_progress for the same goal_id.

        Keeps non-goal facts in confidence order. Pure reordering/dedupe —
        no Graphiti/Zep dependency.
        """
        if not facts:
            return []
        latest_by_goal: dict[str, dict] = {}
        progress_by_goal: dict[str, list[dict]] = {}
        other: list[dict] = []
        for fact in facts:
            key = str(fact.get("key") or "")
            if key.startswith("goal_latest."):
                gid = key[len("goal_latest.") :].strip()
                if not gid:
                    other.append(fact)
                    continue
                prev = latest_by_goal.get(gid)
                if prev is None or float(fact.get("confidence") or 0) > float(
                    prev.get("confidence") or 0
                ):
                    latest_by_goal[gid] = fact
            elif key.startswith("goal_progress."):
                rest = key[len("goal_progress.") :]
                gid = rest.split(".", 1)[0] if rest else ""
                progress_by_goal.setdefault(gid or "_", []).append(fact)
            else:
                other.append(fact)

        ranked: list[dict] = []
        for fact in sorted(
            latest_by_goal.values(),
            key=lambda f: float(f.get("confidence") or 0),
            reverse=True,
        ):
            ranked.append(fact)
        other.sort(key=lambda f: float(f.get("confidence") or 0), reverse=True)
        ranked.extend(other)
        # Historical progress only when no current latest for that goal
        for gid, plist in progress_by_goal.items():
            if gid in latest_by_goal:
                continue
            plist.sort(key=lambda f: float(f.get("confidence") or 0), reverse=True)
            ranked.extend(plist)
        return ranked[: max(1, int(limit))]

    def supersede_stale_goal_progress(
        self,
        goal_id: str,
        *,
        demote_to: float = 0.22,
    ) -> int:
        """When goal_latest is refreshed, demote older goal_progress confidences.

        Returns number of rows updated. Fail-soft.
        """
        gid = (goal_id or "").strip()
        if not gid:
            return 0
        prefix = f"goal_progress.{gid}."
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        floor = max(MIN_RETRIEVAL_CONFIDENCE, float(demote_to))
        try:
            with _conn() as con:
                rows = con.execute(
                    "SELECT key, confidence FROM facts WHERE key LIKE ?",
                    (prefix + "%",),
                ).fetchall()
                n = 0
                for row in rows:
                    conf = float(row["confidence"] or 0.0)
                    if conf <= floor:
                        continue
                    con.execute(
                        "UPDATE facts SET confidence=?, updated=? WHERE key=?",
                        (floor, ts, row["key"]),
                    )
                    n += 1
                return n
        except Exception as exc:
            log.debug("supersede_stale_goal_progress: %s", exc)
            return 0

    def all_facts(self) -> dict[str, str]:
        with _conn() as con:
            rows = con.execute(
                "SELECT key, value FROM facts ORDER BY updated DESC LIMIT 200"
            ).fetchall()
        return {r["key"]: r["value"] for r in rows}

    def delete_facts_matching(
        self,
        *,
        key_prefix: str = "",
        value_contains: str = "",
        key_contains: str = "",
    ) -> int:
        """Delete facts by key prefix and/or substring match. Returns rows deleted."""
        key_prefix = (key_prefix or "").strip()
        value_contains = (value_contains or "").strip().lower()
        key_contains = (key_contains or "").strip().lower()
        if not key_prefix and not value_contains and not key_contains:
            return 0
        with _conn() as con:
            rows = con.execute("SELECT key, value FROM facts").fetchall()
            to_del: list[str] = []
            for r in rows:
                k = (r["key"] or "")
                v = (r["value"] or "")
                if key_prefix and not k.startswith(key_prefix):
                    continue
                if key_contains and key_contains not in k.lower():
                    if not value_contains:
                        continue
                if value_contains and value_contains not in v.lower():
                    if key_contains and key_contains in k.lower():
                        pass
                    elif key_prefix and k.startswith(key_prefix):
                        # key prefix alone is enough when no value filter mismatch was required
                        if value_contains and value_contains not in v.lower():
                            continue
                    else:
                        continue
                # refined: if only value_contains
                if value_contains and not key_prefix and not key_contains:
                    if value_contains not in v.lower() and value_contains not in k.lower():
                        continue
                    to_del.append(k)
                    continue
                if key_prefix and k.startswith(key_prefix):
                    if value_contains and value_contains not in v.lower() and value_contains not in k.lower():
                        continue
                    to_del.append(k)
                    continue
                if key_contains and key_contains in k.lower():
                    to_del.append(k)
            for k in to_del:
                con.execute("DELETE FROM facts WHERE key=?", (k,))
            return len(to_del)

    def purge_eval_noise_facts(self) -> dict:
        """Remove known test/eval goal-progress noise that pollutes retrieval."""
        with _conn() as con:
            rows = con.execute("SELECT key, value FROM facts").fetchall()
            noise_markers = (
                "markt wächst",
                "markt waechst",
                "mobile oder desktop",
                "prototyp ist fertig und getestet",
            )
            to_del: list[str] = []
            for r in rows:
                k = (r["key"] or "")
                v = (r["value"] or "").lower()
                kl = k.lower()
                if k.startswith("goal_latest.") or k.startswith("goal_progress."):
                    if v.strip() in {"again", "ok", "test", "timeout"} or any(m in v for m in noise_markers):
                        to_del.append(k)
                        continue
                    if "markt" in v and "priorisieren" in v:
                        to_del.append(k)
                        continue
                    if "temporal_test" in kl:
                        to_del.append(k)
                        continue
                if k.startswith("goal_answer.") and (
                    "desktop oder mobile" in v or "budget" in v or "max 200" in v
                ):
                    to_del.append(k)
                    continue
                if k.startswith("goal_digest."):
                    # digest snapshots from evals/tests often re-inject fake goals
                    if "digest ziel" in v or "markt" in v:
                        to_del.append(k)
                        continue
                if any(m in v for m in noise_markers) and (
                    k.startswith("goal_") or "goal" in kl
                ):
                    to_del.append(k)
            for k in set(to_del):
                con.execute("DELETE FROM facts WHERE key=?", (k,))
            return {"deleted": len(set(to_del)), "keys": sorted(set(to_del))[:30]}

    # ── Direktiven ────────────────────────────────────────────────────────────
    def save_directive(self, directive_id: str, text: str,
                       priority: int = 10):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with _conn() as con:
            con.execute(
                "INSERT OR REPLACE INTO directives "
                "(id, ts, text, priority, active) VALUES (?,?,?,?,1)",
                (directive_id, ts, text, priority)
            )

    def revoke_directive(self, directive_id: str):
        with _conn() as con:
            con.execute(
                "UPDATE directives SET active=0 WHERE id=?",
                (directive_id,)
            )

    def get_directives(self) -> list[dict]:
        with _conn() as con:
            rows = con.execute(
                "SELECT * FROM directives WHERE active=1 "
                "ORDER BY priority DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Task-Ergebnisse ───────────────────────────────────────────────────────
    def save_task_result(self, task_id: str, description: str,
                         result: str, score: float = 0.0,
                         iterations: int = 1, provider: str = "",
                         tags: list = None):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with _conn() as con:
            con.execute(
                "INSERT OR REPLACE INTO task_results "
                "(id, ts, description, result, score, iterations, "
                "provider, tags) VALUES (?,?,?,?,?,?,?,?)",
                (task_id, ts, description, result[:2000],
                 score, iterations, provider,
                 json.dumps(tags or []))
            )

    def get_relevant_results(self, query: str, limit: int = 3) -> list[dict]:
        """Findet frühere Task-Ergebnisse die zu einer Anfrage passen."""
        with _conn() as con:
            rows = con.execute(
                """SELECT * FROM task_results
                   WHERE description LIKE ? OR result LIKE ?
                   ORDER BY score DESC LIMIT ?""",
                (f"%{query[:30]}%", f"%{query[:30]}%", limit)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Task-Verfahren (Procedure Memory) ─────────────────────────────────────
    def get_procedure_by_signature(self, signature: str) -> Optional[dict]:
        with _conn() as con:
            row = con.execute(
                "SELECT * FROM task_procedures WHERE signature=?", (signature,)
            ).fetchone()
        return dict(row) if row else None

    def upsert_procedure(
        self,
        signature: str,
        task_type: str,
        intent_hint: str = "",
        keywords: list | None = None,
        tools_used: list | None = None,
        trace_summary: str = "",
        reliability: float = 0.5,
        success_count: int = 0,
        failure_count: int = 0,
        last_task_id: str = "",
        last_score: float = 0.0,
        last_status: str = "",
        degraded: bool = False,
        metadata: dict | None = None,
    ) -> int:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with _conn() as con:
            existing = con.execute(
                "SELECT id FROM task_procedures WHERE signature=?", (signature,)
            ).fetchone()
            values = (
                ts,
                task_type,
                intent_hint[:120],
                json.dumps(keywords or [], ensure_ascii=False),
                json.dumps(tools_used or [], ensure_ascii=False),
                trace_summary[:300],
                float(reliability),
                int(success_count),
                int(failure_count),
                last_task_id,
                float(last_score),
                last_status,
                1 if degraded else 0,
                json.dumps(metadata or {}, ensure_ascii=False),
            )
            if existing:
                con.execute(
                    "UPDATE task_procedures SET ts=?, task_type=?, intent_hint=?, "
                    "keywords=?, tools_used=?, trace_summary=?, reliability=?, "
                    "success_count=?, failure_count=?, last_task_id=?, last_score=?, "
                    "last_status=?, degraded=?, metadata=? WHERE signature=?",
                    (*values, signature),
                )
                proc_id = int(existing["id"])
            else:
                cur = con.execute(
                    "INSERT INTO task_procedures "
                    "(signature, ts, task_type, intent_hint, keywords, tools_used, "
                    "trace_summary, reliability, success_count, failure_count, "
                    "last_task_id, last_score, last_status, degraded, metadata) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (signature, *values),
                )
                proc_id = int(cur.lastrowid)
        AuditLog.memory_write("Memory", "procedure", signature[:12])
        return proc_id

    def _normalize_procedure(self, proc: dict) -> dict:
        return {
            "signature": proc.get("signature", ""),
            "task_type": proc.get("task_type", ""),
            "intent_hint": (proc.get("intent_hint") or "")[:120],
            "keywords": json.loads(proc.get("keywords") or "[]"),
            "tools_used": json.loads(proc.get("tools_used") or "[]"),
            "trace_summary": (proc.get("trace_summary") or "")[:200],
            "reliability": float(proc.get("reliability") or 0.0),
            "success_count": int(proc.get("success_count") or 0),
            "failure_count": int(proc.get("failure_count") or 0),
            "degraded": bool(proc.get("degraded")),
            "last_status": proc.get("last_status", ""),
            "last_score": float(proc.get("last_score") or 0.0),
        }

    def search_procedures(self, query: str, limit: int = 3) -> list[dict]:
        terms = [t for t in re.findall(r"\w+", (query or "").lower()) if len(t) >= 3]
        with _conn() as con:
            rows = con.execute(
                "SELECT * FROM task_procedures "
                "ORDER BY reliability DESC, ts DESC LIMIT 80"
            ).fetchall()
        scored: list[tuple[float, dict]] = []
        for row in rows:
            proc = dict(row)
            keywords = json.loads(proc.get("keywords") or "[]")
            hint = (proc.get("intent_hint") or "").lower()
            overlap = 0.0
            for term in terms:
                if any(term in kw or kw in term for kw in keywords):
                    overlap += 1.0
                elif term in hint:
                    overlap += 0.5
            if not terms:
                overlap = 0.5
            reliability = float(proc.get("reliability") or 0.0)
            rank = overlap * reliability
            if proc.get("degraded"):
                rank *= 0.35
            if overlap > 0 or not terms:
                scored.append((rank, proc))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            self._normalize_procedure(proc)
            for rank, proc in scored[: max(1, int(limit))]
            if rank > 0 or not terms
        ]

    def list_procedures(self, limit: int = 20) -> list[dict]:
        with _conn() as con:
            rows = con.execute(
                "SELECT * FROM task_procedures "
                "ORDER BY reliability DESC, ts DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [self._normalize_procedure(dict(r)) for r in rows]

    @staticmethod
    def _regelwerk_open_questions_for_retrieval(user_input: str) -> list[str]:
        try:
            from regelwerk import get_regelwerk

            rw = get_regelwerk()
            pending = sorted(
                rw.offene_fragen(),
                key=lambda f: float(getattr(f, "prioritaet", 0.0)),
                reverse=True,
            )
            if not pending:
                return []
            input_l = (user_input or "").lower()
            matched: list[str] = []
            for frage in pending:
                term = rw._extract_term_from_frage(frage)
                text = (frage.text or "").strip()
                if not text:
                    continue
                if term and term.lower() in input_l:
                    matched.append(f"[Offene Frage] {text}")
            if matched:
                return matched
            top = pending[0]
            if (top.text or "").strip():
                return [f"[Offene Frage] {top.text.strip()}"]
        except Exception:
            return []
        return []

    def build_retrieval_context(
        self, user_input: str, intent: str = "", interaction_class: str = "",
        n_history: int = 6
    ) -> RetrievalContext:
        # Obtain DIVA modulator snapshot to adjust retrieval sensitivity
        mod_state: dict[str, float] = {}
        effective_min_conf = MIN_RETRIEVAL_CONFIDENCE
        try:
            from modulator import get_modulator
            mod = get_modulator()
            mod_state = mod.get_all()
            u_val = mod_state.get("uncertainty", 0.0)
            if u_val > 0.3:
                # Lower confidence threshold under high uncertainty to retrieve more candidate facts
                effective_min_conf = max(0.05, MIN_RETRIEVAL_CONFIDENCE - 0.05 * u_val)
        except Exception:
            mod_state = {}
        query_terms = [w for w in re.findall(r"\w+", user_input.lower()) if len(w) >= 4][:5]
        query = " ".join(query_terms) or user_input[:40]
        directives = self.get_directives()[:3]
        facts = [
            f for f in (self.search_facts(query, limit=8) if query else [])
            if float(f.get("confidence") or 0.0) >= effective_min_conf
        ]
        seen_fact_keys = {f.get("key") for f in facts}
        for term in {w for w in re.findall(r"\w+", user_input.lower()) if len(w) >= 3}:
            def_key = f"definition.{term}"
            if def_key in seen_fact_keys:
                continue
            record = self.get_fact_record(def_key)
            if (
                record
                and float(record.get("confidence") or 0.0) >= effective_min_conf
            ):
                facts.append(record)
                seen_fact_keys.add(def_key)
        facts = self.prioritize_temporal_facts(facts, limit=6)
        relevant_results = self.get_relevant_results(query, limit=4) if query else []
        relevant_procedures = self.search_procedures(query, limit=3) if query else []
        history = self.get_working_memory(n_history)
        vector = getattr(self, "_vector", None)
        semantic_context = ""
        if query and vector and vector.aktiv:
            semantic_context = vector.als_kontext(query) or ""

        # Optional external memory (Mem0/Cognee/Letta) — fail-soft, default off
        external_hits: list[dict] = []
        try:
            from external_memory import get_external_memory_bridge

            ext = get_external_memory_bridge()
            if ext.any_enabled() and user_input:
                external_hits = ext.search_all(user_input, limit=4)
                ext_block = ext.format_hits(external_hits)
                if ext_block:
                    if semantic_context:
                        semantic_context = f"{semantic_context}\n{ext_block}"
                    else:
                        semantic_context = ext_block
        except Exception:
            external_hits = []

        active_directives = []
        preferences = []
        for directive in directives:
            text = (directive.get("text") or "").strip()
            if not text:
                continue
            active_directives.append({
                "id": directive.get("id", ""),
                "text": text[:180],
                "priority": directive.get("priority", 0),
            })
            preferences.append({
                "source": "directive",
                "text": text[:180],
                "priority": directive.get("priority", 0),
            })

        relevant_facts = []
        for fact in facts:
            value = (fact.get("value") or "").strip()
            normalized = {
                "key": fact.get("key", ""),
                "value": value[:180],
                "confidence": fact.get("confidence", 0.0),
                "source": fact.get("source", ""),
            }
            relevant_facts.append(normalized)
            key = (fact.get("key") or "").lower()
            if (
                self._is_preference_key(key)
                and normalized["confidence"] >= effective_min_conf
            ):
                preferences.append({
                    "source": "fact",
                    "key": normalized["key"],
                    "value": normalized["value"],
                    "confidence": normalized["confidence"],
                })

        if external_hits:
            try:
                from external_memory import get_external_memory_bridge

                for pref in get_external_memory_bridge().hits_as_preferences(
                    external_hits
                ):
                    preferences.append(pref)
            except Exception:
                pass

        conversation_history = []
        project_context = []
        for entry in history:
            text = (entry.get("text") or "").strip()
            normalized = {
                "role": entry.get("role", ""),
                "text": text[:180],
            }
            conversation_history.append(normalized)
            if any(marker in text.lower() for marker in ("isaac", "routing", "executor", "tool", "klass", "class")):
                project_context.append(normalized)
        project_context = project_context[-3:]

        relevant_task_results = []
        behavioral_risks = []
        relevant_reflections = []
        for result in relevant_results:
            normalized = {
                "description": (result.get("description") or "")[:120],
                "result": (result.get("result") or "")[:220],
                "score": result.get("score", 0.0),
                "provider": result.get("provider", ""),
            }
            relevant_task_results.append(normalized)
            result_text = normalized["result"].lower()
            risk_tags = []
            if "tools genutzt" in result_text or "tool" in result_text:
                risk_tags.append("tool_overreach_risk")
            if normalized["score"] <= 4.0:
                risk_tags.append("quality_regression_risk")
            if risk_tags:
                behavioral_risks.append({
                    "description": normalized["description"],
                    "score": normalized["score"],
                    "risks": risk_tags,
                })
            if "reflekt" in result_text or "pattern" in result_text:
                relevant_reflections.append(normalized["result"])

        open_questions = []
        if intent == "chat" and interaction_class == "NORMAL_CHAT" and len(user_input.split()) <= 2 and "?" not in user_input:
            open_questions.append("Nutzerabsicht bei sehr kurzem Input potenziell unklar.")
        open_questions.extend(
            self._regelwerk_open_questions_for_retrieval(user_input)[:3]
        )

        for proc in relevant_procedures:
            if proc.get("degraded"):
                behavioral_risks.append({
                    "description": proc.get("intent_hint") or proc.get("task_type", "procedure"),
                    "score": proc.get("reliability", 0.0),
                    "risks": ["degraded_procedure"],
                })

        active_goals = self._active_goals_for_retrieval(limit=5)

        return RetrievalContext(
            query=query,
            active_directives=active_directives,
            relevant_facts=relevant_facts[:6],
            semantic_context=semantic_context.strip(),
            conversation_history=conversation_history[-n_history:],
            relevant_task_results=relevant_task_results[:4],
            preferences_context=preferences[:4],
            project_context=project_context,
            behavioral_risks=behavioral_risks[:3],
            relevant_reflections=relevant_reflections[:2],
            open_questions=open_questions[:3],
            relevant_procedures=relevant_procedures[:3],
            active_goals=active_goals,
            modulator_state=mod_state,
        )

    def _active_goals_for_retrieval(self, *, limit: int = 5) -> list[dict]:
        """Short active Owner-Goal surface for retrieval (fail-soft).

        Caps lines for prompt size. Includes next subgoal title and up to
        two open goal-bound inquiries when available.
        """
        rows: list[dict] = []
        try:
            from goal_store import get_goal_store

            gs = get_goal_store()
            open_inq: dict[str, list[str]] = {}
            try:
                from goal_inquiry import get_inquiry_store

                for item in get_inquiry_store().list_open()[:12]:
                    open_inq.setdefault(item.goal_id, []).append(item.question[:90])
            except Exception:
                pass

            for goal in gs.list_goals(status="active")[: max(1, int(limit))]:
                subs = gs.list_subgoals(goal.id, status="active")
                next_sub = ""
                if subs:
                    # Prefer non-recovery first, else first active
                    ordered = sorted(
                        subs,
                        key=lambda s: (0 if s.origin != "failure_recovery" else 1, s.created_at),
                    )
                    next_sub = (ordered[0].title or "")[:80]
                latest = ""
                try:
                    rec = self.get_fact_record(f"goal_latest.{goal.id}")
                    if rec and float(rec.get("confidence") or 0) >= MIN_RETRIEVAL_CONFIDENCE:
                        latest = str(rec.get("value") or "")[:120]
                except Exception:
                    latest = ""
                rows.append(
                    {
                        "goal_id": goal.id,
                        "title": (goal.title or "")[:100],
                        "priority": float(goal.priority or 0.0),
                        "subgoals_active": len(subs),
                        "next_subgoal": next_sub,
                        "success_criteria": (goal.success_criteria or "")[:120],
                        "latest": latest,
                        "open_inquiries": (open_inq.get(goal.id) or [])[:2],
                    }
                )
        except Exception as exc:
            log.debug("active_goals retrieval: %s", exc)
            return []
        return rows

    def format_retrieval_context(self, retrieval_ctx: RetrievalContext | dict[str, Any]) -> str:
        if isinstance(retrieval_ctx, RetrievalContext):
            data = retrieval_ctx.as_dict()
        else:
            data = retrieval_ctx or {}

        sections: list[str] = []
        if data.get("active_directives"):
            sections.append("[active_directives]")
            for directive in data["active_directives"]:
                sections.append(
                    f"  - prio={directive.get('priority', 0)}: {directive.get('text', '')}"
                )
        if data.get("active_goals"):
            sections.append("[active_goals]")
            for g in data["active_goals"]:
                line = (
                    f"  - p={float(g.get('priority') or 0):.1f} "
                    f"[{str(g.get('goal_id') or '')[-8:]}] "
                    f"{g.get('title', '')}"
                )
                if g.get("next_subgoal"):
                    line += f" → {g.get('next_subgoal')}"
                if g.get("subgoals_active"):
                    line += f" ({g.get('subgoals_active')} sub)"
                sections.append(line)
                if g.get("success_criteria"):
                    sections.append(f"    criteria: {g.get('success_criteria')}")
                if g.get("latest"):
                    sections.append(f"    latest: {g.get('latest')}")
                for q in g.get("open_inquiries") or []:
                    sections.append(f"    open_q: {q}")
        if data.get("relevant_facts"):
            sections.append("[relevant_facts]")
            for fact in data["relevant_facts"]:
                sections.append(f"  - {fact.get('key', '')}: {fact.get('value', '')}")
        if data.get("semantic_context"):
            # May already contain an [external_memory] block from adapters
            if "[external_memory]" in (data["semantic_context"] or ""):
                sections.append("[semantic_context+external]")
            else:
                sections.append("[semantic_context]")
            sections.append(data["semantic_context"])
        if data.get("conversation_history"):
            sections.append("[conversation_history]")
            for entry in data["conversation_history"]:
                sections.append(f"  - {entry.get('role', '')}: {entry.get('text', '')}")
        if data.get("relevant_task_results"):
            sections.append("[relevant_task_results]")
            for result in data["relevant_task_results"]:
                sections.append(
                    f"  - score={result.get('score', 0.0)} {result.get('description', '')}: {result.get('result', '')}"
                )
        if data.get("preferences_context"):
            sections.append("[preferences_context]")
            for item in data["preferences_context"]:
                if item.get("source") == "directive":
                    sections.append(
                        f"  - directive(prio={item.get('priority', 0)}): {item.get('text', '')}"
                    )
                elif item.get("source") == "mem0":
                    sections.append(
                        f"  - mem0: {item.get('text') or item.get('value', '')}"
                    )
                else:
                    sections.append(f"  - fact {item.get('key', '')}: {item.get('value', '')}")
        if data.get("project_context"):
            sections.append("[project_context]")
            for item in data["project_context"]:
                sections.append(f"  - {item.get('role', '')}: {item.get('text', '')}")
        if data.get("behavioral_risks"):
            sections.append("[behavioral_risks]")
            for risk in data["behavioral_risks"]:
                sections.append(f"  - {','.join(risk.get('risks', []))}: {risk.get('description', '')}")
        if data.get("relevant_reflections"):
            sections.append("[relevant_reflections]")
            for ref in data["relevant_reflections"]:
                sections.append(f"  - {ref}")
        if data.get("open_questions"):
            sections.append("[open_questions]")
            for q in data["open_questions"]:
                sections.append(f"  - {q}")
        if data.get("relevant_procedures"):
            sections.append("[relevant_procedures]")
            for proc in data["relevant_procedures"]:
                tools = ", ".join(proc.get("tools_used") or []) or "keine"
                flag = " DEGRADED" if proc.get("degraded") else ""
                sections.append(
                    f"  - rel={proc.get('reliability', 0.0):.2f}{flag} "
                    f"{proc.get('task_type', '')} tools={tools}: "
                    f"{proc.get('trace_summary') or proc.get('intent_hint', '')}"
                )
        # Optional RepoMap enrichment (Phase 1) — only if already on dict
        if data.get("code_map"):
            try:
                from repo_map import format_code_map_section

                block = format_code_map_section(data)
                if block:
                    sections.append(block)
            except Exception:
                sections.append("[code_map]")
                sections.append(str(data.get("code_map") or "")[:4000])
        return "\n".join(sections).strip()

    # ── Kontext-Aufbau für Relay ───────────────────────────────────────────────
    def build_context(self, query: str = "", n_history: int = 6) -> str:
        """Deprecated legacy wrapper.

        Prefer ``build_retrieval_context()`` + ``format_retrieval_context()`` in callers.
        The kernel standard path must not depend on this string builder.
        """
        import warnings

        warnings.warn(
            "memory.build_context() is deprecated; use build_retrieval_context()",
            DeprecationWarning,
            stacklevel=2,
        )
        retrieval_ctx = self.build_retrieval_context(query, n_history=n_history)
        return self.format_retrieval_context(retrieval_ctx)

    # ── Statistiken ───────────────────────────────────────────────────────────
    # ── Epistemisches Memory (Säule 4 - Prove the Kernel) ─────────────────
    def add_epistemic_memory(
        self,
        key: str,
        value: str,
        epistemic_class: str | EpistemicClass = EpistemicClass.OBSERVATION,
        source: str = "",
        source_authority: str = "system",
        confidence: float = 0.5,
        validity_period: Optional[dict] = None,
        evidence_task_ids: Optional[list[str]] = None,
        contradicted_by: Optional[list[str]] = None,
        relations: Optional[list[dict]] = None,
        metadata: Optional[dict] = None,
    ) -> EpistemicMemoryEntry:
        import uuid
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        mem_id = f"mem_{uuid.uuid4().hex[:10]}"
        eclass = epistemic_class.value if isinstance(epistemic_class, EpistemicClass) else str(epistemic_class)
        val_start = (validity_period or {}).get("start", "")
        val_end = (validity_period or {}).get("end", "")
        ev_tasks = json.dumps(evidence_task_ids or [])
        contra = json.dumps(contradicted_by or [])
        rel = json.dumps(relations or [])
        meta = json.dumps(metadata or {})

        with _conn() as con:
            con.execute(
                "INSERT OR REPLACE INTO epistemic_memories "
                "(memory_id, ts, key, value, epistemic_class, source, source_authority, "
                "confidence, validity_start, validity_end, evidence_task_ids, contradicted_by, relations, metadata) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mem_id, ts, key, value, eclass, source, source_authority,
                 float(confidence), val_start, val_end, ev_tasks, contra, rel, meta)
            )

        AuditLog.memory_write("Memory", "epistemic", key)
        return EpistemicMemoryEntry(
            memory_id=mem_id,
            ts=ts,
            key=key,
            value=value,
            epistemic_class=EpistemicClass(eclass) if eclass in EpistemicClass.__members__ else EpistemicClass.OBSERVATION,
            source=source,
            source_authority=source_authority,
            confidence=float(confidence),
            validity_start=val_start,
            validity_end=val_end,
            evidence_task_ids=evidence_task_ids or [],
            contradicted_by=contradicted_by or [],
            relations=relations or [],
            metadata=metadata or {},
        )

    def get_epistemic_memory(self, memory_id_or_key: str) -> Optional[EpistemicMemoryEntry]:
        with _conn() as con:
            row = con.execute(
                "SELECT * FROM epistemic_memories WHERE memory_id=? OR key=? ORDER BY id DESC LIMIT 1",
                (memory_id_or_key, memory_id_or_key),
            ).fetchone()
        if not row:
            return None
        r = dict(row)
        eclass_str = r.get("epistemic_class", "OBSERVATION")
        try:
            eclass = EpistemicClass(eclass_str)
        except ValueError:
            eclass = EpistemicClass.OBSERVATION
        return EpistemicMemoryEntry(
            memory_id=r["memory_id"],
            ts=r["ts"],
            key=r["key"],
            value=r["value"],
            epistemic_class=eclass,
            source=r.get("source", ""),
            source_authority=r.get("source_authority", "system"),
            confidence=float(r.get("confidence") or 0.5),
            validity_start=r.get("validity_start", ""),
            validity_end=r.get("validity_end", ""),
            evidence_task_ids=json.loads(r.get("evidence_task_ids") or "[]"),
            contradicted_by=json.loads(r.get("contradicted_by") or "[]"),
            relations=json.loads(r.get("relations") or "[]"),
            metadata=json.loads(r.get("metadata") or "{}"),
        )

    def resolve_contradiction(
        self,
        memory_id: str,
        contradicting_ref: str,
        new_epistemic_class: EpistemicClass | str = EpistemicClass.CONTRADICTED,
        new_confidence: float = 0.1,
    ) -> Optional[EpistemicMemoryEntry]:
        """Marks an epistemic memory entry as contradicted by new evidence or task."""
        entry = self.get_epistemic_memory(memory_id)
        if not entry:
            return None

        contradicted_list = list(entry.contradicted_by)
        if contradicting_ref not in contradicted_list:
            contradicted_list.append(contradicting_ref)

        eclass_val = new_epistemic_class.value if isinstance(new_epistemic_class, EpistemicClass) else str(new_epistemic_class)

        with _conn() as con:
            con.execute(
                "UPDATE epistemic_memories SET epistemic_class=?, confidence=?, contradicted_by=? WHERE memory_id=?",
                (eclass_val, max(0.0, min(1.0, new_confidence)), json.dumps(contradicted_list), entry.memory_id),
            )
        return self.get_epistemic_memory(entry.memory_id)

    def link_evidence_to_memory(
        self,
        memory_id: str,
        task_id: str,
        confidence_boost: float = 0.0,
    ) -> Optional[EpistemicMemoryEntry]:
        """Links an execution Task ID as supporting evidence for an epistemic memory."""
        entry = self.get_epistemic_memory(memory_id)
        if not entry:
            return None

        evidence_list = list(entry.evidence_task_ids)
        if task_id not in evidence_list:
            evidence_list.append(task_id)

        updated_confidence = min(1.0, max(0.0, entry.confidence + confidence_boost))

        with _conn() as con:
            con.execute(
                "UPDATE epistemic_memories SET evidence_task_ids=?, confidence=? WHERE memory_id=?",
                (json.dumps(evidence_list), updated_confidence, entry.memory_id),
            )
        return self.get_epistemic_memory(entry.memory_id)

    def search_epistemic_memories(
        self, query: str = "", epistemic_class: Optional[str] = None, limit: int = 20
    ) -> list[EpistemicMemoryEntry]:
        with _conn() as con:
            if epistemic_class:
                rows = con.execute(
                    "SELECT * FROM epistemic_memories WHERE epistemic_class=? AND (key LIKE ? OR value LIKE ?) ORDER BY id DESC LIMIT ?",
                    (epistemic_class, f"%{query}%", f"%{query}%", limit),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM epistemic_memories WHERE key LIKE ? OR value LIKE ? ORDER BY id DESC LIMIT ?",
                    (f"%{query}%", f"%{query}%", limit),
                ).fetchall()

        results = []
        for row in rows:
            r = dict(row)
            eclass_str = r.get("epistemic_class", "OBSERVATION")
            try:
                eclass = EpistemicClass(eclass_str)
            except ValueError:
                eclass = EpistemicClass.OBSERVATION
            results.append(
                EpistemicMemoryEntry(
                    memory_id=r["memory_id"],
                    ts=r["ts"],
                    key=r["key"],
                    value=r["value"],
                    epistemic_class=eclass,
                    source=r.get("source", ""),
                    source_authority=r.get("source_authority", "system"),
                    confidence=float(r.get("confidence") or 0.5),
                    validity_start=r.get("validity_start", ""),
                    validity_end=r.get("validity_end", ""),
                    evidence_task_ids=json.loads(r.get("evidence_task_ids") or "[]"),
                    contradicted_by=json.loads(r.get("contradicted_by") or "[]"),
                    relations=json.loads(r.get("relations") or "[]"),
                    metadata=json.loads(r.get("metadata") or "{}"),
                )
            )
        return results

    def mark_contradicted(self, memory_id: str, contradicting_ref: str) -> bool:
        entry = self.get_epistemic_memory(memory_id)
        if not entry:
            return False
        contra = set(entry.contradicted_by)
        contra.add(contradicting_ref)
        contra_json = json.dumps(list(contra))
        with _conn() as con:
            con.execute(
                "UPDATE epistemic_memories SET epistemic_class='CONTRADICTED', contradicted_by=? WHERE memory_id=?",
                (contra_json, entry.memory_id),
            )
        return True

    def mark_verified(self, memory_id: str) -> bool:
        entry = self.get_epistemic_memory(memory_id)
        if not entry:
            return False
        with _conn() as con:
            con.execute(
                "UPDATE epistemic_memories SET epistemic_class='VERIFIED', confidence=1.0 WHERE memory_id=?",
                (entry.memory_id,),
            )
        return True

    def stats(self) -> dict:
        with _conn() as con:
            n_conv  = con.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            n_facts = con.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
            n_tasks = con.execute("SELECT COUNT(*) FROM task_results").fetchone()[0]
            n_proc  = con.execute("SELECT COUNT(*) FROM task_procedures").fetchone()[0]
            n_dir   = con.execute(
                "SELECT COUNT(*) FROM directives WHERE active=1"
            ).fetchone()[0]
        return {
            "conversations":  n_conv,
            "facts":          n_facts,
            "task_results":   n_tasks,
            "procedures":     n_proc,
            "directives":     n_dir,
            "working_memory": len(self._working),
            "vector":         self._vector.stats(),
        }

    def _load_working_memory(self):
        cfg = get_config()
        n = cfg.memory.max_working_memory
        with _conn() as con:
            rows = con.execute(
                "SELECT ts, role, text, task_id, provider, quality "
                "FROM conversations ORDER BY id DESC LIMIT ?", (n,)
            ).fetchall()
        self._working = [dict(r) for r in reversed(rows)]

    # ── Entwicklungslog ───────────────────────────────────────────────────────
    def log_development_event(
        self,
        event_type: str,
        target_kind: str,
        target_key: str,
        delta: float = 0.0,
        confidence_before: float = 0.0,
        confidence_after: float = 0.0,
        evidence_refs: list | None = None,
        contradiction_refs: list | None = None,
        reason: str = "",
        requires_review: bool = False,
        metadata: dict | None = None,
    ) -> int:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with _conn() as con:
            cur = con.execute(
                "INSERT INTO development_events "
                "(ts, event_type, target_kind, target_key, delta, confidence_before, "
                "confidence_after, evidence_refs, contradiction_refs, reason, "
                "requires_review, reviewed_by_owner, metadata) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ts,
                    event_type,
                    target_kind,
                    target_key,
                    float(delta),
                    float(confidence_before),
                    float(confidence_after),
                    json.dumps(evidence_refs or [], ensure_ascii=False),
                    json.dumps(contradiction_refs or [], ensure_ascii=False),
                    reason[:300],
                    1 if requires_review else 0,
                    0,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            return int(cur.lastrowid)

    def recent_development_events(self, n: int = 20) -> list[dict]:
        with _conn() as con:
            rows = con.execute(
                "SELECT * FROM development_events ORDER BY id DESC LIMIT ?",
                (max(1, int(n)),),
            ).fetchall()
        return [dict(r) for r in rows]

    def archive_development_events(
        self, older_than_days: int = 90, keep_recent: int = 300
    ) -> int:
        cutoff = (
            datetime.now() - timedelta(days=max(1, int(older_than_days)))
        ).strftime("%Y-%m-%d %H:%M:%S")
        archived_at = time.strftime("%Y-%m-%d %H:%M:%S")
        with _conn() as con:
            rows = con.execute(
                """SELECT * FROM development_events
                   WHERE ts < ? AND id NOT IN (
                       SELECT id FROM development_events
                       ORDER BY id DESC LIMIT ?
                   )""",
                (cutoff, max(1, int(keep_recent))),
            ).fetchall()
            if not rows:
                return 0
            for row in rows:
                con.execute(
                    "INSERT OR REPLACE INTO development_events_archive "
                    "(id, ts, event_type, target_kind, target_key, delta, "
                    "confidence_before, confidence_after, evidence_refs, "
                    "contradiction_refs, reason, requires_review, "
                    "reviewed_by_owner, metadata, archived_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        row["id"],
                        row["ts"],
                        row["event_type"],
                        row["target_kind"],
                        row["target_key"],
                        row["delta"],
                        row["confidence_before"],
                        row["confidence_after"],
                        row["evidence_refs"],
                        row["contradiction_refs"],
                        row["reason"],
                        row["requires_review"],
                        row["reviewed_by_owner"],
                        row["metadata"],
                        archived_at,
                    ),
                )
            ids = [row["id"] for row in rows]
            placeholders = ",".join("?" for _ in ids)
            con.execute(
                f"DELETE FROM development_events WHERE id IN ({placeholders})",
                ids,
            )
        AuditLog.memory_write("Memory", "archive", f"development_events:{len(rows)}")
        return len(rows)

    def recent_archived_development_events(self, n: int = 20) -> list[dict]:
        with _conn() as con:
            rows = con.execute(
                "SELECT * FROM development_events_archive "
                "ORDER BY archived_at DESC LIMIT ?",
                (max(1, int(n)),),
            ).fetchall()
        return [dict(r) for r in rows]

    def decay_stats(self) -> dict:
        with _conn() as con:
            n_active = con.execute("SELECT COUNT(*) FROM development_events").fetchone()[0]
            n_archive = con.execute(
                "SELECT COUNT(*) FROM development_events_archive"
            ).fetchone()[0]
            n_weak = con.execute(
                """SELECT COUNT(*) FROM facts
                   WHERE confidence < ? AND confidence >= ?""",
                (0.75, MIN_RETRIEVAL_CONFIDENCE),
            ).fetchone()[0]
        return {
            "development_active": int(n_active),
            "development_archived": int(n_archive),
            "weak_facts": int(n_weak),
            "min_retrieval_confidence": MIN_RETRIEVAL_CONFIDENCE,
        }

    # ── Task-Checkpoints ──────────────────────────────────────────────────────
    def save_task_checkpoint(
        self,
        task_id: str,
        state_name: str,
        input_snapshot: dict | None = None,
        tool_snapshot: dict | None = None,
        result_snapshot: dict | None = None,
        memory_refs: list | None = None,
        side_effect_refs: list | None = None,
    ) -> int:
        from task_checkpoint import (
            CHECKPOINT_GLOBAL_MAX,
            CHECKPOINT_MAX_PER_TASK,
        )

        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with _conn() as con:
            cur = con.execute(
                "INSERT INTO task_checkpoints "
                "(task_id, ts, state_name, input_snapshot, tool_snapshot, "
                "result_snapshot, memory_refs, side_effect_refs) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    ts,
                    state_name,
                    json.dumps(input_snapshot or {}, ensure_ascii=False),
                    json.dumps(tool_snapshot or {}, ensure_ascii=False),
                    json.dumps(result_snapshot or {}, ensure_ascii=False),
                    json.dumps(memory_refs or [], ensure_ascii=False),
                    json.dumps(side_effect_refs or [], ensure_ascii=False),
                ),
            )
            checkpoint_id = int(cur.lastrowid)
        try:
            self.cleanup_task_checkpoints(
                task_id=task_id,
                max_per_task=CHECKPOINT_MAX_PER_TASK,
                global_max=CHECKPOINT_GLOBAL_MAX,
            )
        except Exception as exc:
            log.debug("Checkpoint cleanup skipped: %s", exc)
        return checkpoint_id

    def get_latest_checkpoint(self, task_id: str) -> dict | None:
        with _conn() as con:
            row = con.execute(
                "SELECT * FROM task_checkpoints WHERE task_id=? "
                "ORDER BY checkpoint_id DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_checkpoint_by_id(self, checkpoint_id: int) -> dict | None:
        with _conn() as con:
            row = con.execute(
                "SELECT * FROM task_checkpoints WHERE checkpoint_id=?",
                (int(checkpoint_id),),
            ).fetchone()
        return dict(row) if row else None

    def list_checkpoints(self, task_id: str, limit: int = 20) -> list[dict]:
        with _conn() as con:
            rows = con.execute(
                "SELECT * FROM task_checkpoints WHERE task_id=? "
                "ORDER BY checkpoint_id DESC LIMIT ?",
                (task_id, max(1, int(limit))),
            ).fetchall()
        return [dict(r) for r in rows]

    def checkpoint_stats(self) -> dict[str, Any]:
        with _conn() as con:
            total = int(con.execute("SELECT COUNT(*) FROM task_checkpoints").fetchone()[0])
            tasks = int(con.execute(
                "SELECT COUNT(DISTINCT task_id) FROM task_checkpoints"
            ).fetchone()[0])
        return {"total": total, "tasks": tasks}

    @staticmethod
    def _parse_checkpoint_ts(ts: str) -> datetime | None:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(ts, fmt)
            except ValueError:
                continue
        return None

    def cleanup_task_checkpoints(
        self,
        *,
        task_id: str | None = None,
        max_per_task: int | None = None,
        max_age_days: int | None = None,
        global_max: int | None = None,
    ) -> dict[str, Any]:
        from task_checkpoint import (
            CHECKPOINT_GLOBAL_MAX,
            CHECKPOINT_MAX_AGE_DAYS,
            CHECKPOINT_MAX_PER_TASK,
            TERMINAL_CHECKPOINT_STATES,
        )

        max_per_task = max(1, int(max_per_task or CHECKPOINT_MAX_PER_TASK))
        max_age_days = int(max_age_days if max_age_days is not None else CHECKPOINT_MAX_AGE_DAYS)
        global_max = int(global_max if global_max is not None else CHECKPOINT_GLOBAL_MAX)
        removed = 0
        trimmed_tasks = 0
        aged_removed = 0
        global_removed = 0

        with _conn() as con:
            task_ids = [task_id] if task_id else [
                row[0] for row in con.execute(
                    "SELECT DISTINCT task_id FROM task_checkpoints"
                ).fetchall()
            ]

            for current_task_id in task_ids:
                rows = con.execute(
                    "SELECT checkpoint_id, ts, state_name FROM task_checkpoints "
                    "WHERE task_id=? ORDER BY checkpoint_id DESC",
                    (current_task_id,),
                ).fetchall()
                if not rows:
                    continue

                latest_id = int(rows[0]["checkpoint_id"])
                keep_ids = {latest_id}
                drop_ids: list[int] = []

                if len(rows) > max_per_task:
                    for row in rows[max_per_task:]:
                        cp_id = int(row["checkpoint_id"])
                        if cp_id != latest_id:
                            drop_ids.append(cp_id)
                    if drop_ids:
                        trimmed_tasks += 1

                if max_age_days > 0:
                    cutoff = datetime.now() - timedelta(days=max_age_days)
                    for row in rows[1:]:
                        cp_id = int(row["checkpoint_id"])
                        if cp_id in keep_ids or cp_id in drop_ids:
                            continue
                        state_name = (row["state_name"] or "").strip().lower()
                        if state_name not in TERMINAL_CHECKPOINT_STATES:
                            continue
                        parsed = self._parse_checkpoint_ts(row["ts"] or "")
                        if parsed and parsed < cutoff:
                            drop_ids.append(cp_id)
                            aged_removed += 1

                drop_ids = [cp_id for cp_id in drop_ids if cp_id not in keep_ids]
                if drop_ids:
                    con.executemany(
                        "DELETE FROM task_checkpoints WHERE checkpoint_id=?",
                        [(cp_id,) for cp_id in sorted(set(drop_ids))],
                    )
                    removed += len(set(drop_ids))

            if global_max > 0:
                total = int(con.execute("SELECT COUNT(*) FROM task_checkpoints").fetchone()[0])
                if total > global_max:
                    latest_per_task = {
                        row[0]: int(row[1])
                        for row in con.execute(
                            "SELECT task_id, MAX(checkpoint_id) "
                            "FROM task_checkpoints GROUP BY task_id"
                        ).fetchall()
                    }
                    overflow = total - global_max
                    rows = con.execute(
                        "SELECT checkpoint_id, task_id FROM task_checkpoints "
                        "ORDER BY checkpoint_id ASC LIMIT ?",
                        (overflow + len(latest_per_task),),
                    ).fetchall()
                    for row in rows:
                        if overflow <= 0:
                            break
                        cp_id = int(row["checkpoint_id"])
                        if latest_per_task.get(row["task_id"]) == cp_id:
                            continue
                        con.execute(
                            "DELETE FROM task_checkpoints WHERE checkpoint_id=?",
                            (cp_id,),
                        )
                        global_removed += 1
                        overflow -= 1
                    removed += global_removed

        summary = {
            "removed": removed,
            "trimmed_tasks": trimmed_tasks,
            "aged_removed": aged_removed,
            "global_removed": global_removed,
            "remaining": self.checkpoint_stats().get("total", 0),
        }
        if removed:
            AuditLog.action("Memory", "checkpoint_cleanup", json.dumps(summary, ensure_ascii=False))
        return summary


# ── Singleton ──────────────────────────────────────────────────────────────────
_memory: Optional[Memory] = None

def get_memory() -> Memory:
    global _memory
    if _memory is None:
        _memory = Memory()
    return _memory
