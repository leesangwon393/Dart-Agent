"""Phase 15~19 회귀 테스트: HCX Agent Loop, Tools, Evidence Pack, Answer, Validator.

실제 HCX API(.env 의 HCX_API_KEY) 를 호출한다 — 네트워크/키 필요, 느림, 계정
tier 에 따라 rate-limit 영향을 받을 수 있다. 전부 slow 마킹.

핵심 실측 사실(중요, 회귀 방지용으로 여기 남겨둠):
- system prompt 가 길면(예전 버전 ~400자, 6줄 bullet) tool-calling 2번째 턴부터
  HCX API 가 결정적으로(재시도해도 실패) 400 "Unsupported function" 을 반환했다.
  AGENT_SYSTEM_PROMPT 를 짧게 유지해야 한다 — 이 테스트가 그 회귀를 잡는다
  (max_iterations>=2 가 필요한 시나리오를 반드시 하나는 포함해야 함).
- Coarse-to-Fine 검색에서 HCX 가 period 포맷을 잘못 추측해 필터가 0건이 되는
  케이스가 실측으로 흔했다 — search_disclosures 의 자동 필터 완화 재시도로 처리.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from disclosure_rag.agent.calculation import calculate_cagr, calculate_growth_rate, calculate_ratio
from disclosure_rag.agent.evidence import build_evidence_pack
from disclosure_rag.agent.validator import validate_answer
from disclosure_rag.chunking.chunk_schema import filter_leaf_chunks
from disclosure_rag.common.manifest_loader import load_manifest
from disclosure_rag.common.unicode_utils import PathResolver
from disclosure_rag.correction.correction_graph_builder import build_correction_index
from disclosure_rag.entity.entity_extractor import EntityExtractor
from disclosure_rag.pipeline import build_all_chunks
from disclosure_rag.retrieval.bm25_retriever import BM25Retriever
from disclosure_rag.retrieval.tokenizers import build_tokenizer

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
CONFIG_ROOT = Path(__file__).resolve().parents[1] / "config"
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
pytestmark = pytest.mark.skipif(not CORPUS_ROOT.is_dir(), reason="corpus/ 없음")

SAMPLE_DOC_IDS = {
    "periodic_20240312000736", "major_20241118000171",
    "exchange_20250728800035", "exchange_20250731800028",
    "holding_20241025000530",
}


def test_calculation_tools_are_deterministic():
    assert calculate_growth_rate(100, 150)["growth_rate_pct"] == 50.0
    assert calculate_ratio(50, 200)["ratio_pct"] == 25.0
    r = calculate_cagr(100, 200, 3)
    assert 25.0 < r["cagr_pct"] < 26.5  # (2^(1/3)-1)*100 ≈ 25.99


def test_calculation_tools_handle_zero_denominator():
    assert calculate_growth_rate(0, 10)["error"]
    assert calculate_ratio(10, 0)["error"]


def _try_setup():
    if not ENV_PATH.is_file():
        pytest.skip(".env 없음 — HCX API 키 필요")
    try:
        from disclosure_rag.agent.hcx_client import HCXClient
        client = HCXClient(env_path=ENV_PATH)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"HCX client 초기화 불가: {e}")

    manifest = load_manifest(CORPUS_ROOT)
    resolver = PathResolver(CORPUS_ROOT)
    rows = [r for r in manifest if r.doc_id in SAMPLE_DOC_IDS]
    chunks = filter_leaf_chunks(build_all_chunks(str(CORPUS_ROOT), rows=rows, validate=False))
    correction_index = build_correction_index(manifest, resolver)
    tok = build_tokenizer("kiwi", user_dict_path=CONFIG_ROOT / "financial_terms.txt")
    bm25 = BM25Retriever(chunks, tok)

    from disclosure_rag.agent.tools import build_all_tools
    tools = build_all_tools(bm25, manifest, correction_index)
    extractor = EntityExtractor(corpus_root=CORPUS_ROOT, metric_terms_path=CONFIG_ROOT / "metric_terms.txt")
    return client, tools, extractor


@pytest.mark.slow
def test_agent_single_lookup_finds_correct_number():
    """실측 검증된 케이스: 삼성전자 반도체 위탁생산 계약금액 = 22,764,764,160,000원.
    (교차검증: corpus/raw 원문 XML 의 '계약금액(원)' 필드와 일치함)"""
    client, tools, extractor = _try_setup()
    from disclosure_rag.agent.ask import ask

    result = ask(client, tools, "삼성전자 반도체 위탁생산 계약금액 얼마야?", entity_extractor=extractor, max_iterations=4)
    assert "22,764,764,160,000" in result.answer or "22조" in result.answer
    assert result.validation.numbers_grounded
    assert result.validation.has_citation


@pytest.mark.slow
def test_agent_correction_analysis_uses_both_versions_and_two_plus_turns():
    """회귀 테스트: AGENT_SYSTEM_PROMPT 가 길어지면 2턴째부터 API 가 결정적으로
    실패했던 문제를 잡는다 — 이 시나리오는 반드시 다중 턴(get_correction_history
    이후 최소 1번 이상의 추가 tool 호출)이 필요하다.
    실측 검증된 정답: 계약상대가 '글로벌 대형기업' -> '테슬라(Tesla, Inc.)' 로 정정됨
    (교차검증: corpus/raw 원문 XML 의 정정사항 표와 일치함)."""
    client, tools, extractor = _try_setup()
    from disclosure_rag.agent.ask import ask

    result = ask(
        client, tools, "삼성전자 단일판매공급계약체결 공시 정정 전후로 뭐가 바뀌었어?",
        entity_extractor=extractor, max_iterations=6,
    )
    assert result.trace.iterations >= 2, "다중 턴 tool-calling 이 실행되지 않음 (회귀 의심)"
    assert "테슬라" in result.answer or "Tesla" in result.answer
    report_ids_cited = {c.report_id for c in result.evidence_pack.citations}
    assert "exchange_20250728800035" in report_ids_cited
    assert "exchange_20250731800028" in report_ids_cited


def test_agent_system_prompt_stays_under_300_chars():
    """잠긴 제약: AGENT_SYSTEM_PROMPT 가 ~300자 넘으면 tool-calling 2턴째부터
    결정적으로 400 에러가 난다(3회 독립 재현). 느린 실API 통합 테스트
    (`test_agent_correction_analysis_uses_both_versions_and_two_plus_turns`)
    말고도 빠르게 이 회귀를 잡을 수 있게 길이만 따로 고정한다."""
    from disclosure_rag.agent.agent_loop import AGENT_SYSTEM_PROMPT

    assert len(AGENT_SYSTEM_PROMPT) < 300


def test_route_hint_suggests_split_search_for_period_comparison_axis():
    """2026-08-27 추가: "당기 대비 전기"처럼 명시적 비교 문구(period_comparison)가
    없어도, comparison_axis="period"(예: "2023년 사업보고서와 2025년 사업보고서를
    비교")면 "각 기간을 따로 조회하라"는 지시가 떠야 한다 — 파트 2 실검증(Q6)에서
    이 지시가 없어서 Agent가 검색 1회(top_k=1)로 끝내고 정답을 놓친 걸 재현/수정."""
    from disclosure_rag.agent.agent_loop import _route_hint_message
    from disclosure_rag.entity.entity_extractor import ExtractedEntities

    entities = ExtractedEntities(
        raw_query="삼성전자의 2023년 사업보고서와 2025년 사업보고서를 비교했을 때 핵심 사업은 어떻게 변화했는지 설명해줘",
        companies=["삼성전자"], period=["2023년", "2025년"],
        period_comparison=False, comparison_axis="period",
    )
    hint = _route_hint_message(entities, "multi_compare", 0.8)
    assert "각 기간을 따로 조회" in hint


def test_route_hint_marks_sector_derived_companies_as_auto_selected():
    """2026-08-29 추가: sector/peer 선택(entity_extractor의 new/ Phase 1 이식)으로
    채워진 companies 는 사용자가 직접 지정한 게 아니라는 걸 Agent에게 밝혀야
    한다 — 안 밝히면 "사용자가 이 회사들을 콕 집었다"고 오인해 다른 회사를
    빠뜨렸다고 착각하거나 반대로 임의로 목록을 더 좁힐 위험이 있다."""
    from disclosure_rag.agent.agent_loop import _route_hint_message
    from disclosure_rag.entity.entity_extractor import ExtractedEntities

    entities = ExtractedEntities(
        raw_query="주요 방산기업 3곳 비교해줘",
        companies=["한화에어로스페이스", "현대로템", "LIG디펜스앤에어로스페이스"],
        entity_scope="sector", sector="방산·항공우주", sector_no=14,
        peer_selection="market_cap_top_n", requested_top_n=3,
    )
    hint = _route_hint_message(entities, "multi_compare", 0.8)
    assert "자동 선정" in hint
    assert "방산·항공우주" in hint
    assert "시가총액 상위 3곳" in hint


def test_route_hint_no_auto_selection_note_for_explicit_companies():
    from disclosure_rag.agent.agent_loop import _route_hint_message
    from disclosure_rag.entity.entity_extractor import ExtractedEntities

    entities = ExtractedEntities(
        raw_query="삼성전자 영업이익 얼마야?",
        companies=["삼성전자"], entity_scope="explicit_companies",
    )
    hint = _route_hint_message(entities, "single_lookup", 0.9)
    assert "자동 선정" not in hint


def test_answer_system_prompt_distinguishes_event_date_from_document_date():
    """2026-08-27 추가: Q5(§5-D) 재현 — evidence 안에 "정정관련 공시서류제출일"
    처럼 사건 자체와 다른 날짜가 있을 때, 답변모델이 둘을 안 헷갈리게 하라는
    원칙이 실제로 프롬프트에 있는지 고정."""
    from disclosure_rag.agent.answer_generator import ANSWER_SYSTEM_PROMPT

    assert "발생일" in ANSWER_SYSTEM_PROMPT or "체결일" in ANSWER_SYSTEM_PROMPT


def test_answer_system_prompt_requires_consolidated_vs_standalone_distinction():
    """2026-08-29 추가(§7 우선순위 1): SK하이닉스 사례 재현 — 질문에 "연결기준"
    명시가 없으면 별도(개별)재무제표 수치를 연결기준인 것처럼 답변해버리는
    문제. "연결/별도를 구분하고 명시하라"는 원칙이 프롬프트에 있는지 고정."""
    from disclosure_rag.agent.answer_generator import ANSWER_SYSTEM_PROMPT

    assert "연결" in ANSWER_SYSTEM_PROMPT
    assert "별도" in ANSWER_SYSTEM_PROMPT


def test_validator_catches_hallucinated_citation():
    """실측 재현: 답변이 evidence 에 없는 report_id 를 인용하면 has_citation=False 가 돼야 한다."""
    from disclosure_rag.agent.evidence import EvidencePack

    pack = EvidencePack(question="q", prompt_text="[EVIDENCE 1]\n내용: 영업이익 100억원\nreport_id: real_doc_1\n", citations=[])
    answer = "영업이익은 100억원입니다.\n근거: report_id(fake_doc_999)"
    from disclosure_rag.entity.entity_extractor import ExtractedEntities

    entities = ExtractedEntities(raw_query="q")
    result = validate_answer(answer, pack, entities)
    assert result.has_citation is False  # citations 리스트가 비어있으므로


def test_validator_flags_ungrounded_number():
    from disclosure_rag.agent.evidence import EvidencePack
    from disclosure_rag.entity.entity_extractor import ExtractedEntities

    pack = EvidencePack(question="q", prompt_text="[EVIDENCE 1]\n내용: 영업이익 100000원\nreport_id: r1\nchunk_id: c1\n", citations=[])
    answer = "영업이익은 999999999원입니다. 근거: r1"
    result = validate_answer(answer, pack, ExtractedEntities(raw_query="q"))
    assert not result.numbers_grounded
    assert "999999999" in result.ungrounded_numbers


def test_validator_ignores_approx_paren_restatement():
    """회귀(2026-08-16, 회사 일반화 스모크테스트): "7,661,584백만원 (약 7조
    6,615억원)"처럼 같은 숫자를 조/억 단위로 다시 풀어 쓴 괄호 안 숫자가
    evidence 원문과 문자 그대로 안 겹친다는 이유로 "근거 없는 숫자"로 오탐됐다.
    "(약 ...)" 괄호는 근사 재표기이므로 grounding 검사에서 제외해야 한다."""
    from disclosure_rag.agent.evidence import EvidencePack
    from disclosure_rag.entity.entity_extractor import ExtractedEntities

    pack = EvidencePack(
        question="q", prompt_text="[EVIDENCE 1]\n내용: 매출액 7,661,584백만원\nreport_id: r1\nchunk_id: c1\n",
        citations=[],
    )
    answer = "매출액은 7,661,584백만원(약 7조 6,615억원)입니다. 근거: r1"
    result = validate_answer(answer, pack, ExtractedEntities(raw_query="q"))
    assert result.numbers_grounded, f"괄호 안 재표기가 오탐됨: {result.ungrounded_numbers}"


def test_validator_does_not_flag_year_that_is_literal_substring_of_dated_evidence():
    """회귀(2026-08-19, 100문항 일반화 배치): correction_analysis 라우트 질문
    9건 + single_lookup 2건에서 evidence 원문의 "(2023.12)" 같은 날짜 표기가
    "2023.12"라는 하나의 토큰으로 추출되는 바람에, 답변이 같은 연도를
    "2023년 3월 12일에 제출"처럼 점 없이 따로 쓰면 "2023"이 evidence_numbers
    집합에 없어서 "근거 없는 숫자"로 오탐됐다(실측: 기아/NAVER/신한지주 등).
    evidence 원문에 "2023"이 "2023.12"의 앞부분으로 문자 그대로 존재하므로
    ungrounded 로 잡히면 안 된다."""
    from disclosure_rag.agent.evidence import EvidencePack
    from disclosure_rag.entity.entity_extractor import ExtractedEntities

    pack = EvidencePack(
        question="q",
        prompt_text="[TOOL RESULT]\nget_correction_history: '[기재정정]사업보고서 (2023.12)'\n",
        citations=[],
        tool_results_summary=[{
            "tool": "get_correction_history", "arguments": {},
            "result": {"correction_groups": [{"chain": [{"doc_id": "periodic_20240312000909", "title": "[기재정정]사업보고서 (2023.12)"}]}]},
        }],
    )
    answer = (
        "가장 최근 기재정정은 '[기재정정]사업보고서 (2023.12)'이며, "
        "이는 2023년 3월 12일에 제출되었습니다.\n근거: report_id(periodic_20240312000909)"
    )
    result = validate_answer(answer, pack, ExtractedEntities(raw_query="q"))
    assert result.numbers_grounded, f"evidence에 문자 그대로 있는 연도가 오탐됨: {result.ungrounded_numbers}"
    assert "2023" not in result.ungrounded_numbers


def test_validator_has_citation_from_correction_history_tool_result():
    """회귀(2026-08-16): get_correction_history 만 호출돼 evidence_pack.citations
    가 비어있는 경우(search_disclosures 를 안 씀), 답변이 tool 결과의 report_id
    를 정확히 인용했는데도 무조건 has_citation=False 로 잡혔다."""
    from disclosure_rag.agent.evidence import EvidencePack
    from disclosure_rag.entity.entity_extractor import ExtractedEntities

    pack = EvidencePack(
        question="q", prompt_text="[TOOL RESULT]\nget_correction_history: ...\n",
        citations=[],
        tool_results_summary=[{
            "tool": "get_correction_history", "arguments": {},
            "result": {"correction_groups": [{"chain": [{"doc_id": "major_20250519000120"}]}]},
        }],
    )
    answer = "정정이 4번 있었습니다.\n근거: report_id(major_20250519000120)"
    result = validate_answer(answer, pack, ExtractedEntities(raw_query="q"))
    assert result.has_citation is True


def test_validator_rejects_citation_word_without_matching_report_id():
    """2026-08-29 추가(§7 우선순위 3): 기존엔 `"근거" in answer` 라는 느슨한
    폴백이 있어서, evidence가 있고 답변에 "근거"라는 글자만 있으면 인용된
    report_id가 실제로 존재하는지와 무관하게 has_citation=True로 통과됐다.
    이제는 report_id/chunk_id 문자열이 실제로 답변에 등장해야 한다 — 아예
    다른 report_id(형식 손상도 아닌 완전히 무관한 값)를 대면 실패해야 함."""
    from disclosure_rag.agent.evidence import Citation, EvidencePack
    from disclosure_rag.entity.entity_extractor import ExtractedEntities

    pack = EvidencePack(
        question="q", prompt_text="[EVIDENCE 1]\n내용: 영업이익 100억원\n",
        citations=[Citation(
            chunk_id="c1", report_id="periodic_20260515001572", company="한미반도체",
            report_name="사업보고서", filing_date="20260515", section_path=[],
            is_correction=False, is_latest=True,
        )],
    )
    answer = "영업이익은 100억원입니다.\n근거: 사업보고서 내용을 참고했습니다."
    result = validate_answer(answer, pack, ExtractedEntities(raw_query="q"))
    assert result.has_citation is False
    assert any("근거(report_id/chunk_id) 인용이 없음" in w for w in result.warnings)


def test_validator_flags_corrupted_report_id_citation_distinctly():
    """2026-08-29 추가(§7 우선순위 3): 한미반도체 실측 사례 재현 — 답변모델이
    periodic_20260515001572 를 periodic_20260515_01572 로 자릿수를 지우고
    언더스코어를 삽입해서 인용하면, has_citation은 여전히 False(신뢰 가능한
    인용이 아니므로)이되, "인용 자체가 없음"과는 다른 "형식 손상" 경고를
    따로 남겨야 한다(디버깅/원인 추적용)."""
    from disclosure_rag.agent.evidence import Citation, EvidencePack
    from disclosure_rag.entity.entity_extractor import ExtractedEntities

    pack = EvidencePack(
        question="q", prompt_text="[EVIDENCE 1]\n내용: 영업이익 100억원\n",
        citations=[Citation(
            chunk_id="c1", report_id="periodic_20260515001572", company="한미반도체",
            report_name="사업보고서", filing_date="20260515", section_path=[],
            is_correction=False, is_latest=True,
        )],
    )
    answer = "영업이익은 100억원입니다.\n근거: report_id(periodic_20260515_01572)"
    result = validate_answer(answer, pack, ExtractedEntities(raw_query="q"))
    assert result.has_citation is False
    assert any("형식이 손상" in w for w in result.warnings)


# ── 2026-08-18 회귀 테스트: 답변 모델이 tool 없이 evidence 숫자를 직접 조합해
# 암산한 경우를 구분(맞으면 통과, 틀리면 여전히 의심) — 사용자 피드백
# "숫자 계산은 LLM 안 시키고 직접 계산하는 게 맞는 것 같다"로 시작됨.
# 실측 사례(matrix.csv): 현대자동차 "당기 대비 전기 영업이익 변화" 질문에서
# 답변이 2,164,043 - 1,795,249 = 368,794 를 암산했는데, 이전 로직은 이걸
# "근거 없는 숫자"로 오탐(실제로는 정확한 계산이었음) — validator가 직접
# 검산해서 구분해야 한다.


def test_validator_verifies_correct_ad_hoc_subtraction():
    from disclosure_rag.agent.evidence import EvidencePack
    from disclosure_rag.entity.entity_extractor import ExtractedEntities

    pack = EvidencePack(
        question="q",
        prompt_text="[EVIDENCE 1]\n내용: 당기 영업이익 2164043백만원, 전기 영업이익 1795249백만원\nreport_id: r1\nchunk_id: c1\n",
        citations=[],
    )
    answer = "영업이익은 2164043백만원으로 전기 1795249백만원 대비 368794백만원 증가했습니다. 근거: r1"
    result = validate_answer(answer, pack, ExtractedEntities(raw_query="q"))
    assert result.numbers_grounded, f"검산 가능한 뺄셈이 오탐됨: {result.ungrounded_numbers}"
    assert "368794" in result.verified_derived_numbers


def test_validator_still_flags_incorrect_ad_hoc_arithmetic():
    """핵심 회귀: 검산 로직이 있다고 해서 아무 숫자나 통과시키면 안 된다 —
    실제 evidence 숫자들의 사칙연산으로 설명 안 되는 값(예: 알테오젠 10배
    오류처럼 자릿수를 잘못 읽은 경우)은 여전히 의심스러운 숫자로 남아야 한다."""
    from disclosure_rag.agent.evidence import EvidencePack
    from disclosure_rag.entity.entity_extractor import ExtractedEntities

    pack = EvidencePack(
        question="q",
        prompt_text="[EVIDENCE 1]\n내용: 계약금액 8000000000원\nreport_id: r1\nchunk_id: c1\n",
        citations=[],
    )
    answer = "계약금액은 80000000000원입니다. 근거: r1"  # 10배 부풀려진 오답
    result = validate_answer(answer, pack, ExtractedEntities(raw_query="q"))
    assert not result.numbers_grounded
    assert "80000000000" in result.ungrounded_numbers
    assert result.verified_derived_numbers == {}


def test_ask_retries_answer_generation_when_numbers_dont_verify():
    """핵심 회귀: ask() 가 검산 실패한 답변을 그냥 로그만 남기고 사용자에게
    그대로 돌려주면 안 된다 — 교정 지시를 덧붙여 재생성을 시도하고, 재생성된
    (검산 통과한) 답변을 최종 결과로 써야 한다."""
    from disclosure_rag.agent.ask import ask
    from disclosure_rag.agent.tools import ToolDef

    dummy_tool = ToolDef(
        name="dummy_tool", description="테스트용",
        parameters={"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
        handler=lambda x: {"value": x},
    )
    responses = [
        # 1) run_agent_loop: tool 한 번 호출
        {"role": "assistant", "content": "", "toolCalls": [
            {"id": "id1", "type": "function", "function": {"name": "dummy_tool", "arguments": {"x": 100000}}},
        ]},
        # 2) run_agent_loop: 더 이상 tool 호출 없음 -> 종료
        {"role": "assistant", "content": "완료", "toolCalls": None},
        # 3) generate_answer 1차 시도: 검산 안 되는 숫자를 암산해서 답변에 넣음
        {"role": "assistant", "content": "값은 999999999입니다. 근거: r1", "toolCalls": None},
        # 4) generate_answer 재시도: 교정 지시를 받아 암산 없이 답변
        {"role": "assistant", "content": "계산 결과가 제공되지 않았습니다.", "toolCalls": None},
    ]
    client = _StubHCXClient(responses)
    extractor = EntityExtractor(corpus_root=CORPUS_ROOT, metric_terms_path=CONFIG_ROOT / "metric_terms.txt")

    result = ask(client, [dummy_tool], "테스트 질문", entity_extractor=extractor, max_iterations=6)

    assert result.answer == "계산 결과가 제공되지 않았습니다.", "재생성된 답변으로 교체되지 않음"
    assert result.validation.numbers_grounded, "재시도 후에도 검산 실패로 남아있음"


def test_validator_verifies_correct_ratio_calculation():
    """min_digits=3 필터 때문에 결과가 3자리 이상 나오는 숫자로 검증한다
    (예: "25%"는 2자리라 애초에 검사 대상이 아님 — 150%로 검증)."""
    from disclosure_rag.agent.evidence import EvidencePack
    from disclosure_rag.entity.entity_extractor import ExtractedEntities

    pack = EvidencePack(
        question="q",
        prompt_text="[EVIDENCE 1]\n내용: 영업이익 1500000원, 매출액 1000000원\nreport_id: r1\nchunk_id: c1\n",
        citations=[],
    )
    answer = "영업이익률은 150%입니다(영업이익 1500000원 / 매출액 1000000원). 근거: r1"
    result = validate_answer(answer, pack, ExtractedEntities(raw_query="q"))
    assert result.numbers_grounded, f"검산 가능한 비율이 오탐됨: {result.ungrounded_numbers}"


class _StubHCXClient:
    """agent_loop 의 duplicate-tool-call 방지 로직을 실제 API 없이 검증하기
    위한 스텁 — 스크립트된 응답을 순서대로 반환한다."""

    def __init__(self, responses: list[dict]):
        self._responses = list(responses)

    def chat(self, messages, *, tools=None, **kwargs):
        return self._responses.pop(0)


def test_agent_loop_skips_redundant_identical_tool_calls():
    """회귀(2026-08-16, 회사 일반화 스모크테스트): "몇 건이야?" 같은 카운팅
    질문이 calculation route 로 오분류되면, agent 가 calculate_cagr 을 완전히
    동일한(무의미한) 인자로 여러 번 연속 호출하다 포기하는 경우가 실측 재현됐다
    (n_years=0 처럼 이미 실패가 확정된 입력을 계속 재시도). 이름+인자가 완전히
    같은 tool 호출은 실제로 재실행하지 않아야 한다."""
    from disclosure_rag.agent.agent_loop import run_agent_loop
    from disclosure_rag.agent.tools import ToolDef

    call_count = {"n": 0}

    def handler(x: int) -> dict:
        call_count["n"] += 1
        return {"value": x}

    dummy_tool = ToolDef(
        name="dummy_tool", description="테스트용",
        parameters={"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
        handler=handler,
    )
    repeated_call_msg = {
        "role": "assistant", "content": "",
        "toolCalls": [{"id": "id", "type": "function", "function": {"name": "dummy_tool", "arguments": {"x": 5}}}],
    }
    responses = [dict(repeated_call_msg) for _ in range(3)] + [{"role": "assistant", "content": "완료.", "toolCalls": None}]
    client = _StubHCXClient(responses)
    extractor = EntityExtractor(corpus_root=CORPUS_ROOT, metric_terms_path=CONFIG_ROOT / "metric_terms.txt")

    trace = run_agent_loop(client, [dummy_tool], "테스트 질문", entity_extractor=extractor, max_iterations=6)

    assert len(trace.tool_calls) == 3, "호출 기록 자체는 투명하게 3번 다 남아야 함"
    assert call_count["n"] == 1, f"handler 가 {call_count['n']}번 실제 실행됨 — 중복 방지가 동작 안 함"
    assert "이미" in trace.tool_calls[1].result.get("note", ""), "2번째부터는 중복 안내가 붙어야 함"


# ── 2026-08-18 100문항 배치 준비: agent(HCX-007)/answer(HCX-005) 모델 분리
# (PROJECT_STATE §12 TODO). ask()에 answer_client 를 넘기면 agent loop 는
# 여전히 client 로 돌아가고, generate_answer() 호출(최초 시도 + 재시도 루프)만
# answer_client 로 넘어가야 한다 — 두 스텁을 별도로 추적해서 실제로 분리되는지
# 검증한다.


def test_ask_routes_answer_generation_to_separate_answer_client():
    from disclosure_rag.agent.ask import ask
    from disclosure_rag.agent.tools import ToolDef

    dummy_tool = ToolDef(
        name="dummy_tool", description="테스트용",
        parameters={"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
        handler=lambda x: {"value": x},
    )
    agent_responses = [
        {"role": "assistant", "content": "", "toolCalls": [
            {"id": "id1", "type": "function", "function": {"name": "dummy_tool", "arguments": {"x": 1}}},
        ]},
        {"role": "assistant", "content": "완료", "toolCalls": None},
    ]
    answer_responses = [
        {"role": "assistant", "content": "answer-client 가 생성한 답변입니다.", "toolCalls": None},
    ]
    agent_client = _StubHCXClient(agent_responses)
    answer_client = _StubHCXClient(answer_responses)
    extractor = EntityExtractor(corpus_root=CORPUS_ROOT, metric_terms_path=CONFIG_ROOT / "metric_terms.txt")

    result = ask(
        agent_client, [dummy_tool], "테스트 질문", entity_extractor=extractor,
        max_iterations=6, answer_client=answer_client,
    )

    assert result.answer == "answer-client 가 생성한 답변입니다.", "generate_answer 가 answer_client 를 쓰지 않음"
    assert agent_client._responses == [], "agent_client 응답이 소진되지 않음(agent loop 호출 누락 의심)"
    assert answer_client._responses == [], "answer_client 응답이 소진되지 않음(generate_answer 호출 누락 의심)"


def test_ask_defaults_answer_generation_to_same_client_when_answer_client_omitted():
    """하위호환: answer_client 를 안 넘기면 기존처럼 client 하나가 agent loop 와
    generate_answer 양쪽에 다 쓰여야 한다."""
    from disclosure_rag.agent.ask import ask
    from disclosure_rag.agent.tools import ToolDef

    dummy_tool = ToolDef(
        name="dummy_tool", description="테스트용",
        parameters={"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
        handler=lambda x: {"value": x},
    )
    responses = [
        {"role": "assistant", "content": "", "toolCalls": [
            {"id": "id1", "type": "function", "function": {"name": "dummy_tool", "arguments": {"x": 1}}},
        ]},
        {"role": "assistant", "content": "완료", "toolCalls": None},
        {"role": "assistant", "content": "단일 client 답변입니다.", "toolCalls": None},
    ]
    client = _StubHCXClient(responses)
    extractor = EntityExtractor(corpus_root=CORPUS_ROOT, metric_terms_path=CONFIG_ROOT / "metric_terms.txt")

    result = ask(client, [dummy_tool], "테스트 질문", entity_extractor=extractor, max_iterations=6)

    assert result.answer == "단일 client 답변입니다."
    assert client._responses == [], "client 응답이 전부 소진되지 않음"
