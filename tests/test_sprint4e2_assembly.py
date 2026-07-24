"""Integration and adversarial PIT tests for Sprint 4E.2C-D PIT Provenance Closure."""
from datetime import datetime
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


def test_adversarial_pit_provenance_and_no_placeholders() -> None:
    settings = Settings()
    audit_file = settings.data_dir / "audits" / "valuation_4e2_historical_reverse.json"
    content = json.loads(audit_file.read_text(encoding="utf-8"))

    rows = content["assembled_observations"]
    assert len(rows) == 18

    obs_ids = set()

    for row in rows:
        obs_id = row["observation_id"]
        assert obs_id not in obs_ids, f"Duplicate observation_id: {obs_id}"
        obs_ids.add(obs_id)

        val_date = datetime.strptime(row["valuation_date"], "%Y-%m-%d")
        avail_dt = datetime.fromisoformat(row["available_at"].replace("Z", "+00:00"))
        share_avail_dt = datetime.fromisoformat(row["share_available_at"].replace("Z", "+00:00"))

        # 1. Valuation date strictly after document filing availability date
        assert val_date.date() > avail_dt.date(), (
            f"Valuation date {val_date.date()} is not strictly after filing availability {avail_dt.date()}"
        )

        # 2. Share count availability strictly at or before assessment cut
        assessment_cutoff = datetime.combine(val_date.date(), datetime.max.time().replace(microsecond=0))
        assert share_avail_dt.date() <= assessment_cutoff.date(), (
            f"Share availability {share_avail_dt} is after assessment cutoff {assessment_cutoff}"
        )

        # 3. Share reference date is historical and valid
        share_ref_date = datetime.strptime(row["share_reference_date"], "%Y-%m-%d")
        doc_ref_date = datetime.strptime(row["reference_date"], "%Y-%m-%d")
        assert share_ref_date <= doc_ref_date, (
            f"Share reference date {share_ref_date} is after document reference date {doc_ref_date}"
        )

        # 4. Absence of synthetic fallback dates (e.g. 2026-07-19)
        assert "2026-07-19" not in row["share_available_at"], (
            f"Synthetic fallback date 2026-07-19 detected in share_available_at for {row['ticker']}"
        )

    # 5. Reverse valuation contains p25, median, p75 for both companies
    for ticker in ("MGLU3", "SUZB3"):
        rev = content["summary_by_company"][ticker]["reverse_valuation"]
        assert "pe" in rev
        assert rev["pe"]["p25"] is not None
        assert rev["pe"]["median"] is not None
        assert rev["pe"]["p75"] is not None

        assert "ev_ebitda" in rev
        assert rev["ev_ebitda"]["p25"] is not None
        assert rev["ev_ebitda"]["median"] is not None
        assert rev["ev_ebitda"]["p75"] is not None

        assert "p_fcf_proxy" in rev
        assert rev["p_fcf_proxy"]["p25"] is not None
        assert rev["p_fcf_proxy"]["median"] is not None
        assert rev["p_fcf_proxy"]["p75"] is not None


def test_no_lookahead_in_assembled_observations() -> None:
    settings = Settings()
    audit_file = settings.data_dir / "audits" / "valuation_4e2_historical_reverse.json"
    content = json.loads(audit_file.read_text(encoding="utf-8"))

    rows = content["assembled_observations"]
    mglu3_rows = [r for r in rows if r["ticker"] == "MGLU3"]

    mglu3_2023 = [r for r in mglu3_rows if r["reference_date"].startswith("2023")][0]
    mglu3_2025 = [r for r in mglu3_rows if r["reference_date"].startswith("2025")][0]

    assert mglu3_2023["outstanding_shares"] > 6_000_000_000
    assert mglu3_2025["outstanding_shares"] < 1_000_000_000
    assert mglu3_2023["outstanding_shares"] != mglu3_2025["outstanding_shares"]
