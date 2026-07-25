from scripts.validate_staging_config import validate


def _base() -> dict[str, str]:
    return {
        "APP_ENV": "staging",
        "RESEARCH_MODE": "true",
        "ALLOW_BUY_SIGNALS": "false",
        "ALLOW_ORDER_EXECUTION": "false",
        "MIROFISH_IMAGE": "mirofish-local@sha256:" + "a" * 64,
        "STAGING_RUN_ID": "run-1",
        "STAGING_COMMAND_JSON": "[\"macro-b3\", \"validate-config\"]",
    }


def test_staging_config_requires_immutable_sidecar_digest():
    env = _base()
    assert validate(env) == []
    env["MIROFISH_IMAGE"] = "mirofish:staging"
    assert "MIROFISH_IMAGE must use an immutable @sha256 digest" in validate(env)


def test_staging_config_fails_open_flags():
    env = _base()
    env["ALLOW_ORDER_EXECUTION"] = "true"
    assert "ALLOW_ORDER_EXECUTION must remain false" in validate(env)
