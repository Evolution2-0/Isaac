from __future__ import annotations

import json
from evals.governance_eval import run as governance_run
from evals.identity_eval import run as identity_run
from evals.learning_eval import run as learning_run
from evals.reliability_eval import run as reliability_run
from evals.replay_eval import run as replay_run
from evals.regression_eval import run as regression_run
from evals.mcp_eval import run as mcp_run
from evals.goal_eval import run as goal_run
from evals.coding_eval import run as coding_run
from evals.kernel_benchmark import run as kernel_benchmark_run
from evals.kernel_metrics import run as kernel_metrics_run


def run_all() -> dict:
    suites = [
        governance_run(),
        identity_run(),
        learning_run(),
        reliability_run(),
        replay_run(),
        regression_run(),
        mcp_run(),
        goal_run(),
        coding_run(),
        kernel_benchmark_run(),
        kernel_metrics_run(),
    ]
    return {
        "ok": all(s["passed"] == s["total"] for s in suites),
        "suites": suites,
        "passed": sum(s["passed"] for s in suites),
        "total": sum(s["total"] for s in suites),
    }


def run_suite(name: str) -> dict:
    mapping = {
        "governance": governance_run,
        "identity": identity_run,
        "learning": learning_run,
        "reliability": reliability_run,
        "replay": replay_run,
        "regression": regression_run,
        "mcp": mcp_run,
        "goal": goal_run,
        "coding": coding_run,
        "kernel_benchmark": kernel_benchmark_run,
        "kernel_metrics": kernel_metrics_run,
    }
    fn = mapping.get(name)
    if not fn:
        return {"ok": False, "error": f"Unknown suite: {name}"}
    return fn()


if __name__ == "__main__":
    import sys

    result = run_all()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("ok"):
        sys.exit(1)
