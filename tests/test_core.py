"""네트워크 없이 실행되는 핵심 테스트 (합성 데이터)."""
from __future__ import annotations

import math

import pytest

from eqra.ai import narrative
from eqra.ai.hypotheses import rule_based_hypotheses
from eqra.analysis import ratios as R
from eqra.analysis.standardize import fill_derived, run_checks
from eqra.config import load_config
from eqra.data import dart
from eqra.excel.builder import ModelBuilder
from eqra.excel.verify import find_soffice, verify
from eqra.models import Company, Financials, LineItem, MarketData
from eqra.valuation.assumptions import apply_guardrails
from eqra.valuation.dcf import compute_wacc, implied_metrics, run_dcf, sensitivity
from eqra.valuation.relative import PeerMetrics, metrics_from_financials, run_relative


# ── 합성 기업 ──────────────────────────────────────────────────────────
def make_fin(currency="USD") -> Financials:
    c = Company(query="TEST", name="Test Corp", market="US", ticker="TST", currency=currency, cik="0000000001")
    base = {"revenue": 1000, "cogs": 600, "operating_income": 150, "interest_expense": 10, "pretax_income": 140,
            "income_tax": 30, "net_income": 110, "net_income_parent": 105, "cash": 120, "accounts_receivable": 100,
            "inventory": 80, "accounts_payable": 70, "total_current_assets": 400, "total_current_liabilities": 250,
            "total_assets": 1500, "total_liabilities": 800, "total_equity": 700, "equity_parent": 680,
            "short_term_debt": 50, "long_term_debt": 250, "cfo": 180, "capex": 60, "d_and_a": 40}
    fin = Financials(company=c, source_note="synthetic")
    for i, y in enumerate([2021, 2022, 2023, 2024]):
        g = 1.08 ** i
        fin.data[y] = {k: LineItem(v * g * 1e6, "test") for k, v in base.items()}
    fin.years = [2021, 2022, 2023, 2024]
    fin.shares_current = LineItem(100e6, "test")
    fill_derived(fin)
    return fin


MKT = MarketData(price=20.0, price_date="2026-01-01", currency="USD", beta=1.1, beta_note="test",
                 risk_free_rate=0.04, rf_note="test")


# ── DART 파싱 ─────────────────────────────────────────────────────────
def test_parse_amount():
    assert dart.parse_amount("1,234,567") == 1234567
    assert dart.parse_amount("-1,000") == -1000
    assert dart.parse_amount("(500)") == -500
    assert dart.parse_amount("") is None and dart.parse_amount("-") is None and dart.parse_amount(None) is None


def test_dart_standardize_rows():
    # OpenDART fnlttSinglAcntAll 응답 형식 (필드명은 공식 문서 기준)
    rows = [
        {"sj_div": "IS", "account_id": "ifrs-full_Revenue", "account_nm": "매출액", "thstrm_amount": "300,000,000"},
        {"sj_div": "IS", "account_id": "dart_OperatingIncomeLoss", "account_nm": "영업이익", "thstrm_amount": "30,000,000"},
        {"sj_div": "IS", "account_id": "-표준계정코드 미사용-", "account_nm": "이자비용", "thstrm_amount": "1,000,000"},
        {"sj_div": "BS", "account_id": "ifrs-full_ShortTermBorrowings", "account_nm": "단기차입금", "thstrm_amount": "5,000,000"},
        {"sj_div": "BS", "account_id": "-표준계정코드 미사용-", "account_nm": "유동성 장기부채", "thstrm_amount": "2,000,000"},
        {"sj_div": "BS", "account_id": "dart_BondsIssued", "account_nm": "사채", "thstrm_amount": "7,000,000"},
        {"sj_div": "BS", "account_id": "-표준계정코드 미사용-", "account_nm": "유동성사채", "thstrm_amount": "1,000,000"},
        {"sj_div": "CF", "account_id": "ifrs-full_PurchaseOfPropertyPlantAndEquipment", "account_nm": "유형자산의 취득",
         "thstrm_amount": "-9,000,000"},
        {"sj_div": "CF", "account_id": "ifrs-full_PurchaseOfIntangibleAssets", "account_nm": "무형자산의 취득",
         "thstrm_amount": "-1,000,000"},
    ]
    std = dart.standardize_rows(rows)
    assert std["revenue"].value == 300_000_000
    assert std["operating_income"].value == 30_000_000
    assert std["interest_expense"].value == 1_000_000
    assert std["short_term_debt"].value == 8_000_000        # 단기차입금 + 유동성장기부채 + 유동성사채
    assert std["long_term_debt"].value == 7_000_000         # '사채'만 (유동성사채와 혼동 없음)
    assert std["capex"].value == 10_000_000                 # 유형 + 무형, 절대값


# ── 분석 ──────────────────────────────────────────────────────────────
def test_fill_derived_and_checks():
    fin = make_fin()
    assert fin.get(2024, "gross_profit") == pytest.approx(fin.get(2024, "revenue") - fin.get(2024, "cogs"))
    assert fin.get(2024, "minority_interest") == pytest.approx(20e6 * 1.08 ** 3)
    statuses = {c["check"]: c["status"] for c in run_checks(fin)}
    assert statuses["자산 = 부채 + 자본"] == "PASS"
    assert statuses["유통주식수"] == "PASS"


def test_ratios():
    fin = make_fin()
    r = R.compute(fin)[2024]
    assert r["revenue_growth"] == pytest.approx(0.08)
    assert r["operating_margin"] == pytest.approx(0.15)
    assert r["ebitda_margin"] == pytest.approx(0.19)
    assert r["nwc_pct_revenue"] == pytest.approx(0.11)
    assert r["fcf_margin"] == pytest.approx(0.12)


# ── 밸류에이션 수학 (손계산 대조) ─────────────────────────────────────
def test_dcf_matches_hand_calculation():
    fin = make_fin()
    hyp = rule_based_hypotheses(fin, R.compute(fin), 5, 0.025, 0.21)
    a = hyp.assumptions
    a.revenue_growth, a.ebit_margin = [0.05] * 5, [0.15] * 5
    a.tax_rate, a.da_pct_revenue, a.capex_pct_revenue, a.nwc_pct_revenue = 0.2, 0.04, 0.06, 0.10
    w = 0.09
    res = run_dcf(fin, a, w)
    rev, nwc_prev, pv = fin.get(2024, "revenue"), fin.get(2024, "revenue") * 0.10, 0.0
    for t in range(1, 6):
        rev *= 1.05
        nwc = rev * 0.10
        fcff = rev * 0.15 * 0.8 + rev * 0.04 - rev * 0.06 - (nwc - nwc_prev)
        pv += fcff / (1 + w) ** t
        nwc_prev = nwc
    tv = fcff * 1.025 / (w - 0.025)
    ev = pv + tv / (1 + w) ** 5
    assert res.enterprise_value == pytest.approx(ev, rel=1e-12)
    nd = (50 + 250 - 120) * 1.08 ** 3 * 1e6
    assert res.net_debt == pytest.approx(nd)
    assert res.value_per_share == pytest.approx((ev - nd - 20e6 * 1.08 ** 3) / 100e6)


def test_wacc():
    fin = make_fin()
    wr = compute_wacc(fin, MKT, erp=0.05, marginal_tax=0.21, spread=0.015)
    assert wr.cost_of_equity == pytest.approx(0.04 + 1.1 * 0.05)
    debt = 300 * 1.08 ** 3
    kd = (10 * 1.08 ** 3) / ((debt + 300 * 1.08 ** 2) / 2)
    assert wr.kd_pretax == pytest.approx(max(kd, 0.04))
    e = 20 * 100
    assert wr.weight_equity == pytest.approx(e / (e + debt))


def test_relative_median_and_outliers():
    def peer(ni, eb, eq):
        return PeerMetrics("P", "P", 10, 100, ni, eb, eq, 1000, 0, 0, 2024)
    peers = [peer(50, 100, 500), peer(100, 200, 400), peer(5, -1, -10), peer(25, 80, 300)]   # 세번째 PER=200 → 제외
    tgt = PeerMetrics("T", "T", 10, 100, 80, 160, 400, 1000, 100, 0, 2024)
    rr = run_relative(tgt, peers)
    assert rr.median_pe == pytest.approx(20.0)          # [20, 10, 40] → 20
    assert rr.median_ev_ebitda == pytest.approx(10.0)   # [10, 5, 12.5] → 10
    assert rr.implied["PER"] == pytest.approx(20 * 80 / 100)
    assert rr.implied["EV/EBITDA"] == pytest.approx((10 * 160 - 100) / 100)


def test_guardrails_clip_and_log():
    fin = make_fin()
    a = rule_based_hypotheses(fin, R.compute(fin), 5, 0.025, 0.21).assumptions
    a.revenue_growth = [0.9, 0.1, 0.1]
    a.terminal_growth = 0.06
    cfg = load_config()
    log = apply_guardrails(a, cfg["guardrails"], 5, 0.03)
    assert len(a.revenue_growth) == 5 and a.revenue_growth[0] == 0.60
    assert a.terminal_growth == 0.03
    assert any("영구성장률" in l for l in log)


# ── 보고서 수치 감사 ──────────────────────────────────────────────────
def test_placeholder_render_and_audit():
    facts = {"target_price": {"text": "$25.00", "desc": ""}, "upside": {"text": "+25.0%", "desc": ""}}
    txt, unknown = narrative.render("목표주가 {{target_price}}, 상승여력 {{upside}}, {{missing}}", facts)
    assert txt.startswith("목표주가 $25.00, 상승여력 +25.0%") and unknown == ["missing"]
    assert narrative.audit_numbers("2025년 매출은 {{revenue_last}}이며 향후 5개년 성장") == []
    assert narrative.audit_numbers("영업이익률은 32.5%로 개선") != []


# ── Excel 모델 ↔ Python 교차검증 (LibreOffice 필요) ────────────────────
@pytest.mark.skipif(find_soffice() is None, reason="LibreOffice 미설치")
def test_excel_model_matches_python(tmp_path):
    fin = make_fin()
    cfg = load_config()
    ratios = R.compute(fin)
    hyp = rule_based_hypotheses(fin, ratios, 5, 0.025, 0.21)
    a = hyp.assumptions
    wr = compute_wacc(fin, MKT, 0.05, 0.21, 0.015)
    dcf = {"base": run_dcf(fin, a, wr.wacc), "bull": run_dcf(fin, a, wr.wacc, "bull", 0.02, 0.02),
           "bear": run_dcf(fin, a, wr.wacc, "bear", -0.02, -0.02)}
    peers = [PeerMetrics("A", "A", 30, 50e6, 100e6, 200e6, 600e6, 900e6, 50e6, 0, 2024),
             PeerMetrics("B", "B", 15, 80e6, 90e6, None, 500e6, 800e6, -20e6, 5e6, 2024)]
    tm = metrics_from_financials(fin, MKT.price)
    rel = run_relative(tm, peers)
    target = 0.5 * dcf["base"].value_per_share + 0.5 * rel.value_per_share
    ctx = dict(fin=fin, mkt=MKT, ratios=ratios, checks=run_checks(fin), assumptions=a, guardrail_log=[], wacc=wr,
               dcf=dcf, sensitivity=sensitivity(fin, a, wr.wacc), relative=rel, target_metrics=tm,
               summary={"target_price": target, "upside": target / MKT.price - 1, "rating": "HOLD"}, cfg=cfg,
               erp=0.05, marginal_tax=0.21, mid_year=False, ai_used=False, ai_model="", as_of="2026-01-01",
               filing=None, notes=[], implied=implied_metrics(dcf["base"], fin))
    path = tmp_path / "model.xlsx"
    cells = ModelBuilder(ctx).build(path)
    vr = verify(path, cells)
    bad = [r for r in vr["rows"] if r["status"] != "OK"]
    assert vr["status"] == "PASS", (bad, vr.get("excel_errors"))
    assert len(vr["rows"]) > 35
    assert not math.isnan(target)
