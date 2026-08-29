"""SPEC.md §30 core/config.py.

이 프로젝트(공시 agent)의 corpus/universe.csv 는 이미 git repo 루트의
`corpus/`에 존재한다(작업 지시사항 "배경 확인된 사실" 참고) — `new/`는 별도
데이터 준비 없이 이 파일을 그대로 읽는다.

경로는 이 파일 위치를 기준으로 역산한다: new/app/core/config.py 에서
parents[3] 이 repo 루트(공시 agent/)다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_THIS_FILE = Path(__file__).resolve()
REPO_ROOT = _THIS_FILE.parents[3]


@dataclass(frozen=True)
class AppConfig:
    universe_csv_path: Path = REPO_ROOT / "corpus" / "universe.csv"

    def validate(self) -> None:
        if not self.universe_csv_path.is_file():
            raise FileNotFoundError(
                f"Company Master CSV 를 찾을 수 없습니다: {self.universe_csv_path}"
            )


DEFAULT_CONFIG = AppConfig()
