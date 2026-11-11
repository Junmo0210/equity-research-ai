"""재무비율. Excel의 Ratios 시트와 같은 정의를 사용한다 (감사 시트에서 교차검증)."""
from __future__ import annotations

from statistics import mean
from typing import Optional

from ..models import Financials

RATIO_DEFS = {
    # key: (한글명, 형식)
    "revenue_growth": ("매출 성장률", "pct"),
    "gross_margin": ("매출총이익률", "pct"),
    "operating_margin": ("영업이익률", "pct"),
    "ebitda_margin": ("EBITDA 마진", "pct"),
    "net_margin": ("순이익률", "pct"),
    "effective_tax_rate": ("유효세율", "pct"),
    "roe": ("ROE (지배주주)", "pct"),
    "roa": ("ROA", "pct"),
    "roic": ("ROIC (세후)", "pct"),
    "debt_to_equity": ("부채비율 (총부채/자본)", "pct"),
    "net_debt_to_ebitda": ("순차입금/EBITDA", "x"),
    "current_ratio": ("유동비율", "pct"),
    "capex_pct_revenue": ("CAPEX/매출", "pct"),
    "da_pct_revenue": ("D&A/매출", "pct"),
    "nwc_pct_revenue": ("순운전자본/매출", "pct"),
    "fcf_margin": ("FCF 마진 (CFO−CAPEX)", "pct"),
}


def _div(a, b) -> Optional[float]:
    if a is None or b in (None, 0):
        return None
    return a / b


def compute(fin: Financials) -> dict[int, dict[str, Optional[float]]]:
    out = {}
    for i, y in enumerate(fin.years):
        g = lambda k, yy=y: fin.get(yy, k)
        prev = fin.years[i - 1] if i > 0 else None
        rev, oi, da = g("revenue"), g("operating_income"), g("d_and_a")
        ebitda = oi + da if oi is not None and da is not None else None
        debt = (g("short_term_debt") or 0) + (g("long_term_debt") or 0)
        cash = (g("cash") or 0) + (g("short_term_investments") or 0)
        net_debt = debt - cash
        tax = _div(g("income_tax"), g("pretax_income"))
        nwc = None
        if rev and g("accounts_receivable") is not None:
            nwc = (g("accounts_receivable") or 0) + (g("inventory") or 0) - (g("accounts_payable") or 0)
        invested = (g("total_equity") or 0) + net_debt
        t = min(max(tax, 0), 0.5) if tax is not None else None
        r = {
            "revenue_growth": _div(rev, g("revenue", prev)) - 1 if prev and _div(rev, g("revenue", prev)) else None,
            "gross_margin": _div(g("gross_profit"), rev),
            "operating_margin": _div(oi, rev),
            "ebitda_margin": _div(ebitda, rev),
            "net_margin": _div(g("net_income"), rev),
            "effective_tax_rate": tax,
            "roe": _div(g("net_income_parent"), g("equity_parent")),
            "roa": _div(g("net_income"), g("total_assets")),
            "roic": _div(oi * (1 - t), invested) if oi is not None and t is not None and invested > 0 else None,
            "debt_to_equity": _div(g("total_liabilities"), g("total_equity")),
            "net_debt_to_ebitda": _div(net_debt, ebitda) if ebitda and ebitda > 0 else None,
            "current_ratio": _div(g("total_current_assets"), g("total_current_liabilities")),
            "capex_pct_revenue": _div(g("capex"), rev),
            "da_pct_revenue": _div(da, rev),
            "nwc_pct_revenue": _div(nwc, rev),
            "fcf_margin": _div(g("cfo") - g("capex"), rev) if g("cfo") is not None and g("capex") is not None else None,
        }
        out[y] = r
    return out


def summary(ratios: dict[int, dict], key: str, last_n: int = 3) -> Optional[float]:
    vals = [ratios[y][key] for y in sorted(ratios)[-last_n:] if ratios[y].get(key) is not None]
    return mean(vals) if vals else None
