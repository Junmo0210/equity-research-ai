"""추정 가정 스키마. AI(또는 규칙 기반 엔진)가 채우고, 코드가 가드레일로 검증한다."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Rationale(BaseModel):
    assumption: str = Field(description="가정 항목명")
    reasoning: str = Field(description="공시·과거 실적에 근거한 설명 (숫자 없이 서술 가능)")
    evidence: list[str] = Field(description="근거가 된 공시 문장 또는 과거 지표 (짧게 인용)")


class Scenario(BaseModel):
    growth_delta: float = Field(description="기본 시나리오 대비 연간 매출성장률 가감 (소수, 예: 0.03)")
    margin_delta: float = Field(description="기본 시나리오 대비 영업이익률 가감 (소수)")
    narrative: str


class ForecastAssumptions(BaseModel):
    revenue_growth: list[float] = Field(description="향후 5개년 매출 성장률 (소수, 길이 5)")
    ebit_margin: list[float] = Field(description="향후 5개년 영업이익률 (소수, 길이 5)")
    tax_rate: float = Field(description="추정 유효세율 (소수)")
    da_pct_revenue: float = Field(description="감가상각비/매출 (소수)")
    capex_pct_revenue: float = Field(description="CAPEX/매출 (소수)")
    nwc_pct_revenue: float = Field(description="순운전자본/매출 (소수)")
    terminal_growth: float = Field(description="영구성장률 (소수)")
    bull: Scenario
    bear: Scenario
    rationales: list[Rationale]
    key_drivers: list[str] = Field(description="실적을 좌우하는 핵심 동인")
    risks: list[str] = Field(description="주요 리스크")
    thesis: list[str] = Field(description="투자 포인트 가설")


def apply_guardrails(a: ForecastAssumptions, guard: dict, n_years: int, tg_cap: float) -> list[str]:
    """범위를 벗어난 가정은 잘라내고 그 사실을 기록한다."""
    log = []

    def clip(name, v, lo, hi):
        nv = min(max(v, lo), hi)
        if nv != v:
            log.append(f"{name}: {v:.4f} → {nv:.4f} (허용범위 [{lo}, {hi}])")
        return nv

    def fit(lst, name):
        if len(lst) != n_years:
            log.append(f"{name}: 길이 {len(lst)} → {n_years}로 조정")
            lst = (list(lst) + [lst[-1]] * n_years)[:n_years] if lst else [0.0] * n_years
        return lst

    a.revenue_growth = [clip(f"매출성장률[{i+1}]", v, *guard["revenue_growth"]) for i, v in enumerate(fit(a.revenue_growth, "매출성장률"))]
    a.ebit_margin = [clip(f"영업이익률[{i+1}]", v, *guard["ebit_margin"]) for i, v in enumerate(fit(a.ebit_margin, "영업이익률"))]
    a.tax_rate = clip("세율", a.tax_rate, *guard["tax_rate"])
    a.da_pct_revenue = clip("D&A/매출", a.da_pct_revenue, *guard["da_pct_revenue"])
    a.capex_pct_revenue = clip("CAPEX/매출", a.capex_pct_revenue, *guard["capex_pct_revenue"])
    a.nwc_pct_revenue = clip("NWC/매출", a.nwc_pct_revenue, *guard["nwc_pct_revenue"])
    lo, hi = guard["terminal_growth"]
    a.terminal_growth = clip("영구성장률", a.terminal_growth, lo, min(hi, tg_cap))
    return log
