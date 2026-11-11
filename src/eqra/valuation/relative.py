"""상대가치평가: 피어 멀티플(중앙값) → 대상 기업 적정주가."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Optional

from ..models import Financials

PE_CAP = 100.0          # 이상치 제외 기준 (Excel과 동일)
EV_EBITDA_CAP = 60.0
PB_CAP = 30.0


@dataclass
class PeerMetrics:
    name: str
    ticker: str
    price: float
    shares: float
    net_income: Optional[float]       # 지배주주순이익
    ebitda: Optional[float]
    equity: Optional[float]           # 지배주주지분
    revenue: Optional[float]
    net_debt: float
    minority_interest: float
    fiscal_year: int
    reason: str = ""

    @property
    def market_cap(self) -> float:
        return self.price * self.shares

    @property
    def ev(self) -> float:
        return self.market_cap + self.net_debt + self.minority_interest

    def pe(self) -> Optional[float]:
        if self.net_income and self.net_income > 0:
            v = self.market_cap / self.net_income
            return v if v <= PE_CAP else None
        return None

    def ev_ebitda(self) -> Optional[float]:
        if self.ebitda and self.ebitda > 0:
            v = self.ev / self.ebitda
            return v if 0 < v <= EV_EBITDA_CAP else None
        return None

    def pb(self) -> Optional[float]:
        if self.equity and self.equity > 0:
            v = self.market_cap / self.equity
            return v if v <= PB_CAP else None
        return None


def metrics_from_financials(fin: Financials, price: float, reason: str = "") -> PeerMetrics:
    y = fin.last_year
    g = lambda k: fin.get(y, k)
    oi, da = g("operating_income"), g("d_and_a")
    debt = (g("short_term_debt") or 0) + (g("long_term_debt") or 0)
    cash = (g("cash") or 0) + (g("short_term_investments") or 0)
    return PeerMetrics(
        name=fin.company.name, ticker=fin.company.ticker, price=price, shares=fin.shares_current.value,
        net_income=g("net_income_parent"), ebitda=oi + da if oi is not None and da is not None else None,
        equity=g("equity_parent"), revenue=g("revenue"), net_debt=debt - cash,
        minority_interest=g("minority_interest") or 0, fiscal_year=y, reason=reason)


@dataclass
class RelativeResult:
    peers: list[PeerMetrics]
    median_pe: Optional[float]
    median_ev_ebitda: Optional[float]
    median_pb: Optional[float]
    implied: dict[str, Optional[float]]       # 멀티플별 주당가치
    value_per_share: Optional[float]          # 산출 가능한 멀티플 주당가치의 단순평균


def _med(vals):
    vals = [v for v in vals if v is not None]
    return median(vals) if vals else None


def run_relative(target: PeerMetrics, peers: list[PeerMetrics]) -> RelativeResult:
    mpe = _med([p.pe() for p in peers])
    mev = _med([p.ev_ebitda() for p in peers])
    mpb = _med([p.pb() for p in peers])
    imp: dict[str, Optional[float]] = {"PER": None, "EV/EBITDA": None, "PBR": None}
    if mpe and target.net_income and target.net_income > 0:
        imp["PER"] = mpe * target.net_income / target.shares
    if mev and target.ebitda and target.ebitda > 0:
        imp["EV/EBITDA"] = (mev * target.ebitda - target.net_debt - target.minority_interest) / target.shares
    if mpb and target.equity and target.equity > 0:
        imp["PBR"] = mpb * target.equity / target.shares
    vals = [v for v in imp.values() if v is not None]
    return RelativeResult(peers, mpe, mev, mpb, imp, sum(vals) / len(vals) if vals else None)
