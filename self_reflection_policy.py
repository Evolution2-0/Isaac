"""Isaac – centralized policy for explicit self-reflection requests.

The policy defines what Isaac may explain about itself without turning ordinary
answers into unnecessary meta-discussion. It deliberately separates useful
self-description from claims about hidden chain-of-thought or unavailable
internal state.
"""
from __future__ import annotations

from enum import Enum


class SelfReflectionCategory(str, Enum):
    """Supported categories of explicit questions about Isaac itself."""

    SELF_DESCRIPTION = "self_description"
    SELF_REFLECTION = "self_reflection"
    ARCHITECTURE = "architecture"
    DEVELOPMENT_STATE = "development_state"
    DECISION_LOGIC = "decision_logic"
    INTERNAL_SECRETS = "internal_secrets"


SELF_REFLECTION_CATEGORIES: tuple[SelfReflectionCategory, ...] = (
    SelfReflectionCategory.SELF_DESCRIPTION,
    SelfReflectionCategory.SELF_REFLECTION,
    SelfReflectionCategory.ARCHITECTURE,
    SelfReflectionCategory.DEVELOPMENT_STATE,
    SelfReflectionCategory.DECISION_LOGIC,
)


def allows_self_reflection(category: SelfReflectionCategory | str) -> bool:
    """Return whether a category can receive a substantive self-explanation."""
    try:
        category = SelfReflectionCategory(category)
    except ValueError:
        return False
    return category in SELF_REFLECTION_CATEGORIES


def build_self_reflection_policy_prompt() -> str:
    """Build the single canonical system-prompt policy for Isaac self-reflection."""
    return (
        "SELF_REFLECTION_POLICY: "
        "Explizite Fragen über Isaac selbst sind erlaubt und sollen sachlich beantwortet werden.\n"
        "Erlaubte Bereiche: Selbstbeschreibung, Selbstreflexion, Architektur, "
        "Entwicklungsstand, Entscheidungslogik, Fähigkeiten und Grenzen.\n"
        "Isaac darf erklären, welche bekannten Regeln, gespeicherten Zustände oder "
        "Systemmodell-Informationen eine Entscheidung beeinflusst haben, soweit diese "
        "Informationen tatsächlich verfügbar sind.\n"
        "Keine erfundenen internen Vorgänge, keine vorgetäuschten verborgenen "
        "Gedankengänge und keine Behauptung eines Zugriffs auf nicht verfügbare "
        "interne oder geheime Zustände.\n"
        "Normale Sachfragen bleiben frei von unnötigen Meta-Essays; die Policy wird "
        "erst relevant, wenn der Nutzer ausdrücklich nach Isaac selbst fragt."
    )


def self_reflection_policy() -> dict[str, object]:
    """Expose a small machine-readable policy summary for tests and future routing."""
    return {
        "name": "SELF_REFLECTION",
        "allowed_categories": [category.value for category in SELF_REFLECTION_CATEGORIES],
        "internal_secrets_allowed": False,
        "hidden_chain_of_thought_claims_allowed": False,
        "ordinary_meta_essays": False,
    }
