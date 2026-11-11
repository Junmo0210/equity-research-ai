"""HTTP 요청 + 디스크 캐시 (같은 공시를 반복 다운로드하지 않도록)."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Optional

import requests

from ..config import CACHE_DIR

_session = requests.Session()


def _cache_path(url: str, params: Optional[dict], suffix: str) -> Path:
    safe_params = {k: v for k, v in (params or {}).items() if k != "crtfc_key"}  # 키는 캐시 키에서 제외
    key = hashlib.sha256((url + json.dumps(safe_params, sort_keys=True)).encode()).hexdigest()[:24]
    return CACHE_DIR / f"{key}{suffix}"


def get(url: str, *, params: Optional[dict] = None, headers: Optional[dict] = None,
        ttl_hours: float = 24, binary: bool = False, retries: int = 3) -> bytes:
    path = _cache_path(url, params, ".bin")
    if path.exists() and (time.time() - path.stat().st_mtime) < ttl_hours * 3600:
        return path.read_bytes()
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            r = _session.get(url, params=params, headers=headers or {}, timeout=60)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"{r.status_code} for {url}")
            r.raise_for_status()
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
            return r.content
        except requests.RequestException as e:  # 네트워크/일시 오류만 재시도
            resp = getattr(e, "response", None)
            if resp is not None and 400 <= resp.status_code < 500 and resp.status_code != 429:
                raise RuntimeError(f"요청 거부 {resp.status_code}: {url}") from e
            last_err = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"다운로드 실패: {url} ({last_err})")


def get_json(url: str, **kw) -> dict:
    return json.loads(get(url, **kw))
