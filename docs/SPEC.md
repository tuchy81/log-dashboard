# API Intelligence Dashboard — 개발 스펙 (구현 기준)

이 문서는 **현재 저장소에 구현된 코드(FastAPI + Vue 3 + PostgreSQL)** 를 기준으로 데이터 양식, 백엔드 API, 화면, 탐지 규칙을 상세히 정의한다. 기획 단계의 목표 아키텍처(Spring Boot·MyBatis·Redis·OpenSearch·Keycloak)는 [원본 스펙](<../API Intelligence Dashboard 개발 스펙.md>)에 있으며, 구현과의 차이는 [12장](#12-기획-스펙-대비-구현-현황)에 정리했다.

- 기준일(as-of): `AS_OF` 환경변수, 기본 `2026-10-12`
- 집계 최종 일자(`LAST`): `AS_OF - 1일` = `2026-10-11`
- 기본 API 경로: `/api/intel/v1`

## 목차

1. [개요](#1-개요)
2. [시스템 구성](#2-시스템-구성)
3. [데이터 양식](#3-데이터-양식)
4. [PostgreSQL 스키마](#4-postgresql-스키마)
5. [적재 절차](#5-적재-절차)
6. [백엔드 API 명세](#6-백엔드-api-명세)
7. [탐지 규칙 명세](#7-탐지-규칙-명세)
8. [화면 명세](#8-화면-명세)
9. [차트·컴포넌트 규격](#9-차트컴포넌트-규격)
10. [테스트 데이터셋과 정답지](#10-테스트-데이터셋과-정답지)
11. [비기능 요구사항](#11-비기능-요구사항)
12. [기획 스펙 대비 구현 현황](#12-기획-스펙-대비-구현-현황)

---

## 1. 개요

### 1.1 목적

API Gateway(APISIX·Kong·webMethods) 호출 로그와 API Registry를 결합해 API 자산을 가시화하고, 미등록·미사용·정책 불일치·SLO 위반·보안 이상 징후를 자동 탐지해 **조치로 이어지는** 대시보드를 제공한다.

### 1.2 전제 조건

1. 모든 호출 로그에 `api_id`가 채워져 있다. 매핑 실패 건은 `UNKNOWN-*` 접두 ID로 분류되며 미등록 탐지 대상이다.
2. 대시보드는 일 단위 **집계 테이블(`api_daily_stats`)** 을 조회한다. 원천 로그(`api_call_log`)는 API 상세 드릴다운에서만 읽는다.
3. 소비자 식별은 `consumer_system` 코드 기준이며 코드 매핑은 수집 단계에서 끝난 상태다.
4. 시스템은 **읽기 전용**이다(탐지 건 상태 변경과 감사 로그 기록 제외). Gateway 운영에 영향을 주지 않는다.

### 1.3 범위

| 포함 | 제외 |
| --- | --- |
| 데이터 모델·CSV 적재, 조회 API, 4개 뷰 + API 상세, 탐지 배치 9종, 테스트 데이터·정답지 | 로그 수집·정규화 파이프라인, Registry 등록/수정 화면, 알림 발송 채널 연동, 인증/권한(데모 구현에서 미구현) |

---

## 2. 시스템 구성

```text
┌────────────┐  /api/*   ┌────────────┐   SQL   ┌──────────────┐
│ Browser    │──────────▶│ Nginx :80  │────────▶│ FastAPI :8000│────────▶ PostgreSQL 16
│ Vue 3 SPA  │◀──────────│ (static +  │ proxy   │ /api/intel/v1│          (db:5432)
└────────────┘  :8080    │  proxy)    │         └──────────────┘
                         └────────────┘
```

| 컨테이너 | 이미지/빌드 | 포트 | 비고 |
| --- | --- | --- | --- |
| `db` | `postgres:16-alpine` | (내부 5432) | 계정 `dash/dash`, DB `dash`, 볼륨 `dbdata`, healthcheck `pg_isready` |
| `backend` | `./backend` (python:3.12-slim) | 내부 8000 (`docker-compose.dev.yml` 사용 시 8000 노출) | CSV 4종을 `/data`에 read-only 마운트 |
| `frontend` | `./frontend` (Vite 빌드 → Nginx) | **8080 → 80** | `/api/` 를 `backend:8000`으로 프록시, 나머지는 SPA fallback (`try_files … /index.html`) |

**백엔드 기동 순서 (`lifespan`)**: 커넥션 풀 open → `init_db()`(DDL + CSV 적재) → `detection_result`가 비어 있으면 `detect.run_all()` 1회 실행.

**백엔드 의존성**: `fastapi==0.115.6`, `uvicorn[standard]==0.34.0`, `psycopg[binary,pool]==3.2.3`.
**프론트엔드 의존성**: `vue 3.5.13`, `vue-router 4.5.0`, `pinia 2.2.6`, `echarts 5.5.1` / dev: `vite 5.4.11`, `@vitejs/plugin-vue 5.2.1`, `typescript 5.6.3`.

### 2.1 화면 라우트

| 경로 | 뷰 파일 | 설명 |
| --- | --- | --- |
| `/` | — | `/assets`로 redirect |
| `/assets` | `views/Assets.vue` | 자산 현황 |
| `/quality` | `views/Quality.vue` | 운영 품질 |
| `/dependency` | `views/Dependency.vue` | 연계·의존성 |
| `/governance` | `views/Governance.vue` | 보안·거버넌스 |
| `/api/:apiId` | `views/ApiDetail.vue` | API 상세(공통 드릴다운 목적지) |

모든 뷰 상단에 공통 필터 바(`components/FilterBar.vue`, 상태는 `stores/filters.ts`)가 있다.

---

## 3. 데이터 양식

### 3.1 공통 규칙

| 항목 | 규칙 |
| --- | --- |
| 인코딩 | **UTF-8 with BOM** (`utf-8-sig`). 로더가 BOM을 제거한 뒤 적재 |
| 구분자 / 개행 | 쉼표(`,`) / `\n`, 첫 행은 헤더, 컬럼 순서는 DDL과 무관(헤더명으로 매핑) |
| 따옴표 | RFC 4180 (`"` 이스케이프). 쉼표를 포함한 값만 따옴표로 감쌈 |
| 날짜 | `YYYY-MM-DD` (예: `2026-10-11`) |
| 타임스탬프 | UTC ISO-8601, 밀리초, `Z` 접미 (예: `2026-09-12T01:13:30.746Z`) |
| NULL | **빈 문자열**(= PostgreSQL `COPY … CSV`의 NULL). 예: `owner_org` 공란, `deprecate_date` 공란 |
| 숫자 | 천 단위 구분자 없음, 소수점 `.` |
| 식별자 | 대문자 + 하이픈. `api_id`는 `{법인코드}-API-{6자리 일련번호}` |

### 3.2 `api_registry.csv` — API 기준정보 (134행)

PK: `api_id`. 업무 도메인 10개, 버전은 전부 `v1`.

| # | 컬럼 | 타입 | NULL | 설명 / 허용값 | 예시 |
| --- | --- | --- | --- | --- | --- |
| 1 | `api_id` | varchar(32) | ✗ | 불변 식별자 `KSOE-API-{001001~}` | `KSOE-API-001001` |
| 2 | `path_template` | varchar(255) | ✓ | 정규화 경로, 경로 변수는 `{id}` | `/api/v1/prd/work-orders/{id}` |
| 3 | `http_method` | varchar(8) | ✓ | `GET`(68) · `POST`(42) · `PUT`(24) | `GET` |
| 4 | `version` | varchar(8) | ✓ | `v1` (ID에 포함하지 않음) | `v1` |
| 5 | `domain_code` | varchar(8) | ✓ | `PRD`18 · `MAT`16 · `QUA`13 · `HRM`12 · `FIN`11 · `DSN`12 · `SAF`10 · `EQP`10 · `SCM`11 · `CMN`21 | `PRD` |
| 6 | `domain_name` | varchar(32) | ✓ | 생산관리·자재/구매·품질·인사·재무·설계·안전환경·설비·협력사·공통/인증 | `생산관리` |
| 7 | `provider_system` | varchar(32) | ✓ | 제공 시스템: `MES` `IAM` `WMS` `SAP-S4` `EHR` `EAM` `DMS` `PLM` `SRM` `QMS` | `MES` |
| 8 | `owner_org` | varchar(64) | ✓ | 소유 조직. **공란 = `OWNER_MISSING` 대상** | `생산정보팀` |
| 9 | `owner_id` | varchar(32) | ✓ | 담당자 사번 `emp#####`. 공란 = `OWNER_MISSING` 대상 | `emp15654` |
| 10 | `criticality` | smallint | ✓ | 업무 중요도 `1`(최상,26) · `2`(67) · `3`(하,41) | `2` |
| 11 | `lifecycle_status` | varchar(16) | ✓ | `ACTIVE`(126) · `DEPRECATED`(8). 스키마상 `DESIGN`·`RETIRED`도 허용 | `ACTIVE` |
| 12 | `auth_type` | varchar(16) | ✓ | `OAUTH2_JWT`(114) · `PAT`(15) · `MTLS`(5) | `OAUTH2_JWT` |
| 13 | `authz_policy_id` | varchar(16) | ✓ | 외부 인가 서비스 정책 ID `POL-###` | `POL-108` |
| 14 | `rate_limit_rpm` | int | ✓ | Registry 기재 분당 호출 제한 | `600` |
| 15 | `gw_rate_limit_rpm` | int | ✓ | Gateway 실제 설정값 (`POLICY_MISMATCH` 비교 대상) | `600` |
| 16 | `slo_p95_ms` | int | ✓ | P95 응답시간 목표(ms) | `1200` |
| 17 | `slo_availability` | numeric(5,2) | ✓ | 가용률 목표(%) | `99.50` |
| 18 | `openapi_url` | varchar(255) | ✓ | 계약 명세 링크 | `https://registry.hd.local/specs/KSOE-API-001001.yaml` |
| 19 | `gateway` | varchar(16) | ✓ | `APISIX`(75) · `KONG`(36) · `WEBMETHODS`(23) | `APISIX` |
| 20 | `registered_at` | date | ✓ | 등록일 (`UNUSED`의 신규 제외 기준) | `2025-03-01` |
| 21 | `deprecate_date` | date | ✓ | 폐기 예정일. `ACTIVE`는 공란 | `2026-11-30` |

```csv
api_id,path_template,http_method,version,domain_code,domain_name,provider_system,owner_org,owner_id,criticality,lifecycle_status,auth_type,authz_policy_id,rate_limit_rpm,gw_rate_limit_rpm,slo_p95_ms,slo_availability,openapi_url,gateway,registered_at,deprecate_date
KSOE-API-001001,/api/v1/prd/work-orders,GET,v1,PRD,생산관리,MES,생산정보팀,emp15654,2,ACTIVE,OAUTH2_JWT,POL-108,600,600,1200,99.5,https://registry.hd.local/specs/KSOE-API-001001.yaml,APISIX,2025-03-01,
```

### 3.3 `consumer_system.csv` — 소비 시스템 기준정보 (12행)

PK: `consumer_system`.

| # | 컬럼 | 타입 | NULL | 설명 / 허용값 | 예시 |
| --- | --- | --- | --- | --- | --- |
| 1 | `consumer_system` | varchar(32) | ✗ | 소비 시스템 코드 | `EBP` |
| 2 | `consumer_name` | varchar(64) | ✓ | 표시명 | `임직원포털` |
| 3 | `consumer_type` | varchar(16) | ✓ | `INTERNAL` · `BATCH` · `EXTERNAL` · `CITIZEN` | `INTERNAL` |
| 4 | `owner_org` | varchar(64) | ✓ | 소유 조직 | `안전보건팀` |

| 코드 | 이름 | 유형 | | 코드 | 이름 | 유형 |
| --- | --- | --- | --- | --- | --- | --- |
| `EBP` | 임직원포털 | INTERNAL | | `BATCH-FIN` | 재무정산배치 | BATCH |
| `MOCEAN` | 모바일플랫폼 | INTERNAL | | `BATCH-HR` | 인사마감배치 | BATCH |
| `MES-UI` | 생산현장단말 | INTERNAL | | `DW-ETL` | 데이터웨어하우스 | BATCH |
| `SAP-S4` | ERP | INTERNAL | | `PARTNER-P` | 사외협력사포털 | EXTERNAL |
| `PLM-UI` | 설계포털 | INTERNAL | | `CDP-PAT` | 시민개발자앱 | CITIZEN |
| `QMS-UI` | 품질포털 | INTERNAL | | `BI-TOOL` | 경영분석도구 | INTERNAL |

### 3.4 `api_daily_stats.csv` — 일 단위 집계 (52,821행)

논리 키: `(stat_date, api_id, consumer_system)` (DB에는 UNIQUE 제약 없음 — 인덱스만). 기간 `2026-04-15 ~ 2026-10-11`(180일). **호출이 없는 날은 행 자체가 없다**(0 행 미생성). 따라서 "0"과 "데이터 없음"을 구분할 수 있어야 한다.

| # | 컬럼 | 타입 | 설명 | 예시 |
| --- | --- | --- | --- | --- |
| 1 | `stat_date` | date | 집계 일자 | `2026-10-11` |
| 2 | `api_id` | varchar(32) | `api_registry.api_id` 조인 키. **미등록 API는 `UNKNOWN-0##`** | `KSOE-API-001001` |
| 3 | `consumer_system` | varchar(32) | `consumer_system.consumer_system` 조인 키 | `BI-TOOL` |
| 4 | `gateway` | varchar(16) | 처리 게이트웨이 | `APISIX` |
| 5 | `call_count` | bigint | 총 호출 건수 | `73` |
| 6 | `error_count` | bigint | 오류(4xx·5xx) 건수 | `0` |
| 7 | `count_401` | bigint | 상태코드 401 건수 | `0` |
| 8 | `count_403` | bigint | 상태코드 403 건수 | `0` |
| 9 | `count_404` | bigint | 상태코드 404 건수 | `0` |
| 10 | `count_429` | bigint | 상태코드 429 건수 | `0` |
| 11 | `p50_ms` | int | 응답시간 P50 (ms) | `339` |
| 12 | `p95_ms` | int | 응답시간 P95 (ms) | `969` |
| 13 | `p99_ms` | int | 응답시간 P99 (ms) | `1405` |
| 14 | `avg_ms` | int | 평균 응답시간 (ms) | `406` |
| 15 | `req_bytes_avg` | int | 평균 요청 크기 (bytes) | `1663` |
| 16 | `res_bytes_avg` | int | 평균 응답 크기 (bytes) | `10148` |

불변식: `p50 ≤ p95 ≤ p99`. 주의: `error_count`와 `count_401/403/404/429`는 **독립 생성**되어 `error_count ≥ 상태코드별 합`이 성립하지 않는 행이 있다(현재 데이터 92행). 오류율은 `error_count` 기준, 보안 이상 탐지는 상태코드별 컬럼 기준으로 따로 계산한다.

```csv
stat_date,api_id,consumer_system,gateway,call_count,error_count,count_401,count_403,count_404,count_429,p50_ms,p95_ms,p99_ms,avg_ms,req_bytes_avg,res_bytes_avg
2026-10-11,KSOE-API-001001,BI-TOOL,APISIX,73,0,0,0,0,0,339,969,1405,406,1663,10148
```

> **API 단위 백분위 합성**: 여러 소비자·일자를 합칠 때 백분위를 단순 평균하지 않고 **`call_count` 가중 평균** `Σ(p95 × call_count) / Σ(call_count)` 으로 근사한다(`/quality/latency`, `/apis/{id}`, SLO 탐지). 정확한 합성 백분위가 아니라 근사치임에 유의.

### 3.5 `api_call_log.csv` — 원천 호출 로그 (38,409행)

기간 `2026-09-12T01:13 ~ 2026-10-11T23:53`(최근 30일, 1.2% 샘플링). 대시보드는 API 상세 드릴다운에서만 접근한다. **요청 본문·헤더·토큰 원문은 저장하지 않는다.**

| # | 컬럼 | 타입 | 설명 / 허용값 | 예시 |
| --- | --- | --- | --- | --- |
| 1 | `timestamp` | timestamptz | UTC, ms 정밀도 | `2026-09-12T01:13:30.746Z` |
| 2 | `api_id` | varchar(32) | 등록 API 또는 `UNKNOWN-###` | `KSOE-API-001132` |
| 3 | `http_method` | varchar(8) | | `GET` |
| 4 | `path_template` | varchar(255) | 정규화 경로. 미등록은 `/api/legacy/...` 등 | `/api/legacy/qua/ncrs-ext` |
| 5 | `consumer_system` | varchar(32) | 소비 시스템 코드 | `SAP-S4` |
| 6 | `consumer_client_id` | varchar(64) | 소비자 클라이언트 ID `cli-{시스템}-{nn}` | `cli-sap-s4-01` |
| 7 | `subject_hash` | varchar(32) | 사용자 식별자의 SHA-256 앞 16자리(복호화 불가) | `456489201504dc57` |
| 8 | `status_code` | int | `200`·`201`·`400`·`401`·`403`·`404`·`429`·`500` | `200` |
| 9 | `latency_total_ms` | int | 전체 응답시간 | `136` |
| 10 | `latency_upstream_ms` | int | 백엔드 응답시간. 항상 `< latency_total_ms` | `106` |
| 11 | `trace_id` | varchar(64) | 분산 추적 ID (32자리 hex) | `c728075b1c9ca7470b94f4f0c93983d8` |
| 12 | `gateway` | varchar(16) | `APISIX`·`KONG`·`WEBMETHODS` | `APISIX` |
| 13 | `gateway_node` | varchar(16) | `gw-01`·`gw-02`·`gw-03` | `gw-03` |
| 14 | `request_bytes` | int | 요청 크기 | `713` |
| 15 | `response_bytes` | int | 응답 크기 | `1780` |
| 16 | `auth_result` | varchar(16) | `ALLOW` · `DENY` | `ALLOW` |

파생값: **게이트웨이 오버헤드** = `latency_total_ms - latency_upstream_ms` (API `/apis/{id}/calls`의 `gw_overhead_ms`).

```csv
timestamp,api_id,http_method,path_template,consumer_system,consumer_client_id,subject_hash,status_code,latency_total_ms,latency_upstream_ms,trace_id,gateway,gateway_node,request_bytes,response_bytes,auth_result
2026-09-12T01:13:30.746Z,UNKNOWN-011,GET,/api/legacy/qua/ncrs-ext,SAP-S4,cli-sap-s4-01,456489201504dc57,200,136,106,c728075b1c9ca7470b94f4f0c93983d8,APISIX,gw-03,713,1780,ALLOW
```

### 3.6 `expected_detections.csv` — 탐지 정답지 (64행)

| # | 컬럼 | 타입 | 설명 | 예시 |
| --- | --- | --- | --- | --- |
| 1 | `rule_code` | varchar(32) | 탐지 규칙 코드 ([7장](#7-탐지-규칙-명세)) | `AUTHZ_FAILURE` |
| 2 | `api_id` | varchar(32) | 탐지 대상 API | `KSOE-API-001036` |
| 3 | `note` | text | 심은 방식 설명 | `CDP-PAT 401/403 반복` |

```csv
rule_code,api_id,note
AUTHZ_FAILURE,KSOE-API-001036,CDP-PAT 401/403 반복
CONSUMER_SPIKE,KSOE-API-001128,BI-TOOL 호출량 급증
DEPRECATED_CALLED,KSOE-API-001025,폐기 상태이나 호출 발생
```

### 3.7 파일 간 관계

```text
api_registry (1) ──< api_daily_stats >── (1) consumer_system
     │ api_id              │ api_id            consumer_system
     └──────< api_call_log >┘
detection_result ──▶ api_id (미등록 API는 registry에 없을 수 있음 → FK 없음)
```

외래키 제약은 걸지 않는다. 미등록 API(`UNKNOWN-*`)가 stats/log에 존재해야 `UNREGISTERED` 탐지가 가능하기 때문이다.

---

## 4. PostgreSQL 스키마

`backend/app/schema.sql` (`CREATE … IF NOT EXISTS`, 기동 시 매번 실행).

| 테이블 | 용도 | 키/인덱스 |
| --- | --- | --- |
| `api_registry` | Registry | PK `api_id` |
| `consumer_system` | 소비 시스템 | PK `consumer_system` |
| `api_daily_stats` | 일 집계 | `ix_stats_date_api (stat_date, api_id)`, `ix_stats_api_date (api_id, stat_date)`, `ix_stats_cons_date (consumer_system, stat_date)` |
| `api_call_log` | 원천 로그 | `ix_log_api_ts (api_id, "timestamp" DESC)` |
| `detection_result` | 탐지 결과 | PK `id serial`, **UNIQUE `(rule_code, api_id)`** |
| `audit_log` | 조회·조치 감사 | PK `id serial` |

CSV 컬럼 타입은 3장 표를 그대로 따른다(`api_call_log."timestamp"`는 예약어라 따옴표 필요).

### 4.1 `detection_result`

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `id` | serial PK | 탐지 건 ID |
| `rule_code` | varchar(32) | 규칙 코드 |
| `severity` | varchar(8) | `HIGH` · `MEDIUM` · `LOW` |
| `api_id` | varchar(32) | 대상 API |
| `owner_org` | varchar(64) | 통보처. 탐지 시점의 Registry 소유 조직(미등록은 NULL) |
| `detected_at` | timestamptz | **최근** 탐지 시각 (재탐지 시 갱신) |
| `first_detected_at` | timestamptz | **최초** 탐지 시각 (경과일 산출용, 갱신하지 않음) |
| `evidence` | jsonb | 판단 근거 수치 (규칙별 형식은 7.2) |
| `status` | varchar(16) default `OPEN` | `OPEN` · `ACK` · `RESOLVED` · `EXCEPTION` |
| `resolved_at` | timestamptz | `RESOLVED`/`EXCEPTION` 전환 시각, 그 외 상태는 NULL로 초기화 |
| `resolver_id` | varchar(32) | 조치자 (기본 `demo-user`) |
| `comment` | text | 조치 의견 |

### 4.2 `audit_log`

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `id` | serial PK | |
| `at` | timestamptz default `now()` | 발생 시각 |
| `actor` | varchar(64) | 수행자 (현재 `demo-user` 고정) |
| `action` | varchar(32) | `CALL_LOG_QUERY`(원천 로그 조회), `DETECTION_STATUS`(탐지 상태 변경) |
| `detail` | jsonb | 조회 조건/변경 내용. 예: `{"apiId":"KSOE-API-001001","status":null,"consumer":null,"limit":200}` |

---

## 5. 적재 절차

`loader.init_db()` — 백엔드 기동 시 실행.

1. `schema.sql` 실행 (DDL, idempotent).
2. 아래 순서로 테이블이 **비어 있을 때만** 적재 (재기동 시 건너뜀).

| 순서 | 테이블 | 파일 |
| --- | --- | --- |
| 1 | `api_registry` | `api_registry.csv` |
| 2 | `consumer_system` | `consumer_system.csv` |
| 3 | `api_daily_stats` | `api_daily_stats.csv` |
| 4 | `api_call_log` | `api_call_log.csv` |

3. 파일을 `utf-8-sig`로 디코딩해 BOM 제거 → 헤더를 컬럼명으로 사용 → `COPY <table> (<cols>) FROM STDIN WITH (FORMAT csv, HEADER true)`.
4. `detection_result`가 비어 있으면 탐지 배치 전체 실행.

`expected_detections.csv`는 DB에 적재하지 않으며 검증용 정답지다. 재적재는 `docker compose down -v` 후 재기동.

---

## 6. 백엔드 API 명세

기본 경로 `/api/intel/v1`. OpenAPI 문서는 `/docs`(개발용 오버라이드로 8000 포트 노출 시).

### 6.1 공통

**응답 포맷**

```json
{ "data": { }, "meta": { "asOf": "2026-10-12" } }
```

`meta`에는 `asOf`가 항상 들어가며, 엔드포인트별로 `rowCount`, `page`, `size`, `from`, `to`, `windowDays`, `thresholds` 등이 추가된다.

**공통 파라미터**

| 파라미터 | 타입 | 설명 |
| --- | --- | --- |
| `from`, `to` | date (`YYYY-MM-DD`, inclusive) | 조회 기간. `to` 미지정 시 `AS_OF-1일`, `from` 미지정 시 `to - (N-1)일` (엔드포인트별 N: 30 또는 90) |
| `domain` | string | `api_registry.domain_code` |
| `gateway` | string | `api_daily_stats.gateway` |
| `consumer` | string | `api_daily_stats.consumer_system` |
| `page`, `size` | int | 1 기반 페이지, 기본 50 |
| `export=csv` | string | 지원 엔드포인트에서 전체 행을 **UTF-8 BOM CSV**로 다운로드 (`Content-Disposition: attachment`). 객체/배열 값은 문자열화 |

**오류**: `400`(잘못된 상태값), `404`(대상 없음), 검증 오류는 FastAPI 기본 `422`.

### 6.2 엔드포인트 목록

| 메서드 | 경로 | 용도 | CSV |
| --- | --- | --- | --- |
| GET | `/meta/filters` | 공통 필터 선택지·기준일·데이터 지연 여부 | |
| GET | `/assets/summary` | 자산 요약 카드·4분면 | |
| GET | `/assets/apis` | API 목록 (필터·정렬·페이징) | ✓ |
| GET | `/assets/distribution` | 도메인·게이트웨이별 분포 | |
| GET | `/assets/trend` | 월별 등록·실사용 API 추이 | |
| GET | `/quality/summary` | 운영 품질 요약 카드 | |
| GET | `/quality/trend` | 일×게이트웨이 호출량·오류 | |
| GET | `/quality/latency` | API별 P50/P95/P99 vs SLO | |
| GET | `/quality/slo-breaches` | SLO 위반 목록 | ✓ |
| GET | `/dependency/graph` | 소비자→API→제공자 그래프 | |
| GET | `/dependency/impact/{api_id}` | API 영향 소비 시스템 | |
| GET | `/governance/detections` | 탐지 목록 | ✓ |
| PATCH | `/governance/detections/{det_id}` | 탐지 건 상태 변경 | |
| POST | `/governance/run-detection` | 탐지 배치 수동 실행 | |
| GET | `/governance/by-rule` | 규칙×상태 건수 | |
| GET | `/governance/compliance` | 표준 준수율 | |
| GET | `/governance/anomalies` | 소비자별 4xx 추이 | |
| GET | `/apis/{api_id}` | API 상세 | |
| GET | `/apis/{api_id}/calls` | 원천 로그 드릴다운 (감사 기록) | |

### 6.3 상세 명세

#### `GET /meta/filters`

```json
{
  "data": {
    "domains": [{ "code": "PRD", "name": "생산관리" }],
    "gateways": ["APISIX", "KONG", "WEBMETHODS"],
    "consumers": [{ "code": "EBP", "name": "임직원포털", "type": "INTERNAL" }],
    "lastStatDate": "2026-10-11",
    "dataStale": false
  },
  "meta": { "asOf": "2026-10-12" }
}
```

`dataStale` = `AS_OF - max(stat_date) > 1일` — 집계 배치 지연 시 화면에 기준일 경고를 표시하는 근거.

#### `GET /assets/summary`

`meta.windowDays = 90`. 카운트 중 탐지 기반 항목은 `status <> 'EXCEPTION'` 건만 센다.

| 필드 | 산출 |
| --- | --- |
| `registered` | `api_registry` 행 수 |
| `activelyUsed` | 최근 90일 `call_count > 0`인 **등록** API 수 |
| `unregistered` | `UNREGISTERED` 탐지 건수 |
| `retireCandidates` | `UNUSED` 탐지 건수 |
| `ownerMissing` | `OWNER_MISSING` 탐지 건수 |
| `deprecatedButCalled` | `DEPRECATED_CALLED` 탐지 건수 |
| `quadrant` | `registeredUsed`=`activelyUsed`, `registeredUnused`=`registered-activelyUsed`, `unregisteredUsed`=`unregistered`, `unregisteredUnused`=`null`(관측 불가) |

```json
{
  "data": {
    "registered": 134, "activelyUsed": 120, "unregistered": 16,
    "retireCandidates": 14, "ownerMissing": 12, "deprecatedButCalled": 3,
    "quadrant": { "registeredUsed": 120, "registeredUnused": 14, "unregisteredUsed": 16, "unregisteredUnused": null }
  },
  "meta": { "asOf": "2026-10-12", "windowDays": 90 }
}
```

(수치는 예시. 실제 값은 적재된 데이터와 탐지 결과에 따름)

#### `GET /assets/apis`

| 파라미터 | 타입 | 설명 |
| --- | --- | --- |
| `domain`, `status`, `gateway` | string | 도메인·`lifecycle_status`·게이트웨이 일치 |
| `criticality` | int | 1~3 |
| `owner_missing` | bool | 소유 조직/담당자 공란만 |
| `no_calls` | bool | 최근 30일 호출 0건만 |
| `keyword` | string | `api_id` 또는 `path_template` 부분 일치(대소문자 무시) |
| `sort` | string | `api_id`(기본) · `path_template` · `domain_code` · `owner_org` · `lifecycle_status` · `criticality` · `calls30d` · `last_call_date` · `registered_at` (그 외 값은 `api_id`로 대체) |
| `desc` | bool | 내림차순 (NULL은 항상 뒤) |
| `page`, `size` | int | 기본 1, 50 |
| `export` | string | `csv` |

응답 `data[]` 행: `api_id, http_method, path_template, domain_code, domain_name, provider_system, owner_org, owner_id, criticality, lifecycle_status, gateway, registered_at, calls30d, last_call_date`. `meta.rowCount`는 필터 후 전체 건수.

#### `GET /assets/distribution`

```json
{ "data": {
    "domains":  [{ "code": "CMN", "name": "공통/인증", "registered": 21, "active": 19 }],
    "gateways": [{ "gateway": "APISIX", "registered": 75 }] } }
```

`active` = 최근 90일 호출 있는 API 수.

#### `GET /assets/trend`

최근 약 13개월(`AS_OF-365일`이 속한 달 ~ `AS_OF`의 달) 월별 행.

```json
{ "data": [{ "month": "2026-09", "registered": 134, "active": 118 }] }
```

`registered` = 해당 월 말까지 `registered_at`이 있는 API 누계, `active` = 해당 월 호출이 있었던 등록 API 수(없으면 `null`).

#### `GET /quality/summary`

파라미터: `from`, `to`(기본 30일), `domain`, `gateway`, `consumer`.

```json
{ "data": { "totalCalls": 1523400, "errorRate": 0.0123, "sloBreachApis": 7, "p95Median": 480 },
  "meta": { "asOf": "2026-10-12", "from": "2026-09-12", "to": "2026-10-11" } }
```

`errorRate` = `Σerror_count / Σcall_count`(0~1). `p95Median` = 행 단위 `p95_ms`의 중앙값. `sloBreachApis` = `SLO_BREACH` 탐지 건수(EXCEPTION 제외, 기간 필터 무관).

#### `GET /quality/trend`

기본 90일. 행: `{ "date", "gateway", "calls", "errors" }` (일×게이트웨이). 게이트웨이 합계/분리는 프론트에서 처리.

#### `GET /quality/latency`

추가 파라미터 `criticality`(기본 **1**). 행: `{ api_id, path_template, http_method, slo_p95_ms, p50, p95, p99 }` — 호출 가중 평균 근사, 기간 내 호출 > 0인 API만.

#### `GET /quality/slo-breaches`

`SLO_BREACH` 탐지(EXCEPTION 제외)를 초과율 내림차순으로. 행: `{ api_id, path_template, owner_org, slo_p95_ms, p95_ms, consecutive_days, over_pct, status }`. `over_pct = (p95_ms / slo_p95_ms - 1) × 100` (소수 1자리). `export=csv` 지원.

#### `GET /dependency/graph`

파라미터: 공통 + `limit_apis`(기본 60, 호출량 상위 N개 API만 포함).

노드 `type`: `CONSUMER`(id=시스템 코드) · `API`(id=`api_id`, `label`=`METHOD path`, `criticality`, `domain`) · `PROVIDER`(id=`P:{provider_system}`). 엣지: 소비자→API는 `{from, to, calls, errorRate}`, API→제공자는 `{from, to}`. 엣지는 `(from,to)` 기준 중복 제거. 미등록 API는 제공자 노드/엣지가 생기지 않는다.

```json
{ "data": {
    "nodes": [
      { "id": "EBP", "type": "CONSUMER", "label": "임직원포털" },
      { "id": "KSOE-API-001001", "type": "API", "label": "GET /api/v1/prd/work-orders", "criticality": 2, "domain": "PRD" },
      { "id": "P:MES", "type": "PROVIDER", "label": "MES" } ],
    "edges": [
      { "from": "EBP", "to": "KSOE-API-001001", "calls": 84210, "errorRate": 0.004 },
      { "from": "KSOE-API-001001", "to": "P:MES" } ] } }
```

#### `GET /dependency/impact/{api_id}`

최근 30일 해당 API를 호출한 소비 시스템. 행: `{ consumer_system, consumer_name, consumer_type, calls30d, error_rate, last_call }` — 호출량 내림차순, `meta.rowCount`.

#### `GET /governance/detections`

| 파라미터 | 설명 |
| --- | --- |
| `rule`, `status`, `owner_org`, `severity` | 일치 필터 |
| `include_exception` | `true`면 `EXCEPTION` 포함. 기본은 **제외** (단 `status`를 직접 지정하면 그 값 그대로) |
| `sort` | `severity`(기본: HIGH→MEDIUM→LOW, 오래된 건 먼저) · `elapsed`(최초 탐지 오래된 순) · `rule` · 그 외 `id` |
| `page`, `size`, `export` | 페이징 / CSV |

행: `{ id, ruleCode, severity, apiId, ownerOrg, detectedAt, firstDetectedAt, elapsedDays, evidence, status, comment }`. `elapsedDays` = `AS_OF - first_detected_at::date`.

```json
{ "data": [{
    "id": 10421, "ruleCode": "DEPRECATED_CALLED", "severity": "HIGH", "apiId": "KSOE-API-001025",
    "ownerOrg": "품질정보팀", "detectedAt": "2026-10-12T02:00:00Z", "firstDetectedAt": "2026-10-06T02:00:00Z",
    "elapsedDays": 6, "evidence": { "consumerSystem": "PARTNER-P", "calls7d": 1840 },
    "status": "OPEN", "comment": null }],
  "meta": { "asOf": "2026-10-12", "rowCount": 64, "page": 1, "size": 50 } }
```

#### `PATCH /governance/detections/{det_id}`

요청:

```json
{ "status": "ACK", "comment": "담당자 확인 중", "resolverId": "emp12345" }
```

- `status` 필수: `OPEN | ACK | RESOLVED | EXCEPTION` (그 외 `400 invalid status`), `comment`·`resolverId` 선택(기본 `demo-user`).
- `resolved_at`은 `RESOLVED`/`EXCEPTION`일 때만 `now()`, 그 외 NULL.
- 대상 없으면 `404`. 성공 시 `audit_log`에 `DETECTION_STATUS` 기록.
- 응답: `{ "data": { "id": 10421, "status": "ACK" } }`

#### `POST /governance/run-detection`

탐지 배치 9종을 즉시 실행하고 규칙별 신규 탐지 후보 수를 반환한다. `{ "data": { "UNREGISTERED": 16, "UNUSED": 14, ... } }`. 중복 억제 규칙은 7.3 참고.

#### `GET /governance/by-rule`

`[{ "rule": "SLO_BREACH", "status": "OPEN", "n": 7 }]` — 규칙×상태 건수(전체 상태 포함). "규칙별 누적 막대" 입력.

#### `GET /governance/compliance`

```json
{ "data": {
    "registrationRate": 0.8933, "ownerRate": 0.9104, "policyRate": 0.9328, "sloDefinedRate": 1.0,
    "targets": { "registrationRate": 1.0, "ownerRate": 1.0, "policyRate": 1.0, "sloDefinedRate": 1.0 } } }
```

| 필드 | 산식 |
| --- | --- |
| `registrationRate` | `등록 수 / (등록 수 + UNREGISTERED 탐지 건수)` |
| `ownerRate` | 소유 조직·담당자가 모두 채워진 API / 전체 |
| `policyRate` | `rate_limit_rpm = gw_rate_limit_rpm`인 API / 전체 |
| `sloDefinedRate` | `slo_p95_ms`와 `slo_availability`가 모두 있는 API / 전체 |

#### `GET /governance/anomalies`

파라미터: `from`/`to`(기본 30일), `consumer`. 일×소비자별 `{ date, consumer, c401, c403, c404, c429 }` (4개 합계 > 0인 행만). `meta.thresholds = { "authz": 50, "enum": 100 }` — 차트 임계선.

#### `GET /apis/{api_id}`

```json
{ "data": {
    "registry": { "...api_registry 전체 컬럼...": "미등록이면 null" },
    "unregistered": false,
    "daily": [{ "date": "2026-10-11", "calls": 640, "errors": 3, "p95": 812 }],
    "detections": [{ "id": 1, "ruleCode": "SLO_BREACH", "severity": "MEDIUM", "status": "OPEN", "evidence": {} }] } }
```

`daily`는 최근 90일 일별(호출 있는 날만, `p95`는 호출 가중). Registry·stats·로그 어디에도 없으면 `404`. 미등록(`UNKNOWN-*`)도 stats/로그가 있으면 `registry: null`로 응답한다.

#### `GET /apis/{api_id}/calls`

| 파라미터 | 설명 |
| --- | --- |
| `limit` | 기본 200, **최대 1,000** |
| `status` | 상태코드 일치 |
| `consumer` | 소비 시스템 일치 |

행: `timestamp, consumer_system, consumer_client_id, subject_hash, status_code, auth_result, latency_total_ms, latency_upstream_ms, gw_overhead_ms, trace_id, gateway, gateway_node, request_bytes, response_bytes` (최신순). **호출 시마다 `audit_log`에 `CALL_LOG_QUERY`(조건 포함) 기록.**

---

## 7. 탐지 규칙 명세

`backend/app/detect.py`. 규칙은 독립 함수이며 `RULES` 딕셔너리 순서(`UNREGISTERED → DEPRECATED_CALLED → AUTHZ_FAILURE → RESOURCE_ENUM → POLICY_MISMATCH → SLO_BREACH → CONSUMER_SPIKE → OWNER_MISSING → UNUSED`)로 실행된다. 기준일은 `AS_OF`, 탐지 시각은 `AS_OF 02:00 UTC`.

### 7.1 규칙 정의

| 규칙 코드 | 심각도 | 입력 | 판단 기준 (구현) | 기대 건수 |
| --- | --- | --- | --- | --- |
| `UNREGISTERED` | HIGH | stats + log | `api_id`가 stats/log에 있으나 registry에 없음 | 16 |
| `DEPRECATED_CALLED` | HIGH | stats, registry | `lifecycle_status='DEPRECATED'` 이고 최근 7일(`stat_date > AS_OF-7`) `call_count > 0` | 3 |
| `AUTHZ_FAILURE` | HIGH | stats | 최근 7일 중, 동일 api×consumer의 `count_401+count_403` ≥ **50/일**인 날이 있음 | 1 |
| `RESOURCE_ENUM` | HIGH | stats | 최근 7일 중, 동일 api×consumer의 `count_404` ≥ **100/일**인 날이 있음 | 1 |
| `POLICY_MISMATCH` | MEDIUM | registry | `rate_limit_rpm <> gw_rate_limit_rpm` | 9 |
| `SLO_BREACH` | MEDIUM | stats, registry | 최근 30일 일별 호출가중 P95가 `slo_p95_ms` 초과인 날이 **최신일부터 2일 이상 연속** | 7 |
| `CONSUMER_SPIKE` | MEDIUM | stats | 동일 api×consumer의 최근 7일 일평균 > 직전 28일 일평균 × **3.0** (둘 다 > 0) | 1 |
| `OWNER_MISSING` | LOW | registry | `owner_org` 또는 `owner_id`가 NULL/공백 | 12 |
| `UNUSED` | LOW/MEDIUM | stats, registry | `ACTIVE`이고 `registered_at ≤ AS_OF-90`이며 최근 90일 호출 0건. 최근 395일(≈13개월) 호출 이력이 있으면 `LOW`, 없으면 `MEDIUM` | 14 |

> 원본 기획 스펙의 `POLICY_MISMATCH`는 `auth_type` 불일치도 포함하지만, 현재 Registry에 Gateway 실제 `auth_type`이 없어 **`rate_limit_rpm` 불일치만** 구현되어 있다.

**`UNUSED` 오탐 방지**: 월말에만 호출하는 배치 소비자(`BATCH-FIN`, `BATCH-HR`, `DW-ETL`)의 API가 미사용으로 판정되지 않도록 ① 90일 창을 쓰고 ② 신규 API(등록 90일 미만)를 제외하며 ③ 13개월 이력으로 심각도를 낮춘다.

### 7.2 `evidence` (jsonb) 형식

| 규칙 | 형식 |
| --- | --- |
| `UNREGISTERED` | `{"calls": 1520, "consumers": ["SAP-S4","EBP"]}` |
| `DEPRECATED_CALLED` | `{"calls7d": 1840, "consumerSystem": "PARTNER-P"}` (`consumerSystem`은 호출량 최다 소비자) |
| `AUTHZ_FAILURE` | `{"consumerSystem": "CDP-PAT", "days": 7, "peakPerDay": 570}` |
| `RESOURCE_ENUM` | `{"consumerSystem": "PARTNER-P", "days": 5, "peakPerDay": 900}` |
| `POLICY_MISMATCH` | `{"rateLimitRpm": 300, "gwRateLimitRpm": 600}` |
| `SLO_BREACH` | `{"sloP95Ms": 800, "p95Ms": 1450, "consecutiveDays": 12}` |
| `CONSUMER_SPIKE` | `{"consumerSystem": "BI-TOOL", "avg7d": 910.4, "avg28d": 201.7, "ratio": 4.51}` |
| `OWNER_MISSING` | `{}` |
| `UNUSED` | `{"calls90d": 0, "calls13m": 0}` |

`AUTHZ_FAILURE`/`RESOURCE_ENUM`의 `first_detected_at`은 임계 초과가 처음 나타난 `stat_date`의 02:00, `SLO_BREACH`는 연속 위반 시작일, 나머지는 실행 시각이다.

### 7.3 중복 억제 / 예외

- `UNIQUE (rule_code, api_id)` + `INSERT … ON CONFLICT DO UPDATE` — 이미 있는 건은 **`detected_at`·`evidence`만 갱신**하고 `status`·`first_detected_at`·조치 이력은 유지한다. 같은 건이 매일 쌓이지 않는다.
- 한 `(rule, api)`당 1건이므로 동일 API에서 소비자가 둘 이상 해당해도 1건으로 합쳐진다.
- `EXCEPTION` 건은 기본 목록·요약에서 제외된다(분기 1회 재검토 대상, 영구 예외 없음 — 운영 규약).
- 한 번 `RESOLVED`가 된 건이 다시 조건에 해당하면 같은 행의 `detected_at`/`evidence`만 갱신되고 상태는 `RESOLVED`로 남는다. **재오픈 로직은 없다**(알려진 한계).

### 7.4 조치 SLA (화면 표시 기준)

기획 기준은 **보안 24시간 / 일반 10영업일**이다. 프론트엔드 구현(`Governance.vue`의 `overSla`)은 `RESOLVED`·`EXCEPTION`이 아닌 건에 대해 `HIGH`는 `elapsedDays ≥ 1`, 그 외는 영업일을 달력일로 근사한 `elapsedDays > 14`일 때 경과일을 적색으로 표시한다.

---

## 8. 화면 명세

상단 헤더에 탭(4개 뷰)과 **기준일**을 표시하고, `meta/filters.dataStale`이 true면 "집계 데이터가 최신이 아닙니다" 배너를 띄운다. 4개 뷰 상단에는 **공통 필터 바**(`FilterBar.vue`)가 있으며 API 상세(`/api/*`)에서는 숨긴다.

| 필터 | 값 | 비고 |
| --- | --- | --- |
| 기간 | 7일 / 30일(기본) / 90일 | `to`=최종 집계일, `from`=`to-(N-1)일` |
| 업무 도메인 | 전체 + 10개 | `meta/filters.domains` |
| 게이트웨이 | 전체 + 3개 | |
| 소비 시스템 | 전체 + 12개 | |

필터 상태는 Pinia store(`stores/filters.ts`)에 보관되며 라우트 이동 간에 유지된다(URL 쿼리에는 저장하지 않음). 드릴다운 동작: 요약 카드·4분면 클릭 시 거버넌스 뷰로 이동(`/governance?rule=…`)하거나 자산 목록 필터를 적용하고, `api_id` 링크는 `/api/{apiId}`로 이동한다. URL 쿼리로 복원되는 필터는 현재 거버넌스의 `rule`뿐이다.

### 8.1 자산 현황 `/assets`

| 영역 | 구성 | 데이터 소스 |
| --- | --- | --- |
| 요약 카드 6 | 등록 API, 실사용 API(90일), 미등록 API, 폐기 후보, 소유자 미지정, 폐기 API 호출 — 클릭 시 거버넌스(`UNREGISTERED`/`UNUSED`/`DEPRECATED_CALLED`)로 이동하거나 목록 필터 적용 | `/assets/summary` |
| 자산 4분면 | 등록·사용 / 등록·미사용 / 미등록·사용 / 미등록·미사용(관측 불가 `-`) 건수와 비율 | `/assets/summary.quadrant` |
| 도메인별 분포 | 가로 막대(등록, 실사용) | `/assets/distribution` |
| 월별 추이 | 선형(등록 누계, 실사용 API) — 실사용은 집계 보존 180일 구간만 값이 있음 | `/assets/trend` |
| API 목록 | 검색(api_id/경로), 상태·중요도 선택, 소유자 미지정·30일 무호출 체크, 컬럼 정렬, 이전/다음 페이징, CSV | `/assets/apis` |

미등록(>0)은 위험색, 소유자 미지정·폐기 API 호출(>0)은 각각 주의/위험색으로 표시한다.

### 8.2 운영 품질 `/quality`

| 영역 | 구성 | 데이터 소스 |
| --- | --- | --- |
| 요약 카드 4 | 총 호출량(기간), 평균 오류율(2.0% 초과 시 위험색), SLO 위반 API 수, P95 중앙값 | `/quality/summary` |
| 일별 호출량 | 합계 선형 ↔ 게이트웨이별 누적 막대 토글 | `/quality/trend` |
| 일별 오류율 | 막대, 임계 2.0% 점선, 초과일 위험색 | `/quality/trend` |
| API별 응답시간 | P50 점 + P95~P99 수염 + SLO 막대, 중요도 선택(기본 1등급), P95 색: 충족/80~100%/위반, 클릭 시 API 상세 | `/quality/latency` |
| SLO 위반 목록 | API·경로·소유 조직·목표/실측 P95·초과율·연속 일수, CSV | `/quality/slo-breaches` |

추이 차트는 기간을 90일 선택한 경우에만 `from`을 전달하고, 그 외에는 서버 기본값(90일)을 사용한다. SLO 위반 목록은 공통 필터의 영향을 받지 않는다.

### 8.3 연계·의존성 `/dependency`

| 영역 | 구성 | 데이터 소스 |
| --- | --- | --- |
| 그래프 | 소비 시스템(좌) → API(중) → 제공 시스템(우) **3열 고정 배치**(ECharts `graph`, `layout: none`, 확대/이동 가능). 엣지 두께=호출량, 오류율 3% 초과 엣지는 위험색, API 노드 크기=중요도. 표시 API 수 선택(20/40/80/전체, 호출량 상위) | `/dependency/graph` |
| 노드 선택 | 노드 hover/선택 시 인접 노드·엣지 강조(`emphasis.focus: adjacency`) | |
| 영향도 패널 | API 노드 클릭 시 영향 소비 시스템·30일 호출·오류율 | `/dependency/impact/{id}` |
| 소비 시스템 목록 | 클릭 시 공통 필터를 해당 시스템으로 설정 | |

### 8.4 보안·거버넌스 `/governance`

| 영역 | 구성 | 데이터 소스 |
| --- | --- | --- |
| 준수율 카드 4 | 등록률·소유자 지정률·정책 일치율·SLO 정의율 (100% 미만이면 위험/주의색, 목표 100% 표기) | `/governance/compliance` |
| 탐지 현황 | 규칙(9)×상태(OPEN/조치중/조치완료/예외승인) 누적 가로 막대 | `/governance/by-rule` |
| 이상 호출 | 일별 `401+403`, `404`, `429` 선형, 임계선(authz 50, enum 100), 소비 시스템 선택 | `/governance/anomalies` |
| 탐지 목록 | 규칙·심각도·상태 필터, 탐지일(최초), 경과일(SLA 초과 시 적색), 근거 요약, **상태 드롭다운으로 즉시 변경**, 페이징, CSV | `/governance/detections`, `PATCH` |

### 8.5 API 상세 `/api/:apiId`

필터 바 없음. 뒤로가기 링크, 미등록 경고, 탐지 배지(규칙·상태), Registry 정보(19개 필드), 일별 호출·오류·P95 추이(90일, SLO 점선), 영향 소비 시스템(30일), 원천 호출 로그(최신 200건, 상태코드 필터, 시각·소비자·상태·인가·총/upstream/GW 오버헤드 ms·GW 노드·trace_id).

---

## 9. 차트·컴포넌트 규격

| 지표 | 차트 | 근거 |
| --- | --- | --- |
| 일별 호출량 추이 | 선형 | 수준의 변화 |
| 게이트웨이별 호출량 | 누적 막대 | 전체를 구성하는 부분 |
| 오류율 추이 | 영 기준 막대 + 임계 점선 | 임계 초과 여부 판단 |
| API별 응답시간 | 범위 차트(점 + 수염) | P50 + P95~P99 |
| 도메인별 API 수 | 가로 막대 | 긴 레이블 비교 |
| 자산 4분면 | 2×2 매트릭스 | 등록 × 사용 |
| 의존성 | 3열 배치 노드-링크 그래프 (구현: `layout: none`) | 소비자→API→제공자 단계 구조 |
| 탐지 현황 | 규칙별 누적 막대 | 규칙 × 상태 |
| 준수율 | 단일 숫자 + 목표선 | 목표 대비 |

**색상**(`src/api.ts`의 `COLOR`): 정상 `#2a9d6f`, 주의 `#e0a100`, 위험 `#d64545`, 중립 `#5b7a99`, 격자 `#e3e8ee`. 색은 의미를 갖는다 — 정상(SLO 충족·조치 완료), 주의(SLO 목표의 80~100%·조치중), 위험(SLO 위반·OPEN 탐지·폐기 API 호출), 중립(일반 호출량·기준선).

**공통 규칙**

- 모든 차트에 as-of 일자와 데이터 범위를 표기한다.
- 임계치는 차트 내부에 점선+레이블로 표시한다.
- 데이터 포인트 클릭 시 해당 조건의 상세로 이동한다(구현: 응답시간 차트 → API 상세).
- 값 0(선)과 데이터 없음(끊김)을 구분한다 — 3.4절 "0 행 미생성" 특성 때문에 **결측은 `null`로 두고 0으로 채우지 않는다**.
- 숫자는 천 단위 구분(`n()`, `ko-KR`), 응답시간은 ms, 비율은 소수 1자리(`pct()`).
- 테이블: 기본 50행, 서버 사이드 페이징·정렬, 전체 컬럼 CSV 내보내기(`csvUrl()`), `api_id` 클릭 시 상세 이동.

---

## 10. 테스트 데이터셋과 정답지

`gen_testdata.py`가 seed `20261012`로 생성한다(기준일 `2026-10-12`). 이상 케이스를 의도적으로 심고 `expected_detections.csv`에 정답을 기록한다.

| 규칙 | 건수 | 심은 방식 |
| --- | --- | --- |
| `UNREGISTERED` | 16 | `UNKNOWN-001~016` — 로그/집계에만 존재 |
| `UNUSED` | 14 | Registry에 있으나 집계 호출 없음 |
| `OWNER_MISSING` | 12 | `owner_org`·`owner_id` 공란 |
| `POLICY_MISMATCH` | 9 | `gw_rate_limit_rpm = rate_limit_rpm × 2` |
| `SLO_BREACH` | 7 | `p95_ms`를 `slo_p95_ms`의 1.4~2.3배로 생성 |
| `DEPRECATED_CALLED` | 3 | 폐기 8건 중 3건에 호출 유지 |
| `CONSUMER_SPIKE` | 1 | `BI-TOOL` 최근 7일 호출량 4.5배 |
| `AUTHZ_FAILURE` | 1 | `CDP-PAT` 401/403 최근 10일 일 240~570건 |
| `RESOURCE_ENUM` | 1 | `PARTNER-P` 404 최근 5일 일 400~900건 |
| **합계** | **64** | |

**정상 패턴(오탐 검증용)**: 주말 호출량은 평일의 약 15%, 배치 소비자(`BATCH-FIN`·`BATCH-HR`·`DW-ETL`)는 월말에만 호출, 중요도 1등급 일 ~4,000건·3등급 ~120건, 시간대 가중(09~11시, 13~16시 피크), `latency_upstream_ms < latency_total_ms`.

**검증 기준**: 규칙별 탐지 건수와 `(rule_code, api_id)` 쌍이 정답지와 일치(**재현율 100%, 오탐 0건**), 배치 소비자 호출 API가 `UNUSED`로 탐지되지 않을 것.

```bash
# 예: 규칙별 건수 확인
curl -s localhost:8080/api/intel/v1/governance/by-rule
# 예: 정답지와 쌍 대조
curl -s "localhost:8080/api/intel/v1/governance/detections?size=200&include_exception=true" \
  | python3 -c 'import sys,json; print(sorted((d["ruleCode"],d["apiId"]) for d in json.load(sys.stdin)["data"]))'
```

---

## 11. 비기능 요구사항

| 항목 | 목표 |
| --- | --- |
| 요약 카드 응답 | 500ms 이내 |
| 추이 차트 (90일) | 1.5초 이내 |
| API 목록 (50행) | 1초 이내 |
| 의존성 그래프 | 3초 이내 (노드 200 초과 시 도메인 단위 접기 — 현재는 `limit_apis`로 상위 N개 제한) |
| 원천 로그 드릴다운 (1,000건) | 3초 이내 |
| 탐지 배치 전체 | 30분 이내 |

측정 기준은 5만 행 규모 집계 테이블이다. 전사 확대 시 월 단위 파티셔닝과 일→주→월 롤업이 필요하다.

- **가용성**: 읽기 전용 독립 배포. 집계 지연 시 전일 데이터로 조회 가능하며 `meta/filters.dataStale`로 기준일 경고를 띄운다.
- **브라우저**: Chrome·Edge 최신 2개 버전, 1440×900 이상.
- **보안(목표)**: `subject_hash` 복호화 불가, 원천 로그 조회 감사 기록, CSV 내보내기 권한 제한. 권한 등급(전사 관리자/도메인 담당/시스템 담당/열람 전용)별 조회 범위는 목표 스펙이며 현재 미구현.

---

## 12. 기획 스펙 대비 구현 현황

| 항목 | 기획 스펙 | 현재 구현 |
| --- | --- | --- |
| 백엔드 | Spring Boot 3 / Java 21, MyBatis | **FastAPI / Python 3.12, psycopg** |
| 프론트엔드 | Vue 3 + Yarn 4, Module Federation 호환 | Vue 3.5 + npm, Vite (단독 SPA) |
| 로그 저장소 | OpenSearch | PostgreSQL `api_call_log` |
| 캐시 | Redis 60초 | 없음 |
| 인증/권한 | Keycloak OIDC, 4단계 권한 | 없음 (`demo-user` 고정) |
| 집계 배치 | 일 1회 집계 | CSV 적재(집계 완료 데이터) |
| 탐지 배치 | 일 02:00 스케줄 | 기동 시 1회 + `POST /governance/run-detection` |
| `POLICY_MISMATCH` | rate limit + `auth_type` 불일치 | rate limit 불일치만 |
| 파티셔닝 | `stat_date` 월 단위 권장 | 미적용 (인덱스만) |
| 엔드포인트 | 13개 | 19개 (`meta/filters`, `assets/trend`, `quality/summary`, `governance/by-rule`, `governance/anomalies`, `governance/run-detection` 추가) |
| API 목록 응답 | `/assets/apis` 등 | `/assets/apis` 동일 + CSV 내보내기 |
| 화면 | 설계 대비 실제 소비자 차집합, 시스템 트리 선택, 200노드 초과 시 도메인 접기 | 미구현 (Registry에 소비자 정의 컬럼 없음, 상위 N개 API 제한으로 대체) |
| 드릴다운 | 진입 경로를 쿼리 파라미터로 전달해 필터 복원 | 필터는 Pinia store 유지, 쿼리 복원은 거버넌스 `rule`만 |
