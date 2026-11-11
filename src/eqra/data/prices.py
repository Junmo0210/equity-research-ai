"""주가·베타·무위험수익률 (Yahoo Finance chart API)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import numpy as np

from .http import get_json

UA = {"User-Agent": "Mozilla/5.0 (research tool)"}


def chart(symbol: str, range_: str = "2y", interval: str = "1wk") -> dict:
    d = get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                 params={"range": range_, "interval": interval}, headers=UA, ttl_hours=6)
    res = (d.get("chart") or {}).get("result")
    if not res:
        raise RuntimeError(f"가격 조회 실패: {symbol}")
    return res[0]


def current_price(symbol: str) -> tuple[float, str, str]:
    r = chart(symbol, "5d", "1d")
    m = r["meta"]
    ts = datetime.fromtimestamp(m.get("regularMarketTime", 0), tz=timezone.utc).strftime("%Y-%m-%d")
    return float(m["regularMarketPrice"]), ts, m.get("currency", "")


def _closes(r: dict) -> dict[int, float]:
    q = r["indicators"].get("adjclose", [{}])[0].get("adjclose") or r["indicators"]["quote"][0]["close"]
    return {t: c for t, c in zip(r["timestamp"], q) if c is not None}


def beta(symbol: str, index: str, years: int = 2, blume: bool = True,
         floor: float = 0.5, cap: float = 2.5) -> tuple[Optional[float], Optional[float], str]:
    """주간 수익률 회귀 베타. 반환: (조정 베타, 원시 베타, 설명)."""
    try:
        a, b = _closes(chart(symbol, f"{years}y", "1wk")), _closes(chart(index, f"{years}y", "1wk"))
    except RuntimeError as e:
        return None, None, str(e)
    common = sorted(set(a) & set(b))
    if len(common) < 52:
        return None, None, f"공통 관측치 부족({len(common)}주)"
    pa, pb = np.array([a[t] for t in common]), np.array([b[t] for t in common])
    ra, rb = np.diff(np.log(pa)), np.diff(np.log(pb))
    raw = float(np.cov(ra, rb, ddof=1)[0, 1] / np.var(rb, ddof=1))
    adj = 0.67 * raw + 0.33 if blume else raw
    clipped = min(max(adj, floor), cap)
    note = f"{index} 대비 주간 로그수익률 {len(ra)}개 회귀, 원시 β={raw:.3f}"
    if blume:
        note += ", Blume 조정(0.67·β+0.33)"
    if clipped != adj:
        note += f", 범위[{floor},{cap}]로 제한"
    return clipped, raw, note


def us_risk_free(fallback: float) -> tuple[float, str]:
    try:
        p, d, _ = current_price("^TNX")
        return p / 100, f"미 10년물 국채수익률(^TNX) {d}"
    except Exception as e:  # 조회 실패 시 설정값
        return fallback, f"^TNX 조회 실패 → 설정값 사용 ({e})"
