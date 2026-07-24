import json

from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore
from scripts.run_sprint4e2_historical_assembly import run as run_assembly


def test_sprint4e2_historical_assembly_execution() -> None:
    settings = Settings()
    audit_data = run_assembly()

    assert audit_data["status"] == "SUCCESS"
    assert audit_data["total_historical_observations_assembled"] == 18
    assert audit_data["observations"]["MGLU3"] == 9
    assert audit_data["observations"]["SUZB3"] == 9

    audit_file = settings.data_dir / "audits" / "valuation_4e2_historical_reverse.json"
    assert audit_file.exists()

    content = json.loads(audit_file.read_text(encoding="utf-8"))
    assert content["status"] == "SUCCESS"
    assert content["safety"]["fair_value_produced"] == 0
    assert content["safety"]["price_targets"] == 0
    assert content["safety"]["dcf_executed"] == 0
    assert content["safety"]["buy_or_orders"] == 0

    # Verify DuckDB records
    store = DatabaseStore(settings.data_dir / "audit.duckdb")
    obs_count = store.connection.execute(
        "SELECT COUNT(*) FROM historical_valuation_observations"
    ).fetchone()[0]
    mkt_count = store.connection.execute(
        "SELECT COUNT(*) FROM market_snapshots_pit"
    ).fetchone()[0]
    store.close()

    assert obs_count >= 18
    assert mkt_count >= 18


def test_no_lookahead_in_assembled_observations() -> None:
    settings = Settings()
    audit_file = settings.data_dir / "audits" / "valuation_4e2_historical_reverse.json"
    content = json.loads(audit_file.read_text(encoding="utf-8"))

    rows = content["assembled_observations"]
    mglu3_rows = [r for r in rows if r["ticker"] == "MGLU3"]

    # Verify share count for MGLU3 in 2023 vs 2025 reflect post-agrupamento differences
    mglu3_2023 = [r for r in mglu3_rows if r["reference_date"].startswith("2023")][0]
    mglu3_2025 = [r for r in mglu3_rows if r["reference_date"].startswith("2025")][0]

    assert mglu3_2023["outstanding_shares"] > 6_000_000_000
    assert mglu3_2025["outstanding_shares"] < 1_000_000_000
    assert mglu3_2023["outstanding_shares"] != mglu3_2025["outstanding_shares"]
