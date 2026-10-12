# 데이터 확보 및 로깅 설계

샘플 CSV 5종(`api_registry`, `consumer_system`, `api_daily_stats`, `api_call_log`, `expected_detections`)과 **같은 형식의 실데이터**를 확보하는 방법, 그리고 수집 파이프라인 **단계별로 무엇을 기록·보존할지**를 정의한다. 컬럼 정의는 [`SPEC.md` 3장](SPEC.md#3-데이터-양식)이 기준이다.

> 이 문서는 설계 가이드다. 게이트웨이별 설정 키·변수명은 제품/버전에 따라 다르므로 적용 전 해당 버전 문서로 확인한다.

## 1. 한눈에 보기

| 샘플 파일 | 실환경 원천(Source) | 확보 방식 | 갱신 주기 |
| --- | --- | --- | --- |
| `api_call_log.csv` | API Gateway 액세스 로그 (APISIX · Kong · webMethods) | 게이트웨이 로그 플러그인/이벤트 → 메시지 큐 → 정규화 → 로그 저장소 | 준실시간 |
| `api_daily_stats.csv` | `api_call_log` 전량 | 일 배치 집계 (샘플링 전 원본 기준) | 일 1회 |
| `api_registry.csv` | API Registry/포털 + OpenAPI 명세 + Gateway Admin API | 기준정보 동기화 + Gateway 설정 수집 | 일 1회 이상 |
| `consumer_system.csv` | 소비자 식별 체계(Gateway Consumer/클라이언트 등록) + 시스템 기준정보 | 매핑 테이블 관리 | 변경 시 |
| `expected_detections.csv` | 없음 (합성 데이터 전용 정답지) | 실데이터에서는 **수기 검증 셋**으로 대체 | — |

핵심 원칙: **대시보드가 쓰는 건 집계(`api_daily_stats`)이고, 집계의 품질은 원천 로그 정규화(특히 `api_id` 매핑)가 좌우한다.** 정규화 단계를 가장 엄격히 기록·검증한다.

## 2. 전체 파이프라인과 단계별 기록

```text
[1 Gateway]──▶[2 전송]──▶[3 정규화]──▶[4 로그 저장소]──▶[5 일 집계]──▶[6 PostgreSQL]──▶[7 탐지 배치]──▶ 대시보드
   │              │          │              │                │               │               │
 원본 로그      전송 지표    매핑/실패 기록   보존·접근 기록    배치 실행 이력   적재 이력       탐지 실행 이력
                                                                    ▲
[0 Registry/Consumer 동기화] ───────────────────────────────────────┘
```

각 단계가 **남겨야 하는 운영 기록(메타 로그)** 을 아래에 정의한다. 원칙은 "데이터 자체"와 "그 데이터가 만들어진 과정 기록"을 분리하는 것이다.

### 2.1 단계 0 — Registry·Consumer 동기화

| 항목 | 내용 |
| --- | --- |
| 입력 | Registry/포털 DB, OpenAPI 명세 저장소, Gateway Admin API(라우트·플러그인 설정) |
| 출력 | `api_registry`, `consumer_system` |
| 기록할 정보 | `sync_run_id`, 시작/종료 시각, 원천별 조회 건수, 신규/변경/삭제 건수, **변경 전후 값**(특히 `lifecycle_status`, `owner_*`, `rate_limit_rpm`, `gw_rate_limit_rpm`), 원천 간 불일치 건수 |
| 이유 | 정책 불일치·소유자 공란 탐지가 이 단계 데이터에 의존. 값이 언제 바뀌었는지 남겨야 오탐/미탐 원인을 추적 가능 |

### 2.2 단계 1 — Gateway 원본 로그 생성

게이트웨이에서 **요청 1건당 1레코드**를 구조화(JSON)로 내보낸다. 본문·헤더·토큰 원문은 **내보내지 않는다**(마스킹이 아니라 수집 제외).

| 샘플 컬럼 | 원천 정보(개념) | 비고 |
| --- | --- | --- |
| `timestamp` | 요청 수신 시각 | UTC로 통일, ms 정밀도 |
| `http_method` | 요청 메서드 | |
| `path_template` | 요청 URI | 원본 경로 `/work-orders/123`을 **라우트 정의 경로**(`/work-orders/{id}`)로 치환해야 함 → 3단계 |
| `consumer_client_id` | Gateway Consumer / OAuth client_id / API 키 식별자 | 키 원문 금지, 식별자만 |
| `subject_hash` | JWT `sub` 등 사용자 식별자 | **게이트웨이 또는 정규화 단계에서 SHA-256 후 앞 16자리**, 원문 저장 금지 (솔트/키 관리 별도) |
| `status_code` | 최종 응답 코드 | |
| `latency_total_ms` | 요청 전체 소요 시간 | |
| `latency_upstream_ms` | 업스트림(백엔드) 응답 시간 | 차이 = GW 오버헤드 |
| `trace_id` | W3C `traceparent` 등 분산 추적 ID | 없으면 게이트웨이가 생성 |
| `gateway`, `gateway_node` | 제품 식별, 노드 호스트/ID | 정적 값 주입 |
| `request_bytes`, `response_bytes` | 요청/응답 크기 | |
| `auth_result` | 인증·인가 판정 | `ALLOW`/`DENY`, 플러그인 결과를 매핑 |

| 게이트웨이 | 로그 내보내기 수단(예) | 참고 |
| --- | --- | --- |
| Apache APISIX | `http-logger` / `kafka-logger` / `file-logger` 플러그인, 로그 포맷 커스터마이즈 | 요청 시간·업스트림 응답 시간·consumer 등 변수를 로그 포맷에 포함 |
| Kong | `http-log` / `file-log` / Kafka 로그 플러그인 | 로그 페이로드의 `latencies`(request / proxy / kong), `consumer`, `route`, `service` 사용 |
| webMethods API Gateway | 트랜잭션 이벤트 로깅, 외부 대상(예: Elasticsearch, Kafka) 발행 설정 | 이벤트 필드명이 달라 별도 매핑표 필요 |

기록할 정보(운영 메타): 로그 플러그인 **활성화 여부와 설정 버전**, 노드별 로그 포맷 버전(`log_schema_version`). 포맷이 바뀌면 3단계 매핑이 깨지므로 버전을 로그 레코드에 함께 싣는다.

### 2.3 단계 2 — 전송(Gateway → 큐/수집기)

| 항목 | 내용 |
| --- | --- |
| 구성 | Gateway 로그 플러그인 → Kafka(또는 Fluent Bit/Vector) → 정규화 워커 |
| 장애 격리 | 전송 실패가 **API 응답에 영향 없도록** 비동기·버퍼링·드롭 허용. 대시보드는 읽기 전용이며 Gateway 운영에 영향을 주면 안 된다 |
| 기록할 정보 | 노드별 **전송 건수/실패 건수/드롭 건수**, 큐 지연(lag), 마지막 수신 시각 — 이 수치로 "로그 누락"과 "호출 없음"을 구분 |
| 비교 기준 | Gateway 자체 요청 카운터(메트릭) vs 수집된 로그 건수 → 일 단위 **수집 완전성 비율** |

### 2.4 단계 3 — 정규화 (가장 중요)

원본 로그를 샘플 스키마로 변환한다.

| 처리 | 규칙 | 실패 시 |
| --- | --- | --- |
| 시간 | UTC ISO-8601 ms로 통일 | 파싱 실패 → 격리(dead-letter) |
| **`api_id` 매핑** | (`gateway`, 라우트 ID 또는 `http_method`+정규화 경로)로 Registry 조회 | 매핑 실패 시 **`UNKNOWN-{해시/순번}`** 부여 → `UNREGISTERED` 탐지 대상. 버리지 않는다 |
| 경로 정규화 | 라우트 정의 기준 경로 템플릿 사용. 라우트가 없는 요청은 숫자·UUID 세그먼트를 `{id}`로 치환하는 휴리스틱 + 쿼리스트링 제거 | 카디널리티 폭증 방지 |
| 소비자 매핑 | `consumer_client_id` → `consumer_system` (매핑 테이블) | 미매핑은 `UNMAPPED`로 두고 건수 기록 (임의 추정 금지) |
| 개인정보 | `subject_hash` 생성, 토큰/키/헤더/바디 필드 제거 | 원문이 감지되면 레코드 격리 + 알람 |
| 값 검증 | `latency_upstream_ms < latency_total_ms`, 상태코드 범위, 음수/비정상 큰 값 | 위반 건 격리, 사유 코드 기록 |
| 중복 제거 | `trace_id`+`timestamp`+`gateway_node` 기준 | 중복 건수 기록 |

기록할 정보(처리 배치/스트림 단위, 예: 시간당 1행):

| 필드 | 설명 |
| --- | --- |
| `window_start`, `window_end` | 처리 구간 |
| `records_in` / `records_out` | 입력/정상 출력 건수 |
| `records_dead_letter` + `reason_code` 분포 | 격리 건수와 사유(PARSE_ERROR, PII_DETECTED, INVALID_LATENCY …) |
| `unknown_api_count` | `UNKNOWN-*`로 분류된 건수 (급증 = Registry 누락 또는 매핑 룰 결함) |
| `unmapped_consumer_count` | 소비자 미매핑 건수 |
| `registry_snapshot_version` | 매핑에 사용한 Registry 스냅샷 (재처리 재현성) |
| `normalizer_version`, `log_schema_version` | 코드/포맷 버전 |

### 2.5 단계 4 — 로그 저장소 (원천 로그)

| 항목 | 내용 |
| --- | --- |
| 저장소 | OpenSearch 또는 컬럼형 DB (원본 스펙 기준). 데모에서는 PostgreSQL `api_call_log` |
| 보존 | 30~90일. 일 집계 완료·검증 후 만료 |
| 샘플링 | 샘플 CSV는 1.2% 샘플이지만 **운영에서는 샘플링하지 않고 전량 저장** 후, 용량 제약이 있을 때만 정상(2xx) 건 샘플링 + **오류(4xx/5xx)·DENY는 전량 보존** |
| 기록할 정보 | **조회 감사 로그**: 조회자, 조회 조건(`api_id`, 상태코드, 소비자, limit), 시각, 반환 건수 (`audit_log.action='CALL_LOG_QUERY'` 형식) |
| 접근 통제 | 원천 로그 조회는 권한자만, 모든 조회는 감사 기록 |

### 2.6 단계 5 — 일 집계 배치

`api_call_log`(전량) → `api_daily_stats`. 키: `(stat_date, api_id, consumer_system)`.

```sql
-- 예시: 전일(UTC 기준 또는 KST 기준 — 하나로 고정) 집계
SELECT
  date_trunc('day', ts)::date                    AS stat_date,
  api_id, consumer_system, gateway,
  count(*)                                       AS call_count,
  count(*) FILTER (WHERE status_code >= 400)     AS error_count,
  count(*) FILTER (WHERE status_code = 401)      AS count_401,
  count(*) FILTER (WHERE status_code = 403)      AS count_403,
  count(*) FILTER (WHERE status_code = 404)      AS count_404,
  count(*) FILTER (WHERE status_code = 429)      AS count_429,
  percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_total_ms)::int AS p50_ms,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_total_ms)::int AS p95_ms,
  percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_total_ms)::int AS p99_ms,
  avg(latency_total_ms)::int                     AS avg_ms,
  avg(request_bytes)::int                        AS req_bytes_avg,
  avg(response_bytes)::int                       AS res_bytes_avg
FROM api_call_log
WHERE ts >= :day AND ts < :day + interval '1 day'
GROUP BY 1,2,3,4;
```

- **백분위는 원본 로그에서 직접 계산**한다(샘플링 데이터 금지, 일별 백분위의 단순 평균 금지). 대시보드의 기간 합성은 호출 가중 평균 **근사**라는 점을 명시한다([`SPEC.md` 3.4](SPEC.md#34-api_daily_statscsv--일-단위-집계-52821행)).
- 호출이 없는 조합은 **행을 만들지 않는다**(샘플과 동일, 0과 결측 구분).
- `error_count` 정의(4xx+5xx 전체)를 고정하고 문서화한다. 샘플 데이터는 이 정의와 독립 생성이라 일치하지 않는 행이 있으므로, 실데이터에서는 `error_count ≥ count_401+403+404+429` 검증을 품질 검사에 넣는다.
- **재실행 가능(idempotent)**: 일자 단위 DELETE+INSERT 또는 UPSERT.

기록할 정보(배치 1회당 1행, 예: `batch_run_log`):

| 필드 | 설명 |
| --- | --- |
| `batch_name`, `run_id`, `stat_date` | 어떤 배치가 어느 일자를 처리했는지 |
| `started_at`, `finished_at`, `status` | SUCCESS / FAILED / PARTIAL |
| `source_row_count` | 입력 로그 건수 (2.3의 Gateway 카운터와 대조) |
| `output_row_count` | 생성된 집계 행 수 |
| `sum_call_count` | 집계 `call_count` 합계 — **`source_row_count`와 반드시 일치** |
| `error_message` | 실패 사유 |
| `rerun_of` | 재실행 시 원 `run_id` |

실패 시 전일 데이터를 계속 조회하되 화면의 **데이터 기준일 경고**(`dataStale`)가 켜지도록 `max(stat_date)`를 정직하게 둔다.

### 2.7 단계 6 — PostgreSQL 적재

| 항목 | 내용 |
| --- | --- |
| 방식 | 집계/Registry를 스테이징 테이블에 적재 후 검증 통과 시 교체/병합 (샘플 로더는 빈 테이블에만 `COPY`하므로 운영 로더는 증분 UPSERT로 확장 필요) |
| 기록할 정보 | `load_run_id`, 대상 테이블, 적재 전후 행 수, 거부(reject) 행 수와 사유, 파일 체크섬, 적재 소요 시간 |

### 2.8 단계 7 — 탐지 배치

| 항목 | 내용 |
| --- | --- |
| 결과 | `detection_result` (규칙, 근거 `evidence`, 최초/최근 탐지 시각, 상태) |
| 기록할 정보 | 규칙별 **실행 이력**: 실행 시각, 규칙 코드, 입력 기준일, 후보 건수, 신규/갱신/억제(중복) 건수, 소요 시간, 실패 사유. 현재 구현은 건수만 반환하므로 별도 테이블(`detection_run_log`) 추가를 권장 |
| 조치 기록 | 상태 변경은 `audit_log`(`DETECTION_STATUS`)에 **변경 전 상태·후 상태·사유·조치자** 포함 (현재 구현은 변경 후 상태만 기록 — 개선 포인트) |

## 3. 어디서 데이터를 얻는가 — 확보 시나리오

| 단계 | 방법 | 장점 | 한계 |
| --- | --- | --- | --- |
| A. 합성 데이터 (현재) | `gen_testdata.py`로 생성 | 정답지 포함, 재현 가능, 보안 이슈 없음 | 실제 분포 아님 |
| B. 비운영 환경 실로그 | Dev/QA Gateway에 로그 플러그인 활성화, 테스트 트래픽 수집 | 실제 포맷 검증, 위험 낮음 | 트래픽 규모·패턴이 다름 |
| C. 운영 로그 일부(1~2개 도메인) | 선택한 라우트만 로그 플러그인 활성화, **샘플링→전량** 단계 확장 | 영향 범위 통제 | 라우트 선별 필요 |
| D. 운영 전량 | 전체 Gateway 노드 적용 | 완전한 가시성 | 성능·보안 심의 필요 |

권장 순서: **A → B → C → D**. 각 단계 진입 조건으로 아래 [품질 검사](#4-품질-검사-게이트)를 통과시킨다.

### 3.1 기존 로그 자산 활용

새 수집 체계 없이 시작하려면 이미 쌓이는 로그를 변환한다.

- Gateway 액세스 로그 파일(nginx/Kong 로그 등) → 정규식/Grok으로 파싱 → 3단계 정규화 규칙 적용
- 이미 ELK/OpenSearch로 모이는 로그가 있으면 그 인덱스를 원천으로 사용 (필드 매핑표만 작성)
- APM/트레이싱 시스템이 있으면 `trace_id`와 `latency_upstream_ms` 보완에 활용

변환 시 **원본 보존**(Raw 영역)을 유지하고 변환 결과만 샘플 스키마로 만든다. 매핑 오류 시 원본에서 재처리할 수 있어야 한다.

### 3.2 Registry 확보

| 컬럼군 | 원천 | 확보 방법 |
| --- | --- | --- |
| `api_id`, 경로, 메서드, 버전, 도메인 | API 포털/Registry, OpenAPI 명세 | 명세 저장소 크롤링 + 포털 DB 조회 |
| `provider_system`, 소유자 | CMDB, 조직/인사 시스템 | 시스템 코드·조직 매핑 |
| `lifecycle_status`, `deprecate_date`, `criticality` | 거버넌스 기준정보 | 담당 조직 입력 (없으면 공란 → `OWNER_MISSING` 등으로 탐지되는 것이 정상 동작) |
| `rate_limit_rpm`(Registry 기재) vs `gw_rate_limit_rpm`(실제) | Registry vs Gateway Admin API(rate-limit 플러그인 설정) | **두 원천을 각각 수집**해야 불일치 탐지 가능 |
| `slo_*` | 서비스 수준 합의서/명세 | 없으면 공란(SLO 정의율에 반영됨) |
| 미등록 API | Registry에는 없고 Gateway/로그에만 존재 | 3단계에서 `UNKNOWN-*`로 흡수 |

Gateway Admin API로 **실제 라우트 목록**을 주기적으로 수집해 Registry와 비교하면, 호출이 아직 없는 미등록 라우트까지 찾을 수 있다(로그 기반 탐지의 보완).

### 3.3 `expected_detections.csv`의 실데이터 대체

실데이터에는 정답지가 없다. 대신:

1. 탐지 결과 상위 N건을 도메인 담당자가 **수기 검증** (정탐/오탐 라벨링)
2. 라벨을 `detection_result.status`/`comment`와 별도 **검증 셋**(`rule_code, api_id, verdict, reviewer, reviewed_at`)으로 보관
3. 규칙별 정밀도(정탐/탐지 건수)를 월 단위로 추적, 임계치(예: 50/일, 3.0배) 튜닝 근거로 사용

## 4. 품질 검사 게이트

단계 전환 전에 자동 검사하고 결과를 단계 기록(2장)에 남긴다.

| # | 검사 | 기준(예) | 실패 시 |
| --- | --- | --- | --- |
| 1 | 수집 완전성 | 수집 로그 건수 / Gateway 카운터 ≥ 99.5% | 로그 누락 경고, 해당 일자 `PARTIAL` 표시 |
| 2 | 집계 합계 일치 | Σ`call_count` = 정규화 후 로그 건수 | 배치 실패 처리 |
| 3 | `UNKNOWN-*` 비율 | 일 변동 급증 시 알람 | 매핑 룰/Registry 점검 |
| 4 | 소비자 미매핑 비율 | 임계 초과 시 알람 | 매핑 테이블 보완 |
| 5 | 지연 일관성 | `latency_upstream_ms < latency_total_ms` | 위반 건 격리 |
| 6 | 상태코드 분포 | 급격한 5xx 비율 변화 | 수집 오류와 실제 장애 구분 |
| 7 | 개인정보 | 토큰/이메일/주민번호 패턴 0건 | 즉시 격리·보고 |
| 8 | 백분위 순서 | `p50 ≤ p95 ≤ p99` | 집계 오류 |
| 9 | 기준일 신선도 | `max(stat_date) = 어제` | `dataStale` 경고 |
| 10 | 중복 | 키 `(stat_date, api_id, consumer_system)` 유일 | 재집계 |

## 5. 보안·개인정보·시간대 원칙

- **수집 제외**: 요청/응답 본문, 헤더, Authorization 토큰, API 키 원문, 쿼리스트링 값. 경로 변수의 개인 식별자(예: 사번이 경로에 포함)가 있다면 `{id}` 치환 필수.
- **`subject_hash`**: 일방향 해시. 솔트/키는 별도 보관, 개인 식별이 필요한 경우 별도 승인 절차.
- **시간대**: 저장은 UTC, 일 집계 경계(UTC vs KST)는 **하나로 고정**해 문서화. KST 업무일 기준으로 보려면 집계 단계에서 KST 일자로 잘라 `stat_date`를 만든다(샘플 데이터는 UTC 타임스탬프).
- **접근 통제와 감사**: 원천 로그 조회·CSV 내보내기·탐지 상태 변경은 모두 `audit_log`에 남긴다.
- **보존/삭제**: 원천 30~90일, 집계 13개월, 메타 로그(배치·정규화 이력)는 최소 13개월(집계와 동일)로 보존해 과거 수치의 근거를 추적 가능하게 한다.

## 6. 기록 항목 요약 (단계 × 정보)

| 단계 | 남기는 기록 | 저장 위치(권장) |
| --- | --- | --- |
| 0 동기화 | 실행 이력, 변경 전후 값, 원천 간 불일치 | `sync_run_log`, `registry_change_log` |
| 1 Gateway | 로그 포맷/플러그인 설정 버전 | 설정 저장소(Git) |
| 2 전송 | 노드별 전송·실패·드롭 건수, 큐 lag | 메트릭 시스템 |
| 3 정규화 | 입출력/격리 건수, 사유 코드, `UNKNOWN`·미매핑 건수, 스냅샷·코드 버전 | `normalize_run_log`, dead-letter 저장소 |
| 4 로그 저장소 | 조회 감사 (조회자·조건·시각·건수) | `audit_log` |
| 5 집계 | 입력/출력 행 수, 합계 대조, 상태, 재실행 관계 | `batch_run_log` |
| 6 적재 | 적재 전후 행 수, reject 사유, 체크섬 | `load_run_log` |
| 7 탐지 | 규칙별 후보/신규/갱신/억제 건수, 상태 변경 전후 | `detection_run_log`, `audit_log` |

최소 시작안: **`batch_run_log`(5단계)와 `normalize_run_log`(3단계) 두 가지만 먼저 도입**해도 "수치가 맞는가"와 "왜 미등록이 늘었는가" 두 질문에 답할 수 있다.

## 7. 샘플 CSV에서 실데이터로 전환하는 체크리스트

- [ ] Gateway 로그 포맷과 3단계 매핑표 작성 (제품별)
- [ ] 라우트 ↔ `api_id` 매핑 규칙 확정, 미매핑은 `UNKNOWN-*`로 흡수
- [ ] 클라이언트 ID ↔ `consumer_system` 매핑 테이블 구축
- [ ] 개인정보 수집 제외 규칙과 `subject_hash` 생성 위치 확정
- [ ] 일 집계 시간대(UTC/KST)와 `error_count` 정의 확정
- [ ] 단계별 기록 테이블(최소 `batch_run_log`, `normalize_run_log`) 생성
- [ ] 품질 검사 게이트 자동화
- [ ] 비운영 → 일부 도메인 → 전량 순으로 확대
- [ ] 로더를 빈 테이블 전용 `COPY`에서 증분 UPSERT로 확장
- [ ] 탐지 결과 수기 검증 셋 운영 시작
