"""Klien API publik Indodax (tanpa kunci API).

Endpoint yang dipakai:
- GET /api/pairs                  -> semesta pair yang terdaftar
- GET /api/summaries              -> harga & volume 24 jam semua pair
- GET /tradingview/history_v2     -> riwayat candle OHLC (tf=1D harian)

Semua permintaan memakai User-Agent jelas dan dicoba ulang bila jaringan
berkedip, supaya pemindaian ratusan pair tidak gugur oleh satu timeout.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

import httpx

from .config import settings

_UA = {"User-Agent": "crypto-cycle-scanner/0.1 (+riset pribadi)"}


class IndodaxError(RuntimeError):
    pass


class IndodaxClient:
    def __init__(self, base: str | None = None, timeout: float = 30.0) -> None:
        self.base = (base or settings.indodax_base).rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, params: dict | None = None, attempts: int = 3):
        last: Exception | None = None
        for i in range(attempts):
            try:
                with httpx.Client(timeout=self.timeout, headers=_UA,
                                  follow_redirects=True) as client:
                    resp = client.get(f"{self.base}{path}", params=params)
                    resp.raise_for_status()
                    return resp.json()
            except Exception as exc:  # noqa: BLE001 - dicoba ulang lalu dilaporkan
                last = exc
                if i < attempts - 1:
                    time.sleep(0.8 * (i + 1))
        raise IndodaxError(f"gagal mengambil {path}: {last}")

    def pairs_idr(self) -> list[dict]:
        """Pair IDR aktif, dinormalisasi ke bentuk internal aplikasi."""
        raw = self._get("/api/pairs")
        out: list[dict] = []
        for p in raw or []:
            if p.get("base_currency") != "idr" or p.get("is_market_suspended"):
                continue
            out.append(
                {
                    "pair_id": p.get("ticker_id") or p.get("id") or "",
                    "symbol": (p.get("traded_currency_unit")
                               or p.get("traded_currency") or "").upper(),
                    "name": p.get("description") or p.get("traded_currency") or "",
                    "traded_currency": (p.get("traded_currency") or "").lower(),
                    "coingecko_id": p.get("coingecko_id") or "",
                    "logo": p.get("url_logo_png") or p.get("url_logo") or "",
                }
            )
        return [p for p in out if p["pair_id"]]

    def summaries(self) -> dict[str, dict]:
        """ticker_id -> ringkasan 24 jam (harga terakhir, volume IDR, bid/ask)."""
        raw = self._get("/api/summaries")
        tickers = (raw or {}).get("tickers") or {}
        out: dict[str, dict] = {}
        for ticker_id, t in tickers.items():
            try:
                last = float(t.get("last") or 0)
                buy = float(t.get("buy") or 0)
                sell = float(t.get("sell") or 0)
            except (TypeError, ValueError):
                continue
            spread = ((sell - buy) / last * 100.0) if last > 0 and sell >= buy > 0 else 0.0
            out[ticker_id] = {
                "name": t.get("name") or "",
                "last": last,
                "buy": buy,
                "sell": sell,
                "high": float(t.get("high") or 0),
                "low": float(t.get("low") or 0),
                "volume_idr": float(t.get("vol_idr") or 0),
                "spread_pct": spread,
            }
        return out

    def candles(self, pair_id: str, days: int) -> list[dict]:
        """Candle harian `days` hari terakhir untuk pair (mis. btc_idr)."""
        symbol = pair_id.replace("_", "").upper()
        now = int(time.time())
        raw = self._get(
            "/tradingview/history_v2",
            params={"symbol": symbol, "tf": "1D",
                    "from": now - int(days) * 86400, "to": now},
        )
        out: dict[int, dict] = {}
        for r in raw or []:
            try:
                ts = int(r["Time"])
                close = float(r["Close"])
            except (KeyError, TypeError, ValueError):
                continue
            if close <= 0:
                continue
            out[ts] = {
                "ts": ts,
                "open": float(r.get("Open") or close),
                "high": float(r.get("High") or close),
                "low": float(r.get("Low") or close),
                "close": close,
                "volume": float(r.get("Volume") or 0),
            }
        return [out[k] for k in sorted(out)]


def fetch_many(client: IndodaxClient, pair_ids: list[str], days: int,
               workers: int, on_done: Callable[[str, list[dict] | None], None]) -> None:
    """Ambil candle banyak pair secara paralel terbatas.

    Kegagalan satu pair tidak menggugurkan yang lain: on_done dipanggil
    dengan None untuk pair yang gagal supaya pemanggil bisa mencatatnya.
    """
    if not pair_ids:
        return
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(client.candles, pid, days): pid for pid in pair_ids}
        for fut in as_completed(futures):
            pid = futures[fut]
            try:
                on_done(pid, fut.result())
            except Exception:  # noqa: BLE001
                on_done(pid, None)
