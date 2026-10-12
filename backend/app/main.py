import csv, io
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Optional

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from psycopg.types.json import Jsonb

from . import detect
from .config import AS_OF
from .db import pool, q, q1, run
from .loader import init_db

LAST = AS_OF - timedelta(days=1)  # 집계 최종 일자


@asynccontextmanager
async def lifespan(app):
    pool.open()
    init_db()
    if q1("SELECT count(*) AS n FROM detection_result")["n"] == 0:
        print("detections:", detect.run_all(), flush=True)
    yield
    pool.close()


app = FastAPI(title="API Intelligence Dashboard", lifespan=lifespan, root_path="")
P = "/api/intel/v1"


def wrap(data, **meta):
    return {"data": data, "meta": {"asOf": AS_OF.isoformat(), **meta}}


def rng(frm, to, days=30):
    to = to or LAST
    return (frm or to - timedelta(days=days - 1)), to


def sfilter(frm, to, domain=None, gateway=None, consumer=None, days=30):
    """stats(s) + registry(r) 조인용 WHERE 절."""
    frm, to = rng(frm, to, days)
    w, p = ["s.stat_date BETWEEN %s AND %s"], [frm, to]
    if domain:
        w.append("r.domain_code = %s"); p.append(domain)
    if gateway:
        w.append("s.gateway = %s"); p.append(gateway)
    if consumer:
        w.append("s.consumer_system = %s"); p.append(consumer)
    return " AND ".join(w), p, frm, to


def as_csv(rows, name):
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: (str(v) if isinstance(v, (dict, list)) else v) for k, v in r.items()})
    return StreamingResponse(iter(["﻿" + buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


# ------------------------------------------------------------------ meta
@app.get(P + "/meta/filters")
def filters():
    return wrap({
        "domains": q("SELECT DISTINCT domain_code AS code, domain_name AS name FROM api_registry ORDER BY 1"),
        "gateways": [r["gateway"] for r in q("SELECT DISTINCT gateway FROM api_registry ORDER BY 1")],
        "consumers": q("SELECT consumer_system AS code, consumer_name AS name, consumer_type AS type FROM consumer_system ORDER BY 1"),
        "lastStatDate": LAST,
        "dataStale": (AS_OF - (q1("SELECT max(stat_date) AS d FROM api_daily_stats")["d"])).days > 1,
    })


# ------------------------------------------------------------------ assets
@app.get(P + "/assets/summary")
def assets_summary():
    reg = q1("SELECT count(*) AS n FROM api_registry")["n"]
    used = q1("""SELECT count(DISTINCT s.api_id) AS n FROM api_daily_stats s JOIN api_registry r USING (api_id)
                 WHERE s.stat_date > %s::date - 90 AND s.call_count > 0""", (AS_OF,))["n"]
    cnt = {r["rule_code"]: r["n"] for r in q("SELECT rule_code, count(*) AS n FROM detection_result WHERE status <> 'EXCEPTION' GROUP BY 1")}
    unreg, retire = cnt.get("UNREGISTERED", 0), cnt.get("UNUSED", 0)
    return wrap({
        "registered": reg, "activelyUsed": used, "unregistered": unreg, "retireCandidates": retire,
        "ownerMissing": cnt.get("OWNER_MISSING", 0), "deprecatedButCalled": cnt.get("DEPRECATED_CALLED", 0),
        "quadrant": {"registeredUsed": used, "registeredUnused": reg - used,
                     "unregisteredUsed": unreg, "unregisteredUnused": None},
    }, windowDays=90)


@app.get(P + "/assets/apis")
def assets_apis(domain: Optional[str] = None, status: Optional[str] = None, gateway: Optional[str] = None,
                criticality: Optional[int] = None, owner_missing: bool = False, no_calls: bool = False,
                keyword: Optional[str] = None, sort: str = "api_id", desc: bool = False,
                page: int = 1, size: int = 50, export: Optional[str] = None):
    w, p = ["TRUE"], []
    for col, v in (("r.domain_code", domain), ("r.lifecycle_status", status), ("r.gateway", gateway), ("r.criticality", criticality)):
        if v is not None:
            w.append(f"{col} = %s"); p.append(v)
    if owner_missing:
        w.append("(coalesce(trim(r.owner_org),'')='' OR coalesce(trim(r.owner_id),'')='')")
    if no_calls:
        w.append("coalesce(c.calls30d,0) = 0")
    if keyword:
        w.append("(r.api_id ILIKE %s OR r.path_template ILIKE %s)"); p += [f"%{keyword}%"] * 2
    cols = {"api_id", "path_template", "domain_code", "owner_org", "lifecycle_status", "criticality", "calls30d", "last_call_date", "registered_at"}
    if sort not in cols:
        sort = "api_id"
    base = f"""FROM api_registry r LEFT JOIN (
        SELECT api_id, sum(call_count) FILTER (WHERE stat_date > %s::date - 30) AS calls30d, max(stat_date) FILTER (WHERE call_count>0) AS last_call_date
        FROM api_daily_stats GROUP BY api_id) c USING (api_id) WHERE {' AND '.join(w)}"""
    p = [AS_OF] + p
    sel = "SELECT r.api_id, r.http_method, r.path_template, r.domain_code, r.domain_name, r.provider_system, r.owner_org, r.owner_id, r.criticality, r.lifecycle_status, r.gateway, r.registered_at, coalesce(c.calls30d,0) AS calls30d, c.last_call_date "
    order = f" ORDER BY {'r.' if sort in ('api_id','path_template','domain_code','owner_org','lifecycle_status','criticality','registered_at') else ''}{sort} {'DESC' if desc else 'ASC'} NULLS LAST, r.api_id"
    if export == "csv":
        return as_csv(q(sel + base + order, p), "assets_apis")
    total = q1("SELECT count(*) AS n " + base, p)["n"]
    rows = q(sel + base + order + " LIMIT %s OFFSET %s", p + [size, (page - 1) * size])
    return wrap(rows, rowCount=total, page=page, size=size)


@app.get(P + "/assets/distribution")
def assets_distribution():
    dom = q("""SELECT r.domain_code AS code, r.domain_name AS name, count(*) AS registered,
               count(*) FILTER (WHERE u.api_id IS NOT NULL) AS active
               FROM api_registry r LEFT JOIN (SELECT DISTINCT api_id FROM api_daily_stats WHERE stat_date > %s::date - 90 AND call_count>0) u USING (api_id)
               GROUP BY 1,2 ORDER BY registered DESC""", (AS_OF,))
    gw = q("SELECT gateway, count(*) AS registered FROM api_registry GROUP BY 1 ORDER BY 1")
    return wrap({"domains": dom, "gateways": gw})


@app.get(P + "/assets/trend")
def assets_trend():
    rows = q("""
        WITH months AS (SELECT generate_series(date_trunc('month', %(d)s::date - 365)::date, date_trunc('month', %(d)s::date)::date, '1 month') AS m),
        act AS (SELECT date_trunc('month', stat_date)::date AS m, count(DISTINCT api_id) AS active FROM api_daily_stats s
                JOIN api_registry USING (api_id) WHERE call_count>0 GROUP BY 1)
        SELECT to_char(m.m,'YYYY-MM') AS month,
               (SELECT count(*) FROM api_registry WHERE registered_at < (m.m + interval '1 month')) AS registered,
               act.active FROM months m LEFT JOIN act USING (m) ORDER BY m.m""", {"d": AS_OF})
    return wrap(rows)


# ------------------------------------------------------------------ quality
@app.get(P + "/quality/summary")
def quality_summary(frm: Optional[date] = Query(None, alias="from"), to: Optional[date] = None,
                    domain: Optional[str] = None, gateway: Optional[str] = None, consumer: Optional[str] = None):
    w, p, f, t = sfilter(frm, to, domain, gateway, consumer)
    r = q1(f"""SELECT coalesce(sum(call_count),0) AS calls, coalesce(sum(error_count),0) AS errors,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY p95_ms) AS p95_median
               FROM api_daily_stats s LEFT JOIN api_registry r USING (api_id) WHERE {w}""", p)
    br = q1("SELECT count(*) AS n FROM detection_result WHERE rule_code='SLO_BREACH' AND status<>'EXCEPTION'")["n"]
    return wrap({"totalCalls": int(r["calls"]), "errorRate": float(r["errors"]) / max(int(r["calls"]), 1),
                 "sloBreachApis": br, "p95Median": round(float(r["p95_median"] or 0))}, **{"from": f, "to": t})


@app.get(P + "/quality/trend")
def quality_trend(frm: Optional[date] = Query(None, alias="from"), to: Optional[date] = None,
                  domain: Optional[str] = None, gateway: Optional[str] = None, consumer: Optional[str] = None):
    w, p, f, t = sfilter(frm, to, domain, gateway, consumer, days=90)
    rows = q(f"""SELECT s.stat_date AS date, s.gateway, sum(call_count) AS calls, sum(error_count) AS errors
                 FROM api_daily_stats s LEFT JOIN api_registry r USING (api_id) WHERE {w} GROUP BY 1,2 ORDER BY 1,2""", p)
    return wrap([{**r, "calls": int(r["calls"]), "errors": int(r["errors"])} for r in rows], **{"from": f, "to": t})


@app.get(P + "/quality/latency")
def quality_latency(frm: Optional[date] = Query(None, alias="from"), to: Optional[date] = None,
                    domain: Optional[str] = None, gateway: Optional[str] = None, consumer: Optional[str] = None,
                    criticality: int = 1):
    w, p, f, t = sfilter(frm, to, domain, gateway, consumer)
    rows = q(f"""SELECT r.api_id, r.path_template, r.http_method, r.slo_p95_ms,
                 round(sum(s.p50_ms::numeric*s.call_count)/nullif(sum(s.call_count),0)) AS p50,
                 round(sum(s.p95_ms::numeric*s.call_count)/nullif(sum(s.call_count),0)) AS p95,
                 round(sum(s.p99_ms::numeric*s.call_count)/nullif(sum(s.call_count),0)) AS p99
                 FROM api_daily_stats s JOIN api_registry r USING (api_id) WHERE {w} AND r.criticality = %s
                 GROUP BY 1,2,3,4 HAVING sum(s.call_count) > 0 ORDER BY r.api_id""", p + [criticality])
    return wrap([{**r, **{k: int(r[k]) for k in ("p50", "p95", "p99")}} for r in rows], **{"from": f, "to": t})


@app.get(P + "/quality/slo-breaches")
def slo_breaches(export: Optional[str] = None):
    rows = q("""SELECT d.api_id, r.path_template, r.owner_org, r.slo_p95_ms,
                (d.evidence->>'p95Ms')::int AS p95_ms, (d.evidence->>'consecutiveDays')::int AS consecutive_days,
                round(((d.evidence->>'p95Ms')::numeric / r.slo_p95_ms - 1) * 100, 1) AS over_pct, d.status
                FROM detection_result d JOIN api_registry r USING (api_id)
                WHERE d.rule_code='SLO_BREACH' AND d.status <> 'EXCEPTION' ORDER BY over_pct DESC""")
    rows = [{**r, "over_pct": float(r["over_pct"])} for r in rows]
    return as_csv(rows, "slo_breaches") if export == "csv" else wrap(rows, rowCount=len(rows))


# ------------------------------------------------------------------ dependency
@app.get(P + "/dependency/graph")
def dep_graph(frm: Optional[date] = Query(None, alias="from"), to: Optional[date] = None,
              domain: Optional[str] = None, gateway: Optional[str] = None, consumer: Optional[str] = None,
              limit_apis: int = 60):
    w, p, f, t = sfilter(frm, to, domain, gateway, consumer)
    edges = q(f"""SELECT s.consumer_system AS c, s.api_id AS a, sum(call_count) AS calls, sum(error_count) AS errors
                  FROM api_daily_stats s LEFT JOIN api_registry r USING (api_id) WHERE {w} GROUP BY 1,2 HAVING sum(call_count)>0""", p)
    top = {r["a"] for r in sorted(
        q(f"SELECT s.api_id AS a, sum(call_count) AS n FROM api_daily_stats s LEFT JOIN api_registry r USING (api_id) WHERE {w} GROUP BY 1", p),
        key=lambda r: -r["n"])[:limit_apis]}
    edges = [e for e in edges if e["a"] in top]
    apis = {r["api_id"]: r for r in q("SELECT api_id, path_template, http_method, criticality, provider_system, domain_code FROM api_registry")}
    cons = {r["consumer_system"]: r for r in q("SELECT * FROM consumer_system")}
    nodes, out = {}, []
    for e in edges:
        a = apis.get(e["a"])
        nodes[e["c"]] = {"id": e["c"], "type": "CONSUMER", "label": cons.get(e["c"], {}).get("consumer_name", e["c"])}
        nodes[e["a"]] = {"id": e["a"], "type": "API", "label": (a["http_method"] + " " + a["path_template"]) if a else e["a"],
                         "criticality": a["criticality"] if a else None, "domain": a["domain_code"] if a else None}
        out.append({"from": e["c"], "to": e["a"], "calls": int(e["calls"]), "errorRate": float(e["errors"]) / max(int(e["calls"]), 1)})
        if a:
            nodes["P:" + a["provider_system"]] = {"id": "P:" + a["provider_system"], "type": "PROVIDER", "label": a["provider_system"]}
            out.append({"from": e["a"], "to": "P:" + a["provider_system"]})
    seen, uniq = set(), []
    for e in out:
        k = (e["from"], e["to"])
        if k not in seen:
            seen.add(k); uniq.append(e)
    return wrap({"nodes": list(nodes.values()), "edges": uniq}, **{"from": f, "to": t})


@app.get(P + "/dependency/impact/{api_id}")
def dep_impact(api_id: str):
    rows = q("""SELECT s.consumer_system, c.consumer_name, c.consumer_type, sum(s.call_count) AS calls30d,
                sum(s.error_count)::float / nullif(sum(s.call_count),0) AS error_rate, max(s.stat_date) FILTER (WHERE s.call_count>0) AS last_call
                FROM api_daily_stats s LEFT JOIN consumer_system c USING (consumer_system)
                WHERE s.api_id=%s AND s.stat_date > %s::date - 30 GROUP BY 1,2,3 HAVING sum(s.call_count)>0 ORDER BY calls30d DESC""", (api_id, AS_OF))
    return wrap([{**r, "calls30d": int(r["calls30d"])} for r in rows], rowCount=len(rows))


# ------------------------------------------------------------------ governance
@app.get(P + "/governance/detections")
def detections(rule: Optional[str] = None, status: Optional[str] = None, owner_org: Optional[str] = None,
               severity: Optional[str] = None, include_exception: bool = False,
               sort: str = "severity", page: int = 1, size: int = 50, export: Optional[str] = None):
    w, p = ["TRUE"], []
    if not include_exception and not status:
        w.append("d.status <> 'EXCEPTION'")
    for col, v in (("d.rule_code", rule), ("d.status", status), ("d.owner_org", owner_org), ("d.severity", severity)):
        if v:
            w.append(f"{col} = %s"); p.append(v)
    order = {"severity": "CASE d.severity WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END, d.first_detected_at",
             "elapsed": "d.first_detected_at", "rule": "d.rule_code, d.api_id"}.get(sort, "d.id")
    base = f"FROM detection_result d WHERE {' AND '.join(w)}"
    sel = f"""SELECT d.id, d.rule_code AS "ruleCode", d.severity, d.api_id AS "apiId", d.owner_org AS "ownerOrg",
              d.detected_at AS "detectedAt", d.first_detected_at AS "firstDetectedAt",
              ({AS_OF.isoformat()!r}::date - d.first_detected_at::date) AS "elapsedDays", d.evidence, d.status, d.comment """
    if export == "csv":
        return as_csv(q(sel + base + " ORDER BY " + order), "detections")
    total = q1("SELECT count(*) AS n " + base, p)["n"]
    rows = q(sel + base + " ORDER BY " + order + " LIMIT %s OFFSET %s", p + [size, (page - 1) * size])
    return wrap(rows, rowCount=total, page=page, size=size)


@app.patch(P + "/governance/detections/{det_id}")
def detection_patch(det_id: int, body: dict = Body(...)):
    st = body.get("status")
    if st not in ("OPEN", "ACK", "RESOLVED", "EXCEPTION"):
        raise HTTPException(400, "invalid status")
    r = q1("""UPDATE detection_result SET status=%s, comment=%s, resolver_id=%s,
              resolved_at = CASE WHEN %s IN ('RESOLVED','EXCEPTION') THEN now() ELSE NULL END WHERE id=%s RETURNING id, status""",
           (st, body.get("comment"), body.get("resolverId", "demo-user"), st, det_id))
    if not r:
        raise HTTPException(404)
    run("INSERT INTO audit_log (actor, action, detail) VALUES (%s,%s,%s)", ("demo-user", "DETECTION_STATUS", Jsonb({"id": det_id, "status": st})))
    return wrap(r)


@app.post(P + "/governance/run-detection")
def run_detection():
    return wrap(detect.run_all())


@app.get(P + "/governance/by-rule")
def by_rule():
    rows = q("SELECT rule_code AS rule, status, count(*) AS n FROM detection_result GROUP BY 1,2")
    return wrap(rows)


@app.get(P + "/governance/compliance")
def compliance():
    r = q1("""SELECT count(*) AS total,
              count(*) FILTER (WHERE coalesce(trim(owner_org),'')<>'' AND coalesce(trim(owner_id),'')<>'') AS owned,
              count(*) FILTER (WHERE rate_limit_rpm = gw_rate_limit_rpm) AS policy_ok,
              count(*) FILTER (WHERE slo_p95_ms IS NOT NULL AND slo_availability IS NOT NULL) AS slo_def FROM api_registry""")
    unreg = q1("SELECT count(*) AS n FROM detection_result WHERE rule_code='UNREGISTERED'")["n"]
    t = r["total"]
    return wrap({"registrationRate": t / (t + unreg), "ownerRate": r["owned"] / t,
                 "policyRate": r["policy_ok"] / t, "sloDefinedRate": r["slo_def"] / t,
                 "targets": {"registrationRate": 1.0, "ownerRate": 1.0, "policyRate": 1.0, "sloDefinedRate": 1.0}})


@app.get(P + "/governance/anomalies")
def anomalies(frm: Optional[date] = Query(None, alias="from"), to: Optional[date] = None, consumer: Optional[str] = None):
    frm, to = rng(frm, to, 30)
    w, p = "stat_date BETWEEN %s AND %s", [frm, to]
    if consumer:
        w += " AND consumer_system=%s"; p.append(consumer)
    rows = q(f"""SELECT stat_date AS date, consumer_system AS consumer, sum(count_401) AS c401, sum(count_403) AS c403,
                 sum(count_404) AS c404, sum(count_429) AS c429 FROM api_daily_stats WHERE {w}
                 GROUP BY 1,2 HAVING sum(count_401+count_403+count_404+count_429) > 0 ORDER BY 1,2""", p)
    return wrap([{**r, **{k: int(r[k]) for k in ("c401", "c403", "c404", "c429")}} for r in rows],
                thresholds={"authz": 50, "enum": 100}, **{"from": frm, "to": to})


# ------------------------------------------------------------------ api detail
@app.get(P + "/apis/{api_id}")
def api_detail(api_id: str):
    reg = q1("SELECT * FROM api_registry WHERE api_id=%s", (api_id,))
    stats = q("""SELECT stat_date AS date, sum(call_count) AS calls, sum(error_count) AS errors,
                 round(sum(p95_ms::numeric*call_count)/nullif(sum(call_count),0)) AS p95
                 FROM api_daily_stats WHERE api_id=%s AND stat_date > %s::date - 90 GROUP BY 1 ORDER BY 1""", (api_id, AS_OF))
    if not reg and not stats and not q1("SELECT 1 AS x FROM api_call_log WHERE api_id=%s LIMIT 1", (api_id,)):
        raise HTTPException(404)
    dets = q("""SELECT id, rule_code AS "ruleCode", severity, status, evidence FROM detection_result WHERE api_id=%s ORDER BY id""", (api_id,))
    return wrap({"registry": reg, "unregistered": reg is None,
                 "daily": [{**s, "calls": int(s["calls"]), "errors": int(s["errors"]), "p95": int(s["p95"] or 0)} for s in stats],
                 "detections": dets})


@app.get(P + "/apis/{api_id}/calls")
def api_calls(api_id: str, request: Request, limit: int = 200, status: Optional[int] = None, consumer: Optional[str] = None):
    limit = min(limit, 1000)
    w, p = ["api_id=%s"], [api_id]
    if status:
        w.append("status_code=%s"); p.append(status)
    if consumer:
        w.append("consumer_system=%s"); p.append(consumer)
    rows = q(f"""SELECT "timestamp", consumer_system, consumer_client_id, subject_hash, status_code, auth_result,
                 latency_total_ms, latency_upstream_ms, latency_total_ms - latency_upstream_ms AS gw_overhead_ms,
                 trace_id, gateway, gateway_node, request_bytes, response_bytes
                 FROM api_call_log WHERE {' AND '.join(w)} ORDER BY "timestamp" DESC LIMIT %s""", p + [limit])
    run("INSERT INTO audit_log (actor, action, detail) VALUES (%s,%s,%s)",
        ("demo-user", "CALL_LOG_QUERY", Jsonb({"apiId": api_id, "status": status, "consumer": consumer, "limit": limit})))
    return wrap(rows, rowCount=len(rows))
