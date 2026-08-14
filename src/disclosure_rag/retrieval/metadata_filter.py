"""Metadata Filtering (§51): 전체 corpus 를 Coarse 하게 줄인 뒤 BM25/Dense 를 돌린다."""

from __future__ import annotations

from dataclasses import dataclass

from disclosure_rag.chunking.chunk_schema import ChunkSchema
from disclosure_rag.common.unicode_utils import normalize_nfc


@dataclass
class RetrievalFilter:
    companies: list[str] | None = None       # NFC 정규화된 corp_name 리스트
    report_ids: list[str] | None = None       # 특정 문서(report_id/doc_id)로 정확히 좁힐 때 사용
    doc_groups: list[str] | None = None       # periodic|major|exchange|holding
    doc_subtypes: list[str] | None = None
    periods: list[str] | None = None          # "YYYY-MM" or "YYYY"
    filing_date_from: str | None = None       # YYYYMMDD
    filing_date_to: str | None = None
    latest_only: bool = False                 # 일반 조회: 최신 유효본만 (§30)
    include_corrections: bool = True          # 정정 분석: original+정정 체인 유지 (§30)

    def __post_init__(self):
        if self.companies:
            self.companies = [normalize_nfc(c) for c in self.companies]

    def matches(self, chunk: ChunkSchema) -> bool:
        if self.report_ids and chunk.report_id not in self.report_ids:
            return False
        if self.companies and chunk.company not in self.companies:
            return False
        if self.doc_groups and chunk.report_type not in self.doc_groups:
            return False
        if self.doc_subtypes and chunk.report_subtype not in self.doc_subtypes:
            return False
        if self.periods and chunk.period not in self.periods:
            return False
        if self.filing_date_from and (chunk.filing_date or "") < self.filing_date_from:
            return False
        if self.filing_date_to and (chunk.filing_date or "") > self.filing_date_to:
            return False
        if self.latest_only and chunk.is_latest is False:
            return False
        if not self.include_corrections and chunk.is_correction:
            return False
        return True


def filter_chunks(chunks: list[ChunkSchema], flt: RetrievalFilter | None) -> list[ChunkSchema]:
    if flt is None:
        return chunks
    return [c for c in chunks if flt.matches(c)]
