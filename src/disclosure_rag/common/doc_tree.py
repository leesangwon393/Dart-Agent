"""Parser 의 공통 중간 표현 (intermediate document tree).

4종 공시 모두 서로 다른 원본 포맷(DART XML / 위장 HTML)에서 출발하지만,
Parser 는 전부 이 동일한 트리 구조로 수렴한다. Chunker(Phase 5)는 이 트리만
바라보고 동작하므로 원본 포맷을 몰라도 된다 (§22 Parser 계층 설계).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Union


@dataclass
class TableCell:
    text: str
    is_header: bool = False
    field_code: str | None = None   # DART TE[ACODE] — 구조화 추출 필드 코드
    unit_code: str | None = None    # DART TU[AUNIT]
    unit_value: str | None = None   # DART TU[AUNITVALUE] (예: 기간/날짜의 raw 값)


@dataclass
class TableNode:
    """rowspan/colspan 이 이미 확장된(normalize 된) 그리드. 큰 재무제표류 표에 사용."""

    kind: Literal["table"] = field(default="table", init=False)
    rows: list[list[TableCell]] = field(default_factory=list)
    title_hint: str | None = None  # 표 직전 문단(§21 Table Title) — split 시 반복 삽입용
    unit_hint: str | None = None   # "단위: 백만원" 류, 표 안/직전에서 발견되면 채움
    acode_group: str | None = None  # TABLE-GROUP[ACLASS] (예: TBL_ACQ_STK) — 존재 시


@dataclass
class KeyValueNode:
    """작은 2~3열 표. Column-Value 관계를 flatten 하지 않고 (key, value) 쌍으로 보존 (§20)."""

    kind: Literal["key_value"] = field(default="key_value", init=False)
    group_label: str | None = None  # rowspan 으로 묶인 상위 라벨 (예: "2. 계약내역")
    pairs: list[tuple[str, str, str | None, str | None]] = field(default_factory=list)
    # (key, value, field_code, unit_code)
    acode_group: str | None = None


@dataclass
class TextNode:
    kind: Literal["text"] = field(default="text", init=False)
    text: str = ""


ContentNode = Union[TableNode, KeyValueNode, TextNode]


@dataclass
class SectionNode:
    title: str
    level: int  # 1-based (SECTION-1 -> 1, SECTION-2 -> 2, ...)
    path: list[str]  # root 부터 이 섹션까지의 title 경로 (section_path)
    children: list[Union["SectionNode", ContentNode]] = field(default_factory=list)


@dataclass
class ParsedDocument:
    """Parser 의 최종 산출물. report_* 메타는 manifest 에서 넘어온다."""

    doc_id: str                 # manifest doc_id (예: periodic_20240312000736)
    doc_group: str               # periodic | major | exchange | holding
    doc_subtype: str | None
    report_subtype: str          # "main" | "separate_audit_report" | "consolidated_audit_report" | ...
    source_path: str             # 실제 파일의 corpus_root 상대 경로
    document_name: str | None    # DOCUMENT-NAME / HTML <title> 텍스트
    sections: list[SectionNode] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)
