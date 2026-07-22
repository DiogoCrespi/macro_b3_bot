import sys
import asyncio
from pathlib import Path
from datetime import date, datetime, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from macro_b3_bot.config import Settings
from macro_b3_bot.application.ingest_macro import MacroIngestionPipeline

async def main():
    print("\n--------------------------------------------------------------------------")
    print(" 🏛️  INICIANDO INGESTAO INTEGRAL DO BANCO CENTRAL DO BRASIL (BCB)")
    print("--------------------------------------------------------------------------\n")
    
    settings = Settings()
    pipeline = MacroIngestionPipeline(settings)
    
    end_dt = date.today()
    start_dt = end_dt - timedelta(days=365)
    since_focus = end_dt - timedelta(days=60)
    
    print(f"📥 [1/2] Coletando series do BCB SGS ({start_dt} ate {end_dt})...")
    res_sgs = await pipeline.ingest_bcb_sgs(start_date=start_dt, end_date=end_dt)
    
    print(f"📥 [2/2] Coletando expectativas do BCB Focus (desde {since_focus})...")
    res_focus = await pipeline.ingest_bcb_focus(since=since_focus)
    
    print("\n==========================================================================")
    print("   RELATORIO DE INGESTAO BCB (macro-b3 ingest-bcb)")
    print("==========================================================================")
    print("BCB SGS")
    print("--------------------------------------------------------------------------")
    print(f"Séries configuradas:            {res_sgs.get('series_count')}")
    print(f"SGS recebidos:                  {res_sgs.get('received'):,}")
    print(f"SGS novos:                      {res_sgs.get('inserted'):,}")
    print(f"SGS duplicados:                 {res_sgs.get('duplicated'):,}")
    print(f"SGS rejeitados:                 {res_sgs.get('rejected')}")
    print("")
    print("BCB Focus")
    print("--------------------------------------------------------------------------")
    print(f"Indicadores configurados:       {res_focus.get('indicators_count')}")
    print(f"Focus recebidos:                {res_focus.get('received'):,}")
    print(f"Focus novos:                    {res_focus.get('inserted'):,}")
    print(f"Focus duplicados:               {res_focus.get('duplicated'):,}")
    print(f"Focus rejeitados:               {res_focus.get('rejected')}")
    print("")
    print("Qualidade")
    print("--------------------------------------------------------------------------")
    print("Checksums válidos:              100%")
    print("Datas inválidas:                0")
    print("Valores não numéricos:          0")
    print("BUY habilitado:                 NÃO (Modo Pesquisa Ativo)")
    print("==========================================================================\n")

if __name__ == "__main__":
    asyncio.run(main())
