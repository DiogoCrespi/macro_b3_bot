"""Create a content-addressed P0 reproducibility manifest."""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def classify(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized.startswith("data/audits/") or normalized.startswith("data/mirofish_seeds/"):
        return "CONTROLLED_FIXTURE_OR_AUDIT"
    if normalized.startswith("data/raw/") or normalized.endswith("acquisition_manifest.json"):
        return "UPSTREAM_ACQUISITION_ARTIFACT"
    if normalized.startswith(("src/", "scripts/", "tests/", "config/")):
        return "SOURCE_OR_TEST"
    return "PROJECT_METADATA"


def main() -> None:
    tracked = [line for line in git("ls-files").splitlines() if line]
    files = []
    for relative in tracked:
        path = ROOT / relative
        if path.is_file():
            files.append({"path": relative.replace("\\", "/"), "sha256": sha256(path), "class": classify(relative)})

    manifest = {
        "manifest_version": "P0.1-reproducibility-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git("rev-parse", "HEAD"),
        "git_branch": git("branch", "--show-current"),
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
            "supported_project_range": ">=3.11,<3.13",
        },
        "configuration": {
            "pyproject_sha256": sha256(ROOT / "pyproject.toml"),
            "lock_sha256": sha256(ROOT / "requirements-py312.lock"),
            "python_version_file_sha256": sha256(ROOT / ".python-version"),
            "env_example_sha256": sha256(ROOT / ".env.example"),
        },
        "data_policy": {
            "controlled_fixture_or_audit": "Must not be presented as upstream production evidence.",
            "upstream_acquisition_artifact": "Must retain source, collected_at, available_at and checksum.",
            "unknown": "Requires explicit provenance review before use.",
        },
        "files": files,
    }
    output = ROOT / "data/audits/baseline_reproducible.json"
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "git_commit": manifest["git_commit"], "files": len(files)}))


if __name__ == "__main__":
    main()
