import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Paths para projetos de suporte
B3_SCREENER_DIR = Path(os.getenv("B3_SCREENER_DIR", r"C:\Nestjs\b3_screener"))
ADVANCED_BTC_BOT_DIR = Path(os.getenv("ADVANCED_BTC_BOT_DIR", r"C:\Nestjs\Advanced_Btc_Bot\Advanced_Btc_Bot"))

# Cache & Storage
DATA_DIR = BASE_DIR / "data"
MAPPING_FILE = BASE_DIR / "config" / "sector_mappings.json"

# Macro Thresholds
RISK_OFF_VETO_DXY_CHANGE_24H = 0.008   # +0.8% em 24h
RISK_OFF_VETO_SP500_CHANGE_24H = -0.015 # -1.5% em 24h

# Maximum Portfolio Allocation Constraints
MAX_SINGLE_ASSET_WEIGHT = 0.15 # Max 15% em uma única ação
MAX_SINGLE_SECTOR_WEIGHT = 0.35 # Max 35% em um único setor
MIN_CASH_WEIGHT = 0.10          # Mínimo de 10% de caixa/Tesouro Selic

# API Keys & URLs
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
BRAPI_API_KEY = os.getenv("BRAPI_API_KEY", "")
