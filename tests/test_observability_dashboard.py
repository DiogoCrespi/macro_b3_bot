import json

from scripts.render_observability_dashboard import render


def test_dashboard_is_read_only_and_escapes_values(tmp_path):
    metrics = tmp_path / "metrics.json"
    metrics.write_text(json.dumps({"run_id": "r1", "counters": {"runs": 1}, "gauges": {"x": "<unsafe>"}}), encoding="utf-8")
    output = tmp_path / "dashboard.html"
    render(metrics, output)
    content = output.read_text(encoding="utf-8")
    assert "&lt;unsafe&gt;" in content
    assert "BUY" not in content
