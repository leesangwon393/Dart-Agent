"""HyperCLOVA X (CLOVA Studio) Chat Completions 저수준 클라이언트 (§57).

Agent 의 reasoning/routing/최종 답변은 반드시 HCX 계열 모델만 쓴다 — 다른 외부
LLM 을 online Agent 용도로 쓰지 않는다 (§57). Semantic Router 는 embedding 기반
컴포넌트라 별도 취급(§37) — 이 클라이언트는 그와 무관하다.

실제 API 로 확인한 사실 (.env 의 실키로 라이브 테스트):
- endpoint: POST https://clovastudio.stream.ntruss.com/v3/chat-completions/{model}
- auth: `Authorization: Bearer {HCX_API_KEY}` (Bearer 방식 키 기준)
- tool calling 은 OpenAI 호환에 가까움 (messages/tools/tool_choice, 응답의
  message.toolCalls[].function.{name,arguments} — arguments 는 이미 dict 로 옴,
  OpenAI 처럼 JSON string 이 아님).
- `tools` 파라미터와 `maxTokens` 를 동시에 주면 400(Invalid parameter) 이 나는
  조합이 실측으로 확인됨 — tool-calling 요청에는 maxTokens 를 생략한다.
- 짧은 시간에 연속 호출하면(질문 1개 안에서도 tool-calling 라운드트립으로 호출이
  누적됨) 이후 요청이 400("Unsupported function")으로 실패하는 게 반복 관측됨 —
  단일 호출은 항상 성공하고 연속 호출 시에만 실패하는 패턴이라 계정/모델 tier 의
  RPM(분당 요청수) rate-limit 로 추정(에러 메시지 자체는 이유를 설명하지 않음).
  지수 백오프 재시도로 대부분 우회되지만, Phase 15+ 실사용 시 요청 빈도를
  스로틀링하거나 tier 상향이 필요할 수 있다.
- **2026-08-29 실측 확인**: 100문항 전체 코퍼스 재검증(§5-E)에서 위 현상이
  처음으로 실제 영향을 줬다 — CascadingRouter 배선(2026-08-25) 이후
  라우팅 자체가 100문항 중 60건에서 HCX escalation(`HCXStructuredRouter.
  route()`)을 추가로 호출하게 됐고(v1의 SemanticRouterAdapter 단독 구성은
  라우팅에 HCX를 아예 안 썼음), 이 추가 호출량이 누적 요청 빈도를 밀어올려
  400("Unsupported function")이 3건에서 재시도(6회, 최대 200초)를 다 써도
  복구가 안 됐다. 트레이스백은 매번 `agent_loop.py`의 `router.route()`
  호출 지점이었다 — 페이로드는 매번 동일한데 재시도로 종종 성공하는 걸로
  보아 라우터 tool description(2026-08-25 추가) 자체는 원인이 아니고
  순수 호출 빈도 문제로 재확인됐다. **해결**: 아래 `min_interval_sec`
  기반 클래스 레벨 pacing 추가 — 이 프로세스 안의 모든 `HCXClient` 인스턴스
  (agent_client/answer_client 등 서로 다른 모델이어도) 사이에 최소 간격을
  강제한다. RPM 은 계정/API 키 단위지 클라이언트 인스턴스 단위가 아니므로
  인스턴스별이 아니라 클래스 변수로 공유해야 실제 계정 레벨 호출 빈도를
  줄일 수 있다.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

_ENDPOINT_TMPL = "https://clovastudio.stream.ntruss.com/v3/chat-completions/{model}"

# RPM(분당 요청수) rate-limit 는 API 키(=계정) 단위지 파이썬 객체 단위가 아니다
# (agent_client/answer_client 처럼 모델이 다른 별도 인스턴스라도 같은 키를 쓰면
# 같은 한도를 공유한다) — 그래서 pacing 상태를 인스턴스 필드가 아니라 클래스
# 변수로 둬서, 이 프로세스 안의 *모든* HCXClient 인스턴스가 하나의 타이머를
# 공유하게 한다. 스레드는 안 쓰지만(전부 순차 호출) 락은 값싸니 방어적으로 둔다.
_pacing_lock = threading.Lock()
_last_request_at: float = 0.0


class HCXError(RuntimeError):
    pass


class HCXClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        env_path: str | Path = ".env",
        timeout: float = 60.0,
        min_interval_sec: float = 1.0,
    ):
        """`min_interval_sec`: 이 값만큼 이전 HCX 호출(이 프로세스의 어떤
        HCXClient 인스턴스가 보낸 것이든) 이후 최소 간격을 보장한다 — 위
        docstring "2026-08-29 실측" 참고. 계정 tier 의 정확한 RPM 을 모르는
        상태에서 고른 보수적 기본값(1초)이라, 이후 실측으로 더 줄이거나
        늘릴 수 있다. 0으로 주면 기존처럼 pacing 없이 동작(테스트/디버깅용)."""
        if api_key is None or model is None:
            self._load_env(env_path)
        self.api_key = api_key or os.environ.get("HCX_API_KEY")
        self.model = model or os.environ.get("HCX_MODEL")
        if not self.api_key:
            raise HCXError("HCX_API_KEY 가 없습니다 (.env 확인)")
        if not self.model:
            raise HCXError("HCX_MODEL 이 없습니다 (.env 확인)")
        self.timeout = timeout
        self.min_interval_sec = min_interval_sec
        self._url = _ENDPOINT_TMPL.format(model=self.model)
        # HCX-007(reasoning 모델)은 thinking 모드 기본 on 상태에서 tools 와 같이
        # 쓰면 400 이 난다(실측, Stage 10) — 모델명으로 감지해 자동으로 꺼준다.
        # 호출부(agent_loop.py 등)를 모델별로 분기시키지 않기 위해 클라이언트
        # 레벨에서 흡수한다.
        self._default_thinking = {"effort": "none"} if "007" in self.model else None
        # HCX-007 은 max token 제한 파라미터 이름이 다른 모델과 다르다(실측,
        # Stage 12): "maxTokens"를 주면 400("Invalid parameter: maxTokens")이
        # 나고, 대신 "maxCompletionTokens"를 써야 정상 동작한다. 원인 불명
        # (reasoning 모델이라 내부적으로 토큰 예산을 다르게 다루는 것으로 추정)
        # — 호출부 무수정으로 흡수하기 위해 여기서 모델별로 파라미터 이름을 정한다.
        self._max_tokens_param = "maxCompletionTokens" if "007" in self.model else "maxTokens"

    def _load_env(self, env_path: str | Path) -> None:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=str(env_path))

    def _wait_for_pacing(self) -> None:
        """모듈 레벨 `_last_request_at`(클래스 인스턴스가 아니라 프로세스
        전체 공유)을 기준으로 `min_interval_sec` 이상 간격을 강제한다.
        `min_interval_sec=0`이면(테스트 등) 즉시 통과한다."""
        if self.min_interval_sec <= 0:
            return
        global _last_request_at
        with _pacing_lock:
            now = time.monotonic()
            wait = self.min_interval_sec - (now - _last_request_at)
            if wait > 0:
                time.sleep(wait)
            _last_request_at = time.monotonic()

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
        temperature: float = 0.3,
        top_p: float = 0.8,
        max_tokens: int | None = None,
        max_retries: int = 3,
        thinking: dict | None = None,
    ) -> dict:
        """단일 chat completion 호출. 원본 응답의 result.message 를 그대로 반환한다.

        실측: 드물게 동일 요청이 일시적으로 400("Unsupported function")을 내다가
        재시도하면 바로 성공하는 케이스가 관측됨(원인 불명, API 측 일시 오류로 추정)
        — 짧게 재시도한다.

        `thinking`: HCX-007(reasoning 모델)은 기본적으로 thinking 모드가 켜져
        있는데, 이 상태에서 `tools`를 같이 주면 400("Invalid parameter: tools,
        thinking")이 난다(실측, Stage 10). `thinking={"effort": "none"}`을
        명시하면 해결된다. HCX-DASH-002/HCX-005는 이 파라미터가 필요 없다
        (안 줘도 tool-calling 정상 동작) — 모델별로 호출부에서 필요할 때만 전달.

        `max_tokens`: HCX-007 은 파라미터 이름 자체가 다르다("maxTokens"를
        주면 400, "maxCompletionTokens"라고 줘야 함 — 실측, Stage 12).
        `self._max_tokens_param`(모델별 자동 결정)을 키로 써서 흡수한다."""
        payload: dict[str, Any] = {"messages": messages, "topP": top_p, "temperature": temperature}
        if thinking is None:
            thinking = self._default_thinking
        if thinking is not None:
            payload["thinking"] = thinking
        if tools:
            payload["tools"] = tools
            if tool_choice is not None:
                payload["toolChoice"] = tool_choice
            # 실측: tools + maxTokens 동시 사용 시 400 (§ 위 docstring)
        elif max_tokens is not None:
            payload[self._max_tokens_param] = max_tokens

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            self._wait_for_pacing()
            resp = requests.post(self._url, headers=headers, json=payload, timeout=self.timeout)
            if resp.status_code == 200:
                body = resp.json()
                if body.get("status", {}).get("code") == "20000":
                    return body["result"]["message"]
                last_error = HCXError(f"HCX API 오류: {body.get('status')}")
            else:
                last_error = HCXError(f"HCX API 오류 {resp.status_code}: {resp.text[:500]}")
            if attempt < max_retries:
                wait = 3.0 * (2 ** attempt)  # 3s, 6s, 12s, 24s ... (짧은 rate-limit 윈도우를 넘기기 위한 지수 백오프)
                logger.warning("[HCX] chat 호출 실패(시도 %d/%d), %.1fs 후 재시도: %s", attempt + 1, max_retries + 1, wait, last_error)
                time.sleep(wait)
        raise last_error

    def chat_simple(self, prompt: str, *, system: str | None = None, max_tokens: int = 512, temperature: float = 0.3) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        result = self.chat(messages, max_tokens=max_tokens, temperature=temperature)
        return result.get("content", "")
