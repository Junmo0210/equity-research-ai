"""기업명 → 시장·식별자 결정, 시장별 수집기로 분기."""
from __future__ import annotations

import re
from typing import Optional

from ..config import DART_API_KEY
from ..models import Company, Financials, FilingText
from . import dart, sec


def guess_market(query: str) -> str:
    if re.search(r"[가-힣]", query) or re.fullmatch(r"\d{6}(\.K[SQ])?", query.strip()):
        return "KR"
    return "US"


def resolve(query: str, market: Optional[str] = None) -> Company:
    market = (market or guess_market(query)).upper()
    q = re.sub(r"\.K[SQ]$", "", query.strip())
    if market == "KR":
        row = dart.search(q)
        if not row:
            raise LookupError(f"DART 상장사 목록에서 '{query}'를 찾지 못했습니다.")
        return dart.company_from_row(row, query)
    row = sec.search(q)
    if row:
        return sec.company_from_row(row, query)
    if DART_API_KEY:                       # 영문명 한국 기업일 수도 있음
        krow = dart.search(q)
        if krow:
            return dart.company_from_row(krow, query)
    raise LookupError(f"SEC 기업 목록에서 '{query}'를 찾지 못했습니다. 티커로 다시 시도해 보세요.")


def fetch_financials(company: Company, years: int = 6) -> Financials:
    return sec.fetch_financials(company, years) if company.market == "US" else dart.fetch_financials(company, years)


def fetch_filing_text(company: Company) -> Optional[FilingText]:
    return sec.fetch_filing_text(company) if company.market == "US" else dart.fetch_filing_text(company)
