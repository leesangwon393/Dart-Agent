"""`new/app/hybrid/*`가 `disclosure_rag.agent.hcx_client.HCXClient`(인프라성
클라이언트, 작업 지시사항이 명시적으로 재사용을 허용)를 import 할 수 있도록
`src/`를 sys.path 에 추가한다.

`new/app/**`의 다른 서브패키지(company/query/routing)는 절대 `src/`를 import
하지 않는다 — 이 파일은 `hybrid/` 패키지에서만 쓰기 위한 예외 경로다. 이미
import 돼 있으면 아무 것도 하지 않는다(idempotent)."""

from __future__ import annotations

import sys
from pathlib import Path

_THIS_FILE = Path(__file__).resolve()
# new/app/hybrid/_legacy_client_path.py -> parents[3] == repo root
_REPO_ROOT = _THIS_FILE.parents[3]
_SRC_PATH = str(_REPO_ROOT / "src")

if _SRC_PATH not in sys.path:
    sys.path.insert(0, _SRC_PATH)
