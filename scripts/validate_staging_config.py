"""Validate fail-closed staging configuration before deployment."""
from __future__ import annotations

import os
import re
import sys

_DIGEST_RE = re.compile(r"^[A-Za-z0-9./_-]+@sha256:[0-9a-f]{64}$")


def validate(environ: dict[str, str] | None = None) -> list[str]:
    env = os.environ if environ is None else environ
    errors: list[str] = []
    if env.get("APP_ENV") != "staging":
        errors.append("APP_ENV must be staging")
    if env.get("RESEARCH_MODE", "").casefold() != "true":
        errors.append("RESEARCH_MODE must be true")
    for key in ("ALLOW_BUY_SIGNALS", "ALLOW_ORDER_EXECUTION"):
        if env.get(key, "").casefold() == "true":
            errors.append(f"{key} must remain false")
    if not _DIGEST_RE.fullmatch(env.get("MIROFISH_IMAGE", "")):
        errors.append("MIROFISH_IMAGE must use an immutable @sha256 digest")
    if not env.get("STAGING_RUN_ID"):
        errors.append("STAGING_RUN_ID is required")
    if not env.get("STAGING_COMMAND_JSON"):
        errors.append("STAGING_COMMAND_JSON is required")
    return errors


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(f"staging config: ERROR: {error}", file=sys.stderr)
        return 1
    print("staging config: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
