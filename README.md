# API Intelligence Dashboard

API Gateway 호출 로그와 API 기준정보(Registry)를 결합해 **API 자산 현황 · 운영 품질 · 연계 의존성 · 보안/거버넌스**를 한 화면 체계로 보여 주는 읽기 전용 대시보드입니다. 로그 수집 파이프라인이 완성되기 전에도 개발·검증할 수 있도록 **이상 케이스가 심어진 테스트 데이터셋과 정답지**를 함께 제공합니다.

> 기준일(as-of): `2026-10-12` (환경변수 `AS_OF`로 변경 가능)

- 상세 개발 스펙(데이터 양식, API 명세, 탐지 규칙): [`docs/SPEC.md`](docs/SPEC.md)
- 실데이터 확보 방법과 단계별 로깅 설계: [`docs/DATA_SOURCING.md`](docs/DATA_SOURCING.md)
- 기획 단계 원본 스펙(Spring Boot 기준 목표 아키텍처): [`API Intelligence Dashboard 개발 스펙.md`](<API Intelligence Dashboard 개발 스펙.md>)

## 주요 기능

| 뷰 | 경로 | 내용 |
| --- | --- | --- |
| 자산 현황 | `/assets` | 등록/실사용/미등록/폐기 후보 요약 카드, 자산 4분면, 도메인별 분포, 월별 등록 추이, API 목록(필터·정렬·CSV) |
| 운영 품질 | `/quality` | 호출량·오류율 추이, 중요도 1등급 API 응답시간(P50/P95/P99) vs SLO, SLO 위반 목록 |
| 연계·의존성 | `/dependency` | 소비 시스템 → API → 제공 시스템 그래프, API 변경 영향도 |
| 보안·거버넌스 | `/governance` | 표준 준수율, 규칙별 탐지 현황, 탐지 목록(상태 변경), 401/403/404/429 이상 호출 추이 |
| API 상세 | `/api/:apiId` | 4개 뷰 공통 드릴다운 — Registry 정보, 90일 추이, 탐지 건, 원천 로그(감사 로그 기록) |

탐지 규칙 9종(`UNREGISTERED`, `UNUSED`, `OWNER_MISSING`, `POLICY_MISMATCH`, `SLO_BREACH`, `DEPRECATED_CALLED`, `CONSUMER_SPIKE`, `AUTHZ_FAILURE`, `RESOURCE_ENUM`)이 백엔드 기동 시 자동 실행되며, 결과는 `expected_detections.csv`(정답 64건)와 대조해 검증할 수 있습니다.

## 기술 스택

| 구분 | 기술 |
| --- | --- |
| 프론트엔드 | Vue 3.5 + TypeScript, Vite 5, Pinia, Vue Router, ECharts 5.5 |
| 백엔드 | Python 3.12, FastAPI 0.115, Uvicorn, psycopg 3 (connection pool) |
| DB | PostgreSQL 16 |
| 배포 | Docker Compose, Nginx(정적 서빙 + `/api/` 리버스 프록시) |

## 빠른 시작

사전 요건: Docker, Docker Compose v2

```bash
# 전체 기동 (db → backend → frontend)
docker compose up --build

# 브라우저에서 접속
open http://localhost:8080
```

- 최초 기동 시 백엔드가 스키마를 만들고 CSV 4종을 PostgreSQL에 적재(`COPY`)한 뒤 탐지 배치를 1회 실행합니다. 이후 재기동 시에는 이미 적재된 테이블을 건너뜁니다.
- 데이터를 처음부터 다시 적재하려면 볼륨을 지웁니다: `docker compose down -v`
- 백엔드 API를 직접 호출/디버깅하려면 개발용 오버라이드를 함께 사용합니다(`8000` 포트 노출).

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
curl http://localhost:8000/api/intel/v1/assets/summary
# Swagger UI: http://localhost:8000/docs
```

### 로컬 개발 (컨테이너 없이)

```bash
# 1) DB만 컨테이너로
docker compose up -d db          # 5432 포트 노출이 필요하면 override 파일에 ports 추가

# 2) 백엔드
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql://dash:dash@localhost:5432/dash
export DATA_DIR=..               # CSV가 있는 저장소 루트
export AS_OF=2026-10-12
uvicorn app.main:app --reload --port 8000

# 3) 프론트엔드 (vite dev 서버)
cd frontend
npm install
npm run dev
```

### 환경변수

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql://dash:dash@db:5432/dash` | PostgreSQL 접속 문자열 |
| `AS_OF` | `2026-10-12` | 데이터 기준일. 집계 최종 일자는 `AS_OF - 1일` |
| `DATA_DIR` | `/data` | CSV 적재 디렉터리 (compose에서 읽기 전용 마운트) |

## 저장소 구조

```text
log-dashboard/
├─ README.md
├─ docs/SPEC.md                    # 상세 개발 스펙 (데이터 양식 포함)
├─ docs/DATA_SOURCING.md           # 실데이터 확보·단계별 로깅 설계
├─ API Intelligence Dashboard 개발 스펙.md   # 기획 단계 원본 스펙
├─ docker-compose.yml              # db + backend + frontend
├─ docker-compose.dev.yml          # backend 8000 포트 노출
├─ gen_testdata.py                 # 테스트 데이터 생성기 (seed 20261012, 재현 가능)
├─ api_registry.csv                # API 기준정보          134행
├─ consumer_system.csv             # 소비 시스템            12행
├─ api_daily_stats.csv             # 일 단위 집계        52,821행
├─ api_call_log.csv                # 원천 호출 로그      38,409행
├─ expected_detections.csv         # 탐지 정답지            64행
├─ backend/
│  ├─ Dockerfile, requirements.txt
│  └─ app/
│     ├─ main.py                   # FastAPI 라우트 (/api/intel/v1/*)
│     ├─ db.py, config.py          # 커넥션 풀, 설정
│     ├─ schema.sql                # DDL
│     ├─ loader.py                 # CSV → PostgreSQL 최초 적재
│     └─ detect.py                 # 탐지 규칙 9종 + upsert
└─ frontend/
   ├─ Dockerfile, nginx.conf
   └─ src/
      ├─ main.ts, App.vue, api.ts  # 라우터, 공통 필터 바, fetch 헬퍼
      ├─ stores/filters.ts         # 공통 필터 Pinia store
      ├─ components/               # Card, Panel, EChart, FilterBar
      └─ views/                    # Assets, Quality, Dependency, Governance, ApiDetail
```

## 테스트 데이터셋

| 파일 | 행 수 | 설명 |
| --- | --- | --- |
| `api_registry.csv` | 134 | 10개 업무 도메인 API (ACTIVE 126, DEPRECATED 8) |
| `consumer_system.csv` | 12 | 내부·배치·외부·시민개발자 소비 시스템 |
| `api_daily_stats.csv` | 52,821 | 2026-04-15 ~ 2026-10-11 (180일) × API × 소비시스템 |
| `api_call_log.csv` | 38,409 | 2026-09-12 ~ 2026-10-11 (30일, 샘플링) |
| `expected_detections.csv` | 64 | 의도적으로 심은 이상 케이스 정답지 |

모든 CSV는 **UTF-8 BOM** 인코딩이며 로더가 `utf-8-sig`로 처리합니다. 컬럼 정의는 [`docs/SPEC.md`](docs/SPEC.md#3-데이터-양식)를 참고하세요.

데이터를 다시 만들려면:

```bash
python gen_testdata.py
```

> 스크립트의 출력 경로 `OUT`이 `/mnt/user-data/outputs`로 고정되어 있으므로, 로컬에서 실행하려면 `OUT = "."` 등으로 수정하세요. seed(`20261012`)가 고정이라 동일 결과가 재현됩니다.

## 검증

탐지 배치 결과가 정답지와 일치하는지(재현율 100%, 오탐 0건) 확인합니다.

```bash
# 규칙별 탐지 건수 (기대: UNREGISTERED 16, UNUSED 14, OWNER_MISSING 12, POLICY_MISMATCH 9,
#                          SLO_BREACH 7, DEPRECATED_CALLED 3, CONSUMER_SPIKE 1, AUTHZ_FAILURE 1, RESOURCE_ENUM 1)
curl -s http://localhost:8080/api/intel/v1/governance/by-rule
# 탐지 재실행
curl -s -X POST http://localhost:8080/api/intel/v1/governance/run-detection
```

특히 월말에만 호출하는 배치 소비자(`BATCH-FIN`, `BATCH-HR`, `DW-ETL`)의 API가 `UNUSED`로 오탐되지 않는지 확인해야 합니다.

## 현재 구현 범위와 한계 (데모 구현)

- 인증/인가 없음: 조치자와 감사 로그 actor는 `demo-user` 고정 (기획 스펙의 Keycloak·권한 등급은 미구현)
- Redis 캐시, OpenSearch, 월 단위 파티셔닝 미적용 — 원천 로그도 PostgreSQL `api_call_log`에서 조회
- 집계·탐지 배치는 스케줄러 없이 기동 시 1회 + `POST /governance/run-detection` 수동 실행
- 상세 비교는 [`docs/SPEC.md` 12장](docs/SPEC.md#12-기획-스펙-대비-구현-현황) 참고
