import numpy as np

from app.features import (
    FEATURE_NAMES, dtw_distance, features_at, forward_outcome, price_window,
)


def _series(n=900, drift=0.001, vol=0.02, seed=7):
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    volume = rng.uniform(1e5, 5e5, n)
    return close, volume


def test_features_butuh_riwayat_365_hari():
    close, volume = _series()
    assert features_at(close, volume, 100) is None
    feat = features_at(close, volume, 500)
    assert feat is not None
    assert list(feat.keys()) == FEATURE_NAMES


def test_fitur_tidak_mengintip_masa_depan():
    close, volume = _series()
    asli = features_at(close, volume, 500)
    diubah = close.copy()
    diubah[501:] = diubah[501:] * 100  # masa depan diubah drastis
    tetap = features_at(diubah, volume, 500)
    assert asli == tetap


def test_price_window_dan_dtw():
    close, _ = _series()
    w = price_window(close, 800)
    assert w.shape == (26,)
    assert dtw_distance(w, w) == 0.0
    assert dtw_distance(w, price_window(close, 700)) >= 0.0


def test_forward_outcome_hanya_label():
    close, _ = _series()
    out = forward_outcome(close, 500, 30)
    assert out is not None
    assert abs(out["return"] - (close[530] / close[500] - 1)) < 1e-9
    assert out["max_drawdown"] <= 0.001
    # di ujung deret: hasil masa depan belum lengkap -> None
    assert forward_outcome(close, len(close) - 5, 30) is None
