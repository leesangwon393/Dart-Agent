"""SPEC.md §30 core/logging.py. 최소한의 표준 logging 설정 — 이번 Phase는
라우팅/엔티티 해석 계층뿐이라 별도 구조화 로깅 인프라는 과설계이므로,
표준 라이브러리 logging 을 얇게 감싸는 정도로만 둔다."""

from __future__ import annotations

import logging


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers and not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    return logger
