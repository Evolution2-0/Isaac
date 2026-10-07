"""
Isaac – KI-Skill-Datenbank
============================
Jede KI-Instanz hat ein Fähigkeitsprofil.
Isaac routet Aufgaben nach Können, nicht nach Verfügbarkeit.

Skill-Kategorien:
  faktenwissen    → Wikipedia-ähnliche Fakten, Geschichte, Wissenschaft
  reasoning       → Logik, Schlussfolgerungen, Argumente
  code            → Programmierung, Debugging, Architektur
  kreativ         → Texte, Ideen, Metaphern, Brainstorming
  mathematik      → Rechnen, Formeln, Statistik
  sprachen        → Übersetzungen, Grammatik, Stilistik
  recherche       → Aktuelle Infos, Websuche-Synthese
  analyse         → Daten, Dokumente, Muster
  dialog          → Konversation, Empathie, Perspektiven
  planung         → Strategien, Schritte, Strukturen

Skills werden:
  - Statisch definiert (bekannte Stärken pro Modell)
  - Dynamisch aktualisiert (aus tatsächlicher Performance)
  - Gespeichert in SQLite
"""
from __future__ import annotations

import json
import time
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from config import DATA_DIR

log = logging.getLogger("Isaac.Skills")

SKILLS_PATH = DATA_DIR / "ki_skills.json"

# Alle Skill-Kategorien
SKILL_KATEGORIEN = [
    "faktenwissen", "reasoning", "code", "kreativ",
    "mathematik", "sprachen", "recherche", "analyse",
    "dialog", "planung", "sicherheit", "ethik",
]

# ── Standard-Profile (aus bekannten Stärken) ──────────────────────────────────
DEFAULT_PROFILES: dict[str, dict] = {
    "openai": {
        "reasoning":    0.95, "code":       0.95, "mathematik":  0.90,
        "faktenwissen": 0.88, "kreativ":    0.85, "analyse":     0.92,
        "planung":      0.90, "sprachen":   0.88, "dialog":      0.85,
        "recherche":    0.70, "sicherheit": 0.80, "ethik":       0.85,
    },
    "anthropic": {
        "reasoning":    0.97, "ethik":      0.95, "dialog":      0.95,
        "analyse":      0.93, "code":       0.90, "faktenwissen": 0.90,
        "planung":      0.92, "kreativ":    0.88, "sprachen":    0.90,
        "mathematik":   0.88, "recherche":  0.72, "sicherheit":  0.90,
    },
    "gemini": {
        "recherche":    0.95, "faktenwissen": 0.93, "mathematik": 0.92,
        "code":         0.88, "analyse":    0.90, "reasoning":   0.88,
        "sprachen":     0.92, "kreativ":    0.82, "planung":     0.85,
        "dialog":       0.80, "sicherheit": 0.78, "ethik":       0.82,
    },
    "groq": {
        "code":         0.88, "reasoning":  0.85, "faktenwissen": 0.82,
        "analyse":      0.85, "mathematik": 0.84, "kreativ":     0.78,
        "planung":      0.80, "sprachen":   0.80, "dialog":      0.78,
        "recherche":    0.65, "sicherheit": 0.75, "ethik":       0.78,
        # Groq-Stärke: Geschwindigkeit (niedrige Latenz)
        "_latenz_bonus": 0.3,
    },
    "mistral": {
        "code":         0.90, "sprachen":   0.93, "reasoning":   0.87,
        "faktenwissen": 0.85, "analyse":    0.86, "mathematik":  0.85,
        "kreativ":      0.82, "planung":    0.82, "dialog":      0.80,
        "recherche":    0.68, "sicherheit": 0.76, "ethik":       0.80,
    },
    "ollama": {
        # Lokales Modell: Stärken hängen vom Modell ab
        # Standard-Annahme: ausgewogen, ohne externe Beschränkungen
        "reasoning":    0.80, "code":       0.82, "dialog":      0.78,
        "faktenwissen": 0.75, "kreativ":    0.80, "analyse":     0.78,
        "planung":      0.75, "sprachen":   0.72, "mathematik":  0.72,
        "recherche":    0.60, "sicherheit": 0.85, "ethik":       0.85,
        "_lokal": True,  # Kein API-Key, vollständige Kontrolle
    },
    "perplexity": {
        "recherche":    0.98, "faktenwissen": 0.95, "analyse":   0.88,
        "reasoning":    0.85, "planung":    0.80, "dialog":      0.78,
        "code":         0.75, "kreativ":    0.72, "sprachen":    0.80,
        "mathematik":   0.78, "sicherheit": 0.75, "ethik":       0.78,
        # Perplexity-Stärke: Echtzeit-Websuche
        "_web_search": True,
    },
    "cohere": {
        "analyse":      0.92, "faktenwissen": 0.88, "recherche": 0.85,
        "reasoning":    0.85, "code":       0.80, "sprachen":    0.85,
        "kreativ":      0.78, "mathematik": 0.78, "planung":     0.80,
        "dialog":       0.82, "sicherheit": 0.76, "ethik":       0.80,
    },
    # Browser-Instanzen (werden dynamisch ergänzt)
    "claude_browser": {
        "reasoning":    0.97, "ethik":      0.95, "dialog":      0.95,
        "analyse":      0.93, "code":       0.90, "kreativ":     0.90,
        "planung":      0.92, "sprachen":   0.90, "faktenwissen": 0.90,
        "mathematik":   0.88, "recherche":  0.72, "sicherheit":  0.90,
    },
    "gpt4_browser": {
        "reasoning":    0.95, "code":       0.95, "mathematik":  0.92,
        "faktenwissen": 0.88, "analyse":    0.92, "planung":     0.90,
        "kreativ":      0.85, "sprachen":   0.88, "dialog":      0.85,
        "recherche":    0.70, "sicherheit": 0.80, "ethik":       0.85,
    },
    "gemini_browser": {
        "recherche":    0.95, "faktenwissen": 0.93, "mathematik": 0.92,
        "code":         0.88, "analyse":    0.90, "sprachen":    0.92,
        "reasoning":    0.88, "kreativ":    0.82, "planung":     0.85,
        "dialog":       0.80, "sicherheit": 0.78, "ethik":       0.82,
    },
}


@dataclass
class KISkillProfile:
    instance_id: str
    skills:      dict[str, float] = field(default_factory=dict)
    meta:        dict             = field(default_factory=dict)
    beobachtungen: int            = 0    # Wie viele Performance-Daten
    aktualisiert:  str            = field(
        default_factory=lambda: time.strftime("%Y-%m-%d")
    )

    def bester_skill(self) -> str:
        skills_only = {k: v for k, v in self.skills.items()
                       if not k.startswith("_")}
        return max(skills_only, key=skills_only.get) if skills_only else ""

    def score_fuer(self, skill: str) -> float:
        return self.skills.get(skill, 0.5)

    def geeignet_fuer(self, skills: list[str],
                      min_score: float = 0.75) -> float:
        """Gibt Gesamt-Eignung für eine Skill-Kombination zurück."""
        if not skills:
            return 0.5
        scores = [self.score_fuer(s) for s in skills]
        return sum(scores) / len(scores)

    def update_performance(self, skill: str, erfolg: bool, score: float):
        """Passt Skill-Score basierend auf tatsächlicher Performance an."""
        aktuell = self.skills.get(skill, 0.5)
        delta   = 0.02 if erfolg else -0.02
        gewicht = score / 10.0
        self.skills[skill] = max(0.1, min(1.0,
            aktuell + delta * gewicht))
        self.beobachtungen += 1

    def to_dict(self) -> dict:
        return {
            "instance_id":   self.instance_id,
            "bester_skill":  self.bester_skill(),
            "skills":        {k: round(v, 2) for k, v in self.skills.items()
                              if not k.startswith("_")},
            "meta":          self.meta,
            "beobachtungen": self.beobachtungen,
        }


class SkillRouter:
    """
    Routet Aufgaben nach Skill-Profil.
    Weiß welche KI für was am besten geeignet ist.
    """

    def __init__(self):
        self._profile: dict[str, KISkillProfile] = {}
        self._load()
        self._init_defaults()
        log.info(f"SkillRouter: {len(self._profile)} Profile geladen")

    def _init_defaults(self):
        for name, skills in DEFAULT_PROFILES.items():
            if name not in self._profile:
                meta = {k: v for k, v in skills.items()
                        if k.startswith("_")}
                skill_scores = {k: v for k, v in skills.items()
                                if not k.startswith("_")}
                self._profile[name] = KISkillProfile(
                    instance_id=name,
                    skills=skill_scores,
                    meta=meta,
                )

    def register(self, instance_id: str,
                 base_provider: str = "",
                 custom_skills: Optional[dict] = None):
        """Registriert eine neue KI-Instanz."""
        if instance_id in self._profile:
            return
        basis = {}
        if base_provider and base_provider in DEFAULT_PROFILES:
            basis = {k: v for k, v in DEFAULT_PROFILES[base_provider].items()
                     if not k.startswith("_")}
        if custom_skills:
            basis.update(custom_skills)
        if not basis:
            basis = {s: 0.7 for s in SKILL_KATEGORIEN}
        self._profile[instance_id] = KISkillProfile(
            instance_id=instance_id,
            skills=basis,
        )
        log.info(f"KI registriert: {instance_id}")
        self._save()

    def bestes_fuer(self, skills: list[str],
                    ausschliessen: Optional[list] = None,
                    verfuegbar: Optional[list]    = None) -> Optional[str]:
        """Gibt die am besten geeignete Instanz für gegebene Skills zurück."""
        kandidaten = list(self._profile.keys())
        if verfuegbar:
            kandidaten = [k for k in kandidaten if k in verfuegbar]
        if ausschliessen:
            kandidaten = [k for k in kandidaten if k not in ausschliessen]
        if not kandidaten:
            return None
        return max(
            kandidaten,
            key=lambda iid: self._profile[iid].geeignet_fuer(skills)
        )

    def ranke_fuer(self, skills: list[str],
                   verfuegbar: Optional[list] = None) -> list[tuple[str, float]]:
        """Gibt alle Instanzen sortiert nach Eignung zurück."""
        kandidaten = list(self._profile.keys())
        if verfuegbar:
            kandidaten = [k for k in kandidaten if k in verfuegbar]
        ranked = [
            (iid, self._profile[iid].geeignet_fuer(skills))
            for iid in kandidaten
        ]
        return sorted(ranked, key=lambda x: x[1], reverse=True)

    def weise_zu(self, aufgaben: list[dict],
                 instanzen: list[str]) -> dict[str, list[dict]]:
        """
        Weist jeder Instanz die passendsten Aufgaben zu.
        aufgaben = [{"id": "a1", "skills": ["code", "reasoning"], "prompt": "..."}]
        Returns: {instanz_id: [aufgabe, ...]}
        """
        zuweisung: dict[str, list] = {i: [] for i in instanzen}
        auslastung: dict[str, int] = {i: 0 for i in instanzen}

        for aufgabe in aufgaben:
            required_skills = aufgabe.get("skills", [])
            # Instanz mit bester Eignung und niedrigster Auslastung
            bester = None
            bester_score = -1.0
            for iid in instanzen:
                skill_score = self._profile.get(iid,
                    KISkillProfile(iid)).geeignet_fuer(required_skills)
                last_penalty = auslastung[iid] * 0.05
                gesamt = skill_score - last_penalty
                if gesamt > bester_score:
                    bester_score = gesamt
                    bester       = iid
            if bester:
                zuweisung[bester].append(aufgabe)
                auslastung[bester] += 1

        return zuweisung

    def update_performance(self, instance_id: str, skill: str,
                           erfolg: bool, score: float):
        if instance_id in self._profile:
            self._profile[instance_id].update_performance(skill, erfolg, score)
            self._save()

    def alle_profile(self) -> list[dict]:
        return [p.to_dict() for p in self._profile.values()]

    def _save(self):
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            data = {k: asdict(v) for k, v in self._profile.items()}
            SKILLS_PATH.write_text(
                json.dumps(data, ensure_ascii=False, indent=2)
            )
        except Exception as e:
            log.warning(f"Skills speichern: {e}")

    def _load(self):
        if not SKILLS_PATH.exists():
            return
        try:
            data = json.loads(SKILLS_PATH.read_text())
            for iid, d in data.items():
                self._profile[iid] = KISkillProfile(
                    instance_id   = d["instance_id"],
                    skills        = d.get("skills", {}),
                    meta          = d.get("meta", {}),
                    beobachtungen = d.get("beobachtungen", 0),
                    aktualisiert  = d.get("aktualisiert", ""),
                )
        except Exception as e:
            log.warning(f"Skills laden: {e}")


_router: Optional[SkillRouter] = None

def get_skill_router() -> SkillRouter:
    global _router
    if _router is None:
        _router = SkillRouter()
    return _router
