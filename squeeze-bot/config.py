import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _get(k, d=""): return os.environ.get(k, d)
def _float(k, d): return float(os.environ.get(k, "") or d)
def _int(k, d): return int(os.environ.get(k, "") or d)
def _bool(k, d=False):
    v = os.environ.get(k, "").lower()
    return {"true":"true","1":"true","yes":"true"}.get(v, str(d).lower()) == "true"


@dataclass
class Config:
    bybit_api_key: str
    bybit_api_secret: str
    bybit_testnet: bool
    tg_token: str
    tg_chat_id: str
    mode: str
    capital_usd: float
    position_size_usd: float
    max_positions: int
    min_funding_abs_pct: float
    entry_before_sec: int
    stop_loss_pct: float
    take_profit_pct: float
    max_position_age_sec: int
    scan_interval_sec: int
    db_path: str
    log_level: str


def load_config() -> Config:
    return Config(
        bybit_api_key=_get("BYBIT_API_KEY"),
        bybit_api_secret=_get("BYBIT_API_SECRET"),
        bybit_testnet=_bool("BYBIT_TESTNET"),
        tg_token=_get("TELEGRAM_TOKEN"),
        tg_chat_id=_get("TELEGRAM_CHAT_ID"),
        mode=_get("MODE", "paper"),
        capital_usd=_float("CAPITAL_USD", 500),
        position_size_usd=_float("POSITION_SIZE_USD", 100),
        max_positions=_int("MAX_POSITIONS", 2),
        min_funding_abs_pct=_float("MIN_FUNDING_ABS_PCT", 0.3),
        entry_before_sec=_int("ENTRY_BEFORE_FUNDING_SEC", 45),
        stop_loss_pct=_float("STOP_LOSS_PCT", 0.5),
        take_profit_pct=_float("TAKE_PROFIT_PCT", 0.8),
        max_position_age_sec=_int("MAX_POSITION_AGE_SEC", 300),
        scan_interval_sec=_int("SCAN_INTERVAL_SEC", 10),
        db_path=_get("DB_PATH", "/data/squeeze.db" if os.path.isdir("/data") else "squeeze.db"),
        log_level=_get("LOG_LEVEL", "INFO"),
    )
