#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API Intelligence Dashboard 테스트 데이터 생성기
- api_registry.csv        : API Master Registry (설계/관리 정보)
- consumer_system.csv     : 소비 시스템 기준정보
- api_call_log.csv        : 원천 호출 로그 샘플 (최근 30일)
- api_daily_stats.csv     : 일 x API x 소비시스템 집계
- expected_detections.csv : 일부러 심어둔 이상 케이스 정답지
"""
import csv, random, hashlib
from datetime import date, datetime, timedelta, timezone

random.seed(20261012)

TODAY = date(2026, 10, 12)
LOG_DAYS = 30                      # 원천 로그 보존 구간
STATS_DAYS = 180                   # 집계 보존 구간

OUT = "/mnt/user-data/outputs"

# ---------------------------------------------------------------- 기준정보
LEGAL = "KSOE"

DOMAINS = [
    ("PRD", "생산관리"), ("MAT", "자재/구매"), ("QUA", "품질"),
    ("HRM", "인사"), ("FIN", "재무"), ("DSN", "설계"),
    ("SAF", "안전환경"), ("EQP", "설비"), ("SCM", "협력사"),
    ("CMN", "공통/인증"),
]

OWNER_ORGS = [
    "생산정보팀", "구매시스템팀", "품질정보팀", "인사정보팀", "재무정보팀",
    "설계시스템팀", "안전보건팀", "설비기술팀", "협력사관리팀", "시스템아키텍처팀",
]

PROVIDERS = [
    "MES", "SAP-S4", "PLM", "EHR", "QMS", "WMS", "EAM", "SRM", "IAM", "DMS",
]

CONSUMERS = [
    # (코드, 명칭, 유형)
    ("EBP",        "임직원포털",        "INTERNAL"),
    ("MOCEAN",     "모바일플랫폼",      "INTERNAL"),
    ("MES-UI",     "생산현장단말",      "INTERNAL"),
    ("SAP-S4",     "ERP",               "INTERNAL"),
    ("PLM-UI",     "설계포털",          "INTERNAL"),
    ("QMS-UI",     "품질포털",          "INTERNAL"),
    ("BATCH-FIN",  "재무정산배치",      "BATCH"),
    ("BATCH-HR",   "인사마감배치",      "BATCH"),
    ("PARTNER-P",  "사외협력사포털",    "EXTERNAL"),
    ("CDP-PAT",    "시민개발자앱",      "CITIZEN"),
    ("DW-ETL",     "데이터웨어하우스",  "BATCH"),
    ("BI-TOOL",    "경영분석도구",      "INTERNAL"),
]

RESOURCES = {
    "PRD": ["work-orders", "operations", "routings", "lots", "production-plans", "shop-orders"],
    "MAT": ["materials", "purchase-orders", "vendors", "stocks", "receipts"],
    "QUA": ["inspections", "defects", "ncrs", "certificates"],
    "HRM": ["employees", "attendances", "organizations", "payrolls"],
    "FIN": ["accounts", "vouchers", "budgets", "settlements"],
    "DSN": ["drawings", "boms", "revisions", "models"],
    "SAF": ["incidents", "permits", "trainings"],
    "EQP": ["equipments", "maintenances", "sensors"],
    "SCM": ["partners", "contracts", "deliveries"],
    "CMN": ["tokens", "users", "roles", "codes", "files", "notifications"],
}

GATEWAYS = ["APISIX", "KONG", "WEBMETHODS"]


def api_id(seq):
    return f"{LEGAL}-API-{seq:06d}"


# ---------------------------------------------------------------- Registry
registry = []
seq = 1000

for code, dname in DOMAINS:
    for res in RESOURCES[code]:
        for method, suffix in [("GET", ""), ("GET", "/{id}"), ("POST", ""), ("PUT", "/{id}")]:
            if random.random() < 0.35 and suffix:
                continue
            seq += 1
            registry.append({
                "api_id": api_id(seq),
                "path_template": f"/api/v1/{code.lower()}/{res}{suffix}",
                "http_method": method,
                "version": "v1",
                "domain_code": code,
                "domain_name": dname,
                "provider_system": random.choice(PROVIDERS),
                "owner_org": random.choice(OWNER_ORGS),
                "owner_id": f"emp{random.randint(10000, 99999)}",
                "criticality": random.choices([1, 2, 3], weights=[0.2, 0.45, 0.35])[0],
                "lifecycle_status": "ACTIVE",
                "auth_type": random.choices(
                    ["OAUTH2_JWT", "OAUTH2_JWT", "OAUTH2_JWT", "PAT", "MTLS"],
                    weights=[0.5, 0.2, 0.1, 0.15, 0.05])[0],
                "authz_policy_id": f"POL-{random.randint(100, 180)}",
                "rate_limit_rpm": random.choice([300, 600, 1200, 3000]),
                "slo_p95_ms": random.choice([300, 500, 800, 1200]),
                "slo_availability": 99.5,
                "openapi_url": f"https://registry.hd.local/specs/{api_id(seq)}.yaml",
                "gateway": random.choices(GATEWAYS, weights=[0.55, 0.3, 0.15])[0],
                "registered_at": (TODAY - timedelta(days=random.randint(120, 1400))).isoformat(),
                "deprecate_date": "",
            })

# --- 일부러 심는 이상 케이스 -------------------------------------------------
expected = []

# (A) 소유자 미지정 12건
for r in random.sample(registry, 12):
    r["owner_org"] = ""
    r["owner_id"] = ""
    expected.append(("OWNER_MISSING", r["api_id"], "소유 조직·담당자 공란"))

# (B) 폐기 상태 8건 (그중 3건은 계속 호출됨 → C에서 처리)
deprecated = random.sample([r for r in registry if r["criticality"] == 3], 8)
for r in deprecated:
    r["lifecycle_status"] = "DEPRECATED"
    r["deprecate_date"] = (TODAY - timedelta(days=random.randint(30, 200))).isoformat()

# (C) 폐기인데 호출되는 3건
still_called = deprecated[:3]
for r in still_called:
    expected.append(("DEPRECATED_CALLED", r["api_id"], "폐기 상태이나 호출 발생"))

# (D) 미사용 14건 (90일 이상 무호출)
unused = random.sample([r for r in registry if r not in deprecated], 14)
for r in unused:
    expected.append(("UNUSED", r["api_id"], "90일 이상 호출 없음"))

# (E) 정책 불일치 9건 — Registry와 Gateway 설정 상이
mismatch = random.sample([r for r in registry if r not in deprecated and r not in unused], 9)
for r in mismatch:
    r["gw_rate_limit_rpm"] = r["rate_limit_rpm"] * 2       # GW 실제 설정
    expected.append(("POLICY_MISMATCH", r["api_id"], "Rate Limit 설정 불일치"))
for r in registry:
    r.setdefault("gw_rate_limit_rpm", r["rate_limit_rpm"])

# (F) SLO 상습 위반 7건
slo_violators = random.sample(
    [r for r in registry if r not in deprecated and r not in unused], 7)
for r in slo_violators:
    expected.append(("SLO_BREACH", r["api_id"], "P95 응답시간 SLO 초과"))

# (G) 미등록 API 16건 — 로그에만 존재, Registry에 없음
shadow = []
for i in range(16):
    code, dname = random.choice(DOMAINS)
    res = random.choice(RESOURCES[code])
    shadow.append({
        "api_id": f"UNKNOWN-{i+1:03d}",
        "path_template": f"/api/legacy/{code.lower()}/{res}-ext",
        "http_method": random.choice(["GET", "POST"]),
        "provider_system": random.choice(PROVIDERS),
        "gateway": random.choice(GATEWAYS),
        "slo_p95_ms": 800,
    })
    expected.append(("UNREGISTERED", shadow[-1]["api_id"], "로그에 있으나 Registry 미등록"))

registry_by_id = {r["api_id"]: r for r in registry}
callable_apis = [r for r in registry if r not in unused]

# 이상 소비자 시나리오
SPIKE_CONSUMER = "BI-TOOL"       # 최근 7일 호출량 급증
AUTHZ_CONSUMER = "CDP-PAT"       # 401/403 반복
ENUM_CONSUMER = "PARTNER-P"      # 404 자원 열거
spike_api = random.choice([r for r in callable_apis if r["criticality"] <= 2])
authz_api = random.choice(callable_apis)
enum_api = random.choice([r for r in callable_apis if "{id}" in r["path_template"]])
expected.append(("CONSUMER_SPIKE", spike_api["api_id"], f"{SPIKE_CONSUMER} 호출량 급증"))
expected.append(("AUTHZ_FAILURE", authz_api["api_id"], f"{AUTHZ_CONSUMER} 401/403 반복"))
expected.append(("RESOURCE_ENUM", enum_api["api_id"], f"{ENUM_CONSUMER} 404 자원 열거"))


# ---------------------------------------------------------------- 호출 패턴
def consumers_for(api):
    """API 도메인에 따라 그럴듯한 소비 시스템 집합을 고정 배정"""
    h = int(hashlib.md5(api["api_id"].encode()).hexdigest(), 16)
    n = 1 + h % 4
    pool = [c[0] for c in CONSUMERS]
    picks = []
    for k in range(n):
        picks.append(pool[(h >> (3 * k)) % len(pool)])
    return sorted(set(picks))


def base_volume(api):
    c = api.get("criticality", 2)
    return {1: 4000, 2: 900, 3: 120}[c]


def weekday_factor(d, consumer):
    if consumer.startswith("BATCH"):
        # 배치는 월말/분기말에만
        nxt = d + timedelta(days=1)
        return 8.0 if nxt.day == 1 else 0.02
    return 0.15 if d.weekday() >= 5 else 1.0


def p95_for(api, day_idx):
    base = api.get("slo_p95_ms", 800)
    if api in slo_violators:
        return int(base * random.uniform(1.4, 2.3))
    return int(base * random.uniform(0.45, 0.92))


# ---------------------------------------------------------------- 집계 생성
stats_rows = []
for day_off in range(STATS_DAYS):
    d = TODAY - timedelta(days=day_off + 1)
    for api in callable_apis + still_called + shadow:
        reg = registry_by_id.get(api["api_id"])
        if api in still_called:
            if random.random() > 0.35:
                continue
        for consumer in consumers_for(api):
            vol = base_volume(reg or api) * weekday_factor(d, consumer)
            vol *= random.uniform(0.75, 1.25)
            if api["api_id"] == spike_api["api_id"] and consumer == SPIKE_CONSUMER and day_off < 7:
                vol *= 4.5
            calls = max(0, int(vol / max(1, len(consumers_for(api)))))
            if calls == 0:
                continue
            err_rate = random.uniform(0.001, 0.012)
            if reg in slo_violators:
                err_rate = random.uniform(0.03, 0.09)
            err = int(calls * err_rate)
            c401 = c403 = c404 = 0
            if api["api_id"] == authz_api["api_id"] and consumer == AUTHZ_CONSUMER and day_off < 10:
                c401 = random.randint(180, 420)
                c403 = random.randint(60, 150)
            if api["api_id"] == enum_api["api_id"] and consumer == ENUM_CONSUMER and day_off < 5:
                c404 = random.randint(400, 900)
            p95 = p95_for(reg or api, day_off)
            stats_rows.append({
                "stat_date": d.isoformat(),
                "api_id": api["api_id"],
                "consumer_system": consumer,
                "gateway": api.get("gateway", "APISIX"),
                "call_count": calls + c401 + c403 + c404,
                "error_count": err,
                "count_401": c401,
                "count_403": c403,
                "count_404": c404,
                "count_429": int(calls * random.uniform(0, 0.002)),
                "p50_ms": int(p95 * 0.35),
                "p95_ms": p95,
                "p99_ms": int(p95 * 1.45),
                "avg_ms": int(p95 * 0.42),
                "req_bytes_avg": random.randint(200, 2400),
                "res_bytes_avg": random.randint(400, 18000),
            })


# ---------------------------------------------------------------- 원천 로그
STATUS_POOL = [200] * 92 + [201] * 3 + [400, 401, 403, 404, 429, 500]
log_rows = []
recent = [s for s in stats_rows
          if date.fromisoformat(s["stat_date"]) >= TODAY - timedelta(days=LOG_DAYS)]
SAMPLE_RATE = 0.012   # 전수 로그는 과대하므로 샘플링

for s in recent:
    n = max(1, int(s["call_count"] * SAMPLE_RATE))
    n = min(n, 40)
    reg = registry_by_id.get(s["api_id"])
    base_day = date.fromisoformat(s["stat_date"])
    path = reg["path_template"] if reg else next(
        (a["path_template"] for a in shadow if a["api_id"] == s["api_id"]), "/api/unknown")
    method = reg["http_method"] if reg else "GET"
    for _ in range(n):
        hh = random.choices(range(24),
                            weights=[1,1,1,1,1,2,4,9,14,16,15,13,8,14,16,15,12,9,5,3,2,2,1,1])[0]
        ts = datetime(base_day.year, base_day.month, base_day.day,
                      hh, random.randint(0, 59), random.randint(0, 59),
                      random.randint(0, 999) * 1000, tzinfo=timezone.utc)
        if s["count_404"] and random.random() < 0.5:
            status = 404
        elif s["count_401"] and random.random() < 0.4:
            status = random.choice([401, 403])
        else:
            status = random.choice(STATUS_POOL)
        lat = max(5, int(random.gauss(s["avg_ms"], s["avg_ms"] * 0.5)))
        if status >= 500:
            lat = int(lat * 2.2)
        upstream = int(lat * random.uniform(0.6, 0.95))
        log_rows.append({
            "timestamp": ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "api_id": s["api_id"],
            "http_method": method,
            "path_template": path,
            "consumer_system": s["consumer_system"],
            "consumer_client_id": f"cli-{s['consumer_system'].lower()}-01",
            "subject_hash": hashlib.sha256(
                f"{s['consumer_system']}{random.randint(1, 180)}".encode()).hexdigest()[:16],
            "status_code": status,
            "latency_total_ms": lat,
            "latency_upstream_ms": upstream,
            "trace_id": hashlib.md5(f"{ts}{random.random()}".encode()).hexdigest(),
            "gateway": s["gateway"],
            "gateway_node": f"gw-{random.randint(1, 3):02d}",
            "request_bytes": max(0, int(random.gauss(s["req_bytes_avg"], 300))),
            "response_bytes": max(0, int(random.gauss(s["res_bytes_avg"], 2000))),
            "auth_result": "DENY" if status in (401, 403) else "ALLOW",
        })

random.shuffle(log_rows)
log_rows.sort(key=lambda r: r["timestamp"])


# ---------------------------------------------------------------- 파일 출력
def write(name, rows, fields):
    with open(f"{OUT}/{name}", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"{name:28s} {len(rows):>8,} rows")


write("api_registry.csv", registry, [
    "api_id", "path_template", "http_method", "version", "domain_code", "domain_name",
    "provider_system", "owner_org", "owner_id", "criticality", "lifecycle_status",
    "auth_type", "authz_policy_id", "rate_limit_rpm", "gw_rate_limit_rpm",
    "slo_p95_ms", "slo_availability", "openapi_url", "gateway",
    "registered_at", "deprecate_date"])

write("consumer_system.csv",
      [{"consumer_system": c, "consumer_name": n, "consumer_type": t,
        "owner_org": random.choice(OWNER_ORGS)} for c, n, t in CONSUMERS],
      ["consumer_system", "consumer_name", "consumer_type", "owner_org"])

write("api_daily_stats.csv", stats_rows, [
    "stat_date", "api_id", "consumer_system", "gateway", "call_count", "error_count",
    "count_401", "count_403", "count_404", "count_429",
    "p50_ms", "p95_ms", "p99_ms", "avg_ms", "req_bytes_avg", "res_bytes_avg"])

write("api_call_log.csv", log_rows, [
    "timestamp", "api_id", "http_method", "path_template", "consumer_system",
    "consumer_client_id", "subject_hash", "status_code", "latency_total_ms",
    "latency_upstream_ms", "trace_id", "gateway", "gateway_node",
    "request_bytes", "response_bytes", "auth_result"])

write("expected_detections.csv",
      [{"rule_code": a, "api_id": b, "note": c} for a, b, c in sorted(expected)],
      ["rule_code", "api_id", "note"])

print("\n--- 요약 ---")
print(f"Registry API            : {len(registry):,}")
print(f"미등록(Shadow) API      : {len(shadow)}")
print(f"집계 기간               : {STATS_DAYS}일")
print(f"원천 로그 기간          : {LOG_DAYS}일 (샘플링 {SAMPLE_RATE:.1%})")
print(f"심어둔 이상 케이스      : {len(expected)}건")
