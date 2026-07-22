from __future__ import annotations

import sys
# Fix encoding no console Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from datetime import datetime, timezone
import json
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


if __name__ == "__main__":
    app()
