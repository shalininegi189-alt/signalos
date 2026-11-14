"""OS MODULE 1 - CPU scheduling.

Every intersection is a CPU. Each incoming lane is a *process*:
    burst        = vehicles waiting at the stop line (ready queue length)
    arrival      = tick the lane became runnable
    priority     = ambulance (0) < bus (1) < normal (2)
    blocked      = head vehicle waits for a resource (full road ahead) -> I/O wait
    context switch = one all-red clearance tick when the green moves to another lane

Policies: FCFS, RR, SRTF (preemptive SJF + aging), PRIORITY (preemptive + aging),
MLFQ (3 levels, demotion on quantum use, periodic boost). An optional real-time
class lets an ambulance pre-empt whatever is running.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

APPROACH_ORDER = ["N", "E", "S", "W"]
POLICIES = ["FCFS", "RR", "SRTF", "PRIORITY", "MLFQ"]
POLICY_LABELS = {
    "FCFS": "First come, first served",
    "RR": "Round robin",
    "SRTF": "Shortest queue first (preemptive, aging)",
    "PRIORITY": "Priority with aging",
    "MLFQ": "Multilevel feedback queue",
}
MLFQ_QUANTA = [2, 4, 8]
MIN_GREEN = 2         # hysteresis so preemptive policies do not thrash
BOOST_EVERY = 30      # MLFQ priority boost period


@dataclass
class Lane:
    n: int = 0              # runnable vehicles at the stop line
    emergency: bool = False
    bus: bool = False
    blocked: bool = False   # waiting on a resource (not runnable)


class PhaseScheduler:
    def __init__(self, approaches, policy="RR", quantum=4, max_green=12,
                 cs_cost=1, emergency_preempt=True, aging=6):
        self.approaches = [a for a in APPROACH_ORDER if a in approaches]
        self.policy = policy
        self.quantum = quantum
        self.max_green = max_green
        self.cs_cost = cs_cost
        self.emergency_preempt = emergency_preempt
        self.aging = aging
        self.reset()

    # ------------------------------------------------------------------ state
    def reset(self):
        self.current = None
        self.pending = None
        self.clearance = 0
        self.run_for = 0
        self.quantum_left = 0
        self.last_green = None
        self.last_rr = -1
        self.wait = {a: 0 for a in self.approaches}
        self.arrival = {a: None for a in self.approaches}
        self.level = {a: 0 for a in self.approaches}
        self.context_switches = 0
        self.preemptions = 0
        self.gantt = deque(maxlen=90)
        self.events: list = []      # (tick, type, approach) drained by the simulation

    def set_policy(self, policy: str):
        if policy in POLICIES and policy != self.policy:
            self.policy = policy
            self.level = {a: 0 for a in self.approaches}
            self.quantum_left = min(self.quantum_left, self._quantum_for(self.current))

    def _quantum_for(self, approach):
        if self.policy == "MLFQ" and approach is not None:
            return MLFQ_QUANTA[self.level[approach]]
        if self.policy == "RR":
            return self.quantum
        return self.max_green

    # --------------------------------------------------------------- selection
    def _priority(self, a, lanes):
        if lanes[a].emergency:
            return 0
        if lanes[a].bus:
            return 1
        return 2

    def _rr_distance(self, a):
        idx = self.approaches.index(a)
        return (idx - self.last_rr - 1) % len(self.approaches)

    def _pick(self, ready, lanes, exclude=None):
        cand = [a for a in ready if a != exclude] or list(ready)
        if not cand:
            return None
        if self.emergency_preempt:
            em = [a for a in cand if lanes[a].emergency]
            if em:
                cand = em
        order = self.approaches.index
        p = self.policy
        if p == "FCFS":
            return min(cand, key=lambda a: (self.arrival[a] if self.arrival[a] is not None else 0, order(a)))
        if p == "RR":
            return min(cand, key=self._rr_distance)
        if p == "SRTF":
            return min(cand, key=lambda a: (lanes[a].n - self.wait[a] // self.aging, order(a)))
        if p == "PRIORITY":
            return min(cand, key=lambda a: (self._priority(a, lanes) - self.wait[a] // self.aging,
                                            -lanes[a].n, order(a)))
        # MLFQ: highest level first, round-robin inside a level
        return min(cand, key=lambda a: (self.level[a], self._rr_distance(a)))

    # ------------------------------------------------------------------ switch
    def _log(self, ch):
        self.gantt.append(ch)

    def _activate(self, nxt, tick, reason):
        self.current = nxt
        self.pending = None
        self.run_for = 0
        self.quantum_left = self._quantum_for(nxt)
        self.last_green = nxt
        self.last_rr = self.approaches.index(nxt)
        self.events.append((tick, reason, nxt))

    def _switch_to(self, nxt, tick, reason):
        """Return the green approach for this tick (None while clearing)."""
        if (self.last_green is not None and nxt != self.last_green
                and self.cs_cost > 0):
            self.context_switches += 1
            self.current = None
            self.pending = (nxt, reason)
            self.clearance = self.cs_cost - 1
            self.run_for = 0
            self._log("x")
            return None
        self._activate(nxt, tick, reason)
        return self._run(tick)

    def _run(self, tick):
        a = self.current
        self.run_for += 1
        self.quantum_left -= 1
        self._log(a)
        return a

    # -------------------------------------------------------------------- step
    def step(self, tick: int, lanes: dict) -> str | None:
        ready = [a for a in self.approaches if lanes[a].n > 0]

        # bookkeeping: waiting time, arrival, aging
        for a in self.approaches:
            if lanes[a].n > 0:
                if self.arrival[a] is None:
                    self.arrival[a] = tick
                if a != self.current:
                    self.wait[a] += 1
            else:
                self.arrival[a] = None
                self.wait[a] = 0
                self.level[a] = 0
        if self.policy == "MLFQ" and tick % BOOST_EVERY == 0:
            self.level = {a: 0 for a in self.approaches}

        # finishing an all-red clearance interval
        if self.pending is not None:
            if self.clearance > 0:
                self.clearance -= 1
                self._log("x")
                return None
            nxt, reason = self.pending
            self.pending = None
            if lanes[nxt].n > 0:
                self._activate(nxt, tick, reason)
                return self._run(tick)
            # lane emptied while we were clearing: choose again
            self.last_green = None

        cur = self.current
        if cur is not None and lanes[cur].n == 0:
            cur = None
            self.current = None

        if cur is None:
            choice = self._pick(ready, lanes)
            if choice is None:
                self.last_green = None      # idle: next start costs nothing
                self._log("-")
                return None
            return self._switch_to(choice, tick, "GREEN")

        # a lane is running: decide whether to preempt or rotate
        cl = lanes[cur]
        rt_waiting = [a for a in ready if a != cur and lanes[a].emergency]
        if self.emergency_preempt and rt_waiting and not cl.emergency:
            self.preemptions += 1
            return self._switch_to(self._pick(rt_waiting, lanes), tick, "PREEMPT")

        expired = self.quantum_left <= 0 or self.run_for >= self.max_green
        if expired and len(ready) > 1:
            if self.policy == "MLFQ" and self.quantum_left <= 0:
                self.level[cur] = min(len(MLFQ_QUANTA) - 1, self.level[cur] + 1)
            nxt = self._pick(ready, lanes, exclude=cur)
            if nxt != cur:
                return self._switch_to(nxt, tick, "SWITCH")
            self.quantum_left = self._quantum_for(cur)
            self.run_for = 0
        elif expired:
            self.quantum_left = self._quantum_for(cur)
            self.run_for = 0

        if self.run_for >= MIN_GREEN and self.policy in ("SRTF", "PRIORITY"):
            best = self._pick(ready, lanes)
            if best is not None and best != cur:
                if self.policy == "SRTF":
                    better = (lanes[best].n - self.wait[best] // self.aging) < (lanes[cur].n - 1)
                else:
                    better = (self._priority(best, lanes) - self.wait[best] // self.aging) < \
                             (self._priority(cur, lanes) - self.wait[cur] // self.aging)
                if better:
                    self.preemptions += 1
                    return self._switch_to(best, tick, "PREEMPT")
        return self._run(tick)

    def snapshot(self) -> dict:
        return {
            "policy": self.policy,
            "current": self.current,
            "pending": self.pending[0] if self.pending else None,
            "context_switches": self.context_switches,
            "preemptions": self.preemptions,
            "wait": dict(self.wait),
            "level": dict(self.level),
            "gantt": "".join(self.gantt),
        }
