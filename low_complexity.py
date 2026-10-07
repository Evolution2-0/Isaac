from __future__ import annotations
import re
from dataclasses import dataclass


class InteractionClass:
    SOCIAL_GREETING = "SOCIAL_GREETING"
    SOCIAL_ACKNOWLEDGMENT = "SOCIAL_ACKNOWLEDGMENT"
    SHORT_CLARIFICATION = "SHORT_CLARIFICATION"
    STATUS_QUERY = "STATUS_QUERY"
    TOOL_REQUEST = "TOOL_REQUEST"
    AMBIGUOUS_SHORT = "AMBIGUOUS_SHORT"
    NORMAL_CHAT = "NORMAL_CHAT"


@dataclass(frozen=True)
class ClassificationResult:
    interaction_class: str
    normalized_text: str
    has_question: bool
    word_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "interaction_class": self.interaction_class,
            "normalized_text": self.normalized_text,
            "has_question": self.has_question,
            "word_count": self.word_count,
        }


_GREETING_MARKERS = {
    "hallo", "hi", "hey", "servus", "moin", "guten morgen", "guten tag", "guten abend"
}
_ACK_MARKERS = {
    "danke", "dankeschön", "danke schön", "thx", "thanks", "ok danke", "okidoki danke"
}
_CLARIFY_MARKERS = {
    "ist nur eine begrüßung", "war nur ein test", "nur kurz hallo", "ich wollte nur testen ob du antwortest"
}
_STATUS_MARKERS = {"status", "info", "hilfe"}
_TOOL_PREFIXES = (
    "suche:", "suche ", "search:", "search ",
    "recherche:", "recherche ", "recherchiere:", "recherchiere ",
    "browser:", "browser ", "agent:", "agent ", "finde:",
)
_BROWSER_PREFIXES = (
    "browser:",
    "browser auf",
    "öffne im browser",
    "navigiere zu",
)
_TOOL_MARKERS = ("internet", "web", "browser", "wetter", "github", "api", "tool", "mcp", "suche", "search", "recherche")
_PROVIDER_SETUP_MARKERS = (
    "api key erstellen",
    "api key holen",
    "api keys einrichten",
    "provider verbinden",
    "provider connecten",
    "groq einrichten",
    "groq verbinden",
    "openrouter token",
    "verbinde groq",
    "connect groq",
    "fehlende keys",
    "alle api keys",
    "alle provider verbinden",
    "keys selbst beschaffen",
    "api keys selbst",
)
_TOOL_ACTION_WORDS = {"suche", "search", "recherchiere"}
_EXPLANATORY_PREFIXES = (
    "erkläre ",
    "erklär ",
    "erklaere ",
    "erklaer ",
    "was bedeutet ",
    "was ist ",
)
_EXPLANATORY_CONTEXT_MARKERS = (
    " als ",
    "architektur",
    "grundlage",
    "grundlagen",
    "konzept",
    "konzeptionell",
    "literatur",
    "motiv",
    "prinzip",
)
_ACTION_SHORT_MARKERS = ("mach", "weiter", "fortsetzen", "hilfe", "erklär", "erklär", "wer", "was", "wie", "warum")

# Bolt performance optimization: pre-compile non-word char regex and use
# ' '.join(t.split()) for whitespace normalization (~50% faster string normalization).
_STRIP_NON_WORD_RE = re.compile(r"[^\wäöüß\s]")


def normalize_low_complexity(text: str) -> str:
    if not text:
        return ""
    t = text.strip().lower()
    t = _STRIP_NON_WORD_RE.sub("", t)
    return " ".join(t.split())


def classify_interaction(text: str) -> str:
    return classify_interaction_result(text).interaction_class


def _has_explicit_tool_action(tokens: list[str]) -> bool:
    return any(token in _TOOL_ACTION_WORDS for token in tokens)


def _looks_like_explanatory_chat(normalized: str) -> bool:
    padded = f" {normalized} "
    return (
        normalized.startswith(_EXPLANATORY_PREFIXES) or
        any(marker in padded for marker in _EXPLANATORY_CONTEXT_MARKERS)
    )


def classify_interaction_result(text: str) -> ClassificationResult:
    normalized = normalize_low_complexity(text)
    if not normalized:
        return ClassificationResult(
            interaction_class=InteractionClass.AMBIGUOUS_SHORT,
            normalized_text=normalized,
            has_question="?" in (text or ""),
            word_count=0,
        )

    tokens = normalized.split()
    normalized_wo_name = " ".join(t for t in tokens if t != "isaac").strip()
    if normalized_wo_name:
        normalized = normalized_wo_name
        tokens = normalized.split()
    word_count = len(tokens)
    has_question = "?" in (text or "")

    if normalized in _STATUS_MARKERS:
        return ClassificationResult(
            interaction_class=InteractionClass.STATUS_QUERY,
            normalized_text=normalized,
            has_question=has_question,
            word_count=word_count,
        )

    if normalized.startswith(_TOOL_PREFIXES):
        return ClassificationResult(
            interaction_class=InteractionClass.TOOL_REQUEST,
            normalized_text=normalized,
            has_question=has_question,
            word_count=word_count,
        )
    if any(normalized.startswith(prefix) for prefix in _BROWSER_PREFIXES):
        return ClassificationResult(
            interaction_class=InteractionClass.TOOL_REQUEST,
            normalized_text=normalized,
            has_question=has_question,
            word_count=word_count,
        )
    if any(marker in normalized for marker in _PROVIDER_SETUP_MARKERS):
        return ClassificationResult(
            interaction_class=InteractionClass.TOOL_REQUEST,
            normalized_text=normalized,
            has_question=has_question,
            word_count=word_count,
        )
    if any(marker in normalized for marker in _TOOL_MARKERS) and word_count >= 2:
        explanatory_chat = (
            _looks_like_explanatory_chat(normalized)
            and not _has_explicit_tool_action(tokens)
        )
        if not explanatory_chat and (":" in (text or "") or word_count >= 3):
            return ClassificationResult(
                interaction_class=InteractionClass.TOOL_REQUEST,
                normalized_text=normalized,
                has_question=has_question,
                word_count=word_count,
            )

    if normalized in _CLARIFY_MARKERS or (
        word_count <= 6 and
        any(x in normalized for x in ("nur", "test", "begrüßung", "begruessung"))
    ):
        return ClassificationResult(
            interaction_class=InteractionClass.SHORT_CLARIFICATION,
            normalized_text=normalized,
            has_question=has_question,
            word_count=word_count,
        )

    if normalized in _ACK_MARKERS:
        return ClassificationResult(
            interaction_class=InteractionClass.SOCIAL_ACKNOWLEDGMENT,
            normalized_text=normalized,
            has_question=has_question,
            word_count=word_count,
        )

    if normalized in _GREETING_MARKERS:
        return ClassificationResult(
            interaction_class=InteractionClass.SOCIAL_GREETING,
            normalized_text=normalized,
            has_question=has_question,
            word_count=word_count,
        )
    if word_count <= 3 and any(g in normalized for g in ("hallo", "hi", "hey", "moin")):
        return ClassificationResult(
            interaction_class=InteractionClass.SOCIAL_GREETING,
            normalized_text=normalized,
            has_question=has_question,
            word_count=word_count,
        )

    if word_count <= 2 and not has_question and not any(m in normalized for m in _ACTION_SHORT_MARKERS):
        return ClassificationResult(
            interaction_class=InteractionClass.AMBIGUOUS_SHORT,
            normalized_text=normalized,
            has_question=has_question,
            word_count=word_count,
        )

    return ClassificationResult(
        interaction_class=InteractionClass.NORMAL_CHAT,
        normalized_text=normalized,
        has_question=has_question,
        word_count=word_count,
    )


def is_lightweight_local_class(interaction_class: str) -> bool:
    return interaction_class in {
        InteractionClass.SOCIAL_GREETING,
        InteractionClass.SOCIAL_ACKNOWLEDGMENT,
        InteractionClass.SHORT_CLARIFICATION,
    }


def is_low_complexity_local_input(text: str) -> bool:
    return is_lightweight_local_class(classify_interaction(text))


def local_class_response(interaction_class: str, text: str = "") -> str:
    if interaction_class == InteractionClass.SOCIAL_GREETING:
        normalized = normalize_low_complexity(text)
        if "guten morgen" in normalized:
            return "Guten Morgen. Ich bin da."
        return "Hallo. Ich bin da."
    if interaction_class == InteractionClass.SOCIAL_ACKNOWLEDGMENT:
        return "Gern. Ich bin da."
    if interaction_class == InteractionClass.SHORT_CLARIFICATION:
        return "Alles gut. Verstanden."
    if interaction_class == InteractionClass.AMBIGUOUS_SHORT:
        return "Ich bin da."
    return "Ich bin da."


def local_fast_response(text: str) -> str:
    return local_class_response(classify_interaction(text), text)
