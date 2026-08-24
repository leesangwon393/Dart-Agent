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

=== Kim 브랜치 감사 결과 병합 (2026-08-25) ===
원본 XML 사전정리를 `_escape_bare_special_chars`(bare &/< 만 처리)에서
`xml_sanitizer.sanitize_dart_xml`(bare &, bare <, **속성값 안 여분 따옴표** 3종
전부 처리)로 교체했다. 배경과 실측(BODY 직속 SECTION-1 개수 회복 등)은
`xml_sanitizer.py` docstring 참고. 핵심 교훈: bare `<` 만 고치고 속성값 따옴표는
안 고치면 **오히려 본문이 줄어든다** — bare `<` 가 만드는 유령 element 가
스택을 깊게 쌓아 속성값 파싱 실패의 연쇄 tag mismatch 가 DOCUMENT 까지 pop
되는 걸 완충해주고 있었기 때문이다(현대차 813천자→422천자, KB금융
1,634천자→814천자로 실측 확인됨). 두 malformation 을 반드시 함께 고쳐야 한다.

`_cell_to_raw`는 Kim 브랜치를 따라 ACODE/AUNIT/AUNITVALUE 를 태그(TE/TU)와
무관하게 읽는다 — 기존에는 TE 에서만 ACODE, TU 에서만 AUNIT 을 읽어 TD/TH 에
붙은 같은 속성이 그냥 버려졌다. `_parse_table_el`이 TABLE(-GROUP) 뒤에도
`last_text`를 지우지 않는 것도 Kim 브랜치를 따른다 — 소제목 하나 아래 표가
여러 개 이어지는 것이 DART 서식의 일반형이라, 지우면 두 번째 표부터
title_hint 를 잃는다(실측: 표의 31.1%만 title_hint 보유).
"""

from __future__ import annotations

import logging

from lxml import etree

from disclosure_rag.common.doc_tree import ParsedDocument, SectionNode, TextNode
from disclosure_rag.parsing.table_parser import RawCell, classify_grid, expand_grid
from disclosure_rag.parsing.xml_sanitizer import sanitize_dart_xml

logger = logging.getLogger(__name__)

_SKIP_TAGS = {"PGBRK", "COVER", "SUMMARY", "FORMULA-VERSION"}

# 회귀 발견: 실제 코퍼스 중 한 건(현대자동차 사업보고서, 정관 5호 의안 표)에서
# lxml 의 malformed-XML recover 모드가 문서 전체 TR(12,184개) 중 11,786개를
# 엉뚱하게 표 하나에 몰아넣어 73,000자짜리 chunk 가 나온 사례를 실측으로 확인.
#
# 2026-08-16 갱신(Kim 실측): 이 증상의 근본 원인이 밝혀졌다 — 본문에 이스케이프
# 없이 쓰인 `<`(예: `<신  설>`)가 만든 유령 element 였다. xml_sanitizer 도입 후
# 같은 문서의 최대 표 크기는 11,786행 -> 1,491행으로 떨어진다(메리츠금융지주는
# 8,013 -> 1,633). 즉 cap 이 상시 발동하던 이유는 해소됐다.
# 그래도 **cap 자체는 안전장치로 유지한다** — sanitizer 가 못 잡는 malformation
# 이 남아 있을 수 있고, cap 은 그런 입력에서도 chunk 폭주를 막아준다.
# 캡을 넘기면 WARNING 을 남기고 앞부분만 취한다 (완전 생략하면 §7 silent
# failure 이므로 일부라도 보존).
# 500 -> 3000: 이 cap 은 malformed XML 로 lxml recover 가 폭주할 때의 안전장치로
# 도입됐는데(TR 11,786행), xml_sanitizer 가 근본 원인을 고친 뒤 같은 문서의
# 실제 최대는 1,491행이다. 500 은 정상 표를 33%만 남기고 자른다.
_MAX_TABLE_ROWS = 3000
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


def _cell_indent(cell_el) -> int:
    """셀 텍스트의 leading whitespace 폭 (semantic block 들여쓰기 판단용).

    _text_of() 는 이미 strip() 을 하므로 들여쓰기가 소실된다 — 여기서는 별도로
    strip 하지 않은 원본 텍스트에서 leading space 길이만 잰다. 실측(SK하이닉스
    사업보고서 20260317000635.xml)으로 원본 XML 이 "    연결조정"처럼 일반
    ASCII 공백 4칸을 들여쓰기로 그대로 쓰는 것을 확인했다."""
    raw_text = "".join(cell_el.itertext())
    return len(raw_text) - len(raw_text.lstrip(" 　"))


def _cell_to_raw(cell_el) -> RawCell:
    """Kim 브랜치: ACODE/AUNIT/AUNITVALUE 를 태그(TE/TU)와 무관하게 읽는다.
    기존에는 TE 에서만 ACODE, TU 에서만 AUNIT 을 읽어 TD/TH 에 붙은 같은 속성이
    그냥 버려졌다. 없으면 None 이므로 더 읽어서 손해 볼 일은 없다."""
    tag = cell_el.tag
    return RawCell(
        text=_text_of(cell_el),
        rowspan=int(cell_el.get("ROWSPAN", "1") or "1"),
        colspan=int(cell_el.get("COLSPAN", "1") or "1"),
        is_header=(tag == "TH"),
        field_code=cell_el.get("ACODE"),
        unit_code=cell_el.get("AUNIT"),
        unit_value=cell_el.get("AUNITVALUE"),
        indent=_cell_indent(cell_el),
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
            # Kim 브랜치: 표 뒤에 last_text 를 지우지 않는다. 하나의 소제목 아래
            # 표가 여러 개 이어지는 것이 DART 서식의 일반형이라, 지우면 두 번째
            # 표부터 제목을 잃는다(실측: 표의 31.1%만 title_hint 보유).
            nodes = _parse_table_el(child, title_hint=last_text, acode_group=None)
            section.children.extend(nodes)
        elif tag == "TABLE-GROUP":
            acode_group = child.get("ACLASS")
            for table_el in child.findall("TABLE"):
                nodes = _parse_table_el(table_el, title_hint=last_text, acode_group=acode_group)
                section.children.extend(nodes)
        elif tag in _IMAGE_TAGS:
            caption_el = child.find("IMG-CAPTION")
            caption = _text_of(caption_el) if caption_el is not None else ""
            section.children.append(TextNode(text=f"[이미지{': ' + caption if caption else ''} — 원문 참조]"))
        elif tag in _SKIP_TAGS:
            continue
        else:
            # 알 수 없는 wrapper 태그 -> silent 하게 내용을 버리지 않고 통과시켜 재귀
            _walk(child, section, warnings)


def _text_volume(root) -> int:
    """트리가 실제로 담고 있는 본문 총량. 문서 절단을 탐지하는 지표."""
    if root is None:
        return -1
    return sum(len(t) for t in root.itertext())


def _parse_with_sanitizer(file_bytes: bytes):
    """사전정리 후 파싱하되, 정리가 오히려 손해면 원본 파싱 결과를 쓴다.

    안전망을 두는 이유(실측): malformation 은 서로를 가릴 수 있다. 본문의 맨 `<`
    가 만든 유령 element 가 스택을 깊게 쌓아둔 덕에, 깨진 속성이 유발하는 연쇄
    tag mismatch 가 DOCUMENT 까지 pop 되지 못하고 멈춰 있던 문서가 실존한다.
    그 상태에서 `<` 만 고치면 완충재가 사라져 루트가 조기 종료되고 이후 내용이
    통째로 폐기된다 (현대자동차 813천자 -> 422천자, KB금융 1,634 -> 814).

    지금 sanitizer 는 3종을 모두 처리하므로 이 경로는 실제로는 거의 타지 않지만,
    앞으로 만날 미지의 malformation 조합에서 같은 함정에 조용히 빠지는 것을
    막는다 (§7 silent failure 금지).
    """
    sanitized, stats = sanitize_dart_xml(file_bytes)

    parser = etree.XMLParser(recover=True, huge_tree=True)
    root = etree.fromstring(sanitized, parser=parser)
    note = stats.describe() if stats.total else None

    if root is not None and len(parser.error_log) == 0:
        return root, note

    # 정리 후에도 파서가 불평한다 — 원본과 본문 총량을 비교해 손해가 아닌지 확인한다.
    fallback_parser = etree.XMLParser(recover=True, huge_tree=True)
    fallback_root = etree.fromstring(file_bytes, parser=fallback_parser)
    if _text_volume(fallback_root) > _text_volume(root):
        logger.warning(
            "[XML_SANITIZER] 사전정리 결과가 원본보다 본문이 적어 원본 파싱으로 되돌림 "
            "(정리후=%d자, 원본=%d자, %s) — 미지의 malformation 조합 가능성",
            _text_volume(root), _text_volume(fallback_root), stats.describe(),
        )
        return fallback_root, (
            f"{stats.describe()} — 그러나 본문이 줄어 원본 파싱으로 되돌림"
            f"(정리후 {_text_volume(root)}자 < 원본 {_text_volume(fallback_root)}자)"
        )
    return root, note


def parse_dart_xml(
    file_bytes: bytes,
    *,
    doc_id: str,
    doc_group: str,
    doc_subtype: str | None,
    report_subtype: str,
    source_path: str,
) -> ParsedDocument:
    warnings: list[str] = []

    # DART 원본은 well-formed 가 아니다. recover 파서에 그냥 넘기면 최상위
    # SECTION-1 이 표 안에 파묻혀 문서 대부분이 조용히 유실된다 — 배경과 실측은
    # xml_sanitizer 모듈 docstring 참고. recover=True 는 그대로 둔다: sanitizer
    # 가 못 잡는 malformation 이 남아 있을 수 있다.
    root, sanitize_note = _parse_with_sanitizer(file_bytes)
    if sanitize_note:
        warnings.append(sanitize_note)

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
