"""Crypto Cycle Scanner — dasbor pemindai kemiripan fase siklus kripto.

Prinsip kejujuran yang dijaga di UI:
- output adalah statistik episode historis yang mirip, bukan ramalan pasti;
- setiap koin menampilkan episode analog asalnya (koin, tanggal, hasil
  nyatanya) supaya pengguna bisa menilai sendiri;
- selalu ada penafian bahwa ini alat riset, bukan rekomendasi finansial.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .config import settings
from .services import db, run_scan, start_scan

WIB = timezone(timedelta(hours=7))
BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Crypto Cycle Scanner")


def fmt_idr(value) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    if v >= 1_000_000_000_000:
        return f"Rp{v / 1e12:,.2f} T".replace(",", "x").replace(".", ",").replace("x", ".")
    if v >= 1_000_000_000:
        return f"Rp{v / 1e9:,.1f} M".replace(",", "x").replace(".", ",").replace("x", ".")
    if v >= 1_000_000:
        return f"Rp{v / 1e6:,.1f} jt".replace(",", "x").replace(".", ",").replace("x", ".")
    if v >= 1:
        return f"Rp{v:,.0f}".replace(",", ".")
    return f"Rp{v:,.4f}".replace(",", "x").replace(".", ",").replace("x", ".")


def fmt_price(value) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    if v >= 1000:
        return f"Rp{v:,.0f}".replace(",", ".")
    if v >= 1:
        return f"Rp{v:,.2f}".replace(",", "x").replace(".", ",").replace("x", ".")
    return f"Rp{v:,.6f}".replace(",", "x").replace(".", ",").replace("x", ".")


def fmt_date(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), WIB).strftime("%d %b %Y")
    except (TypeError, ValueError, OSError):
        return "-"


def fmt_pct(value, signed: bool = True) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    sign = "+" if (signed and v > 0) else ""
    return f"{sign}{v:.1f}%".replace(".", ",")


templates.env.filters["idr"] = fmt_idr
templates.env.filters["price"] = fmt_price
templates.env.filters["tgl"] = fmt_date
templates.env.filters["pct"] = fmt_pct


@app.get("/health")
def health():
    return {"ok": True, "demo_mode": settings.demo_mode,
            "db": str(settings.database_path)}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, horizon: int = Query(30),
              q: str = Query(""), kelas: str = Query(""),
              scan_id: int | None = Query(None)):
    if horizon not in (30, 365):
        horizon = 30
    scan = db.latest_done_scan(horizon) if scan_id is None else db.get_scan(scan_id)
    results = db.scan_results(scan["id"]) if scan else []
    if q:
        needle = q.strip().lower()
        results = [r for r in results
                   if needle in r.get("symbol", "").lower()
                   or needle in r.get("name", "").lower()
                   or needle in r.get("pair_id", "").lower()]
    if kelas:
        results = [r for r in results if r.get("classification") == kelas]
    running = db.latest_scan()
    scans = db.list_scans(10)
    return templates.TemplateResponse(
        request, "dashboard.html",
        {
            "horizon": horizon, "q": q, "kelas": kelas,
            "scan": scan, "results": results, "scans": scans,
            "running": running if running and running["status"] == "running" else None,
            "classes": ["Potensi Naik", "Netral / Menunggu", "Potensi Turun"],
            "demo": settings.demo_mode,
        },
    )


@app.post("/scan")
def trigger_scan(horizon_days: int = Form(...), max_coins: int = Form(40),
                 min_volume_idr: float = Form(100_000_000)):
    if horizon_days not in (30, 365):
        horizon_days = 30
    max_coins = max(5, min(120, int(max_coins)))
    scan_id = start_scan(horizon_days, max_coins, float(min_volume_idr))
    return RedirectResponse(f"/?horizon={horizon_days}", status_code=303)


@app.post("/scan/sync")
def trigger_scan_sync(horizon_days: int = Form(...), max_coins: int = Form(40),
                      min_volume_idr: float = Form(100_000_000)):
    """Versi sinkron — dipakai skrip/uji; dasbor memakai /scan (latar)."""
    run_scan(horizon_days, max(5, min(120, int(max_coins))), float(min_volume_idr))
    return RedirectResponse(f"/?horizon={horizon_days}", status_code=303)


@app.get("/api/scan/status")
def scan_status(scan_id: int | None = Query(None)):
    scan = db.get_scan(scan_id) if scan_id else db.latest_scan()
    return JSONResponse(scan or {})


def _sparkline(pair_id: str, days: int = 365) -> str:
    rows = db.candles(pair_id, limit_days=days)
    if len(rows) < 5:
        return ""
    closes = [float(r["close"]) for r in rows]
    lo, hi = min(closes), max(closes)
    span = (hi - lo) or 1.0
    width, height = 720, 180
    pts = []
    for i, c in enumerate(closes):
        x = i / (len(closes) - 1) * width
        y = height - 8 - (c - lo) / span * (height - 16)
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


@app.get("/coin/{pair_id}", response_class=HTMLResponse)
def coin_detail(request: Request, pair_id: str,
                scan_id: int | None = Query(None),
                horizon: int = Query(30)):
    if scan_id is None:
        scan = db.latest_done_scan(horizon if horizon in (30, 365) else 30)
    else:
        scan = db.get_scan(scan_id)
    result = db.result_for_pair(scan["id"], pair_id) if scan else None
    analogs = []
    if result:
        analogs = []
        for a in result.get("analogs", []):
            b = dict(a)
            b["date_label"] = fmt_date(a.get("date_ts"))
            analogs.append(b)
    return templates.TemplateResponse(
        request, "detail.html",
        {
            "pair_id": pair_id, "scan": scan, "result": result,
            "analogs": analogs,
            "spark": _sparkline(pair_id),
            "demo": settings.demo_mode,
        },
    )
