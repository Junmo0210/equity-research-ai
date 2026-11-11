"""보고서 조립: 코드 생성 표·차트 + AI 서술(자리표시자 치환) → Markdown → PDF."""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

from ..ai.narrative import SECTION_KEYS, SECTION_TITLES, audit_numbers, render  # noqa: E402
from ..analysis.ratios import RATIO_DEFS  # noqa: E402
from ..models import LABELS_KO  # noqa: E402
from .facts import fmt_price  # noqa: E402

KOREAN_FONTS = ["Malgun Gothic", "AppleGothic", "Apple SD Gothic Neo", "NanumGothic", "Noto Sans CJK KR",
                "Noto Sans KR", "WenQuanYi Zen Hei", "Unifont"]


def _setup_font():
    names = {f.name for f in font_manager.fontManager.ttflist}
    for f in KOREAN_FONTS:
        if f in names:
            plt.rcParams["font.family"] = f
            break
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["text.parse_math"] = False      # "$" 금액 표기를 수식으로 해석하지 않도록
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)


C_ACT, C_EST, C_LINE, C_ACCENT = "#1F3864", "#8EA9DB", "#C55A11", "#2E75B6"


def chart_revenue(ctx, path: Path):
    fin, d = ctx["fin"], ctx["dcf"]["base"]
    yrs = [f"FY{y}" for y in fin.years] + [f"FY{y}E" for y in d.years]
    rev = [fin.get(y, "revenue") / 1e9 for y in fin.years] + [v / 1e9 for v in d.revenue]
    mg = [ctx["ratios"][y]["operating_margin"] or 0 for y in fin.years] + d.margin
    fig, ax = plt.subplots(figsize=(8, 3.6), dpi=150)
    colors = [C_ACT] * len(fin.years) + [C_EST] * len(d.years)
    ax.bar(yrs, rev, color=colors)
    unit = "십억원" if fin.company.currency == "KRW" else "USD bn"
    ax.set_ylabel(f"매출액 ({unit})")
    ax2 = ax.twinx()
    ax2.plot(yrs, [m * 100 for m in mg], color=C_LINE, marker="o")
    ax2.set_ylabel("영업이익률 (%)")
    ax.set_title("매출액(막대: 진한색=실적, 연한색=추정) · 영업이익률(선)")
    ax.spines[["top"]].set_visible(False); ax2.spines[["top"]].set_visible(False)
    plt.setp(ax.get_xticklabels(), rotation=30, fontsize=8)
    fig.tight_layout(); fig.savefig(path); plt.close(fig)


def chart_football(ctx, path: Path):
    cur = ctx["fin"].company.currency
    d, rel, s = ctx["dcf"], ctx["relative"], ctx["summary"]
    sens = [v for row in ctx["sensitivity"]["values"] for v in row if v is not None]
    bars = [("DCF 시나리오 (Bear~Bull)", d["bear"].value_per_share, d["bull"].value_per_share),
            ("DCF 민감도 (WACC±1%p, g±0.5%p)", min(sens), max(sens))]
    if rel and rel.implied:
        vals = [v for v in rel.implied.values() if v is not None]
        if vals:
            bars.append(("상대가치 (PER·EV/EBITDA·PBR)", min(vals), max(vals)))
    fig, ax = plt.subplots(figsize=(8, 2.8), dpi=150)
    for i, (label, lo, hi) in enumerate(bars):
        ax.barh(i, hi - lo, left=lo, color=C_ACCENT, height=0.5)
        ax.text(hi, i, f"  {fmt_price(hi, cur)}", va="center", fontsize=8)
        ax.text(lo, i, f"{fmt_price(lo, cur)}  ", va="center", ha="right", fontsize=8)
    ax.set_yticks(range(len(bars))); ax.set_yticklabels([b[0] for b in bars], fontsize=9)
    ax.axvline(ctx["mkt"].price, color="black", ls="--", lw=1)
    ax.axvline(s["target_price"], color=C_LINE, lw=2)
    ax.set_title(f"밸류에이션 범위 — 점선: 현재가 {fmt_price(ctx['mkt'].price, cur)} / 주황: 목표주가 {fmt_price(s['target_price'], cur)}",
                 fontsize=9)
    ax.spines[["top", "right"]].set_visible(False)
    lo_all = min(min(b[1] for b in bars), ctx["mkt"].price)
    hi_all = max(max(b[2] for b in bars), ctx["mkt"].price)
    pad = (hi_all - lo_all) * 0.25
    ax.set_xlim(lo_all - pad, hi_all + pad)
    fig.tight_layout(); fig.savefig(path); plt.close(fig)


def _m(v, cur):
    if v is None:
        return "-"
    return f"{v / 1e9:,.1f}" if cur == "USD" else f"{v / 1e8:,.0f}"


def build_markdown(ctx: dict, facts: dict, draft, out_dir: Path) -> tuple[str, dict]:
    _setup_font()
    fin, cur = ctx["fin"], ctx["fin"].company.currency
    unit = "USD bn" if cur == "USD" else "억원"
    charts = out_dir / "charts"; charts.mkdir(exist_ok=True)
    chart_revenue(ctx, charts / "revenue.png"); chart_football(ctx, charts / "football.png")

    sections, unknown, violations = {}, [], {}
    for k in ["headline"] + SECTION_KEYS:
        raw = getattr(draft, k)
        txt, unk = render(raw, facts)
        sections[k] = txt
        unknown += unk
        v = audit_numbers(raw)
        if v:
            violations[k] = v

    s, d, w, a, rel = ctx["summary"], ctx["dcf"], ctx["wacc"], ctx["assumptions"], ctx["relative"]
    L = []
    L.append(f"# {fin.company.name} ({fin.company.ticker}) 투자분석 보고서\n")
    L.append(f"**{sections['headline']}**\n")
    L.append(f"작성일 {ctx['as_of']} · {'AI 해석: ' + ctx['ai_model'] if ctx['ai_used'] else 'AI 미사용 (규칙 기반 모드)'}\n")
    L.append("| 투자의견 | 목표주가 | 현재가 | 상승여력 | DCF (Base) | 상대가치 |\n|---|---|---|---|---|---|")
    L.append(f"| **{facts['rating']['text']}** | **{facts['target_price']['text']}** | {facts['price']['text']} | "
             f"{facts['upside']['text']} | {facts['dcf_value']['text']} | {facts['rel_value']['text']} |\n")

    L.append(f"## 1. {SECTION_TITLES['investment_summary']}\n\n{sections['investment_summary']}\n")
    L.append(f"## 2. {SECTION_TITLES['business_overview']}\n\n{sections['business_overview']}\n")

    L.append("## 3. 재무 분석\n")
    L.append(f"| 항목 ({unit}) | " + " | ".join(f"FY{y}" for y in fin.years) + " |")
    L.append("|---|" + "---:|" * len(fin.years))
    for k in ["revenue", "operating_income", "net_income_parent", "cfo", "capex", "total_assets", "total_equity"]:
        L.append(f"| {LABELS_KO[k]} | " + " | ".join(_m(fin.get(y, k), cur) for y in fin.years) + " |")
    for k in ["revenue_growth", "operating_margin", "net_margin", "roe", "roic", "debt_to_equity", "fcf_margin"]:
        label, kind = RATIO_DEFS[k]
        cells = [ctx["ratios"][y][k] for y in fin.years]
        L.append(f"| {label} | " + " | ".join("-" if v is None else (f"{v:.1%}" if kind == "pct" else f"{v:.1f}x") for v in cells) + " |")
    L.append(f"\n*출처: {fin.source_note}*\n")
    L.append(f"{sections['financial_commentary']}\n")

    L.append("## 4. 실적 추정\n\n![매출·이익률](charts/revenue.png)\n")
    db = d["base"]
    L.append(f"| Base 시나리오 ({unit}) | " + " | ".join(f"FY{y}E" for y in db.years) + " |")
    L.append("|---|" + "---:|" * len(db.years))
    L.append("| 매출 성장률 | " + " | ".join(f"{g:.1%}" for g in db.growth) + " |")
    L.append("| 매출액 | " + " | ".join(_m(v, cur) for v in db.revenue) + " |")
    L.append("| 영업이익률 | " + " | ".join(f"{m:.1%}" for m in db.margin) + " |")
    L.append("| 영업이익 | " + " | ".join(_m(v, cur) for v in db.ebit) + " |")
    L.append("| FCFF | " + " | ".join(_m(v, cur) for v in db.fcff) + " |")
    L.append(f"\n기타 가정: 세율 {a.tax_rate:.1%}, D&A/매출 {a.da_pct_revenue:.1%}, CAPEX/매출 {a.capex_pct_revenue:.1%}, "
             f"순운전자본/매출 {a.nwc_pct_revenue:.1%}\n")
    L.append(f"{sections['forecast_commentary']}\n")
    if ctx["guardrail_log"]:
        L.append("> **가드레일 조정** (AI 제안값이 허용범위를 벗어나 코드가 제한): " + "; ".join(ctx["guardrail_log"]) + "\n")

    L.append("## 5. 밸류에이션\n\n### 5.1 DCF\n")
    L.append("| 항목 | 값 |\n|---|---:|")
    for label, val in [("무위험수익률", f"{w.rf:.2%}"), ("베타", f"{w.beta:.2f}"), ("ERP", f"{w.erp:.2%}"),
                       ("자기자본비용", f"{w.cost_of_equity:.2%}"), ("세후 타인자본비용", f"{w.kd_aftertax:.2%}"),
                       ("자기자본 비중", f"{w.weight_equity:.1%}"), ("**WACC**", f"**{w.wacc:.2%}**"),
                       ("영구성장률", f"{a.terminal_growth:.2%}"), ("기업가치", facts["ev"]["text"]),
                       ("순차입금", facts["net_debt"]["text"]), ("주주가치", facts["equity_value"]["text"]),
                       ("**주당가치 (Base)**", f"**{facts['dcf_value']['text']}**"),
                       ("Bull / Bear", f"{facts['dcf_bull']['text']} / {facts['dcf_bear']['text']}"),
                       ("잔존가치 비중", facts["tv_share"]["text"])]:
        L.append(f"| {label} | {val} |")
    sens = ctx["sensitivity"]
    L.append("\n**민감도 (주당가치)** — 행: WACC, 열: 영구성장률\n")
    L.append("| WACC \\ g | " + " | ".join(f"{g:.2%}" for g in sens["g"]) + " |")
    L.append("|---|" + "---:|" * len(sens["g"]))
    for wv, row in zip(sens["wacc"], sens["values"]):
        L.append(f"| {wv:.2%} | " + " | ".join("-" if v is None else fmt_price(v, cur) for v in row) + " |")

    L.append("\n### 5.2 상대가치\n")
    if rel and rel.peers:
        L.append("| 비교기업 | 티커 | PER | EV/EBITDA | PBR |\n|---|---|---:|---:|---:|")
        f = lambda v: "-" if v is None else f"{v:.1f}x"
        for p in rel.peers:
            L.append(f"| {p.name} | {p.ticker} | {f(p.pe())} | {f(p.ev_ebitda())} | {f(p.pb())} |")
        L.append(f"| **중앙값** | | **{f(rel.median_pe)}** | **{f(rel.median_ev_ebitda)}** | **{f(rel.median_pb)}** |\n")
        L.append("| 적용 멀티플 | 적정 주당가치 |\n|---|---:|")
        for k, v in rel.implied.items():
            L.append(f"| {k} | {fmt_price(v, cur)} |")
        L.append(f"| **평균** | **{fmt_price(rel.value_per_share, cur)}** |\n")
    else:
        L.append("비교기업 데이터가 없어 상대가치평가를 생략했습니다 (목표주가 = DCF).\n")
    L.append("### 5.3 종합\n\n![밸류에이션 범위](charts/football.png)\n")
    wts = ctx["cfg"]["valuation"]["blend_weights"]
    L.append(f"목표주가 = DCF {wts['dcf']:.0%} + 상대가치 {wts['relative']:.0%} 가중평균 → **{facts['target_price']['text']}** "
             f"(상승여력 {facts['upside']['text']}, 투자의견 {facts['rating']['text']})\n")
    L.append(f"{sections['valuation_commentary']}\n")
    L.append(f"## 6. {SECTION_TITLES['risks']}\n\n{sections['risks']}\n")

    # 부록: 검증
    L.append("## 부록 A. 검증 결과\n")
    vr = ctx["verification"]
    ok_rows = sum(1 for r in vr.get("rows", []) if r["status"] == "OK")
    L.append(f"- **Excel 교차검증**: {vr['status']} — Python 엔진과 Excel 수식(LibreOffice 재계산)이 "
             f"{len(vr.get('rows', []))}개 항목 중 {ok_rows}개 일치" + (f" ({vr.get('reason')})" if vr.get("reason") else ""))
    if vr.get("excel_errors"):
        L.append(f"- Excel 오류 셀: {', '.join(vr['excel_errors'][:10])}")
    chk = ctx["checks"]
    L.append(f"- **데이터 검증**: PASS {sum(c['status'] == 'PASS' for c in chk)} / WARN {sum(c['status'] == 'WARN' for c in chk)} "
             f"/ FAIL {sum(c['status'] == 'FAIL' for c in chk)}")
    for c in chk:
        if c["status"] != "PASS":
            L.append(f"  - [{c['status']}] {c['check']} {c['year'] or ''}: {c['detail']}")
    L.append(f"- **수치 감사** (AI 서술 내 자리표시자 외 숫자): {sum(len(v) for v in violations.values())}건"
             + (f" — 미정의 자리표시자: {sorted(set(unknown))}" if unknown else ""))
    for k, vs in violations.items():
        for v in vs[:5]:
            L.append(f"  - {SECTION_TITLES.get(k, k)}: “…{v}…”")
    for wmsg in fin.warnings + ctx["notes"]:
        L.append(f"- 참고: {wmsg}")
    L.append("\n## 부록 B. 데이터 출처·방법론\n")
    L.append(f"- 재무: {fin.source_note}")
    if ctx.get("filing"):
        L.append(f"- 공시 원문: [{ctx['filing'].title}]({ctx['filing'].url})")
    L.append(f"- 주가·베타: Yahoo Finance ({ctx['mkt'].beta_note})")
    L.append(f"- 무위험수익률: {ctx['mkt'].rf_note}")
    L.append("- 역할 분담: 수집·표준화·비율·추정·DCF·상대가치·투자의견은 코드와 Excel 수식이 계산하고, "
             "AI는 공시 해석·가정 제안(가드레일로 검증)·서술만 담당합니다. 보고서의 모든 수치는 코드 계산값을 자리표시자로 삽입한 것입니다.")
    if ctx["ai_usage"]:
        tot_in = sum(u["input_tokens"] + u["cache_read"] + u["cache_write"] for u in ctx["ai_usage"])
        tot_out = sum(u["output_tokens"] for u in ctx["ai_usage"])
        L.append(f"- AI 호출: {len(ctx['ai_usage'])}회, 입력 {tot_in:,} 토큰 / 출력 {tot_out:,} 토큰 (상세: ai_log/)")
    L.append("\n---\n*본 보고서는 자동 생성된 분석 초안으로 투자 권유가 아닙니다. 공시 데이터의 표준화 과정에서 오차가 있을 수 있으니 "
             "Excel 모델의 원천 데이터와 가정을 검토한 뒤 활용하세요.*")
    audit = {"unknown_placeholders": sorted(set(unknown)), "numeric_violations": violations}
    # "$" 두 개 사이를 수식으로 해석하는 렌더러(GitHub, pandoc)를 위해 이스케이프
    return "\n".join(L).replace("$", "\\$"), audit


CSS = """
body { font-family: 'Malgun Gothic','AppleGothic','Noto Sans CJK KR','NanumGothic','WenQuanYi Zen Hei',sans-serif;
       font-size: 10.5pt; line-height: 1.55; color: #222; max-width: 820px; margin: 0 auto; }
h1 { color: #1F3864; border-bottom: 3px solid #1F3864; padding-bottom: 6px; font-size: 20pt; }
h2 { color: #1F3864; border-bottom: 1px solid #8EA9DB; margin-top: 26px; font-size: 14pt; }
h3 { color: #2E75B6; font-size: 12pt; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 9pt; }
th { background: #1F3864; color: #fff; padding: 4px 6px; }
td { border-bottom: 1px solid #ddd; padding: 3px 6px; }
img { max-width: 100%; }
blockquote { background: #FFF2CC; border-left: 4px solid #C55A11; margin: 8px 0; padding: 6px 12px; }
@page { size: A4; margin: 16mm 14mm; }
"""


def to_pdf(md_path: Path) -> tuple[Path | None, str]:
    """Markdown → HTML(pandoc) → PDF(Chromium 우선, 없으면 LibreOffice)."""
    md_path = md_path.resolve()
    html_path = md_path.with_suffix(".html")
    css_path = md_path.parent / "report.css"
    css_path.write_text(CSS, encoding="utf-8")
    if not shutil.which("pandoc"):
        return None, "pandoc이 없어 PDF 생략 (Markdown만 생성)"
    subprocess.run(["pandoc", str(md_path), "-f", "gfm", "-t", "html5", "-s", "--css", css_path.name,
                    "--metadata", f"pagetitle={md_path.stem}", "-o", str(html_path)], check=True, cwd=md_path.parent)
    pdf_path = md_path.with_suffix(".pdf")
    chrome = find_chrome()
    if chrome:
        try:
            with tempfile.TemporaryDirectory() as td:
                subprocess.run([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", f"--user-data-dir={td}",
                                "--no-first-run", "--disable-background-networking", "--disable-extensions",
                                "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri()],
                               check=True, capture_output=True, timeout=180)
            if pdf_path.exists():
                return pdf_path, "Chrome"
        except (subprocess.SubprocessError, OSError):
            pass
    from ..excel.verify import find_soffice
    so = find_soffice()
    if so:
        subprocess.run([so, "--headless", "--convert-to", "pdf", "--outdir", str(md_path.parent), str(html_path)],
                       check=True, capture_output=True, timeout=180)
        if pdf_path.exists():
            return pdf_path, "LibreOffice"
    return None, "PDF 변환기(Chrome/LibreOffice) 없음 — HTML을 브라우저에서 PDF로 인쇄하세요"


def find_chrome() -> str | None:
    import glob
    shells = sorted(glob.glob("/opt/pw-browsers/chromium_headless_shell-*/*/chrome-headless-shell"))
    if shells:
        return shells[-1]
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        if shutil.which(name):
            return shutil.which(name)
    cands = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
             r"C:\Program Files\Google\Chrome\Application\chrome.exe",
             r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
             "/opt/pw-browsers/chromium"] + sorted(glob.glob("/opt/pw-browsers/chromium*/chrome-linux*/chrome"))
    for c in cands:
        if Path(c).is_file():
            return c
    return None
