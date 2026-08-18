"""BM25 용 Tokenizer 추상화 (§32, §74: whitespace / Kiwi / char n-gram 비교 가능해야 함).

baseline 은 Kiwi(kiwipiepy) 를 사용한다. 금융 전문용어가 형태소 분석기 기본
사전에 없어 엉뚱하게 쪼개지는 문제를 막기 위해 사용자 사전(config/financial_terms.txt)
을 로딩해 확장 가능하게 한다 (§32).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

# BM25 키워드로 남길 품사: 체언(명사류) + 어근 + 외국어/한자/숫자. 조사/어미/부호는 제외.
_DEFAULT_KEEP_TAGS = {"NNG", "NNP", "NNB", "NR", "NP", "SL", "SH", "SN", "XR"}


class Tokenizer(Protocol):
    name: str

    def tokenize(self, text: str) -> list[str]: ...

    # `tokenize_batch` 는 선택적(optional) 메서드다 — 있으면 BM25Retriever가
    # 대량 코퍼스 인덱싱 시 우선 사용한다(getattr 로 구조적 체크, Protocol
    # 이라 실제 구현은 강제 안 됨). KiwiTokenizer 만 실질적인 배치 구현을
    # 갖고 있다: kiwipiepy 의 멀티스레드 배치 API 를 써서 훨씬 빠르다(실측,
    # 2026-08-18: 237,212 chunk 기준 순차 호출은 45분+ 걸려도 안 끝났고,
    # 배치(num_workers=-1)는 ~4분). WhitespaceTokenizer/CharNgramTokenizer는
    # 순수 Python 문자열 연산이라 이미 충분히 빨라서 배치 구현이 따로 없다
    # — 그 경우 BM25Retriever 가 순차 호출로 자동 폴백한다.
    def tokenize_batch(self, texts: list[str]) -> list[list[str]]: ...


class WhitespaceTokenizer:
    """가장 단순한 baseline. 비교 실험용."""

    name = "whitespace"

    def tokenize(self, text: str) -> list[str]:
        return text.split()


class CharNgramTokenizer:
    """형태소 분석기 없이도 동작하는 대안. 한글 조사 변화에 어느 정도 강건하다."""

    def __init__(self, n: int = 2):
        self.n = n
        self.name = f"char_{n}gram"

    def tokenize(self, text: str) -> list[str]:
        cleaned = "".join(text.split())
        if len(cleaned) < self.n:
            return [cleaned] if cleaned else []
        return [cleaned[i:i + self.n] for i in range(len(cleaned) - self.n + 1)]


class KiwiTokenizer:
    """baseline tokenizer. kiwipiepy 형태소 분석 + 금융 용어 사용자 사전."""

    name = "kiwi"

    def __init__(
        self,
        *,
        user_dict_path: str | Path | None = None,
        keep_tags: set[str] | None = None,
    ):
        from kiwipiepy import Kiwi  # 무거운 import 는 실제 사용 시점에

        # num_workers=-1: 가용 코어 전부 써서 멀티스레드 분석(kiwipiepy 자체
        # 지원). 단일 문자열 tokenize() 호출에도 문제없이 동작함을 확인(실측).
        self._kiwi = Kiwi(num_workers=-1)
        self._keep_tags = keep_tags or _DEFAULT_KEEP_TAGS
        if user_dict_path is not None:
            self._load_user_dict(Path(user_dict_path))

    def _load_user_dict(self, path: Path) -> None:
        if not path.is_file():
            return
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            word = parts[0].strip()
            tag = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "NNG"
            score = float(parts[2]) if len(parts) > 2 and parts[2].strip() else 0.0
            if word:
                self._kiwi.add_user_word(word, tag, score)

    def tokenize(self, text: str) -> list[str]:
        tokens = self._kiwi.tokenize(text)
        return [t.form for t in tokens if t.tag in self._keep_tags]

    def tokenize_batch(self, texts: list[str]) -> list[list[str]]:
        """`Kiwi.tokenize(list[str])` 에 리스트를 통째로 넘기면 kiwipiepy 가
        내부적으로 멀티스레드(num_workers)로 병렬 분석한다 — 대량 코퍼스
        인덱싱에서 순차 호출 대비 실측 6배+ 빠름(§tokenizers.py 상단 주석)."""
        results = self._kiwi.tokenize(texts)
        return [[t.form for t in doc_tokens if t.tag in self._keep_tags] for doc_tokens in results]


def build_tokenizer(name: str, *, user_dict_path: str | Path | None = None) -> Tokenizer:
    if name == "whitespace":
        return WhitespaceTokenizer()
    if name == "kiwi":
        return KiwiTokenizer(user_dict_path=user_dict_path)
    if name.startswith("char_") and name.endswith("gram"):
        n = int(name[len("char_"):-len("gram")])
        return CharNgramTokenizer(n=n)
    raise ValueError(f"알 수 없는 tokenizer: {name}")
