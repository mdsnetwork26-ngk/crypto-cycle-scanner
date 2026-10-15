"""Orkestrasi pemindaian: ambil data Indodax, bangun perpustakaan episode,
nilai semua kandidat, simpan hasilnya ke SQLite."""
from __future__ import annotations

import math
import threading
import time

import numpy as np

from .config import settings
from .db import Database
from .demo import demo_market
from .episodes import build_library, find_analogs, sol_reference_similarity, _aligned_btc
from .features import features_at, price_window
from .indodax import IndodaxClient, fetch_many

db = Database(settings.database_path)
db.init()

_scan_lock = threading.Lock()
_running_thread: threading.Thread | None = None

_STABLE = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "BUSD", "IDRT", "XAUT", "PAXG"}
_LEVERAGED_HINTS = ("3L", "3S", "5L", "5S", "BULL", "BEAR")

# Horizon yang didukung (hari): mingguan, bulanan, tahunan.
HORIZONS = (7, 30, 365)
# Ambang "naik besar" (%) dan skala median untuk skor, per horizon —
# gerak 7 hari memang jauh lebih kecil daripada gerak 1 tahun.
_BIG_PCT = {7: 5.0, 30: 10.0, 365: 20.0}
_MEDIAN_SCALE = {7: 0.05, 30: 0.10, 365: 0.35}


def _excluded(pair: dict, summary: dict) -> str | None:
    """Alasan koin dicoret dari peringkat (None = layak dianalisa)."""
    symbol = (pair.get("symbol") or "").upper()
    name = (summary.get("name") or pair.get("name") or "").lower()
    if symbol in _STABLE:
        return "stablecoin/emas token"
    if "tokenized" in name or "xstock" in name:
        return "saham tokenisasi (bukan kripto murni)"
    if any(h in symbol for h in _LEVERAGED_HINTS):
        return "token leveraged"
    return None


def _trend_score(feat: dict) -> float:
    score = 50.0
    score += max(-25.0, min(25.0, feat["dist_ma50"] * 120))
    score += max(-25.0, min(25.0, feat["dist_ma200"] * 80))
    score += max(-15.0, min(15.0, (feat["rsi_14"] - 50.0) * 0.45))
    score += max(-15.0, min(15.0, feat["rel_btc_90"] * 60))
    return float(max(0.0, min(100.0, score)))


def _weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    order = np.argsort(values)
    v, w = values[order], weights[order]
    total = float(np.sum(w))
    if total <= 0 or len(v) == 0:
        return 0.0
    cdf = np.cumsum(w) / total
    return float(v[min(int(np.searchsorted(cdf, q)), len(v) - 1)])


def _reasons(feat: dict, agg: dict, pair: dict, summary: dict,
             n_candles: int) -> tuple[list[str], list[str]]:
    plus: list[str] = []
    minus: list[str] = []
    dd = feat["drawdown_ath"] * 100
    if dd <= -70:
        plus.append(f"sudah jatuh {dd:.0f}% dari puncak data (area harga dasar)")
    elif dd <= -50:
        plus.append(f"drawdown {dd:.0f}% dari puncak — fase koreksi dalam")
    if feat["vol_ratio"] < 0.8:
        plus.append("volatilitas 30 hari menyempit di bawah rata-rata 90 hari (fase akumulasi)")
    if feat["dist_low_365"] < math.log(1.25):
        plus.append("harga dekat dasar 1 tahun (kurang dari +25% dari titik terendah)")
    if feat["dist_ma50"] > 0 and feat["dist_ma200"] > 0:
        plus.append("harga sudah di atas MA-50 dan MA-200 (struktur berbalik naik)")
    elif feat["dist_ma50"] > 0:
        plus.append("harga baru merebut kembali MA-50")
    if feat["rel_btc_90"] > 0.05:
        plus.append("mengungguli BTC dalam 90 hari terakhir (kekuatan relatif naik)")
    if agg["probability_up"] >= 60:
        plus.append(f"{agg['probability_up']:.0f}% episode analog historis berakhir naik")

    if feat["dist_ma200"] < -0.1:
        minus.append("harga masih jauh di bawah MA-200 (tren besar belum berbalik)")
    if feat["rsi_14"] > 72:
        minus.append(f"RSI {feat['rsi_14']:.0f} — jenuh beli jangka pendek")
    if feat["ret_30"] > 0.35:
        minus.append("sudah naik tajam 30 hari terakhir (risiko kejar harga)")
    if summary.get("volume_idr", 0) < 1_000_000_000:
        minus.append(f"volume Indodax hanya Rp{summary.get('volume_idr', 0):,.0f}/hari")
    if summary.get("spread_pct", 0) > 1:
        minus.append(f"spread bid-ask lebar ({summary['spread_pct']:.1f}%)")
    if n_candles < 730:
        minus.append(f"riwayat pair baru {n_candles} hari — kepercayaan pola lebih rendah")
    if agg["forward_drawdown_median_pct"] < -25:
        minus.append("episode analog dulu sempat jatuh dalam sebelum bergerak")
    return plus, minus


def _aggregate(analogs: list[dict], horizon_days: int) -> dict:
    if not analogs:
        return {"probability_up": 50.0, "probability_big": 0.0, "median_pct": 0.0,
                "p25_pct": 0.0, "p75_pct": 0.0, "forward_drawdown_median_pct": 0.0,
                "similarity_avg": 0.0}
    rets = np.array([a["forward_return_pct"] for a in analogs], dtype=float)
    dds = np.array([a["forward_drawdown_pct"] for a in analogs], dtype=float)
    w = np.array([a["similarity"] for a in analogs], dtype=float)
    big = _BIG_PCT.get(horizon_days, 10.0)
    return {
        "probability_up": float(np.sum(w[rets > 0]) / np.sum(w) * 100) if np.sum(w) else 50.0,
        "probability_big": float(np.sum(w[rets > big]) / np.sum(w) * 100) if np.sum(w) else 0.0,
        "median_pct": _weighted_quantile(rets, w, 0.5),
        "p25_pct": _weighted_quantile(rets, w, 0.25),
        "p75_pct": _weighted_quantile(rets, w, 0.75),
        "forward_drawdown_median_pct": _weighted_quantile(dds, w, 0.5),
        "similarity_avg": float(np.mean([a["similarity"] for a in analogs])),
        "big_threshold_pct": big,
    }


def _classify(score: float, agg: dict) -> str:
    if score >= 60 and agg["probability_up"] >= 55:
        return "Potensi Naik"
    if score <= 40 and agg["probability_up"] <= 45:
        return "Potensi Turun"
    return "Netral / Menunggu"


def run_scan(horizon_days: int, max_coins: int = 60,
             min_volume_idr: float = 100_000_000, scan_id: int | None = None) -> int:
    """Jalankan satu pemindaian penuh (sinkron; pemanggil mengatur thread)."""
    if horizon_days not in HORIZONS:
        raise ValueError("horizon hanya 7 (1 minggu), 30 (1 bulan), atau 365 (1 tahun)")
    started = int(time.time())
    if scan_id is None:
        scan_id = db.create_scan(horizon_days, started)

    def stage(text: str, **kw) -> None:
        db.update_scan(scan_id, stage=text, **kw)

    try:
        client = None if settings.demo_mode else IndodaxClient()
        demo_series: dict[str, list[dict]] = {}
        if settings.demo_mode:
            stage("menyiapkan data demo (sintetis)")
            pairs, summaries, demo_series = demo_market()
        else:
            stage("mengambil daftar pair & ringkasan Indodax")
            pairs = client.pairs_idr()  # type: ignore[union-attr]
            summaries = client.summaries()  # type: ignore[union-attr]
        db.upsert_pairs(pairs, started)

        pair_by_symbol = {(p.get("symbol") or "").upper(): p for p in pairs}
        pair_by_id = {p["pair_id"]: p for p in pairs}

        # ---- kandidat: terlaris di Indodax, lolos filter kelayakan
        ranked = sorted(summaries.items(), key=lambda kv: kv[1].get("volume_idr", 0), reverse=True)
        candidates: list[str] = []
        skipped_reasons: dict[str, str] = {}
        for pair_id, summ in ranked:
            pair = pair_by_id.get(pair_id)
            if pair is None:
                continue
            why = _excluded(pair, summ)
            if why:
                skipped_reasons[pair_id] = why
                continue
            if summ.get("volume_idr", 0) < min_volume_idr:
                continue
            candidates.append(pair_id)
            if len(candidates) >= max_coins:
                break
        stage(f"mengambil candle {len(candidates)} kandidat", total=len(candidates))

        # ---- candle kandidat (pakai cache bila masih segar)
        series: dict[str, list[dict]] = {}
        need_fetch: list[str] = []
        now = int(time.time())
        for pair_id in candidates:
            if settings.demo_mode:
                symbol = pair_by_id[pair_id]["symbol"].upper()
                series[pair_id] = demo_series.get(symbol, [])
                continue
            count, last = db.candle_freshness(pair_id)
            if count >= 200 and last >= now - 2 * 86400:
                series[pair_id] = db.candles(pair_id, limit_days=1200)
            else:
                need_fetch.append(pair_id)
        if need_fetch and client is not None:
            done = {"n": 0}

            def _on(pid: str, rows) -> None:
                done["n"] += 1
                if rows:
                    db.upsert_candles(pid, rows)
                    series[pid] = db.candles(pid, limit_days=1200)
                stage(f"mengambil candle kandidat {done['n']}/{len(need_fetch)}")

            fetch_many(client, need_fetch, days=1200,
                       workers=settings.fetch_workers, on_done=_on)

        # ---- perpustakaan episode dari koin-koin acuan
        stage("membangun perpustakaan episode historis")
        ref_series: dict[str, list[dict]] = {}
        for symbol in settings.reference_symbols:
            pair = pair_by_symbol.get(symbol)
            if pair is None:
                continue
            pid = pair["pair_id"]
            if settings.demo_mode:
                if symbol in demo_series:
                    ref_series[symbol] = demo_series[symbol]
                continue
            count, last = db.candle_freshness(pid)
            # Acuan butuh riwayat sedalam mungkin (target ±3650 hari), bukan
            # sekadar cache kandidat 1200 hari. Ambil penuh bila tersimpan
            # belum dalam; koin yang listingnya memang lebih muda akan
            # diambil ulang tiap pindai (satu request per koin, murah).
            if count >= 3400 and last >= now - 2 * 86400:
                ref_series[symbol] = db.candles(pid)
            elif client is not None:
                rows = client.candles(pid, days=3650)
                if rows:
                    db.upsert_candles(pid, rows)
                    ref_series[symbol] = db.candles(pid)
        library = build_library(ref_series, horizon_days)
        if not library.episodes:
            raise RuntimeError("perpustakaan episode kosong — data acuan tidak cukup")

        btc_candles = ref_series.get("BTC", [])

        # ---- nilai setiap kandidat
        stage(f"menilai kandidat dengan {len(library.episodes)} episode acuan",
              total=len(candidates))
        analyzed = 0
        skipped = 0
        for pair_id in candidates:
            pair = pair_by_id[pair_id]
            summ = summaries.get(pair_id, {})
            candles = series.get(pair_id) or []
            if len(candles) < 366:
                skipped += 1
                continue
            close = np.array([float(c["close"]) for c in candles], dtype=float)
            volume = np.array([float(c.get("volume", 0.0)) for c in candles], dtype=float)
            idx = len(candles) - 1
            btc_aligned = _aligned_btc({**ref_series, "BTC": btc_candles},
                                       pair["symbol"].upper(),
                                       candles) if btc_candles else None
            feat = features_at(close, volume, idx, btc_aligned)
            if feat is None:
                skipped += 1
                continue
            cand_window = price_window(close, idx)
            analogs = find_analogs(library, feat, cand_window,
                                   exclude_symbol=pair["symbol"].upper())
            if not analogs:
                skipped += 1
                continue
            agg = _aggregate(analogs, horizon_days)
            trend = _trend_score(feat)
            median_scale = _MEDIAN_SCALE.get(horizon_days, 0.10)
            median_score = 50 + 38 * math.tanh(agg["median_pct"] / 100.0 / median_scale)
            penalty = 0.0
            vol_idr = summ.get("volume_idr", 0)
            penalty += 0 if vol_idr > 10e9 else 3 if vol_idr > 1e9 else 8 if vol_idr > 1e8 else 15
            if summ.get("spread_pct", 0) > 2:
                penalty += 8
            elif summ.get("spread_pct", 0) > 1:
                penalty += 4
            score = (0.34 * agg["probability_up"] + 0.28 * median_score
                     + 0.18 * agg["similarity_avg"] + 0.20 * trend - penalty)
            score = float(max(0.0, min(100.0, score)))
            price = float(close[idx])
            plus, minus = _reasons(feat, agg, pair, summ, len(candles))
            sol_ref = sol_reference_similarity(
                library, feat, cand_window, ref_series.get("SOL"))
            payload = {
                "pair_id": pair_id,
                "symbol": pair["symbol"],
                "name": summ.get("name") or pair["name"],
                "logo": pair.get("logo", ""),
                "horizon_days": horizon_days,
                "price": price,
                "score": round(score, 1),
                "classification": _classify(score, agg),
                "probability_up": round(agg["probability_up"], 1),
                "probability_big": round(agg["probability_big"], 1),
                "big_threshold_pct": agg["big_threshold_pct"],
                "median_pct": round(agg["median_pct"], 1),
                "p25_pct": round(agg["p25_pct"], 1),
                "p75_pct": round(agg["p75_pct"], 1),
                "scenario_p25": price * (1 + agg["p25_pct"] / 100),
                "scenario_median": price * (1 + agg["median_pct"] / 100),
                "scenario_p75": price * (1 + agg["p75_pct"] / 100),
                "forward_drawdown_median_pct": round(agg["forward_drawdown_median_pct"], 1),
                "similarity_avg": round(agg["similarity_avg"], 1),
                "trend_score": round(trend, 1),
                "volume_idr": vol_idr,
                "spread_pct": round(summ.get("spread_pct", 0), 2),
                "change_24h_pct": (
                    round(float(close[idx] / close[idx - 1] - 1) * 100, 2)
                    if idx >= 1 and close[idx - 1] > 0 else 0.0),
                "features": {k: round(float(v), 4) for k, v in feat.items()},
                "analogs": analogs,
                "sol_reference": sol_ref,
                "reasons_plus": plus,
                "reasons_minus": minus,
                "n_candles": len(candles),
                "episodes_used": len(library.episodes),
                "as_of_ts": int(candles[idx]["ts"]),
                "demo": settings.demo_mode,
            }
            db.save_result(scan_id, payload)
            analyzed += 1
            stage(f"menilai kandidat {analyzed + skipped}/{len(candidates)}",
                  analyzed=analyzed, skipped=skipped)

        db.update_scan(scan_id, status="done", stage="selesai",
                       finished_at=int(time.time()), analyzed=analyzed,
                       skipped=skipped + (len(candidates) - analyzed - skipped),
                       total=len(candidates),
                       message=f"{len(library.episodes)} episode acuan dipakai")
        return scan_id
    except Exception as exc:  # noqa: BLE001
        db.update_scan(scan_id, status="error", stage="gagal",
                       finished_at=int(time.time()), message=str(exc)[:500])
        raise


def start_scan(horizon_days: int, max_coins: int = 60,
               min_volume_idr: float = 100_000_000) -> int | None:
    """Mulai pemindaian di thread latar. None bila sudah ada yang berjalan."""
    global _running_thread
    with _scan_lock:
        if _running_thread is not None and _running_thread.is_alive():
            return None
        scan_id = db.create_scan(horizon_days, int(time.time()))

        def _job() -> None:
            try:
                run_scan(horizon_days, max_coins, min_volume_idr, scan_id=scan_id)
            except Exception:  # noqa: BLE001 - status sudah dicatat di DB
                pass

        _running_thread = threading.Thread(target=_job, daemon=True)
        _running_thread.start()
        return scan_id
