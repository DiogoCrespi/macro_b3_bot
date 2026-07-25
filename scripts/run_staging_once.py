"""Serialize and make an application run idempotent for staging."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def acquire(lock_path: Path):
    handle = lock_path.open("a+")
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        handle.close()
        raise SystemExit("STAGING_RUN_LOCKED")
    return handle


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--state-dir", type=Path, default=Path("data/staging_runs"))
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    if not command:
        raise SystemExit("a command is required after --")
    args.state_dir.mkdir(parents=True, exist_ok=True)
    marker = args.state_dir / f"{args.run_id}.json"
    if marker.is_file() and json.loads(marker.read_text(encoding="utf-8")).get("status") == "SUCCESS":
        print("STAGING_RUN_ALREADY_SUCCEEDED")
        return
    lock = acquire(args.state_dir / ".run.lock")
    try:
        result = subprocess.run(command, check=False)
        payload = {
            "run_id": args.run_id,
            "command": command,
            "status": "SUCCESS" if result.returncode == 0 else "FAILED",
            "returncode": result.returncode,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        marker.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        raise SystemExit(result.returncode)
    finally:
        lock.close()


if __name__ == "__main__":
    main()
