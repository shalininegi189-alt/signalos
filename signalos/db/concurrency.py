"""DBMS MODULE 3 - Concurrency control.

* conflict_serializable(): precedence-graph test + equivalent serial order
* strict_2pl():            lock-manager simulation (S/X locks held until commit),
                           with deadlock detection on the transaction wait-for graph
* lost_update_demo():      real threads and real SQLite connections - unsafe
                           read-modify-write vs BEGIN IMMEDIATE
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from collections import defaultdict
from typing import Dict, List

import config
from engine.deadlock import find_cycles

OP_RE = re.compile(r"^([rwca])(\d+)(?:\((\w+)\))?$", re.IGNORECASE)


def parse_schedule(text: str) -> List[dict]:
    ops = []
    for tok in re.split(r"[\s,;]+", text.strip()):
        if not tok:
            continue
        m = OP_RE.match(tok)
        if not m:
            raise ValueError(f"Cannot read '{tok}'. Use forms like r1(x), w2(y), c1, a2.")
        kind, txn, item = m.group(1).lower(), int(m.group(2)), m.group(3)
        if kind in "rw" and not item:
            raise ValueError(f"'{tok}' needs a data item, for example {kind}{txn}(x).")
        ops.append({"kind": kind, "txn": txn, "item": item, "text": tok.lower()})
    if not ops:
        raise ValueError("The schedule is empty.")
    return ops


def conflict_serializable(ops: List[dict]) -> dict:
    edges = set()
    reasons: Dict[tuple, str] = {}
    for i, a in enumerate(ops):
        if a["kind"] not in "rw":
            continue
        for b in ops[i + 1:]:
            if b["kind"] in "rw" and a["item"] == b["item"] and a["txn"] != b["txn"] \
                    and "w" in (a["kind"], b["kind"]):
                e = (a["txn"], b["txn"])
                edges.add(e)
                reasons.setdefault(e, f"{a['text']} before {b['text']}")
    txns = sorted({o["txn"] for o in ops})
    graph = defaultdict(set)
    for u, v in edges:
        graph[u].add(v)
    cycles = find_cycles({k: v for k, v in graph.items()})
    order = None
    if not cycles:
        indeg = {t: 0 for t in txns}
        for u, v in edges:
            indeg[v] += 1
        ready = sorted(t for t in txns if indeg[t] == 0)
        order = []
        while ready:
            t = ready.pop(0)
            order.append(t)
            for v in sorted(graph[t]):
                indeg[v] -= 1
                if indeg[v] == 0:
                    ready.append(v)
            ready.sort()
    return {"serializable": not cycles, "txns": txns,
            "edges": [{"from": u, "to": v, "why": reasons[(u, v)]} for u, v in sorted(edges)],
            "cycles": cycles, "serial_order": order}


def strict_2pl(ops: List[dict]) -> dict:
    """Run the schedule through a strict two-phase-locking lock manager."""
    locks: Dict[str, dict] = {}                 # item -> {"mode": "S"/"X", "holders": set}
    waiting: Dict[int, List[dict]] = {}         # blocked txn -> queued ops
    aborted: set = set()
    log: List[str] = []
    executed: List[str] = []
    deadlocks: List[dict] = []

    def can_grant(t, item, mode):
        L = locks.get(item)
        if not L or not L["holders"] or L["holders"] == {t}:
            return True
        return mode == "S" and L["mode"] == "S"

    def holders_blocking(t, item, mode):
        L = locks.get(item)
        return sorted(h for h in (L["holders"] if L else set()) if h != t)

    def grant(t, item, mode):
        L = locks.setdefault(item, {"mode": mode, "holders": set()})
        if mode == "X":
            L["mode"] = "X"
        elif not L["holders"]:
            L["mode"] = "S"
        L["holders"].add(t)

    def release(t):
        for item in list(locks):
            locks[item]["holders"].discard(t)
            if not locks[item]["holders"]:
                del locks[item]

    def run(op) -> bool:
        """Try to execute an op. False if the txn must block."""
        t, k = op["txn"], op["kind"]
        if k in "ca":
            release(t)
            executed.append(op["text"])
            log.append(f"T{t} {'commits' if k == 'c' else 'aborts'}: all its locks are released")
            return True
        mode = "S" if k == "r" else "X"
        if can_grant(t, op["item"], mode):
            grant(t, op["item"], mode)
            executed.append(op["text"])
            log.append(f"{op['text']}: T{t} gets a {mode} lock on {op['item']}")
            return True
        return False

    def wait_graph():
        g = defaultdict(set)
        for t, q in waiting.items():
            op = q[0]
            if op["kind"] in "rw":
                for h in holders_blocking(t, op["item"], "S" if op["kind"] == "r" else "X"):
                    g[t].add(h)
        return g

    def resolve_deadlocks():
        while True:
            cycles = find_cycles(wait_graph())
            if not cycles:
                return
            cyc = cycles[0]
            victim = max(cyc)           # youngest transaction loses
            deadlocks.append({"cycle": [f"T{t}" for t in cyc], "victim": f"T{victim}"})
            log.append(f"DEADLOCK among {', '.join('T%d' % t for t in cyc)}: abort youngest T{victim}")
            aborted.add(victim)
            waiting.pop(victim, None)
            release(victim)
            executed.append(f"a{victim}")
            retry()

    def retry():
        progressed = True
        while progressed:
            progressed = False
            for t in list(waiting):
                q = waiting.get(t)
                while q:
                    if run(q[0]):
                        q.pop(0)
                        progressed = True
                    else:
                        break
                if not q:
                    waiting.pop(t, None)
                    log.append(f"T{t} is unblocked")

    for op in ops:
        t = op["txn"]
        if t in aborted:
            log.append(f"{op['text']} skipped (T{t} was aborted)")
            continue
        if t in waiting:
            waiting[t].append(op)
            log.append(f"{op['text']} queued behind T{t}'s blocked operation")
            continue
        if run(op):
            if op["kind"] in "ca":
                retry()
        else:
            waiting[t] = [op]
            by = holders_blocking(t, op["item"], "S" if op["kind"] == "r" else "X")
            log.append(f"{op['text']}: T{t} must wait for {', '.join('T%d' % h for h in by)}")
            resolve_deadlocks()
    still = sorted(waiting)
    if still:
        log.append(f"Schedule ended with blocked transactions: {', '.join('T%d' % t for t in still)}")
    return {"log": log, "executed": executed, "deadlocks": deadlocks,
            "aborted": sorted(aborted), "blocked_at_end": still}


def analyse(text: str) -> dict:
    ops = parse_schedule(text)
    return {"ops": [o["text"] for o in ops], "serializability": conflict_serializable(ops),
            "locking": strict_2pl(ops)}


def lost_update_demo(threads: int = 4, iterations: int = 40) -> dict:
    """Increment one counter from several threads, unsafe vs transactional."""
    path = config.DATA_DIR / "lost_update.db"
    for suffix in ("", "-wal", "-shm"):
        try:
            (path.parent / (path.name + suffix)).unlink()
        except FileNotFoundError:
            pass
    setup = sqlite3.connect(str(path), isolation_level=None)
    setup.execute("PRAGMA journal_mode = WAL")
    setup.execute("CREATE TABLE counter(id INTEGER PRIMARY KEY, n INTEGER NOT NULL)")
    setup.close()

    def worker(safe: bool):
        c = sqlite3.connect(str(path), timeout=30, isolation_level=None)
        for _ in range(iterations):
            if safe:
                c.execute("BEGIN IMMEDIATE")
                n = c.execute("SELECT n FROM counter WHERE id = 1").fetchone()[0]
                time.sleep(0.0004)
                c.execute("UPDATE counter SET n = ? WHERE id = 1", (n + 1,))
                c.execute("COMMIT")
            else:
                n = c.execute("SELECT n FROM counter WHERE id = 1").fetchone()[0]
                time.sleep(0.0004)
                for _retry in range(200):
                    try:
                        c.execute("UPDATE counter SET n = ? WHERE id = 1", (n + 1,))
                        break
                    except sqlite3.OperationalError:
                        time.sleep(0.001)
        c.close()

    out = {}
    for label, safe in (("unsafe", False), ("transactional", True)):
        conn = sqlite3.connect(str(path), isolation_level=None)
        conn.execute("INSERT OR REPLACE INTO counter VALUES (1, 0)")
        conn.close()
        ts = [threading.Thread(target=worker, args=(safe,)) for _ in range(threads)]
        t0 = time.time()
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        conn = sqlite3.connect(str(path), isolation_level=None)
        final = conn.execute("SELECT n FROM counter WHERE id = 1").fetchone()[0]
        conn.close()
        out[label] = {"final": final, "expected": threads * iterations,
                      "lost": threads * iterations - final, "seconds": round(time.time() - t0, 2)}
    return out
