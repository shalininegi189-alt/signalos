"""OS MODULE 4 - Memory management (paging and page replacement).

The downtown garage is *physical memory*: each parking bay is a page frame.
Every registered plate is a *page*. A car arriving when its page is not resident
is a page fault. If all bays are used a victim page is evicted to the overflow
lot (swap space) using FIFO, LRU, LFU or Clock.
"""
from __future__ import annotations

from typing import Dict, List, Sequence

POLICIES = ["FIFO", "LRU", "LFU", "CLOCK"]


class FrameTable:
    def __init__(self, frames: int, policy: str = "LRU"):
        self.n = frames
        self.policy = policy.upper()
        self.slots: List[dict | None] = [None] * frames
        self.clock = 0
        self.hand = 0
        self.hits = 0
        self.faults = 0
        self.evictions = 0

    def set_policy(self, policy: str):
        self.policy = policy.upper()

    def _find(self, page):
        for i, s in enumerate(self.slots):
            if s and s["page"] == page:
                return i
        return None

    def _victim(self) -> int:
        if self.policy == "FIFO":
            return min(range(self.n), key=lambda i: self.slots[i]["loaded"])
        if self.policy == "LRU":
            return min(range(self.n), key=lambda i: self.slots[i]["last"])
        if self.policy == "LFU":
            return min(range(self.n), key=lambda i: (self.slots[i]["freq"], self.slots[i]["loaded"]))
        # CLOCK / second chance
        while True:
            s = self.slots[self.hand]
            if s["ref"]:
                s["ref"] = False
                self.hand = (self.hand + 1) % self.n
            else:
                victim = self.hand
                self.hand = (self.hand + 1) % self.n
                return victim

    def access(self, page) -> dict:
        self.clock += 1
        i = self._find(page)
        if i is not None:
            s = self.slots[i]
            s["last"] = self.clock
            s["freq"] += 1
            s["ref"] = True
            self.hits += 1
            return {"hit": True, "slot": i, "evicted": None}
        self.faults += 1
        entry = {"page": page, "loaded": self.clock, "last": self.clock, "freq": 1, "ref": True}
        free = next((k for k, s in enumerate(self.slots) if s is None), None)
        evicted = None
        if free is not None:
            self.slots[free] = entry
            return {"hit": False, "slot": free, "evicted": None}
        v = self._victim()
        evicted = self.slots[v]["page"]
        self.slots[v] = entry
        self.evictions += 1
        return {"hit": False, "slot": v, "evicted": evicted}

    def resident(self) -> List:
        return [s["page"] if s else None for s in self.slots]

    def snapshot(self) -> dict:
        total = self.hits + self.faults
        return {
            "policy": self.policy,
            "frames": [
                None if s is None else {"page": s["page"], "age": self.clock - s["last"],
                                        "freq": s["freq"], "ref": s["ref"]}
                for s in self.slots
            ],
            "hits": self.hits,
            "faults": self.faults,
            "evictions": self.evictions,
            "hit_ratio": round(self.hits / total, 3) if total else 0.0,
            "hand": self.hand,
        }


def simulate(refs: Sequence, frames: int, policy: str) -> dict:
    """Replay a reference string with one policy (or OPT)."""
    policy = policy.upper()
    if policy == "OPT":
        return simulate_opt(refs, frames)
    ft = FrameTable(frames, policy)
    steps = []
    for page in refs:
        r = ft.access(page)
        steps.append({"page": page, "fault": not r["hit"], "evicted": r["evicted"],
                      "frames": ft.resident()})
    return {"policy": policy, "faults": ft.faults, "hits": ft.hits, "steps": steps}


def simulate_opt(refs: Sequence, frames: int) -> dict:
    """Belady's optimal algorithm (needs the future, used as a lower bound)."""
    resident: List = []
    faults = hits = 0
    steps = []
    for t, page in enumerate(refs):
        evicted = None
        if page in resident:
            hits += 1
            fault = False
        else:
            faults += 1
            fault = True
            if len(resident) >= frames:
                def next_use(p):
                    try:
                        return refs.index(p, t + 1)
                    except ValueError:
                        return float("inf")
                victim = max(resident, key=next_use)
                resident[resident.index(victim)] = page
                evicted = victim
            else:
                resident.append(page)
        steps.append({"page": page, "fault": fault, "evicted": evicted, "frames": list(resident)})
    return {"policy": "OPT", "faults": faults, "hits": hits, "steps": steps}


def compare(refs: Sequence, frames: int) -> Dict[str, dict]:
    out = {}
    for p in POLICIES + ["OPT"]:
        r = simulate(list(refs), frames, p)
        out[p] = {"faults": r["faults"], "hits": r["hits"],
                  "hit_ratio": round(r["hits"] / len(refs), 3) if refs else 0}
    return out


def belady_demo() -> dict:
    """FIFO gets *worse* with more frames on this classic string."""
    refs = [1, 2, 3, 4, 1, 2, 5, 1, 2, 3, 4, 5]
    return {
        "refs": refs,
        "frames3": simulate(refs, 3, "FIFO"),
        "frames4": simulate(refs, 4, "FIFO"),
    }
