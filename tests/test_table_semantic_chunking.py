"""Phase 0~5 회귀 테스트: 표 semantic block chunking.

배경(실제 재현 사례, SK하이닉스 사업보고서 20260317000635.xml, "192,972,588" 검색):
기존 render_table_node() 는 max_rows_per_chunk/max_tokens_per_chunk 같은 순수
행count/토큰 기준으로만 표를 잘라, "1. 매출액"의 "계"(192,972,588백만원) 바로
다음에 오는 "2. 영업이익"의 "계"(47,206,319백만원, 실제 정답)가 다른 chunk 로
갈라졌다 — 단일 chunk 검색으로는 정답을 찾을 수 없었다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from disclosure_rag.agent.tools import make_search_disclosures_tool
from disclosure_rag.chunking.chunk_schema import ChunkSchema, render_table_node_fragments, estimate_tokens
from disclosure_rag.common.doc_tree import TableCell, TableNode
from disclosure_rag.parsing.dart_xml_parser import parse_dart_xml
from disclosure_rag.parsing.exchange_parser import parse_exchange_html
from disclosure_rag.parsing.table_parser import detect_semantic_blocks

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
pytestmark_corpus = pytest.mark.skipif(not CORPUS_ROOT.is_dir(), reason="corpus/ 없음")


def _mk_row(label, indent, *vals):
    return [TableCell(label, indent=indent)] + [TableCell(v) for v in vals]


# ---------------------------------------------------------------------------
# CHECKPOINT 1: detect_semantic_blocks 단위 테스트
# ---------------------------------------------------------------------------


def test_detect_semantic_blocks_numbered_and_indent():
    """"1. 매출액" / "2. 영업이익" 처럼 번호 있는 상위항목 + 들여쓴 하위행 묶음이
    각각 독립 block 으로, block 내부(연결조정/계)는 안 잘리고 유지되는지 확인."""
    rows = [
        _mk_row("1. 매출액", 0, "", ""),
        _mk_row("외부매출액", 4, "1,932,342", "1,904,112"),
        _mk_row("지역간내부매출액", 4, "87,467,888", "56,139,057"),
        _mk_row("계", 4, "89,400,230", "58,043,169"),
        _mk_row("2. 영업이익", 0, "43,995,989", "21,266,459"),
    ]
    blocks = detect_semantic_blocks(rows)
    assert blocks == [[0, 1, 2, 3], [4]]


def test_detect_semantic_blocks_repro_case_no_rowspan():
    """실제 재현 사례(SK하이닉스 20260317000635.xml) 축소판: "2. 영업이익"이 값
    없이 자기 행만 있고, 바로 다음 "연결조정"/"계" 가 들여써진 하위행으로 오는
    경우도 정상적으로 "1. 매출액" block 과 분리된 "2. 영업이익" block 으로 묶여야
    한다(이 케이스는 rowspan 이 전혀 없는, 각자 독립된 행들이다)."""
    rows = [
        _mk_row("외부매출액", 4, "97,146,675", "66,192,960"),
        _mk_row("지역간내부매출액", 4, "95,825,913", "63,767,574"),
        _mk_row("계", 4, "192,972,588", "129,960,534"),  # "1. 매출액" 그룹의 합계
        _mk_row("2. 영업이익", 0, "", ""),
        _mk_row("연결조정", 4, "32,476", "(182,030)"),
        _mk_row("계", 4, "47,206,319", "23,467,319"),  # "2. 영업이익" 그룹의 합계 (정답)
    ]
    blocks = detect_semantic_blocks(rows)
    assert blocks == [[0, 1, 2], [3, 4, 5]], (
        "매출액 계 와 영업이익 계가 여전히 같은 block 에 섞이거나 잘못 갈라짐"
    )


def test_detect_semantic_blocks_rowspan_repeat_stays_one_block():
    """rowspan 확장으로 첫 열 값이 여러 행에 반복되는 경우(예: "2. 투자내역"이
    투자금액/자기자본/자기자본대비/대규모법인여부 4행에 걸쳐 반복)는 전부 같은
    block 으로 묶여야 한다 — origin_id 동일 여부로 판단(번호 패턴과 무관하게)."""
    origin = 10
    rows = [
        [TableCell("2. 투자내역", indent=0, origin_id=origin), TableCell("투자금액"), TableCell("5,296,200,000,000")],
        [TableCell("2. 투자내역", indent=0, origin_id=origin), TableCell("자기자본"), TableCell("53,503,752,397,611")],
        [TableCell("2. 투자내역", indent=0, origin_id=origin), TableCell("자기자본대비"), TableCell("9.90")],
        [TableCell("2. 투자내역", indent=0, origin_id=origin), TableCell("대규모법인여부"), TableCell("해당")],
        [TableCell("3. 투자목적", indent=0, origin_id=99), TableCell(""), TableCell("선제적 대응")],
    ]
    blocks = detect_semantic_blocks(rows)
    assert blocks == [[0, 1, 2, 3], [4]]


def test_detect_semantic_blocks_flat_table_no_structure():
    """번호도 들여쓰기도 없는 평평한 표(예: 삼성SDI 손익계산서처럼 각 행이 완결된
    항목)는 모든 행이 각자 독립 block 이 되어 기존 동작과 동일해야 한다."""
    rows = [
        _mk_row("매출액", 0, "100"),
        _mk_row("매출원가", 0, "60"),
        _mk_row("매출총이익", 0, "40"),
    ]
    blocks = detect_semantic_blocks(rows)
    assert blocks == [[0], [1], [2]]


# ---------------------------------------------------------------------------
# CHECKPOINT 2/3: render_table_node_fragments 패킹 + oversized block 분할
# ---------------------------------------------------------------------------


def test_packer_merges_small_blocks_into_one_chunk():
    """3-block 표(매출액/영업이익/당기순이익)가 전부 max_tokens 이내면 한 chunk 에
    같이 들어가야 한다 — max_rows_per_chunk 를 이유로 억지로 분리하면 안 된다."""
    header = [TableCell("구분", is_header=True), TableCell("2024", is_header=True)]
    rows = (
        [_mk_row("1. 매출액", 0, "")] + [_mk_row(f"세부{i}", 4, "1") for i in range(3)] + [_mk_row("계", 4, "100")]
        + [_mk_row("2. 영업이익", 0, "")] + [_mk_row(f"세부{i}", 4, "1") for i in range(3)] + [_mk_row("계", 4, "50")]
        + [_mk_row("3. 당기순이익", 0, "")] + [_mk_row("계", 4, "30")]
    )
    node = TableNode(rows=[header] + rows)
    frags = render_table_node_fragments(node, max_tokens_per_chunk=1000)
    assert len(frags) == 1
    assert set(frags[0].semantic_groups) == {"1. 매출액", "2. 영업이익", "3. 당기순이익"}


def test_packer_does_not_split_block_across_chunks_when_budget_exceeded():
    """3-block 표가 예산을 넘겨 여러 chunk 로 나뉘어도, "매출액 계 --- CUT ---
    영업이익 시작" 패턴(어느 block 이 chunk 경계에서 반으로 잘리는 것)이 재발하면
    안 된다."""
    header = [TableCell("구분", is_header=True), TableCell("2024", is_header=True)]

    def block(num, name, pad_len):
        pad = "x" * pad_len
        rows = [_mk_row(f"{num}. {name}", 0, "")]
        for i in range(5):
            rows.append(_mk_row(f"세부항목{i}_{pad}", 4, "111,111"))
        rows.append(_mk_row("계", 4, "999,999"))
        return rows

    rows = block(1, "매출액", 200) + block(2, "영업이익", 200) + block(3, "당기순이익", 200)
    node = TableNode(rows=[header] + rows)
    frags = render_table_node_fragments(node, max_tokens_per_chunk=1000)
    assert len(frags) >= 2, "예산 초과로 실제 분할이 발생해야 테스트 의미가 있음"

    for f in frags:
        for label in f.semantic_groups:
            assert label in f.text, f"block label {label!r} 이 자기 fragment 안에 없음(잘렸을 가능성)"

    boundaries = [(frags[i].semantic_groups, frags[i + 1].semantic_groups) for i in range(len(frags) - 1)]
    for left, right in boundaries:
        assert not (left and right and left[-1] == right[0]), (
            f"같은 block 이 두 fragment 경계에 걸쳐 나뉨: {left} | {right}"
        )


def test_oversized_block_split_keeps_label_title_unit_header_in_every_fragment():
    """하나의 block 자체가 max_tokens 보다 크면 내부적으로 분할하되, 분할된 모든
    조각에 title_hint/unit_hint/header/semantic block label 이 반복 삽입돼야
    한다 — 특히 마지막 조각에 "계"만 남아도 어느 항목의 합계인지 알 수 있어야
    한다."""
    header = [TableCell("구분", is_header=True), TableCell("2024", is_header=True)]
    pad = "y" * 60
    rows = [_mk_row("1. 매출액", 0, "")]
    for i in range(60):
        rows.append(_mk_row(f"세부항목{i:03d}_{pad}", 4, "111,111"))
    rows.append(_mk_row("계", 4, "999,999,999"))
    node = TableNode(rows=[header] + rows, title_hint="매출 세부내역", unit_hint="단위: 백만원")

    frags = render_table_node_fragments(node, max_tokens_per_chunk=1000)
    assert len(frags) > 1
    for f in frags:
        assert f.split_reason == "oversized_block"
        assert "매출 세부내역" in f.text
        assert "단위: 백만원" in f.text
        assert "구분" in f.text
        assert any(g.startswith("1. 매출액") for g in f.semantic_groups)
    assert "계" in frags[-1].text
    assert "999,999,999" in frags[-1].text


def test_no_semantic_structure_falls_back_to_row_and_token_budget():
    """구조 신호가 없는 평평한 표는 여전히 max_rows_per_chunk/max_tokens_per_chunk
    로 잘려야 한다(3순위 fallback) — 기존 동작 유지."""
    header = [TableCell("구분", is_header=True), TableCell("2024", is_header=True)]
    rows = [_mk_row(f"항목{i}", 0, "111") for i in range(25)]
    node = TableNode(rows=[header] + rows)
    frags = render_table_node_fragments(node, max_rows_per_chunk=10, max_tokens_per_chunk=10**9)
    assert len(frags) == 3  # 25 rows / 10 per chunk -> 3 fragments
    for f in frags:
        assert f.split_reason == "no_semantic_structure"


# ---------------------------------------------------------------------------
# CHECKPOINT 4: 실제 XML regression (4개 exchange 파일 + SK하이닉스 사업보고서)
# ---------------------------------------------------------------------------

_EXCHANGE_REQUIRED = {
    "20240424800596": ["5,296,200,000,000", "9.90", "청주 M15X 건설"],
    "20240726800615": ["9,411,500,000,000", "17.59", "용인 반도체 클러스터 내 신규 Fab 건설"],
    "20241220800005": ["5.9조원", "HBM 경쟁력 강화", "2025년 1월 ~ 2039년 12월"],
    "20260225801974": ["21,608,100,000,000", "29.23", "용인 반도체 클러스터 1기 Fab Phase 2~6 건설"],
}


@pytestmark_corpus
@pytest.mark.parametrize("doc_id,required", list(_EXCHANGE_REQUIRED.items()))
def test_exchange_xml_regression_values_survive(doc_id, required):
    path = CORPUS_ROOT / "raw" / "exchange" / "SK하이닉스" / doc_id / f"{doc_id}.xml"
    assert path.is_file(), f"fixture 없음: {path}"
    doc = parse_exchange_html(path.read_bytes(), doc_id=doc_id, doc_subtype=None, source_path=str(path))
    from disclosure_rag.chunking.chunkers import chunk_flat_whole_doc_preferred
    from disclosure_rag.common.manifest_loader import ManifestRow
    from disclosure_rag.correction.correction_graph_builder import CorrectionRecord

    row = ManifestRow(
        doc_id=f"exchange_{doc_id}", corp_code="000660", corp_name="SK하이닉스", listed_name="SK하이닉스",
        stock_code="000660", industry="반도체", sector="", doc_group="exchange", doc_subtype=None,
        report_nm="주요사항", is_correction=False, rcept_no=doc_id, rcept_dt="20240101", flr_nm="SK하이닉스",
        base_year=None, base_month=None, file_path=str(path), file_format="xml", n_files=1,
    )
    correction = CorrectionRecord(
        doc_id=row.doc_id, correction_group_id="g1", correction_order=0, is_correction=False,
        is_latest=True, resolution_source="test",
    )
    chunks = chunk_flat_whole_doc_preferred(doc, row, correction)
    joined = "\n".join(c.raw_text for c in chunks)
    for value in required:
        assert value in joined, f"{doc_id}: {value!r} 이 최종 chunk 에서 사라짐 (회귀)"


@pytestmark_corpus
def test_periodic_sk_hynix_repro_operating_profit_same_chunk():
    """실제 failure 재현 사례의 핵심 검증: SK하이닉스 사업보고서(20260317000635)
    지역별 매출/영업이익 표에서 "1. 매출액 계"(192,972,588)와 "2. 영업이익 계"
    (47,206,319, 실제 정답)가 같은 chunk 안에 함께 존재해야 한다."""
    from disclosure_rag.common.manifest_loader import load_manifest
    from disclosure_rag.pipeline import build_all_chunks

    manifest = load_manifest(str(CORPUS_ROOT))
    rows = [r for r in manifest if r.doc_id == "periodic_20260317000635"]
    assert rows, "manifest 에 SK하이닉스 사업보고서(20260317000635)가 없음"
    chunks = build_all_chunks(str(CORPUS_ROOT), rows=rows, validate=False)

    hit = [c for c in chunks if "192,972,588" in c.raw_text and "47,206,319" in c.raw_text]
    assert hit, (
        "192,972,588(1.매출액 계)과 47,206,319(2.영업이익 계, 정답)가 같은 chunk 에 "
        "없음 — semantic block chunking 회귀가 재발했을 가능성"
    )


# ---------------------------------------------------------------------------
# CHECKPOINT 5: search_disclosures sibling expansion
# ---------------------------------------------------------------------------


def _mk_chunk(chunk_id, table_id, idx, count, prev, next_, metric_hints, text):
    return ChunkSchema(
        chunk_id=chunk_id, report_id="r1", raw_text=text, text=text,
        table_id=table_id, table_chunk_index=idx, table_chunk_count=count,
        prev_table_chunk_id=prev, next_table_chunk_id=next_, metric_hints=metric_hints,
    )


class _FakeBM25:
    def __init__(self, chunks_by_id):
        self.chunks_by_id = chunks_by_id


class _FakeRetriever:
    def __init__(self, chunks_by_id, hit_chunk):
        self.bm25 = _FakeBM25(chunks_by_id)
        self._hit_chunk = hit_chunk

    def search(self, query, k=5, flt=None):
        return [(self._hit_chunk, 1.0)]


def test_sibling_expansion_pulls_in_metric_matching_sibling():
    c3 = _mk_chunk("C3", "T1", 1, 3, None, "C4", ["당기순이익"], "당기순이익 계 100")
    c4 = _mk_chunk("C4", "T1", 2, 3, "C3", "C5", ["매출액"], "1. 매출액 계 200")
    c5 = _mk_chunk("C5", "T1", 3, 3, "C4", None, ["영업이익"], "2. 영업이익 계 300")
    chunks_by_id = {"C3": c3, "C4": c4, "C5": c5}

    tool_on = make_search_disclosures_tool(
        _FakeRetriever(chunks_by_id, c4), expand_table_siblings=True, max_table_sibling_expansion=1,
    )
    out_on = tool_on.handler(query="영업이익 얼마야")
    ids_on = [r["chunk_id"] for r in out_on["results"]]
    assert "C4" in ids_on and "C5" in ids_on

    tool_off = make_search_disclosures_tool(
        _FakeRetriever(chunks_by_id, c4), expand_table_siblings=False,
    )
    out_off = tool_off.handler(query="영업이익 얼마야")
    assert [r["chunk_id"] for r in out_off["results"]] == ["C4"]
