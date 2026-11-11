"""사용법: python -m eqra "Apple"  |  python -m eqra 삼성전자  |  python -m eqra NVDA --peers AMD AVGO"""
from __future__ import annotations

import argparse
import sys

from .pipeline import run


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="eqra", description="기업명 → 공시 수집·분석·밸류에이션·투자분석 보고서")
    p.add_argument("company", help="기업명, 티커(AAPL) 또는 종목코드(005930)")
    p.add_argument("--market", choices=["US", "KR"], help="시장 지정 (기본: 자동 판별 — 한글/6자리 숫자는 KR)")
    p.add_argument("--no-ai", action="store_true", help="AI 없이 규칙 기반 가정·템플릿 보고서로 실행")
    p.add_argument("--peers", nargs="*", default=[], help="비교기업 직접 지정 (AI 제안에 추가)")
    p.add_argument("--out", help="출력 폴더 (기본: outputs/)")
    a = p.parse_args(argv)
    try:
        from pathlib import Path
        res = run(a.company, a.market, use_ai=False if a.no_ai else None, peers=a.peers,
                  out_root=Path(a.out) if a.out else None)
    except (LookupError, RuntimeError) as e:
        print(f"\n오류: {e}", file=sys.stderr)
        return 1
    return 0 if res["verification"]["status"] in ("PASS", "SKIPPED") else 2


if __name__ == "__main__":
    sys.exit(main())
