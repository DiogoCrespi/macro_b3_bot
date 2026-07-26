"""Render a self-contained, read-only staging observability dashboard."""
from __future__ import annotations

import html
import json
from pathlib import Path
import sys


def render(metrics_path: Path, output_path: Path) -> None:
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    rows = []
    for name, value in metrics.get("counters", {}).items():
        rows.append(f"<tr><td>counter</td><td>{html.escape(name)}</td><td>{html.escape(str(value))}</td></tr>")
    for name, value in metrics.get("gauges", {}).items():
        rows.append(f"<tr><td>gauge</td><td>{html.escape(name)}</td><td>{html.escape(str(value))}</td></tr>")
    body = "".join(rows) or '<tr><td colspan="3">No metrics recorded</td></tr>'
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "<!doctype html><meta charset='utf-8'><title>Macro B3 staging</title>"
        "<h1>Macro B3 staging observability</h1>"
        f"<p>run_id={html.escape(str(metrics.get('run_id', 'NOT_EXPOSED')))} "
        f"recorded_at={html.escape(str(metrics.get('recorded_at', 'NOT_EXPOSED')))}</p>"
        "<table border='1'><tr><th>kind</th><th>metric</th><th>value</th></tr>"
        + body + "</table>"
        "<p>Read-only artifact. It does not issue decisions or orders.</p>\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: render_observability_dashboard.py METRICS_JSON OUTPUT_HTML")
    render(Path(sys.argv[1]), Path(sys.argv[2]))
