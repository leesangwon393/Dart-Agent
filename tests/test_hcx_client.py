"""HCXClient pacing 회귀 테스트.

2026-08-29 추가: 전체 코퍼스 100문항 재검증(§5-E)에서 CascadingRouter가
HCX escalation 호출을 추가하면서(100건 중 60건) 누적 요청 빈도가 계정
RPM 한도를 건드려 400("Unsupported function")이 재시도로도 못 고쳐지는
케이스가 3건 나왔다. `HCXClient._wait_for_pacing()`이 프로세스 전체에서
공유되는 모듈 레벨 타이머로 최소 호출 간격을 강제하는지 확인한다 —
네트워크는 실제로 안 타고 `requests.post`를 스텁으로 대체한다."""

from __future__ import annotations

import time

import pytest

from disclosure_rag.agent import hcx_client as hcx_client_module
from disclosure_rag.agent.hcx_client import HCXClient


@pytest.fixture(autouse=True)
def _reset_pacing_state():
    """모듈 레벨 `_last_request_at`은 프로세스 전체 공유라, 테스트 간에
    서로 영향을 주지 않도록 매 테스트 전에 초기화한다."""
    hcx_client_module._last_request_at = 0.0
    yield
    hcx_client_module._last_request_at = 0.0


class _FakeResponse:
    status_code = 200

    def json(self):
        return {"status": {"code": "20000"}, "result": {"message": {"content": "ok"}}}


def test_chat_waits_min_interval_between_calls(monkeypatch):
    monkeypatch.setattr(hcx_client_module.requests, "post", lambda *a, **k: _FakeResponse())
    client = HCXClient(api_key="k", model="HCX-005", min_interval_sec=0.2)

    t0 = time.monotonic()
    client.chat([{"role": "user", "content": "1"}])
    client.chat([{"role": "user", "content": "2"}])
    elapsed = time.monotonic() - t0

    assert elapsed >= 0.2, "두 번째 호출이 min_interval_sec 만큼 대기하지 않음"


def test_chat_pacing_shared_across_different_client_instances(monkeypatch):
    """agent_client(HCX-007)/answer_client(HCX-005)처럼 모델이 다른 별도
    인스턴스라도, RPM은 계정(API 키) 단위지 인스턴스 단위가 아니므로 같은
    타이머를 공유해서 서로를 기다려야 한다."""
    monkeypatch.setattr(hcx_client_module.requests, "post", lambda *a, **k: _FakeResponse())
    agent_client = HCXClient(api_key="k", model="HCX-007", min_interval_sec=0.2)
    answer_client = HCXClient(api_key="k", model="HCX-005", min_interval_sec=0.2)

    t0 = time.monotonic()
    agent_client.chat([{"role": "user", "content": "1"}])
    answer_client.chat([{"role": "user", "content": "2"}])
    elapsed = time.monotonic() - t0

    assert elapsed >= 0.2, "서로 다른 HCXClient 인스턴스 사이에 pacing이 공유되지 않음"


def test_chat_pacing_disabled_when_zero(monkeypatch):
    monkeypatch.setattr(hcx_client_module.requests, "post", lambda *a, **k: _FakeResponse())
    client = HCXClient(api_key="k", model="HCX-005", min_interval_sec=0)

    t0 = time.monotonic()
    client.chat([{"role": "user", "content": "1"}])
    client.chat([{"role": "user", "content": "2"}])
    elapsed = time.monotonic() - t0

    assert elapsed < 0.2, "min_interval_sec=0 인데도 대기가 발생함"
