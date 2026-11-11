"""5단계: 보고서 서술 (AI는 숫자를 직접 쓰지 않고 {{키}} 자리표시자만 사용) + 수치 감사."""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field

from .client import ClaudeClient

SECTION_KEYS = ["investment_summary", "business_overview", "financial_commentary",
                "forecast_commentary", "valuation_commentary", "risks"]
SECTION_TITLES = {
    "investment_summary": "투자 요약", "business_overview": "사업 개요", "financial_commentary": "재무 분석 해석",
    "forecast_commentary": "실적 추정 근거", "valuation_commentary": "밸류에이션 해석", "risks": "투자 리스크",
}


class ReportDraft(BaseModel):
    headline: str = Field(description="보고서 제목용 한 줄 핵심 메시지 (숫자는 자리표시자로)")
    investment_summary: str
    business_overview: str
    financial_commentary: str
    forecast_commentary: str
    valuation_commentary: str
    risks: str


SYSTEM = """당신은 증권사 리서치센터의 애널리스트로서 투자분석 보고서 본문을 작성합니다.
절대 규칙 — 숫자:
1. 숫자(금액·비율·배수·주가 등)를 직접 쓰지 마세요. 반드시 제공된 팩트시트의 자리표시자 {{키}}를 그대로 쓰세요.
   예: "목표주가 {{target_price}}는 현재가 대비 {{upside}}의 상승여력을 의미한다."
2. 팩트시트에 없는 수치가 필요하면 숫자 없이 정성적으로 서술하세요 ("큰 폭으로", "소폭" 등).
3. 연도(예: 2025년)와 기간·개수 표현(예: 5개년, 3개사)만 예외적으로 숫자로 쓸 수 있습니다.
4. 투자의견과 목표주가는 코드가 정한 값({{rating}}, {{target_price}})을 따르고 바꾸지 마세요.
서술 규칙: 각 섹션은 마크다운(소제목 없이 문단·글머리표)으로, 근거 → 해석 → 시사점 순서로 간결하게.
공시 요약과 가정 근거를 활용하되 제공되지 않은 사실을 지어내지 마세요. 한국어로 작성."""


def ai_report(client: ClaudeClient, facts: dict, context: dict) -> ReportDraft:
    fact_lines = "\n".join(f"- {{{{{k}}}}} = {v['text']}  ({v['desc']})" for k, v in facts.items())
    user = [{"type": "text", "text":
             "# 팩트시트 (자리표시자 = 코드 계산값)\n" + fact_lines +
             "\n\n# 분석 맥락 (사업 요약·가정 근거·리스크 가설)\n" + json.dumps(context, ensure_ascii=False, indent=1) +
             "\n\n위 자료로 보고서 각 섹션을 작성하세요."}]
    return client.structured("05_report", SYSTEM, user, ReportDraft)


PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
YEAR = re.compile(r"(FY\s?)?(19|20)\d{2}(년|E|A)?")
PERIOD = re.compile(r"\d+\s?(개년|년차|개년간|개 연도|개 분기|분기|개사|개)")   # 기간·개수 표현은 재무 수치가 아님


def render(text: str, facts: dict) -> tuple[str, list[str]]:
    unknown = []

    def sub(m):
        k = m.group(1)
        if k in facts:
            return facts[k]["text"]
        unknown.append(k)
        return f"[확인필요:{k}]"
    return PLACEHOLDER.sub(sub, text), unknown


def audit_numbers(text: str) -> list[str]:
    """자리표시자·연도를 제외하고 남은 숫자 = AI가 직접 쓴 수치 → 위반으로 기록."""
    stripped = PERIOD.sub("", YEAR.sub("", PLACEHOLDER.sub("", text)))
    hits = []
    for m in re.finditer(r"[\d][\d,\.]*\s*(%|배|x|원|달러|억|조|bp|p)?", stripped):
        s = stripped[max(0, m.start() - 15): m.end() + 10].replace("\n", " ")
        hits.append(s.strip())
    return hits


def rule_based_report(facts: dict, hyp) -> ReportDraft:
    a = hyp.assumptions
    return ReportDraft(
        headline="{{company_name}} — 투자의견 {{rating}}, 목표주가 {{target_price}}",
        investment_summary=("- 목표주가 {{target_price}}는 DCF({{dcf_value}})와 상대가치({{rel_value}})를 가중평균한 값이다.\n"
                            "- 현재가 {{price}} 대비 상승여력은 {{upside}}로, 투자의견은 {{rating}}이다.\n"
                            "- (AI 미사용 모드: 정성적 투자포인트는 생략되었다.)"),
        business_overview=hyp.business_summary,
        financial_commentary=("- 최근 회계연도 매출은 {{revenue_last}}, 전년 대비 성장률은 {{revenue_growth_last}}이다.\n"
                              "- 영업이익률은 {{op_margin_last}} (과거 3개년 평균 {{op_margin_3y}}), ROE는 {{roe_last}}이다."),
        forecast_commentary="\n".join(f"- **{r.assumption}**: {r.reasoning}" for r in a.rationales),
        valuation_commentary=("- WACC {{wacc}} (자기자본비용 {{ke}}, 베타 {{beta}}), 영구성장률 {{terminal_growth}} 기준 "
                              "DCF 주당가치는 {{dcf_value}}이며, 잔존가치 비중은 {{tv_share}}이다.\n"
                              "- 피어 중앙값 PER {{median_pe}}, EV/EBITDA {{median_ev_ebitda}}, PBR {{median_pb}} 적용 시 "
                              "상대가치 주당가치는 {{rel_value}}이다."),
        risks="- (AI 미사용 모드: 리스크 분석은 생략되었다.)",
    )
