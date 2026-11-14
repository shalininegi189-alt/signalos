"""DBMS MODULE 6 - Logging and crash recovery (write-ahead log, ARIES-lite).

A tiny page store with a *steal / no-force* buffer pool:
  steal    - a dirty page of an uncommitted transaction may be flushed to disk
  no-force - committing does not flush data pages, only the log
After a crash recovery runs three passes: analysis, redo (repeat history) and
undo (roll back losers, writing compensation records).
"""
from __future__ import annotations

from typing import Dict, List


class MiniDB:
    def __init__(self, data: Dict[str, int]):
        self.disk = {k: {"v": v, "lsn": 0} for k, v in data.items()}
        self.buffer: Dict[str, dict] = {}
        self.log: List[dict] = []
        self.lsn = 0
        self.flushed_lsn = 0
        self.last_checkpoint = 0

    # --- normal operation -------------------------------------------------
    def _append(self, **rec) -> int:
        self.lsn += 1
        rec["lsn"] = self.lsn
        self.log.append(rec)
        return self.lsn

    def _value(self, k):
        return self.buffer[k]["v"] if k in self.buffer else self.disk[k]["v"]

    def begin(self, t):
        self._append(type="BEGIN", txn=t)

    def write(self, t, k, v):
        before = self._value(k)
        lsn = self._append(type="UPDATE", txn=t, key=k, before=before, after=v)
        self.buffer[k] = {"v": v, "lsn": lsn}

    def commit(self, t):
        self._append(type="COMMIT", txn=t)
        self.flushed_lsn = self.lsn             # force the log, not the data pages

    def flush_page(self, k):
        page = self.buffer.pop(k)
        self.flushed_lsn = max(self.flushed_lsn, page["lsn"])      # WAL rule: log first
        self.disk[k] = {"v": page["v"], "lsn": page["lsn"]}

    def checkpoint(self):
        self._append(type="CHECKPOINT")
        self.flushed_lsn = self.lsn
        self.last_checkpoint = self.lsn

    def crash(self):
        """Lose the buffer pool and the unflushed tail of the log."""
        lost = [r for r in self.log if r["lsn"] > self.flushed_lsn]
        self.log = [r for r in self.log if r["lsn"] <= self.flushed_lsn]
        self.buffer = {}
        return lost

    # --- recovery ---------------------------------------------------------
    def recover(self) -> dict:
        steps: List[dict] = []
        status: Dict[int, str] = {}
        for r in self.log:
            if r["type"] == "BEGIN":
                status[r["txn"]] = "active"
            elif r["type"] == "COMMIT":
                status[r["txn"]] = "committed"
            elif r["type"] == "END":
                status[r["txn"]] = "done"
        winners = sorted(t for t, s in status.items() if s == "committed")
        losers = sorted(t for t, s in status.items() if s == "active")
        steps.append({"phase": "ANALYSIS", "text": f"Scanned {len(self.log)} log records. "
                      f"Committed: {['T%d' % t for t in winners]}. Losers (no commit): {['T%d' % t for t in losers]}."})
        for r in self.log:                       # REDO: repeat history
            if r["type"] in ("UPDATE", "CLR"):
                page = self.disk[r["key"]]
                if page["lsn"] < r["lsn"]:
                    page["v"], page["lsn"] = r["after"], r["lsn"]
                    steps.append({"phase": "REDO", "text": f"LSN {r['lsn']}: {r['key']} = {r['after']} "
                                  f"(page was older than the log record)"})
                else:
                    steps.append({"phase": "REDO", "text": f"LSN {r['lsn']}: {r['key']} already on disk, skipped"})
        for t in losers:                         # UNDO: newest first
            for r in reversed([x for x in self.log if x["txn"] == t and x["type"] == "UPDATE"]):
                self.lsn += 1
                clr = {"lsn": self.lsn, "type": "CLR", "txn": t, "key": r["key"],
                       "before": r["after"], "after": r["before"]}
                self.log.append(clr)
                self.disk[r["key"]] = {"v": r["before"], "lsn": self.lsn}
                steps.append({"phase": "UNDO", "text": f"T{t}: {r['key']} restored to {r['before']} (CLR {self.lsn})"})
            self.lsn += 1
            self.log.append({"lsn": self.lsn, "type": "END", "txn": t})
        self.flushed_lsn = self.lsn
        return {"steps": steps, "winners": winners, "losers": losers,
                "final": {k: p["v"] for k, p in sorted(self.disk.items())}}


def run_scenario() -> dict:
    """Credit balances of four toll-gate accounts; crash in the middle of the workload."""
    db = MiniDB({"A": 100, "B": 200, "C": 300, "D": 400})
    initial = {k: p["v"] for k, p in db.disk.items()}
    db.begin(1); db.write(1, "A", 150); db.write(1, "B", 250); db.commit(1)     # committed, pages NOT flushed
    db.begin(2); db.write(2, "C", 350); db.flush_page("C")                      # steal: uncommitted page on disk
    db.begin(3); db.write(3, "D", 450); db.commit(3)                            # committed, page NOT flushed
    db.begin(4); db.write(4, "A", 999)                                          # log tail never reaches disk
    timeline = [
        "T1 writes A=150, B=250 and commits (data pages stay in memory: no-force)",
        "T2 writes C=350; the buffer manager flushes page C early (steal)",
        "T3 writes D=450 and commits",
        "T4 writes A=999 but has not committed; its log record is still in memory",
    ]
    log_before = [dict(r) for r in db.log]
    db_flushed = db.flushed_lsn
    disk_at_crash = {k: p["v"] for k, p in db.disk.items()}
    lost = db.crash()
    result = db.recover()
    expected = {"A": 150, "B": 250, "C": 300, "D": 450}
    return {
        "timeline": timeline, "initial": initial, "disk_at_crash": disk_at_crash,
        "log_before_crash": log_before, "flushed_lsn": db_flushed, "lost_log_records": lost,
        "steps": result["steps"], "winners": result["winners"], "losers": result["losers"],
        "final": result["final"], "expected": expected, "correct": result["final"] == expected,
    }
