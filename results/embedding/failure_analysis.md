# Stage 3 — Dense Embedding: Failure Analysis + 제외 사유

## Qwen/Qwen3-Embedding-0.6B 제외 (실험 미완료)
사용자 조건에 "기본 세 후보"로 명시됐으나, 이 환경에서 다음을 전부 시도했음에도
끝내 정상 사용이 불가능했다:
1. `SentenceTransformer(...)` 로 최초 로드 시도 → HuggingFace CDN(xet) 403 Forbidden.
2. `HF_HUB_DISABLE_XET=1` 로 `snapshot_download` 재시도 → 다운로드는 진행됐으나
   특정 파일에서 무한정 정체(다른 두 모델은 같은 조건에서 정상 완료).
3. 개별 파일 단위 `hf_hub_download` 시도 → 역시 정체.
4. 파일 크기가 완전해 보인 뒤 `SentenceTransformer` 로 로드 테스트 → 10분+ 응답
   없음(같은 크기의 e5-large 는 로드에 10.5초 걸림), 강제 종료.

원인은 이 환경의 네트워크/HF CDN 특이 이슈로 추정되며, 재현 가능한 코드 버그는
아니다. **사용자 지정 조건("성능이 비슷하거나 추가 실험 가치가 있을 때만
Qwen3-Embedding-4B 등 추가 후보 실행")에 준해, 이미 실행 불가가 확정된 3개
기본 후보 중 하나이므로 나머지 2개(BGE-M3, e5-large-instruct)만으로 Stage 3
결론을 낸다.** 향후 GPU 환경이나 다른 네트워크 조건에서 재시도 가치 있음.

## BGE-M3 vs e5-instruct 실패 사례
두 모델 다 Stage 1/2 와 동일한 근본 원인(유형 A: periodic 문서 과다 매칭,
유형 B: 정정 체인 일부만 검색)을 공유한다. e5-instruct 가 실패 6건으로
bge-m3 의 8건보다 적어(=더 잘 찾음) 정확도 우위를 보이는데, 특히 유형 A(broad
comparison/trend 질의)에서 e5 가 상대적으로 더 강건했다 — instruction-tuned
embedding 이 "비교", "추이" 같은 의도 표현을 bge-m3 보다 잘 해석하는 것으로
보인다.

## 결론
정확도: e5-instruct > bge-m3 (모든 지표에서 근소하지 않은 차이).
속도: bge-m3 > e5-instruct (591.7ms/chunk vs 3710.0ms/chunk, **6.3배**).
45만 chunk 전체 코퍼스로 환산 시 bge-m3 ≈ 73시간, e5-instruct ≈ 463시간(19일) —
이 CPU-only 환경에서 e5-instruct 로 전체 인덱싱은 사실상 불가능하다.
"성능 차이가 미미하면 자원을 우선한다"는 원칙의 반대 상황(성능 차이는 크지만
자원 차이가 극단적)이므로, **실무 배포 기준으로는 BGE-M3 를 baseline 으로
채택**하고, e5-instruct 는 "정확도 상한 참고용"으로 기록한다. GPU 확보 시
e5-instruct 재검토 권장.
