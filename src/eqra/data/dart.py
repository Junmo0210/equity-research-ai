"""한국: 금융감독원 OpenDART (재무제표 전체계정 + 사업보고서 원문).

API 문서: https://opendart.fss.or.kr/guide/main.do
필요: 환경변수 DART_API_KEY (무료 발급)
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import date
from typing import Optional

from lxml import etree

from ..config import DART_API_KEY
from ..models import Company, Financials, FilingText, LineItem
from .http import get, get_json

BASE = "https://opendart.fss.or.kr/api"
ANNUAL = "11011"  # 사업보고서

# 표준 계정 → (IFRS/DART account_id 후보, 계정명 후보). 튜플 항목은 합산 그룹.
ACCOUNTS: dict[str, dict] = {
    "revenue": {"ids": ["ifrs-full_Revenue"], "names": ["매출액", "수익(매출액)", "영업수익", "매출"], "sj": ("IS", "CIS")},
    "cogs": {"ids": ["ifrs-full_CostOfSales"], "names": ["매출원가", "영업비용"], "sj": ("IS", "CIS")},
    "gross_profit": {"ids": ["ifrs-full_GrossProfit"], "names": ["매출총이익"], "sj": ("IS", "CIS")},
    "sga": {"ids": ["dart_TotalSellingGeneralAdministrativeExpenses"], "names": ["판매비와관리비", "판매비와 관리비"], "sj": ("IS", "CIS")},
    "operating_income": {"ids": ["dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"],
                         "names": ["영업이익", "영업이익(손실)", "영업손익"], "sj": ("IS", "CIS")},
    "interest_expense": {"ids": [], "names": ["이자비용"], "sj": ("IS", "CIS")},
    "pretax_income": {"ids": ["ifrs-full_ProfitLossBeforeTax"], "names": ["법인세비용차감전순이익", "법인세비용차감전순이익(손실)"], "sj": ("IS", "CIS")},
    "income_tax": {"ids": ["ifrs-full_IncomeTaxExpenseContinuingOperations"], "names": ["법인세비용"], "sj": ("IS", "CIS")},
    "net_income": {"ids": ["ifrs-full_ProfitLoss"], "names": ["당기순이익", "당기순이익(손실)"], "sj": ("IS", "CIS")},
    "net_income_parent": {"ids": ["ifrs-full_ProfitLossAttributableToOwnersOfParent"],
                          "names": ["지배기업 소유주지분", "지배기업의 소유주에게 귀속되는 당기순이익", "지배기업소유주지분"], "sj": ("IS", "CIS")},
    "eps_diluted": {"ids": ["ifrs-full_DilutedEarningsLossPerShare"], "names": ["희석주당이익", "희석주당이익(손실)"], "sj": ("IS", "CIS")},
    "cash": {"ids": ["ifrs-full_CashAndCashEquivalents"], "names": ["현금및현금성자산"], "sj": ("BS",)},
    "short_term_investments": {"ids": [], "names": ["단기금융상품", "단기투자자산"], "sj": ("BS",)},
    "accounts_receivable": {"ids": ["dart_ShortTermTradeReceivable", "ifrs-full_TradeAndOtherCurrentReceivables"],
                            "names": ["매출채권", "매출채권 및 기타채권"], "sj": ("BS",)},
    "inventory": {"ids": ["ifrs-full_Inventories"], "names": ["재고자산"], "sj": ("BS",)},
    "total_current_assets": {"ids": ["ifrs-full_CurrentAssets"], "names": ["유동자산"], "sj": ("BS",)},
    "ppe": {"ids": ["ifrs-full_PropertyPlantAndEquipment"], "names": ["유형자산"], "sj": ("BS",)},
    "total_assets": {"ids": ["ifrs-full_Assets"], "names": ["자산총계"], "sj": ("BS",)},
    "accounts_payable": {"ids": ["dart_ShortTermTradePayables", "ifrs-full_TradeAndOtherCurrentPayables"],
                         "names": ["매입채무", "매입채무 및 기타채무"], "sj": ("BS",)},
    "total_current_liabilities": {"ids": ["ifrs-full_CurrentLiabilities"], "names": ["유동부채"], "sj": ("BS",)},
    "short_term_debt": {"sum": [
        {"ids": ["ifrs-full_ShortTermBorrowings"], "names": ["단기차입금"]},
        {"ids": ["ifrs-full_CurrentPortionOfLongtermBorrowings"], "names": ["유동성장기부채", "유동성장기차입금"]},
        {"ids": [], "names": ["유동성사채"]},
    ], "sj": ("BS",)},
    "long_term_debt": {"sum": [
        {"ids": ["ifrs-full_LongtermBorrowings"], "names": ["장기차입금"]},
        {"ids": ["dart_BondsIssued"], "names": ["사채"]},
    ], "sj": ("BS",)},
    "lease_liabilities": {"sum": [
        {"ids": ["ifrs-full_CurrentLeaseLiabilities"], "names": ["유동리스부채"]},
        {"ids": ["ifrs-full_NoncurrentLeaseLiabilities"], "names": ["비유동리스부채"]},
    ], "sj": ("BS",)},
    "total_liabilities": {"ids": ["ifrs-full_Liabilities"], "names": ["부채총계"], "sj": ("BS",)},
    "total_equity": {"ids": ["ifrs-full_Equity"], "names": ["자본총계"], "sj": ("BS",)},
    "equity_parent": {"ids": ["ifrs-full_EquityAttributableToOwnersOfParent"],
                      "names": ["지배기업 소유주지분", "지배기업의 소유주에게 귀속되는 자본", "지배기업소유주지분"], "sj": ("BS",)},
    "minority_interest": {"ids": ["ifrs-full_NoncontrollingInterests"], "names": ["비지배지분"], "sj": ("BS",)},
    "cfo": {"ids": ["ifrs-full_CashFlowsFromUsedInOperatingActivities"],
            "names": ["영업활동현금흐름", "영업활동으로 인한 현금흐름", "영업활동으로인한현금흐름"], "sj": ("CF",)},
    "capex": {"sum": [
        {"ids": ["ifrs-full_PurchaseOfPropertyPlantAndEquipment"], "names": ["유형자산의 취득", "유형자산의취득"]},
        {"ids": ["ifrs-full_PurchaseOfIntangibleAssets"], "names": ["무형자산의 취득", "무형자산의취득"]},
    ], "sj": ("CF",), "abs": True},
    "d_and_a": {"sum": [
        {"ids": ["ifrs-full_AdjustmentsForDepreciationExpense"], "names": ["감가상각비"]},
        {"ids": ["ifrs-full_AdjustmentsForAmortisationExpense"], "names": ["무형자산상각비"]},
        {"ids": [], "names": ["사용권자산상각비"]},
    ], "sj": ("CF",)},
    "dividends_paid": {"ids": ["ifrs-full_DividendsPaidClassifiedAsFinancingActivities"],
                       "names": ["배당금지급", "배당금의 지급", "배당금의지급"], "sj": ("CF",), "abs": True},
    "buybacks": {"ids": [], "names": ["자기주식의 취득", "자기주식의취득"], "sj": ("CF",), "abs": True},
}


class DartError(RuntimeError):
    pass


def _key() -> str:
    if not DART_API_KEY:
        raise DartError("DART_API_KEY 환경변수가 없습니다. https://opendart.fss.or.kr 에서 무료 발급 후 설정하세요.")
    return DART_API_KEY


def _api(endpoint: str, ttl_hours: float = 24, **params) -> dict:
    d = get_json(f"{BASE}/{endpoint}", params={"crtfc_key": _key(), **params}, ttl_hours=ttl_hours)
    status = d.get("status")
    if status == "013":           # 조회된 데이터 없음
        return {"list": []}
    if status != "000":
        raise DartError(f"DART {endpoint} 오류 {status}: {d.get('message')}")
    return d


def parse_amount(s) -> Optional[float]:
    if s is None:
        return None
    s = str(s).strip().replace(",", "")
    if s in ("", "-"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


# ── 기업 검색 ─────────────────────────────────────────────────────────
def load_corp_codes() -> list[dict]:
    raw = get(f"{BASE}/corpCode.xml", params={"crtfc_key": _key()}, ttl_hours=24 * 7)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        root = etree.fromstring(z.read(z.namelist()[0]))
    out = []
    for el in root.iter("list"):
        rec = {c.tag: (c.text or "").strip() for c in el}
        if rec.get("stock_code"):           # 상장사만
            out.append(rec)
    return out


def search(query: str) -> Optional[dict]:
    q = query.strip()
    rows = load_corp_codes()
    if re.fullmatch(r"\d{6}", q):
        return next((r for r in rows if r["stock_code"] == q), None)
    norm = lambda s: re.sub(r"\s|\(주\)|주식회사", "", s).lower()
    nq = norm(q)
    exact = [r for r in rows if norm(r["corp_name"]) == nq]
    if exact:
        return exact[0]
    part = sorted([r for r in rows if nq in norm(r["corp_name"])], key=lambda r: len(r["corp_name"]))
    return part[0] if part else None


def company_from_row(row: dict, query: str) -> Company:
    info = _api("company.json", ttl_hours=24 * 7, corp_code=row["corp_code"])
    suffix = {"Y": ".KS", "K": ".KQ"}.get(info.get("corp_cls"), ".KS")
    return Company(query=query, name=info.get("corp_name") or row["corp_name"], market="KR",
                   ticker=row["stock_code"] + suffix, currency="KRW", corp_code=row["corp_code"],
                   stock_code=row["stock_code"], industry=f"KSIC {info.get('induty_code', '')}",
                   fiscal_year_end=info.get("acc_mt"))


# ── 재무제표 ──────────────────────────────────────────────────────────
def _match(rows: list[dict], ids: list[str], names: list[str], sj: tuple) -> Optional[dict]:
    rows = [r for r in rows if r.get("sj_div") in sj]
    for i in ids:
        for r in rows:
            if r.get("account_id") == i:
                return r
    for n in names:
        for r in rows:
            if re.sub(r"\s", "", r.get("account_nm", "")) == re.sub(r"\s", "", n):
                return r
    return None


def standardize_rows(rows: list[dict]) -> dict[str, LineItem]:
    """DART 전체계정 응답(한 연도) → 표준 계정. 단위: 원."""
    out: dict[str, LineItem] = {}
    for item, spec in ACCOUNTS.items():
        sj = spec["sj"]
        if "sum" in spec:
            total, used = 0.0, []
            for part in spec["sum"]:
                r = _match(rows, part["ids"], part["names"], sj)
                v = parse_amount(r.get("thstrm_amount")) if r else None
                if v is not None:
                    total += abs(v) if spec.get("abs") else v
                    used.append(r["account_nm"])
            if used:
                out[item] = LineItem(total, "DART:" + "+".join(used))
        else:
            r = _match(rows, spec["ids"], spec["names"], sj)
            v = parse_amount(r.get("thstrm_amount")) if r else None
            if v is not None:
                out[item] = LineItem(abs(v) if spec.get("abs") else v, f"DART:{r['account_nm']}({r.get('account_id', '')})")
    return out


def fetch_financials(company: Company, years: int = 5) -> Financials:
    fin = Financials(company=company)
    this_year = date.today().year
    fs_used = None
    data: dict[int, dict[str, LineItem]] = {}
    for y in range(this_year, this_year - years - 2, -1):
        rows = []
        for fs_div in ([fs_used] if fs_used else ["CFS", "OFS"]):   # 연결 우선, 없으면 별도
            rows = _api("fnlttSinglAcntAll.json", ttl_hours=24 * 7, corp_code=company.corp_code,
                        bsns_year=str(y), reprt_code=ANNUAL, fs_div=fs_div).get("list", [])
            if rows:
                fs_used = fs_div
                break
        if rows:
            std = standardize_rows(rows)
            if "revenue" in std:
                data[y] = std
        if len(data) >= years:
            break
    fin.years = sorted(data)
    fin.data = data
    fin.source_note = f"OpenDART 단일회사 전체 재무제표 ({'연결' if fs_used == 'CFS' else '별도'}), 사업보고서 기준, 단위: 원"
    if fs_used == "OFS":
        fin.warnings.append("연결재무제표가 없어 별도재무제표를 사용했습니다.")
    if fin.years:
        fin.shares_current = fetch_shares(company, fin.last_year)
        if fin.shares_current:
            for y in fin.years:   # 연도별 주식수: 해당 연도 보고서에서 조회 (실패 시 생략)
                li = fetch_shares(company, y)
                if li:
                    data[y]["shares_outstanding"] = li
    return fin


def fetch_shares(company: Company, year: int) -> Optional[LineItem]:
    """주식의 총수 현황 → 유통주식수 (보통주 + 우선주 합계)."""
    rows = _api("stockTotqySttus.json", ttl_hours=24 * 7, corp_code=company.corp_code,
                bsns_year=str(year), reprt_code=ANNUAL).get("list", [])
    tot = next((r for r in rows if r.get("se", "").replace(" ", "") in ("합계", "계")), None)
    common = next((r for r in rows if "보통" in r.get("se", "")), None)
    row = tot or common
    if not row:
        return None
    v = parse_amount(row.get("distb_stock_co"))
    if v is None:
        return None
    return LineItem(v, f"DART 주식총수현황 {year} 유통주식수({row.get('se')})")


# ── 사업보고서 원문 ───────────────────────────────────────────────────
SECTIONS = {
    "business": (r"II\.\s*사업의\s*내용", r"III\.\s*재무에\s*관한\s*사항"),
    "mdna": (r"IV\.\s*이사의\s*경영진단\s*및\s*분석의견", r"V\.\s*(회계)?감사인"),
}
MAX_SECTION_CHARS = 120_000


def fetch_filing_text(company: Company) -> Optional[FilingText]:
    end = date.today()
    lst = _api("list.json", corp_code=company.corp_code, bgn_de=f"{end.year - 2}0101", end_de=end.strftime("%Y%m%d"),
               pblntf_detail_ty="A001", page_count="100").get("list", [])
    lst = [x for x in lst if "사업보고서" in x.get("report_nm", "")]
    if not lst:
        return None
    latest = sorted(lst, key=lambda x: x["rcept_dt"])[-1]
    raw = get(f"{BASE}/document.xml", params={"crtfc_key": _key(), "rcept_no": latest["rcept_no"]}, ttl_hours=24 * 30)
    texts = []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for name in sorted(z.namelist()):
            xml = z.read(name)
            try:
                root = etree.fromstring(xml, parser=etree.XMLParser(recover=True, huge_tree=True))
                texts.append("\n".join(t.strip() for t in root.itertext() if t.strip()))
            except etree.XMLSyntaxError:
                texts.append(re.sub(r"<[^>]+>", " ", xml.decode("utf-8", "ignore")))
    text = "\n".join(texts)
    ft = FilingText(title=f"{latest['report_nm']} (접수 {latest['rcept_dt']})",
                    url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={latest['rcept_no']}")
    for name, (s, e) in SECTIONS.items():
        best = ""
        for m in re.finditer(s, text):
            em = re.search(e, text[m.end():])
            if em:
                chunk = text[m.start(): m.end() + em.start()]
                if len(chunk) > len(best):
                    best = chunk
        if best:
            if len(best) > MAX_SECTION_CHARS:
                ft.truncated.append(f"{name}: {MAX_SECTION_CHARS:,}자에서 잘림")
            ft.sections[name] = best[:MAX_SECTION_CHARS]
    return ft
