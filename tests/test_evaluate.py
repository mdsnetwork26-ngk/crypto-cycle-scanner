from datetime import datetime

from app.db import Database
from app.evaluate import WIB, monthly_report
from app.scheduler import due_horizons, next_run

TS0 = 1735689600  # 2025-01-01 00:00 UTC


def _candles(days=120, growth=1.01, start=100.0):
    rows, price = [], start
    for i in range(days):
        if i:
            price *= growth
        rows.append({"ts": TS0 + i * 86400, "open": price, "high": price,
                     "low": price, "close": price, "volume": 1000.0})
    return rows


def _payload(pair_id, symbol, as_of_idx, price, kelas, prob, median, p25, p75, score):
    return {
        "pair_id": pair_id, "symbol": symbol, "horizon_days": 7,
        "score": score, "classification": kelas, "probability_up": prob,
        "median_pct": median, "p25_pct": p25, "p75_pct": p75,
        "price": price, "as_of_ts": TS0 + as_of_idx * 86400,
    }


def _pair(pair_id, symbol):
    return {"pair_id": pair_id, "symbol": symbol, "name": f"{symbol}/IDR",
            "traded_currency": symbol.lower(), "coingecko_id": "", "logo": ""}


def _db_isi(tmp_path):
    db = Database(str(tmp_path / "eval.db"))
    db.init()
    db.upsert_pairs([_pair("xxx_idr", "XXX"), _pair("yyy_idr", "YYY"),
                     _pair("zzz_idr", "ZZZ"), _pair("btc_idr", "BTC")], TS0)
    xxx = _candles()
    for pid in ("xxx_idr", "yyy_idr", "zzz_idr"):
        db.upsert_candles(pid, xxx)
    db.upsert_candles("btc_idr", _candles(growth=1.0, start=50000.0))
    idx = 50
    price0 = xxx[idx]["close"]
    started = TS0 + idx * 86400
    scan = db.create_scan(7, started)
    db.update_scan(scan, status="done", finished_at=started)
    # naik & benar (realisasi +7,2%), turun tapi salah, dan satu belum tempo
    db.save_result(scan, _payload("xxx_idr", "XXX", idx, price0,
                                  "Potensi Naik", 70, 5, 0, 10, 80))
    db.save_result(scan, _payload("yyy_idr", "YYY", idx, price0,
                                  "Potensi Turun", 30, -5, -10, 0, 20))
    db.save_result(scan, _payload("zzz_idr", "ZZZ", 118, xxx[118]["close"],
                                  "Potensi Naik", 60, 4, 0, 9, 70))
    return db, started


def test_evaluasi_menghitung_realisasi(tmp_path):
    db, started = _db_isi(tmp_path)
    dt = datetime.fromtimestamp(started, WIB)
    rep = monthly_report(db, dt.year, dt.month)
    h7 = rep["horizons"][7]
    assert rep["n_scans"] == 1
    assert h7["n_matured"] == 2 and h7["n_pending"] == 1
    kelas = {c["name"]: c for c in h7["classes"]}
    assert kelas["Potensi Naik"]["hit_pct"] == 100.0
    assert kelas["Potensi Turun"]["hit_pct"] == 0.0
    assert kelas["Potensi Naik"]["avg"] > 7.0  # 1,01^7 - 1
    assert abs(h7["band_pct"] - 50.0) < 1e-9   # satu masuk rentang, satu tidak
    kal = {k["label"]: k for k in h7["calibration"]}
    assert kal["70-100%"]["up_pct"] == 100.0
    assert kal["0-40%"]["up_pct"] == 100.0  # golongan Turun yang ternyata naik


def test_evaluasi_kumulatif_dan_bulan_lain(tmp_path):
    db, started = _db_isi(tmp_path)
    rep = monthly_report(db)  # kumulatif
    assert rep["cumulative"] is True
    assert rep["horizons"][7]["n_matured"] == 2
    dt = datetime.fromtimestamp(started, WIB)
    kosong = monthly_report(db, dt.year, dt.month + 6 if dt.month <= 6 else dt.month)
    assert kosong["n_scans"] >= 0  # tidak error untuk bulan tanpa data


def test_penjadwal_keputusan_jatuh_tempo():
    pagi = datetime(2026, 10, 15, 5, 0, tzinfo=WIB)
    lewat = datetime(2026, 10, 15, 7, 0, tzinfo=WIB)
    assert due_horizons(pagi, {}) == []
    assert due_horizons(lewat, {}) == [7, 30, 365]
    baru = int(datetime(2026, 10, 15, 6, 30, tzinfo=WIB).timestamp())
    assert due_horizons(lewat, {h: baru for h in (7, 30, 365)}) == []
    assert next_run(pagi).hour == 6 and next_run(pagi).day == 15
    assert next_run(lewat).day == 16
