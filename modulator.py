from __future__ import annotations

import time
import threading
import logging
from typing import Any, Optional

try:
    from audit import AuditLog
except ImportError:
    AuditLog = None  # type: ignore

log = logging.getLogger("Isaac.Modulator")

# Default decay rate per second (e.g., 0.05 per second -> returns to baseline in ~20s)
DEFAULT_DECAY_RATE_PER_SEC = 0.05


class DivaModulator:
    """
    DIVA Runtime Modulator for Isaac.
    Manages global, slow-varying scalar modulators (uncertainty, caution, curiosity)
    that modulate memory retrieval sensitivity, decision thresholds, and strategy selection.
    """

    def __init__(self, decay_rate: float = DEFAULT_DECAY_RATE_PER_SEC):
        self._lock = threading.Lock()
        self._decay_rate = decay_rate
        self._last_update_ts = time.time()
        self._modulators: dict[str, float] = {
            "uncertainty": 0.0,
            "caution": 0.0,
            "curiosity": 0.0,
        }

    def _apply_decay_locked(self, now: float) -> None:
        dt = now - self._last_update_ts
        if dt <= 0:
            return
        decay_amount = self._decay_rate * dt
        for key in self._modulators:
            val = self._modulators[key]
            if val > 0.0:
                self._modulators[key] = max(0.0, val - decay_amount)
        self._last_update_ts = now

    def decay(self, dt: Optional[float] = None) -> dict[str, float]:
        """Explicitly decay modulators by dt seconds (or auto dt if None)."""
        now = time.time()
        with self._lock:
            if dt is not None:
                decay_amount = max(0.0, self._decay_rate * dt)
                for key in self._modulators:
                    self._modulators[key] = max(0.0, self._modulators[key] - decay_amount)
                self._last_update_ts = now
            else:
                self._apply_decay_locked(now)
            res = dict(self._modulators)

        if AuditLog and hasattr(AuditLog, "action"):
            try:
                AuditLog.action("DivaModulator", "decay", f"dt={dt} state={res}")
            except Exception as e:
                log.debug("AuditLog error during decay: %s", e)
        return res

    def raise_for_diva(
        self,
        uncertainty: float,
        relevance: float,
        reason: str = "",
    ) -> dict[str, float]:
        """
        Triggered on DIVA event (unclear provenance + behavioral relevance).
        Raises modulators based on uncertainty and relevance factors.
        """
        now = time.time()
        u_factor = max(0.0, min(1.0, float(uncertainty)))
        r_factor = max(0.0, min(1.0, float(relevance)))
        boost = u_factor * r_factor

        with self._lock:
            self._apply_decay_locked(now)
            # Raise modulators proportionately
            self._modulators["uncertainty"] = min(1.0, self._modulators["uncertainty"] + boost)
            self._modulators["caution"] = min(1.0, self._modulators["caution"] + 0.8 * boost)
            self._modulators["curiosity"] = min(1.0, self._modulators["curiosity"] + 0.6 * boost)
            res = dict(self._modulators)

        log.info("DIVA trigger raised modulators: boost=%.2f, reason='%s', new_state=%s", boost, reason, res)

        if AuditLog and hasattr(AuditLog, "action"):
            try:
                AuditLog.action(
                    "DivaModulator",
                    "raise_for_diva",
                    f"boost={boost:.2f} reason={reason} state={res}",
                )
            except Exception as e:
                log.debug("AuditLog error during raise_for_diva: %s", e)

        return res

    def resolve_provenance(self, reason: str = "") -> dict[str, float]:
        """
        Event-based resolution of uncertainty (e.g. provenance successfully verified).
        Sharply lowers modulators back towards zero.
        """
        now = time.time()
        with self._lock:
            self._apply_decay_locked(now)
            for key in self._modulators:
                self._modulators[key] = round(self._modulators[key] * 0.1, 4)
                if self._modulators[key] < 0.05:
                    self._modulators[key] = 0.0
            res = dict(self._modulators)

        log.info("DIVA provenance resolved: reason='%s', new_state=%s", reason, res)

        if AuditLog and hasattr(AuditLog, "action"):
            try:
                AuditLog.action("DivaModulator", "resolve_provenance", f"reason={reason} state={res}")
            except Exception as e:
                log.debug("AuditLog error during resolve_provenance: %s", e)

        return res

    def get(self, name: str) -> float:
        """Get current value of a specific modulator after applying decay."""
        now = time.time()
        with self._lock:
            self._apply_decay_locked(now)
            return self._modulators.get(name, 0.0)

    def get_all(self) -> dict[str, float]:
        """Get copy of all modulator values after applying decay."""
        now = time.time()
        with self._lock:
            self._apply_decay_locked(now)
            return dict(self._modulators)


_global_modulator: Optional[DivaModulator] = None
_global_lock = threading.Lock()


def get_modulator() -> DivaModulator:
    global _global_modulator
    if _global_modulator is None:
        with _global_lock:
            if _global_modulator is None:
                _global_modulator = DivaModulator()
    return _global_modulator
