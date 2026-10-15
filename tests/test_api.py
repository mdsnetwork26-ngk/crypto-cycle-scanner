"""Tes ujung-ke-ujung dalam MODE DEMO (data sintetis, tanpa jaringan)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import db, run_scan

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def _pindai_demo():
    run_scan(30, max_coins=8, min_volume_idr=0)
    run_scan(7, max_coins=8, min_volume_idr=0)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["demo_mode"] is True


def test_dasbor_berisi_hasil():
    r = client.get("/")
    assert r.status_code == 200
    assert "Crypto Cycle Scanner" in r.text
    assert "MODE DEMO" in r.text
    assert "bukan rekomendasi finansial" in r.text


def test_hasil_tersimpan_dan_terurut_skor():
    scan = db.latest_done_scan(30)
    assert scan is not None and scan["analyzed"] >= 5
    results = db.scan_results(scan["id"])
    skor = [r["score"] for r in results]
    assert skor == sorted(skor, reverse=True)
    for r in results:
        assert r["classification"] in (
            "Potensi Naik", "Netral / Menunggu", "Potensi Turun")
        assert len(r["analogs"]) >= 1
        assert r["scenario_p25"] <= r["scenario_median"] <= r["scenario_p75"]


def test_halaman_detail():
    scan = db.latest_done_scan(30)
    top = db.scan_results(scan["id"])[0]
    r = client.get(f"/coin/{top['pair_id']}?scan_id={scan['id']}")
    assert r.status_code == 200
    assert "Episode analog terdekat" in r.text
    assert "Pembanding favorit" in r.text  # SOL tetap tampil sebagai pembanding


def test_status_api():
    r = client.get("/api/scan/status")
    assert r.status_code == 200
    assert r.json()["status"] == "done"
