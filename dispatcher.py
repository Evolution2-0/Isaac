"""
Isaac – Multi-KI Dispatcher
==============================
Verteilt komplexe Befehle auf mehrere KI-Modelle gleichzeitig.

Drei Betriebsmodi:

1. BROADCAST
   Gleicher Prompt → alle Instanzen → Antworten cross-validiert.
   Gut für: Fakten, Meinungen, kreative Aufgaben.

2. SPLIT
   Komplexer Befehl → Zerlegung in Sub-Tasks → Jeder Sub-Task
   an eine andere Instanz → Aggregation.
   Gut für: Lange Recherchen, Übersetzungen, Analysen.

3. PIPELINE
   Antwort von Instanz A → als Input an Instanz B (Verbesserung).
   Gut für: Qualitätsverbesserung durch iterative Kritik.

Stagger-Mechanismus:
   Anfragen gehen NICHT gleichzeitig raus — jede mit N ms Versatz.
   Verhindert Rate-Limit-Sperrungen bei gleicher Base-URL.

Cross-Validation:
   Nach Broadcast → Ergebnisse werden von einem "Judge"-Modell
   bewertet → Bestes Ergebnis oder synthetisierte Zusammenfassung.
"""
from __future__ import annotations

import asyncio
import time
import logging
import json
from dataclasses import dataclass, field
from typing import Optional, Callable, Any

from config  import get_config
from audit   import AuditLog
from logic   import get_logic

log = logging.getLogger("Isaac.Dispatcher")


# ── Dispatch-Ergebnis ─────────────────────────────────────────────────────────
@dataclass
class InstanceResult:
    instance_id: str
    antwort:     str
    latenz:      float
    score:       float   = 0.0
    fehler:      bool    = False


@dataclass
class DispatchResult:
    modus:       str
    prompt:      str
    ergebnisse:  list[InstanceResult] = field(default_factory=list)
    final:       str    = ""   # Finale Antwort nach Cross-Validation
    judge_id:    str    = ""   # Welche Instanz als Judge
    dauer:       float  = 0.0
    n_instanzen: int    = 0

    def bestes(self) -> Optional[InstanceResult]:
        gueltig = [r for r in self.ergebnisse if not r.fehler and r.antwort]
        if not gueltig:
            return None
        return max(gueltig, key=lambda r: r.score)

    def alle_antworten(self) -> str:
        teile = []
        for r in self.ergebnisse:
            if not r.fehler:
                teile.append(
                    f"[{r.instance_id}] ({r.latenz:.1f}s, Score:{r.score:.1f}):\n"
                    f"{r.antwort}"
                )
        return "\n\n---\n\n".join(teile)


# ── Dispatcher ────────────────────────────────────────────────────────────────
class MultiKIDispatcher:
    """
    Koordiniert Anfragen an mehrere KI-Instanzen.
    Nutzt sowohl Browser-Instanzen (Playwright) als auch API-Provider.
    """

    # Standard-Stagger zwischen Anfragen (Millisekunden)
    DEFAULT_STAGGER_MS = 1500
    # Max gleichzeitige Requests (verhindert CPU-Throttling auf Smartphone)
    MAX_CONCURRENT     = int(__import__('os').getenv("DISPATCHER_MAX_CONCURRENT", "3"))

    def __init__(self):
        self.logic    = get_logic()
        self.cfg      = get_config()
        self._relay   = None    # lazy
        self._browser = None    # lazy
        self._executor= None    # lazy
        log.info("MultiKIDispatcher online")

    def _get_relay(self):
        if not self._relay:
            from relay import get_relay
            self._relay = get_relay()
        return self._relay

    def _get_browser(self):
        if not self._browser:
            from browser import get_browser
            self._browser = get_browser()
        return self._browser

    def _get_executor(self):
        if not self._executor:
            from executor import get_executor
            self._executor = get_executor()
        return self._executor

    # ── Modus-Erkennung ───────────────────────────────────────────────────────
    def bestimme_modus(self, prompt: str,
                       n_instanzen: int,
                       force_modus: Optional[str] = None) -> str:
        if force_modus:
            return force_modus
        themen = self.logic.extract_topics(prompt)
        wortanzahl = len(prompt.split())

        # Langer / komplexer Prompt → SPLIT
        if wortanzahl > 80 or "aufzählung_erwartet" in themen:
            return "split"
        # Mehrere Instanzen verfügbar und Faktenfrage → BROADCAST
        if n_instanzen >= 2:
            return "broadcast"
        return "single"

    # ── BROADCAST: Gleicher Prompt an alle ────────────────────────────────────
    async def broadcast(self, prompt: str,
                        instance_ids: list[str],
                        stagger_ms:   int           = DEFAULT_STAGGER_MS,
                        cross_validate: bool        = True,
                        judge_instance: Optional[str] = None,
                        system:        str          = "") -> DispatchResult:
        """
        Schickt denselben Prompt an alle Instanzen mit Stagger.
        Optionale Cross-Validation durch ein Judge-Modell.
        """
        t0     = time.monotonic()
        result = DispatchResult(modus="broadcast", prompt=prompt,
                                n_instanzen=len(instance_ids))

        log.info(
            f"Broadcast → {len(instance_ids)} Instanzen "
            f"(Stagger: {stagger_ms}ms)"
        )
        AuditLog.action("Dispatcher", "broadcast",
                        f"n={len(instance_ids)} stagger={stagger_ms}ms")

        # Gestaggerte Anfragen mit Semaphore (Thermal Throttling verhindern)
        sem   = asyncio.Semaphore(self.MAX_CONCURRENT)
        tasks = []
        for i, iid in enumerate(instance_ids):
            delay = (i * stagger_ms) / 1000.0
            tasks.append(
                self._ask_with_sem(sem, iid, prompt, system, delay)
            )

        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        for iid, raw in zip(instance_ids, raw_results):
            if isinstance(raw, Exception):
                result.ergebnisse.append(InstanceResult(
                    instance_id=iid, antwort=f"[Fehler: {raw}]",
                    latenz=0.0, fehler=True
                ))
            else:
                antwort, latenz = raw
                score = self.logic.evaluate(antwort, prompt).total
                result.ergebnisse.append(InstanceResult(
                    instance_id=iid, antwort=antwort,
                    latenz=latenz, score=score
                ))
                log.debug(f"  [{iid}] Score:{score:.1f} ({latenz:.1f}s)")

        # Cross-Validation
        if cross_validate and len(result.ergebnisse) >= 2:
            result.final, result.judge_id = await self._cross_validate(
                prompt, result.ergebnisse, judge_instance
            )
        else:
            bestes = result.bestes()
            result.final = bestes.antwort if bestes else "[Keine Antwort]"

        result.dauer = time.monotonic() - t0
        AuditLog.action("Dispatcher", "broadcast_done",
                        f"dauer={result.dauer:.1f}s final_len={len(result.final)}")
        return result

    # ── SPLIT: Aufgabe zerlegen und verteilen ─────────────────────────────────
    async def split(self, prompt: str,
                    instance_ids: list[str],
                    stagger_ms:   int = DEFAULT_STAGGER_MS,
                    system:       str = "") -> DispatchResult:
        """
        Zerlegt einen komplexen Prompt in Sub-Tasks.
        Jeder Sub-Task geht an eine andere Instanz.
        Ergebnisse werden aggregiert.
        """
        t0     = time.monotonic()
        result = DispatchResult(modus="split", prompt=prompt,
                                n_instanzen=len(instance_ids))

        # Zerlegung
        sub_prompts = self._zerlege_prompt(prompt, len(instance_ids))
        log.info(f"Split → {len(sub_prompts)} Sub-Tasks auf "
                 f"{len(instance_ids)} Instanzen")

        tasks = []
        pairs = list(zip(instance_ids, sub_prompts))
        for i, (iid, sub) in enumerate(pairs):
            delay = (i * stagger_ms) / 1000.0
            tasks.append(
                self._ask_instance_delayed(iid, sub, system, delay)
            )

        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        antworten = []
        for (iid, sub), raw in zip(pairs, raw_results):
            if isinstance(raw, Exception):
                result.ergebnisse.append(InstanceResult(
                    instance_id=iid, antwort=f"[Fehler]",
                    latenz=0.0, fehler=True
                ))
            else:
                antwort, latenz = raw
                score = self.logic.evaluate(antwort, sub).total
                result.ergebnisse.append(InstanceResult(
                    instance_id=iid, antwort=antwort,
                    latenz=latenz, score=score
                ))
                antworten.append(antwort)

        # Aggregation: Alle Teil-Antworten zusammenführen
        result.final = self._aggregiere(antworten, prompt)
        result.dauer = time.monotonic() - t0
        return result

    # ── PIPELINE: Iterative Verbesserung ──────────────────────────────────────
    async def pipeline(self, prompt: str,
                       instance_ids: list[str],
                       stagger_ms:   int = DEFAULT_STAGGER_MS,
                       system:       str = "") -> DispatchResult:
        """
        Schickt Prompt an Instanz 1 → Antwort geht als Input an Instanz 2
        → Instanz 2 verbessert → weiter an Instanz 3 usw.
        Beste iterative Qualitätsverbesserung.
        """
        t0      = time.monotonic()
        result  = DispatchResult(modus="pipeline", prompt=prompt,
                                 n_instanzen=len(instance_ids))
        current = prompt

        for i, iid in enumerate(instance_ids):
            if i > 0:
                await asyncio.sleep(stagger_ms / 1000.0)

            if i == 0:
                verbesserungs_prompt = current
            else:
                verbesserungs_prompt = (
                    f"Hier ist eine Antwort auf die Aufgabe: '{prompt}'\n\n"
                    f"Antwort:\n{current}\n\n"
                    f"Verbessere diese Antwort: mache sie ausführlicher, "
                    f"korrekter und besser strukturiert. "
                    f"Gib NUR die verbesserte Antwort zurück."
                )

            t_inst = time.monotonic()
            antwort_raw, _ = await self._get_single(iid, verbesserungs_prompt, system)
            latenz = time.monotonic() - t_inst
            score  = self.logic.evaluate(antwort_raw, prompt).total

            result.ergebnisse.append(InstanceResult(
                instance_id=iid, antwort=antwort_raw,
                latenz=latenz, score=score
            ))
            current = antwort_raw
            log.debug(f"Pipeline [{i+1}/{len(instance_ids)}] {iid}: Score={score:.1f}")

        result.final = current
        result.dauer = time.monotonic() - t0
        return result

    # ── Cross-Validation ──────────────────────────────────────────────────────
    async def _cross_validate(self, original_prompt: str,
                               ergebnisse: list[InstanceResult],
                               judge_id: Optional[str]) -> tuple[str, str]:
        """
        Lässt ein Judge-Modell die Antworten bewerten und die beste auswählen
        oder eine synthetisierte Antwort generieren.
        """
        gueltig = [r for r in ergebnisse if not r.fehler and r.antwort]
        if not gueltig:
            return "[Keine validen Antworten]", "none"
        if len(gueltig) == 1:
            return gueltig[0].antwort, gueltig[0].instance_id

        # Judge-Prompt aufbauen
        antworten_block = "\n\n".join(
            f"=== Antwort {i+1} (von {r.instance_id}) ===\n{r.antwort[:800]}"
            for i, r in enumerate(gueltig)
        )

        judge_prompt = (
            f"Original-Aufgabe: {original_prompt}\n\n"
            f"Mehrere KI-Modelle haben diese Aufgabe beantwortet:\n\n"
            f"{antworten_block}\n\n"
            f"Erstelle eine optimale Synthese-Antwort die:\n"
            f"1. Die besten Informationen aus allen Antworten kombiniert\n"
            f"2. Widersprüche auflöst (die wahrscheinlichere Version wählt)\n"
            f"3. Lücken aus anderen Antworten füllt\n"
            f"4. Gut strukturiert und vollständig ist\n\n"
            f"Gib NUR die synthetisierte Antwort zurück, keine Meta-Kommentare."
        )

        # Judge: bevorzugt das Modell mit höchstem Score
        if judge_id:
            judge_antwort, used_judge = await self._get_single(
                judge_id, judge_prompt,
                "Du bist ein kritischer Qualitäts-Synthesizer."
            )
        else:
            # Bestes Modell als Judge
            bestes = max(gueltig, key=lambda r: r.score)
            judge_antwort, used_judge = await self._get_single(
                bestes.instance_id, judge_prompt,
                "Du bist ein kritischer Qualitäts-Synthesizer."
            )
            used_judge = bestes.instance_id

        log.info(f"Cross-Validation durch {used_judge}: "
                 f"{len(judge_antwort.split())} Wörter")
        return judge_antwort, used_judge

    # ── Prompt-Zerlegung (Delegiert an Decomposer) ─────────────────────────────
    def _zerlege_prompt(self, prompt: str, n: int) -> list[str]:
        """Delegiert die atomare Zerlegung an den AtomicDecomposer."""
        from decomposer import get_decomposer
        decomposer = get_decomposer()
        fragmente = decomposer.atomisiere(prompt)
        if len(fragmente) >= n:
            return fragmente[:n]
        # Falls weniger Fragmente als Instanzen: auffüllen/aufteilen
        if not fragmente:
            fragmente = [prompt]
        while len(fragmente) < n:
            fragmente.append(fragmente[-1])
        return fragmente[:n]

    def _aggregiere(self, antworten: list[str], original: str) -> str:
        """Fasst Sub-Task-Antworten zu einer zusammen."""
        if not antworten:
            return "[Keine Antworten]"
        if len(antworten) == 1:
            return antworten[0]

        header = f"[Zusammengeführt aus {len(antworten)} KI-Instanzen]\n\n"
        teile  = []
        for i, a in enumerate(antworten, 1):
            teile.append(f"**Teil {i}:**\n{a}")
        return header + "\n\n---\n\n".join(teile)

    # ── Interne Hilfsmethoden ─────────────────────────────────────────────────
    async def _ask_with_sem(self, sem: asyncio.Semaphore,
                             iid: str, prompt: str,
                             system: str, delay: float) -> tuple[str, float]:
        """Semaphore-gesicherter Request (verhindert CPU-Throttling)."""
        await asyncio.sleep(delay)
        async with sem:
            return await self._get_single(iid, prompt, system)

    async def _ask_instance_delayed(self, iid: str, prompt: str,
                                    system: str,
                                    delay: float) -> tuple[str, float]:
        await asyncio.sleep(delay)
        return await self._get_single(iid, prompt, system)

    async def _get_single(self, iid: str, prompt: str,
                          system: str,
                          parent_id: Optional[str] = None) -> tuple[str, float]:
        """
        Flexible Anfrage über Executor (Subtask), mit Browser/Relay als Ausführungsziel.
        """
        t0 = time.monotonic()

        # Browser-Instanz verfügbar?
        browser = self._get_browser()
        inst = browser._instances.get(iid)
        if inst and inst.aktiv:
            antwort = await browser.ask_instance(iid, prompt)
            return antwort, time.monotonic() - t0

        executor = self._get_executor()
        from executor import TaskType, Strategy
        task = executor.create_task(
            typ=TaskType.CHAT,
            prompt=prompt,
            beschreibung=f"dispatcher_subtask:{iid}:{prompt[:40]}",
            provider=iid,
            parent_id=parent_id,
            system_prompt=system,
            strategy=Strategy(
                allow_tools=False,
                allow_followup=False,
                allow_provider_switch=True,
            ),
        )
        task = await executor.submit_and_wait(task, timeout=120.0)
        antwort = task.antwort or task.fehler or "[Keine Antwort]"
        return antwort, time.monotonic() - t0

    def stats(self) -> dict:
        return {"status": "online"}


import re  # oben vergessen


# ── Singleton ─────────────────────────────────────────────────────────────────
_dispatcher: Optional[MultiKIDispatcher] = None

def get_dispatcher() -> MultiKIDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = MultiKIDispatcher()
    return _dispatcher
