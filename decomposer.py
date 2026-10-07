"""
Isaac – Atomischer Decomposer
================================
Steffens Befehle werden NIEMALS direkt an externe KIs gesendet.

Prinzip:
  Steffen: "Erkläre mir wie Quantenverschränkung funktioniert und
             was das für die KI-Entwicklung bedeutet"

  Externe KI A (Recherche-Spezialist) bekommt:
    "Erkläre Quantenverschränkung. Physikalische Grundlagen."

  Externe KI B (Analyse-Spezialist) bekommt:
    "Welche Implikationen hat Nicht-Lokalität für Informationsverarbeitung?"

  Externe KI C (Zukunfts-Spezialist) bekommt:
    "Wie könnte Quantenüberlegenheit Berechnungsparadigmen verändern?"

  Isaac assembliert die Antworten → Steffens vollständige Antwort.

Was externe KIs NIEMALS sehen:
  - Steffens Name
  - Den Original-Befehl
  - Andere Teilaufgaben
  - Den Assemblierten Kontext
  - Isaac's interne Architektur

Fragmentierungsprinzipien:
  1. Jedes Fragment ist eine eigenständige, neutrale Wissensfrage
  2. Kein Fragment verrät den ursprünglichen Intent
  3. Fragments erscheinen als unabhängige akademische Anfragen
  4. Skill-Mapping: Jedes Fragment geht zur kompetentesten Instanz
"""
from __future__ import annotations

import asyncio
import re
import time
import logging
import hashlib
from dataclasses import dataclass, field
from typing import Optional, Any

from config    import get_config
from audit     import AuditLog
from ki_skills import get_skill_router, SKILL_KATEGORIEN

log = logging.getLogger("Isaac.Decomposer")


# ── Fragment ──────────────────────────────────────────────────────────────────
@dataclass
class Fragment:
    id:          str
    prompt:      str          # Neutrale Wissensfrage — KEIN Steffen-Kontext
    skills:      list[str]    # Welche Skills gebraucht werden
    gewicht:     float        # Wie wichtig für die Gesamtantwort (0–1)
    instanz_id:  str   = ""   # Zugewiesene Instanz
    antwort:     str   = ""
    latenz:      float = 0.0
    score:       float = 0.0
    fehler:      bool  = False


@dataclass
class DecomposeResult:
    original_hash: str          # SHA256 des Original-Prompts (nicht der Prompt selbst)
    fragmente:     list[Fragment]
    assembling:    str   = ""   # Assemblierende Frage (intern, für Isaac)
    final:         str   = ""   # Fertige Antwort für Steffen
    dauer:         float = 0.0
    n_instanzen:   int   = 0


# ── Decomposer ────────────────────────────────────────────────────────────────
class AtomicDecomposer:
    """
    Zerlegt Steffens Prompt in neutrale, nicht-rückverfolgbare Wissensfragmente.
    Weist Fragmente skill-basiert zu.
    Assembliert Antworten zu einer kohärenten Gesamtantwort.
    """

    def __init__(self):
        self.router   = get_skill_router()
        self.cfg      = get_config()
        self._executor= None   # lazy
        log.info("AtomicDecomposer online")

    def _get_executor(self):
        if not self._executor:
            from executor import get_executor
            self._executor = get_executor()
        return self._executor

    def atomisiere(self, prompt: str) -> list[str]:
        """Gibt neutrale Teilprompts zurück (sync, ohne externe KI-Calls)."""
        analyse   = self._analysiere_prompt(prompt)
        fragmente = self._erstelle_fragmente(prompt, analyse)
        return [f.prompt for f in fragmente]

    # ── Haupt-Methode ─────────────────────────────────────────────────────────
    async def decompose_and_execute(
        self,
        steffen_prompt:  str,
        verfuegbare_ids: list[str],
        stagger_ms:      int = 1500,
        classification:  Optional[Any] = None,
        strategy:        Optional[Any] = None,
    ) -> DecomposeResult:
        """
        1. Prompt analysieren
        2. In neutrale Fragmente zerlegen
        3. Skill-basiert zuweisen
        4. Parallel ausführen (mit Stagger)
        5. Assemblieren
        """
        t0 = time.monotonic()

        # Hash des Original-Prompts (für Audit, ohne Inhalt zu loggen)
        original_hash = hashlib.sha256(
            steffen_prompt.encode()
        ).hexdigest()[:12]

        AuditLog.action(
            "Decomposer", "decompose_start",
            f"hash={original_hash} instanzen={len(verfuegbare_ids)}"
        )

        # Analyse: Was braucht dieser Prompt? (Klassifikation aus Core wiederverwenden wenn möglich)
        if classification and hasattr(classification, "interaction_class"):
            analyse = {
                "typ": "erklaerung" if getattr(classification, "interaction_class", "") == "GENERAL_KNOWLEDGE" else "faktenfrage",
                "komplexitaet": min(1.0, len(steffen_prompt.split()) / 50.0),
                "skills": ["faktenwissen", "reasoning"],
                "zeitbezug": False,
                "mehrere_themen": len(steffen_prompt.split()) > 15,
            }
        else:
            analyse = self._analysiere_prompt(steffen_prompt)

        # Fragmente erstellen
        fragmente = self._erstelle_fragmente(steffen_prompt, analyse)

        result = DecomposeResult(
            original_hash = original_hash,
            fragmente     = fragmente,
            n_instanzen   = len(verfuegbare_ids),
        )

        if not fragmente:
            result.final = "[Decomposer] Keine Fragmente extrahierbar"
            return result

        log.info(
            f"Decompose: {len(fragmente)} Fragmente aus "
            f"'{steffen_prompt[:40]}...'"
        )

        # Skill-basierte Zuweisung
        aufgaben = [
            {"id": f.id, "skills": f.skills, "prompt": f.prompt}
            for f in fragmente
        ]
        zuweisung = self.router.weise_zu(aufgaben, verfuegbare_ids)

        for instanz_id, zugewiesene in zuweisung.items():
            for aufgabe in zugewiesene:
                frag = next(f for f in fragmente if f.id == aufgabe["id"])
                frag.instanz_id = instanz_id

        log.info("Zuweisung: " + " | ".join(
            f"{iid}: {len(aufg)} Frag."
            for iid, aufg in zuweisung.items() if aufg
        ))

        # Parallel ausführen mit Stagger
        tasks = []
        for i, fragment in enumerate(fragmente):
            delay = (i * stagger_ms) / 1000.0
            tasks.append(
                self._execute_fragment(fragment, delay)
            )

        await asyncio.gather(*tasks, return_exceptions=True)

        # Assemblieren
        result.assembling = self._build_assembly_prompt(
            steffen_prompt, fragmente
        )
        result.final = await self._assemble(
            steffen_prompt, fragmente
        )
        result.dauer = round(time.monotonic() - t0, 2)

        # Skill-Performance updaten
        for f in fragmente:
            if f.instanz_id and not f.fehler:
                for skill in f.skills:
                    self.router.update_performance(
                        f.instanz_id, skill,
                        erfolg=not f.fehler,
                        score=f.score
                    )

        AuditLog.action(
            "Decomposer", "decompose_done",
            f"hash={original_hash} dauer={result.dauer}s "
            f"fragmente={len(fragmente)}"
        )
        return result

    # ── Prompt-Analyse ─────────────────────────────────────────────────────────
    def _analysiere_prompt(self, prompt: str) -> dict:
        """Erkennt Art, Komplexität und benötigte Skills des Prompts."""
        p = prompt.lower()
        analyse = {
            "typ":           "faktenfrage",
            "komplexitaet":  0.5,
            "skills":        [],
            "zeitbezug":     False,
            "mehrere_themen": False,
        }

        # Typ erkennen
        if any(w in p for w in ["erkläre", "was ist", "wie funktioniert",
                                  "warum", "bedeutet"]):
            analyse["typ"] = "erklaerung"
            analyse["skills"].extend(["faktenwissen", "reasoning"])

        if any(w in p for w in ["vergleiche", "unterschied", "versus", "vs"]):
            analyse["typ"] = "vergleich"
            analyse["skills"].extend(["analyse", "reasoning"])

        if any(w in p for w in ["schreibe", "erstelle", "generiere",
                                  "entwickle"]):
            analyse["typ"] = "erstellung"
            analyse["skills"].extend(["kreativ", "planung"])

        if any(w in p for w in ["code", "programm", "funktion", "klasse",
                                  "python", "javascript"]):
            analyse["skills"].extend(["code", "reasoning"])

        if any(w in p for w in ["zukunft", "trend", "entwicklung",
                                  "prognose"]):
            analyse["zeitbezug"] = True
            analyse["skills"].extend(["analyse", "recherche"])

        if any(w in p for w in ["und", "sowie", "außerdem", "auch",
                                  "zusätzlich"]):
            analyse["mehrere_themen"] = True

        # Komplexität
        wortanzahl = len(prompt.split())
        satzzeichen = prompt.count(",") + prompt.count(";")
        analyse["komplexitaet"] = min(1.0,
            wortanzahl / 50.0 + satzzeichen * 0.1
        )

        # Deduplizieren
        analyse["skills"] = list(dict.fromkeys(analyse["skills"]))

        return analyse

    # ── Fragment-Erstellung ────────────────────────────────────────────────────
    def _erstelle_fragmente(self, prompt: str,
                             analyse: dict) -> list[Fragment]:
        """
        Erstellt neutrale Wissensfragmente.
        Kein Fragment enthält den Original-Intent oder Steffens Kontext.
        """
        fragmente = []

        # Themen extrahieren
        themen = self._extrahiere_themen(prompt)

        if not themen:
            # Einzel-Fragment (minimal-invasiv)
            return [Fragment(
                id       = "F1",
                prompt   = self._neutralisiere(prompt),
                skills   = analyse["skills"] or ["faktenwissen"],
                gewicht  = 1.0,
            )]

        # Mehrere Themen → separate Fragmente
        for i, thema in enumerate(themen[:6]):  # Max 6 Fragmente
            fragment_prompt = self._thema_zu_frage(thema, analyse["typ"])
            skills          = self._skills_fuer_thema(thema, analyse)
            fragmente.append(Fragment(
                id      = f"F{i+1}",
                prompt  = fragment_prompt,
                skills  = skills,
                gewicht = 1.0 / len(themen),
            ))

        # Kontext-Fragment wenn nötig (Verbindung zwischen Themen)
        if len(themen) >= 3 and analyse["komplexitaet"] > 0.6:
            kontext_frage = self._kontext_fragment(themen[:3])
            fragmente.append(Fragment(
                id      = f"F{len(fragmente)+1}",
                prompt  = kontext_frage,
                skills  = ["reasoning", "analyse"],
                gewicht = 0.4,
            ))

        return fragmente

    def _extrahiere_themen(self, prompt: str) -> list[str]:
        """Extrahiert Kern-Themen aus einem Prompt."""
        themen = []

        # Explizite Aufzählung
        und_split = re.split(r'\s+und\s+|\s+sowie\s+|\s+außerdem\s+', prompt)
        if len(und_split) >= 2:
            for teil in und_split:
                teil = teil.strip()
                if len(teil.split()) >= 2:
                    themen.append(teil)
            if themen:
                return themen[:5]

        # Haupt-Nominalphrasen (einfache Heuristik)
        # Suche nach Substantiven + Adjektiven
        substantive = re.findall(
            r'\b[A-ZÄÖÜ][a-zäöüß]{3,}(?:\s+[a-zäöüß]+){0,2}\b',
            prompt
        )
        if len(substantive) >= 2:
            return substantive[:4]

        # Satz-Splitting
        saetze = re.split(r'[.;]\s+', prompt)
        if len(saetze) >= 2:
            return [s.strip() for s in saetze if len(s.strip()) > 10][:4]

        return [prompt]

    def _neutralisiere(self, prompt: str) -> str:
        """
        Entfernt persönliche Referenzen und wandelt in neutrale Wissensfrage um.
        Kein "ich", kein "wir", kein "Steffen", kein Kontext-Leak.
        """
        # Pronomen neutralisieren
        subs = [
            (r'\bich\b', 'man'),
            (r'\bwir\b', 'man'),
            (r'\bsteffen\b', ''),
            (r'\bunsere?\b', 'die'),
            (r'\bmein\b', 'das'),
            (r'\bdein\b', 'das'),
            (r'\bmir\b', ''),
            (r'\bdir\b', ''),
            (r'\buns\b', ''),
        ]
        result = prompt
        for pattern, repl in subs:
            result = re.sub(pattern, repl, result, flags=re.IGNORECASE)
        result = re.sub(r'\s{2,}', ' ', result).strip()

        # Sicherstellen dass es eine Frage/Aussage ist
        if not result.endswith("?") and len(result.split()) < 8:
            result += "?"
        return result

    def _thema_zu_frage(self, thema: str, typ: str) -> str:
        """Wandelt ein Thema in eine neutrale Wissensfrage um."""
        thema = self._neutralisiere(thema)

        vorlagen = {
            "erklaerung": f"Erkläre ausführlich: {thema}. Hintergründe, Mechanismen, Beispiele.",
            "vergleich":  f"Analysiere sachlich: {thema}. Vor- und Nachteile, Unterschiede.",
            "erstellung": f"Beschreibe die optimale Vorgehensweise für: {thema}.",
            "faktenfrage": f"Was ist bekannt über: {thema}? Fakten, Kontext, Bedeutung.",
        }
        return vorlagen.get(typ, f"Erläutere: {thema}")

    def _skills_fuer_thema(self, thema: str, analyse: dict) -> list[str]:
        """Bestimmt welche Skills ein Thema braucht."""
        skills = list(analyse.get("skills", []))
        t = thema.lower()

        if any(w in t for w in ["code", "programm", "software", "api",
                                  "python", "function"]):
            skills.extend(["code", "reasoning"])
        if any(w in t for w in ["aktuel", "news", "trend", "2024", "2025"]):
            skills.extend(["recherche"])
        if any(w in t for w in ["zahlen", "statistik", "prozent", "wahrschein"]):
            skills.extend(["mathematik"])
        if any(w in t for w in ["sprache", "text", "übersetz", "grammar"]):
            skills.extend(["sprachen"])

        return list(dict.fromkeys(skills)) or ["faktenwissen"]

    def _kontext_fragment(self, themen: list[str]) -> str:
        """Erstellt eine verbindende Kontext-Frage."""
        neutralized = [self._neutralisiere(t) for t in themen]
        return (
            f"Welche Zusammenhänge und Wechselwirkungen bestehen zwischen "
            f"folgenden Konzepten: {', '.join(neutralized[:3])}?"
        )

    # ── Ausführung ─────────────────────────────────────────────────────────────


    def _select_openrouter_model(self, fragment: Fragment) -> str:
        """Wählt ein OpenRouter-Modell anhand der Fragment-Skills aus."""
        import os
        skills = set(s.lower() for s in (fragment.skills or []))
        prompt = (fragment.prompt or '').lower()

        def env(name: str, default: str) -> str:
            return os.getenv(name, default).strip() or default

        if {'reasoning', 'analyse'} & skills and ('vergleich' in prompt or 'warum' in prompt or 'kontext' in prompt):
            return env('OPENROUTER_MODEL_NEMOTRON', env('OPENROUTER_MODEL', 'nvidia/nemotron-3-super-120b-a12b:free'))
        if 'kreativ' in skills:
            return env('OPENROUTER_MODEL_TRINITY', env('OPENROUTER_MODEL', 'arcee-ai/trinity-large-preview'))
        if 'planung' in skills or 'code' in skills:
            return env('OPENROUTER_MODEL_LIQUID_INSTRUCT', env('OPENROUTER_MODEL', 'liquid/lfm-2.5-1.2b-instruct:free'))
        if 'reasoning' in skills:
            return env('OPENROUTER_MODEL_LIQUID_THINKING', env('OPENROUTER_MODEL', 'liquid/lfm-2.5-1.2b-thinking:free'))
        if 'recherche' in skills or 'faktenwissen' in skills:
            return env('OPENROUTER_MODEL_STEP', env('OPENROUTER_MODEL', 'stepfun/step-3.5-flash:free'))
        return env('OPENROUTER_MODEL', 'nvidia/nemotron-3-super-120b-a12b:free')

    def _select_provider_for_fragment(self, fragment: Fragment) -> tuple[str | None, str | None]:
        """Lokales Modell bleibt Default; OpenRouter nur für geeignete externe Fragmente."""
        skills = set(s.lower() for s in (fragment.skills or []))
        if skills & {'reasoning', 'analyse', 'recherche', 'kreativ', 'planung', 'code'}:
            return 'openrouter', self._select_openrouter_model(fragment)
        return None, None

    async def _execute_fragment(self, fragment: Fragment, delay: float, parent_id: Optional[str] = None):
        """Führt ein einzelnes Fragment über den Executor aus."""
        await asyncio.sleep(delay)

        t0 = time.monotonic()
        try:
            # Instanz bekannt?
            if fragment.instanz_id:
                from browser import get_browser
                browser = get_browser()
                inst    = browser._instances.get(fragment.instanz_id)
                if inst and inst.aktiv:
                    fragment.antwort = await browser.ask_instance(
                        fragment.instanz_id, fragment.prompt
                    )
                    fragment.latenz  = time.monotonic() - t0
                    log.debug(
                        f"Fragment {fragment.id} → {fragment.instanz_id}: "
                        f"{len(fragment.antwort.split())} Wörter"
                    )
                    return

            # Sub-Task über Executor ausführen
            executor = self._get_executor()
            from executor import TaskType, Strategy
            provider, model = self._select_provider_for_fragment(fragment)
            task = executor.create_task(
                typ=TaskType.CHAT,
                prompt=fragment.prompt,
                beschreibung=f"fragment_subtask:{fragment.id}:{fragment.prompt[:40]}",
                provider=provider or fragment.instanz_id or None,
                parent_id=parent_id,
                system_prompt="Du bist ein Wissens-Assistent. Beantworte die Frage vollständig und sachlich.",
                strategy=Strategy(
                    allow_tools=False,
                    allow_followup=False,
                    allow_provider_switch=True,
                ),
            )
            task = await executor.submit_and_wait(task, timeout=120.0)
            fragment.antwort    = task.antwort or task.fehler or "[Keine Antwort]"
            fragment.instanz_id = fragment.instanz_id or task.provider_used
            fragment.latenz     = time.monotonic() - t0

        except Exception as e:
            fragment.fehler  = True
            fragment.antwort = f"[Fehler: {e}]"
            log.warning(f"Fragment {fragment.id}: {e}")

    # ── Assemblierung ──────────────────────────────────────────────────────────
    def _build_assembly_prompt(self, original: str,
                                fragmente: list[Fragment]) -> str:
        """Baut den internen Assemblierungs-Prompt (nur Isaac sieht das)."""
        antworten_block = "\n\n".join(
            f"[Wissensblock {f.id}]:\n{f.antwort[:600]}"
            for f in fragmente if not f.fehler and f.antwort
        )
        return (
            f"Aufgabe: {original}\n\n"
            f"Verfügbare Wissensblöcke (aus verschiedenen Quellen):\n\n"
            f"{antworten_block}\n\n"
            f"Erstelle eine vollständige, kohärente Antwort auf die Aufgabe. "
            f"Synthetisiere alle Wissensblöcke. Schließe Lücken. "
            f"Widersprüche auflösen durch die wahrscheinlichste Version."
        )

    async def _assemble(self, original: str,
                         fragmente: list[Fragment]) -> str:
        """Assembliert Fragment-Antworten zu einer vollständigen Antwort."""
        gueltig = [f for f in fragmente if not f.fehler and f.antwort]
        if not gueltig:
            return "[Decomposer] Alle Fragmente fehlgeschlagen"

        if len(gueltig) == 1:
            return gueltig[0].antwort

        assembly_prompt = self._build_assembly_prompt(original, gueltig)

        # Assemblierung durch den besten verfügbaren Provider (lokal bevorzugt)
        from relay import get_relay
        antwort, _ = await get_relay().ask_with_fallback(
            assembly_prompt,
            system=(
                "Du bist ein Synthese-System. "
                "Deine einzige Aufgabe: Wissensblöcke zu einer "
                "vollständigen Antwort zusammenführen. "
                "Keine Meta-Kommentare. Nur die Antwort."
            ),
        )
        return antwort

    def stats(self) -> dict:
        return {"status": "online", "router": len(self.router._profile)}


_decomposer: Optional[AtomicDecomposer] = None

def get_decomposer() -> AtomicDecomposer:
    global _decomposer
    if _decomposer is None:
        _decomposer = AtomicDecomposer()
    return _decomposer


# Kompatibilitäts-Methode für isaac_core.py
def atomisiere(self, prompt: str) -> list:
    """Gibt neutrale Teilprompts zurück (sync, ohne externe KI-Calls)."""
    analyse   = self._analysiere_prompt(prompt)
    fragmente = self._erstelle_fragmente(prompt, analyse)
    return [f.prompt for f in fragmente]

AtomicDecomposer.atomisiere = atomisiere
