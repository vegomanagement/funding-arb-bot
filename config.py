"""Загрузка и валидация конфигурации из .env"""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _get_float(key: str, default: float) -> float:
    val = os.environ.get(key, "")
    return float(val) if val else default


def _get_int(key: str, default: int) -> int:
    val = os.environ.get(key, "")
    return int(val) if val else default


def _get_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key, "").lower()
    if val in ("true", "1", "yes"):
        return True
    if val in ("false", "0", "no"):
        return False
    return default


@dataclass
class Config:
    # Bybit
    bybit_api_key: str
    bybit_api_secret: str
    bybit_testnet: bool

    # Hyperliquid
    hl_private_key: str
    hl_address: str
    hl_testnet: bool

    # Telegram
    tg_token: str
    tg_chat_id: str

    # Стратегия
    mode: str  # paper | testnet | live
    capital_usd: float
    min_funding_diff_pct: float
    max_positions: int
    max_position_pct: float
    stop_loss_pct: float
    daily_drawdown_limit_pct: float

    # Сканирование
    scan_interval_sec: int
    min_volume_24h_usd: float

    # Комиссии бирж (taker, доля: 0.00055 = 0.055%)
    bybit_taker_fee: float
    hl_taker_fee: float

    # DB
    db_path: str

    # Логи
    log_level: str
    timezone: str

    @property
    def is_paper(self) -> bool:
        return self.mode == "paper"

    @property
    def is_live(self) -> bool:
        return self.mode == "live"


def load_config() -> Config:
    cfg = Config(
        bybit_api_key=_get("BYBIT_API_KEY"),
        bybit_api_secret=_get("BYBIT_API_SECRET"),
        bybit_testnet=_get_bool("BYBIT_TESTNET"),
        hl_private_key=_get("HYPERLIQUID_PRIVATE_KEY"),
        hl_address=_get("HYPERLIQUID_ADDRESS"),
        hl_testnet=_get_bool("HYPERLIQUID_TESTNET"),
        tg_token=_get("TELEGRAM_TOKEN"),
        tg_chat_id=_get("TELEGRAM_CHAT_ID"),
        mode=_get("MODE", "paper"),
        capital_usd=_get_float("CAPITAL_USD", 1000),
        min_funding_diff_pct=_get_float("MIN_FUNDING_DIFF_PCT", 0.03),
        max_positions=_get_int("MAX_POSITIONS", 3),
        max_position_pct=_get_float("MAX_POSITION_PCT", 30),
        stop_loss_pct=_get_float("STOP_LOSS_PCT", 2),
        daily_drawdown_limit_pct=_get_float("DAILY_DRAWDOWN_LIMIT_PCT", 5),
        scan_interval_sec=_get_int("SCAN_INTERVAL_SEC", 60),
        min_volume_24h_usd=_get_float("MIN_VOLUME_24H_USD", 1_000_000),
        bybit_taker_fee=_get_float("BYBIT_TAKER_FEE", 0.00055),
        hl_taker_fee=_get_float("HL_TAKER_FEE", 0.00045),
        db_path=_get("DB_PATH", "/tmp/funding_arb.db"),
        log_level=_get("LOG_LEVEL", "INFO"),
        timezone=_get("TIMEZONE", "UTC"),
    )

    if cfg.mode not in ("paper", "testnet", "live"):
        raise ValueError(f"MODE должен быть paper/testnet/live, получено: {cfg.mode}")

    if cfg.is_live and not (cfg.bybit_api_key and cfg.hl_private_key):
        raise ValueError("Live mode требует API ключи Bybit и Hyperliquid")

    return cfg
