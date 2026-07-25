"""Create a verified point-in-time copy of the local DuckDB store."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import duckdb


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("data/macro_b3_bot.duckdb"))
    parser.add_argument("--destination", type=Path, default=Path("data/backups"))
    parser.add_argument("--keep", type=int, default=7)
    args = parser.parse_args()
    source = args.source.resolve()
    if not source.is_file():
        raise SystemExit(f"source DuckDB not found: {source}")
    args.destination.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = args.destination / f"{source.stem}_{timestamp}.duckdb"
    connection = duckdb.connect(str(source))
    try:
        connection.execute("CHECKPOINT")
    finally:
        connection.close()
    shutil.copy2(source, target)
    restore_check = "PASS"
    restored = duckdb.connect(str(target), read_only=True)
    try:
        restored.execute("SELECT 1").fetchone()
    except Exception:
        restore_check = "FAILED"
        raise
    finally:
        restored.close()
    manifest = {
        "source": str(source),
        "backup": str(target),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sha256": sha256(target),
        "byte_size": target.stat().st_size,
        "restore_check": restore_check,
    }
    backups = sorted(
        args.destination.glob(f"{source.stem}_*.duckdb"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for old_backup in backups[args.keep:]:
        old_backup.unlink()
        old_backup.with_suffix(".json").unlink(missing_ok=True)
    manifest["retained_backups"] = min(args.keep, len(backups))
    target.with_suffix(".json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
