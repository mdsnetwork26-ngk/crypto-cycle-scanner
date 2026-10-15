"""Perhitungan fitur teknikal & fase siklus dari deret candle harian.

Prinsip penting: semua fitur pada tanggal t hanya memakai data <= t
(tidak ada kebocoran masa depan). Label hasil masa depan dihitung terpisah
di app.episodes dan tidak pernah masuk fitur.
"""
from __future__ import annotations

import math

import numpy as np

FEATURE_NAMES = [
    "ret_30", "ret_90", "ret_180", "ret_365",
    "drawdown_ath", "log_age_ath", "dist_low_365",
    "rsi_14", "dist_ma50", "dist_ma200",
    "vol_30", "vol_ratio", "range_90",
    "volume_trend", "rel_btc_90",
]


def _log_returns(close: np.ndarray) -> np.ndarray:
    return np.diff(np.log(np.maximum(close, 1e-12)))


def rsi(close: np.ndarray, period: int = 14) -> float:
    """RSI sederhana dari `period` perubahan terakhir. 50 bila data datar."""
    if len(close) < period + 1:
        return 50.0
    diff = np.diff(close[-(period + 1):])
    gains = float(np.mean(np.clip(diff, 0, None)))
    losses = float(np.mean(np.clip(-diff, 0, None)))
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    rs = gains / losses
    return float(100.0 - 100.0 / (1.0 + rs))


def features_at(close: np.ndarray, volume: np.ndarray, idx: int,
                btc_close_by_pos: np.ndarray | None = None) -> dict | None:
    """Fitur pada posisi idx (inklusif). None bila riwayat belum cukup.

    close/volume adalah deret harian penuh; btc_close_by_pos adalah harga
    BTC yang sudah diselaraskan posisinya dengan deret ini (boleh None).
    """
    if idx < 365 or idx >= len(close):
        return None
    price = float(close[idx])
    if price <= 0:
        return None
    window = close[: idx + 1]
    logret = _log_returns(window)

    def ret(days: int) -> float:
        return float(math.log(price / close[idx - days])) if close[idx - days] > 0 else 0.0

    ath = float(np.max(window))
    ath_pos = int(np.argmax(window))
    low_365 = float(np.min(close[idx - 364: idx + 1]))
    ma50 = float(np.mean(close[idx - 49: idx + 1]))
    ma200 = float(np.mean(close[idx - 199: idx + 1]))

    vol_30 = float(np.std(logret[-30:]) * math.sqrt(365)) if len(logret) >= 30 else 0.0
    vol_90 = float(np.std(logret[-90:]) * math.sqrt(365)) if len(logret) >= 90 else 0.0
    range_90 = float(math.log(np.max(close[idx - 89: idx + 1]) /
                              max(np.min(close[idx - 89: idx + 1]), 1e-12)))

    vol_trend = 0.0
    if len(volume) > idx and float(np.mean(volume[idx - 179: idx + 1])) > 0:
        recent = float(np.mean(volume[idx - 29: idx + 1]))
        base = float(np.mean(volume[idx - 179: idx + 1]))
        if base > 0 and recent > 0:
            vol_trend = float(math.log(recent / base))

    rel_btc = 0.0
    if btc_close_by_pos is not None and idx < len(btc_close_by_pos):
        btc_now = float(btc_close_by_pos[idx])
        btc_then = float(btc_close_by_pos[idx - 90]) if idx >= 90 else 0.0
        if btc_now > 0 and btc_then > 0 and close[idx - 90] > 0:
            rel_btc = float((price / close[idx - 90]) / (btc_now / btc_then) - 1.0)

    return {
        "ret_30": ret(30),
        "ret_90": ret(90),
        "ret_180": ret(180),
        "ret_365": ret(365),
        "drawdown_ath": float(price / ath - 1.0) if ath > 0 else 0.0,
        "log_age_ath": float(math.log1p(idx - ath_pos) / math.log(366.0)),
        "dist_low_365": float(math.log(price / low_365)) if low_365 > 0 else 0.0,
        "rsi_14": rsi(window),
        "dist_ma50": float(math.log(price / ma50)) if ma50 > 0 else 0.0,
        "dist_ma200": float(math.log(price / ma200)) if ma200 > 0 else 0.0,
        "vol_30": vol_30,
        "vol_ratio": float(vol_30 / vol_90) if vol_90 > 0 else 1.0,
        "range_90": range_90,
        "volume_trend": vol_trend,
        "rel_btc_90": rel_btc,
    }


def feature_vector(feat: dict) -> np.ndarray:
    return np.array([float(feat.get(n, 0.0)) for n in FEATURE_NAMES], dtype=float)


def price_window(close: np.ndarray, idx: int, days: int = 180, points: int = 26) -> np.ndarray:
    """Jendela harga ternormalisasi untuk perbandingan bentuk grafik.

    Mengembalikan `points` titik log-harga relatif ke titik pertama lalu
    di-z-score, supaya koin berharga Rp100 dan Rp1 juta bisa dibandingkan
    bentuknya, bukan nominalnya.
    """
    start = max(0, idx - days + 1)
    seg = close[start: idx + 1]
    if len(seg) < points:
        return np.zeros(points)
    pos = np.linspace(0, len(seg) - 1, points).astype(int)
    sampled = np.log(np.maximum(seg[pos], 1e-12))
    sampled = sampled - sampled[0]
    std = float(np.std(sampled))
    if std > 1e-9:
        sampled = sampled / std
    return sampled


def dtw_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Jarak Dynamic Time Warping ternormalisasi untuk dua jendela harga."""
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return float("inf")
    prev = np.full(m + 1, np.inf)
    prev[0] = 0.0
    for i in range(1, n + 1):
        cur = np.full(m + 1, np.inf)
        for j in range(1, m + 1):
            cost = abs(float(a[i - 1]) - float(b[j - 1]))
            cur[j] = cost + min(prev[j], cur[j - 1], prev[j - 1])
        prev = cur
    return float(prev[m]) / (n + m)


def forward_outcome(close: np.ndarray, idx: int, horizon_days: int) -> dict | None:
    """Hasil nyata `horizon_days` setelah idx (HANYA untuk label historis)."""
    end = idx + horizon_days
    if end >= len(close):
        return None
    start = float(close[idx])
    if start <= 0:
        return None
    future = close[idx + 1: end + 1]
    return {
        "return": float(close[end] / start - 1.0),
        "max_drawdown": float(np.min(future) / start - 1.0),
        "max_gain": float(np.max(future) / start - 1.0),
    }
