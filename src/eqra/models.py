"""파이프라인 전 단계가 공유하는 데이터 모델."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ── 표준 계정 (시장과 무관하게 동일한 키 사용) ───────────────────────────
INCOME_ITEMS = [
    "revenue", "cogs", "gross_profit", "sga", "operating_income",
    "interest_expense", "pretax_income", "income_tax", "net_income",
    "net_income_parent", "eps_diluted",
]
BALANCE_ITEMS = [
    "cash", "short_term_investments", "accounts_receivable", "inventory",
    "total_current_assets", "ppe", "total_assets", "accounts_payable",
    "total_current_liabilities", "short_term_debt", "long_term_debt",
    "lease_liabilities", "total_liabilities", "total_equity", "equity_parent",
    "minority_interest",
]
CASHFLOW_ITEMS = ["cfo", "capex", "d_and_a", "dividends_paid", "buybacks"]
OTHER_ITEMS = ["shares_outstanding"]
ALL_ITEMS = INCOME_ITEMS + BALANCE_ITEMS + CASHFLOW_ITEMS + OTHER_ITEMS

LABELS_KO = {
    "revenue": "매출액", "cogs": "매출원가", "gross_profit": "매출총이익",
    "sga": "판매비와관리비", "operating_income": "영업이익",
    "interest_expense": "이자비용", "pretax_income": "법인세차감전순이익",
    "income_tax": "법인세비용", "net_income": "당기순이익",
    "net_income_parent": "지배주주순이익", "eps_diluted": "희석EPS",
    "cash": "현금및현금성자산", "short_term_investments": "단기금융상품",
    "accounts_receivable": "매출채권", "inventory": "재고자산",
    "total_current_assets": "유동자산", "ppe": "유형자산", "total_assets": "자산총계",
    "accounts_payable": "매입채무", "total_current_liabilities": "유동부채",
    "short_term_debt": "단기차입금(유동성장기부채 포함)", "long_term_debt": "장기차입금·사채",
    "lease_liabilities": "리스부채", "total_liabilities": "부채총계",
    "total_equity": "자본총계", "equity_parent": "지배주주지분",
    "minority_interest": "비지배지분", "cfo": "영업활동현금흐름",
    "capex": "설비투자(CAPEX)", "d_and_a": "감가상각비(D&A)",
    "dividends_paid": "배당금지급", "buybacks": "자사주매입",
    "shares_outstanding": "유통주식수",
}


@dataclass
class Company:
    query: str
    name: str
    market: str                 # "US" | "KR"
    ticker: str                 # 가격 조회용 (AAPL, 005930.KS)
    currency: str
    cik: Optional[str] = None   # US
    corp_code: Optional[str] = None  # KR (DART)
    stock_code: Optional[str] = None
    industry: Optional[str] = None
    fiscal_year_end: Optional[str] = None


@dataclass
class LineItem:
    value: float
    source: str                 # 원천 태그 / 계정명 (추적용)


@dataclass
class Financials:
    """연도별 표준화 재무제표. data[year][item] = LineItem"""
    company: Company
    years: list[int] = field(default_factory=list)
    data: dict[int, dict[str, LineItem]] = field(default_factory=dict)
    source_note: str = ""
    warnings: list[str] = field(default_factory=list)
    shares_current: Optional[LineItem] = None   # 밸류에이션용 최신 유통주식수

    def get(self, year: int, item: str, default: Optional[float] = None) -> Optional[float]:
        li = self.data.get(year, {}).get(item)
        return li.value if li is not None else default

    def series(self, item: str) -> dict[int, Optional[float]]:
        return {y: self.get(y, item) for y in self.years}

    @property
    def last_year(self) -> int:
        return self.years[-1]


@dataclass
class MarketData:
    price: float
    price_date: str
    currency: str
    beta: Optional[float] = None
    beta_raw: Optional[float] = None
    beta_note: str = ""
    risk_free_rate: Optional[float] = None
    rf_note: str = ""


@dataclass
class FilingText:
    """AI 해석용 공시 원문 발췌."""
    title: str
    url: str
    sections: dict[str, str] = field(default_factory=dict)
    truncated: list[str] = field(default_factory=list)
