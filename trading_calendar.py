# -*- coding: utf-8 -*-
"""A股交易日历工具（用于「每周最后一个交易日」判定）

数据源：timor.tech 公共节假日 API（含调休），按年拉取并本地缓存，
离线/接口失败时退回内置节假日表，保证轮动推送不因网络问题中断。

口径：交易日 = 周一~周五 且 非法定节假日（调休上班的周末对股市仍休市，不适用）。
"""
import json
import os
from datetime import date, timedelta

import requests

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "holiday_cache.json")
_API = "https://timor.tech/api/holiday/year/{year}"

# 内置节假日兜底（仅工作日休市日；周末本就不开市无需列出）
_FALLBACK_HOLIDAYS = {
    "2026-01-01", "2026-01-02",
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
    "2026-04-06",
    "2026-05-01", "2026-05-04", "2026-05-05",
    "2026-06-19",
    "2026-09-25",
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",
    "2027-01-01",
}


def _load_cache() -> dict:
    if os.path.exists(CACHE):
        try:
            with open(CACHE, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    return {}


def _save_cache(d: dict) -> None:
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=0, sort_keys=True)
    except Exception:
        pass


def holidays_for_year(year: int) -> set[str]:
    """返回该年所有休市日（'YYYY-MM-DD'）集合，带缓存与兜底。"""
    key = str(year)
    cache = _load_cache()
    if key in cache and isinstance(cache[key], list):
        return set(cache[key])

    days: set[str] = set()
    try:
        r = requests.get(_API.format(year=year),
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        data = r.json() or {}
        for v in (data.get("holiday") or {}).values():
            d = str(v.get("date") or "")
            if v.get("holiday") and len(d) == 10:
                wd = date(int(d[:4]), int(d[5:7]), int(d[8:10])).weekday()
                if wd < 5:  # 只收工作日休市（周末本就休市）
                    days.add(d)
    except Exception as e:
        print(f"[calendar] {year} 节假日接口失败: {e}")

    if not days:
        days = {d for d in _FALLBACK_HOLIDAYS if d.startswith(key)}

    cache[key] = sorted(days)
    _save_cache(cache)
    return days


def is_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return d.isoformat() not in holidays_for_year(d.year)


def last_trading_day_of_week(d: date) -> date | None:
    """返回 d 所在自然周（周一~周日）的最后一个交易日；整周休市返回 None。"""
    monday = d - timedelta(days=d.weekday())
    last = None
    for i in range(5):  # 只看周一~周五
        day = monday + timedelta(days=i)
        if is_trading_day(day):
            last = day
    return last