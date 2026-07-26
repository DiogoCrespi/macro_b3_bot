import json
import subprocess
import sys


def test_p4_metrics_do_not_invent_precision_or_mirofish_effect() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/run_p4_decision_metrics.py"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["metrics"]["precision_at_k"] is None
    assert payload["metrics"]["hit_rate"] is None
    assert payload["metrics"]["metric_status"] == "NOT_EVALUABLE_NO_ALLOCATED_OUTCOMES"
    assert payload["ablation"]["difference_count"] == 0
    assert payload["promotion_status"] == "NOT_PROMOTED_TO_DECISION_POLICY"
