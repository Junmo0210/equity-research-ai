"""미국: SEC EDGAR (XBRL companyfacts + 10-K 원문)."""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from lxml import html as lxml_html

from ..config import SEC_USER_AGENT
from ..models import Company, Financials, FilingText, LineItem
from .http import get, get_json

HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}

# 표준 계정 → XBRL 태그 후보 (우선순위 순). 튜플은 "가용한 구성요소의 합".
CONCEPTS: dict[str, list] = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet"],
    "cogs": ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold", "CostOfServices"],
    "gross_profit": ["GrossProfit"],
    "sga": ["SellingGeneralAndAdministrativeExpense"],
    "operating_income": ["OperatingIncomeLoss"],
    "interest_expense": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"],
    "pretax_income": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "income_tax": ["IncomeTaxExpenseBenefit"],
    "net_income": ["ProfitLoss", "NetIncomeLoss"],
    "net_income_parent": ["NetIncomeLoss"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "short_term_investments": ["MarketableSecuritiesCurrent", "ShortTermInvestments", "AvailableForSaleSecuritiesCurrent",
                               "AvailableForSaleSecuritiesDebtSecuritiesCurrent"],
    "accounts_receivable": ["AccountsReceivableNetCurrent"],
    "inventory": ["InventoryNet"],
    "total_current_assets": ["AssetsCurrent"],
    "ppe": ["PropertyPlantAndEquipmentNet"],
    "total_assets": ["Assets"],
    "accounts_payable": ["AccountsPayableCurrent", "AccountsPayableAndAccruedLiabilitiesCurrent"],
    "total_current_liabilities": ["LiabilitiesCurrent"],
    "short_term_debt": ["DebtCurrent", ("LongTermDebtCurrent", "CommercialPaper", "ShortTermBorrowings")],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"],
    "lease_liabilities": ["OperatingLeaseLiability"],
    "total_liabilities": ["Liabilities"],
    "total_equity": ["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "StockholdersEquity"],
    "equity_parent": ["StockholdersEquity"],
    "minority_interest": ["MinorityInterest"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": [("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireIntangibleAssets"),
              "PaymentsToAcquireProductiveAssets"],
    "d_and_a": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
                "DepreciationAmortizationAndAccretionNet", "Depreciation"],
    "dividends_paid": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock"],
    "shares_outstanding": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
}
INSTANT_ITEMS = {"cash", "short_term_investments", "accounts_receivable", "inventory", "total_current_assets",
                 "ppe", "total_assets", "accounts_payable", "total_current_liabilities", "short_term_debt",
                 "long_term_debt", "lease_liabilities", "total_liabilities", "total_equity", "equity_parent",
                 "minority_interest"}


# ── 기업 검색 ─────────────────────────────────────────────────────────
def load_tickers() -> list[dict]:
    d = get_json("https://www.sec.gov/files/company_tickers.json", headers=HEADERS, ttl_hours=24 * 7)
    return list(d.values())


_SUFFIXES = {"inc", "incorporated", "corp", "corporation", "co", "company", "plc", "ltd", "limited", "llc",
             "holdings", "group", "the", "sa", "nv", "ag", "de", "del"}


def _norm(s: str) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", s.lower()).split()
    return " ".join(w for w in words if w not in _SUFFIXES)


def search(query: str) -> Optional[dict]:
    q = query.strip().lower()
    rows = load_tickers()
    for r in rows:                                   # 1) 티커 정확히 일치
        if r["ticker"].lower() == q:
            return r
    nq = _norm(q)
    for r in rows:                                   # 2) 회사명 정확히 일치 (법인 접미어·문장부호 무시)
        if _norm(r["title"]) == nq:
            return r
    cands = [r for r in rows if _norm(r["title"]).startswith(nq + " ") or _norm(r["title"]) == nq]  # 3) 접두 일치 (목록 순서 = 시가총액 큰 순)
    return cands[0] if cands else None


def company_from_row(row: dict, query: str) -> Company:
    cik = str(row["cik_str"]).zfill(10)
    sub = get_json(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=HEADERS)
    return Company(query=query, name=sub.get("name") or row["title"], market="US", ticker=row["ticker"],
                   currency="USD", cik=cik, industry=sub.get("sicDescription"),
                   fiscal_year_end=sub.get("fiscalYearEnd"))


# ── 재무제표 ──────────────────────────────────────────────────────────
def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _annual_values(facts: dict, concept: str, instant: bool) -> dict[int, float]:
    """10-K 기준 연간값. 같은 기간이 여러 공시에 나오면 가장 최근 공시(정정 반영)를 사용."""
    node = facts.get("us-gaap", {}).get(concept)
    if not node:
        return {}
    units = node["units"].get("USD") or node["units"].get("USD/shares") or node["units"].get("shares")
    if not units:
        return {}
    best: dict[str, dict] = {}
    for x in units:
        if not str(x.get("form", "")).startswith("10-K"):
            continue
        if instant:
            if "start" in x:
                continue
        else:
            if "start" not in x or not (350 <= _days(x["start"], x["end"]) <= 380):
                continue
        prev = best.get(x["end"])
        if prev is None or x["filed"] > prev["filed"]:
            best[x["end"]] = x
    return {int(end[:4]): float(x["val"]) for end, x in best.items()}


def fetch_financials(company: Company, years: int = 6) -> Financials:
    facts = get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{company.cik}.json", headers=HEADERS)["facts"]
    fin = Financials(company=company, source_note=f"SEC EDGAR XBRL companyfacts (CIK {company.cik}), 10-K 연간값")
    per_year: dict[int, dict[str, LineItem]] = {}
    for item, cands in CONCEPTS.items():
        instant = item in INSTANT_ITEMS
        for cand in cands:                       # 연도별로 우선순위가 높은 태그부터 채움
            if isinstance(cand, tuple):
                parts = {c: _annual_values(facts, c, instant) for c in cand}
                yrs = set().union(*[set(v) for v in parts.values()])
                vals = {}
                for y in yrs:
                    used = [c for c in cand if y in parts[c]]
                    vals[y] = (sum(parts[c][y] for c in used), "+".join(used))
            else:
                vals = {y: (v, cand) for y, v in _annual_values(facts, cand, instant).items()}
            for y, (v, src) in vals.items():
                per_year.setdefault(y, {})
                if item not in per_year[y]:
                    per_year[y][item] = LineItem(abs(v) if item in ("capex", "dividends_paid", "buybacks") else v,
                                                 f"us-gaap:{src}")
    # 매출이 있는 연도만, 최근 N년
    yrs = sorted(y for y, d in per_year.items() if "revenue" in d and "total_assets" in d)[-years:]
    fin.years = yrs
    fin.data = {y: per_year[y] for y in yrs}

    dei = facts.get("dei", {}).get("EntityCommonStockSharesOutstanding")
    if dei:
        pts = sorted(dei["units"]["shares"], key=lambda x: (x["end"], x["filed"]))
        if pts:
            # 다중 클래스 주식(예: GOOGL)은 같은 날짜에 여러 값 → 합산
            last_end = pts[-1]["end"]
            latest_filed = max(p["filed"] for p in pts if p["end"] == last_end)
            vals = [p["val"] for p in pts if p["end"] == last_end and p["filed"] == latest_filed]
            fin.shares_current = LineItem(float(sum(vals)), f"dei:EntityCommonStockSharesOutstanding ({last_end})")
    return fin


# ── 10-K 원문 ─────────────────────────────────────────────────────────
# 제목은 줄의 시작에 단독으로 나온다는 점을 이용 (본문 속 "see Item 1A" 같은 상호참조 배제)
SECTION_PATTERNS = {
    "business": (r"^\s*item\s*1\s*[.:\-—]?\s*business\b", r"^\s*item\s*1a\s*[.:\-—]?\s*risk\s*factors"),
    "risk_factors": (r"^\s*item\s*1a\s*[.:\-—]?\s*risk\s*factors", r"^\s*item\s*(1b|1c|2)\s*[.:\-—]?"),
    "mdna": (r"^\s*item\s*7\s*[.:\-—]?\s*management.{0,3}s\s*discussion",
             r"^\s*item\s*(7a|8)\s*[.:\-—]?"),
}
MAX_SECTION_CHARS = 120_000
BLOCK_TAGS = {"div", "p", "tr", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "td"}


def _html_to_text(raw: bytes) -> str:
    doc = lxml_html.fromstring(raw)
    for bad in doc.xpath("//script|//style"):
        bad.drop_tree()
    for el in doc.iter():
        if isinstance(el.tag, str) and el.tag.lower().split("}")[-1] in BLOCK_TAGS:
            el.tail = ("\n" if el.tag.lower() != "td" else " | ") + (el.tail or "")
    text = re.sub(r"[ \t\xa0]+", " ", doc.text_content())
    return re.sub(r"\n\s*\n+", "\n\n", text)


def _extract_section(text: str, start_pat: str, end_pat: str) -> tuple[str, bool]:
    """목차에도 같은 제목이 나오므로, 시작~끝 구간이 가장 긴 것을 본문으로 간주."""
    best = ""
    for m in re.finditer(start_pat, text, flags=re.I | re.M):
        e = re.search(end_pat, text[m.end():], flags=re.I | re.M)
        if not e:
            continue
        chunk = text[m.start(): m.end() + e.start()]
        if len(chunk) > len(best):
            best = chunk
    best = best.strip()
    truncated = len(best) > MAX_SECTION_CHARS
    return best[:MAX_SECTION_CHARS], truncated


def fetch_filing_text(company: Company) -> Optional[FilingText]:
    sub = get_json(f"https://data.sec.gov/submissions/CIK{company.cik}.json", headers=HEADERS)
    r = sub["filings"]["recent"]
    for i, form in enumerate(r["form"]):
        if form == "10-K":
            accn = r["accessionNumber"][i].replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{int(company.cik)}/{accn}/{r['primaryDocument'][i]}"
            text = _html_to_text(get(url, headers=HEADERS, ttl_hours=24 * 30))
            ft = FilingText(title=f"Form 10-K (기준일 {r['reportDate'][i]})", url=url)
            for name, (s, e) in SECTION_PATTERNS.items():
                sec, cut = _extract_section(text, s, e)
                if sec:
                    ft.sections[name] = sec
                    if cut:
                        ft.truncated.append(f"{name}: {MAX_SECTION_CHARS:,}자에서 잘림")
            return ft
    return None
