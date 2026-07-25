"""Fail-fast health check for the staging orchestrator container."""
from __future__ import annotations

import os
from pathlib import Path


def main() -> None:
    import macro_b3_bot  # noqa: F401

    data_dir = Path(os.environ.get("DATA_DIR", "/app/data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    probe = data_dir / ".healthcheck"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()
    if os.environ.get("ALLOW_BUY_SIGNALS", "false").casefold() == "true":
        raise SystemExit("staging safety violation: BUY signals must remain disabled")
    if os.environ.get("ALLOW_ORDER_EXECUTION", "false").casefold() == "true":
        raise SystemExit("staging safety violation: order execution must remain disabled")
    print("staging health: ok")


if __name__ == "__main__":
    main()
