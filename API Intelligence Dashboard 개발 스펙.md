# API Intelligence Dashboard 개발 스펙

Oct 12, 2026 · 작성 @이창엽 · 기획서 9장 상세화

## 1. 개요 및 개발 범위

기획서 9장의 4개 뷰를 실제 개발 가능한 수준으로 상세화한 스펙이다. 테스트 데이터셋이 함께 제공되므로 **로그 수집 파이프라인(T4) 완성 전에도 대시보드 개발을 선행**할 수 있다.

**다루는 것**

- 대시보드가 읽는 데이터 모델 (Registry·집계·원천 로그)
- 백엔드 조회 API 명세
- 4개 뷰의 화면 명세와 차트 규격
- 탐지 규칙 배치의 입출력 정의
- 개발·검증용 테스트 데이터셋과 정답지

**다루지 않는 것**

- 로그 수집·정규화 파이프라인 구현 (T4 별도 스펙)
- Registry 등록·수정 관리 화면 (T6 별도 스펙)
- 알림 발송 채널 연동 (메일·메신저)

**전제 조건**

1. 모든 호출 로그에 `api_id`가 채워져 있다 — 미채움 건은 `UNKNOWN-*`로 분류해 미등록 탐지 대상으로 처리
2. 집계 테이블은 일 단위 배치로 생성되며 대시보드는 집계만 조회한다 — 원천 로그는 드릴다운 상세 조회에서만 사용
3. 소비자 식별은 `consumer_system` 코드 기준이며, 코드 매핑은 수집 단계에서 완료된다

## 2. 시스템 구성 및 기술 스택

| 구분 | 기술 | 버전 | 비고 |
| --- | --- | --- | --- |
| 프론트엔드 | Vue 3 + TypeScript | Vue 3.4+ | Composition API, `<script setup>` |
| 상태관리 | Pinia | 2.x | 뷰별 store 분리 |
| 차트 | ECharts | 5.x | 대용량 시계열·그래프 모두 지원 |
| 빌드 | Vite + Yarn 4.x | — | MFE Shell 편입 고려해 Module Federation 호환 유지 |
| 백엔드 | Spring Boot | 3.x / Java 21 | Springdoc OpenAPI로 명세 자동 생성 |
| 영속 | MyBatis | 3.5+ | 집계 쿼리 중심, 동적 필터 다수 |
| 기준정보 DB | PostgreSQL | 15+ | Registry + 집계 테이블 |
| 로그 저장소 | OpenSearch (또는 컬럼형 DB) | — | 원천 로그 드릴다운 조회 전용 |
| 캐시 | Redis | 7.x | 대시보드 요약 카드 60초 캐시 |
| 인증 | Keycloak (OIDC) | — | BFF 토큰 핸들러 패턴, 기존 체계 재사용 |

**모듈 구조 (백엔드)**

```markdown
api-intelligence/
├─ domain/
│   ├─ registry/      # API 자산 메타데이터 조회
│   ├─ stats/         # 집계 조회 (호출량·성능·오류)
│   ├─ dependency/    # 소비자→API→제공자 그래프
│   └─ detection/     # 탐지 규칙 실행·결과 조회
├─ batch/             # 일 단위 집계 + 탐지 배치 (common-batch-starter 기반)
├─ api/               # REST 컨트롤러
└─ support/           # 공통 응답·권한·캐시
```

**화면 라우트**

| 경로 | 뷰 |
| --- | --- |
| `/assets` | 자산 현황 |
| `/quality` | 운영 품질 |
| `/dependency` | 연계·의존성 |
| `/governance` | 보안·거버넌스 |
| `/api/{apiId}` | API 상세 (4개 뷰 공통 드릴다운 목적지) |

## 3. 데이터 모델

세 테이블이 `api_id`로 연결된다. 대시보드 조회는 `api_registry` + `api_daily_stats` 조인으로 끝나며, 원천 로그는 상세 드릴다운에서만 읽는다.

### 3.1 `api_registry` — API 기준정보

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `api_id` | varchar(32) PK | 불변 식별자 `{법인}-API-{일련번호}` |
| `path_template` | varchar(255) | 정규화 경로 `/api/v1/prd/work-orders/{id}` |
| `http_method` | varchar(8) | GET·POST·PUT·DELETE |
| `version` | varchar(8) | v1, v2 — ID에 포함하지 않음 |
| `domain_code` / `domain_name` | varchar | 업무 도메인 (PRD, MAT, QUA …) |
| `provider_system` | varchar(32) | 제공 시스템 코드 |
| `owner_org` / `owner_id` | varchar | 소유 조직·담당자 (공란 = 미지정 탐지 대상) |
| `criticality` | smallint | 업무 중요도 1(최상)\~3(하) |
| `lifecycle_status` | varchar(16) | DESIGN·ACTIVE·DEPRECATED·RETIRED |
| `auth_type` | varchar(16) | OAUTH2\_JWT·PAT·MTLS |
| `authz_policy_id` | varchar(16) | 외부 인가 서비스 정책 ID |
| `rate_limit_rpm` | int | Registry 기재값 |
| `gw_rate_limit_rpm` | int | Gateway 실제 설정값 (불일치 탐지용) |
| `slo_p95_ms` | int | P95 응답시간 목표 |
| `slo_availability` | numeric(5,2) | 가용률 목표 % |
| `openapi_url` | varchar(255) | 계약 명세 링크 |
| `gateway` | varchar(16) | APISIX·KONG·WEBMETHODS |
| `registered_at` / `deprecate_date` | date | 등록일 / 폐기 예정일 |

### 3.2 `api_daily_stats` — 일 단위 집계

키: `(stat_date, api_id, consumer_system)`. 보존 13개월.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `stat_date` | date | 집계 일자 |
| `api_id` / `consumer_system` | varchar | 조인 키 |
| `gateway` | varchar(16) | 처리 게이트웨이 |
| `call_count` / `error_count` | bigint | 총 호출 / 5xx·4xx 오류 |
| `count_401` `count_403` `count_404` `count_429` | bigint | 상태코드별 건수 (탐지 규칙 입력) |
| `p50_ms` `p95_ms` `p99_ms` `avg_ms` | int | 응답시간 백분위 |
| `req_bytes_avg` / `res_bytes_avg` | int | 평균 페이로드 크기 |

**인덱스**: `(stat_date, api_id)`, `(api_id, stat_date)`, `(consumer_system, stat_date)`. 날짜 범위 조회가 지배적이므로 `stat_date` 월 단위 파티셔닝 권장.

### 3.3 `api_call_log` — 원천 로그

보존 30\~90일. 대시보드는 **상세 조회 시에만** 접근한다.

| 컬럼 | 설명 |
| --- | --- |
| `timestamp` | UTC ISO-8601 ms |
| `api_id` `http_method` `path_template` | API 식별 |
| `consumer_system` `consumer_client_id` `subject_hash` | 소비자 식별 (subject는 SHA-256 앞 16자리) |
| `status_code` `auth_result` | 응답코드, 인가 판정 |
| `latency_total_ms` `latency_upstream_ms` | 전체 / 백엔드 — 차이가 GW 오버헤드 |
| `trace_id` | 분산 추적 ID |
| `gateway` `gateway_node` | 처리 노드 (용량 분석용) |
| `request_bytes` `response_bytes` | 페이로드 크기 |

요청 본문·헤더·토큰 원문은 저장하지 않는다.

## 4. 테스트 데이터셋

이상 케이스를 **의도적으로 심어두고 정답지를 함께 제공**한다. 탐지 규칙 구현이 끝나면 `expected_detections.csv`와 대조해 재현율을 바로 측정할 수 있다.

**파일 구성** (기준일 2026-10-12)

| 파일 | 행 수 | 내용 |
| --- | --- | --- |
| `api_registry.csv` | 134 | 10개 업무 도메인의 API 기준정보 |
| `consumer_system.csv` | 12 | 소비 시스템 (내부·배치·외부·시민개발자) |
| `api_daily_stats.csv` | 52,821 | 최근 180일 × API × 소비시스템 집계 |
| `api_call_log.csv` | 38,409 | 최근 30일 원천 로그 (1.2% 샘플링) |
| `expected_detections.csv` | 64 | 심어둔 이상 케이스 정답지 |
| `gen_testdata.py` | — | 생성 스크립트 (시드 고정, 재현 가능) |

**심어둔 이상 케이스와 기대 탐지 건수**

| 규칙 코드 | 건수 | 심은 방식 |
| --- | --- | --- |
| `UNREGISTERED` | 16 | `UNKNOWN-001`\~`016` — 로그에만 존재, Registry 미등록 |
| `UNUSED` | 14 | Registry에 있으나 집계에 호출 기록 없음 |
| `OWNER_MISSING` | 12 | `owner_org`·`owner_id` 공란 |
| `POLICY_MISMATCH` | 9 | `gw_rate_limit_rpm` = `rate_limit_rpm` × 2 |
| `SLO_BREACH` | 7 | `p95_ms`를 `slo_p95_ms`의 1.4\~2.3배로 생성 |
| `DEPRECATED_CALLED` | 3 | 폐기 8건 중 3건에 호출 로그 유지 |
| `CONSUMER_SPIKE` | 1 | `BI-TOOL`의 최근 7일 호출량 4.5배 |
| `AUTHZ_FAILURE` | 1 | `CDP-PAT`의 401/403 최근 10일 일 240\~570건 |
| `RESOURCE_ENUM` | 1 | `PARTNER-P`의 404 최근 5일 일 400\~900건 |

**데이터에 반영한 현실적 패턴** — 이상 케이스만 있으면 오탐 검증이 불가능하므로 정상 패턴도 함께 심었다.

- 주말 호출량이 평일의 15% 수준으로 감소
- 배치 소비자(`BATCH-FIN`, `BATCH-HR`, `DW-ETL`)는 **월말에만 호출** — 호출 부재를 폐기 신호로 오판하는지 검증하는 함정
- 업무중요도 1등급은 일 4,000건, 3등급은 120건 수준으로 차등
- 시간대별 가중치 적용 (09\~11시, 13\~16시 피크)
- `latency_upstream_ms` < `latency_total_ms` 관계 유지 (GW 오버헤드 분리 검증용)

**검증 절차**

1. CSV를 PostgreSQL·OpenSearch에 적재
2. 탐지 배치 실행
3. 탐지 결과를 `expected_detections.csv`와 대조 — **재현율 100%, 오탐 0건**이 인수 기준
4. 배치 소비자 10건이 `UNUSED`로 잘못 탐지되지 않는지 별도 확인

## 5. 백엔드 API 명세

기본 경로 `/api/intel/v1`. 모든 조회는 집계 테이블 기준이며, 날짜 파라미터는 `from`·`to` (inclusive, `YYYY-MM-DD`).

| 메서드 | 경로 | 용도 |
| --- | --- | --- |
| GET | `/assets/summary` | 자산 4분면 카운트 (등록·실사용·미등록·폐기후보) |
| GET | `/assets/apis` | API 목록 — 도메인·상태·소유자·중요도 필터, 페이징 |
| GET | `/assets/distribution` | 도메인별·게이트웨이별 분포 |
| GET | `/quality/trend` | 일별 호출량·오류율 추이 |
| GET | `/quality/latency` | API별 P50·P95·P99, SLO 대비 |
| GET | `/quality/slo-breaches` | SLO 위반 API 목록 |
| GET | `/dependency/graph` | 소비자→API→제공자 그래프 (노드·엣지) |
| GET | `/dependency/impact/{apiId}` | 해당 API 변경 시 영향 소비 시스템 |
| GET | `/governance/detections` | 탐지 결과 목록 — 규칙·상태·담당조직 필터 |
| GET | `/governance/compliance` | 표준 준수율 (소유자 지정률, 정책 일치율, SLO 정의율) |
| GET | `/apis/{apiId}` | API 상세 (Registry + 최근 집계) |
| GET | `/apis/{apiId}/calls` | 원천 로그 조회 — 드릴다운 전용, 최대 1,000건 |
| PATCH | `/governance/detections/{id}` | 탐지 건 조치 상태 변경 (확인·조치완료·예외승인) |

**공통 응답 포맷**

```markdown
{
  "data": { ... },
  "meta": { "asOf": "2026-10-12", "rowCount": 134, "cached": true }
}
```

**`GET /assets/summary` 응답 예시**

```markdown
{
  "data": {
    "registered": 134,
    "activelyUsed": 107,
    "unregistered": 16,
    "retireCandidates": 14,
    "ownerMissing": 12,
    "deprecatedButCalled": 3
  },
  "meta": { "asOf": "2026-10-12", "windowDays": 90 }
}
```

**`GET /dependency/graph` 응답 예시**

```markdown
{
  "data": {
    "nodes": [
      { "id": "EBP", "type": "CONSUMER", "label": "임직원포털" },
      { "id": "KSOE-API-001001", "type": "API", "label": "/api/v1/prd/work-orders", "criticality": 2 },
      { "id": "MES", "type": "PROVIDER", "label": "MES" }
    ],
    "edges": [
      { "from": "EBP", "to": "KSOE-API-001001", "calls30d": 84210, "errorRate": 0.004 },
      { "from": "KSOE-API-001001", "to": "MES" }
    ]
  }
}
```

**`GET /governance/detections` 응답 예시**

```markdown
{
  "data": [
    {
      "id": 10421,
      "ruleCode": "DEPRECATED_CALLED",
      "severity": "HIGH",
      "apiId": "KSOE-API-001087",
      "ownerOrg": "품질정보팀",
      "detectedAt": "2026-10-12T02:00:00Z",
      "evidence": { "consumerSystem": "PARTNER-P", "calls7d": 1840 },
      "status": "OPEN"
    }
  ],
  "meta": { "rowCount": 64 }
}
```

## 6. 화면 명세

4개 뷰 모두 상단에 **공통 필터 바**(기간, 업무 도메인, 게이트웨이, 소비 시스템)를 두고, 모든 숫자는 클릭 시 해당 대상 목록으로 드릴다운한다. 숫자를 보여주고 끝나면 조치로 이어지지 않는다.

### 6.1 자산 현황 (`/assets`)

| 영역 | 구성 |
| --- | --- |
| 요약 카드 6개 | 등록 API, 실사용 API, 미등록 API, 폐기 후보, 소유자 미지정, 폐기 API 호출 |
| 자산 4분면 | 등록×사용 여부 2×2 — 각 분면에 건수와 비율, 클릭 시 목록 |
| 도메인별 분포 | 가로 막대 — 도메인별 등록/실사용 건수 |
| 등록 추이 | 선형 — 월별 등록 API 누계와 실사용 API 수 |
| API 목록 | 정렬·필터 가능 테이블 (api\_id, 경로, 도메인, 소유자, 상태, 30일 호출량, 최근 호출일) |

요약 카드는 **미등록·소유자 미지정·폐기 호출** 3개가 0이 아니면 경고색으로 표시한다.

### 6.2 운영 품질 (`/quality`)

| 영역 | 구성 |
| --- | --- |
| 요약 카드 4개 | 총 호출량(30일), 평균 오류율, SLO 위반 API 수, P95 중앙값 |
| 호출량 추이 | 선형 — 일별 총 호출량, 게이트웨이별 분리 토글 |
| 오류율 추이 | 영 기준 막대 — 일별 오류율, SLO 임계선 점선 |
| 응답시간 분포 | 범위 차트 — API별 P50(점) + P95\~P99(수염), SLO 목표선 표시 |
| SLO 위반 목록 | 테이블 — API, 소유자, 목표 P95, 실측 P95, 초과율, 연속 위반 일수 |

응답시간 분포는 **중요도 1등급 API만 기본 표시**한다. 134개를 모두 그리면 읽을 수 없다.

### 6.3 연계·의존성 (`/dependency`)

| 영역 | 구성 |
| --- | --- |
| 그래프 뷰 | 3단 그래프 (소비 시스템 → API → 제공 시스템), 엣지 두께 = 호출량 |
| 시스템 선택 | 좌측 트리에서 시스템 선택 시 연결 경로만 하이라이트 |
| 영향도 패널 | 선택한 API 변경 시 영향받는 소비 시스템 목록 + 각 시스템의 30일 호출량 |
| 설계 대비 실제 | 테이블 — Registry 등록 소비자와 실호출 소비자의 차집합 |

그래프는 노드 200개 초과 시 도메인 단위로 접어서 렌더링한다.

### 6.4 보안·거버넌스 (`/governance`)

| 영역 | 구성 |
| --- | --- |
| 준수율 카드 4개 | Registry 등록률, 소유자 지정률, 정책 일치율, SLO 정의율 |
| 탐지 현황 | 규칙별 누적 막대 — OPEN/조치중/완료 상태 구분 |
| 탐지 목록 | 테이블 — 규칙, 심각도, API, 소유 조직, 탐지일, 경과일, 상태 (상태 변경 가능) |
| 이상 호출 | 소비자별 401·403·404·429 추이, 임계 초과 구간 강조 |

탐지 목록의 **경과일**은 조치 SLA(보안 24시간 / 일반 10영업일) 초과 시 적색 표시한다.

## 7. 화면 흐름

&#91;embedded content: 화면 흐름 · 4개 뷰 → API 상세\]

드릴다운 목적지를 하나로 모으면 상세 화면을 한 번만 만들면 되고, 어느 뷰에서 진입했든 담당자가 보는 화면이 같아진다. 진입 경로는 쿼리 파라미터로 전달해 뒤로가기 시 필터 상태를 복원한다.

## 8. 차트·컴포넌트 규격

| 지표 | 차트 유형 | 근거 |
| --- | --- | --- |
| 일별 호출량 추이 | 선형 (line) | 수준의 변화 |
| 게이트웨이별 호출량 | 누적 막대 | 전체를 구성하는 부분 |
| 오류율 추이 | 영 기준 막대 + 임계 점선 | 임계 초과 여부가 판단 대상 |
| API별 응답시간 | 범위 차트 (점 + 수염) | P50 중앙값 + P95\~P99 범위 |
| 도메인별 API 수 | 가로 막대 | 항목 간 비교, 레이블이 긴 경우 |
| 자산 4분면 | 2×2 매트릭스 | 등록 여부 × 사용 여부 |
| 의존성 | force-directed 그래프 | 관계 구조 |
| 탐지 현황 | 규칙별 누적 막대 | 규칙 × 상태 |
| 준수율 | 게이지 또는 단일 숫자 + 목표선 | 단일 값과 목표 대비 |

**색상 규칙** — 색은 의미를 갖는다. 장식용 다색 팔레트는 쓰지 않는다.

| 의미 | 사용처 |
| --- | --- |
| 정상 | SLO 충족, 조치 완료 |
| 주의 | SLO 근접(목표의 80\~100%), 조치중 |
| 위험 | SLO 위반, OPEN 상태 탐지, 폐기 API 호출 |
| 중립 | 일반 호출량, 비교 기준선 |

**공통 규칙**

- 모든 차트에 **as-of 일자**와 데이터 범위를 표기
- 임계치는 점선 + 레이블로 차트 내부에 표시 (범례에만 두지 않는다)
- 데이터 포인트 클릭 시 해당 조건의 상세 목록으로 이동
- 값이 0인 구간과 데이터가 없는 구간을 시각적으로 구분 (0은 선, 결측은 끊김)
- 숫자는 천 단위 구분, 응답시간은 ms, 비율은 소수 1자리

**테이블 공통**

- 기본 페이지 50행, 서버 사이드 페이징·정렬
- 전체 컬럼 CSV 내보내기 (보고서 작성 수요가 많다)
- `api_id` 클릭 → `/api/{apiId}` 상세로 이동

## 9. 탐지 규칙 배치 명세

일 1회 02:00 실행 (집계 배치 완료 후). `common-batch-starter` 기반, 규칙별 독립 Step으로 구성해 실패 시 해당 규칙만 재실행한다.

| 규칙 코드 | 심각도 | 입력 | 판단 기준 |
| --- | --- | --- | --- |
| `UNREGISTERED` | HIGH | stats, registry | stats에 있는 `api_id`가 registry에 없음 |
| `DEPRECATED_CALLED` | HIGH | stats, registry | `lifecycle_status = DEPRECATED` 이면서 최근 7일 호출 > 0 |
| `AUTHZ_FAILURE` | HIGH | stats | 동일 consumer×api의 `count_401 + count_403` ≥ 50/일 |
| `RESOURCE_ENUM` | HIGH | stats | 동일 consumer×api의 `count_404` ≥ 100/일 |
| `POLICY_MISMATCH` | MEDIUM | registry | `rate_limit_rpm ≠ gw_rate_limit_rpm` 또는 `auth_type` 불일치 |
| `SLO_BREACH` | MEDIUM | stats, registry | `p95_ms > slo_p95_ms` 2일 연속 |
| `CONSUMER_SPIKE` | MEDIUM | stats | 최근 7일 평균 > 직전 28일 평균 × 3.0 |
| `OWNER_MISSING` | LOW | registry | `owner_org` 또는 `owner_id` 공란 |
| `UNUSED` | LOW | stats, registry | 최근 90일 호출 0건, **등록 후 90일 경과** API만 |

**의사코드 — `UNUSED` (가장 오탐 위험이 큰 규칙)**

```markdown
for api in registry where lifecycle_status = 'ACTIVE':
    if api.registered_at > today - 90d:  continue      # 신규 API 제외
    calls = sum(stats.call_count where api_id = api.api_id
                                  and stat_date > today - 90d)
    if calls > 0:                        continue
    # 드물게 호출되는 API 보호: 13개월 창까지 확인
    calls13m = sum(stats.call_count where api_id = api.api_id
                                     and stat_date > today - 395d)
    severity = 'LOW' if calls13m > 0 else 'MEDIUM'     # 과거 호출 이력 있으면 하향
    emit(UNUSED, api, evidence = {calls90d: 0, calls13m: calls13m})
```

**출력 — `detection_result` 테이블**

| 컬럼 | 설명 |
| --- | --- |
| `id` | PK |
| `rule_code` `severity` | 규칙·심각도 |
| `api_id` `owner_org` | 대상·통보처 |
| `detected_at` `first_detected_at` | 최초 탐지일 유지 (경과일 산출용) |
| `evidence` | jsonb — 판단 근거 수치 |
| `status` | OPEN·ACK·RESOLVED·EXCEPTION |
| `resolved_at` `resolver_id` `comment` | 조치 이력 |

**중복 억제** — 동일 `(rule_code, api_id)` 가 이미 OPEN이면 신규 생성하지 않고 `detected_at`과 `evidence`만 갱신한다. 매일 같은 건이 쌓이면 목록이 쓸모없어진다.

**예외 승인** — `status = EXCEPTION` 건은 탐지 목록에서 제외하되 분기 1회 재검토 대상으로 표시한다. 영구 예외는 두지 않는다.

## 10. 비기능 요구사항

**성능**

| 항목 | 목표 |
| --- | --- |
| 요약 카드 응답 | 500ms 이내 (Redis 60초 캐시) |
| 추이 차트 (90일) | 1.5초 이내 |
| API 목록 (50행) | 1초 이내 |
| 의존성 그래프 (노드 200) | 3초 이내 |
| 원천 로그 드릴다운 (1,000건) | 3초 이내 |
| 탐지 배치 전체 | 30분 이내 |

집계 테이블 기준 180일 × 134 API × 12 소비자 ≈ 5만 행 규모에서 측정한 값이다. 전사 확대 시 수백만 행이 되므로 **파티셔닝과 사전 집계(일→주→월 롤업)를 2단계에 반영**한다.

**보안**

| 권한 등급 | 조회 범위 |
| --- | --- |
| 전사 관리자 | 전체 API, 전체 소비자, 원천 로그 |
| 도메인 담당 | 소속 도메인 API + 해당 API 호출 소비자 |
| 시스템 담당 | 소유 시스템의 제공/소비 API |
| 열람 전용 | 집계 지표만, 원천 로그 접근 불가 |

- `subject_hash`는 복호화 불가 — 개인 식별이 필요한 경우 별도 승인 절차로 처리
- 원천 로그 조회는 모두 감사 로그에 기록 (조회자, 조건, 시각)
- CSV 내보내기는 전사 관리자·도메인 담당만 허용

**가용성·운영**

- 대시보드 장애가 API Gateway 운영에 영향을 주지 않는 독립 배포 (읽기 전용 시스템)
- 집계 배치 실패 시 전일 데이터로 조회 가능, 화면에 **데이터 기준일 경고** 표시
- 브라우저: Chrome·Edge 최신 2개 버전, 해상도 1440×900 이상 기준 설계

## 11. 개발 일정 및 인수 기준

2주 스프린트 5회, 총 10주 기준이다.

| 스프린트 | 범위 | 완료 조건 |
| --- | --- | --- |
| S1 | 데이터 모델 구축, 테스트 데이터 적재, 공통 응답·권한 골격 | CSV 5종 적재 완료, `/assets/summary` 응답 |
| S2 | 자산 현황 뷰 + API 목록·상세 | 4분면 수치가 정답지와 일치 |
| S3 | 운영 품질 뷰 + 집계 배치 | SLO 위반 7건 정확 탐지 |
| S4 | 탐지 규칙 배치 + 보안·거버넌스 뷰 | 정답지 64건 재현율 100%, 오탐 0건 |
| S5 | 연계·의존성 뷰 + 성능 튜닝 + 인수 테스트 | 성능 목표 전 항목 충족 |

**인수 체크리스트**

- [ ] 자산 4분면 수치가 `expected_detections.csv` 기준과 일치 (미등록 16, 미사용 14, 폐기 호출 3)
- [ ] 배치 소비자(`BATCH-FIN`, `BATCH-HR`, `DW-ETL`)가 호출하는 API가 `UNUSED`로 오탐되지 않음
- [ ] SLO 위반 7건이 정확히 탐지되고, 나머지 API는 위반으로 표시되지 않음
- [ ] `CONSUMER_SPIKE`·`AUTHZ_FAILURE`·`RESOURCE_ENUM` 각 1건 탐지
- [ ] 소유자 미지정 12건이 자산 현황과 거버넌스 뷰 양쪽에서 동일 건수로 표시
- [ ] 모든 요약 카드 클릭 시 해당 목록으로 드릴다운
- [ ] 권한 등급별 조회 범위 제한 동작 (도메인 담당이 타 도메인 API 조회 불가)
- [ ] 원천 로그 조회 시 감사 로그 기록
- [ ] 집계 배치 미실행 상태에서 전일 데이터 조회 + 기준일 경고 표시
- [ ] 성능 목표 6개 항목 전부 충족
- [ ] 전 화면 CSV 내보내기 동작 및 권한 제한 적용
