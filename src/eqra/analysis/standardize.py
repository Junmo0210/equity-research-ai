"""누락 계정 파생 + 데이터 검증 체크."""
from __future__ import annotations

from ..models import Financials, LineItem


def fill_derived(fin: Financials) -> None:
    for y in fin.years:
        d = fin.data[y]
        g = lambda k: d[k].value if k in d else None

        def put(k, v, how):
            if k not in d and v is not None:
                d[k] = LineItem(v, f"파생: {how}")

        if g("revenue") is not None and g("cogs") is not None:
            put("gross_profit", g("revenue") - g("cogs"), "매출액 − 매출원가")
        if g("revenue") is not None and g("gross_profit") is not None:
            put("cogs", g("revenue") - g("gross_profit"), "매출액 − 매출총이익")
        put("net_income_parent", g("net_income"), "당기순이익(비지배 구분 없음)")
        put("net_income", g("net_income_parent"), "지배주주순이익")
        if g("total_equity") is not None and g("equity_parent") is not None:
            put("minority_interest", g("total_equity") - g("equity_parent"), "자본총계 − 지배주주지분")
        if g("total_equity") is not None:
            put("equity_parent", g("total_equity") - (g("minority_interest") or 0), "자본총계 − 비지배지분")
        if g("total_assets") is not None and g("total_equity") is not None:
            put("total_liabilities", g("total_assets") - g("total_equity"), "자산총계 − 자본총계")
        for k, label in [("short_term_debt", "단기차입금"), ("long_term_debt", "장기차입금"),
                         ("short_term_investments", "단기금융상품"), ("minority_interest", "비지배지분")]:
            if k not in d:
                d[k] = LineItem(0.0, f"미보고 → 0 처리 ({label})")

    if fin.shares_current is None and fin.years:
        s = fin.get(fin.last_year, "shares_outstanding")
        if s:
            fin.shares_current = LineItem(s, f"최근 연도 희석가중평균주식수로 대체 ({fin.last_year})")
            fin.warnings.append("최신 유통주식수 태그가 없어 희석가중평균주식수를 사용했습니다.")
    missing_da = [y for y in fin.years if "d_and_a" not in fin.data[y]]
    if missing_da:
        fin.warnings.append(f"감가상각비(D&A) 미확인 연도: {missing_da} — EBITDA 관련 지표 일부 산출 불가")


def run_checks(fin: Financials) -> list[dict]:
    """데이터 품질 검증. status: PASS / WARN / FAIL."""
    out = []

    def add(name, year, status, detail):
        out.append({"check": name, "year": year, "status": status, "detail": detail})

    add("분석 연도 수", None, "PASS" if len(fin.years) >= 3 else "FAIL", f"{len(fin.years)}개년: {fin.years}")
    for y in fin.years:
        d = fin.data[y]
        g = lambda k: d[k].value if k in d else None
        ta, tl, te = g("total_assets"), g("total_liabilities"), g("total_equity")
        if None not in (ta, tl, te) and not d["total_liabilities"].source.startswith("파생"):
            diff = abs(ta - (tl + te)) / ta
            add("자산 = 부채 + 자본", y, "PASS" if diff < 0.005 else "WARN", f"차이 {diff:.3%}")
        rev, gp, cogs = g("revenue"), g("gross_profit"), g("cogs")
        if None not in (rev, gp, cogs):
            diff = abs(rev - cogs - gp) / abs(rev)
            add("매출총이익 = 매출 − 원가", y, "PASS" if diff < 0.005 else "WARN", f"차이 {diff:.3%}")
        oi = g("operating_income")
        if rev and oi is not None:
            add("영업이익 ≤ 매출", y, "PASS" if oi <= rev else "FAIL", f"영업이익률 {oi / rev:.1%}")
        for k in ("revenue", "operating_income", "net_income", "total_equity", "cash"):
            if k not in d:
                add("필수 계정 존재", y, "FAIL", f"{k} 누락")
    add("유통주식수", None, "PASS" if fin.shares_current else "FAIL",
        fin.shares_current.source if fin.shares_current else "주식수 없음 — 주당가치 산출 불가")
    return out
