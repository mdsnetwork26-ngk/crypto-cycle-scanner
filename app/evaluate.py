"""Evaluasi hasil pemindaian terhadap harga realisasi.

Setiap pemindaian menyimpan harga & skenario pada tanggalnya. Modul ini
membandingkan ramalan lama itu dengan yang benar-benar terjadi setelah
horizonnya lewat: arah kelasnya benar atau tidak, harga realisasi masuk
rentang P25-P75 atau tidak, seberapa meleset skenario mediannya, dan
bagaimana hasil peringkatnya dibanding sekadar memegang BTC pada
jendela yang sama. Dihitung langsung dari basis data setiap halaman
dibuka, jadi angka "matang" bertambah sendiri seiring waktu.

Catatan kejujuran: bulan-bulan awal jumlah sampelnya kecil. Semua tabel
menampilkan n supaya tidak ada kesimpulan yang terasa lebih pasti
daripada data yang sebenarnya.
"""
from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone

import numpy as np

from .db import Database

WIB = timezone(timedelta(hours=7))
_BIG_PCT = {7: 5.0, 30: 10.0, 365: 20.0}
_CLASSES = ("Potensi Naik", "Netral / Menunggu", "Potensi Turun")


def _close_at_or_before(candles: list[dict], ts: int) -> float | None:
    best = None
    for c in candles:
        if c["ts"] <= ts:
            best = float(c["close"])
        else:
            break
    return best


def _close_at_or_after(candles: list[dict], ts: int,
                       tolerance_days: int = 3) -> float | None:
    for c in candles:
        if c["ts"] >= ts:
            if c["ts"] > ts + tolerance_days * 86400:
                return None
            return float(c["close"])
    return None


def _realized_pct(db: Database, pair_id: str, as_of_ts: int,
                  price0: float, horizon_days: int) -> float | None:
    """Perubahan % harga `horizon_days` setelah as_of (None = belum matang)."""
    if not price0 or price0 <= 0:
        return None
    candles = db.candles(pair_id)
    if not candles:
        return None
    close = _close_at_or_after(candles, as_of_ts + horizon_days * 86400)
    if close is None:
        return None
    return (close / float(price0) - 1.0) * 100.0


def _btc_realized_pct(db: Database, as_of_ts: int, horizon_days: int,
                      cache: dict) -> float | None:
    if "pair_id" not in cache:
        cache["pair_id"] = next(
            (p["pair_id"] for p in db.pairs() if (p.get("symbol") or "") == "BTC"),
            "btc_idr")
        cache["candles"] = db.candles(cache["pair_id"])
    candles = cache["candles"]
    if not candles:
        return None
    p0 = _close_at_or_before(candles, as_of_ts)
    p1 = _close_at_or_after(candles, as_of_ts + horizon_days * 86400)
    if not p0 or not p1:
        return None
    return (p1 / p0 - 1.0) * 100.0


def scan_outcomes(db: Database, scan: dict, btc_cache: dict) -> list[dict]:
    """Hasil per koin dari satu pemindaian, ditambah realisasinya."""
    horizon = int(scan["horizon_days"])
    out: list[dict] = []
    for payload in db.results(scan["id"]):
        realized = _realized_pct(db, payload["pair_id"], int(payload["as_of_ts"]),
                                 float(payload["price"]), horizon)
        out.append({
            "symbol": payload.get("symbol", ""),
            "pair_id": payload["pair_id"],
            "classification": payload.get("classification", ""),
            "score": float(payload.get("score", 0)),
            "probability_up": float(payload.get("probability_up", 0)),
            "median_pct": float(payload.get("median_pct", 0)),
            "p25_pct": float(payload.get("p25_pct", 0)),
            "p75_pct": float(payload.get("p75_pct", 0)),
            "realized_pct": realized,
            "matured": realized is not None,
            "btc_pct": (_btc_realized_pct(db, int(payload["as_of_ts"]), horizon,
                                          btc_cache) if realized is not None else None),
        })
    return out


def _arah_benar(o: dict, horizon: int) -> bool:
    r = o["realized_pct"]
    if o["classification"] == "Potensi Naik":
        return r > 0
    if o["classification"] == "Potensi Turun":
        return r < 0
    return abs(r) <= _BIG_PCT.get(horizon, 10.0)


def _summarize(outcomes: list[dict], horizon: int) -> dict:
    matang = [o for o in outcomes if o["matured"]]
    s: dict = {
        "n_total": len(outcomes),
        "n_matured": len(matang),
        "n_pending": len(outcomes) - len(matang),
        "classes": [],
        "calibration": [],
        "band_pct": None,
        "median_error": None,
        "avg_realized": None,
        "avg_btc": None,
        "top_bottom": None,
        "coins_best": [],
        "coins_worst": [],
        "notes": [],
    }
    if not matang:
        return s
    r_all = np.array([o["realized_pct"] for o in matang], dtype=float)
    s["avg_realized"] = float(np.mean(r_all))
    btc = [o["btc_pct"] for o in matang if o["btc_pct"] is not None]
    s["avg_btc"] = float(np.mean(btc)) if btc else None
    s["median_error"] = float(np.median(
        [o["realized_pct"] - o["median_pct"] for o in matang]))
    band = [o for o in matang if o["p25_pct"] <= o["realized_pct"] <= o["p75_pct"]]
    s["band_pct"] = len(band) / len(matang) * 100.0

    for cls in _CLASSES:
        group = [o for o in matang if o["classification"] == cls]
        if not group:
            s["classes"].append({"name": cls, "n": 0, "hit_pct": None,
                                 "avg": None, "avg_btc": None, "excess": None})
            continue
        hit = np.mean([_arah_benar(o, horizon) for o in group]) * 100.0
        avg = float(np.mean([o["realized_pct"] for o in group]))
        gb = [o["btc_pct"] for o in group if o["btc_pct"] is not None]
        avg_btc = float(np.mean(gb)) if gb else None
        s["classes"].append({
            "name": cls, "n": len(group), "hit_pct": float(hit), "avg": avg,
            "avg_btc": avg_btc,
            "excess": (avg - avg_btc) if avg_btc is not None else None,
        })

    for lo, hi in ((0, 40), (40, 55), (55, 70), (70, 100.01)):
        group = [o for o in matang if lo <= o["probability_up"] < hi]
        if group:
            s["calibration"].append({
                "label": f"{lo:.0f}-{min(hi, 100):.0f}%",
                "n": len(group),
                "up_pct": float(np.mean([o["realized_pct"] > 0 for o in group]) * 100.0),
            })

    ranked = sorted(matang, key=lambda o: o["score"], reverse=True)
    if len(ranked) >= 6:
        top = ranked[:5]
        bottom = ranked[-5:]
        s["top_bottom"] = {
            "top_avg": float(np.mean([o["realized_pct"] for o in top])),
            "bottom_avg": float(np.mean([o["realized_pct"] for o in bottom])),
            "top_symbols": ", ".join(o["symbol"] for o in top),
            "bottom_symbols": ", ".join(o["symbol"] for o in bottom),
        }

    per_coin: dict[str, list[dict]] = {}
    for o in matang:
        per_coin.setdefault(o["symbol"], []).append(o)
    coin_rows = []
    for symbol, group in per_coin.items():
        if len(group) >= 2:
            coin_rows.append({
                "symbol": symbol, "n": len(group),
                "avg": float(np.mean([o["realized_pct"] for o in group])),
                "hit_pct": float(np.mean([_arah_benar(o, horizon) for o in group]) * 100.0),
            })
    coin_rows.sort(key=lambda c: c["avg"], reverse=True)
    s["coins_best"] = coin_rows[:5]
    s["coins_worst"] = list(reversed(coin_rows[-5:])) if len(coin_rows) > 5 else []

    # Catatan pola sederhana, selalu menyebut jumlah sampelnya.
    for row in s["classes"]:
        if row["n"] >= 5 and row["hit_pct"] is not None:
            if row["hit_pct"] >= 70:
                s["notes"].append(
                    f"Kelas {row['name']} cukup bisa diandalkan sejauh ini: "
                    f"{row['hit_pct']:.0f}% arahnya benar dari {row['n']} sampel.")
            elif row["hit_pct"] <= 35:
                s["notes"].append(
                    f"Kelas {row['name']} justru sering meleset "
                    f"({row['hit_pct']:.0f}% benar dari {row['n']} sampel) — "
                    "pola kontrarian yang layak diawasi, bukan dibalik membabi buta.")
    if s["band_pct"] is not None and len(matang) >= 8:
        if s["band_pct"] < 35:
            s["notes"].append(
                "Harga realisasi sering keluar dari rentang P25-P75: rentang "
                "skenario aplikasi terlalu sempit untuk volatilitas nyata.")
        elif s["band_pct"] > 75:
            s["notes"].append(
                "Harga realisasi hampir selalu di dalam rentang P25-P75: "
                "rentangnya cenderung terlalu lebar untuk jadi pembeda.")
    if s["top_bottom"] and len(matang) >= 10:
        tb = s["top_bottom"]
        if tb["top_avg"] > tb["bottom_avg"]:
            s["notes"].append(
                f"Peringkat bekerja bulan ini: 5 skor teratas rata-rata "
                f"{tb['top_avg']:+.1f}% vs 5 terbawah {tb['bottom_avg']:+.1f}%.")
        else:
            s["notes"].append(
                "Peringkat belum bekerja bulan ini: skor teratas tidak "
                "mengalahkan skor terbawah.")
    return s


def available_months(db: Database) -> list[dict]:
    """Bulan-bulan (WIB) yang memiliki pemindaian selesai, terbaru dulu."""
    seen: dict[tuple[int, int], None] = {}
    for scan in db.list_scans(10000):
        if scan["status"] != "done":
            continue
        dt = datetime.fromtimestamp(int(scan["started_at"]), WIB)
        seen.setdefault((dt.year, dt.month), None)
    return [{"year": y, "month": m,
             "label": datetime(y, m, 1, tzinfo=WIB).strftime("%B %Y")}
            for (y, m) in sorted(seen, reverse=True)]


def monthly_report(db: Database, year: int | None = None,
                   month: int | None = None) -> dict:
    """Laporan satu bulan (atau kumulatif bila year=None)."""
    now = datetime.now(WIB)
    cumulative = year is None or month is None
    if cumulative:
        start_ts, end_ts = 0, int(now.timestamp()) + 1
        label = "Kumulatif (semua waktu)"
    else:
        start = datetime(year, month, 1, tzinfo=WIB)
        end = start + timedelta(days=calendar.monthrange(year, month)[1])
        start_ts, end_ts = int(start.timestamp()), int(end.timestamp())
        label = start.strftime("%B %Y")

    scans = [s for s in db.scans_between(start_ts, end_ts) if s["status"] == "done"]
    btc_cache: dict = {}
    per_horizon: dict[int, list[dict]] = {}
    for scan in scans:
        horizon = int(scan["horizon_days"])
        per_horizon.setdefault(horizon, []).extend(
            scan_outcomes(db, scan, btc_cache))

    return {
        "label": label,
        "cumulative": cumulative,
        "year": year, "month": month,
        "n_scans": len(scans),
        "horizons": {h: _summarize(outs, h) for h, outs in sorted(per_horizon.items())},
        "months": available_months(db),
        "generated_at": now.strftime("%d %b %Y %H:%M WIB"),
    }
