"""One-shot staging job entrypoint; production scheduling remains external."""
from __future__ import annotations

import json
import os
import subprocess
import sys

from validate_staging_config import validate


def main() -> None:
    errors = validate()
    if errors:
        raise SystemExit("invalid staging configuration: " + "; ".join(errors))
    raw = os.environ.get("STAGING_COMMAND_JSON", "")
    run_id = os.environ.get("STAGING_RUN_ID", "")
    if not raw or not run_id:
        raise SystemExit("STAGING_COMMAND_JSON and STAGING_RUN_ID are required")
    command = json.loads(raw)
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        raise SystemExit("STAGING_COMMAND_JSON must be a JSON array of strings")
    result = subprocess.run(
        [sys.executable, "scripts/run_staging_once.py", "--run-id", run_id, "--", *command],
        check=False,
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
