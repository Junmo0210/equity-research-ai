"""4단계 산출물: 수식이 살아있는 Excel 밸류에이션 모델.

원칙
- 원천 데이터(공시값·주가·가정)만 값으로 입력 (파란 글씨), 나머지는 전부 수식.
- 금액 단위는 백만(통화 단위), 주식수도 백만 주 → 주당가치는 통화 단위.
- Audit 시트: Python 엔진 값 vs Excel 수식 결과를 나란히 두고 차이를 계산 → LibreOffice 재계산으로 자동 검증.
"""
from __future__ import annotations

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as col
from openpyxl.worksheet.worksheet import Worksheet

from ..analysis.ratios import RATIO_DEFS
from ..models import ALL_ITEMS, LABELS_KO

M = 1e6
BLUE = Font(color="0000CC")
BOLD = Font(bold=True)
TITLE = Font(bold=True, size=14)
HEAD_FILL = PatternFill("solid", fgColor="1F3864")
HEAD_FONT = Font(bold=True, color="FFFFFF")
SUB_FILL = PatternFill("solid", fgColor="D9E1F2")
KEY_FILL = PatternFill("solid", fgColor="FFF2CC")
THIN = Border(top=Side(style="thin", color="999999"))
PCT, AMT, MULT = "0.0%", "#,##0;[Red](#,##0)", '0.0"x"'


def _hdr(ws: Worksheet, row: int, values: list, start_col: int = 1):
    for i, v in enumerate(values):
        c = ws.cell(row=row, column=start_col + i, value=v)
        c.fill, c.font = HEAD_FILL, HEAD_FONT
        c.alignment = Alignment(horizontal="center", wrap_text=True)


def _section(ws: Worksheet, row: int, text: str, width: int = 7):
    for i in range(1, width + 1):
        ws.cell(row=row, column=i).fill = SUB_FILL
    ws.cell(row=row, column=1, value=text).font = BOLD


def _num(v):
    return None if v is None else float(v)


class ModelBuilder:
    def __init__(self, ctx: dict):
        self.ctx = ctx
        self.fin = ctx["fin"]
        self.wb = Workbook()
        self.price_fmt = "#,##0.00" if self.fin.company.currency == "USD" else "#,##0"
        self.hist_row: dict[str, int] = {}
        self.hist_col: dict[int, str] = {}
        self.ratio_row: dict[str, int] = {}
        self.audit: list[tuple[str, float | None, str, str]] = []   # (항목, python값, excel참조, 형식)

    # ── 공통 참조 ─────────────────────────────────────────────
    def h(self, item: str, year: int, absolute: bool = True) -> str:
        c, r = self.hist_col[year], self.hist_row[item]
        return f"Historical!${c}${r}" if absolute else f"Historical!{c}{r}"

    def build(self, path):
        self.wb.remove(self.wb.active)
        self.summary = self.wb.create_sheet("Summary")
        self._assumptions()
        self._historical()
        self._ratios()
        self._wacc()
        self._dcf()
        self._sensitivity()
        self._relative()
        self._summary()
        self._checks()
        self._audit()
        for ws in self.wb.worksheets:
            ws.sheet_view.showGridLines = False
            ws.freeze_panes = ws["B4"] if ws.title in ("Historical", "Ratios", "DCF") else None
        from openpyxl.workbook.properties import CalcProperties
        self.wb.calculation = CalcProperties(fullCalcOnLoad=True)
        self.wb.save(path)
        return self.audit_cells

    # ── Assumptions ─────────────────────────────────────────────
    def _assumptions(self):
        ws = self.wb.create_sheet("Assumptions")
        c, fin, mkt, a, cfg = self.ctx["fin"].company, self.fin, self.ctx["mkt"], self.ctx["assumptions"], self.ctx["cfg"]
        ws["A1"], ws["A1"].font = f"{c.name} — 가정 (파란 글씨 = 입력값, 변경 시 전체 모델 재계산)", TITLE
        rows = [(3, "회사명", c.name, ""), (4, "티커", c.ticker, c.market), (5, "통화", c.currency, ""),
                (6, "금액 단위", "백만 " + c.currency, "주식수: 백만 주")]
        for r, k, v, n in rows:
            ws.cell(r, 1, k); ws.cell(r, 2, v); ws.cell(r, 3, n)
        _section(ws, 8, "시장 데이터 (코드 수집·산출)")
        mrows = [
            (9, "현재 주가", mkt.price, self.price_fmt, f"Yahoo Finance {mkt.price_date}"),
            (10, "유통주식수 (백만 주)", fin.shares_current.value / M, "#,##0.000", fin.shares_current.source),
            (11, "무위험수익률", mkt.risk_free_rate, "0.00%", mkt.rf_note),
            (12, "주식위험프리미엄 (ERP)", self.ctx["erp"], "0.00%", "config/assumptions.yaml"),
            (13, "베타", mkt.beta if mkt.beta is not None else 1.0, "0.000", mkt.beta_note or "산출 불가 → 1.0"),
            (14, "법정 한계세율 (Kd 절세효과)", self.ctx["marginal_tax"], "0.0%", "config"),
            (15, "Kd 스프레드 (산출 불가 시)", cfg["valuation"]["cost_of_debt_spread"], "0.00%", "config"),
            (16, "Kd 상한 스프레드", 0.08, "0.00%", "이자비용/평균차입금 상한 = rf + 이 값"),
        ]
        for r, k, v, f, n in mrows:
            ws.cell(r, 1, k); cell = ws.cell(r, 2, v); cell.font, cell.number_format = BLUE, f; ws.cell(r, 3, n)
        _section(ws, 18, "추정 가정 (AI 제안 → 가드레일 검증)" if self.ctx["ai_used"] else "추정 가정 (규칙 기반)")
        y0 = fin.last_year
        _hdr(ws, 19, ["항목"] + [f"FY{y0 + t}E" for t in range(1, 6)])
        ws.cell(20, 1, "매출 성장률"); ws.cell(21, 1, "영업이익률")
        for t in range(5):
            for r, lst in [(20, a.revenue_growth), (21, a.ebit_margin)]:
                cell = ws.cell(r, 2 + t, lst[t]); cell.font, cell.number_format = BLUE, PCT
        for r, k, v in [(22, "법인세율 (NOPAT)", a.tax_rate), (23, "D&A / 매출", a.da_pct_revenue),
                        (24, "CAPEX / 매출", a.capex_pct_revenue), (25, "순운전자본 / 매출", a.nwc_pct_revenue),
                        (26, "영구성장률", a.terminal_growth)]:
            ws.cell(r, 1, k); cell = ws.cell(r, 2, v); cell.font, cell.number_format = BLUE, "0.00%"
        ws.cell(27, 1, "기중 할인 (1=적용, 0=기말)"); ws.cell(27, 2, 1 if self.ctx["mid_year"] else 0).font = BLUE
        _section(ws, 29, "시나리오")
        ws.cell(30, 1, "적용 시나리오 (1=Base, 2=Bull, 3=Bear)"); ws.cell(30, 2, 1).font = BLUE
        _hdr(ws, 31, ["", "Base", "Bull", "Bear"])
        ws.cell(32, 1, "성장률 가감"); ws.cell(33, 1, "영업이익률 가감")
        for j, (g, m) in enumerate([(0, 0), (a.bull.growth_delta, a.bull.margin_delta), (a.bear.growth_delta, a.bear.margin_delta)]):
            for r, v in [(32, g), (33, m)]:
                cell = ws.cell(r, 2 + j, v); cell.font, cell.number_format = BLUE, "+0.0%;-0.0%;0.0%"
        ws.cell(34, 1, "적용 성장률 가감"); ws["B34"] = "=CHOOSE($B$30,B32,C32,D32)"; ws["B34"].number_format = "+0.0%;-0.0%;0.0%"
        ws.cell(35, 1, "적용 마진 가감"); ws["B35"] = "=CHOOSE($B$30,B33,C33,D33)"; ws["B35"].number_format = "+0.0%;-0.0%;0.0%"
        _section(ws, 37, "목표주가 산정")
        w = cfg["valuation"]["blend_weights"]; th = cfg["valuation"]["rating_thresholds"]
        for r, k, v in [(38, "DCF 가중치", w["dcf"]), (39, "상대가치 가중치", w["relative"]),
                        (40, "BUY 기준 상승여력", th["buy"]), (41, "SELL 기준 상승여력", th["sell"])]:
            ws.cell(r, 1, k); cell = ws.cell(r, 2, v); cell.font, cell.number_format = BLUE, "0%"
        _section(ws, 43, "가정 근거 (AI 해석)" if self.ctx["ai_used"] else "가정 근거 (규칙)")
        _hdr(ws, 44, ["가정", "근거", "", "", "", "", "증거"])
        r = 45
        for ra in a.rationales:
            ws.cell(r, 1, ra.assumption)
            ws.cell(r, 2, ra.reasoning).alignment = Alignment(wrap_text=True, vertical="top")
            ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=6)
            ws.cell(r, 7, " / ".join(ra.evidence)).alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[r].height = 60
            r += 1
        if self.ctx["guardrail_log"]:
            _section(ws, r + 1, "가드레일 조정 내역 (코드가 AI 제안값을 제한)")
            for i, line in enumerate(self.ctx["guardrail_log"]):
                ws.cell(r + 2 + i, 1, line)
        ws.column_dimensions["A"].width = 34
        for cc in "BCDEF":
            ws.column_dimensions[cc].width = 14
        ws.column_dimensions["G"].width = 60

    # ── Historical ─────────────────────────────────────────────
    def _historical(self):
        ws = self.wb.create_sheet("Historical")
        fin = self.fin
        ws["A1"], ws["A1"].font = f"표준화 재무제표 (단위: 백만 {fin.company.currency}, EPS는 통화 단위)", TITLE
        ws["A2"] = fin.source_note
        _hdr(ws, 3, ["항목", "키"] + [f"FY{y}" for y in fin.years] + ["출처 (최근 연도)"])
        for j, y in enumerate(fin.years):
            self.hist_col[y] = col(3 + j)
        for i, item in enumerate(ALL_ITEMS):
            r = 4 + i
            self.hist_row[item] = r
            ws.cell(r, 1, LABELS_KO[item]); ws.cell(r, 2, item).font = Font(color="888888", size=8)
            for y in fin.years:
                v = fin.get(y, item)
                if v is not None:
                    cell = ws.cell(r, 3 + fin.years.index(y), v if item == "eps_diluted" else v / M)
                    cell.font, cell.number_format = BLUE, ("#,##0.00" if item == "eps_diluted" else AMT)
            li = fin.data[fin.last_year].get(item)
            ws.cell(r, 3 + len(fin.years), li.source if li else "").font = Font(color="666666", size=8)
        ws.column_dimensions["A"].width = 30
        for j in range(len(fin.years)):
            ws.column_dimensions[col(3 + j)].width = 13
        ws.column_dimensions[col(3 + len(fin.years))].width = 70

    # ── Ratios ─────────────────────────────────────────────────
    def _ratios(self):
        ws = self.wb.create_sheet("Ratios")
        fin = self.fin
        ws["A1"], ws["A1"].font = "재무비율 (전부 Historical 시트 참조 수식)", TITLE
        _hdr(ws, 3, ["비율", "키"] + [f"FY{y}" for y in fin.years])
        for i, (k, (label, kind)) in enumerate(RATIO_DEFS.items()):
            self.ratio_row[k] = 4 + i
        for i, (k, (label, kind)) in enumerate(RATIO_DEFS.items()):
            r = 4 + i
            ws.cell(r, 1, label); ws.cell(r, 2, k).font = Font(color="888888", size=8)
            for j, y in enumerate(fin.years):
                g = lambda item, yy=y: self.h(item, yy, absolute=False)
                prev = fin.years[j - 1] if j > 0 else None
                div = lambda a, b: f'IF(AND(ISNUMBER({a}),ISNUMBER({b}),{b}<>0),{a}/{b},"")'
                rev, oi, da = g("revenue"), g("operating_income"), g("d_and_a")
                nd = f'(N({g("short_term_debt")})+N({g("long_term_debt")})-N({g("cash")})-N({g("short_term_investments")}))'
                tax_cell = f"{col(3 + j)}{self.ratio_row['effective_tax_rate']}"
                f = {
                    "revenue_growth": (f'IF(AND(ISNUMBER({g("revenue", prev)}),{g("revenue", prev)}<>0),{rev}/{g("revenue", prev)}-1,"")'
                                       if prev else '""'),
                    "gross_margin": div(g("gross_profit"), rev),
                    "operating_margin": div(oi, rev),
                    "ebitda_margin": f'IF(AND(ISNUMBER({oi}),ISNUMBER({da}),ISNUMBER({rev}),{rev}<>0),({oi}+{da})/{rev},"")',
                    "net_margin": div(g("net_income"), rev),
                    "effective_tax_rate": div(g("income_tax"), g("pretax_income")),
                    "roe": div(g("net_income_parent"), g("equity_parent")),
                    "roa": div(g("net_income"), g("total_assets")),
                    "roic": (f'IF(AND(ISNUMBER({oi}),ISNUMBER({tax_cell}),N({g("total_equity")})+{nd}>0),'
                             f'{oi}*(1-MIN(MAX({tax_cell},0),0.5))/(N({g("total_equity")})+{nd}),"")'),
                    "debt_to_equity": div(g("total_liabilities"), g("total_equity")),
                    "net_debt_to_ebitda": f'IF(AND(ISNUMBER({oi}),ISNUMBER({da})),IF({oi}+{da}>0,{nd}/({oi}+{da}),""),"")',
                    "current_ratio": div(g("total_current_assets"), g("total_current_liabilities")),
                    "capex_pct_revenue": div(g("capex"), rev),
                    "da_pct_revenue": div(da, rev),
                    "nwc_pct_revenue": (f'IF(AND(ISNUMBER({g("accounts_receivable")}),ISNUMBER({rev}),{rev}<>0),'
                                        f'({g("accounts_receivable")}+N({g("inventory")})-N({g("accounts_payable")}))/{rev},"")'),
                    "fcf_margin": (f'IF(AND(ISNUMBER({g("cfo")}),ISNUMBER({g("capex")}),ISNUMBER({rev}),{rev}<>0),'
                                   f'({g("cfo")}-{g("capex")})/{rev},"")'),
                }[k]
                cell = ws.cell(r, 3 + j, "=" + f)
                cell.number_format = PCT if kind == "pct" else MULT
        ws.column_dimensions["A"].width = 28
        last = len(self.fin.years) - 1
        for k, (label, kind) in RATIO_DEFS.items():
            v = self.ctx["ratios"][self.fin.last_year].get(k)
            self.audit.append((f"비율 FY{self.fin.last_year} {label}", v, f"Ratios!{col(3 + last)}{self.ratio_row[k]}",
                               PCT if kind == "pct" else MULT))

    # ── WACC ───────────────────────────────────────────────────
    def _wacc(self):
        ws = self.wb.create_sheet("WACC")
        y, fin = self.fin.last_year, self.fin
        prev = fin.years[-2] if len(fin.years) > 1 else y
        ws["A1"], ws["A1"].font = "가중평균자본비용 (WACC)", TITLE
        rows = [
            (3, "무위험수익률", "=Assumptions!B11", "0.00%"), (4, "베타", "=Assumptions!B13", "0.000"),
            (5, "ERP", "=Assumptions!B12", "0.00%"), (6, "자기자본비용 Ke = rf + β·ERP", "=B3+B4*B5", "0.00%"),
            (8, f"이자비용 FY{y}", f"=N({self.h('interest_expense', y)})", AMT),
            (9, f"총차입금 FY{y}", f"=N({self.h('short_term_debt', y)})+N({self.h('long_term_debt', y)})", AMT),
            (10, f"총차입금 FY{prev}", f"=N({self.h('short_term_debt', prev)})+N({self.h('long_term_debt', prev)})", AMT),
            (11, "평균 차입금", "=(B9+B10)/2", AMT),
            (12, "세전 타인자본비용 Kd", "=IF(AND(B8>0,B11>0),MIN(MAX(B8/B11,B3),B3+Assumptions!B16),B3+Assumptions!B15)", "0.00%"),
            (13, "한계세율", "=Assumptions!B14", "0.0%"), (14, "세후 Kd", "=B12*(1-B13)", "0.00%"),
            (16, "시가총액 E", "=Assumptions!B9*Assumptions!B10", AMT), (17, "차입금 D (장부가)", "=B9", AMT),
            (18, "E/(D+E)", "=B16/(B16+B17)", "0.0%"), (19, "D/(D+E)", "=1-B18", "0.0%"),
            (20, "WACC", "=B18*B6+B19*B14", "0.00%"),
        ]
        for r, k, f, fmt in rows:
            ws.cell(r, 1, k); cell = ws.cell(r, 2, f); cell.number_format = fmt
        ws["A20"].font = ws["B20"].font = BOLD; ws["B20"].fill = KEY_FILL
        ws.column_dimensions["A"].width = 34; ws.column_dimensions["B"].width = 16
        w = self.ctx["wacc"]
        self.audit += [("자기자본비용", w.cost_of_equity, "WACC!B6", "0.00%"), ("세전 Kd", w.kd_pretax, "WACC!B12", "0.00%"),
                       ("WACC", w.wacc, "WACC!B20", "0.00%")]

    # ── DCF ────────────────────────────────────────────────────
    def _dcf(self):
        ws = self.wb.create_sheet("DCF")
        y0, fin = self.fin.last_year, self.fin
        ws["A1"], ws["A1"].font = "DCF (FCFF) — Assumptions!B30 시나리오 적용", TITLE
        _hdr(ws, 3, ["항목", f"FY{y0}A"] + [f"FY{y0 + t}E" for t in range(1, 6)])
        labels = {4: "연도", 5: "매출 성장률", 6: "영업이익률", 7: "매출액", 8: "영업이익 (EBIT)", 9: "EBIT 법인세",
                  10: "NOPAT", 11: "D&A", 12: "CAPEX", 13: "순운전자본", 14: "순운전자본 증감", 15: "FCFF",
                  16: "할인 기간", 17: "할인계수", 18: "FCFF 현재가치"}
        for r, t in labels.items():
            ws.cell(r, 1, t)
        ws["B4"] = y0
        ws["B7"] = f"={self.h('revenue', y0)}"
        ws["B13"] = "=B7*Assumptions!$B$25"
        for t in range(1, 6):
            c, p = col(2 + t), col(1 + t)
            ac = col(1 + t)  # Assumptions 열 (B..F)
            ws[f"{c}4"] = f"={p}4+1"
            ws[f"{c}5"] = f"=Assumptions!{ac}20+Assumptions!$B$34"
            ws[f"{c}6"] = f"=Assumptions!{ac}21+Assumptions!$B$35"
            ws[f"{c}7"] = f"={p}7*(1+{c}5)"
            ws[f"{c}8"] = f"={c}7*{c}6"
            ws[f"{c}9"] = f"={c}8*Assumptions!$B$22"
            ws[f"{c}10"] = f"={c}8-{c}9"
            ws[f"{c}11"] = f"={c}7*Assumptions!$B$23"
            ws[f"{c}12"] = f"={c}7*Assumptions!$B$24"
            ws[f"{c}13"] = f"={c}7*Assumptions!$B$25"
            ws[f"{c}14"] = f"={c}13-{p}13"
            ws[f"{c}15"] = f"={c}10+{c}11-{c}12-{c}14"
            ws[f"{c}16"] = f"={t}-0.5*Assumptions!$B$27"
            ws[f"{c}17"] = f"=1/(1+WACC!$B$20)^{c}16"
            ws[f"{c}18"] = f"={c}15*{c}17"
        for r in range(5, 19):
            for cc in range(2, 8):
                ws.cell(r, cc).number_format = PCT if r in (5, 6) else ("0.0" if r == 16 else ("0.0000" if r == 17 else AMT))
        ws["A15"].font = BOLD
        val = [
            (20, "FCFF 현재가치 합계", "=SUM(C18:G18)", AMT), (21, "영구성장률", "=Assumptions!B26", "0.00%"),
            (22, "잔존가치 (Gordon)", "=G15*(1+B21)/(WACC!B20-B21)", AMT),
            (23, "잔존가치 현재가치", "=B22/(1+WACC!B20)^5", AMT), (24, "기업가치 (EV)", "=B20+B23", AMT),
            (25, f"순차입금 FY{y0}", f"=N({self.h('short_term_debt', y0)})+N({self.h('long_term_debt', y0)})"
                                   f"-N({self.h('cash', y0)})-N({self.h('short_term_investments', y0)})", AMT),
            (26, "비지배지분", f"=N({self.h('minority_interest', y0)})", AMT),
            (27, "주주가치", "=B24-B25-B26", AMT), (28, "주식수 (백만 주)", "=Assumptions!B10", "#,##0.000"),
            (29, "주당 내재가치", "=B27/B28", self.price_fmt), (30, "EV 중 잔존가치 비중", "=B23/B24", PCT),
            (31, "현재 주가", "=Assumptions!B9", self.price_fmt), (32, "상승여력", "=B29/B31-1", "+0.0%;-0.0%"),
        ]
        for r, k, f, fmt in val:
            ws.cell(r, 1, k); cell = ws.cell(r, 2, f); cell.number_format = fmt
        for r in (24, 27, 29):
            ws.cell(r, 1).font = ws.cell(r, 2).font = BOLD
        ws["B29"].fill = KEY_FILL
        ws.column_dimensions["A"].width = 26
        for cc in range(2, 8):
            ws.column_dimensions[col(cc)].width = 14
        d = self.ctx["dcf"]["base"]
        for t in range(5):
            c = col(3 + t)
            self.audit += [(f"매출 FY{d.years[t]}E", d.revenue[t] / M, f"DCF!{c}7", AMT),
                           (f"FCFF FY{d.years[t]}E", d.fcff[t] / M, f"DCF!{c}15", AMT)]
        self.audit += [("잔존가치", d.terminal_value / M, "DCF!B22", AMT), ("기업가치", d.enterprise_value / M, "DCF!B24", AMT),
                       ("순차입금", d.net_debt / M, "DCF!B25", AMT), ("주주가치", d.equity_value / M, "DCF!B27", AMT),
                       ("DCF 주당가치", d.value_per_share, "DCF!B29", self.price_fmt)]

    # ── Sensitivity ────────────────────────────────────────────
    def _sensitivity(self):
        from ..valuation.dcf import G_STEPS, WACC_STEPS
        ws = self.wb.create_sheet("Sensitivity")
        ws["A1"], ws["A1"].font = "민감도: 주당가치 (행 = WACC, 열 = 영구성장률)", TITLE
        ws["A3"] = "WACC \\ g"
        ws["A3"].fill, ws["A3"].font = HEAD_FILL, HEAD_FONT
        for j, dg in enumerate(G_STEPS):
            c = ws.cell(3, 2 + j, f"=Assumptions!$B$26+({dg})"); c.number_format = "0.00%"; c.fill, c.font = HEAD_FILL, HEAD_FONT
        for i, dw in enumerate(WACC_STEPS):
            r = 4 + i
            c = ws.cell(r, 1, f"=WACC!$B$20+({dw})"); c.number_format = "0.00%"; c.font = BOLD
            for j in range(len(G_STEPS)):
                gc, wc = f"{col(2 + j)}$3", f"$A{r}"
                f = (f'=IF({wc}-{gc}<=0.005,"",(SUMPRODUCT(DCF!$C$15:$G$15,1/(1+{wc})^DCF!$C$16:$G$16)'
                     f'+DCF!$G$15*(1+{gc})/({wc}-{gc})/(1+{wc})^5-DCF!$B$25-DCF!$B$26)/DCF!$B$28)')
                cell = ws.cell(r, 2 + j, f); cell.number_format = self.price_fmt
                if i == 2 and j == 2:
                    cell.fill, cell.font = KEY_FILL, BOLD
        ws.column_dimensions["A"].width = 12
        s = self.ctx["sensitivity"]
        self.audit += [("민감도 중앙값", s["values"][2][2], "Sensitivity!D6", self.price_fmt),
                       ("민감도 (WACC−1%p, g+0.5%p)", s["values"][0][4], "Sensitivity!F4", self.price_fmt),
                       ("민감도 (WACC+1%p, g−0.5%p)", s["values"][4][0], "Sensitivity!B8", self.price_fmt)]

    # ── Relative ───────────────────────────────────────────────
    def _relative(self):
        ws = self.wb.create_sheet("Relative")
        rel, tgt = self.ctx["relative"], self.ctx["target_metrics"]
        ws["A1"], ws["A1"].font = "상대가치평가 — 피어 멀티플 중앙값 (이상치: PER>100, EV/EBITDA>60, PBR>30 제외)", TITLE
        heads = ["기업", "티커", "회계연도", "주가", "주식수(백만)", "시가총액", "순차입금", "비지배지분", "EV",
                 "지배순이익", "EBITDA", "지배자본", "PER", "EV/EBITDA", "PBR", "선정 사유"]
        _hdr(ws, 3, heads)
        peers = rel.peers if rel else []
        r0 = 4
        for i, p in enumerate(peers):
            r = r0 + i
            vals = [p.name, p.ticker, p.fiscal_year, p.price, p.shares / M, None, p.net_debt / M, p.minority_interest / M, None,
                    _num(p.net_income and p.net_income / M), _num(p.ebitda and p.ebitda / M), _num(p.equity and p.equity / M)]
            for j, v in enumerate(vals):
                if v is not None:
                    cell = ws.cell(r, 1 + j, v)
                    if j >= 3:
                        cell.font, cell.number_format = BLUE, AMT if j != 3 else "#,##0.00"
            ws[f"F{r}"] = f"=D{r}*E{r}"
            ws[f"I{r}"] = f"=F{r}+G{r}+H{r}"
            ws[f"M{r}"] = f'=IF(AND(ISNUMBER(J{r}),N(J{r})>0),IF(F{r}/J{r}<={100},F{r}/J{r},""),"")'
            ws[f"N{r}"] = f'=IF(AND(ISNUMBER(K{r}),N(K{r})>0),IF(AND(I{r}/K{r}>0,I{r}/K{r}<={60}),I{r}/K{r},""),"")'
            ws[f"O{r}"] = f'=IF(AND(ISNUMBER(L{r}),N(L{r})>0),IF(F{r}/L{r}<={30},F{r}/L{r},""),"")'
            ws[f"P{r}"] = p.reason
            for cc in "FI":
                ws[f"{cc}{r}"].number_format = AMT
            for cc in "MNO":
                ws[f"{cc}{r}"].number_format = MULT
        last = r0 + max(len(peers), 1) - 1
        mr = last + 2
        ws.cell(mr, 1, "피어 중앙값").font = BOLD
        for cc in "MNO":
            ws[f"{cc}{mr}"] = f'=IFERROR(MEDIAN({cc}{r0}:{cc}{last}),"")'
            ws[f"{cc}{mr}"].number_format = MULT; ws[f"{cc}{mr}"].fill = KEY_FILL
        y0 = self.fin.last_year
        tr = mr + 2
        _section(ws, tr, "대상 기업 지표 (Historical 연결)", 6)
        oi, da = self.h("operating_income", y0), self.h("d_and_a", y0)
        items = [(tr + 1, "지배순이익", f"={self.h('net_income_parent', y0)}"),
                 (tr + 2, "EBITDA", f'=IF(AND(ISNUMBER({oi}),ISNUMBER({da})),{oi}+{da},"")'),
                 (tr + 3, "지배자본", f"={self.h('equity_parent', y0)}"), (tr + 4, "순차입금", "=DCF!B25"),
                 (tr + 5, "비지배지분", "=DCF!B26"), (tr + 6, "주식수 (백만 주)", "=Assumptions!B10")]
        for r, k, f in items:
            ws.cell(r, 1, k); ws.cell(r, 2, f).number_format = AMT
        ir = tr + 8
        _section(ws, ir, "멀티플별 적정 주당가치", 6)
        b = lambda n: f"B{tr + n}"
        imp = [(ir + 1, "PER", f'=IF(AND(ISNUMBER(M{mr}),N({b(1)})>0),M{mr}*{b(1)}/{b(6)},"")'),
               (ir + 2, "EV/EBITDA", f'=IF(AND(ISNUMBER(N{mr}),ISNUMBER({b(2)})),IF({b(2)}>0,(N{mr}*{b(2)}-{b(4)}-{b(5)})/{b(6)},""),"")'),
               (ir + 3, "PBR", f'=IF(AND(ISNUMBER(O{mr}),N({b(3)})>0),O{mr}*{b(3)}/{b(6)},"")')]
        for r, k, f in imp:
            ws.cell(r, 1, k); ws.cell(r, 2, f).number_format = self.price_fmt
        self.rel_value_cell = f"Relative!B{ir + 4}"
        ws.cell(ir + 4, 1, "상대가치 주당가치 (평균)").font = BOLD
        ws.cell(ir + 4, 2, f'=IFERROR(AVERAGE(B{ir + 1}:B{ir + 3}),"")').number_format = self.price_fmt
        ws.cell(ir + 4, 2).fill = KEY_FILL
        ws.column_dimensions["A"].width = 28; ws.column_dimensions["P"].width = 50
        if rel:
            self.audit += [("피어 중앙값 PER", rel.median_pe, f"Relative!M{mr}", MULT),
                           ("피어 중앙값 EV/EBITDA", rel.median_ev_ebitda, f"Relative!N{mr}", MULT),
                           ("피어 중앙값 PBR", rel.median_pb, f"Relative!O{mr}", MULT),
                           ("상대가치 주당가치", rel.value_per_share, self.rel_value_cell, self.price_fmt)]

    # ── Summary ────────────────────────────────────────────────
    def _summary(self):
        ws = self.summary
        c, s = self.fin.company, self.ctx["summary"]
        ws["A1"], ws["A1"].font = f"{c.name} ({c.ticker}) 밸류에이션 요약", Font(bold=True, size=16)
        ws["A2"] = f"작성일 {self.ctx['as_of']} · 데이터: {self.fin.source_note}"
        rows = [
            (4, "현재 주가", "=Assumptions!B9", self.price_fmt),
            (5, "DCF 주당가치 (적용 시나리오)", "=DCF!B29", self.price_fmt),
            (6, "상대가치 주당가치", f"={self.rel_value_cell}", self.price_fmt),
            (7, "목표주가 (가중평균)", f'=IF(ISNUMBER(B6),Assumptions!B38*B5+Assumptions!B39*B6,B5)', self.price_fmt),
            (8, "상승여력", "=B7/B4-1", "+0.0%;-0.0%"),
            (9, "투자의견", '=IF(B8>=Assumptions!B40,"BUY",IF(B8<=Assumptions!B41,"SELL","HOLD"))', None),
            (11, "WACC", "=WACC!B20", "0.00%"), (12, "영구성장률", "=Assumptions!B26", "0.00%"),
            (13, "EV 중 잔존가치 비중", "=DCF!B30", PCT),
        ]
        for r, k, f, fmt in rows:
            ws.cell(r, 1, k); cell = ws.cell(r, 2, f)
            if fmt:
                cell.number_format = fmt
        for r in (7, 8, 9):
            ws.cell(r, 1).font = ws.cell(r, 2).font = Font(bold=True, size=12)
            ws.cell(r, 2).fill = KEY_FILL
        _section(ws, 15, "시나리오별 DCF 주당가치 (Python 엔진 계산값 · Assumptions!B30을 바꾸면 DCF 시트에서 재현)", 4)
        for i, k in enumerate(["bear", "base", "bull"]):
            d = self.ctx["dcf"][k]
            ws.cell(16 + i, 1, k.capitalize()); cell = ws.cell(16 + i, 2, d.value_per_share); cell.number_format = self.price_fmt
        ws["A20"] = "시트 안내: Assumptions(입력) → Historical(공시) → Ratios → WACC → DCF → Sensitivity → Relative → Checks → Audit(교차검증)"
        ws.column_dimensions["A"].width = 34; ws.column_dimensions["B"].width = 18
        self.audit += [("목표주가", s["target_price"], "Summary!B7", self.price_fmt),
                       ("상승여력", s["upside"], "Summary!B8", "+0.0%;-0.0%")]

    # ── Checks ─────────────────────────────────────────────────
    def _checks(self):
        ws = self.wb.create_sheet("Checks")
        ws["A1"], ws["A1"].font = "데이터 검증", TITLE
        _hdr(ws, 3, ["검증", "연도", "결과", "내용"])
        for i, ch in enumerate(self.ctx["checks"]):
            ws.append([ch["check"], ch["year"], ch["status"], ch["detail"]])
            color = {"PASS": "C6EFCE", "WARN": "FFEB9C", "FAIL": "FFC7CE"}[ch["status"]]
            ws.cell(4 + i, 3).fill = PatternFill("solid", fgColor=color)
        r = ws.max_row + 2
        _section(ws, r, "Excel 수식 검증: 자산 − (부채 + 자본)", 4)
        for j, y in enumerate(self.fin.years):
            ws.cell(r + 1 + j, 1, f"FY{y}")
            ws.cell(r + 1 + j, 2, f"=N({self.h('total_assets', y)})-(N({self.h('total_liabilities', y)})+N({self.h('total_equity', y)}))").number_format = AMT
        ws.column_dimensions["A"].width = 26; ws.column_dimensions["D"].width = 70

    # ── Audit ──────────────────────────────────────────────────
    def _audit(self):
        ws = self.wb.create_sheet("Audit")
        ws["A1"], ws["A1"].font = "교차검증: Python 계산 엔진 vs Excel 수식 (두 경로가 독립적으로 같은 값을 내는지 확인)", TITLE
        ws["A2"] = "상대오차 < 0.0001% 이면 OK. 금액 단위 백만. 시나리오 선택이 Base(1)일 때 기준."
        _hdr(ws, 3, ["항목", "Python 값", "Excel 수식 값", "상대오차", "판정", "Excel 셀"])
        self.audit_cells = []
        for i, (label, pv, ref, fmt) in enumerate(self.audit):
            r = 4 + i
            ws.cell(r, 1, label)
            b = ws.cell(r, 2, pv if pv is not None else ""); b.number_format = fmt
            cc = ws.cell(r, 3, f"={ref}"); cc.number_format = fmt
            ws.cell(r, 4, f'=IF(AND(ISNUMBER(B{r}),ISNUMBER(C{r})),ABS(C{r}-B{r})/MAX(ABS(B{r}),0.000000001),IF(AND(B{r}="",C{r}=""),0,1))').number_format = "0.0000%"
            ws.cell(r, 5, f'=IF(D{r}<0.000001,"OK","CHECK")')
            ws.cell(r, 6, ref).font = Font(color="888888", size=8)
            self.audit_cells.append((label, pv, ref, r))
        n = len(self.audit)
        ws.cell(5 + n, 1, "전체 판정").font = BOLD
        ws.cell(5 + n, 5, f'=IF(COUNTIF(E4:E{3 + n},"OK")={n},"ALL OK","CHECK")').font = BOLD
        ws.column_dimensions["A"].width = 34
        for cc in "BCD":
            ws.column_dimensions[cc].width = 16
