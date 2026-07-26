"""Scan the complete persisted B3 universe and produce a diversified research report.

This is deliberately a research ranking, never a BUY/order signal.  Macro factors
are reported separately and are not allowed to dominate the asset ranking: El
Nino/ENSO is only shown when present in the upstream event table.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _metric(metrics: dict[str, Any], name: str) -> float | None:
    value = metrics.get(name)
    return float(value) if isinstance(value, (int, float)) and math.isfinite(float(value)) else None


def _lower_is_better(value: float | None, good: float, bad: float) -> float | None:
    if value is None or value <= 0:
        return None
    return _clamp((bad - value) / (bad - good))


def _higher_is_better(value: float | None, bad: float, good: float) -> float | None:
    if value is None:
        return None
    return _clamp((value - bad) / (good - bad))


def score_asset(record: dict[str, Any], macro: dict[str, Any] | None = None, manual_exposure: dict[str, Any] | None = None) -> dict[str, Any]:
    metrics = record.get("metrics", {})
    components = {
        "valuation_pe": _lower_is_better(_metric(metrics, "pe"), 8, 30),
        "valuation_pvp": _lower_is_better(_metric(metrics, "pvp"), 0.8, 4),
        "valuation_ev_ebitda": _lower_is_better(_metric(metrics, "ev_ebitda"), 5, 18),
        "quality_roe": _higher_is_better(_metric(metrics, "roe"), 0, 0.25),
        "quality_roic": _higher_is_better(_metric(metrics, "roic"), 0, 0.20),
        "balance_net_debt_ebitda": _lower_is_better(_metric(metrics, "net_debt_ebitda"), 0, 5),
        "income_dividend_yield": _higher_is_better(_metric(metrics, "dividend_yield"), 0, 0.12),
        "liquidity": _higher_is_better(math.log10(max(float(record.get("avg_daily_volume_brl") or 0), 1)), 4, 9),
    }
    known = [value for value in components.values() if value is not None]
    # Equal-weight component groups prevent one duplicated valuation metric from
    # overwhelming quality, balance and liquidity.
    groups = {
        "valuation": [components[k] for k in components if k.startswith("valuation_")],
        "quality": [components[k] for k in components if k.startswith("quality_")],
        "balance": [components["balance_net_debt_ebitda"]],
        "income": [components["income_dividend_yield"]],
        "liquidity": [components["liquidity"]],
    }
    group_scores = {name: (sum(v for v in values if v is not None) / len([v for v in values if v is not None]) if any(v is not None for v in values) else None) for name, values in groups.items()}
    usable_groups = [value for value in group_scores.values() if value is not None]
    score = sum(usable_groups) / len(usable_groups) if usable_groups else 0.0
    completeness = len(known) / len(components)
    macro = macro or {}
    manual_exposure = manual_exposure or {}
    macro_impact = macro.get("net_company_impact")
    macro_confidence = macro.get("confidence")
    macro_score = None
    if isinstance(macro_impact, (int, float)) and isinstance(macro_confidence, (int, float)):
        # The causal score remains a directional/context score, never a return
        # forecast.  Evidence confidence attenuates it and opposing paths are
        # surfaced rather than averaged away.
        macro_score = _clamp((0.5 + 0.5 * float(macro_impact)) * float(macro_confidence))
    integrated_score = score if macro_score is None else (0.65 * score + 0.35 * macro_score)
    if macro_score is None:
        integrated_score *= 0.75  # unknown causal coverage is visible as a penalty
    return {
        "ticker": record["ticker"],
        "asset_class": record.get("asset_class"),
        "sector": record.get("sector"),
        "price": record.get("price"),
        "as_of": record.get("as_of"),
        "research_score": round(score, 4),
        "macro_score": None if macro_score is None else round(macro_score, 4),
        "integrated_score": round(integrated_score, 4),
        "macro_status": macro.get("status", "NO_CAUSAL_COVERAGE"),
        "macro_factors": macro.get("factors", []),
        "macro_missing_exposures": macro.get("missing_exposures", []),
        "macro_conflict_ratio": macro.get("conflict_ratio"),
        "manual_exposure_status": "CATALOGED" if manual_exposure else "NOT_CATALOGED",
        "manual_macro_channels": manual_exposure.get("macro_channels", []),
        "manual_exposure_observations": manual_exposure.get("observations", []),
        "data_completeness": round(completeness, 4),
        "group_scores": {key: None if value is None else round(value, 4) for key, value in group_scores.items()},
        "metrics": metrics,
        "decision": "RESEARCH_WATCHLIST_ONLY",
        "buy_signal": False,
    }


def build_universe_report(records: list[dict[str, Any]], factor_counts: dict[str, int], as_of: str, macro_by_ticker: dict[str, dict[str, Any]] | None = None, manual_by_ticker: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    macro_by_ticker = macro_by_ticker or {}
    manual_by_ticker = manual_by_ticker or {}
    ranked = sorted((score_asset(item, macro_by_ticker.get(item["ticker"]), manual_by_ticker.get(item["ticker"])) for item in records), key=lambda x: (x["integrated_score"] * x["data_completeness"], x["integrated_score"]), reverse=True)
    total_factor_events = sum(factor_counts.values())
    factor_summary = [
        {"factor": factor, "event_count": count, "share": round(count / total_factor_events, 4) if total_factor_events else 0}
        for factor, count in sorted(factor_counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    return {
        "report_type": "FULL_UNIVERSE_RESEARCH_SCAN",
        "as_of_timestamp": as_of,
        "assets_scanned": len(records),
        "assets_ranked": len(ranked),
        "assets_with_causal_candidate": sum(1 for item in ranked if item["macro_score"] is not None),
        "assets_with_manual_exposure_catalog": sum(1 for item in ranked if item["manual_exposure_status"] == "CATALOGED"),
        "ranking_policy": "equal_weighted_groups; macro factors are context only",
        "macro_factor_summary": factor_summary,
        "factor_dominance_guard": {"max_single_factor_weight": 1.0, "enso_is_not_special_cased": True},
        "results": ranked,
        "safety": {"buy_signals": 0, "orders": 0, "valuation": "DESCRIPTIVE_ONLY"},
    }


def main() -> None:
    settings = Settings()
    db = DatabaseStore(settings.data_dir / "audit.duckdb")
    cutoff = datetime.now(timezone.utc)
    rows = db.connection.execute(
        """SELECT ticker, asset_class, as_of, price, avg_daily_volume_brl, sector, metrics_json
           FROM asset_snapshots WHERE as_of <= ?
           QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY as_of DESC) = 1""", [cutoff]
    ).fetchall()
    records = [{"ticker": r[0], "asset_class": r[1], "as_of": r[2].isoformat(), "price": r[3], "avg_daily_volume_brl": r[4], "sector": r[5], "metrics": json.loads(r[6] or "{}")} for r in rows]
    factors = db.connection.execute("SELECT event_type, COUNT(*) FROM macro_event_candidates WHERE detected_at <= ? GROUP BY event_type", [cutoff]).fetchall()
    macro_by_ticker: dict[str, dict[str, Any]] = {}
    manual_path = settings.data_dir / "audits" / "manual_asset_exposure_review_20260726.json"
    manual_by_ticker: dict[str, dict[str, Any]] = {}
    if manual_path.exists():
        manual_by_ticker = {item["ticker"]: item for item in json.loads(manual_path.read_text(encoding="utf-8")).get("assets", [])}
    candidate_rows = db.connection.execute("""SELECT ticker, impact_payload, confidence, conflict_ratio, missing_exposures, status
        FROM company_impact_candidates
        QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY
            CASE WHEN status = 'WATCH' THEN 0 ELSE 1 END,
            as_of_timestamp DESC, created_at DESC) = 1""").fetchall()
    for ticker, payload, confidence, conflict_ratio, missing, status in candidate_rows:
        item = json.loads(payload or "{}")
        contributions = item.get("factor_contributions", [])
        macro_by_ticker[str(ticker)] = {
            "net_company_impact": item.get("net_company_impact"),
            "confidence": confidence,
            "conflict_ratio": conflict_ratio,
            "missing_exposures": json.loads(missing or "[]"),
            "status": str(status),
            "factors": sorted({str(c.get("factor")) for c in contributions if c.get("factor")}),
        }
    report = build_universe_report(records, {str(row[0]): int(row[1]) for row in factors}, cutoff.isoformat(), macro_by_ticker, manual_by_ticker)
    requested = sorted(manual_by_ticker)
    universe_tickers = {item["ticker"] for item in records}
    report["manual_catalog_assets"] = [{"ticker": ticker, "asset_type": manual_by_ticker[ticker].get("asset_type"), "macro_channels": manual_by_ticker[ticker].get("macro_channels", []), "observations": manual_by_ticker[ticker].get("observations", []), "in_universe": ticker in universe_tickers} for ticker in requested]
    report["requested_assets_not_in_universe"] = [ticker for ticker in requested if ticker not in universe_tickers]
    out_json = settings.data_dir / "audits" / "universe_research_scan.json"
    out_md = settings.data_dir / "audits" / "universe_research_scan.md"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# Full B3 universe research scan\n", f"As of: `{report['as_of_timestamp']}`", f"\nAssets scanned: **{report['assets_scanned']}**", f"Causal candidates available: **{report['assets_with_causal_candidate']}**", f"Manual exposure catalogs: **{report['assets_with_manual_exposure_catalog']}**", "\n## Macro context (not a single-factor ranking)\n"]
    lines += [f"- `{item['factor']}`: {item['event_count']} events ({item['share']:.1%})" for item in report["macro_factor_summary"]]
    lines += ["\n## Top 25 integrated research watchlist\n", "| Rank | Ticker | Integrated | Fundamental | Macro | Macro status | Completeness |", "|---:|---|---:|---:|---:|---|---:|"]
    lines += [f"| {idx} | {item['ticker']} | {item['integrated_score']:.4f} | {item['research_score']:.4f} | {item['macro_score'] if item['macro_score'] is not None else 'UNKNOWN'} | {item['macro_status']} | {item['data_completeness']:.1%} |" for idx, item in enumerate(report["results"][:25], 1)]
    lines += ["\n## Assets with persisted causal coverage\n", "| Ticker | Macro score | Status | Factors | Missing exposures |", "|---|---:|---|---|---|"]
    covered = [item for item in report["results"] if item["macro_score"] is not None]
    lines += [f"| {item['ticker']} | {item['macro_score']:.4f} | {item['macro_status']} | {', '.join(item['macro_factors']) or 'UNKNOWN'} | {', '.join(item['macro_missing_exposures']) or 'none'} |" for item in covered]
    lines += ["\n## Manual catalogs requested\n", "| Ticker | In current universe | Channels |", "|---|---|---|"]
    lines += [f"| {item['ticker']} | {'yes' if item['in_universe'] else 'no'} | {', '.join(item['macro_channels'])} |" for item in report["manual_catalog_assets"]]
    lines += ["\nThis is a comparative research report, not BUY advice, valuation or order execution."]
    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"assets_scanned": report["assets_scanned"], "json": str(out_json), "markdown": str(out_md)}))


if __name__ == "__main__":
    main()
