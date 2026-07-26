from macro_b3_bot.application.observability import MetricsRegistry, evaluate_alerts


def test_metrics_snapshot_persists_and_alerts_are_deterministic(tmp_path):
    registry = MetricsRegistry()
    registry.inc("sidecar_failures", 3)
    registry.inc("unresolved_conflicts")
    registry.set_gauge("hypothesis_approval_rate", 0.25)
    snapshot = registry.persist(tmp_path / "metrics.json", run_id="run-1", app_version="test")
    assert {alert.code for alert in evaluate_alerts(snapshot)} == {"SIDECAR_FAILURE_BURST", "UNRESOLVED_CONFLICT", "LOW_HYPOTHESIS_APPROVAL_RATE"}


def test_negative_counter_increment_is_rejected():
    registry = MetricsRegistry()
    try:
        registry.inc("runs", -1)
    except ValueError:
        pass
    else:
        raise AssertionError("negative counter increment must fail")
