import sys
import json
from pathlib import Path
from datetime import datetime, timezone

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
from macro_b3_bot.adapters.b3_screener import B3ScreenerJsonBridge
from macro_b3_bot.infrastructure.store import DatabaseStore

def run_ingest_b3():
    print("\n--------------------------------------------------------------------------")
    print(" 📦 EXECUNTANDO INGESTAO REAL DO B3 SCREENER (macro-b3 ingest-b3)")
    print("--------------------------------------------------------------------------\n")
    
    settings = Settings()
    export_path = settings.b3_screener_export
    
    if not export_path.exists():
        print(f"❌ ARQUIVO DE EXPORTACAO NAO ENCONTRADO: {export_path}")
        return

    raw_text = export_path.read_text(encoding="utf-8")
    payload = json.loads(raw_text)
    raw_records = payload.get("records", [])
    
    bridge = B3ScreenerJsonBridge(export_path)
    
    valid_snapshots = []
    rejected_records = []
    duplicate_tickers = set()
    seen_tickers = set()
    stale_count = 0
    
    for r in raw_records:
        ticker = str(r.get("ticker", "")).strip().upper()
        if not ticker:
            rejected_records.append((r, "Ticker Ausente"))
            continue
            
        if ticker in seen_tickers:
            duplicate_tickers.add(ticker)
        seen_tickers.add(ticker)
        
        price = float(r.get("price") or 0)
        if price <= 0:
            rejected_records.append((r, f"Preco Invalido ({price})"))
            continue
            
        # Parse pelo Bridge
        try:
            snapshot = bridge._parse(r, payload)
            valid_snapshots.append(snapshot)
        except Exception as e:
            rejected_records.append((r, str(e)))

    # Persistência no DuckDB
    db_path = settings.data_dir / "audit.duckdb"
    store = DatabaseStore(db_path)
    
    snapshots_inserted = 0
    for snap in valid_snapshots:
        snap_dict = snap.model_dump(mode="json")
        store.save_asset_snapshot(snap_dict)
        snapshots_inserted += 1
        
    store.close()
    
    buy_enabled_str = "SIM" if (not settings.research_mode and settings.allow_buy_signals) else "NÃO (Modo Pesquisa Ativo)"

    print(f"  - Registros recebidos: {len(raw_records)}")
    print(f"  - Registros válidos: {len(valid_snapshots)}")
    print(f"  - Registros rejeitados: {len(rejected_records)}")
    print(f"  - Snapshots gravados no DuckDB: {snapshots_inserted}")
    print(f"  - Dados antigos / Stale: {stale_count}")
    print(f"  - Tickers duplicados: {len(duplicate_tickers)}")
    print(f"  - BUY habilitado: {buy_enabled_str}")
    print("\n--------------------------------------------------------------------------\n")

if __name__ == "__main__":
    run_ingest_b3()
