"""CSV 5종을 PostgreSQL에 적재 (최초 1회)."""
import os
from pathlib import Path
from .db import pool

DATA = Path(os.environ.get("DATA_DIR", "/data"))
FILES = [
    ("api_registry", "api_registry.csv"),
    ("consumer_system", "consumer_system.csv"),
    ("api_daily_stats", "api_daily_stats.csv"),
    ("api_call_log", "api_call_log.csv"),
]


def init_db():
    with pool.connection() as c:
        c.execute((Path(__file__).parent / "schema.sql").read_text())
        for table, fname in FILES:
            n = c.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
            if n:
                continue
            raw = (DATA / fname).read_bytes().decode("utf-8-sig")
            header = raw.split("\n", 1)[0].strip()
            cols = ",".join(f'"{h}"' for h in header.split(","))
            with c.cursor().copy(f"COPY {table} ({cols}) FROM STDIN WITH (FORMAT csv, HEADER true)") as cp:
                cp.write(raw.encode())
            print(f"loaded {table}", flush=True)
