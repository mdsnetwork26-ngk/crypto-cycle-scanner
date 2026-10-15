"""Penjadwal pemindaian berkala (benang latar di dalam aplikasi).

Aktif bila SCHEDULE_ENABLED=1. Setiap hari pada jam SCHEDULE_HOUR:
SCHEDULE_MINUTE (WIB), semua horizon di SCHEDULED_HORIZONS dipindai
bergantian memakai jalur yang sama dengan tombol manual (start_scan),
jadi tidak akan bertabrakan dengan pemindaian yang sedang berjalan.

Keputusan "sudah waktunya" murni fungsi dari jam jadwal hari ini dan
waktu mulai pemindaian terakhir per horizon di basis data — sehingga
aman dari duplikasi setelah aplikasi/server direstart: bila jam jadwal
sudah lewat tetapi hari ini belum ada pemindaian, ia langsung menyusul.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from .config import settings
from .services import db, start_scan

WIB = timezone(timedelta(hours=7))

_thread: threading.Thread | None = None
_stop = threading.Event()


def _today_schedule(now: datetime) -> datetime:
    return now.replace(hour=settings.schedule_hour,
                       minute=settings.schedule_minute,
                       second=0, microsecond=0)


def due_horizons(now: datetime, last_started: dict[int, int]) -> list[int]:
    """Horizon yang sudah melewati jam jadwal hari ini & belum dipindai."""
    if now < _today_schedule(now):
        return []
    sched_ts = int(_today_schedule(now).timestamp())
    return [h for h in settings.schedule_horizons
            if int(last_started.get(h, 0)) < sched_ts]


def next_run(now: datetime) -> datetime:
    """Waktu jadwal berikutnya (hari ini bila belum lewat, else besok)."""
    t = _today_schedule(now)
    return t if now < t else t + timedelta(days=1)


def _last_started_map() -> dict[int, int]:
    out: dict[int, int] = {}
    for h in settings.schedule_horizons:
        scan = db.latest_scan(h)
        out[h] = int(scan["started_at"]) if scan else 0
    return out


def _loop() -> None:
    while not _stop.wait(30):
        if not settings.schedule_enabled:
            continue
        try:
            for horizon in due_horizons(datetime.now(WIB), _last_started_map()):
                start_scan(horizon)  # None (diabaikan) bila ada yang berjalan
        except Exception:  # noqa: BLE001 - penjadwal tidak boleh mematikan app
            continue


def start_scheduler() -> threading.Thread | None:
    global _thread
    if not settings.schedule_enabled:
        return None
    if _thread is not None and _thread.is_alive():
        return _thread
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="penjadwal-pindai")
    _thread.start()
    return _thread


def schedule_info() -> dict:
    """Ringkasan jadwal untuk ditampilkan di dasbor."""
    now = datetime.now(WIB)
    return {
        "enabled": settings.schedule_enabled,
        "horizons": settings.schedule_horizons,
        "time": f"{settings.schedule_hour:02d}:{settings.schedule_minute:02d} WIB",
        "next": next_run(now).strftime("%d %b %Y %H:%M WIB"),
    }
