"""전체 파이프라인: 기업명 → 수집 → 분석 → 가설 → 추정·밸류에이션 → Excel(검증) → 보고서."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, is_dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from .ai import narrative
from .ai.client import ClaudeClient, ai_available
from .ai.hypotheses import ai_hypotheses, rule_based_hypotheses
from .analysis import ratios as R
from .analysis.standardize import fill_derived, run_checks
from .config import ROOT, load_config
from .data import prices, resolver
from .excel.builder import ModelBuilder
from .excel.verify import verify
from .models import MarketData
from .report.facts import build_facts
from .report.render import build_markdown, to_pdf
from .valuation.assumptions import apply_guardrails
from .valuation.dcf import compute_wacc, implied_metrics, run_dcf, sensitivity
from .valuation.relative import metrics_from_financials, run_relative


def log(msg: str):
    print(msg, flush=True)


def market_data(company, cfg) -> MarketData:
    mcfg = cfg["markets"][company.market]
    price, pdate, cur = prices.current_price(company.ticker)
    b = cfg["valuation"]["beta"]
    beta, raw, note = prices.beta(company.ticker, mcfg["market_index"], b["lookback_years"], b["blume_adjust"],
                                  b["floor"], b["cap"])
    if mcfg["risk_free_rate"] == "auto":
        rf, rf_note = prices.us_risk_free(mcfg["risk_free_fallback"])
    else:
        rf, rf_note = float(mcfg["risk_free_rate"]), "config/assumptions.yaml 설정값"
    return MarketData(price=price, price_date=pdate, currency=cur, beta=beta, beta_raw=raw, beta_note=note,
                      risk_free_rate=rf, rf_note=rf_note)


def fetch_peers(suggestions: list[tuple[str, str, str]], target, notes: list[str], limit: int = 6):
    out = []
    for ident, market, reason in suggestions:
        if len(out) >= limit:
            break
        try:
            c = resolver.resolve(ident, market)
            if c.ticker == target.ticker or (c.cik and c.cik == target.cik) or (c.corp_code and c.corp_code == target.corp_code):
                continue
            if c.currency != target.currency:
                notes.append(f"피어 {ident}: 통화 불일치로 제외")
                continue
            f = resolver.fetch_financials(c, years=2)
            fill_derived(f)
            if not f.years or not f.shares_current:
                notes.append(f"피어 {ident}: 재무 데이터 부족으로 제외")
                continue
            price, _, _ = prices.current_price(c.ticker)
            out.append(metrics_from_financials(f, price, reason))
            log(f"   · 피어 {c.name} ({c.ticker}) 수집")
        except Exception as e:  # 개별 피어 실패는 전체를 멈추지 않음
            notes.append(f"피어 {ident} 수집 실패: {e}")
    return out


def _jsonable(o):
    if is_dataclass(o):
        return asdict(o)
    if hasattr(o, "model_dump"):
        return o.model_dump()
    return str(o)


def run(query: str, market: Optional[str] = None, use_ai: Optional[bool] = None, peers: Optional[list[str]] = None,
        out_root: Optional[Path] = None) -> dict:
    cfg = load_config()
    notes: list[str] = []
    use_ai = ai_available() if use_ai is None else use_ai

    log(f"[1/6] 기업 확인 및 공시 수집: {query}")
    company = resolver.resolve(query, market)
    log(f"   → {company.name} ({company.ticker}, {company.market})")
    out_dir = (out_root or ROOT / "outputs") / re.sub(r"[^\w가-힣.-]", "_", f"{company.ticker}_{date.today():%Y%m%d}")
    out_dir.mkdir(parents=True, exist_ok=True)
    fin = resolver.fetch_financials(company)
    if not fin.years:
        raise RuntimeError("연간 재무제표를 찾지 못했습니다.")
    fill_derived(fin)
    checks = run_checks(fin)
    try:
        filing = resolver.fetch_filing_text(company)
    except Exception as e:
        filing, _ = None, notes.append(f"공시 원문 수집 실패: {e}")
    if filing and filing.truncated:
        notes += [f"공시 원문 {t}" for t in filing.truncated]
    mcfg = cfg["markets"][company.market]
    mkt = market_data(company, cfg)
    log(f"   → 재무 {fin.years[0]}~{fin.years[-1]}, 주가 {mkt.price:,.2f} {mkt.currency}, 베타 {mkt.beta}")

    ind = (company.industry or "").lower()
    if any(k in ind for k in ("bank", "insurance", "savings", "finance", "금융", "보험", "은행")) \
            or company.industry and company.industry.startswith("KSIC 64") or company.industry and company.industry.startswith("KSIC 65"):
        notes.append("금융업(은행·보험 등)은 영업이익·운전자본 개념이 달라 FCFF DCF의 신뢰도가 낮습니다. "
                     "PBR·PER 중심의 상대가치 결과를 우선 참고하세요.")
    log("[2/6] 재무제표 분석 (비율·검증)")
    ratios = R.compute(fin)
    fails = [c for c in checks if c["status"] == "FAIL"]
    if any(c["check"] in ("유통주식수", "분석 연도 수") for c in fails):
        raise RuntimeError(f"밸류에이션에 필요한 데이터 부족: {fails}")

    n = cfg["valuation"]["forecast_years"]
    tg_cap = mcfg["terminal_growth_cap"]
    client = None
    if use_ai:
        log(f"[3/6] AI 가설 수립 ({cfg['ai']['model']}) — 공시 해석·추정 가정·피어 제안")
        client = ClaudeClient(cfg["ai"]["model"], cfg["ai"]["effort"], out_dir / "ai_log")
        hyp, ctx_notes = ai_hypotheses(client, fin, ratios, mkt, filing, cfg["ai"]["max_context_chars"])
        notes += ctx_notes
    else:
        log("[3/6] 규칙 기반 가정 (AI 미사용: ANTHROPIC_API_KEY 없음 또는 --no-ai)")
        hyp = rule_based_hypotheses(fin, ratios, n, min(0.025, tg_cap), mcfg["marginal_tax_rate"])
    a = hyp.assumptions
    guard_log = apply_guardrails(a, cfg["guardrails"], n, tg_cap)

    log("[4/6] 실적 추정 · DCF · 상대가치평가 (코드 계산)")
    wacc = compute_wacc(fin, mkt, mcfg["equity_risk_premium"], mcfg["marginal_tax_rate"],
                        cfg["valuation"]["cost_of_debt_spread"])
    if wacc.wacc - a.terminal_growth < 0.01:
        a.terminal_growth = max(wacc.wacc - 0.01, 0.0)
        guard_log.append(f"영구성장률을 WACC−1%p({a.terminal_growth:.2%})로 제한")
    mid = cfg["valuation"]["mid_year_convention"]
    dcf = {"base": run_dcf(fin, a, wacc.wacc, "base", mid_year=mid),
           "bull": run_dcf(fin, a, wacc.wacc, "bull", a.bull.growth_delta, a.bull.margin_delta, mid),
           "bear": run_dcf(fin, a, wacc.wacc, "bear", a.bear.growth_delta, a.bear.margin_delta, mid)}
    sens = sensitivity(fin, a, wacc.wacc, mid)
    suggestions = [(p.identifier, p.market, p.reason) for p in hyp.peers]
    suggestions = [(p, company.market, "사용자 지정") for p in (peers or [])] + suggestions
    peer_metrics = fetch_peers(suggestions, company, notes)
    target_metrics = metrics_from_financials(fin, mkt.price)
    rel = run_relative(target_metrics, peer_metrics) if peer_metrics else None

    w = cfg["valuation"]["blend_weights"]
    dcf_v = dcf["base"].value_per_share
    rel_v = rel.value_per_share if rel else None
    target = w["dcf"] * dcf_v + w["relative"] * rel_v if rel_v is not None else dcf_v
    upside = target / mkt.price - 1
    th = cfg["valuation"]["rating_thresholds"]
    rating = "BUY" if upside >= th["buy"] else ("SELL" if upside <= th["sell"] else "HOLD")
    summary = {"target_price": target, "upside": upside, "rating": rating, "dcf_value": dcf_v, "relative_value": rel_v}
    log(f"   → WACC {wacc.wacc:.2%}, DCF {dcf_v:,.2f}, 상대가치 {rel_v if rel_v is None else round(rel_v, 2)}, "
        f"목표주가 {target:,.2f} ({upside:+.1%}, {rating})")

    ctx = dict(fin=fin, mkt=mkt, ratios=ratios, checks=checks, assumptions=a, guardrail_log=guard_log, wacc=wacc,
               dcf=dcf, sensitivity=sens, relative=rel, target_metrics=target_metrics, summary=summary, cfg=cfg,
               erp=mcfg["equity_risk_premium"], marginal_tax=mcfg["marginal_tax_rate"], mid_year=mid,
               ai_used=use_ai, ai_model=cfg["ai"]["model"], as_of=date.today().isoformat(), filing=filing,
               notes=notes, implied=implied_metrics(dcf["base"], fin))

    log("[5/6] Excel 모델 생성 및 교차검증 (LibreOffice 재계산)")
    xlsx = out_dir / f"{company.ticker}_valuation_model.xlsx"
    audit_cells = ModelBuilder(ctx).build(xlsx)
    vr = verify(xlsx, audit_cells)
    ctx["verification"] = vr
    log(f"   → 검증 {vr['status']}" + (f": {sum(r['status'] == 'OK' for r in vr['rows'])}/{len(vr['rows'])} 일치" if vr["rows"] else ""))

    log("[6/6] 보고서 작성" + (" (AI 서술 + 코드 수치)" if use_ai else " (템플릿)"))
    facts = build_facts(ctx)
    if use_ai:
        context = {"business_summary": hyp.business_summary, "rationales": [r.model_dump() for r in a.rationales],
                   "key_drivers": a.key_drivers, "risks": a.risks, "thesis": a.thesis,
                   "scenarios": {"bull": a.bull.narrative, "bear": a.bear.narrative},
                   "guardrail_adjustments": guard_log, "data_warnings": fin.warnings}
        draft = narrative.ai_report(client, facts, context)
    else:
        draft = narrative.rule_based_report(facts, hyp)
    ctx["ai_usage"] = client.usage if client else []
    md, report_audit = build_markdown(ctx, facts, draft, out_dir)
    md_path = out_dir / f"{company.ticker}_report.md"
    md_path.write_text(md, encoding="utf-8")
    pdf, how = to_pdf(md_path)
    log(f"   → {md_path.name}" + (f", {pdf.name} ({how})" if pdf else f" (PDF 생략: {how})"))

    dump = {"company": company, "years": fin.years, "financials": {y: {k: asdict(v) for k, v in d.items()} for y, d in fin.data.items()},
            "shares_current": fin.shares_current, "market": mkt, "ratios": ratios, "checks": checks,
            "hypotheses": hyp, "guardrail_log": guard_log, "wacc": wacc, "dcf": dcf, "sensitivity": sens,
            "relative": rel, "summary": summary, "verification": vr, "report_audit": report_audit,
            "facts": facts, "notes": notes, "ai_usage": ctx["ai_usage"]}
    (out_dir / "analysis.json").write_text(json.dumps(dump, default=_jsonable, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"\n완료: {out_dir}")
    return {"out_dir": out_dir, "summary": summary, "verification": vr, "report_audit": report_audit,
            "xlsx": xlsx, "md": md_path, "pdf": pdf}
