CREATE TABLE IF NOT EXISTS api_registry (
  api_id varchar(32) PRIMARY KEY,
  path_template varchar(255), http_method varchar(8), version varchar(8),
  domain_code varchar(8), domain_name varchar(32), provider_system varchar(32),
  owner_org varchar(64), owner_id varchar(32), criticality smallint,
  lifecycle_status varchar(16), auth_type varchar(16), authz_policy_id varchar(16),
  rate_limit_rpm int, gw_rate_limit_rpm int, slo_p95_ms int, slo_availability numeric(5,2),
  openapi_url varchar(255), gateway varchar(16), registered_at date, deprecate_date date
);
CREATE TABLE IF NOT EXISTS consumer_system (
  consumer_system varchar(32) PRIMARY KEY, consumer_name varchar(64),
  consumer_type varchar(16), owner_org varchar(64)
);
CREATE TABLE IF NOT EXISTS api_daily_stats (
  stat_date date, api_id varchar(32), consumer_system varchar(32), gateway varchar(16),
  call_count bigint, error_count bigint, count_401 bigint, count_403 bigint,
  count_404 bigint, count_429 bigint, p50_ms int, p95_ms int, p99_ms int, avg_ms int,
  req_bytes_avg int, res_bytes_avg int
);
CREATE INDEX IF NOT EXISTS ix_stats_date_api ON api_daily_stats (stat_date, api_id);
CREATE INDEX IF NOT EXISTS ix_stats_api_date ON api_daily_stats (api_id, stat_date);
CREATE INDEX IF NOT EXISTS ix_stats_cons_date ON api_daily_stats (consumer_system, stat_date);
CREATE TABLE IF NOT EXISTS api_call_log (
  "timestamp" timestamptz, api_id varchar(32), http_method varchar(8), path_template varchar(255),
  consumer_system varchar(32), consumer_client_id varchar(64), subject_hash varchar(32),
  status_code int, latency_total_ms int, latency_upstream_ms int, trace_id varchar(64),
  gateway varchar(16), gateway_node varchar(16), request_bytes int, response_bytes int,
  auth_result varchar(16)
);
CREATE INDEX IF NOT EXISTS ix_log_api_ts ON api_call_log (api_id, "timestamp" DESC);
CREATE TABLE IF NOT EXISTS detection_result (
  id serial PRIMARY KEY,
  rule_code varchar(32), severity varchar(8), api_id varchar(32), owner_org varchar(64),
  detected_at timestamptz, first_detected_at timestamptz, evidence jsonb,
  status varchar(16) DEFAULT 'OPEN', resolved_at timestamptz, resolver_id varchar(32), comment text,
  UNIQUE (rule_code, api_id)
);
CREATE TABLE IF NOT EXISTS audit_log (
  id serial PRIMARY KEY, at timestamptz DEFAULT now(), actor varchar(64), action varchar(32), detail jsonb
);
