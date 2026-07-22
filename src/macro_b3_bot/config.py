from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    data_dir: Path = Path("./data")

    advanced_btc_bot_root: Path = Path(r"C:\Nestjs\Advanced_Btc_Bot\Advanced_Btc_Bot")
    b3_screener_root: Path = Path(r"C:\Nestjs\Advanced_Btc_Bot\b3_screener")
    b3_screener_export: Path = Path(r"C:\Nestjs\Advanced_Btc_Bot\b3_screener\exports\universe.json")

    legacy_tribunal_module: str = "logic.tribunal"
    legacy_tribunal_function: str = "evaluate"
    legacy_risk_module: str = "logic.risk_manager"
    legacy_risk_function: str = "evaluate"

    mirofish_enabled: bool = False
    mirofish_base_url: str = "http://localhost:5001"
    mirofish_graph_prefix: str = "/api/graph"
    mirofish_simulation_prefix: str = "/api/simulation"
    mirofish_report_prefix: str = "/api/report"
    mirofish_timeout_seconds: float = 120.0

    fred_api_key: str | None = None
    eia_api_key: str | None = None
    youtube_api_key: str | None = None

    research_mode: bool = True
    allow_buy_signals: bool = False
    allow_order_execution: bool = False

    min_score_buy: float = 0.72
    min_confidence_buy: float = 0.65
    min_reward_risk: float = 1.8
    min_independent_evidence: int = 3

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
