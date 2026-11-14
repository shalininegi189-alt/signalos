"""DBMS MODULE 4 - Indexing and query optimisation.

A scratch database holds 200,000 sensor readings. The same analytical query is
run with no index, a composite B-tree index, and a covering index; the query
plans (EXPLAIN QUERY PLAN) and timings are returned.
"""
from __future__ import annotations

import random
import sqlite3
import time

import config

ROWS = 200_000
QUERY = ("SELECT ROUND(AVG(q_n + q_e + q_s + q_w), 3) AS avg_queue, COUNT(*) AS n "
         "FROM bench_reading WHERE node_id = ? AND tick BETWEEN ? AND ?")
ARGS = ("2-2", 40_000, 46_000)


def _prepare() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.BENCH_DB_PATH), isolation_level=None)
    conn.execute("PRAGMA journal_mode = OFF")
    conn.execute("PRAGMA synchronous = OFF")
    has = conn.execute("SELECT name FROM sqlite_master WHERE name = 'bench_reading'").fetchone()
    if has and conn.execute("SELECT COUNT(*) FROM bench_reading").fetchone()[0] == ROWS:
        return conn
    conn.execute("DROP TABLE IF EXISTS bench_reading")
    conn.execute("""CREATE TABLE bench_reading(
        id INTEGER PRIMARY KEY, node_id TEXT NOT NULL, tick INTEGER NOT NULL,
        q_n INTEGER, q_e INTEGER, q_s INTEGER, q_w INTEGER)""")
    rng = random.Random(5)
    nodes = [f"{r}-{c}" for r in range(4) for c in range(4)]
    conn.execute("BEGIN")
    conn.executemany(
        "INSERT INTO bench_reading(node_id,tick,q_n,q_e,q_s,q_w) VALUES (?,?,?,?,?,?)",
        ((nodes[i % 16], i // 16 * 5 + rng.randint(0, 4), rng.randint(0, 4), rng.randint(0, 4),
          rng.randint(0, 4), rng.randint(0, 4)) for i in range(ROWS)))
    conn.execute("COMMIT")
    return conn


def _time(conn, runs: int = 5) -> float:
    best = float("inf")
    for _ in range(runs):
        t = time.perf_counter()
        conn.execute(QUERY, ARGS).fetchall()
        best = min(best, time.perf_counter() - t)
    return round(best * 1000, 3)


def _plan(conn) -> list:
    return [r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + QUERY, ARGS).fetchall()]


def benchmark() -> dict:
    conn = _prepare()
    for name in ("idx_bench_node_tick", "idx_bench_cover"):
        conn.execute(f"DROP INDEX IF EXISTS {name}")
    stages = []
    result = conn.execute(QUERY, ARGS).fetchone()

    stages.append({"name": "No index", "ddl": None, "plan": _plan(conn), "ms": _time(conn)})
    ddl1 = "CREATE INDEX idx_bench_node_tick ON bench_reading(node_id, tick)"
    conn.execute(ddl1)
    stages.append({"name": "Composite B-tree index", "ddl": ddl1, "plan": _plan(conn), "ms": _time(conn)})
    ddl2 = "CREATE INDEX idx_bench_cover ON bench_reading(node_id, tick, q_n, q_e, q_s, q_w)"
    conn.execute(ddl2)
    conn.execute("DROP INDEX idx_bench_node_tick")
    stages.append({"name": "Covering index", "ddl": ddl2, "plan": _plan(conn), "ms": _time(conn)})
    base = stages[0]["ms"] or 0.001
    for s in stages:
        s["speedup"] = round(base / max(s["ms"], 0.001), 1)
    conn.execute("DROP INDEX IF EXISTS idx_bench_cover")
    conn.close()
    return {"rows": ROWS, "query": QUERY.replace("?", "{}").format(*[repr(a) for a in ARGS]),
            "result": dict(zip(("avg_queue", "n"), result)), "stages": stages}
