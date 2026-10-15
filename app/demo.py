"""Data sintetis deterministik untuk mencoba UI tanpa jaringan (DEMO_MODE).

Pola tiap koin dibuat dari fase-fase (naik, jatuh, dasar, pulih) supaya
pemindai menghasilkan peringkat yang masuk akal untuk didemonstrasikan.
Ini BUKAN data pasar dan selalu ditandai jelas di UI.
"""
from __future__ import annotations

import hashlib
import time

import numpy as np

_DEMO_COINS = [
    ("BTC", "Bitcoin", 9.0e11), ("ETH", "Ethereum", 4.5e11),
    ("SOL", "Solana", 8.0e10), ("SUI", "Sui", 3.0e10),
    ("XRP", "XRP", 6.0e10), ("DOGE", "Dogecoin", 2.5e10),
    ("ADA", "Cardano", 2.2e10), ("LINK", "Chainlink", 1.5e10),
    ("AVAX", "Avalanche", 1.4e10), ("NEAR", "Near Protocol", 8.0e9),
    ("DOT", "Polkadot", 9.0e9), ("LTC", "Litecoin", 7.0e9),
]


def _seed(text: str) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)


def demo_market(days: int = 2400):
    """Kembalikan (pairs, summaries, series) data demo yang konsisten."""
    now_day = int(time.time()) // 86400 * 86400
    pairs: list[dict] = []
    summaries: dict[str, dict] = {}
    series: dict[str, list[dict]] = {}
    for i, (symbol, name, vol) in enumerate(_DEMO_COINS):
        pair_id = f"{symbol.lower()}_idr"
        rng = np.random.default_rng(_seed(symbol))
        # fase: naik -> jatuh dalam -> dasar menyamping -> pulih sebagian
        n1, n2, n3 = days // 3, days // 3, days - 2 * (days // 3)
        rets = np.concatenate([
            rng.normal(0.0015, 0.02, n1),
            rng.normal(-0.0035, 0.035, n2),
            rng.normal(0.0004, 0.016, n3),
        ])
        start = float(rng.uniform(2_000, 60_000))
        close = start * np.exp(np.cumsum(rets))
        candles = []
        for j, c in enumerate(close):
            ts = now_day - (days - 1 - j) * 86400
            spread = float(abs(rng.normal(0, c * 0.004)))
            candles.append({
                "ts": ts, "open": float(c), "high": float(c + spread),
                "low": float(max(c - spread, 0.01)), "close": float(c),
                "volume": float(rng.uniform(5_000, 50_000)),
            })
        series[symbol] = candles
        last = float(close[-1])
        pairs.append({
            "pair_id": pair_id, "symbol": symbol, "name": f"{symbol}/IDR",
            "traded_currency": symbol.lower(), "coingecko_id": "", "logo": "",
        })
        summaries[pair_id] = {
            "name": name, "last": last, "buy": last * 0.999, "sell": last * 1.001,
            "high": last * 1.03, "low": last * 0.97,
            "volume_idr": float(vol / (i + 1)), "spread_pct": 0.2,
        }
    return pairs, summaries, series
