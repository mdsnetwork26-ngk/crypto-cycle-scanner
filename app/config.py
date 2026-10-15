"""Konfigurasi aplikasi. Semua nilai bisa dioverride lewat .env / environment."""
from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv opsional
    pass


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "ya", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


@dataclass
class Settings:
    app_name: str = os.getenv("APP_NAME", "Crypto Cycle Scanner")
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = _int("PORT", 8020)
    database_path: str = os.getenv("DATABASE_PATH", "data/scanner.db")
    demo_mode: bool = _bool("DEMO_MODE", False)
    fetch_workers: int = max(1, _int("FETCH_WORKERS", 4))
    reference_symbols_raw: str = os.getenv(
        "REFERENCE_SYMBOLS",
        "BTC,ETH,BNB,SOL,XRP,DOGE,ADA,TRX,LINK,LTC,"
        "DOT,AVAX,ATOM,NEAR,UNI,AAVE,FIL,ALGO,XLM,ETC",
    )
    indodax_base: str = os.getenv("INDODAX_BASE", "https://indodax.com").rstrip("/")

    @property
    def reference_symbols(self) -> list[str]:
        return [s.strip().upper() for s in self.reference_symbols_raw.split(",") if s.strip()]


settings = Settings()
