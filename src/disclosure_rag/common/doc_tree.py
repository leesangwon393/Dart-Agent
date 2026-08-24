"""Parser 의 공통 중간 표현 (intermediate document tree).

4종 공시 모두 서로 다른 원본 포맷(DART XML / 위장 HTML)에서 출발하지만,
Parser 는 전부 이 동일한 트리 구조로 수렴한다. Chunker(Phase 5)는 이 트리만
바라보고 동작하므로 원본 포맷을 몰라도 된다 (§22 Parser 계층 설계).

=== Kim 브랜치 감사 결과 병합 (2026-08-25) ===
1. TableCell 에 grid 좌표(row/col)와 중복 표시(dup_left/dup_up)를 추가했다.
   기존 코드는 rowspan/colspan 을 펼친 뒤 RLE 로 **다시 축약해서** 저장했기 때문에
   본문 행의 53.1%가 헤더와 열 수가 달랐다(Kim 실측). 이제 TableNode.rows 는
   **정규 그리드(모든 행의 열 수가 동일)** 를 유지하고, 반복된 셀은 dup_* 플래그로
   표시해 렌더링에서만 빈칸으로 낸다 -> 열 정렬 보존 + 텍스트 중복 방지.
2. KeyValueNode.pairs 를 4-tuple 에서 KVPair(NamedTuple, 5필드)로 바꿔
   AUNITVALUE(unit_value) 를 보존한다. 기존에는 100% 폐기되고 있었다.
3. TextNode.from_table_row: 표의 1열 전폭 행을 폐기하지 않고 보존하기 위한 플래그
   (Kim 실측 폐기율: periodic 18.8%/holding 58.1%/major 17.6%/exchange 13.7%).
4. TableNode.period_hint: "제 41 기 1분기말 2024.03.31 현재" 류 기준일 표기.

=== 우리 어젯밤 변경분(b112925, 2026-08-24) 은 그대로 유지 ===
5. TableCell.indent/origin_id: semantic block 경계 검출(table_parser.
   detect_semantic_blocks)을 위해 셀 텍스트의 들여쓰기 폭과, rowspan/colspan
   확장 전 "원본 셀" 식별자를 보존한다. Kim 은 이 식별자를 `_GridCell.origin_id`
   래퍼로 따로 관리했지만, 개념은 완전히 동일하다(rowspan 으로 반복된 같은 원본
   셀이면 값이 같다) — 여기서는 래퍼 없이 TableCell 자체 필드로 흡수해 하나로
   통합한다.
6. TableNode.table_id: 이 표(원본 하나)를 식별하는 id. 나중에 이 표가 여러
   chunk 로 쪼개져도 전부 같은 table_id 를 공유해 sibling expansion 에 쓴다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal, NamedTuple, Union


@dataclass
class TableCell:
    text: str
    is_header: bool = False
    field_code: str | None = None   # DART TE[ACODE] — 구조화 추출 필드 코드
    unit_code: str | None = None    # DART TU[AUNIT]
    unit_value: str | None = None   # DART TU[AUNITVALUE] (기계판독 정규화 값)
    # --- grid 상의 위치. 정규 그리드이므로 (row, col) 이 곧 헤더와의 대응 관계다 ---
    row: int = -1
    col: int = -1
    # 같은 원본 셀이 colspan/rowspan 으로 이 칸에 복제된 것인지.
    # True 면 렌더링 시 빈칸으로 낸다(열 수는 유지, 텍스트만 생략).
    dup_left: bool = False   # 바로 왼쪽 칸과 같은 원본 셀
    dup_up: bool = False     # 바로 위 칸과 같은 원본 셀
    # 아래 두 필드는 semantic block 경계 검출(table_parser.detect_semantic_blocks)
    # 을 위해 추가됐다 — 원본 셀 텍스트의 들여쓰기 폭과, rowspan/colspan 확장 전
    # "원본 셀" 식별자(같은 origin_id 가 여러 grid 칸에 반복되면 rowspan 로 묶인
    # 같은 셀이라는 뜻)를 보존한다.
    indent: int = 0
    origin_id: int = -1


class KVPair(NamedTuple):
    """key-value 표의 한 행. tuple 로도 동작하지만 이름으로 접근하는 것을 권장한다."""

    key: str
    value: str
    field_code: str | None = None
    unit_code: str | None = None
    unit_value: str | None = None


@dataclass
class TableNode:
    """rowspan/colspan 이 확장된 **정규 그리드**. 모든 행의 열 수가 같다."""

    kind: Literal["table"] = field(default="table", init=False)
    rows: list[list[TableCell]] = field(default_factory=list)
    title_hint: str | None = None  # 표 직전 문단(§21 Table Title) — split 시 반복 삽입용
    unit_hint: str | None = None   # "단위: 백만원" 류, 표 안/직전에서 발견되면 채움
    period_hint: str | None = None  # "제 41 기 1분기말 2024.03.31 현재" 류
    acode_group: str | None = None  # TABLE-GROUP[ACLASS] (예: TBL_ACQ_STK) — 존재 시
    # 이 표(원본 하나) 를 식별하는 id. 나중에 이 표가 여러 chunk 로 쪼개져도
    # 전부 같은 table_id 를 공유해 sibling expansion(§Phase5)에 쓸 수 있게 한다.
    # 재현성(빌드마다 동일 id)은 요구되지 않고, 한 빌드 내에서만 유일하면 된다.
    table_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def n_cols(self) -> int:
        return len(self.rows[0]) if self.rows else 0


@dataclass
class KeyValueNode:
    """작은 2~3열 표. Column-Value 관계를 flatten 하지 않고 (key, value) 쌍으로 보존 (§20)."""

    kind: Literal["key_value"] = field(default="key_value", init=False)
    group_label: str | None = None  # rowspan 으로 묶인 상위 라벨 (예: "2. 계약내역")
    pairs: list[KVPair] = field(default_factory=list)
    acode_group: str | None = None


@dataclass
class TextNode:
    kind: Literal["text"] = field(default="text", init=False)
    text: str = ""
    # 표 안의 전폭(1열) 행에서 온 텍스트인지. DART/KRX 서식에서 이 자리는
    # "기타 투자판단과 관련한 중요사항" 같은 서술형 본문이 들어간다.
    # 기존 코드는 이걸 로그 없이 버렸다(holding 58.1%, periodic 18.8% 실측).
    from_table_row: bool = False


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
    report_subtype: str          # "main" | "separate_audit_report" | ...
    source_path: str             # 실제 파일의 corpus_root 상대 경로
    document_name: str | None    # DOCUMENT-NAME / HTML <title> 텍스트
    sections: list[SectionNode] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)
