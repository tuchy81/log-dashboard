import os
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

pool = ConnectionPool(
    os.environ.get("DATABASE_URL", "postgresql://dash:dash@db:5432/dash"),
    min_size=1, max_size=8, kwargs={"row_factory": dict_row}, open=False,
)


def q(sql, params=None):
    with pool.connection() as c:
        return c.execute(sql, params).fetchall()


def q1(sql, params=None):
    with pool.connection() as c:
        return c.execute(sql, params).fetchone()


def run(sql, params=None):
    with pool.connection() as c:
        c.execute(sql, params)
