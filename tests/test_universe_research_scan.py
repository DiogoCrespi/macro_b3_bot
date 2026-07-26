from scripts.run_universe_research_scan import build_universe_report, score_asset


def _asset(ticker: str, pe: float, roe: float) -> dict:
    return {
        "ticker": ticker, "asset_class": "stock", "sector": "test", "price": 10,
        "avg_daily_volume_brl": 1_000_000, "as_of": "2026-07-26T00:00:00+00:00",
        "metrics": {"pe": pe, "pvp": 1, "ev_ebitda": 8, "roe": roe, "roic": .1,
                     "net_debt_ebitda": 1, "dividend_yield": .05},
    }


def test_score_is_research_only_and_has_completeness() -> None:
    result = score_asset(_asset("AAA3", 8, .2))
    assert result["decision"] == "RESEARCH_WATCHLIST_ONLY"
    assert result["buy_signal"] is False
    assert result["data_completeness"] > .5


def test_universe_report_scans_all_and_keeps_macro_context_separate() -> None:
    report = build_universe_report([_asset("AAA3", 8, .2), _asset("BBB3", 30, .01)], {"FX": 4, "ENSO": 1}, "2026-07-26T00:00:00+00:00")
    assert report["assets_scanned"] == 2
    assert report["macro_factor_summary"][0]["factor"] == "FX"
    assert report["safety"]["buy_signals"] == 0
    assert all(item["decision"] == "RESEARCH_WATCHLIST_ONLY" for item in report["results"])
