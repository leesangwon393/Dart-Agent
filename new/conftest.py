"""pytest가 `new/` 디렉터리를 rootdir 로 잡을 때 `app` 패키지를 import할 수
있도록 sys.path 에 `new/`를 추가한다. `new/`는 아직 pip install -e 로 설치된
패키지가 아니므로 이 방식이 가장 단순하다."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
