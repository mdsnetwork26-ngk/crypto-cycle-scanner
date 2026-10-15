"""Perpustakaan episode historis & mesin pencari analog terdekat.

Idenya (sesuai rancangan yang disepakati):
- Bukan hanya Solana 2022. Setiap minggu historis dari banyak koin
  berumur panjang direkam keadaannya (fitur saat itu juga, tanpa data
  masa depan), lalu diberi label hasil nyatanya +30/+365 hari.
- Untuk koin kandidat hari ini, dicari episode-episode yang keadaannya
  paling mirip (jarak fitur robust + kemiripan bentuk grafik DTW).
- Potensi harga = distribusi hasil nyata dari para analog itu, bukan
  satu angka ramalan: probabilitas naik, median, kuartil, dan risiko
  penurunan terdalamnya.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .features import FEATURE_NAMES, dtw_distance, features_at, feature_vector, forward_outcome, price_window


@dataclass
class Episode:
    symbol: str
    date_ts: int
    features: np.ndarray
    window: np.ndarray
    outcome_return: float
    outcome_drawdown: float
    outcome_gain: float


@dataclass
class Library:
    horizon_days: int
    episodes: list[Episode] = field(default_factory=list)
    median: np.ndarray | None = None
    iqr: np.ndarray | None = None
    feature_scale: float = 1.0

    def finalize(self) -> None:
        if not self.episodes:
            return
        mat = np.vstack([e.features for e in self.episodes])
        self.median = np.median(mat, axis=0)
        q75, q25 = np.percentile(mat, [75, 25], axis=0)
        iqr = q75 - q25
        iqr[iqr < 1e-9] = 1.0
        self.iqr = iqr


def _aligned_btc(series: dict[str, list[dict]], symbol: str,
                 candles: list[dict]) -> np.ndarray | None:
    """Harga BTC diselaraskan ke tanggal-tanggal `candles` (forward-fill)."""
    btc = series.get("BTC")
    if not btc or symbol == "BTC":
        return None
    btc_by_ts = {int(c["ts"]): float(c["close"]) for c in btc}
    out = np.zeros(len(candles))
    last = 0.0
    for i, c in enumerate(candles):
        v = btc_by_ts.get(int(c["ts"]))
        if v:
            last = v
        out[i] = last
    return out if np.any(out > 0) else None


def build_library(series: dict[str, list[dict]], horizon_days: int,
                  step_days: int = 7) -> Library:
    """Bangun perpustakaan episode dari deret banyak koin.

    Episode diambil tiap `step_days` hari. Syarat: >=365 hari riwayat
    sebelum tanggal episode dan hasil `horizon_days` ke depan sudah
    lengkap diketahui (episode terlalu muda tidak dipakai, supaya label
    masa depannya tidak bocor/mengecoh).
    """
    lib = Library(horizon_days=horizon_days)
    for symbol, candles in sorted(series.items()):
        if len(candles) < 365 + horizon_days + 7:
            continue
        close = np.array([float(c["close"]) for c in candles], dtype=float)
        volume = np.array([float(c.get("volume", 0.0)) for c in candles], dtype=float)
        btc = _aligned_btc(series, symbol, candles)
        for idx in range(365, len(candles) - horizon_days, step_days):
            feat = features_at(close, volume, idx, btc)
            if feat is None:
                continue
            outcome = forward_outcome(close, idx, horizon_days)
            if outcome is None:
                continue
            lib.episodes.append(
                Episode(
                    symbol=symbol,
                    date_ts=int(candles[idx]["ts"]),
                    features=feature_vector(feat),
                    window=price_window(close, idx),
                    outcome_return=outcome["return"],
                    outcome_drawdown=outcome["max_drawdown"],
                    outcome_gain=outcome["max_gain"],
                )
            )
    lib.finalize()
    return lib


def _feature_distance(lib: Library, vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    med = lib.median if lib.median is not None else np.zeros_like(vec)
    iqr = lib.iqr if lib.iqr is not None else np.ones_like(vec)
    z = (matrix - med) / iqr
    zc = (vec - med) / iqr
    return np.sqrt(np.mean((z - zc) ** 2, axis=1))


def _weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    order = np.argsort(values)
    v, w = values[order], weights[order]
    total = float(np.sum(w))
    if total <= 0:
        return float(np.median(values)) if len(values) else 0.0
    cdf = np.cumsum(w) / total
    pos = int(np.searchsorted(cdf, q))
    return float(v[min(pos, len(v) - 1)])


def find_analogs(lib: Library, cand_features: dict, cand_window: np.ndarray,
                 *, preselect: int = 60, top_k: int = 12,
                 exclude_symbol: str = "") -> list[dict]:
    """Episode historis paling mirip dengan keadaan kandidat saat ini."""
    if not lib.episodes:
        return []
    matrix = np.vstack([e.features for e in lib.episodes])
    vec = feature_vector(cand_features)
    dist_f = _feature_distance(lib, vec, matrix)

    order = np.argsort(dist_f)

    # Panggung 1: saring awal dari jarak fitur. Panggung 2: nilai ulang
    # dengan kemiripan bentuk grafik (DTW) yang lebih mahal hitungnya.
    staged: list[tuple[float, Episode, float]] = []
    taken = 0
    per_symbol: dict[str, int] = {}
    for pos in order:
        ep = lib.episodes[int(pos)]
        if exclude_symbol and ep.symbol == exclude_symbol:
            continue
        # Batasi dominasi satu koin/periode pada daftar analog akhir.
        if per_symbol.get(ep.symbol, 0) >= 4:
            continue
        d_shape = dtw_distance(cand_window, ep.window)
        combined = float(dist_f[int(pos)]) * 0.65 + d_shape * 0.35
        staged.append((combined, ep, float(dist_f[int(pos)])))
        per_symbol[ep.symbol] = per_symbol.get(ep.symbol, 0) + 1
        taken += 1
        if taken >= preselect:
            break

    staged.sort(key=lambda x: x[0])
    if not staged:
        return []
    scale = float(np.median([s[0] for s in staged])) or 1.0
    out: list[dict] = []
    for combined, ep, d_feat in staged[:top_k]:
        similarity = float(100.0 * math.exp(-combined / (2.5 * scale + 1e-9)))
        out.append(
            {
                "symbol": ep.symbol,
                "date_ts": ep.date_ts,
                "distance": round(combined, 4),
                "feature_distance": round(d_feat, 4),
                "similarity": round(max(1.0, min(99.0, similarity)), 1),
                "forward_return_pct": round(ep.outcome_return * 100, 1),
                "forward_drawdown_pct": round(ep.outcome_drawdown * 100, 1),
                "forward_gain_pct": round(ep.outcome_gain * 100, 1),
            }
        )
    return out


def sol_reference_similarity(lib: Library, cand_features: dict,
                             cand_window: np.ndarray,
                             sol_series: list[dict] | None) -> dict | None:
    """Kemiripan khusus ke fase dasar Solana akhir 2022 (pembanding favorit).

    Ini hanya alat pembanding, BUKAN penentu skor: skor utama berasal dari
    seluruh perpustakaan episode multi-koin.
    """
    if not sol_series:
        return None
    close = np.array([float(c["close"]) for c in sol_series], dtype=float)
    volume = np.array([float(c.get("volume", 0.0)) for c in sol_series], dtype=float)
    # cari hari dasar: harga terendah pada paruh akhir 2022 (zona dasar SOL)
    target = None
    for i, c in enumerate(sol_series):
        ts = int(c["ts"])
        # 2022-10-01 s/d 2023-01-31 dalam epoch kasar
        if 1664582400 <= ts <= 1675209600 and i >= 365:
            if target is None or close[i] < close[target]:
                target = i
    if target is None:
        return None
    feat = features_at(close, volume, target, None)
    if feat is None:
        return None
    med = lib.median if lib.median is not None else feature_vector(feat)
    iqr = lib.iqr if lib.iqr is not None else np.ones(len(FEATURE_NAMES))
    z_a = (feature_vector(cand_features) - med) / iqr
    z_b = (feature_vector(feat) - med) / iqr
    d_feat = float(np.sqrt(np.mean((z_a - z_b) ** 2)))
    d_shape = dtw_distance(cand_window, price_window(close, target))
    combined = d_feat * 0.65 + d_shape * 0.35
    similarity = float(100.0 * math.exp(-combined / 3.0))
    outcome = forward_outcome(close, target, 365)
    return {
        "date_ts": int(sol_series[target]["ts"]),
        "similarity": round(max(1.0, min(99.0, similarity)), 1),
        "sol_forward_365_pct": (round(outcome["return"] * 100, 1)
                                if outcome else None),
    }
