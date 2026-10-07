from __future__ import annotations
import json
import time
import hashlib
import uuid
from dataclasses import dataclass, asdict, field
from enum import Enum
from pathlib import Path
from typing import Optional, Any
from config import DATA_DIR

PATH = DATA_DIR / 'learning_events.jsonl'
CANDIDATES_PATH = DATA_DIR / 'learning_candidates.jsonl'
COMMITS_PATH = DATA_DIR / 'learning_commits.jsonl'


class CandidateStatus(Enum):
    CANDIDATE          = "candidate"
    EVALUATING         = "evaluating"
    REPLAY_PASSED      = "replay_passed"
    REPLAY_FAILED      = "replay_failed"
    REGRESSION_FAILED  = "regression_failed"
    GOVERNANCE_BLOCKED = "governance_blocked"
    COMMITTED          = "committed"
    REJECTED           = "rejected"


@dataclass
class LearningEvent:
    ts: float
    prompt: str
    route: str
    outcome: str
    score: float = 0.0
    notes: str = ''


@dataclass
class LearningCandidate:
    candidate_id: str
    ts: float
    observation: str
    relevance_notes: str
    evidence_task_ids: list[str] = field(default_factory=list)
    evidence_data: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.5
    contradicts_existing: bool = False
    replay_passed: bool = False
    eval_score_gain: float = 0.0
    regression_check_passed: bool = False
    governance_approved: bool = False
    status: CandidateStatus = CandidateStatus.CANDIDATE
    version: int = 1
    commit_hash: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value if isinstance(self.status, CandidateStatus) else str(self.status)
        return d


class LearningEngine:
    def learn(self, prompt: str, route: str, outcome: str, score: float = 0.0, notes: str = ''):
        ev = LearningEvent(time.time(), prompt[:1000], route, outcome, score, notes[:1000])
        with PATH.open('a', encoding='utf-8') as f:
            f.write(json.dumps(asdict(ev), ensure_ascii=False) + '\n')

    def recent(self, n: int = 50):
        if not PATH.exists():
            return []
        lines = PATH.read_text(encoding='utf-8').splitlines()[-n:]
        return [json.loads(x) for x in lines if x.strip()]

    # ── Formaler Learning-Commit-Prozess (Säule 3 - Prove the Kernel) ────────

    def propose_candidate(
        self,
        observation: str,
        relevance_notes: str = "",
        evidence_task_ids: Optional[list[str]] = None,
        evidence_data: Optional[dict[str, Any]] = None,
        confidence: float = 0.5,
        contradicts_existing: bool = False,
        notes: str = "",
    ) -> LearningCandidate:
        cid = f"cand_{uuid.uuid4().hex[:10]}"
        cand = LearningCandidate(
            candidate_id=cid,
            ts=time.time(),
            observation=observation,
            relevance_notes=relevance_notes,
            evidence_task_ids=evidence_task_ids or [],
            evidence_data=evidence_data or {},
            confidence=confidence,
            contradicts_existing=contradicts_existing,
            status=CandidateStatus.CANDIDATE,
            notes=notes,
        )
        self._save_candidate(cand)
        return cand

    def evaluate_candidate(
        self,
        candidate_id: str,
        replay_passed: bool,
        eval_score_gain: float,
        regression_check_passed: bool,
        governance_approved: bool,
        notes: str = "",
    ) -> Optional[LearningCandidate]:
        cand = self.get_candidate(candidate_id)
        if not cand:
            return None

        cand.replay_passed = replay_passed
        cand.eval_score_gain = eval_score_gain
        cand.regression_check_passed = regression_check_passed
        cand.governance_approved = governance_approved
        if notes:
            cand.notes = f"{cand.notes} | {notes}".strip(" |")

        if not governance_approved:
            cand.status = CandidateStatus.GOVERNANCE_BLOCKED
        elif not replay_passed:
            cand.status = CandidateStatus.REPLAY_FAILED
        elif not regression_check_passed:
            cand.status = CandidateStatus.REGRESSION_FAILED
        else:
            cand.status = CandidateStatus.REPLAY_PASSED

        self._save_candidate(cand)
        return cand

    def verify_candidate_evidence(
        self,
        candidate_id: str,
        require_evidence_tasks: bool = True,
        require_decision_trace: bool = False,
    ) -> dict[str, Any]:
        """Verifies candidate evidence against TaskGraph and DecisionTrace nodes."""
        cand = self.get_candidate(candidate_id)
        if not cand:
            return {"verified": False, "error": "Candidate not found"}

        task_ids = cand.evidence_task_ids or []
        if require_evidence_tasks and not task_ids:
            return {"verified": False, "error": "No evidence task IDs linked to candidate"}

        try:
            from executor import get_executor
            executor = get_executor()
            valid_tasks = []
            for tid in task_ids:
                if executor.get_task(tid):
                    valid_tasks.append(tid)
            if require_evidence_tasks and not valid_tasks:
                return {"verified": False, "error": "None of the evidence task IDs exist in Executor"}
        except Exception as exc:
            valid_tasks = task_ids

        return {
            "verified": True,
            "candidate_id": candidate_id,
            "evidence_task_count": len(task_ids),
            "valid_task_count": len(valid_tasks),
        }

    def commit_candidate(self, candidate_id: str, verify_evidence: bool = False) -> Optional[LearningCandidate]:
        cand = self.get_candidate(candidate_id)
        if not cand:
            return None

        if verify_evidence:
            ev_check = self.verify_candidate_evidence(candidate_id, require_evidence_tasks=True)
            if not ev_check.get("verified"):
                cand.status = CandidateStatus.REJECTED
                cand.notes = f"{cand.notes} [Evidence verification failed: {ev_check.get('error')}]".strip()
                self._save_candidate(cand)
                return cand

        if not (cand.replay_passed and cand.regression_check_passed and cand.governance_approved):
            cand.status = CandidateStatus.REJECTED
            self._save_candidate(cand)
            return cand

        payload = f"{cand.candidate_id}:{cand.observation}:{cand.ts}:{cand.version}"
        commit_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

        cand.commit_hash = commit_hash
        cand.status = CandidateStatus.COMMITTED
        self._save_candidate(cand)

        with COMMITS_PATH.open('a', encoding='utf-8') as f:
            f.write(json.dumps(cand.to_dict(), ensure_ascii=False) + '\n')

        return cand

    def get_candidate(self, candidate_id: str) -> Optional[LearningCandidate]:
        for cand_dict in self.list_candidates():
            if cand_dict.get("candidate_id") == candidate_id:
                status_str = cand_dict.get("status", "candidate")
                try:
                    status_enum = CandidateStatus(status_str)
                except ValueError:
                    status_enum = CandidateStatus.CANDIDATE
                cand_dict["status"] = status_enum
                return LearningCandidate(**cand_dict)
        return None

    def list_candidates(self, limit: int = 100) -> list[dict[str, Any]]:
        if not CANDIDATES_PATH.exists():
            return []
        lines = CANDIDATES_PATH.read_text(encoding='utf-8').splitlines()[-limit:]
        result = []
        for line in lines:
            if line.strip():
                try:
                    result.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return result

    def list_commits(self, limit: int = 100) -> list[dict[str, Any]]:
        if not COMMITS_PATH.exists():
            return []
        lines = COMMITS_PATH.read_text(encoding='utf-8').splitlines()[-limit:]
        result = []
        for line in lines:
            if line.strip():
                try:
                    result.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return result

    def _save_candidate(self, cand: LearningCandidate):
        existing = []
        if CANDIDATES_PATH.exists():
            for line in CANDIDATES_PATH.read_text(encoding='utf-8').splitlines():
                if line.strip():
                    try:
                        d = json.loads(line)
                        if d.get("candidate_id") != cand.candidate_id:
                            existing.append(d)
                    except json.JSONDecodeError:
                        pass
        existing.append(cand.to_dict())
        lines = [json.dumps(x, ensure_ascii=False) for x in existing]
        CANDIDATES_PATH.write_text('\n'.join(lines) + '\n', encoding='utf-8')


_engine = None


def get_learning_engine() -> LearningEngine:
    global _engine
    if _engine is None:
        _engine = LearningEngine()
    return _engine
