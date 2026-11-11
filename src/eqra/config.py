"""설정 로딩."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.environ.get("EQRA_CONFIG", ROOT / "config" / "assumptions.yaml"))
CACHE_DIR = Path(os.environ.get("EQRA_CACHE", ROOT / "outputs" / ".cache"))

# SEC는 연락처가 포함된 User-Agent를 요구한다. 본인 연락처로 설정 권장.
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "EquityResearchAI research-tool@example.com")
DART_API_KEY = os.environ.get("DART_API_KEY", "")


@lru_cache(maxsize=1)
def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)
