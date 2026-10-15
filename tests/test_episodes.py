import numpy as np

from app.episodes import build_library, find_analogs
from app.features import features_at, price_window
from app.services import _aggregate, _classify


def _candles(symbol_seed, n=1200):
    rng = np.random.default_rng(symbol_seed)
    close = 50.0 * np.exp(np.cumsum(rng.normal(0.0005, 0.025, n)))
    base_ts = 1_600_000_000
    return [
        {"ts": base_ts + i * 86400, "open": float(c), "high": float(c * 1.01),
         "low": float(c * 0.99), "close": float(c), "volume": 1e6}
        for i, c in enumerate(close)
    ]


def _series():
    return {"AAA": _candles(1), "BBB": _candles(2), "BTC": _candles(3)}


def test_perpustakaan_episode_terbentuk():
    lib = build_library(_series(), horizon_days=30)
    assert len(lib.episodes) > 100
    assert lib.median is not None and lib.iqr is not None
    # semua episode punya label hasil nyata 30 hari
    for ep in lib.episodes[:50]:
        assert -1.0 < ep.outcome_return < 10.0


def test_episode_terlalu_muda_dikeluarkan():
    # horizon 365 menuntut riwayat 365 (fitur) + 365 (label): deret 1200
    # hari hanya menyisakan sedikit episode yang sah.
    lib30 = build_library(_series(), horizon_days=30)
    lib365 = build_library(_series(), horizon_days=365)
    assert 0 < len(lib365.episodes) < len(lib30.episodes)


def test_cari_analog_dan_agregasi():
    series = _series()
    lib = build_library(series, horizon_days=30)
    candles = series["AAA"]
    close = np.array([c["close"] for c in candles])
    volume = np.array([c["volume"] for c in candles])
    idx = len(candles) - 1
    feat = features_at(close, volume, idx)
    analogs = find_analogs(lib, feat, price_window(close, idx),
                           exclude_symbol="AAA")
    assert analogs, "harus ada analog"
    assert all(a["symbol"] != "AAA" for a in analogs)
    sims = [a["similarity"] for a in analogs]
    assert sims == sorted(sims, reverse=True)
    agg = _aggregate(analogs, 30)
    assert 0 <= agg["probability_up"] <= 100
    assert agg["p25_pct"] <= agg["median_pct"] <= agg["p75_pct"]
    assert _classify(80, {"probability_up": 70}) == "Potensi Naik"
    assert _classify(20, {"probability_up": 30}) == "Potensi Turun"
    assert _classify(50, {"probability_up": 50}) == "Netral / Menunggu"
