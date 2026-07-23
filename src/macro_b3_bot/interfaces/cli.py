from __future__ import annotations

import sys
# Fix encoding no console Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from datetime import datetime, timezone
import json
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table

from macro_b3_bot.application.pipeline import DecisionPipeline
from macro_b3_bot.config import Settings
from macro_b3_bot.domain.models import AssetClass, OpportunityAssessment
from macro_b3_bot.tools.reuse_discovery import discover, write_manifest

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command("validate-config")
def validate_config() -> None:
    settings = Settings()
    rows = {
        "Advanced_Btc_Bot": settings.advanced_btc_bot_root.exists(),
        "b3_screener": settings.b3_screener_root.exists(),
        "b3_screener export": settings.b3_screener_export.exists(),
        "MiroFish enabled": settings.mirofish_enabled,
    }
    table = Table(title="Configuration validation")
    table.add_column("Item")
    table.add_column("Status")
    for key, value in rows.items():
        table.add_row(key, "OK" if value else "NOT READY")
    console.print(table)


@app.command("discover-reuse")
def discover_reuse(
    write: bool = typer.Option(False, "--write-manifest", help="Write data/reuse_manifest.json"),
) -> None:
    settings = Settings()
    candidates = discover("Advanced_Btc_Bot", settings.advanced_btc_bot_root)
    candidates += discover("b3_screener", settings.b3_screener_root)
    table = Table(title=f"Reusable candidates ({len(candidates)})")
    table.add_column("Repository")
    table.add_column("Capability")
    table.add_column("Path")
    for item in candidates[:100]:
        table.add_row(item.repository, item.capability, item.path)
    console.print(table)
    if write:
        output = settings.data_dir / "reuse_manifest.json"
        write_manifest(candidates, output)
        console.print(f"Manifest written to {output}")


@app.command("ingest-b3")
def ingest_b3() -> None:
    from scripts.ingest_b3 import run_ingest_b3
    run_ingest_b3()


@app.command("ingest-bcb")
def ingest_bcb() -> None:
    import asyncio
    from scripts.ingest_bcb import main as run_bcb
    asyncio.run(run_bcb())


@app.command("ingest-cvm")
def ingest_cvm() -> None:
    import asyncio
    from scripts.ingest_cvm import main as run_cvm
    asyncio.run(run_cvm())


@app.command("audit-cvm")
def audit_cvm() -> None:
    from scripts.audit_cvm import run_audit
    run_audit()


@app.command("ingest-cvm-ipe-index")
def ingest_cvm_ipe_index() -> None:
    import asyncio
    from scripts.ingest_cvm_ipe import main as run_ipe
    asyncio.run(run_ipe())


@app.command("analyze-ipe-queue")
def analyze_ipe_queue() -> None:
    from scripts.analyze_ipe_queue import analyze_queue
    analyze_queue()


@app.command("process-cvm-ipe")
def process_cvm_ipe(limit: int = 500, min_priority: float = 0.65) -> None:
    import asyncio
    from scripts.process_cvm_ipe import main as run_proc
    asyncio.run(run_proc(limit=limit, min_priority=min_priority))


@app.command("demo")
def demo() -> None:
    settings = Settings()
    now = datetime.now(timezone.utc)
    assessments = [
        OpportunityAssessment(
            ticker="EXEMPLO3",
            asset_class=AssetClass.STOCK,
            event_id="demo_event",
            evidence_quality=0.82,
            scenario_probability=0.68,
            causal_strength=0.78,
            company_exposure=0.80,
            fundamental_quality=0.72,
            valuation_attractiveness=0.70,
            entry_timing=0.64,
            portfolio_fit=0.75,
            penalties={"uncalibrated_scenario": 0.05},
            confidence=0.69,
            expected_upside=0.28,
            expected_downside=-0.13,
            independent_evidence_count=4,
            has_primary_source=True,
            thesis=["synthetic demonstration only; replace with real evidence"],
            invalidators=["scenario probability below 45%", "valuation rerates above fair value"],
            metadata={"generated_at": now.isoformat()},
        ),
        OpportunityAssessment(
            ticker="NARRATIVA4",
            asset_class=AssetClass.STOCK,
            event_id="demo_event",
            evidence_quality=0.42,
            scenario_probability=0.74,
            causal_strength=0.45,
            company_exposure=0.55,
            fundamental_quality=0.65,
            valuation_attractiveness=0.60,
            entry_timing=0.70,
            portfolio_fit=0.70,
            penalties={"unconfirmed_youtube_narrative": 0.20},
            confidence=0.45,
            expected_upside=0.30,
            expected_downside=-0.20,
            independent_evidence_count=1,
            has_primary_source=False,
            thesis=["popular narrative without sufficient confirmation"],
        ),
    ]
    results = DecisionPipeline(settings).evaluate(assessments)
    console.print_json(json.dumps([item.model_dump(mode="json") for item in results], ensure_ascii=False))


@app.command("map-event-tickers")
def map_event_tickers() -> None:
    from macro_b3_bot.application.event_ticker_mapper import EventTickerMapper
    settings = Settings()
    mapper = EventTickerMapper(settings)
    res = mapper.run_mapping()
    console.print("[green]Ticker mapping completed successfully![/green]")
    console.print(res)

@app.command("ingest-event-market-history")
def ingest_event_market_history(
    start: str = typer.Option("2025-01-01", "--start"),
    end: str = typer.Option("2026-07-21", "--end")
) -> None:
    from macro_b3_bot.application.calculate_event_returns import EventReturnsCalculator
    from datetime import date
    settings = Settings()
    calc = EventReturnsCalculator(settings)
    store = calc.db_path
    from macro_b3_bot.infrastructure.store import DatabaseStore
    db_store = DatabaseStore(store)
    db_store._init_tables()
    tickers = db_store.connection.execute(
        "SELECT DISTINCT primary_ticker FROM event_market_mappings WHERE primary_ticker != 'UNKNOWN'"
    ).fetchall()
    ticker_list = [t[0] for t in tickers]
    console.print(f"Baixando preços para {len(ticker_list)} tickers de {start} a {end}...")
    for ticker in ticker_list:
        calc._ingest_prices_to_db(db_store, ticker, date.fromisoformat(start), date.fromisoformat(end))
    calc._ingest_prices_to_db(db_store, "^BVSP", date.fromisoformat(start), date.fromisoformat(end))
    db_store.close()
    console.print("[green]Market history ingestion completed successfully![/green]")

@app.command("calculate-event-reactions")
def calculate_event_reactions() -> None:
    from macro_b3_bot.application.calculate_event_returns import EventReturnsCalculator
    settings = Settings()
    calc = EventReturnsCalculator(settings)
    res = calc.run_calculator()
    console.print("[green]Event returns and CAR calculation completed successfully![/green]")
    console.print(res)

@app.command("audit-event-reactions")
def audit_event_reactions() -> None:
    from macro_b3_bot.application.significance_bootstrap import SignificanceBootstrapper
    from macro_b3_bot.application.recalibrate_scores import ScoreRecalibrator
    from macro_b3_bot.application.audit_event_reactions import EventReactionsAuditor
    settings = Settings()
    
    boot = SignificanceBootstrapper(settings)
    res_boot = boot.run_bootstrap()
    
    recal = ScoreRecalibrator(settings)
    res_recal = recal.run_recalibration()
    
    auditor = EventReactionsAuditor(settings)
    res_audit = auditor.run_export()
    
    console.print("[green]Audit, Bootstrap, and Recalibration completed successfully![/green]")
    console.print("Bootstrap:", res_boot)
    console.print("Recalibration:", res_recal)
    console.print("Audit Export:", res_audit)

@app.command("export-claims-audit")
def export_claims_audit() -> None:
    from macro_b3_bot.application.audit_event_reactions import EventReactionsAuditor
    settings = Settings()
    auditor = EventReactionsAuditor(settings)
    res = auditor.run_export()
    console.print(f"[green]Claims audit exported to {res['claims_path']}[/green]")

@app.command("validate-sprint3b")
def validate_sprint3b() -> None:
    from macro_b3_bot.infrastructure.store import DatabaseStore
    settings = Settings()
    store = DatabaseStore(settings.data_dir / "audit.duckdb")
    
    mappings_count = store.connection.execute("SELECT COUNT(*) FROM event_market_mappings").fetchone()[0]
    prices_count = store.connection.execute("SELECT COUNT(*) FROM market_prices").fetchone()[0]
    events_count = store.connection.execute("SELECT COUNT(*) FROM effective_market_events").fetchone()[0]
    outcomes_count = store.connection.execute("SELECT COUNT(*) FROM event_market_outcomes").fetchone()[0]
    confirmed_count = store.connection.execute("SELECT COUNT(*) FROM event_market_outcomes WHERE outcome_label = 'CONFIRMED'").fetchone()[0]
    
    table = Table(title="Sprint 3B Database Metrics")
    table.add_column("Metric")
    table.add_column("Count")
    table.add_row("Event Market Mappings", str(mappings_count))
    table.add_row("Cached Market Prices", str(prices_count))
    table.add_row("Effective Market Events", str(events_count))
    table.add_row("Event Market Outcomes", str(outcomes_count))
    table.add_row("CONFIRMED Events", str(confirmed_count))
    
    console.print(table)
    store.close()

@app.command("run-event-study")
def run_event_study(
    start: str = typer.Option("2025-01-01", "--start"),
    end: str = typer.Option("2026-07-21", "--end"),
    bootstrap_iterations: int = typer.Option(2000, "--bootstrap-iterations"),
    seed: int = typer.Option(42, "--seed")
) -> None:
    console.print("\n[bold blue]=== EXECUNTANDO PIPELINE UNIFICADO DO SPRINT 3B (EVENT STUDY) ===[/bold blue]")
    
    console.print("\n[bold]Passo 0/5: Consolidação de Candidatos a Eventos...[/bold]")
    from macro_b3_bot.application.consolidate_events import EventConsolidator
    settings = Settings()
    consolidator = EventConsolidator(settings)
    res_cons = consolidator.consolidate(limit=5000)
    console.print(f"  Consolidação: {res_cons}")

    console.print("\n[bold]Passo 1/5: Mapeamento de Tickers...[/bold]")
    map_event_tickers()
    
    console.print("\n[bold]Passo 2/5: Ingestão de Histórico de Preços...[/bold]")
    ingest_event_market_history(start=start, end=end)
    
    console.print("\n[bold]Passo 3/5: Cálculo de Retornos e CAR...[/bold]")
    calculate_event_reactions()
    
    console.print("\n[bold]Passo 4/5: Bootstrap e Recalibração de Scores...[/bold]")
    from macro_b3_bot.application.significance_bootstrap import SignificanceBootstrapper
    from macro_b3_bot.application.recalibrate_scores import ScoreRecalibrator
    from macro_b3_bot.application.audit_event_reactions import EventReactionsAuditor
    settings = Settings()

    boot = SignificanceBootstrapper(settings, iterations=bootstrap_iterations, seed=seed)
    res_boot = boot.run_bootstrap()
    console.print("  Bootstrap:", res_boot)

    recal = ScoreRecalibrator(settings)
    res_recal = recal.run_recalibration()
    console.print("  Recalibração:", res_recal)

    console.print("\n[bold]Passo 5/5: Exportando Auditorias em CSV...[/bold]")
    auditor = EventReactionsAuditor(settings)
    res_audit = auditor.run_export()
    console.print("  Audit Export:", res_audit)

    console.print("\n[bold green]✓ Pipeline do Sprint 3B executado com sucesso![/bold green]")
    validate_sprint3b()


# ─────────────────────────────────────────────────────────────────────────────
# Sprint 4A — Global Macro Engine commands
# ─────────────────────────────────────────────────────────────────────────────

@app.command("ingest-global-macro")
def ingest_global_macro(
    sources: str = typer.Option("fred,eia,noaa", "--sources", help="Comma-separated sources: fred,eia,noaa,bcb"),
    incremental: bool = typer.Option(True, "--incremental/--full", help="Incremental (since last stored) vs full history"),
) -> None:
    """Ingest macro series from FRED, EIA, and NOAA into the DuckDB store."""
    from macro_b3_bot.application.ingest_global_macro import GlobalMacroIngester
    settings = Settings()
    sources_list = [s.strip() for s in sources.split(",")]
    console.print(f"\n[bold]Ingestão de Macro Global — fontes: {sources_list}[/bold]")
    ingester = GlobalMacroIngester(settings, incremental=incremental)
    result = ingester.run(sources=sources_list)
    ingester.close()

    table = Table(title="Macro Ingestion Result")
    table.add_column("Metric")
    table.add_column("Value")
    for k, v in result.items():
        table.add_row(str(k), str(v))
    console.print(table)

    if result.get("failed_series"):
        console.print(f"[yellow]⚠ Failed series: {result['failed_series']}[/yellow]")
    else:
        console.print("[bold green]✓ Ingestion completed successfully[/bold green]")


@app.command("detect-macro-events")
def detect_macro_events(
    since: str = typer.Option(None, "--since", help="Start date YYYY-MM-DD (default: 90 days ago)"),
) -> None:
    """Detect macro surprises and generate MacroEventCandidates from stored releases."""
    from datetime import date, timedelta
    from macro_b3_bot.application.build_macro_events import MacroEventBuilder
    from macro_b3_bot.application.detect_regime_changes import RegimeDetector
    from macro_b3_bot.infrastructure.store import DatabaseStore
    import uuid

    settings = Settings()
    db_path = settings.data_dir / "audit.duckdb"
    store = DatabaseStore(db_path)
    run_id = str(uuid.uuid4())

    since_date = date.fromisoformat(since) if since else (date.today() - timedelta(days=90))

    console.print(f"\n[bold]Detectando eventos macro desde {since_date}...[/bold]")

    # Step 1: Regime snapshot
    detector = RegimeDetector(store, run_id)
    snap = detector.detect_and_snapshot()
    console.print(f"  Regime atual: [cyan]{snap['regime_label']}[/cyan] (confiança={snap['confidence']:.2f})")

    # Step 2: Build event candidates
    builder = MacroEventBuilder(store, run_id)
    result = builder.process_since(since_date)
    store.close()

    table = Table(title="Macro Event Detection")
    table.add_column("Metric")
    table.add_column("Count")
    for k, v in result.items():
        table.add_row(str(k), str(v))
    console.print(table)


@app.command("audit-global-macro")
def audit_global_macro() -> None:
    """Audit macro releases, available_at anomalies, and data vintages in DuckDB."""
    from macro_b3_bot.infrastructure.store import DatabaseStore

    settings = Settings()
    db_path = settings.data_dir / "audit.duckdb"
    store = DatabaseStore(db_path)

    # 1. Summary of releases per source and indicator
    rows_summary = store.connection.execute(
        """
        SELECT source, indicator, COUNT(*) AS observations,
               MIN(reference_date) AS min_ref, MAX(reference_date) AS max_ref,
               COUNT(DISTINCT record_checksum) AS versions
        FROM macro_releases
        GROUP BY source, indicator
        ORDER BY source, indicator
        """
    ).fetchall()

    table1 = Table(title=f"Macro Releases Summary ({len(rows_summary)} series)")
    table1.add_column("Source")
    table1.add_column("Indicator")
    table1.add_column("Observations")
    table1.add_column("Min RefDate")
    table1.add_column("Max RefDate")
    table1.add_column("Distinct Versions")
    for r in rows_summary:
        table1.add_row(*[str(x) for x in r])
    console.print(table1)

    # 2a. Chronological anomaly check: available_at < reference_date
    chrono_anomalies = store.connection.execute(
        """
        SELECT COUNT(*)
        FROM macro_releases
        WHERE available_at < reference_date
        """
    ).fetchone()[0]

    if chrono_anomalies > 0:
        console.print(f"[bold red]⚠ Chronology warning: {chrono_anomalies} records have available_at < reference_date[/bold red]")
    else:
        console.print("[bold green]✓ Zero chronological anomalies detected (available_at >= reference_date)[/bold green]")

    # 2b. True Look-ahead anomaly check: evidence available_at > event detected_at
    lookahead_anomalies = store.connection.execute(
        """
        SELECT COUNT(*)
        FROM macro_event_evidence_links l
        JOIN macro_releases r ON r.release_id = l.release_id
        JOIN macro_event_candidates e ON e.event_id = l.event_id
        WHERE r.available_at > e.detected_at
        """
    ).fetchone()[0]

    if lookahead_anomalies > 0:
        console.print(f"[bold red]⚠ Look-ahead violation: {lookahead_anomalies} evidence links have available_at > event detected_at[/bold red]")
    else:
        console.print("[bold green]✓ Zero look-ahead violations detected (evidence available_at <= event detected_at)[/bold green]")

    # 3. Vintages check: multiple versions for same reference_date
    vintages_rows = store.connection.execute(
        """
        SELECT source, series_code, reference_date, COUNT(*) AS versions
        FROM macro_data_vintages
        GROUP BY source, series_code, reference_date
        HAVING COUNT(*) > 1
        ORDER BY source, series_code, reference_date
        """
    ).fetchall()

    table2 = Table(title=f"Macro Data Vintages ({len(vintages_rows)} multiple versions)")
    table2.add_column("Source")
    table2.add_column("Indicator")
    table2.add_column("RefDate")
    table2.add_column("Version Count")
    for r in vintages_rows:
        table2.add_row(*[str(x) for x in r])
    console.print(table2)

    store.close()


@app.command("audit-macro-events")
def audit_macro_events(
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Filter candidate events by specific ingestion_run_id"),
) -> None:
    """Print a summary of MacroEventCandidates in the store scoped by run_id."""
    from macro_b3_bot.config import Settings
    from macro_b3_bot.infrastructure.store import DatabaseStore

    settings = Settings()
    db_path = settings.data_dir / "audit.duckdb"
    store = DatabaseStore(db_path)

    target_run_id = run_id or store.get_latest_macro_event_run_id()

    if target_run_id:
        rows = store.connection.execute(
            """
            SELECT event_type, indicator, reference_date, direction, status,
                   round(surprise_score, 3), round(novelty_score, 3),
                   round(regime_shift_score, 3), round(data_quality_score, 3),
                   score_breakdown
            FROM macro_event_candidates
            WHERE ingestion_run_id = ?
            ORDER BY detected_at DESC
            """,
            [target_run_id]
        ).fetchall()
        if not rows:
            rows = store.connection.execute(
                """
                SELECT event_type, indicator, reference_date, direction, status,
                       round(surprise_score, 3), round(novelty_score, 3),
                       round(regime_shift_score, 3), round(data_quality_score, 3),
                       score_breakdown
                FROM macro_event_candidates
                ORDER BY detected_at DESC
                """
            ).fetchall()
    else:
        rows = store.connection.execute(
            """
            SELECT event_type, indicator, reference_date, direction, status,
                   round(surprise_score, 3), round(novelty_score, 3),
                   round(regime_shift_score, 3), round(data_quality_score, 3),
                   score_breakdown
            FROM macro_event_candidates
            ORDER BY detected_at DESC
            """
        ).fetchall()

    import subprocess
    try:
        git_hash = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        git_hash = "unknown"

    target_run_id = run_id
    console.print("[bold cyan]=== MACRO EVENT CANDIDATES AUDIT ===[/bold cyan]")
    console.print(f"Git Commit  : {git_hash}")
    console.print(f"DB Path     : {db_path}")
    console.print(f"Run ID      : {target_run_id or 'ALL_RUNS'}")
    console.print(f"Evaluated   : {len(rows)} candidates\n")

    table = Table(title=f"MacroEventCandidates ({len(rows)} total for run {target_run_id or 'ALL'})")
    for col in ["EventType", "Indicator", "RefDate", "Direction", "Status",
                "Surprise", "Novelty", "RegimeShift", "DataQuality"]:
        table.add_column(col)
    for r in rows:
        table.add_row(*[str(x) for x in r[:9]])
    console.print(table)

    # Breakdown by status
    approved = sum(1 for r in rows if r[4] == "MACRO_EVENT_APPROVED")
    watch = sum(1 for r in rows if r[4] == "MACRO_EVENT_WATCH")
    rejected = sum(1 for r in rows if r[4] == "MACRO_EVENT_REJECTED")
    console.print(f"\nApproved={approved} | Watch={watch} | Rejected={rejected}")

    # Percentiles table
    scores = {
        "Surprise": [float(r[5]) for r in rows if r[5] is not None],
        "Novelty": [float(r[6]) for r in rows if r[6] is not None],
        "RegimeShift": [float(r[7]) for r in rows if r[7] is not None],
        "DataQuality": [float(r[8]) for r in rows if r[8] is not None],
    }

    def calc_percentiles(vals: list[float]) -> list[float]:
        if not vals:
            return [0.0] * 6
        s = sorted(vals)
        n = len(s)
        def p(pct: float) -> float:
            idx = int(pct * (n - 1))
            return round(s[idx], 4)
        return [p(0.10), p(0.25), p(0.50), p(0.75), p(0.90), round(max(s), 4)]

    p_table = Table(title="Score Percentiles (P10, P25, Median, P75, P90, Max)")
    p_table.add_column("Score Component")
    p_table.add_column("P10")
    p_table.add_column("P25")
    p_table.add_column("Median")
    p_table.add_column("P75")
    p_table.add_column("P90")
    p_table.add_column("Max")

    for name, vals in scores.items():
        p_table.add_row(name, *[str(x) for x in calc_percentiles(vals)])
    console.print(p_table)

    # Rejection Reasons Breakdown
    import json
    from collections import Counter
    store_conn = DatabaseStore(db_path)
    sb_rows = store_conn.connection.execute(
        "SELECT surprise_score, novelty_score, regime_shift_score, data_quality_score, score_breakdown FROM macro_event_candidates WHERE status = 'MACRO_EVENT_REJECTED'"
    ).fetchall()
    store_conn.close()

    reason_counter: Counter[str] = Counter()
    for s_sc, n_sc, r_sc, q_sc, sb_str in sb_rows:
        conditions = []
        if sb_str:
            try:
                sb = json.loads(sb_str) if isinstance(sb_str, str) else sb_str
                conditions = sb.get("failed_conditions", [])
            except Exception:
                pass
        if not conditions:
            if (s_sc or 0.0) < 0.60 and (r_sc or 0.0) < 0.65:
                conditions.append("SURPRISE_AND_REGIME_BELOW_THRESHOLD")
            if (n_sc or 0.0) < 0.50:
                conditions.append("NOVELTY_BELOW_THRESHOLD")
            if (q_sc or 0.0) < 0.80:
                conditions.append("QUALITY_BELOW_THRESHOLD")
        for cond in conditions:
            reason_counter[cond] += 1

    r_table = Table(title="Rejection Reason Breakdown (failed_conditions)")
    r_table.add_column("Failed Condition")
    r_table.add_column("Count")
    for cond, count in reason_counter.most_common():
        r_table.add_row(cond, str(count))
    console.print(r_table)

    console.print("[bold]BUY habilitado: NÃO[/bold]")


@app.command("evaluate-sector-impacts")
def evaluate_sector_impacts(
    since: Optional[str] = typer.Option(None, "--since", help="Start date (YYYY-MM-DD) for candidate evaluation"),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Ingestion run ID"),
    sector_run_id: Optional[str] = typer.Option(None, "--sector-run-id", help="Sector evaluation run ID"),
    as_of: Optional[str] = typer.Option(None, "--as-of", help="Point-in-time cutoff (ISO-8601)"),
) -> None:
    """Propagate approved macro events through the Causal Graph to evaluate B3 sector impacts."""
    from datetime import date, timedelta
    from uuid import uuid4
    from macro_b3_bot.application.evaluate_sector_impacts import CausalGraphEngine
    from macro_b3_bot.infrastructure.store import DatabaseStore

    settings = Settings()
    db_path = settings.data_dir / "audit.duckdb"
    store = DatabaseStore(db_path)

    since_date = date.fromisoformat(since) if since else (date.today() - timedelta(days=180))

    console.print("\n[bold cyan]=== SPRINT 4B: SECTOR IMPACT EVALUATION ===[/bold cyan]")
    console.print(f"Avaliando impactos setoriais desde {since_date}...")

    target_run_id = run_id or store.get_latest_macro_event_run_id() or "run_sector_eval"
    target_sector_run_id = sector_run_id or f"sector_{uuid4()}"
    engine = CausalGraphEngine(store, target_sector_run_id)
    as_of_timestamp = datetime.fromisoformat(as_of) if as_of else datetime.now(timezone.utc)
    summary = engine.evaluate_events_window(
        since_date=since_date, as_of_timestamp=as_of_timestamp, event_run_id=target_run_id
    )
    store.close()

    table = Table(title="Sector Impact Candidates Summary")
    table.add_column("Métrica")
    table.add_column("Contagem")
    for k, v in summary.items():
        table.add_row(str(k), str(v))
    console.print(table)
    console.print("[bold]BUY / Seleção de Ticker: 100% DESABILITADO[/bold]")


@app.command("audit-sector-impacts")
def audit_sector_impacts(
    status: Optional[str] = typer.Option(None, "--status", help="Filter sector candidates by status"),
) -> None:
    """Print an audit summary of evaluated SectorImpactCandidates in DuckDB."""
    from macro_b3_bot.infrastructure.store import DatabaseStore

    settings = Settings()
    db_path = settings.data_dir / "audit.duckdb"
    store = DatabaseStore(db_path)

    candidates = store.get_sector_impact_candidates(status=status)
    store.close()

    console.print("\n[bold cyan]=== AUDITORIA DE IMPACTOS SETORIAIS (SPRINT 4B) ===[/bold cyan]")
    console.print(f"Total de Candidatos Avaliados: {len(candidates)}\n")

    table = Table(title=f"SectorImpactCandidates ({len(candidates)} total)")
    table.add_column("EventId")
    table.add_column("EventType")
    table.add_column("Setor")
    table.add_column("Direção")
    table.add_column("Score")
    table.add_column("Confiança")
    table.add_column("Conflito")
    table.add_column("Status")

    for c in candidates:
        table.add_row(
            str(c["event_id"])[:10],
            str(c["event_type"]),
            str(c["sector"]),
            str(c["direction"]),
            f"{float(c['impact_score']):.3f}",
            f"{float(c['confidence']):.3f}",
            "SIM" if c.get("conflict_detected") else "NÃO",
            str(c["status"]),
        )
    console.print(table)

    approved = sum(1 for c in candidates if c["status"] == "SECTOR_IMPACT_APPROVED")
    watch = sum(1 for c in candidates if c["status"] == "SECTOR_IMPACT_WATCH")
    rejected = sum(1 for c in candidates if c["status"] == "SECTOR_IMPACT_REJECTED")
    console.print(f"\nAprovados={approved} | Watch={watch} | Rejeitados={rejected}")
    console.print("[bold]BUY / Ordens: PERMANENTEMENTE BLOQUEADOS[/bold]")


@app.command("build-company-exposures")
def build_company_exposures(
    as_of: str = typer.Option(..., "--as-of", help="Point-in-time cutoff (ISO-8601)"),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Exposure build run ID"),
    source_run_id: Optional[str] = typer.Option(
        None, "--source-run-id", help="Audited exposure-document selection run"
    ),
) -> None:
    """Build evidenced CVM exposure snapshots for the 15-company pilot."""
    from uuid import uuid4

    from macro_b3_bot.application.build_company_exposures import CompanyExposureBuilder
    from macro_b3_bot.infrastructure.store import DatabaseStore

    settings = Settings()
    store = DatabaseStore(settings.data_dir / "audit.duckdb")
    target_run_id = run_id or f"exposure_{uuid4()}"
    summary = CompanyExposureBuilder(
        store, target_run_id, source_selection_run_id=source_run_id
    ).build_pilot(datetime.fromisoformat(as_of))
    store.close()

    table = Table(title="Sprint 4C.3 — Company Exposure PIT Pilot")
    table.add_column("Métrica")
    table.add_column("Valor")
    for key in (
        "run_id", "as_of_timestamp", "pilot_requested", "snapshots_built",
        "missing_mapping", "missing_point_in_time_document",
    ):
        table.add_row(key, str(summary[key]))
    console.print(table)
    console.print("[bold]Ausências permanecem NULL/UNKNOWN; valuation e BUY desabilitados.[/bold]")


@app.command("ingest-company-exposure-documents")
def ingest_company_exposure_documents(
    as_of: str = typer.Option(..., "--as-of", help="Point-in-time cutoff"),
    per_family: int = typer.Option(1, "--per-family", min=1, max=3),
) -> None:
    """Download the latest PIT document in each relevant information family."""
    import asyncio

    from macro_b3_bot.application.ingest_company_exposure_documents import (
        CompanyExposureDocumentPipeline,
    )

    result = asyncio.run(
        CompanyExposureDocumentPipeline(Settings()).ingest(
            datetime.fromisoformat(as_of), per_family
        )
    )
    console.print_json(json.dumps(result, default=str))


@app.command("extract-company-macro-exposures")
def extract_company_macro_exposures(
    selection_run_id: str = typer.Option(..., "--selection-run-id"),
    as_of: str = typer.Option(..., "--as-of"),
) -> None:
    """Extract only whitelisted quantitative/role disclosures with evidence."""
    from macro_b3_bot.application.extract_company_macro_exposures import (
        CompanyMacroExposureExtractor,
    )
    from macro_b3_bot.infrastructure.store import DatabaseStore

    settings = Settings()
    store = DatabaseStore(settings.data_dir / "audit.duckdb")
    result = CompanyMacroExposureExtractor(store).extract(
        selection_run_id, datetime.fromisoformat(as_of)
    )
    store.close()
    console.print_json(json.dumps(result, default=str, ensure_ascii=False))


@app.command("reclassify-company-exposure-documents")
def reclassify_company_exposure_documents(
    selection_run_id: str = typer.Option(..., "--selection-run-id"),
) -> None:
    """Apply the corrected issuer/fiduciary/FRE document taxonomy."""
    from macro_b3_bot.application.ingest_company_exposure_documents import (
        CompanyExposureDocumentPipeline,
    )

    result = CompanyExposureDocumentPipeline(Settings()).reclassify(selection_run_id)
    console.print_json(json.dumps(result, ensure_ascii=False))


@app.command("ingest-company-fre-documents")
def ingest_company_fre_documents(
    as_of: str = typer.Option(..., "--as-of"),
) -> None:
    """Download latest official FRE XML and extract exposure-relevant PDF sections."""
    from macro_b3_bot.application.ingest_company_fre_documents import (
        CompanyFreDocumentPipeline,
    )

    result = CompanyFreDocumentPipeline(Settings()).ingest(datetime.fromisoformat(as_of))
    console.print_json(json.dumps(result, ensure_ascii=False, default=str))


@app.command("export-company-exposure-review")
def export_company_exposure_review(
    selection_run_id: str = typer.Option(..., "--selection-run-id"),
    output: str = typer.Option(..., "--output"),
) -> None:
    """Export pending facts for an identified human reviewer."""
    from pathlib import Path

    from macro_b3_bot.application.review_company_macro_exposures import (
        CompanyMacroExposureReviewer,
    )
    from macro_b3_bot.infrastructure.store import DatabaseStore

    settings = Settings()
    store = DatabaseStore(settings.data_dir / "audit.duckdb")
    manifest = CompanyMacroExposureReviewer(store).pending_manifest(selection_run_id)
    store.close()
    Path(output).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    console.print(f"Pending review manifest: {output}")


@app.command("apply-company-exposure-review")
def apply_company_exposure_review(
    manifest: str = typer.Option(..., "--manifest"),
    reviewer_type: str = typer.Option("HUMAN", "--reviewer-type"),
) -> None:
    """Apply explicit HUMAN approval/rejection decisions bound to excerpt hashes."""
    from pathlib import Path

    from macro_b3_bot.application.review_company_macro_exposures import (
        CompanyMacroExposureReviewer,
    )
    from macro_b3_bot.infrastructure.store import DatabaseStore

    settings = Settings()
    store = DatabaseStore(settings.data_dir / "audit.duckdb")
    import getpass

    reviewer_identity = getpass.getuser()
    if reviewer_type not in {"HUMAN", "DELEGATED_AI"}:
        raise typer.BadParameter("reviewer type must be HUMAN or DELEGATED_AI")
    manifest_path = Path(manifest)
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest_payload.get("reviewer_type") != reviewer_type:
        raise typer.BadParameter(
            "manifest reviewer_type does not match --reviewer-type"
        )
    confirmed = typer.confirm(
        f"Confirm that you are the reviewer '{reviewer_identity}' and personally "
        "made every decision in this manifest?"
    )
    result = CompanyMacroExposureReviewer(store).apply_manifest(
        manifest_path,
        confirmed_identity=reviewer_identity,
        confirmed=confirmed,
    )
    store.close()
    console.print_json(json.dumps(result, ensure_ascii=False, default=str))


@app.command("audit-company-exposures")
def audit_company_exposures(
    run_id: str = typer.Option(..., "--run-id", help="Exposure build run ID"),
    output: Optional[str] = typer.Option(None, "--output", help="Optional JSON output"),
) -> None:
    """Reconcile extracted values with the selected official CVM statement lines."""
    from pathlib import Path

    from macro_b3_bot.application.audit_company_exposures import CompanyExposureAuditor
    from macro_b3_bot.infrastructure.store import DatabaseStore

    settings = Settings()
    store = DatabaseStore(settings.data_dir / "audit.duckdb")
    rows = CompanyExposureAuditor(store).audit_run(run_id)
    store.close()
    if output:
        Path(output).write_text(
            json.dumps(rows, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    table = Table(title=f"Accounting audit — {run_id}")
    for column in ("Ticker", "Metric", "Extracted", "Published", "Difference", "Status"):
        table.add_column(column)
    for row in rows:
        table.add_row(
            row["ticker"], row["metric"], str(row["extracted_value"]),
            str(row["published_normalized_value"]), str(row["absolute_difference"]),
            row["validation_status"],
        )
    console.print(table)
    validated = sum(row["validation_status"] == "VALIDATED" for row in rows)
    console.print(f"Validated={validated}/{len(rows)} | Mismatches={len(rows) - validated}")


@app.command("audit-company-macro-exposures")
def audit_company_macro_exposures(
    selection_run_id: str = typer.Option(..., "--selection-run-id"),
    exposure_run_id: str = typer.Option(..., "--exposure-run-id"),
    output: Optional[str] = typer.Option(None, "--output"),
) -> None:
    """Audit PIT documents, field evidence, coverage, and explicit blockers."""
    from pathlib import Path

    from macro_b3_bot.application.audit_company_macro_exposures import (
        CompanyMacroExposureAuditor,
    )
    from macro_b3_bot.infrastructure.store import DatabaseStore

    settings = Settings()
    store = DatabaseStore(settings.data_dir / "audit.duckdb")
    report = CompanyMacroExposureAuditor(store).audit(
        selection_run_id, exposure_run_id
    )
    store.close()
    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if output:
        Path(output).write_text(rendered, encoding="utf-8")
    console.print_json(rendered)


@app.command("dry-run-company-impact-pilot")
def dry_run_company_impact_pilot(
    exposure_run_id: str = typer.Option(..., "--exposure-run-id"),
    selection_run_id: str = typer.Option(..., "--selection-run-id"),
    sector_run_id: str = typer.Option(..., "--sector-run-id"),
    output: Optional[str] = typer.Option(None, "--output"),
) -> None:
    """Run only KLBN11 and SLCE3; block unreviewed facts and all trading actions."""
    from pathlib import Path

    from macro_b3_bot.application.dry_run_company_impact_pilot import (
        CompanyImpactPilotDryRun,
    )
    from macro_b3_bot.infrastructure.store import DatabaseStore

    settings = Settings()
    store = DatabaseStore(settings.data_dir / "audit.duckdb")
    result = CompanyImpactPilotDryRun(store).run(
        exposure_run_id, selection_run_id, sector_run_id
    )
    store.close()
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if output:
        Path(output).write_text(rendered, encoding="utf-8")
    console.print_json(rendered)


@app.command("run-approved-company-impact-pilot")
def run_approved_company_impact_pilot(
    selection_run_id: str = typer.Option(..., "--selection-run-id"),
    sector_run_id: str = typer.Option(..., "--sector-run-id"),
    as_of: str = typer.Option(..., "--as-of"),
    output: Optional[str] = typer.Option(None, "--output"),
) -> None:
    """Build five approved snapshots and compare both company decision policies."""
    from pathlib import Path

    from macro_b3_bot.application.run_approved_company_impact_pilot import (
        ApprovedCompanyImpactPilot,
    )
    from macro_b3_bot.infrastructure.store import DatabaseStore

    settings = Settings()
    store = DatabaseStore(settings.data_dir / "audit.duckdb")
    result = ApprovedCompanyImpactPilot(store).run(
        selection_run_id=selection_run_id,
        sector_run_id=sector_run_id,
        as_of_timestamp=datetime.fromisoformat(as_of),
    )
    store.close()
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if output:
        Path(output).write_text(rendered, encoding="utf-8")
    console.print_json(rendered)


@app.command("run-financial-bridge-pilot")
def run_financial_bridge_pilot(
    selection_run_id: str = typer.Option(..., "--selection-run-id"),
    sector_run_id: str = typer.Option(..., "--sector-run-id"),
    as_of: str = typer.Option(..., "--as-of"),
    output: Optional[str] = typer.Option(None, "--output"),
) -> None:
    """Run the five-company Sprint 4D.2A financial bridge pilot."""
    from pathlib import Path

    from macro_b3_bot.application.run_financial_bridge_pilot import (
        FinancialBridgePilot,
    )
    from macro_b3_bot.infrastructure.store import DatabaseStore

    settings = Settings()
    store = DatabaseStore(settings.data_dir / "audit.duckdb")
    result = FinancialBridgePilot(store).run(
        selection_run_id=selection_run_id,
        sector_run_id=sector_run_id,
        as_of_timestamp=datetime.fromisoformat(as_of),
    )
    store.close()
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if output:
        Path(output).write_text(rendered, encoding="utf-8")
    console.print_json(rendered)


@app.command("reconcile-company-mappings")
def reconcile_company_mappings() -> None:
    """Validate and persist the 15-company pilot mapping against the CVM registry."""
    from macro_b3_bot.application.reconcile_company_mappings import PilotMappingReconciler
    from macro_b3_bot.infrastructure.store import DatabaseStore

    settings = Settings()
    store = DatabaseStore(settings.data_dir / "audit.duckdb")
    result = PilotMappingReconciler(store).reconcile()
    store.close()
    table = Table(title="Sprint 4C.2 — Pilot Mapping Reconciliation")
    table.add_column("Métrica")
    table.add_column("Valor")
    for key in ("mapping_version", "requested", "validated", "tickers", "failures"):
        table.add_row(key, str(result[key]))
    console.print(table)


@app.command("ingest-company-pilot")
def ingest_company_pilot() -> None:
    """Ingest official ITR/DFP data only for the 15-company Sprint 4C.2 pilot."""
    import asyncio

    from macro_b3_bot.application.ingest_company_pilot import (
        ingest_company_pilot as run_pilot_ingestion,
    )

    result = asyncio.run(run_pilot_ingestion())
    table = Table(title="Sprint 4C.2 — Targeted CVM Ingestion")
    table.add_column("Métrica")
    table.add_column("Valor")
    for key, value in result.items():
        table.add_row(key, str(value))
    console.print(table)


@app.command("run-macro-engine")
def run_macro_engine(
    sources: str = typer.Option("fred,eia,noaa", "--sources"),
    incremental: bool = typer.Option(True, "--incremental/--full"),
    since: str = typer.Option(None, "--since"),
) -> None:
    """Run the full Sprint 4A & 4B Macro Event -> Sector Engine pipeline end-to-end."""
    console.print("\n[bold cyan]=== SPRINT 4A & 4B: MACRO TO SECTOR PIPELINE ===[/bold cyan]\n")

    console.print("[bold]Passo 1/4: Ingestão de Dados Macro Globais...[/bold]")
    ingest_global_macro(sources=sources, incremental=incremental)

    console.print("\n[bold]Passo 2/4: Detecção de Regime e Eventos Macro...[/bold]")
    detect_macro_events(since=since)

    console.print("\n[bold]Passo 3/4: Avaliação de Impactos Setoriais (Grafo Causal)...[/bold]")
    evaluate_sector_impacts(since=since)

    console.print("\n[bold]Passo 4/4: Auditoria Completa...[/bold]")
    audit_macro_events()
    audit_sector_impacts()

    console.print("\n[bold green]✓ Macro Engine executado com sucesso![/bold green]")
    console.print("[bold red]BUY: DESABILITADO | ORDENS: DESABILITADAS[/bold red]")


if __name__ == "__main__":
    app()
