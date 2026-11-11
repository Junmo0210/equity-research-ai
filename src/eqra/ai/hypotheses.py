"""3단계: 공시 해석 → 추정 가정·피어 가설 (AI) / 규칙 기반 대체 엔진."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from ..analysis import ratios as R
from ..models import LABELS_KO, FilingText, Financials, MarketData
from ..valuation.assumptions import ForecastAssumptions, Rationale, Scenario
from .client import ClaudeClient


class PeerSuggestion(BaseModel):
    identifier: str = Field(description="미국: 티커(예: MSFT). 한국: 6자리 종목코드 또는 정확한 회사명")
    market: str = Field(description="'US' 또는 'KR' — 대상 기업과 같은 시장")
    reason: str


class HypothesisOutput(BaseModel):
    business_summary: str = Field(description="사업 구조·수익원·경쟁 위치 요약 (숫자 사용 금지)")
    assumptions: ForecastAssumptions
    peers: list[PeerSuggestion] = Field(description="비교 기업 4~6개, 같은 상장 시장")


SYSTEM = """당신은 sell-side 주식 애널리스트입니다. 역할은 '해석과 가설 수립'에 한정됩니다.
- 모든 계산(비율, 추정치, 밸류에이션)은 별도의 검증된 코드가 수행합니다. 당신은 가정값과 그 근거만 제시합니다.
- 가정은 반드시 제공된 과거 실적 지표와 공시 원문에 근거해야 하며, 근거(evidence)에 인용하세요.
- 공시에 없는 사실을 지어내지 마세요. 불확실하면 보수적으로 가정하고 그 이유를 적으세요.
- 비율은 소수로 표기합니다 (5% = 0.05). 향후 5개년 값을 정확히 5개씩 제시하세요.
- 영구성장률은 장기 명목성장률을 넘지 않게, 영업이익률 경로는 과거 수준·경쟁 구도와 정합적으로 설정하세요.
- 피어는 사업 모델이 유사하고 같은 시장에 상장된 기업으로, 식별자를 정확히 쓰세요.
- 출력 언어: 한국어."""


def fmt_amount(v: Optional[float], currency: str) -> str:
    if v is None:
        return "-"
    return f"{v / 1e6:,.0f}"


def historical_table(fin: Financials, ratios: dict) -> str:
    cur = fin.company.currency
    keys = ["revenue", "gross_profit", "operating_income", "net_income_parent", "d_and_a", "capex", "cfo",
            "cash", "short_term_debt", "long_term_debt", "total_equity"]
    head = "| 항목 (단위: 백만 " + cur + ") | " + " | ".join(f"FY{y}" for y in fin.years) + " |"
    lines = [head, "|" + "---|" * (len(fin.years) + 1)]
    for k in keys:
        lines.append(f"| {LABELS_KO[k]} | " + " | ".join(fmt_amount(fin.get(y, k), cur) for y in fin.years) + " |")
    for k, (label, kind) in R.RATIO_DEFS.items():
        cells = []
        for y in fin.years:
            v = ratios[y].get(k)
            cells.append("-" if v is None else (f"{v:.1%}" if kind == "pct" else f"{v:.2f}x"))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def build_context(fin: Financials, ratios: dict, mkt: MarketData, filing: Optional[FilingText],
                  max_chars: int) -> tuple[list[dict], list[str]]:
    c = fin.company
    notes = []
    head = (f"# 대상 기업\n- 회사명: {c.name}\n- 시장/티커: {c.market} / {c.ticker}\n- 업종: {c.industry}\n"
            f"- 결산월: {c.fiscal_year_end}\n- 현재 주가: {mkt.price:,.2f} {mkt.currency} ({mkt.price_date})\n"
            f"- 베타(코드 산출): {mkt.beta}\n- 무위험수익률: {mkt.risk_free_rate}\n\n"
            f"# 과거 실적 (코드가 공시에서 표준화·계산한 값)\n{historical_table(fin, ratios)}\n")
    blocks = []
    if filing:
        budget = max_chars
        body = [f"# 공시 원문 발췌: {filing.title}\n출처: {filing.url}\n"]
        for name, txt in filing.sections.items():
            if budget <= 0:
                notes.append(f"공시 섹션 '{name}' 전체 생략 (AI 컨텍스트 상한)")
                continue
            part = txt[:budget]
            if len(part) < len(txt):
                notes.append(f"공시 섹션 '{name}' {len(txt):,}자 중 {len(part):,}자만 AI에 전달")
            body.append(f"\n## [{name}]\n{part}")
            budget -= len(part)
        # 공시 원문은 변하지 않으므로 앞쪽에 두어 프롬프트 캐시 대상이 되게 함
        blocks.append({"type": "text", "text": "\n".join(body)})
    else:
        notes.append("공시 원문을 가져오지 못해 재무 데이터만으로 가설 수립")
    blocks.append({"type": "text", "text": head + "\n위 자료를 바탕으로 사업 요약, 향후 5개년 추정 가정(근거 포함), "
                                                  "강세/약세 시나리오, 핵심 동인·리스크·투자포인트, 비교 기업을 제시하세요."})
    return blocks, notes


def ai_hypotheses(client: ClaudeClient, fin, ratios, mkt, filing, max_chars) -> tuple[HypothesisOutput, list[str]]:
    blocks, notes = build_context(fin, ratios, mkt, filing, max_chars)
    return client.structured("03_hypotheses", SYSTEM, blocks, HypothesisOutput), notes


# ── AI 미사용 시: 규칙 기반 가정 (과거 3년 평균 + 성장률 수렴) ──────────────
def _clamp(v, lo, hi):
    return min(max(v, lo), hi)


def rule_based_hypotheses(fin: Financials, ratios: dict, n_years: int, tg: float,
                          marginal_tax: float) -> HypothesisOutput:
    avg = lambda k, d: R.summary(ratios, k) if R.summary(ratios, k) is not None else d
    g0 = _clamp(avg("revenue_growth", 0.03), -0.05, 0.25)
    growth = [g0 + (tg - g0) * t / (n_years - 1) for t in range(n_years)]   # 1년차 = 과거평균 → 5년차 = 영구성장률
    margin = avg("operating_margin", 0.10)
    tax = avg("effective_tax_rate", marginal_tax)
    tax = tax if 0.05 <= tax <= 0.35 else marginal_tax
    a = ForecastAssumptions(
        revenue_growth=growth, ebit_margin=[margin] * n_years, tax_rate=tax,
        da_pct_revenue=avg("da_pct_revenue", 0.03), capex_pct_revenue=avg("capex_pct_revenue", 0.04),
        nwc_pct_revenue=avg("nwc_pct_revenue", 0.05), terminal_growth=tg,
        bull=Scenario(growth_delta=0.02, margin_delta=0.02, narrative="규칙 기반: 성장률·마진 각 +2%p"),
        bear=Scenario(growth_delta=-0.02, margin_delta=-0.02, narrative="규칙 기반: 성장률·마진 각 −2%p"),
        rationales=[
            Rationale(assumption="매출 성장률", reasoning="최근 3개년 평균 성장률에서 출발해 5년차에 영구성장률로 선형 수렴",
                      evidence=["과거 매출 성장률 (Ratios 시트)"]),
            Rationale(assumption="영업이익률", reasoning="최근 3개년 평균 영업이익률 유지", evidence=["과거 영업이익률"]),
            Rationale(assumption="세율·D&A·CAPEX·NWC", reasoning="최근 3개년 평균 비율 사용 (세율은 비정상 범위면 법정세율)",
                      evidence=["과거 유효세율, D&A/매출, CAPEX/매출, NWC/매출"]),
        ],
        key_drivers=["(AI 미사용) 과거 추세 연장"], risks=["(AI 미사용) 정성 리스크 분석 생략"],
        thesis=["(AI 미사용) 정성 투자포인트 생략"],
    )
    return HypothesisOutput(business_summary="(AI 미사용 모드: 사업 요약 생략)", assumptions=a, peers=[])
