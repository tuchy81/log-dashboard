"""탐지 규칙 배치 (스펙 9장). 규칙별 독립 함수, (rule_code, api_id) 중복 억제."""
import json
from datetime import datetime, time, timezone
from psycopg.types.json import Jsonb
from .config import AS_OF
from .db import pool, q

SEV = {
    "UNREGISTERED": "HIGH", "DEPRECATED_CALLED": "HIGH", "AUTHZ_FAILURE": "HIGH",
    "RESOURCE_ENUM": "HIGH", "POLICY_MISMATCH": "MEDIUM", "SLO_BREACH": "MEDIUM",
    "CONSUMER_SPIKE": "MEDIUM", "OWNER_MISSING": "LOW", "UNUSED": "LOW",
}
NOW = datetime.combine(AS_OF, time(2, 0), tzinfo=timezone.utc)


def _owner(api_id):
    r = q("SELECT owner_org FROM api_registry WHERE api_id=%s", (api_id,))
    return (r[0]["owner_org"] if r else None) or None


def unregistered():
    rows = q("""
        SELECT api_id, sum(n) AS calls, array_agg(DISTINCT consumer_system) AS consumers FROM (
          SELECT api_id, consumer_system, call_count AS n FROM api_daily_stats
          UNION ALL SELECT api_id, consumer_system, 1 FROM api_call_log
        ) t WHERE api_id NOT IN (SELECT api_id FROM api_registry) GROUP BY api_id""")
    return [dict(api_id=r["api_id"], evidence={"calls": int(r["calls"]), "consumers": r["consumers"]}) for r in rows]


def deprecated_called():
    rows = q("""
        SELECT r.api_id, sum(s.call_count) AS calls7d, (array_agg(s.consumer_system ORDER BY s.call_count DESC))[1] AS top
        FROM api_registry r JOIN api_daily_stats s USING (api_id)
        WHERE r.lifecycle_status='DEPRECATED' AND s.stat_date > %s::date - 7 AND s.call_count > 0
        GROUP BY r.api_id""", (AS_OF,))
    return [dict(api_id=r["api_id"], evidence={"calls7d": int(r["calls7d"]), "consumerSystem": r["top"]}) for r in rows]


def _daily_hits(col_expr, threshold, key):
    rows = q(f"""
        SELECT api_id, consumer_system, min(stat_date) AS first_day, max(stat_date) AS last_day,
               count(*) AS days, max(v) AS peak FROM (
          SELECT api_id, consumer_system, stat_date, sum({col_expr}) AS v FROM api_daily_stats
          WHERE stat_date > %s::date - 7 GROUP BY 1,2,3) t
        WHERE v >= %s GROUP BY api_id, consumer_system""", (AS_OF, threshold))
    return [dict(api_id=r["api_id"], first=r["first_day"],
                 evidence={"consumerSystem": r["consumer_system"], "days": r["days"], key: int(r["peak"])}) for r in rows]


def authz_failure():
    return _daily_hits("count_401 + count_403", 50, "peakPerDay")


def resource_enum():
    return _daily_hits("count_404", 100, "peakPerDay")


def policy_mismatch():
    rows = q("SELECT api_id, rate_limit_rpm, gw_rate_limit_rpm FROM api_registry WHERE rate_limit_rpm <> gw_rate_limit_rpm")
    return [dict(api_id=r["api_id"], evidence={"rateLimitRpm": r["rate_limit_rpm"], "gwRateLimitRpm": r["gw_rate_limit_rpm"]}) for r in rows]


def slo_breach():
    rows = q("""
        SELECT s.api_id, s.stat_date, (sum(s.p95_ms::numeric * s.call_count) / sum(s.call_count)) AS p95, r.slo_p95_ms
        FROM api_daily_stats s JOIN api_registry r USING (api_id)
        WHERE s.stat_date > %s::date - 30 AND s.call_count > 0 AND r.slo_p95_ms IS NOT NULL
        GROUP BY s.api_id, s.stat_date, r.slo_p95_ms ORDER BY s.api_id, s.stat_date DESC""", (AS_OF,))
    by = {}
    for r in rows:
        by.setdefault(r["api_id"], []).append(r)
    out = []
    for api, days in by.items():
        streak = 0
        for d in days:  # 최신일부터 연속 위반 일수
            if d["p95"] > d["slo_p95_ms"]:
                streak += 1
            else:
                break
        if streak >= 2:
            out.append(dict(api_id=api, first=days[streak - 1]["stat_date"], evidence={
                "sloP95Ms": days[0]["slo_p95_ms"], "p95Ms": round(float(days[0]["p95"])), "consecutiveDays": streak}))
    return out


def consumer_spike():
    rows = q("""
        SELECT api_id, consumer_system,
          sum(call_count) FILTER (WHERE stat_date > %(d)s::date - 7) / 7.0 AS recent,
          sum(call_count) FILTER (WHERE stat_date <= %(d)s::date - 7 AND stat_date > %(d)s::date - 35) / 28.0 AS prior
        FROM api_daily_stats GROUP BY api_id, consumer_system""", {"d": AS_OF})
    rows = [{**r, "recent": float(r["recent"] or 0), "prior": float(r["prior"] or 0)} for r in rows]
    return [dict(api_id=r["api_id"], evidence={"consumerSystem": r["consumer_system"],
            "avg7d": round(r["recent"], 1), "avg28d": round(r["prior"], 1), "ratio": round(r["recent"] / r["prior"], 2)})
            for r in rows if r["recent"] and r["prior"] and r["recent"] > r["prior"] * 3.0]


def owner_missing():
    rows = q("""SELECT api_id FROM api_registry WHERE coalesce(trim(owner_org),'')='' OR coalesce(trim(owner_id),'')=''""")
    return [dict(api_id=r["api_id"], evidence={}) for r in rows]


def unused():
    rows = q("""
        SELECT r.api_id,
          coalesce(sum(s.call_count) FILTER (WHERE s.stat_date > %(d)s::date - 90), 0) AS c90,
          coalesce(sum(s.call_count) FILTER (WHERE s.stat_date > %(d)s::date - 395), 0) AS c13m
        FROM api_registry r LEFT JOIN api_daily_stats s USING (api_id)
        WHERE r.lifecycle_status='ACTIVE' AND r.registered_at <= %(d)s::date - 90
        GROUP BY r.api_id HAVING coalesce(sum(s.call_count) FILTER (WHERE s.stat_date > %(d)s::date - 90), 0) = 0""", {"d": AS_OF})
    return [dict(api_id=r["api_id"], severity="LOW" if r["c13m"] > 0 else "MEDIUM",
                 evidence={"calls90d": 0, "calls13m": int(r["c13m"])}) for r in rows]


RULES = {
    "UNREGISTERED": unregistered, "DEPRECATED_CALLED": deprecated_called, "AUTHZ_FAILURE": authz_failure,
    "RESOURCE_ENUM": resource_enum, "POLICY_MISMATCH": policy_mismatch, "SLO_BREACH": slo_breach,
    "CONSUMER_SPIKE": consumer_spike, "OWNER_MISSING": owner_missing, "UNUSED": unused,
}


def run_all():
    """규칙 실행 → upsert. 이미 존재하는 (rule, api)는 detected_at/evidence만 갱신(상태·최초탐지일 유지)."""
    counts = {}
    with pool.connection() as c:
        for rule, fn in RULES.items():
            hits = fn()
            counts[rule] = len(hits)
            for h in hits:
                first = datetime.combine(h["first"], time(2, 0), tzinfo=timezone.utc) if h.get("first") else NOW
                c.execute("""
                    INSERT INTO detection_result (rule_code, severity, api_id, owner_org, detected_at, first_detected_at, evidence)
                    VALUES (%s,%s,%s,(SELECT owner_org FROM api_registry WHERE api_id=%s),%s,%s,%s)
                    ON CONFLICT (rule_code, api_id) DO UPDATE SET detected_at=EXCLUDED.detected_at, evidence=EXCLUDED.evidence""",
                          (rule, h.get("severity", SEV[rule]), h["api_id"], h["api_id"], NOW, first, Jsonb(h["evidence"])))
    return counts
