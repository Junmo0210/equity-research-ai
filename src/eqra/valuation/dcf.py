"""실적 추정 + WACC + DCF (FCFF). Excel 모델과 동일한 수식을 Python으로 구현해 교차검증한다."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..models import Financials, MarketData
from .assumptions import ForecastAssumptions

KD_MAX_SPREAD = 0.08   # 이자비용/차입금 기반 Kd 상한 = rf + 8%p


@dataclass
class WaccResult:
    rf: float
    erp: float
    beta: float
    cost_of_equity: float
    kd_pretax: float
    kd_note: str
    tax_rate: float
    kd_aftertax: float
    equity_value_mkt: float
    debt: float
    weight_equity: float
    weight_debt: float
    wacc: float


@dataclass
class DcfResult:
    scenario: str
    years: list[int]
    revenue: list[float]
    ebit: list[float]
    nopat: list[float]
    da: list[float]
    capex: list[float]
    nwc: list[float]
    delta_nwc: list[float]
    fcff: list[float]
    discount_factor: list[float]
    pv_fcff: list[float]
    terminal_value: float
    pv_terminal: float
    enterprise_value: float
    net_debt: float
    minority_interest: float
    equity_value: float
    shares: float
    value_per_share: float
    tv_share_of_ev: float
    growth: list[float] = field(default_factory=list)
    margin: list[float] = field(default_factory=list)


def total_debt(fin: Financials, year: int) -> float:
    return (fin.get(year, "short_term_debt") or 0) + (fin.get(year, "long_term_debt") or 0)


def net_debt(fin: Financials, year: int) -> float:
    cash = (fin.get(year, "cash") or 0) + (fin.get(year, "short_term_investments") or 0)
    return total_debt(fin, year) - cash


def compute_wacc(fin: Financials, mkt: MarketData, erp: float, marginal_tax: float, spread: float) -> WaccResult:
    y = fin.last_year
    rf = mkt.risk_free_rate
    beta = mkt.beta if mkt.beta is not None else 1.0
    ke = rf + beta * erp
    debt = total_debt(fin, y)
    prev = fin.years[-2] if len(fin.years) > 1 else y
    avg_debt = (debt + total_debt(fin, prev)) / 2
    interest = fin.get(y, "interest_expense")
    if interest and interest > 0 and avg_debt > 0:
        kd = min(max(interest / avg_debt, rf), rf + KD_MAX_SPREAD)
        note = f"이자비용/평균차입금, [rf, rf+{KD_MAX_SPREAD:.0%}] 범위 제한"
    else:
        kd = rf + spread
        note = f"이자비용 또는 차입금 없음 → rf + {spread:.2%}"
    kd_at = kd * (1 - marginal_tax)
    e = mkt.price * fin.shares_current.value
    we = e / (e + debt)
    wd = 1 - we
    return WaccResult(rf, erp, beta, ke, kd, note, marginal_tax, kd_at, e, debt, we, wd, we * ke + wd * kd_at)


def run_dcf(fin: Financials, a: ForecastAssumptions, wacc: float, scenario: str = "base",
            growth_delta: float = 0.0, margin_delta: float = 0.0, mid_year: bool = False) -> DcfResult:
    y0 = fin.last_year
    n = len(a.revenue_growth)
    rev_prev = fin.get(y0, "revenue")
    nwc_prev = rev_prev * a.nwc_pct_revenue
    g_list = [g + growth_delta for g in a.revenue_growth]
    m_list = [m + margin_delta for m in a.ebit_margin]
    r = DcfResult(scenario, [], [], [], [], [], [], [], [], [], [], [], 0, 0, 0, 0, 0, 0, 0, 0, 0,
                  growth=g_list, margin=m_list)
    for t in range(1, n + 1):
        rev = rev_prev * (1 + g_list[t - 1])
        ebit = rev * m_list[t - 1]
        nopat = ebit * (1 - a.tax_rate)
        da = rev * a.da_pct_revenue
        capex = rev * a.capex_pct_revenue
        nwc = rev * a.nwc_pct_revenue
        dnwc = nwc - nwc_prev
        fcff = nopat + da - capex - dnwc
        period = t - 0.5 if mid_year else t
        df = 1 / (1 + wacc) ** period
        for lst, v in [(r.years, y0 + t), (r.revenue, rev), (r.ebit, ebit), (r.nopat, nopat), (r.da, da),
                       (r.capex, capex), (r.nwc, nwc), (r.delta_nwc, dnwc), (r.fcff, fcff),
                       (r.discount_factor, df), (r.pv_fcff, fcff * df)]:
            lst.append(v)
        rev_prev, nwc_prev = rev, nwc
    g = a.terminal_growth
    r.terminal_value = r.fcff[-1] * (1 + g) / (wacc - g)
    r.pv_terminal = r.terminal_value / (1 + wacc) ** n
    r.enterprise_value = sum(r.pv_fcff) + r.pv_terminal
    r.net_debt = net_debt(fin, y0)
    r.minority_interest = fin.get(y0, "minority_interest") or 0
    r.equity_value = r.enterprise_value - r.net_debt - r.minority_interest
    r.shares = fin.shares_current.value
    r.value_per_share = r.equity_value / r.shares
    r.tv_share_of_ev = r.pv_terminal / r.enterprise_value if r.enterprise_value else 0
    return r


WACC_STEPS = [-0.01, -0.005, 0.0, 0.005, 0.01]
G_STEPS = [-0.005, -0.0025, 0.0, 0.0025, 0.005]


def sensitivity(fin: Financials, a: ForecastAssumptions, wacc: float, mid_year: bool = False) -> dict:
    grid = []
    for dw in WACC_STEPS:
        row = []
        for dg in G_STEPS:
            w, g = wacc + dw, a.terminal_growth + dg
            if w - g <= 0.005:
                row.append(None)
                continue
            a2 = a.model_copy(update={"terminal_growth": g})
            row.append(run_dcf(fin, a2, w, mid_year=mid_year).value_per_share)
        grid.append(row)
    return {"wacc": [wacc + d for d in WACC_STEPS], "g": [a.terminal_growth + d for d in G_STEPS], "values": grid}


def implied_metrics(r: DcfResult, fin: Financials) -> dict[str, Optional[float]]:
    """DCF 결과가 내포하는 멀티플 (합리성 점검용)."""
    y0 = fin.last_year
    ni = fin.get(y0, "net_income_parent")
    oi, da = fin.get(y0, "operating_income"), fin.get(y0, "d_and_a")
    ebitda = oi + da if oi is not None and da is not None else None
    return {
        "implied_pe": r.equity_value / ni if ni and ni > 0 else None,
        "implied_ev_ebitda": r.enterprise_value / ebitda if ebitda and ebitda > 0 else None,
    }
