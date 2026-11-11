"""Claude API 래퍼: 구조화 출력(JSON 스키마) + 스트리밍 + 거절 시 서버측 fallback + 감사 로그."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional, Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def ai_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


class ClaudeClient:
    def __init__(self, model: str, effort: str, log_dir: Path):
        import anthropic  # 지연 import: AI 미사용 모드에서는 SDK 없어도 동작

        self.anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.usage: list[dict] = []

    def structured(self, step: str, system: str, user_blocks: list[dict], schema: Type[T],
                   max_tokens: int = 64000) -> T:
        """스키마에 맞는 JSON을 받아 pydantic 객체로 반환. 모든 요청·응답은 ai_log/에 저장."""
        t0 = time.time()
        try:
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",                   # 정책 거절 시 서버가 대체 모델로 재시도
                system=system,
                messages=[{"role": "user", "content": user_blocks}],
                output_config={"effort": self.effort},
                output_format=schema,
                cache_control={"type": "ephemeral"},
            ) as stream:
                msg = stream.get_final_message()
        except self.anthropic.AuthenticationError as e:
            raise RuntimeError("Anthropic API 키가 유효하지 않습니다 (ANTHROPIC_API_KEY 확인).") from e
        except self.anthropic.RateLimitError as e:
            raise RuntimeError("Anthropic API 사용량 한도 초과 — 잠시 후 다시 실행하세요.") from e
        except self.anthropic.APIStatusError as e:
            raise RuntimeError(f"Anthropic API 오류 {e.status_code}: {e.message}") from e
        except self.anthropic.APIConnectionError as e:
            raise RuntimeError("Anthropic API 연결 실패 — 네트워크를 확인하세요.") from e

        if msg.stop_reason == "refusal":
            raise RuntimeError(f"[{step}] 모델이 요청을 거절했습니다: {getattr(msg, 'stop_details', None)}")
        if msg.stop_reason == "max_tokens":
            raise RuntimeError(f"[{step}] 응답이 max_tokens에서 잘렸습니다.")

        parsed: Optional[T] = getattr(msg, "parsed_output", None)
        text = next((b.text for b in msg.content if b.type == "text"), "")
        if parsed is None:
            parsed = schema.model_validate_json(text)

        u = msg.usage
        rec = {"step": step, "model": msg.model, "seconds": round(time.time() - t0, 1),
               "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
               "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
               "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
               "request_id": getattr(msg, "_request_id", None)}
        self.usage.append(rec)
        (self.log_dir / f"{step}.json").write_text(json.dumps({
            "meta": rec, "system": system,
            "user": [b.get("text", "") for b in user_blocks],
            "response": json.loads(parsed.model_dump_json()),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return parsed
