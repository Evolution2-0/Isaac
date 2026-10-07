from __future__ import annotations

"""Isaac – Self-Model Hooks
Bindet Interaktionen an das explizite Selbstmodell: Feedback, Themen, Präferenzen.
"""

import re
import logging
from typing import Any

from low_complexity import InteractionClass, classify_interaction_result

log = logging.getLogger("Isaac.SelfModelHooks")

PREFER_PATTERNS = (
    (re.compile(r"(?:ich bevorzuge|ich mag lieber|bitte immer|ab jetzt|lieber)\s+(.+)", re.I), "prefer"),
    (re.compile(r"(?:nicht mehr|bitte nicht|vermeide|kein(?:e)?)\s+(.+)", re.I), "avoid"),
)
STYLE_DIRECTIVE_PATTERNS = (
    (
        re.compile(
            r"^(?:bitte\s+)?(?:antworte?|schreib(?:e)?|formuliere)\b.{0,30}\b"
            r"(kürzer|kurz und knapp|weniger detail|knapp)\b",
            re.I,
        ),
        "kurz, strukturiert",
    ),
    (
        re.compile(
            r"^(?:bitte\s+)?(?:antworte?|schreib(?:e)?|formuliere)\b.{0,30}\b"
            r"(ausführlicher|detaillierter|mehr detail|genauer)\b",
            re.I,
        ),
        "detailliert, strukturiert",
    ),
    (re.compile(r"^(kürzer|kurz und knapp|weniger detail|knapp halten)\s*[!.]?$", re.I), "kurz, strukturiert"),
    (re.compile(r"^(ausführlicher|detaillierter|mehr detail|genauer)\s*[!.]?$", re.I), "detailliert, strukturiert"),
)
CONFIRMATION_MARKERS = (
    "genau so",
    "richtig so",
    "passt so",
    "perfekt so",
    "so ist gut",
    "genau richtig",
    "gut gemacht",
    "super so",
    "das stimmt",
    "korrekt",
)
CORRECTION_FEEDBACK_MARKERS = (
    "falsch",
    "stimmt nicht",
    "nicht richtig",
    "das war falsch",
    "nein so nicht",
    "korrigiere",
)
CORRECTION_PATTERN = re.compile(
    r"^(?:korrektur|fakt|weiß):\s*(.+?)\s*=\s*(.+)$",
    re.IGNORECASE,
)
PREFERENCE_RECORD_CLASSES = frozenset({
    InteractionClass.NORMAL_CHAT,
})
CONFIRMATION_MIN_SCORE = 5.0
TOPIC_STOPWORDS = frozenset({
    "isaac", "bitte", "danke", "hallo", "mach", "mache", "kannst", "können",
    "would", "could", "please", "thanks", "hello",
})


def _extract_topic_theme(text: str) -> str:
    words = [
        w.strip(".,!?").lower()
        for w in (text or "").split()
        if len(w.strip(".,!?")) >= 4
    ]
    meaningful = [w for w in words if w not in TOPIC_STOPWORDS]
    if len(meaningful) < 2:
        return ""
    return " ".join(meaningful[:3])[:80]


def _is_positive_confirmation(text: str) -> bool:
    lowered = (text or "").strip().lower()
    return any(marker in lowered for marker in CONFIRMATION_MARKERS)


def _can_record_preferences(interaction_class: str) -> bool:
    return interaction_class in PREFERENCE_RECORD_CLASSES


def _extract_style_directive(text: str) -> str:
    for pattern, style in STYLE_DIRECTIVE_PATTERNS:
        if pattern.search(text or ""):
            return style
    return ""


def detect_self_model_fact_contradictions(memory: Any = None) -> list[dict[str, Any]]:
    from self_model import get_self_model

    return get_self_model().detect_fact_contradictions(memory=memory)


_SELF_QUERY_MARKERS = (
    "du selbst",
    "dich selbst",
    "verbesser",
    "selbstmodell",
    "self model",
    "wer bist du",
    "was bist du",
    "dein erbauer",
    "deine stärken",
    "deine schwächen",
    "über dich",
    "dein system",
    "architektur",
    "reife",
    "maturity",
)


def _is_self_referential_query(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in _SELF_QUERY_MARKERS)


def enrich_retrieval_with_self_model(
    retrieval_ctx: dict[str, Any],
    memory: Any = None,
    *,
    user_input: str = "",
) -> dict[str, Any]:
    from self_model import get_self_model

    sm = get_self_model()
    prefs = sm.relevant_preferences(limit=4)
    contradictions = sm.detect_fact_contradictions(memory=memory)
    self_query = _is_self_referential_query(user_input)
    # Always inject a compact self snapshot for self-referential owner questions
    # (fixes coverage=2.0 on "where would you improve yourself?" when facts=0).
    snapshot: list[dict[str, Any]] = []
    if self_query:
        try:
            data = {}
            if hasattr(sm, "snapshot"):
                data = sm.snapshot() or {}
            elif hasattr(sm, "data"):
                data = sm.data or {}
            ident = (data.get("identity_core") or {}) if isinstance(data, dict) else {}
            dev = (data.get("development_state") or {}) if isinstance(data, dict) else {}
            rel = (data.get("relationship_state") or {}) if isinstance(data, dict) else {}
            epi = (data.get("epistemic_state") or {}) if isinstance(data, dict) else {}
            if ident:
                snapshot.append({
                    "key": "self_identity",
                    "value": (
                        f"name={ident.get('name')}; role={ident.get('role')}; "
                        f"owner={ident.get('owner')}"
                    ),
                    "source": "self_model",
                })
            if dev:
                snapshot.append({
                    "key": "self_development",
                    "value": (
                        f"phase={dev.get('phase')}; maturity={dev.get('maturity')}; "
                        f"milestones={','.join(dev.get('milestones') or [])}"
                    ),
                    "source": "self_model",
                })
            if rel:
                snapshot.append({
                    "key": "self_relationship",
                    "value": (
                        f"owner_trust={rel.get('owner_trust')}; "
                        f"style={rel.get('interaction_style')}"
                    ),
                    "source": "self_model",
                })
            # Surface architecture constraints as operational "facts"
            snapshot.append({
                "key": "self_architecture",
                "value": (
                    "pipeline=classify→retrieve→strategy→task→execute→evaluate→memory; "
                    "executor_does_not_reclassify; normal_chat_no_opportunistic_tools; "
                    "external_memory=mem0/letta/cognee_bounded; "
                    "canonical_repo=hansheute88/isaac"
                ),
                "source": "self_model",
            })
            known = list(epi.get("known_facts") or [])[:4]
            for fact in known:
                if isinstance(fact, dict):
                    snapshot.append({
                        "key": f"self_fact_{fact.get('key') or 'x'}",
                        "value": str(fact.get("value") or fact)[:240],
                        "source": "self_model",
                    })
                elif fact:
                    snapshot.append({
                        "key": "self_fact",
                        "value": str(fact)[:240],
                        "source": "self_model",
                    })
        except Exception as exc:
            log.debug("self_model snapshot failed: %s", exc)

    if not prefs and not contradictions and not snapshot:
        return retrieval_ctx
    merged = dict(retrieval_ctx)
    if prefs:
        existing = list(merged.get("preferences_context") or [])
        for pref in prefs:
            existing.append({
                "source": "self_model",
                "key": pref.get("key", ""),
                "value": pref.get("value", ""),
                "confidence": pref.get("confidence", 0.0),
            })
        merged["preferences_context"] = existing[:6]
        merged["self_model_preferences"] = prefs
    if snapshot:
        facts = list(merged.get("relevant_facts") or [])
        for row in snapshot:
            facts.append(row)
        merged["relevant_facts"] = facts[:16]
        merged["self_model_snapshot"] = snapshot
    if contradictions:
        merged["self_model_contradictions"] = contradictions
        merged["risk_flags"] = list(dict.fromkeys(
            list(merged.get("risk_flags") or []) + ["self_model_fact_contradiction"]
        ))
    return merged


def process_interaction(
    *,
    user_input: str,
    antwort: str = "",
    emp: Any = None,
    interaction_class: str = "",
    score: float = 0.0,
) -> dict[str, Any]:
    from self_model import get_self_model

    sm = get_self_model()
    sm.sync_constitutional_state()

    if not interaction_class:
        interaction_class = classify_interaction_result(user_input).interaction_class

    updates: dict[str, Any] = {
        "preferences": [],
        "themes": [],
        "feedback": False,
        "confirmations": 0,
    }

    if _can_record_preferences(interaction_class):
        correction = CORRECTION_PATTERN.match((user_input or "").strip())
        if correction:
            key = correction.group(1).strip()
            value = correction.group(2).strip()
            entry = sm.record_owner_preference(
                key=key,
                value=value,
                confidence=1.0,
                source="owner_correction",
                evidence=user_input[:200],
            )
            sm.note_owner_feedback(user_input[:500])
            updates["preferences"].append(entry)
            updates["feedback"] = True

        for pattern, category in PREFER_PATTERNS:
            match = pattern.search(user_input or "")
            if not match:
                continue
            value = match.group(1).strip()[:120]
            if not value:
                continue
            conf = 0.75 if category == "avoid" else 0.7
            entry = sm.record_owner_preference(
                key=category,
                value=value,
                confidence=conf,
                source="owner_statement",
                evidence=user_input[:200],
            )
            updates["preferences"].append(entry)

        style = _extract_style_directive(user_input or "")
        if style:
            entry = sm.record_owner_preference(
                key="response_style",
                value=style,
                confidence=0.55,
                source="owner_style_hint",
                evidence=user_input[:200],
            )
            updates["preferences"].append(entry)
            sm.update_preference("response_style", style, source="owner_style_hint")

    if interaction_class == InteractionClass.SOCIAL_ACKNOWLEDGMENT:
        sm.note_owner_feedback(user_input[:500])
        updates["feedback"] = True
        if _is_positive_confirmation(user_input) and score >= CONFIRMATION_MIN_SCORE:
            updates["confirmations"] = sm.confirm_pending_preferences(
                boost=0.06 if score >= 6.0 else 0.04,
                reason="positive owner confirmation",
            )
            if updates["confirmations"]:
                sm.apply_relationship_delta("owner_trust", 0.03, "positive confirmation")
        else:
            sm.apply_relationship_delta("owner_trust", 0.02, "positive acknowledgment")

    # Explicit correction language (any class) → soft trust dip + feedback note
    lowered_fb = (user_input or "").strip().lower()
    if any(m in lowered_fb for m in CORRECTION_FEEDBACK_MARKERS):
        sm.note_owner_feedback(user_input[:500])
        sm.apply_relationship_delta("owner_trust", -0.03, "owner correction language")
        updates["feedback"] = True

    if interaction_class == InteractionClass.NORMAL_CHAT:
        theme = _extract_topic_theme(user_input)
        if theme:
            tracked = sm.track_shared_theme(theme)
            if tracked:
                updates["themes"].append(tracked)
        # Positive confirmation in normal chat also strengthens relationship (C4)
        if _is_positive_confirmation(user_input) and score >= CONFIRMATION_MIN_SCORE:
            sm.note_owner_feedback(user_input[:500])
            sm.apply_relationship_delta("owner_trust", 0.02, "chat confirmation")
            updates["feedback"] = True

    if emp and getattr(emp, "node", None):
        zustand = str(getattr(emp.node, "zustand", "") or "").lower()
        if any(token in zustand for token in ("frustriert", "verärgert", "enttäuscht")):
            sm.note_owner_feedback(user_input[:500])
            sm.apply_relationship_delta("owner_trust", -0.04, "negative empathy signal")
            updates["feedback"] = True

    if score >= 6.0 and interaction_class == InteractionClass.NORMAL_CHAT:
        sm.bump_maturity(0.005)

    return updates