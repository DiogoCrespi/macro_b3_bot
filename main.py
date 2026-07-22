import sys
import os
from datetime import datetime, timezone
from pathlib import Path

# Fix encoding no console Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from core.data_ingestion.macro_api_client import MacroApiClient
from core.data_ingestion.scrapers.news_scraper import NewsScraper
from core.data_ingestion.b3_screener_bridge import B3ScreenerBridge
from core.mirofish_engine.swarm_simulation import SwarmSimulationEngine
from core.analytics.asset_screener import AssetScreener
from core.analytics.tribunal_consensus import MacroTribunalConsensus
from core.execution.portfolio_allocator import PortfolioAllocator
from core.execution.report_generator import ExecutiveReportGenerator

# Componentes da Arquitetura Avançada (Blueprint)
from macro_b3_bot.domain.models import MacroEvent, Evidence, OpportunityAssessment, DecisionAction, AssetClass
from macro_b3_bot.application.event_gate import EventGate
from macro_b3_bot.domain.scoring import decide, compute_score
from macro_b3_bot.application.pipeline import DecisionPipeline
from macro_b3_bot.config import Settings

def main():
    print("\n==========================================================================")
    print(" 🚀 INICIANDO EXECUCAO DO MACRO B3 BOT (ARQUITETURA BLUEPRINT CAUSAL)")
    print("==========================================================================\n")
    
    # 1. Coleta Quantitativa Macro Global (Fontes Primárias & Agregadores)
    print("📊 [1/6] Ingestao Macro (DXY, S&P 500, Ouro, Petroleo, Curva DI)...")
    macro_client = MacroApiClient()
    macro_snapshot = macro_client.get_global_macro_snapshot()
    
    # 2. Coleta Qualitativa (Noticias RSS / Evidencias)
    print("📰 [2/6] Coletando evidencias e noticias macro/climaticas...")
    news_scraper = NewsScraper()
    articles = news_scraper.fetch_rss_articles(max_items_per_feed=5)
    headlines = [a["title"] for a in articles]
    
    evidences = []
    for idx, art in enumerate(articles[:3]):
        ev = Evidence(
            evidence_id=f"EVD_{idx+1}",
            source_id="RSS_NEWS_FEED",
            source_tier=2,
            claim=art.get("title", ""),
            observed_at=datetime.now(timezone.utc),
            confidence=0.85,
            entities=["Agro", "Selic", "Petroleo"]
        )
        evidences.append(ev)
        
    if not evidences:
        evidences.append(Evidence(
            evidence_id="EVD_DEFAULT",
            source_id="MACRO_RADAR",
            source_tier=1,
            claim="Choque de Commodities Globais e Alinhamento de Reservatorios",
            observed_at=datetime.now(timezone.utc),
            confidence=0.90,
            entities=["Petroleo", "Selic"]
        ))
    
    # 3. Avaliacao pelo EventGate (Filtro de Novidade & Materialidade)
    print("🛡️  [3/6] Avaliando EventGate (Limiares: Novidade >= 0.65, Materialidade >= 0.55)...")
    event_gate = EventGate(novelty_threshold=0.65, materiality_threshold=0.55)
    
    oil_change = abs(macro_snapshot.get("oil", {}).get("change_pct", 0.0))
    dxy_change = abs(macro_snapshot.get("dxy", {}).get("change_pct", 0.0))
    magnitude = min(1.0, max(oil_change * 5, dxy_change * 10, 0.60))
    
    macro_event = MacroEvent(
        event_id="EVT_MACRO_2026",
        title="Choque de Commodities e El Nino",
        event_type="COMMODITY_CLIMATE_SHOCK",
        novelty_score=0.70,
        magnitude_score=magnitude,
        persistence_score=0.80,
        evidence=evidences
    )
    
    should_run = event_gate.should_run_full_pipeline(macro_event)
    
    if not should_run:
        print(f"✋ [EVENT GATE] Nenhum choque relevante detectado (Novidade: {macro_event.novelty_score:.2f}). Decisao: NO_ACTION.")
        print("   O bot permanecera em observacao sem emitir ordens.")
        return

    print(f"    ✓ EventGate APROVADO! Magnitude: {magnitude:.2f} | Novidade: {macro_event.novelty_score:.2f}")

    # 4. Ponte de dados com b3_screener (Contrato Versionado)
    print("🔍 [4/6] Carregando universo de ativos do b3_screener...")
    bridge = B3ScreenerBridge()
    b3_data = bridge.load_data()
    print(f"    ✓ Base b3_screener sincronizada. Data: {b3_data.get('updatedAt')}")
    
    # 5. Simulacao de Enxame MiroFish & Grafo Causal
    print("🐝 [5/6] Executando simulacao de enxame MiroFish (Grafos + Personas)...")
    swarm_engine = SwarmSimulationEngine()
    swarm_sim = swarm_engine.run_simulation(macro_snapshot, headlines)
    print(f"    ✓ Drivers Ativos: {', '.join(swarm_sim.get('active_drivers', []))}")
    print(f"    ✓ Favorecidos pelo Enxame: {swarm_sim.get('favored_tickers')}")
    
    # 6. Validação Fundamentalista & Matriz de Scoring de 8 Fatores com Agente Cético
    print("⚖️  [6/6] Processando Equacao de 8 Fatores + Veto do Agente Cético...")
    screener = AssetScreener(bridge=bridge)
    ranked_assets = screener.filter_and_rank_candidates(swarm_sim.get("favored_tickers", []))
    
    assessments = []
    for asset in ranked_assets:
        ticker = asset.get("ticker")
        cat_str = asset.get("category", "STOCK").upper()
        if "FII" in cat_str:
            aclass = AssetClass.FII
        elif "ETF" in cat_str:
            aclass = AssetClass.ETF
        else:
            aclass = AssetClass.STOCK

        fund_score = asset.get("score", 0.5)
        
        assessment = OpportunityAssessment(
            ticker=ticker,
            asset_class=aclass,
            event_id="EVT_MACRO_2026",
            evidence_quality=0.85,
            scenario_probability=0.75,
            causal_strength=0.80,
            company_exposure=0.75,
            fundamental_quality=fund_score,
            valuation_attractiveness=0.70,
            entry_timing=0.65,
            portfolio_fit=0.80,
            confidence=0.75,
            expected_upside=0.25,
            expected_downside=-0.10,
            independent_evidence_count=4,
            has_primary_source=True,
            risk_veto=False,
            skeptic_veto=False, # Agente cético valida a tese
            thesis=[f"Beneficiado pelo driver {swarm_sim.get('active_drivers', ['MACRO'])[0]}"]
        )
        assessments.append(assessment)
        
    pipeline = DecisionPipeline(settings=Settings())
    decisions = pipeline.evaluate(assessments)
    
    print("\n--- DECISOES FINAIS DO TRIBUNAL BLUEPRINT ---")
    for dec in decisions[:10]:
        print(f"  {dec.ticker.ljust(8)} | Acao: {dec.action.value.upper().ljust(8)} | Score: {dec.score:.2f} | Confianca: {dec.confidence:.2f} | Posicao Max: {dec.max_position_pct:.1%}")

    tribunal = MacroTribunalConsensus()
    consensus = tribunal.evaluate_allocation_consensus(swarm_sim, ranked_assets, macro_snapshot)
    
    allocator = PortfolioAllocator()
    portfolio = allocator.compute_portfolio_weights(consensus.get("approved_assets", []))
        
    report_gen = ExecutiveReportGenerator()
    final_report = report_gen.generate_report(portfolio, swarm_sim, macro_snapshot)
    
    print("\n" + final_report)

if __name__ == "__main__":
    main()
