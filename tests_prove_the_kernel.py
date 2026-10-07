from __future__ import annotations

import asyncio
import unittest
from typing import Any

from decision_trace import DecisionTrace, TracePhase
from executor import Executor, Strategy, Task, TaskStatus, TaskType, get_executor
from learning_engine import CandidateStatus, get_learning_engine
from memory import EpistemicClass, Memory, get_memory
from evals.kernel_benchmark import run as run_kernel_benchmark
from evals.kernel_metrics import KernelMetricsEvaluator, KernelMetricsReport


class TestProveTheKernelPillars(unittest.TestCase):
    def setUp(self):
        self.executor = get_executor()
        self.executor._tasks.clear()
        self.memory = get_memory()
        self.learning = get_learning_engine()

    def test_pillar1_e2e_100_task_benchmark(self):
        """Pillar 1: Standardized 100-Task E2E Benchmark Pipeline."""
        res = run_kernel_benchmark()
        self.assertTrue(res["ok"])
        self.assertEqual(res["passed"], 100)
        self.assertEqual(res["total"], 100)
        self.assertGreaterEqual(res["metrics"]["autonomy_without_loss_of_control"], 0.9)

    def test_pillar2_task_graph_dag_integrity(self):
        """Pillar 2: Formal TaskGraph DAG model & cycle detection."""
        t_root = Task(id="root_1", typ=TaskType.PLAN, prompt="Main Plan Goal", beschreibung="Root Goal")
        t_sub1 = Task(id="sub_1", typ=TaskType.ANALYSIS, prompt="Subtask 1", beschreibung="Analysis", parent_id="root_1")
        t_sub2 = Task(id="sub_2", typ=TaskType.CODE, prompt="Subtask 2", beschreibung="Coding", parent_id="root_1")
        t_sub2.dependencies.append("sub_1")

        self.executor._tasks["root_1"] = t_root
        self.executor._tasks["sub_1"] = t_sub1
        self.executor._tasks["sub_2"] = t_sub2
        t_root.sub_task_ids.extend(["sub_1", "sub_2"])

        # Check graph retrieval
        graph = self.executor.get_task_graph("root_1")
        self.assertEqual(graph["root_id"], "root_1")
        self.assertEqual(graph["node_count"], 3)

        # Check DAG validity
        val_res = self.executor.validate_task_graph("root_1")
        self.assertTrue(val_res["is_valid"])
        self.assertFalse(val_res["has_cycle"])

        # Check dependencies
        self.assertFalse(self.executor.are_dependencies_satisfied("sub_2"))
        t_sub1.status = TaskStatus.DONE
        self.assertTrue(self.executor.are_dependencies_satisfied("sub_2"))

        # Test cycle detection
        t_sub1.dependencies.append("sub_2")
        val_cycle = self.executor.validate_task_graph("root_1")
        self.assertFalse(val_cycle["is_valid"])
        self.assertTrue(val_cycle["has_cycle"])

    def test_pillar3_formal_learning_commit_pipeline(self):
        """Pillar 3: Formal Learning Commit Pipeline & Evidence Verification."""
        t_ev = Task(id="ev_task_1", typ=TaskType.RESEARCH, prompt="Research Task", beschreibung="Evidence")
        t_ev.status = TaskStatus.DONE
        self.executor._tasks["ev_task_1"] = t_ev

        cand = self.learning.propose_candidate(
            observation="Observed efficient search query format",
            relevance_notes="Reduces web search latency",
            evidence_task_ids=["ev_task_1"],
            confidence=0.88,
        )
        self.assertEqual(cand.status, CandidateStatus.CANDIDATE)

        # Verify evidence
        ev_res = self.learning.verify_candidate_evidence(cand.candidate_id, require_evidence_tasks=True)
        self.assertTrue(ev_res["verified"])

        # Evaluate candidate
        self.learning.evaluate_candidate(
            candidate_id=cand.candidate_id,
            replay_passed=True,
            eval_score_gain=0.15,
            regression_check_passed=True,
            governance_approved=True,
        )

        # Commit candidate with evidence verification
        committed = self.learning.commit_candidate(cand.candidate_id, verify_evidence=True)
        self.assertIsNotNone(committed)
        self.assertEqual(committed.status, CandidateStatus.COMMITTED)
        self.assertEqual(len(committed.commit_hash), 16)

    def test_pillar4_epistemic_memory_and_contradiction_handling(self):
        """Pillar 4: Epistemic Memory classes, evidence linking & contradiction resolution."""
        mem_entry = self.memory.add_epistemic_memory(
            key="user_preference_concise",
            value="User prefers bullet points",
            epistemic_class=EpistemicClass.USER_ASSERTION,
            source="steffen",
            source_authority="owner",
            confidence=0.9,
        )
        self.assertEqual(mem_entry.epistemic_class, EpistemicClass.USER_ASSERTION)
        self.assertEqual(mem_entry.source_authority, "owner")

        # Link evidence task
        linked = self.memory.link_evidence_to_memory(mem_entry.memory_id, task_id="task_proof_4", confidence_boost=0.05)
        self.assertIn("task_proof_4", linked.evidence_task_ids)
        self.assertAlmostEqual(linked.confidence, 0.95)

        # Resolve contradiction
        contradicted = self.memory.resolve_contradiction(
            memory_id=mem_entry.memory_id,
            contradicting_ref="task_proof_5",
            new_epistemic_class=EpistemicClass.CONTRADICTED,
            new_confidence=0.1,
        )
        self.assertEqual(contradicted.epistemic_class, EpistemicClass.CONTRADICTED)
        self.assertIn("task_proof_5", contradicted.contradicted_by)
        self.assertAlmostEqual(contradicted.confidence, 0.1)

    def test_pillar5_autonomy_governance_metrics_evaluator(self):
        """Pillar 5: Objective Autonomy & Governance Metrics calculation."""
        tasks = []
        for i in range(10):
            t = Task(id=f"metric_t_{i}", typ=TaskType.CHAT, prompt=f"Prompt {i}", beschreibung="Desc")
            t.status = TaskStatus.DONE
            t.record_action("classify", {"intent": "chat"})
            t.record_evidence("test", "good", 1.0)
            t.decision_trace = DecisionTrace()
            for phase in TracePhase:
                t.decision_trace.add(phase, f"event_{phase.value}", {"status": "ok"})
            tasks.append(t)

        evaluator = KernelMetricsEvaluator()
        report = evaluator.evaluate_tasks(tasks)

        self.assertEqual(report.total_tasks, 10)
        self.assertEqual(report.successful_tasks, 10)
        self.assertEqual(report.task_success_rate, 1.0)
        self.assertEqual(report.unauthorized_action_rate, 0.0)
        self.assertEqual(report.trace_completeness, 1.0)
        self.assertEqual(report.autonomy_without_loss_of_control, 1.0)


if __name__ == "__main__":
    unittest.main()
