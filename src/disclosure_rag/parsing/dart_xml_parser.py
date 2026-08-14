"""DART DOCUMENT/SECTION-N XML 공통 Parser.

periodic / major / holding 3종은 실측 결과(Phase 0 §3,4,6) 전부 동일한 DART
`<DOCUMENT><SECTION-1>...<SECTION-N>` 스키마를 공유한다 (SECTION 깊이만 문서마다
1~4단으로 다르다). 따라서 §22 의 "반드시 클래스 4개를 새로 만들 필요는 없다"는
원칙에 따라 이 3종은 하나의 Parser 함수로 처리하고, doc_group 은 태깅용으로만
넘긴다. exchange 만 실제로 다른 포맷(위장 HTML)이라 별도 parser(exchange_parser.py)
를 둔다.

TABLE / TABLE-GROUP 안의 TE[ACODE], TU[AUNIT/AUNITVALUE] 코드는 문서 유형에
상관없이 어디서든 나타날 수 있으므로 (§10, §14, §19 실측 공통) 이 모듈에서
공통으로 보존한다.
"""

from __future__ import annotations

import logging

from lxml import etree

from disclosure_rag.common.doc_tree import ParsedDocument, SectionNode, TextNode
from disclosure_rag.parsing.table_parser import RawCell, classify_grid, expand_grid

logger = logging.getLogger(__name__)

_SKIP_TAGS = {"PGBRK", "COVER", "SUMMARY", "FORMULA-VERSION"}

# 회귀 발견: 실제 코퍼스 중 한 건(현대자동차 사업보고서, 정관 5호 의안 표)에서
# lxml 의 malformed-XML recover 모드가 문서 전체 TR(12,184개) 중 11,786개를
# 엉뚱하게 표 하나에 몰아넣어 73,000자짜리 chunk 가 나온 사례를 실측으로 확인.
# 근본 원인(원본 XML 의 어떤 malformation 이 recover 파서를 오동작시켰는지)은
# 문서별로 다를 수 있어 완전히 고치기 어렵다 — 대신 방어적으로 표 크기를 캡핑해
# 어떤 malformed 입력이 와도 폭주하지 않게 한다. 원본 표가 실제로 이렇게 큰
# 경우는 실무상 없다고 보고, 캡을 넘기면 WARNING 을 남기고 앞부분만 취한다
# (완전 생략하면 §7 silent failure 이므로 일부라도 보존).
_MAX_TABLE_ROWS = 500
# 주의: LIBRARY 는 skip 하지 않는다 — 실측 결과(holding 문서) SECTION-1 이
# LIBRARY 안에 감싸져 있는 경우가 있다. skip 하면 본문 전체가 silent 하게
# 유실된다 (§7 금지 사항). 알 수 없는 wrapper 로 취급해 그냥 재귀 통과시킨다.
_TEXT_TAGS = {"P"}
_IMAGE_TAGS = {"IMAGE", "IMG"}


def _text_of(el) -> str:
    return "".join(el.itertext()).strip()


def _title_of(section_el) -> str:
    title_el = section_el.find("TITLE")
    if title_el is not None:
        return _text_of(title_el)
    return ""


def _cell_to_raw(cell_el) -> RawCell:
    tag = cell_el.tag
    return RawCell(
        text=_text_of(cell_el),
        rowspan=int(cell_el.get("ROWSPAN", "1") or "1"),
        colspan=int(cell_el.get("COLSPAN", "1") or "1"),
        is_header=(tag == "TH"),
        field_code=cell_el.get("ACODE") if tag == "TE" else None,
        unit_code=cell_el.get("AUNIT") if tag == "TU" else None,
        unit_value=cell_el.get("AUNITVALUE") if tag == "TU" else None,
    )


def _parse_table_el(table_el, *, title_hint: str | None, acode_group: str | None):
    raw_rows: list[list[RawCell]] = []
    for tr in table_el.iter("TR"):
        cells = [c for c in tr if c.tag in ("TH", "TD", "TE", "TU")]
        if cells:
            raw_rows.append([_cell_to_raw(c) for c in cells])
    if not raw_rows:
        return []
    if len(raw_rows) > _MAX_TABLE_ROWS:
        logger.warning(
            "[TABLE_PARSER] 표 1개의 TR 수(%d)가 비정상적으로 많음(cap=%d) — "
            "malformed XML 로 인한 lxml recover 오동작 의심. 앞부분만 사용하고 나머지 버림. "
            "title_hint=%r",
            len(raw_rows), _MAX_TABLE_ROWS, title_hint,
        )
        raw_rows = raw_rows[:_MAX_TABLE_ROWS]
    grid = expand_grid(raw_rows)
    return classify_grid(grid, title_hint=title_hint, acode_group=acode_group)


def _contains_section(el) -> bool:
    return any(d.tag.startswith("SECTION-") for d in el.iter())


def _walk(el, section: SectionNode, warnings: list[str]) -> None:
    last_text: str | None = None
    for child in el:
        tag = child.tag
        if tag == "TITLE":
            continue
        if tag in _TEXT_TAGS and _contains_section(child):
            # 회귀 발견: 극히 드물게(실측 4,204건 중 2건) 정정신고 wrapper 안에서
            # <P> 안에 SECTION-N 이 잘못 중첩된 malformed XML 이 있다. 그대로
            # 텍스트로 flatten 하면 SECTION 구조 전체가 유실되므로, 이 경우엔
            # 구조적으로 재귀한다 (P 자신의 짧은 서두 텍스트는 희생하더라도
            # SECTION 구조를 살리는 쪽을 택함).
            _walk(child, section, warnings)
            last_text = None
            continue
        if tag.startswith("SECTION-"):
            try:
                level = int(tag.rsplit("-", 1)[1])
            except ValueError:
                level = section.level + 1
            title = _title_of(child)
            child_section = SectionNode(title=title, level=level, path=section.path + [title])
            section.children.append(child_section)
            _walk(child, child_section, warnings)
            last_text = None
        elif tag in _TEXT_TAGS:
            text = _text_of(child)
            if text:
                section.children.append(TextNode(text=text))
                last_text = text
        elif tag == "TABLE":
            nodes = _parse_table_el(child, title_hint=last_text, acode_group=None)
            section.children.extend(nodes)
            last_text = None
        elif tag == "TABLE-GROUP":
            acode_group = child.get("ACLASS")
            for table_el in child.findall("TABLE"):
                nodes = _parse_table_el(table_el, title_hint=last_text, acode_group=acode_group)
                section.children.extend(nodes)
            last_text = None
        elif tag in _IMAGE_TAGS:
            caption_el = child.find("IMG-CAPTION")
            caption = _text_of(caption_el) if caption_el is not None else ""
            section.children.append(TextNode(text=f"[이미지{': ' + caption if caption else ''} — 원문 참조]"))
        elif tag in _SKIP_TAGS:
            continue
        else:
            # 알 수 없는 wrapper 태그 -> silent 하게 내용을 버리지 않고 통과시켜 재귀
            _walk(child, section, warnings)


def parse_dart_xml(
    file_bytes: bytes,
    *,
    doc_id: str,
    doc_group: str,
    doc_subtype: str | None,
    report_subtype: str,
    source_path: str,
) -> ParsedDocument:
    parser = etree.XMLParser(recover=True, huge_tree=True)
    root = etree.fromstring(file_bytes, parser=parser)

    warnings: list[str] = []
    if root is None:
        warnings.append("XML parse 완전 실패 (root None)")
        return ParsedDocument(
            doc_id=doc_id, doc_group=doc_group, doc_subtype=doc_subtype,
            report_subtype=report_subtype, source_path=source_path,
            document_name=None, sections=[], parse_warnings=warnings,
        )

    doc_name_el = root.find("DOCUMENT-NAME")
    document_name = _text_of(doc_name_el) if doc_name_el is not None else None

    body = root.find("BODY")
    walk_root = body if body is not None else root

    doc = ParsedDocument(
        doc_id=doc_id, doc_group=doc_group, doc_subtype=doc_subtype,
        report_subtype=report_subtype, source_path=source_path,
        document_name=document_name, sections=[], parse_warnings=warnings,
    )

    # walk_root 바로 아래에 SECTION-1 들이 온다. 가짜 root SectionNode 를 만들어
    # _walk 를 재사용하고, 그 children 을 doc.sections 로 옮긴다.
    fake_root = SectionNode(title="__ROOT__", level=0, path=[])
    _walk(walk_root, fake_root, warnings)
    doc.sections = [c for c in fake_root.children if isinstance(c, SectionNode)]

    # 회귀 발견: 어떤 SECTION-N 에도 속하지 않는 "loose" 최상위 content(TextNode 등)
    # 가 존재할 수 있다 — 예전엔 SectionNode 만 골라내며 이걸 조용히 버렸다(§7 금지).
    # synthetic section 으로 감싸서 보존한다.
    loose_content = [c for c in fake_root.children if not isinstance(c, SectionNode)]
    if loose_content:
        synthetic_title = document_name or "(section 없는 본문)"
        doc.sections.append(SectionNode(title=synthetic_title, level=1, path=[synthetic_title], children=loose_content))
        warnings.append(f"SECTION 밖의 최상위 content {len(loose_content)}개를 synthetic section 으로 보존함 (doc_id={doc_id})")

    if not doc.sections:
        warnings.append(f"SECTION 없음 — 문서 구조 확인 필요 (doc_id={doc_id})")

    return doc
