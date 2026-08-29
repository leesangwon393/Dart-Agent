"""SPEC.md §4-1: 회사명 Resolution.

corp_name/listed_name/corp_eng_name/stock_code + alias dictionary 를 모두
합쳐 하나의 alias map 을 만들고, 질문 텍스트에서 겹치지 않는 가장 긴 매칭을
우선하는 방식(longest-alias-first, non-overlapping span)으로 explicit
company mention 을 찾는다.

이 매칭 전략은 기존 시스템(src/disclosure_rag/entity/entity_extractor.py 의
`_extract_companies`)에서 이미 검증된 패턴이다 — "LLM이 회사명을 임의 추론하는
것보다 deterministic lookup을 우선한다"(SPEC §4-1)는 원칙을 그대로 따르되,
`new/`는 그 파일을 import하지 않고 독립적으로 재구현했다.
"""

from __future__ import annotations

from app.company.aliases import MANUAL_ALIASES
from app.company.repository import CompanyMasterRepository, CompanyRecord, normalize_nfc


class CompanyResolver:
    def __init__(
        self,
        repository: CompanyMasterRepository,
        *,
        extra_aliases: dict[str, str] | None = None,
    ):
        self._repository = repository

        alias_to_canonical: dict[str, str] = {}
        for record in repository.all():
            for key in filter(None, [record.corp_name, record.listed_name, record.corp_eng_name, record.stock_code]):
                key_nfc = normalize_nfc(key)
                # corp_name 자체가 canonical 이므로, 짧은 stock_code 나 영문명이
                # 우연히 다른 회사 corp_name 과 겹치는 경우는 실질적으로 없지만
                # (universe.csv 70개 회사 검증됨), 먼저 등록된 값을 우선한다.
                alias_to_canonical.setdefault(key_nfc, record.corp_name)

        aliases = dict(MANUAL_ALIASES)
        if extra_aliases:
            aliases.update(extra_aliases)
        for alias, canonical in aliases.items():
            alias_to_canonical[normalize_nfc(alias)] = normalize_nfc(canonical)

        self._alias_to_canonical = alias_to_canonical
        # 긴 alias 를 먼저 매칭해야 "SK하이닉스" 매칭 시 "SK"만 잘못 잡는 일이
        # 없다(longest-match-first).
        self._sorted_aliases = sorted(alias_to_canonical, key=len, reverse=True)

    def find_explicit_company_spans(self, text: str) -> list[tuple[int, int, str]]:
        """(start, end, canonical_corp_name) 튜플의 리스트를 문장 내 등장 순서로
        반환한다. 서로 겹치는 매칭은 먼저(왼쪽에서) 찾은 것을 우선한다."""
        text_nfc = normalize_nfc(text)
        spans: list[tuple[int, int, str]] = []
        for alias in self._sorted_aliases:
            if not alias:
                continue
            start = 0
            while True:
                idx = text_nfc.find(alias, start)
                if idx == -1:
                    break
                end = idx + len(alias)
                overlaps = any(not (end <= s or idx >= e) for s, e, _ in spans)
                if not overlaps:
                    spans.append((idx, end, self._alias_to_canonical[alias]))
                start = idx + 1
        return sorted(spans, key=lambda s: s[0])

    def find_explicit_companies(self, text: str) -> list[CompanyRecord]:
        """텍스트에 명시적으로 등장한 회사를 CompanyRecord 리스트로 반환한다
        (등장 순서, 중복 제거)."""
        spans = self.find_explicit_company_spans(text)
        seen: list[str] = []
        for _s, _e, canonical in spans:
            if canonical not in seen:
                seen.append(canonical)
        records: list[CompanyRecord] = []
        for name in seen:
            record = self._repository.find_by_name(name)
            if record is not None:
                records.append(record)
        return records
