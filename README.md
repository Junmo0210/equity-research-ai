# Equity Research AI (프로토타입)

> **⚠️ 프로토타입 저장소입니다 — 유지보수하지 않습니다.**
>
> 이 저장소는 전체 흐름(수집 → 분석 → 밸류에이션 → 보고서)을 빠르게 검증하기 위해 만든 초기 프로토타입입니다.
> 정식 개발은 새 저장소 **AI Equity Research Agent** (`ai-equity-research-agent`)에서 단계적으로 진행합니다.
>
> 이 프로토타입의 알려진 한계 (정식 버전에서 재설계):
> - DART 연결재무제표가 없으면 별도재무제표로 자동 대체함 (연결·별도 혼합 위험)
> - 공시 접수번호와 원본 계정 행을 보존하지 않고 고정 항목으로 요약함
> - 공시 정정·비교기간 재작성 차이를 추적하지 않음
> - 한국(DART) 수집과 AI(Claude API) 단계는 실데이터로 검증되지 않음
>
> 참고·비교 용도로만 보존하며, 실무 판단에 사용하지 마세요.

기업명만 입력하면 **공시 수집 → 재무제표 분석 → 실적 추정 → DCF·상대가치평가 → 투자분석 보고서**까지 자동으로 만드는 시스템입니다.

```bash
python -m eqra "Apple"
python -m eqra 삼성전자
```

## 설계 원칙: 계산은 코드, 해석은 AI

| 역할 | 담당 | 검증 방법 |
|---|---|---|
| 공시 수집·표준화 | 코드 (SEC EDGAR, OpenDART) | 원천 태그/계정명을 셀마다 기록, 자산=부채+자본 등 검증 |
| 재무비율·추정·DCF·상대가치 | 코드 (Python 엔진) + **Excel 수식** | 두 경로를 독립 계산 → Audit 시트에서 자동 대조 |
| 공시 해석·가정 수립·피어 선정 | AI (Claude) | JSON 스키마 강제 + 가드레일(허용범위)로 코드가 검증·제한 |
| 보고서 서술 | AI | 숫자는 `{{자리표시자}}`로만 쓰고 코드가 값을 삽입, 직접 쓴 숫자는 감사 로그에 기록 |
| 투자의견·목표주가 | 코드 | 상승여력 기준 규칙 (`config/assumptions.yaml`) |

AI는 숫자를 만들지 않습니다. AI가 제안한 가정(성장률·마진 등)도 가드레일을 통과해야 모델에 들어가며, 조정 내역은 Excel과 보고서에 남습니다.

## 파이프라인

```
[1] 기업 확인·수집    resolver → sec.py / dart.py / prices.py
[2] 분석             standardize.py (파생·검증) → ratios.py
[3] 가설 (AI)        hypotheses.py: 10-K/사업보고서 원문 + 과거 지표 → 가정·근거·시나리오·피어
[4] 추정·밸류에이션    dcf.py (5개년 FCFF, WACC, 민감도) · relative.py (피어 중앙값 PER/EV·EBITDA/PBR)
[5] Excel            builder.py (수식 모델) → verify.py (LibreOffice 재계산, Python 값과 대조)
[6] 보고서 (AI)       narrative.py (서술) → render.py (표·차트·수치 감사 → Markdown/PDF)
```

## 설치

```bash
git clone https://github.com/<계정>/equity-research-ai.git
cd equity-research-ai
pip install -r requirements.txt
```

선택 도구:
- **LibreOffice**: Excel 수식 교차검증 (없으면 검증만 생략)
- **pandoc + Chrome/Chromium**: PDF 생성 (없으면 Markdown·HTML만)

## 설정 (환경변수)

| 변수 | 필요 | 설명 |
|---|---|---|
| `ANTHROPIC_API_KEY` | AI 모드 | https://console.anthropic.com — 없으면 규칙 기반 모드로 실행 |
| `DART_API_KEY` | 한국 기업 | https://opendart.fss.or.kr 무료 발급 |
| `SEC_USER_AGENT` | 권장 | SEC 요구사항: `"이름 이메일"` 형식 |

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export DART_API_KEY="..."
export SEC_USER_AGENT="Hong Gildong hong@example.com"
```

## 사용법

```bash
# src 폴더를 경로에 추가 (또는 pip install -e . 대신)
export PYTHONPATH=src

python -m eqra "Apple"                       # 미국: 회사명 또는 티커
python -m eqra NVDA --peers AMD AVGO         # 비교기업 직접 지정 (AI 제안에 추가)
python -m eqra 삼성전자                        # 한국: 회사명 또는 6자리 종목코드
python -m eqra MSFT --no-ai                  # AI 없이 규칙 기반 가정·템플릿 보고서
```

### 산출물 (`outputs/<티커>_<날짜>/`)

| 파일 | 내용 |
|---|---|
| `*_valuation_model.xlsx` | 수식이 살아있는 모델. **Assumptions 시트의 파란 글씨**를 바꾸면 전체 재계산 |
| `*_report.md` / `.pdf` / `.html` | 투자분석 보고서 |
| `analysis.json` | 모든 원천 데이터·계산값·검증 결과 |
| `ai_log/*.json` | AI 요청·응답 원문 (감사 추적) |

### Excel 시트 구성

`Summary` → `Assumptions`(입력) → `Historical`(공시 원천값 + 출처 태그) → `Ratios` → `WACC` → `DCF` → `Sensitivity` → `Relative` → `Checks` → `Audit`(Python vs Excel 대조)

시나리오는 `Assumptions!B30`(1=Base, 2=Bull, 3=Bear)으로 전환합니다.

## 시장 가정 관리

`config/assumptions.yaml`에서 사람이 관리합니다 (AI가 바꾸지 않음): 무위험수익률, ERP, 세율, 베타 산출 기간, 목표주가 가중치, 투자의견 기준, AI 가정 허용범위(가드레일).

## 테스트

```bash
pytest
```

네트워크 없이 합성 데이터로 DART 파싱, 비율, DCF(손계산 대조), WACC, 상대가치, 가드레일, 수치 감사, Excel 교차검증을 검사합니다.

## 한계와 주의

- **금융업**(은행·보험)은 FCFF DCF가 부적합합니다. 보고서에 경고가 표시되며 PBR 중심으로 해석하세요.
- **규칙 기반 모드**(`--no-ai`)는 과거 평균을 기계적으로 연장하므로, 고성장·고평가 기업은 보수적인(낮은) 가치가 나옵니다.
- XBRL/DART 계정 표준화는 기업마다 태그가 달라 일부 계정이 누락될 수 있습니다. `Historical` 시트의 출처 열과 `Checks` 시트를 확인하세요.
- 외국 기업(20-F 제출)은 현재 지원하지 않습니다.
- 자동 생성된 분석 초안이며 투자 권유가 아닙니다.

## 예시

[`examples/AAPL_no-ai/`](examples/AAPL_no-ai/)에 규칙 기반 모드로 생성한 Apple 보고서와 Excel 모델이 있습니다.
