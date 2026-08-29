"""SPEC.md §3, §25: CompanyMasterRepository.

corpus/universe.csv 를 로드해 Company Master 를 "핵심 Entity Registry"로
사용한다(§3). 실제 DART 공시 corpus 는 이번 Phase 1 에서 필요 없다.

기존 시스템(src/disclosure_rag/common/manifest_loader.py)의 두 가지 검증된
패턴을 참고해 새로 구현했다(재발명 대신 재사용, import는 하지 않음):
  - corp_code/stock_code 는 선행 0 이 있는 문자열이므로 반드시 str 로 로딩.
  - 한글 문자열은 로딩 시점에 NFC 정규화(플랫폼별 NFC/NFD 혼재 방어).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


def normalize_nfc(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    return unicodedata.normalize("NFC", value)


def _to_optional_str(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    return s if s else None


def _to_optional_int(value) -> int | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_optional_float(value) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class CompanyRecord:
    """universe.csv 한 행. 스펙 §3 컬럼과 1:1 대응."""

    corp_code: str
    stock_code: str | None
    corp_name: str
    listed_name: str | None
    corp_eng_name: str | None
    market: str | None
    industry: str | None
    sector_no: int | None
    sector: str | None
    listing_date: str | None
    fiscal_month: str | None
    market_cap: float | None
    n_periodic: int
    n_major: int
    n_exchange: int
    n_holding: int
    note: str | None


REQUIRED_COLUMNS = [
    "corp_code", "stock_code", "corp_name", "listed_name", "corp_eng_name",
    "market", "industry", "sector_no", "sector", "listing_date",
    "fiscal_month", "market_cap", "n_periodic", "n_major", "n_exchange",
    "n_holding", "note",
]


class CompanyMasterRepository:
    """Company Master CSV 를 로드하고 조회/필터/정렬을 제공하는 Entity Registry."""

    def __init__(self, csv_path: str | Path):
        path = Path(csv_path)
        if not path.is_file():
            raise FileNotFoundError(f"universe.csv 를 찾을 수 없습니다: {path}")

        df = pd.read_csv(path, dtype={"corp_code": str, "stock_code": str})
        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"universe.csv 에 필요한 컬럼이 없습니다: {missing}")

        records: list[CompanyRecord] = []
        for _, row in df.iterrows():
            records.append(
                CompanyRecord(
                    corp_code=str(row["corp_code"]).strip(),
                    stock_code=_to_optional_str(row["stock_code"]),
                    corp_name=normalize_nfc(str(row["corp_name"]).strip()),
                    listed_name=normalize_nfc(_to_optional_str(row["listed_name"])),
                    corp_eng_name=normalize_nfc(_to_optional_str(row["corp_eng_name"])),
                    market=_to_optional_str(row["market"]),
                    industry=normalize_nfc(_to_optional_str(row["industry"])),
                    sector_no=_to_optional_int(row["sector_no"]),
                    sector=normalize_nfc(_to_optional_str(row["sector"])),
                    listing_date=_to_optional_str(row["listing_date"]),
                    fiscal_month=_to_optional_str(row["fiscal_month"]),
                    market_cap=_to_optional_float(row["market_cap"]),
                    n_periodic=_to_optional_int(row["n_periodic"]) or 0,
                    n_major=_to_optional_int(row["n_major"]) or 0,
                    n_exchange=_to_optional_int(row["n_exchange"]) or 0,
                    n_holding=_to_optional_int(row["n_holding"]) or 0,
                    note=_to_optional_str(row["note"]),
                )
            )

        self._records: list[CompanyRecord] = records
        self._by_corp_code: dict[str, CompanyRecord] = {r.corp_code: r for r in records}
        self._by_stock_code: dict[str, CompanyRecord] = {
            r.stock_code: r for r in records if r.stock_code
        }
        # corp_name/listed_name/corp_eng_name 전부 exact-match lookup 대상.
        # 대소문자 무시(영문사명 대응)를 위해 key 는 lower() 로 저장.
        by_name: dict[str, CompanyRecord] = {}
        for r in records:
            for key in filter(None, [r.corp_name, r.listed_name, r.corp_eng_name]):
                by_name.setdefault(key.lower(), r)
        self._by_name = by_name

    # ------------------------------------------------------------------
    # 단건 조회
    # ------------------------------------------------------------------
    def all(self) -> list[CompanyRecord]:
        return list(self._records)

    def get_by_corp_code(self, corp_code: str) -> CompanyRecord | None:
        return self._by_corp_code.get(corp_code)

    def get_by_stock_code(self, stock_code: str) -> CompanyRecord | None:
        return self._by_stock_code.get(stock_code)

    def find_by_name(self, name: str) -> CompanyRecord | None:
        """corp_name / listed_name / corp_eng_name / stock_code 중 하나와
        exact match(대소문자 무시)하는 회사를 찾는다."""
        normalized = normalize_nfc(name)
        if not normalized:
            return None
        if normalized in self._by_stock_code:
            return self._by_stock_code[normalized]
        return self._by_name.get(normalized.lower())

    # ------------------------------------------------------------------
    # 그룹 필터 (§5 sector/industry 활용)
    # ------------------------------------------------------------------
    def sector_names(self) -> list[str]:
        seen: list[str] = []
        for r in self._records:
            if r.sector and r.sector not in seen:
                seen.append(r.sector)
        return seen

    def industry_names(self) -> list[str]:
        seen: list[str] = []
        for r in self._records:
            if r.industry and r.industry not in seen:
                seen.append(r.industry)
        return seen

    def filter_by_sector(self, sector: str) -> list[CompanyRecord]:
        return [r for r in self._records if r.sector == sector]

    def filter_by_sector_no(self, sector_no: int) -> list[CompanyRecord]:
        return [r for r in self._records if r.sector_no == sector_no]

    def filter_by_industry(self, industry: str) -> list[CompanyRecord]:
        return [r for r in self._records if r.industry == industry]

    # ------------------------------------------------------------------
    # "주요 기업" = market_cap 상위 N (§5). peer_selector.py 도 이 메서드를 씀.
    # ------------------------------------------------------------------
    def top_n_by_market_cap(
        self, records: list[CompanyRecord] | None = None, n: int = 3,
    ) -> list[CompanyRecord]:
        pool = self._records if records is None else records
        ranked = sorted(pool, key=lambda r: (r.market_cap or 0.0), reverse=True)
        return ranked[:n]
