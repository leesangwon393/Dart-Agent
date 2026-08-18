"""Phase 8 회귀 테스트: Tokenizer 추상화 + BM25S baseline retriever."""

from __future__ import annotations

from pathlib import Path

import pytest

from disclosure_rag.chunking.chunk_schema import filter_leaf_chunks
from disclosure_rag.common.manifest_loader import load_manifest
from disclosure_rag.pipeline import build_all_chunks
from disclosure_rag.retrieval.bm25_retriever import BM25Retriever
from disclosure_rag.retrieval.metadata_filter import RetrievalFilter
from disclosure_rag.retrieval.tokenizers import (
    CharNgramTokenizer,
    KiwiTokenizer,
    WhitespaceTokenizer,
    build_tokenizer,
)

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
CONFIG_ROOT = Path(__file__).resolve().parents[1] / "config"
pytestmark = pytest.mark.skipif(not CORPUS_ROOT.is_dir(), reason="corpus/ 없음")

SAMPLE_DOC_IDS = {
    "periodic_20240312000736",
    "major_20241118000171",
    "exchange_20250728800035",
    "holding_20241025000530",
}


@pytest.fixture(scope="module")
def sample_chunks():
    manifest = load_manifest(CORPUS_ROOT)
    rows = [r for r in manifest if r.doc_id in SAMPLE_DOC_IDS]
    all_chunks = build_all_chunks(str(CORPUS_ROOT), rows=rows, validate=False)
    return filter_leaf_chunks(all_chunks)  # 검색 인덱스에는 leaf chunk 만 (parent 제외)


def test_whitespace_tokenizer():
    tok = WhitespaceTokenizer()
    assert tok.tokenize("삼성전자 영업이익") == ["삼성전자", "영업이익"]


def test_char_ngram_tokenizer():
    tok = CharNgramTokenizer(n=2)
    tokens = tok.tokenize("영업이익")
    assert tokens == ["영업", "업이", "이익"]


def test_kiwi_tokenizer_keeps_content_words():
    tok = KiwiTokenizer()
    tokens = tok.tokenize("삼성전자의 영업이익은 얼마야?")
    assert "삼성전자" in tokens
    # 조사(JKG)/어미(EF) 등은 제거되어야 함
    assert "의" not in tokens
    assert "야" not in tokens


def test_kiwi_user_dict_keeps_financial_term_whole():
    """§32: 금융 전문용어 사용자 사전이 실제로 형태소 분석에 반영돼야 한다."""
    tok = KiwiTokenizer(user_dict_path=CONFIG_ROOT / "financial_terms.txt")
    tokens = tok.tokenize("단일판매공급계약 체결 공시입니다")
    assert "단일판매공급계약" in tokens


def test_kiwi_tokenize_batch_matches_sequential_tokenize():
    """회귀(2026-08-18): 100문항 배치 테스트에서 38개사(237,212 chunk) 코퍼스를
    순차 tokenize() 로 인덱싱하다가 45분+ 걸려도 안 끝나서 프로세스가 죽는
    문제가 실측으로 발생 — kiwipiepy 의 멀티스레드 배치 API(`tokenize_batch`)
    로 바꾸니 실측 6배+ 빨라짐(3000건 기준 20.9s -> 3.2s). 결과가 순차 호출과
    동일한지 고정한다(속도만 다르고 토큰화 결과는 같아야 함)."""
    tok = KiwiTokenizer(user_dict_path=CONFIG_ROOT / "financial_terms.txt")
    texts = [
        "삼성전자의 영업이익은 얼마야?",
        "단일판매공급계약 체결 공시입니다",
        "정정 전후 매출액 비교해줘",
    ]
    batch_result = tok.tokenize_batch(texts)
    sequential_result = [tok.tokenize(t) for t in texts]
    assert batch_result == sequential_result


def test_bm25_retriever_uses_tokenize_batch_when_available(sample_chunks):
    """BM25Retriever 가 코퍼스 인덱싱 시 tokenize_batch 를 실제로 쓰는지 확인
    (호출 카운트로 검증 — tokenize() 를 chunk 수만큼 개별 호출하면 안 됨)."""
    from disclosure_rag.retrieval.tokenizers import WhitespaceTokenizer

    class _CountingTokenizer(WhitespaceTokenizer):
        def __init__(self):
            super().__init__()
            self.batch_calls = 0
            self.single_calls = 0

        def tokenize(self, text):
            self.single_calls += 1
            return super().tokenize(text)

        def tokenize_batch(self, texts):
            self.batch_calls += 1
            return [self.tokenize_impl(t) for t in texts]

        def tokenize_impl(self, text):
            return super().tokenize(text)

    tok = _CountingTokenizer()
    BM25Retriever(sample_chunks, tok)
    assert tok.batch_calls == 1, "코퍼스 인덱싱이 tokenize_batch 를 한 번에 호출하지 않음"
    assert tok.single_calls == 0, "tokenize_batch 가 있는데도 개별 tokenize() 를 호출함"


def test_build_tokenizer_dispatch():
    assert isinstance(build_tokenizer("whitespace"), WhitespaceTokenizer)
    assert isinstance(build_tokenizer("kiwi"), KiwiTokenizer)
    assert isinstance(build_tokenizer("char_2gram"), CharNgramTokenizer)
    with pytest.raises(ValueError):
        build_tokenizer("nonexistent")


def test_bm25_retriever_finds_relevant_chunk(sample_chunks):
    tok = build_tokenizer("kiwi", user_dict_path=CONFIG_ROOT / "financial_terms.txt")
    retriever = BM25Retriever(sample_chunks, tok)
    results = retriever.search("반도체 위탁생산 계약금액", k=3)
    assert results
    top_chunk, top_score = results[0]
    assert top_chunk.report_type == "exchange"
    assert "22,764,764,160,000" in top_chunk.raw_text
    assert top_score > 0


def test_bm25_retriever_metadata_filter_narrows_results(sample_chunks):
    tok = build_tokenizer("whitespace")
    retriever = BM25Retriever(sample_chunks, tok)
    flt = RetrievalFilter(doc_groups=["major"])
    results = retriever.search("취득", k=5, flt=flt)
    assert results
    assert all(c.report_type == "major" for c, _ in results)


def test_bm25_retriever_filter_excludes_all_returns_empty(sample_chunks):
    tok = build_tokenizer("whitespace")
    retriever = BM25Retriever(sample_chunks, tok)
    flt = RetrievalFilter(companies=["존재하지않는회사"])
    results = retriever.search("계약", k=5, flt=flt)
    assert results == []


def test_bm25_retriever_empty_query_tokens_returns_empty(sample_chunks):
    tok = build_tokenizer("whitespace")
    retriever = BM25Retriever(sample_chunks, tok)
    results = retriever.search("   ", k=5)
    assert results == []
