"""팩트시트: 보고서에 들어갈 모든 수치를 코드가 계산·포맷해 {{키}}로 제공."""
from __future__ import annotations

from typing import Optional

from ..analysis import ratios as R


def fmt_price(v: Optional[float], cur: str) -> str:
    if v is None:
        return "산출 불가"
    return f"${v:,.2f}" if cur == "USD" else f"{v:,.0f}원"


def fmt_amount(v: Optional[float], cur: str) -> str:
    """원 단위 금액 → 사람이 읽는 단위."""
    if v is None:
        return "산출 불가"
    if cur == "KRW":
        eok = v / 1e8
        return f"{eok / 1e4:,.1f}조원" if abs(eok) >= 1e4 else f"{eok:,.0f}억원"
    b = v / 1e9
    return f"${b:,.1f}B" if abs(b) >= 1 else f"${v / 1e6:,.0f}M"


def pct(v: Optional[float], signed: bool = False) -> str:
    if v is None:
        return "산출 불가"
    return f"{v:+.1%}" if signed else f"{v:.1%}"


def mult(v: Optional[float]) -> str:
    return "산출 불가" if v is None else f"{v:.1f}배"


RATING_KO = {"BUY": "매수(BUY)", "HOLD": "중립(HOLD)", "SELL": "매도(SELL)"}


def build_facts(ctx: dict) -> dict[str, dict]:
    fin, cur = ctx["fin"], ctx["fin"].company.currency
    y = fin.last_year
    rt, s, w, a = ctx["ratios"], ctx["summary"], ctx["wacc"], ctx["assumptions"]
    d, rel = ctx["dcf"], ctx["relative"]
    facts: dict[str, dict] = {}

    def add(k, text, desc):
        facts[k] = {"text": text, "desc": desc}

    first = fin.years[0]
    rev0, rev1 = fin.get(first, "revenue"), fin.get(y, "revenue")
    n = y - first
    cagr = (rev1 / rev0) ** (1 / n) - 1 if rev0 and rev1 and n > 0 and rev0 > 0 else None
    fr = d["base"].revenue
    f_cagr = (fr[-1] / rev1) ** (1 / len(fr)) - 1 if rev1 else None

    add("company_name", fin.company.name, "회사명")
    add("ticker", fin.company.ticker, "티커")
    add("last_fy", f"FY{y}", "최근 회계연도")
    add("price", fmt_price(ctx["mkt"].price, cur), f"현재 주가 ({ctx['mkt'].price_date})")
    add("target_price", fmt_price(s["target_price"], cur), "목표주가 (DCF·상대가치 가중평균)")
    add("upside", pct(s["upside"], True), "상승여력")
    add("rating", RATING_KO[s["rating"]], "투자의견 (상승여력 기준, 코드 결정)")
    add("dcf_value", fmt_price(d["base"].value_per_share, cur), "DCF 주당가치 (Base)")
    add("dcf_bull", fmt_price(d["bull"].value_per_share, cur), "DCF 주당가치 (Bull)")
    add("dcf_bear", fmt_price(d["bear"].value_per_share, cur), "DCF 주당가치 (Bear)")
    add("rel_value", fmt_price(rel.value_per_share if rel else None, cur), "상대가치 주당가치")
    add("wacc", pct(w.wacc), "WACC")
    add("ke", pct(w.cost_of_equity), "자기자본비용")
    add("beta", f"{w.beta:.2f}", "베타")
    add("rf", pct(w.rf), "무위험수익률")
    add("erp", pct(w.erp), "주식위험프리미엄")
    add("terminal_growth", pct(a.terminal_growth), "영구성장률")
    add("tv_share", pct(d["base"].tv_share_of_ev), "EV 중 잔존가치 비중")
    add("ev", fmt_amount(d["base"].enterprise_value, cur), "DCF 기업가치")
    add("equity_value", fmt_amount(d["base"].equity_value, cur), "DCF 주주가치")
    add("net_debt", fmt_amount(d["base"].net_debt, cur), "순차입금 (음수 = 순현금)")
    add("market_cap", fmt_amount(w.equity_value_mkt * 1.0, cur), "현재 시가총액")
    add("revenue_last", fmt_amount(rev1, cur), f"FY{y} 매출액")
    add("op_income_last", fmt_amount(fin.get(y, "operating_income"), cur), f"FY{y} 영업이익")
    add("net_income_last", fmt_amount(fin.get(y, "net_income_parent"), cur), f"FY{y} 지배주주순이익")
    add("revenue_growth_last", pct(rt[y]["revenue_growth"], True), f"FY{y} 매출 성장률")
    add("rev_cagr_hist", pct(cagr), f"FY{first}~FY{y} 매출 연평균성장률")
    add("op_margin_last", pct(rt[y]["operating_margin"]), f"FY{y} 영업이익률")
    add("op_margin_3y", pct(R.summary(rt, "operating_margin")), "최근 3개년 평균 영업이익률")
    add("net_margin_last", pct(rt[y]["net_margin"]), f"FY{y} 순이익률")
    add("roe_last", pct(rt[y]["roe"]), f"FY{y} ROE")
    add("roic_last", pct(rt[y]["roic"]), f"FY{y} ROIC")
    add("debt_to_equity_last", pct(rt[y]["debt_to_equity"]), f"FY{y} 부채비율")
    add("fcf_margin_last", pct(rt[y]["fcf_margin"]), f"FY{y} FCF 마진")
    add("forecast_rev_cagr", pct(f_cagr), "추정기간 매출 연평균성장률 (Base)")
    add("forecast_margin_avg", pct(sum(a.ebit_margin) / len(a.ebit_margin)), "추정기간 평균 영업이익률")
    add("forecast_rev_final", fmt_amount(fr[-1], cur), f"FY{y + len(fr)}E 매출액")
    for i in range(len(a.revenue_growth)):
        add(f"growth_y{i + 1}", pct(a.revenue_growth[i]), f"FY{y + i + 1}E 매출 성장률 가정")
        add(f"margin_y{i + 1}", pct(a.ebit_margin[i]), f"FY{y + i + 1}E 영업이익률 가정")
    add("capex_pct", pct(a.capex_pct_revenue), "CAPEX/매출 가정")
    add("da_pct", pct(a.da_pct_revenue), "D&A/매출 가정")
    add("nwc_pct", pct(a.nwc_pct_revenue), "순운전자본/매출 가정")
    add("tax_rate", pct(a.tax_rate), "세율 가정")
    add("median_pe", mult(rel.median_pe if rel else None), "피어 중앙값 PER")
    add("median_ev_ebitda", mult(rel.median_ev_ebitda if rel else None), "피어 중앙값 EV/EBITDA")
    add("median_pb", mult(rel.median_pb if rel else None), "피어 중앙값 PBR")
    tm = ctx["target_metrics"]
    add("current_pe", mult(tm.pe()), "현재 PER (최근 연도 순이익 기준)")
    add("current_ev_ebitda", mult(tm.ev_ebitda()), "현재 EV/EBITDA")
    add("current_pb", mult(tm.pb()), "현재 PBR")
    im = ctx["implied"]
    add("dcf_implied_pe", mult(im["implied_pe"]), "DCF 가치가 내포하는 PER")
    add("dcf_implied_ev_ebitda", mult(im["implied_ev_ebitda"]), "DCF 가치가 내포하는 EV/EBITDA")
    add("peer_count", f"{len(rel.peers) if rel else 0}개", "비교기업 수")
    return facts
