"""LibreOffice로 Excel 수식을 실제 재계산한 뒤, Audit 시트의 Python 값과 비교."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from openpyxl import load_workbook


def find_soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    for p in ("/Applications/LibreOffice.app/Contents/MacOS/soffice",
              r"C:\Program Files\LibreOffice\program\soffice.exe"):
        if Path(p).exists():
            return p
    return None


def recalc(path: Path, out_dir: Path) -> Path | None:
    """LibreOffice로 수식을 독립 계산한 사본을 out_dir에 만든다 (원본 파일은 그대로)."""
    soffice = find_soffice()
    if not soffice:
        return None
    profile = out_dir / "lo_profile"
    cmd = [soffice, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--calc",
           "--convert-to", "xlsx:Calc MS Excel 2007 XML", "--outdir", str(out_dir), str(path)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=180)
    out = out_dir / path.name
    return out if out.exists() else None


def verify(path: Path, audit_cells: list) -> dict:
    with tempfile.TemporaryDirectory() as td:
        calc = recalc(path, Path(td))
        if calc is None:
            return {"status": "SKIPPED", "reason": "LibreOffice가 없어 Excel 재계산 검증을 생략했습니다.", "rows": []}
        wb = load_workbook(calc, data_only=True)
        return _compare(wb, audit_cells)


def _compare(wb, audit_cells: list) -> dict:
    ws = wb["Audit"]
    rows, ok = [], True
    for label, pv, ref, r in audit_cells:
        xv = ws.cell(r, 3).value
        if pv is None and xv in (None, ""):
            status, err = "OK", 0.0
        elif isinstance(pv, (int, float)) and isinstance(xv, (int, float)):
            err = abs(xv - pv) / max(abs(pv), 1e-9)
            status = "OK" if err < 1e-6 else "MISMATCH"
        else:
            status, err = "MISMATCH", None
        ok &= status == "OK"
        rows.append({"item": label, "python": pv, "excel": xv, "rel_error": err, "status": status, "cell": ref})
    errors = []
    for sh in wb.worksheets:
        for row in sh.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("#") and c.value.rstrip("!?0/").upper() in (
                        "#DIV", "#VALUE", "#REF", "#NAME", "#N/A", "#NUM"):
                    errors.append(f"{sh.title}!{c.coordinate}={c.value}")
    return {"status": "PASS" if ok and not errors else "FAIL", "rows": rows, "excel_errors": errors[:50]}
