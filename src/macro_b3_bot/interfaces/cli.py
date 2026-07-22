from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
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


if __name__ == "__main__":
    app()
