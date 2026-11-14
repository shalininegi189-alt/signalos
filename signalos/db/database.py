"""Connection handling, schema creation, seeding, event persistence.

The simulation never talks to SQL directly: it emits events into a queue
(producer) and DBWriter (consumer thread) writes them in batched transactions.
"""
from __future__ import annotations

import queue
import random
import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterable

import config

SCHEMA = Path(__file__).with_name("schema.sql")

VIOLATION_TYPES = [
    ("RED_LIGHT", "Ran a red light", 500.0),
    ("SPEEDING", "Exceeded the speed limit", 300.0),
    ("OVERSTAY", "Overstayed in the downtown garage", 150.0),
    ("BUS_LANE", "Used the bus lane", 250.0),
]
FIRST = ["Aarav", "Diya", "Kabir", "Meera", "Rohan", "Ananya", "Vihaan", "Ishita", "Arjun", "Saanvi",
         "Dev", "Tara", "Neel", "Riya", "Yash", "Kiara", "Aditya", "Zoya", "Karan", "Nisha"]
LAST = ["Sharma", "Verma", "Negi", "Rawat", "Bisht", "Joshi", "Singh", "Gupta", "Mehta", "Kapoor", "Nair", "Rao"]


def connect(path: Path | str = config.DB_PATH, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10, check_same_thread=False)
    else:
        conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None            # we issue BEGIN/COMMIT explicitly
    conn.execute("PRAGMA foreign_keys = ON")
    if not readonly:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA.read_text())


def seed_static(conn: sqlite3.Connection, topology: dict, seed: int = 3) -> None:
    rng = random.Random(seed)
    conn.execute("BEGIN")
    conn.executemany("INSERT OR IGNORE INTO intersection(node_id, grid_row, grid_col, name, zone) VALUES (?,?,?,?,?)",
                     [(n["id"], n["row"], n["col"], n.get("name"), n.get("zone")) for n in topology["nodes"]])
    conn.executemany("INSERT OR IGNORE INTO road(road_id, from_node, to_node, direction, length, capacity, street) VALUES (?,?,?,?,?,?,?)",
                     [(r["id"], r["from"], r["to"], r["dir"], r["length"], r["cap"], r.get("street")) for r in topology["roads"]])
    conn.executemany("INSERT OR IGNORE INTO violation_type VALUES (?,?,?)", VIOLATION_TYPES)
    conn.execute("INSERT OR IGNORE INTO treasury(id, balance) VALUES (1, 0)")
    for i in range(1, config.COMMUTERS + 1):
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        email = f"driver{i:03d}@signalos.example"
        conn.execute("INSERT OR IGNORE INTO owner(owner_id, name, email) VALUES (?,?,?)", (i, name, email))
        conn.execute("INSERT OR IGNORE INTO wallet VALUES (?,?)", (i, round(rng.uniform(300, 2500), 2)))
        conn.execute("INSERT OR IGNORE INTO vehicle VALUES (?,?,?)", (f"SO{i:03d}", i, "car"))
    conn.execute("COMMIT")


def migrate(conn: sqlite3.Connection, topology: dict) -> None:
    """Upgrade a database created before the Dehradun update: add name columns, refresh views."""
    def cols(table):
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if "name" not in cols("intersection"):
        conn.execute("ALTER TABLE intersection ADD COLUMN name TEXT")
        conn.execute("ALTER TABLE intersection ADD COLUMN zone TEXT")
    if "street" not in cols("road"):
        conn.execute("ALTER TABLE road ADD COLUMN street TEXT")
    conn.execute("BEGIN")
    conn.executemany("UPDATE intersection SET name = ?, zone = ? WHERE node_id = ?",
                     [(n.get("name"), n.get("zone"), n["id"]) for n in topology["nodes"]])
    conn.executemany("UPDATE road SET street = ? WHERE road_id = ?",
                     [(r.get("street"), r["id"]) for r in topology["roads"]])
    conn.execute("COMMIT")
    init_schema(conn)          # creates any view that is missing (CREATE VIEW IF NOT EXISTS)


def is_seeded(conn: sqlite3.Connection) -> bool:
    try:
        return conn.execute("SELECT COUNT(*) FROM intersection").fetchone()[0] > 0
    except sqlite3.Error:
        return False


def apply_events(conn: sqlite3.Connection, events: Iterable[tuple]) -> int:
    """Persist a batch of (kind, data) events in one transaction (atomic batch)."""
    rows = {k: [] for k in ("sensor", "signal", "trip", "violation", "parking", "deadlock", "incident")}
    for kind, d in events:
        rows.setdefault(kind, []).append(d)
    n = 0
    conn.execute("BEGIN")
    try:
        conn.executemany(
            "INSERT INTO sensor_reading(node_id,tick,q_n,q_e,q_s,q_w,green,policy) VALUES (?,?,?,?,?,?,?,?)",
            [(d["node"], d["tick"], d["q"].get("N", 0), d["q"].get("E", 0), d["q"].get("S", 0),
              d["q"].get("W", 0), d["green"], d["policy"]) for d in rows["sensor"]])
        conn.executemany(
            "INSERT INTO signal_event(node_id,tick,type,approach) VALUES (?,?,?,?)",
            [(d["node"], d["tick"], d["type"], d["approach"]) for d in rows["signal"]
             if d["type"] in ("GREEN", "SWITCH", "PREEMPT")])
        conn.executemany(
            "INSERT INTO trip(plate,origin,dest,spawn_tick,end_tick,wait_ticks,hops,towed) VALUES (?,?,?,?,?,?,?,?)",
            [(d["plate"], d["origin"], d["dest"], d["spawn"], d["end"], d["wait"], d["hops"], d["towed"])
             for d in rows["trip"]])
        for d in rows["violation"]:           # trigger issues the fine
            conn.execute("INSERT INTO violation(plate,node_id,code,tick) VALUES (?,?,?,?)",
                         (d["plate"], d["node"], d["code"], d["tick"]))
        conn.executemany(
            "INSERT INTO parking_event(tick,plate,hit,slot,evicted,policy) VALUES (?,?,?,?,?,?)",
            [(d["tick"], d["plate"], d["hit"], d["slot"], d["evicted"], d["policy"]) for d in rows["parking"]])
        conn.executemany(
            "INSERT INTO deadlock_event(tick,mode,cycle,victim,resolved) VALUES (?,?,?,?,?)",
            [(d["tick"], d["mode"], d["cycle"], d["victim"], d["resolved"]) for d in rows["deadlock"]])
        conn.executemany(
            "INSERT INTO incident(kind,road_id,start_tick,end_tick,details) VALUES (?,?,?,?,?)",
            [(d["kind"], d["road"], d["start"], d["end"], d["details"]) for d in rows["incident"]])
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    for v in rows.values():
        n += len(v)
    return n


class DBWriter:
    """Consumer thread: drains the event queue and writes batches."""

    def __init__(self, path=config.DB_PATH, flush_every: float = 1.0):
        self.q: "queue.Queue[tuple]" = queue.Queue(maxsize=50000)
        self.path = path
        self.flush_every = flush_every
        self._stop = threading.Event()
        self.written = 0
        self.thread = threading.Thread(target=self._run, name="db-writer", daemon=True)

    def emit(self, kind: str, data: dict):
        try:
            self.q.put_nowait((kind, data))
        except queue.Full:
            pass

    def start(self):
        self.thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        conn = connect(self.path)
        while not self._stop.is_set():
            time.sleep(self.flush_every)
            batch = []
            while len(batch) < 5000:
                try:
                    batch.append(self.q.get_nowait())
                except queue.Empty:
                    break
            if batch:
                try:
                    self.written += apply_events(conn, batch)
                except sqlite3.Error as exc:           # never kill the writer thread
                    print("DBWriter error:", exc)


def warm_up(conn: sqlite3.Connection, ticks_per_policy: int = 260) -> int:
    """Fill the database with real simulated history: each scheduling policy gets a run."""
    from engine.simulation import CitySimulation
    from engine.scheduler import POLICIES
    buf: list = []
    total = 0
    base = 0
    for i, pol in enumerate(POLICIES):
        sim = CitySimulation(seed=100 + i, params={"policy": pol, "demand": 2.6,
                                                   "parking_policy": ["FIFO", "LRU", "LFU", "CLOCK", "LRU"][i]},
                             emit=lambda k, d: buf.append((k, d)))
        sim.tick_base = base
        for t in range(ticks_per_policy):
            if t and t % 80 == 0:
                sim.action("ambulance")
            sim.step()
        base += ticks_per_policy
        total += apply_events(conn, buf)
        buf.clear()
    return base


def ensure_database(path=config.DB_PATH) -> int:
    """Create + seed + warm up the DB on first start. Returns the highest tick stored."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    from engine.simulation import CitySimulation
    conn = connect(path)
    init_schema(conn)
    topo = CitySimulation().topology()
    if not is_seeded(conn):
        seed_static(conn, topo)
        warm_up(conn)
    migrate(conn, topo)
    top = conn.execute("SELECT COALESCE(MAX(tick),0) FROM sensor_reading").fetchone()[0]
    conn.close()
    return top
