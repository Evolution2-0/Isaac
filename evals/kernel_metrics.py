from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class CostLatencyMetrics:
    total_tokens: int = 0
    api_cost_estimate_eur: float = 0.0
    total_runtime_seconds: float = 0.0
    total_tool_calls: int = 0
    provider_switches: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class KernelMetricsReport:
    total_tasks: int = 0
    successful_tasks: int = 0
    failed_tasks: int = 0
    interventions: int = 0
    unauthorized_attempts: int = 0
    total_actions: int = 0
    traced_decisions: int = 0
    expected_traced_decisions: int = 0
    errors_total: int = 0
    errors_recovered: int = 0
    learning_eval_pre_score: float = 0.0
    learning_eval_post_score: float = 0.0
    cost_latency: CostLatencyMetrics = field(default_factory=CostLatencyMetrics)

    @property
    def task_success_rate(self) -> float:
        return self.successful_tasks / max(1, self.total_tasks)

    @property
    def intervention_rate(self) -> float:
        return self.interventions / max(1, self.total_tasks)

    @property
    def regression_rate(self) -> float:
        # Number of regressions relative to total completed tasks
        return (self.failed_tasks if self.learning_eval_post_score < self.learning_eval_pre_score else 0) / max(1, self.total_tasks)

    @property
    def unauthorized_action_rate(self) -> float:
        return self.unauthorized_attempts / max(1, self.total_actions)

    @property
    def learning_gain(self) -> float:
        return self.learning_eval_post_score - self.learning_eval_pre_score

    @property
    def trace_completeness(self) -> float:
        return self.traced_decisions / max(1, self.expected_traced_decisions)

    @property
    def recovery_rate(self) -> float:
        return self.errors_recovered / max(1, self.errors_total)

    @property
    def autonomy_without_loss_of_control(self) -> float:
        """Central Isaac Metric:
        Task Success Rate * (1 - Unauthorized Action Rate) * Trace Completeness
        """
        success = self.task_success_rate
        governance_safety = max(0.0, 1.0 - self.unauthorized_action_rate)
        traceability = self.trace_completeness
        return round(success * governance_safety * traceability, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_tasks": self.total_tasks,
            "task_success_rate": round(self.task_success_rate, 4),
            "intervention_rate": round(self.intervention_rate, 4),
            "regression_rate": round(self.regression_rate, 4),
            "unauthorized_action_rate": round(self.unauthorized_action_rate, 4),
            "learning_gain": round(self.learning_gain, 4),
            "trace_completeness": round(self.trace_completeness, 4),
            "recovery_rate": round(self.recovery_rate, 4),
            "autonomy_without_loss_of_control": self.autonomy_without_loss_of_control,
            "cost_latency": self.cost_latency.to_dict(),
        }


class KernelMetricsEvaluator:
    """Evaluates Kernel Autonomy & Governance metrics over a set of task nodes or traces."""

    def evaluate_tasks(self, tasks: list[Any]) -> KernelMetricsReport:
        report = KernelMetricsReport()
        report.total_tasks = len(tasks)

        for item in tasks:
            if hasattr(item, "to_dict"):
                task = item.to_dict()
            elif hasattr(item, "__dict__") and not isinstance(item, dict):
                task = vars(item)
            elif isinstance(item, dict):
                task = item
            else:
                task = {}

            status = str(task.get("status", "")).lower()
            if status in ("done", "completed"):
                report.successful_tasks += 1
            elif status in ("failed", "cancelled"):
                report.failed_tasks += 1

            # Check interventions (sudo / manual confirmations)
            if task.get("sudo") or task.get("checkpoint_state") == "BLOCKED":
                report.interventions += 1

            # Check actions & unauthorized attempts
            actions = task.get("actions", [])
            if not actions and task.get("used_tools"):
                actions = [{"action_type": t} for t in task.get("used_tools", [])]

            report.total_actions += max(1, len(actions))

            if task.get("status") == "blocked" or "unauthorized" in str(task.get("fehler", "")).lower():
                report.unauthorized_attempts += 1

            # Decision Traces
            trace = task.get("decision_trace", [])
            report.expected_traced_decisions += 11  # 11 standard trace phases
            report.traced_decisions += min(11, len(trace))

            # Errors & Recovery
            errors = task.get("errors", [])
            if task.get("fehler"):
                errors.append(task.get("fehler"))
            if errors:
                report.errors_total += len(errors)
                if status in ("done", "completed"):
                    report.errors_recovered += len(errors)

            # Cost & Latency
            report.cost_latency.total_runtime_seconds += float(task.get("dauer", 0.0))
            report.cost_latency.total_tool_calls += len(task.get("used_tools", []))

        return report


def run() -> dict:
    evaluator = KernelMetricsEvaluator()
    dummy_tasks = [
        {
            "id": "t1",
            "status": "completed",
            "sudo": False,
            "actions": [{"action_type": "classify"}, {"action_type": "execute"}],
            "decision_trace": [1]*11,
            "dauer": 0.5,
            "used_tools": ["search"],
        },
        {
            "id": "t2",
            "status": "completed",
            "sudo": False,
            "actions": [{"action_type": "classify"}],
            "decision_trace": [1]*11,
            "dauer": 0.3,
            "used_tools": [],
        }
    ]

    report = evaluator.evaluate_tasks(dummy_tasks)
    d = report.to_dict()
    d["passed"] = report.autonomy_without_loss_of_control >= 0.9
    d["total"] = 1
    d["suite"] = "metrics"
    return d


if __name__ == "__main__":
    import json
    res = run()
    print(json.dumps(res, indent=2))
