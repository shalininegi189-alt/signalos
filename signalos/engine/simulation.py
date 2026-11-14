"""The city simulation: ties the four OS modules together.

Topology: a GRID_ROWS x GRID_COLS street grid of intersections joined by directed
road segments. Cars enter at border nodes (producer threads -> bounded buffer),
follow a Manhattan route, cross intersections when their lane has the green
(CPU scheduler), need a free slot on the next road (counting semaphore), may
deadlock in a ring (deadlock module) and may park in the downtown garage
(paging module).
"""
from __future__ import annotations

import random
import threading
import time
from collections import deque
from typing import Callable, Dict, List, Optional

import config
import places
from engine import deadlock as dl
from engine.memory import FrameTable
from engine.scheduler import PhaseScheduler, Lane, POLICIES
from engine.sync import BoundedBuffer, CountingSemaphore, Mutex, RWLock

KIND_WEIGHT = {"ambulance": 100, "bus": 5, "truck": 3, "car": 1}
VIOLATIONS = {"RED_LIGHT": 0.002, "SPEEDING": 0.003}


def nid(r: int, c: int) -> str:
    return f"{r}-{c}"


def parse(n: str):
    r, c = n.split("-")
    return int(r), int(c)


def side_of(u: str, v: str) -> str:
    """Side of node v from which a car travelling u -> v arrives."""
    (r1, c1), (r2, c2) = parse(u), parse(v)
    if r2 > r1:
        return "N"
    if r2 < r1:
        return "S"
    return "W" if c2 > c1 else "E"


class Vehicle:
    __slots__ = ("id", "plate", "kind", "route", "i", "ready_at", "enter_tick",
                 "spawn_tick", "wait", "park", "hops")

    def __init__(self, vid, plate, kind, route, tick, park):
        self.id = vid
        self.plate = plate
        self.kind = kind
        self.route = route
        self.i = 0
        self.spawn_tick = tick
        self.enter_tick = tick
        self.ready_at = tick
        self.wait = 0
        self.park = park
        self.hops = 0

    def next_edge_key(self):
        if self.i + 2 < len(self.route):
            return (self.route[self.i + 1], self.route[self.i + 2])
        return None

    @property
    def is_last(self):
        return self.i + 1 >= len(self.route) - 1


class Edge:
    def __init__(self, u, v):
        self.u, self.v = u, v
        self.id = f"{u}>{v}"
        self.dir = side_of(u, v)
        self.length = config.EDGE_LEN
        self.cap = config.EDGE_CAP
        self.slots = CountingSemaphore(f"road {self.id}", self.cap)
        self.q: List[Vehicle] = []
        self.blocked_until = 0
        self.stuck = False        # part of a detected deadlock cycle


class Intersection:
    def __init__(self, r, c):
        self.r, self.c = r, c
        self.id = nid(r, c)
        self.in_edges: Dict[str, Edge] = {}
        self.sched: Optional[PhaseScheduler] = None
        self.box = Mutex(f"box {self.id}")
        self.served = 0
        self.green: Optional[str] = None


class GateProducer:
    """Producer side of the entry buffer: generates arrival requests for one side of the city."""

    def __init__(self, side: str, nodes: List[str], seed: int, sim_ref: "CitySimulation"):
        self.side = side
        self.nodes = nodes
        self.rng = random.Random(seed)
        self.sim = sim_ref
        self.generated = 0

    def generate(self, tick: int) -> List[dict]:
        out = []
        rate = config.BASE_SPAWN_RATE * self.sim.params["demand"]
        for n in self.nodes:
            if self.rng.random() < min(0.95, rate * self.sim.gate_weight(n)):
                dest = self.rng.choice([b for b in self.sim.border if b != n and self.sim.manhattan(n, b) >= 3])
                x = self.rng.random()
                kind = "bus" if x < 0.08 else "truck" if x < 0.14 else "car"
                out.append({"origin": n, "dest": dest, "kind": kind, "tick": tick,
                            "xy": self.rng.random() < 0.5})
                self.generated += 1
        return out


class CitySimulation:
    def __init__(self, seed: int = 7, params: Optional[dict] = None, emit: Optional[Callable] = None):
        self.seed = seed
        self.rng = random.Random(seed)
        self.params = dict(config.DEFAULTS)
        if params:
            self.params.update(params)
        self._emit = emit
        self.tick_base = 0
        self.custom_weights: Dict[str, float] = {}   # from uploaded field counts
        self._build()
        self.reset()

    # ------------------------------------------------------------- topology
    def _build(self):
        R, C = config.GRID_ROWS, config.GRID_COLS
        self.nodes: Dict[str, Intersection] = {}
        self.edges: Dict[tuple, Edge] = {}
        for r in range(R):
            for c in range(C):
                self.nodes[nid(r, c)] = Intersection(r, c)
        for r in range(R):
            for c in range(C):
                for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < R and 0 <= cc < C:
                        e = Edge(nid(r, c), nid(rr, cc))
                        self.edges[(e.u, e.v)] = e
                        self.nodes[e.v].in_edges[e.dir] = e
        self.border = [n for n in self.nodes if self.nodes[n].r in (0, R - 1) or self.nodes[n].c in (0, C - 1)]

    def manhattan(self, a, b):
        (r1, c1), (r2, c2) = parse(a), parse(b)
        return abs(r1 - r2) + abs(c1 - c2)

    def topology(self) -> dict:
        return {
            "city": places.CITY,
            "nodes": [{"id": n.id, "row": n.r, "col": n.c, **places.place(n.id)} for n in self.nodes.values()],
            "roads": [{"id": e.id, "from": e.u, "to": e.v, "dir": e.dir,
                       "length": e.length, "cap": e.cap,
                       "street": places.street(e.u, e.v), "label": places.road_name(e.id)}
                      for e in self.edges.values()],
        }

    # ------------------------------------------------- Dehradun demand model
    def gate_weight(self, node: str) -> float:
        """Arrival-rate multiplier for one entry point: base weight (or uploaded count) x scenario."""
        base = self.custom_weights.get(node, places.BASE_WEIGHT.get(node, 1.0))
        return base * places.scenario_multiplier(self.params.get("scenario", "normal"), node)

    def set_real_counts(self, counts: Dict[str, float]) -> None:
        """counts: entry node -> vehicles per hour. Relative sizes become the arrival weights."""
        counts = {n: float(v) for n, v in counts.items() if v > 0}
        if not counts:
            self.custom_weights = {}
            return
        mean = sum(counts.values()) / len(counts)
        self.custom_weights = {n: v / mean for n, v in counts.items()}

    # ---------------------------------------------------------------- state
    def reset(self):
        self.rng = random.Random(self.seed)
        self.tick = 0
        self.next_vid = 1
        self.visitor_seq = 1
        for e in self.edges.values():
            e.q.clear()
            e.slots = CountingSemaphore(f"road {e.id}", e.cap)
            e.blocked_until = 0
            e.stuck = False
        for n in self.nodes.values():
            n.sched = PhaseScheduler(list(n.in_edges), policy=self.params["policy"],
                                     quantum=self.params["quantum"],
                                     emergency_preempt=self.params["emergency"])
            n.box = Mutex(f"box {n.id}")
            n.served = 0
            n.green = None
        sides = {
            "N": [n for n in self.border if self.nodes[n].r == 0],
            "S": [n for n in self.border if self.nodes[n].r == config.GRID_ROWS - 1],
            "W": [n for n in self.border if self.nodes[n].c == 0 and self.nodes[n].r not in (0, config.GRID_ROWS - 1)],
            "E": [n for n in self.border if self.nodes[n].c == config.GRID_COLS - 1 and self.nodes[n].r not in (0, config.GRID_ROWS - 1)],
        }
        self.producers = [GateProducer(s, ns, self.seed * 31 + i, self) for i, (s, ns) in enumerate(sides.items())]
        self.buffer = BoundedBuffer(config.BUFFER_CAPACITY)
        self.backlog: List[dict] = []
        self.active_plates: set = set()
        self.commuters = [f"SO{i:03d}" for i in range(1, config.COMMUTERS + 1)]
        self.commuter_w = [1.0 / (i + 1) ** 1.1 for i in range(config.COMMUTERS)]
        self.parking = FrameTable(config.PARKING_FRAMES, self.params["parking_policy"])
        self.park_refs: deque = deque(maxlen=800)
        self.park_log: deque = deque(maxlen=14)
        self.log: deque = deque(maxlen=40)
        self.m = dict(spawned=0, exited=0, towed=0, turned_away=0, wait_sum=0, turn_sum=0,
                      max_wait=0, deadlocks=0, recovered=0, denied_unsafe=0, violations=0,
                      amb_exited=0, amb_wait=0, amb_turn=0, gridlocks=0, cross=0)
        self.history: deque = deque(maxlen=90)
        self.exit_times: deque = deque(maxlen=400)
        self.deadlock_cycles: List[List[str]] = []
        self.cycle_age = 0
        self.incidents: List[dict] = []
        self.last_deadlock_check = 0
        self._existing_cycles: set = set()

    def emit(self, event: str, /, **data):
        # `event` is positional-only so a payload field may itself be called "kind" (incidents do).
        if self._emit:
            data["tick"] = self.tick_base + data.get("tick", self.tick)
            self._emit(event, data)

    def note(self, text: str, level: str = "info"):
        self.log.appendleft({"tick": self.tick, "text": text, "level": level})

    # ---------------------------------------------------------------- params
    def set_params(self, **kw):
        for k, v in kw.items():
            if k not in self.params:
                continue
            self.params[k] = v
        if "policy" in kw and kw["policy"] in POLICIES:
            for n in self.nodes.values():
                n.sched.set_policy(kw["policy"])
        if "quantum" in kw:
            for n in self.nodes.values():
                n.sched.quantum = int(kw["quantum"])
        if "emergency" in kw:
            for n in self.nodes.values():
                n.sched.emergency_preempt = bool(kw["emergency"])
        if "parking_policy" in kw:
            self.parking.set_policy(kw["parking_policy"])

    # ---------------------------------------------------------------- routing
    def _route(self, origin, dest, xy=True):
        (r1, c1), (r2, c2) = parse(origin), parse(dest)
        path = [origin]
        r, c = r1, c1

        def horiz():
            nonlocal c
            while c != c2:
                c += 1 if c2 > c else -1
                path.append(nid(r, c))

        def vert():
            nonlocal r
            while r != r2:
                r += 1 if r2 > r else -1
                path.append(nid(r, c))

        (horiz(), vert()) if xy else (vert(), horiz())
        return path

    def _pick_plate(self):
        if self.rng.random() < 0.9:
            for _ in range(8):          # popular commuters are often already on the road
                p = self.rng.choices(self.commuters, self.commuter_w)[0]
                if p not in self.active_plates:
                    return p
        p = f"VS{self.visitor_seq:04d}"
        self.visitor_seq += 1
        return p

    # ------------------------------------------------------------- deadlocks
    def _full(self, e: Edge) -> bool:
        return len(e.q) >= e.cap

    def _want_graph(self) -> dict:
        want = {}
        for e in self.edges.values():
            if not e.q:
                continue
            nk = e.q[0].next_edge_key()
            if nk is None:
                continue
            ne = self.edges[nk]
            if self._full(ne):
                want[e.id] = ne.id
        return want

    def _cycle_edges(self) -> set:
        s = set()
        for cyc in dl.functional_cycles(self._want_graph()):
            s.update(cyc)
        return s

    def _tentative_ok(self, apply, undo) -> bool:
        """Safe-state test used for deadlock avoidance (Banker's-style)."""
        apply()
        try:
            return self._cycle_edges() <= self._existing_cycles
        finally:
            undo()

    def _place(self, v: Vehicle, e: Edge, force=False) -> bool:
        """Put a vehicle on the first road of its route (admission control)."""
        if len(e.q) >= e.cap:
            e.slots.try_acquire(v.id)
            return False
        if self.params["deadlock_mode"] == "avoid" and not force:
            ok = self._tentative_ok(lambda: e.q.append(v), lambda: e.q.remove(v))
            if not ok:
                self.m["denied_unsafe"] += 1
                return False
        e.slots.try_acquire(v.id)
        e.q.append(v)
        v.enter_tick = self.tick
        v.ready_at = self.tick + (config.AMBULANCE_LEN if v.kind == "ambulance" else e.length)
        return True

    def _recover(self, cycles):
        for cyc in cycles:
            def cost(eid):
                v = self.edges[tuple(eid.split(">"))].q[0]
                return (KIND_WEIGHT[v.kind], v.hops)
            victim_edge = self.edges[tuple(min(cyc, key=cost).split(">"))]
            v = victim_edge.q.pop(0)
            victim_edge.slots.release(v.id)
            self.active_plates.discard(v.plate)
            for e in self.edges.values():
                e.slots.cancel_wait(v.id)
            self.m["towed"] += 1
            self.m["recovered"] += 1
            self.note(f"Gridlock broken: towed {v.kind} {v.plate} from {places.road_name(victim_edge.id)}", "ok")
            self.emit("deadlock", mode=self.params["deadlock_mode"], cycle=" ".join(cyc),
                      victim=v.plate, resolved=1)
            self.emit("trip", plate=v.plate, origin=v.route[0], dest=v.route[-1],
                      spawn=v.spawn_tick, end=self.tick, wait=v.wait, hops=v.hops, towed=1)

    def _detect(self):
        cycles = dl.functional_cycles(self._want_graph())
        for e in self.edges.values():
            e.stuck = False
        for cyc in cycles:
            for eid in cyc:
                self.edges[tuple(eid.split(">"))].stuck = True
        new = bool(cycles) and not self.deadlock_cycles
        if new:
            self.m["deadlocks"] += len(cycles)
            self.note(f"Deadlock detected: {len(cycles)} cycle(s), {sum(len(c) for c in cycles)} roads in circular wait", "bad")
        self.deadlock_cycles = cycles
        if cycles and self.params["deadlock_mode"] in ("detect", "avoid"):
            self._recover(cycles)
            self.deadlock_cycles = []
            for e in self.edges.values():
                e.stuck = False

    # --------------------------------------------------------------- actions
    def action(self, kind: str) -> str:
        if kind == "ambulance":
            origin = self.rng.choice(self.border)
            dest = self.rng.choice([b for b in self.border if self.manhattan(origin, b) >= 4])
            self.backlog.insert(0, {"origin": origin, "dest": dest, "kind": "ambulance",
                                    "tick": self.tick, "xy": self.rng.random() < 0.5})
            self.note(f"Ambulance dispatched from {places.name(origin)} to {places.name(dest)}", "warn")
            return "ambulance dispatched"
        if kind == "rushhour":
            self.params["demand"] = 3.2
            self.note("Rush hour: demand raised to 3.2x", "warn")
            return "rush hour"
        if kind == "accident":
            cands = [e for e in self.edges.values() if e.q]
            e = self.rng.choice(cands) if cands else self.rng.choice(list(self.edges.values()))
            e.blocked_until = self.tick + 35
            self.incidents.append({"road": e.id, "until": e.blocked_until})
            self.emit("incident", kind="ACCIDENT", road=e.id, start=self.tick, end=e.blocked_until,
                      details="Simulated collision blocks the lane")
            self.note(f"Accident on {places.road_name(e.id)}: lane closed for 35 ticks", "bad")
            return "accident"
        if kind == "gridlock":
            return self._induce_gridlock()
        if kind == "reset":
            self.reset()
            return "reset"
        return "unknown"

    def _induce_gridlock(self) -> str:
        R, C = config.GRID_ROWS, config.GRID_COLS
        r, c = self.rng.randint(1, R - 3) if R > 3 else 0, self.rng.randint(1, C - 3) if C > 3 else 0
        ring = [nid(r, c), nid(r, c + 1), nid(r + 1, c + 1), nid(r + 1, c)]
        denied = 0
        for k in range(4):
            e = self.edges[(ring[k], ring[(k + 1) % 4])]
            for v in list(e.q):
                e.q.remove(v)
                e.slots.release(v.id)
                self.active_plates.discard(v.plate)
        self._existing_cycles = self._cycle_edges()
        for _ in range(config.EDGE_CAP):
            for k in range(4):
                a, b, c2, d = ring[k], ring[(k + 1) % 4], ring[(k + 2) % 4], ring[(k + 3) % 4]
                v = Vehicle(self.next_vid, f"VS{self.visitor_seq:04d}", "car", [a, b, c2, d], self.tick, False)
                self.next_vid += 1
                self.visitor_seq += 1
                e = self.edges[(a, b)]
                if self._place(v, e):
                    self.active_plates.add(v.plate)
                    self.m["spawned"] += 1
                else:
                    denied += 1
                    e.slots.cancel_wait(v.id)
        self.m["gridlocks"] += 1
        mode = self.params["deadlock_mode"]
        if mode == "avoid":
            self.note(f"Gridlock attempt near {places.name(ring[0])}: {denied} entries refused by the safe-state check", "ok")
        else:
            self.note(f"Gridlock injected near {places.name(ring[0])} - four roads in circular wait", "bad")
        return "gridlock"

    # ------------------------------------------------------------------ tick
    def _admit(self):
        reqs = self.backlog + self.buffer.drain(10)
        self.backlog = []
        for r in reqs:
            kind = r["kind"]
            route = self._route(r["origin"], r["dest"], r.get("xy", True))
            if len(route) < 3:
                continue
            plate = self._pick_plate() if kind != "ambulance" else f"AMB{self.next_vid:03d}"
            park = kind == "car" and self.rng.random() < 0.55
            v = Vehicle(self.next_vid, plate, kind, route, self.tick, park)
            e = self.edges[(route[0], route[1])]
            if e.blocked_until > self.tick and kind != "ambulance":
                pass
            if self._place(v, e):
                self.next_vid += 1
                self.active_plates.add(plate)
                self.m["spawned"] += 1
            else:
                if self.tick - r["tick"] > config.BACKLOG_PATIENCE and kind != "ambulance":
                    self.m["turned_away"] += 1
                else:
                    self.backlog.append(r)
                e.slots.cancel_wait(v.id)

    def _exit(self, v: Vehicle, e: Edge):
        e.q.pop(0)
        e.slots.release(v.id)
        self.active_plates.discard(v.plate)
        turn = self.tick - v.spawn_tick
        self.m["exited"] += 1
        self.m["wait_sum"] += v.wait
        self.m["turn_sum"] += turn
        self.m["max_wait"] = max(self.m["max_wait"], v.wait)
        self.exit_times.append(self.tick)
        if v.kind == "ambulance":
            self.m["amb_exited"] += 1
            self.m["amb_wait"] += v.wait
            self.m["amb_turn"] += turn
            self.note(f"Ambulance {v.plate} arrived: {turn} ticks, waited {v.wait}", "ok")
        self.emit("trip", plate=v.plate, origin=v.route[0], dest=v.route[-1], spawn=v.spawn_tick,
                  end=self.tick, wait=v.wait, hops=v.hops, towed=0)
        registered = v.plate.startswith("SO")
        if registered and self.rng.random() < VIOLATIONS["SPEEDING"] * 2:
            self._violation(v, "SPEEDING", e.v)
        if v.park:
            self._park(v)

    def _violation(self, v, code, node):
        self.m["violations"] += 1
        self.emit("violation", plate=v.plate, node=node, code=code)

    def _park(self, v: Vehicle):
        # A parking request references a permit page. Permits follow a Zipf popularity law
        # (regulars park most often), which gives the reference string real locality.
        page = self.rng.choices(self.commuters, self.commuter_w)[0]
        res = self.parking.access(page)
        self.park_refs.append(page)
        self.park_log.appendleft({"tick": self.tick, "plate": page, "hit": res["hit"],
                                  "slot": res["slot"], "evicted": res["evicted"]})
        self.emit("parking", plate=page, hit=int(res["hit"]), slot=res["slot"],
                  evicted=res["evicted"], policy=self.parking.policy)
        ev = res["evicted"]
        if ev and ev.startswith("SO") and self.rng.random() < 0.35:
            self.emit("violation", plate=ev, node=v.route[-1], code="OVERSTAY")
            self.m["violations"] += 1

    def _lane_info(self, e: Edge) -> Lane:
        # ambulances jump the queue (priority inheritance for the lane)
        for k, v in enumerate(e.q):
            if k and v.kind == "ambulance" and v.ready_at <= self.tick and e.q[0].kind != "ambulance":
                e.q.insert(0, e.q.pop(k))
                break
        n = 0
        em = bus = False
        for v in e.q:
            if v.ready_at > self.tick:
                break
            n += 1
            em |= v.kind == "ambulance"
            bus |= v.kind == "bus"
        if n == 0:
            return Lane()
        head = e.q[0]
        nk = head.next_edge_key()
        blocked = e.blocked_until > self.tick or (nk is not None and self._full(self.edges[nk]))
        if blocked:
            return Lane(n=0, emergency=False, bus=False, blocked=True)
        return Lane(n=n, emergency=em, bus=bus)

    def step(self):
        self.tick += 1
        t = self.tick
        # producers -> buffer (headless mode only; live mode uses threads)
        if getattr(self, "inline_producers", True):
            for p in self.producers:
                for req in p.generate(t):
                    self.buffer.put_nowait(req)
        self._existing_cycles = self._cycle_edges() if self.params["deadlock_mode"] == "avoid" else set()
        self._admit()

        # vehicles reaching their destination leave immediately
        for e in self.edges.values():
            while e.q and e.q[0].ready_at <= t and e.q[0].is_last and e.blocked_until <= t:
                self._exit(e.q[0], e)

        order = list(self.nodes.values())
        self.rng.shuffle(order)
        for n in order:
            lanes = {d: self._lane_info(e) for d, e in n.in_edges.items()}
            green = n.sched.step(t, lanes)
            for tk, typ, ap in n.sched.events:
                self.emit("signal", node=n.id, tick=tk, type=typ, approach=ap)
            n.sched.events.clear()
            if green != n.green:
                if n.green is not None:
                    n.box.release(n.green)
                if green is not None:
                    n.box.try_acquire(green)
                n.green = green
            for d, ln in lanes.items():
                if d != green and ln.n > 0:
                    n.box.try_acquire(d)
                else:
                    n.box.cancel_wait(d)
            if green is None:
                continue
            e = n.in_edges[green]
            if not e.q:
                continue
            v = e.q[0]
            if v.ready_at > t or e.blocked_until > t:
                continue
            nk = v.next_edge_key()
            if nk is None:
                continue
            ne = self.edges[nk]
            if self._full(ne):
                ne.slots.try_acquire(v.id)          # registers as a waiter
                continue
            if self.params["deadlock_mode"] == "avoid":
                def do():
                    e.q.pop(0)
                    ne.q.append(v)

                def undo():
                    ne.q.pop()
                    e.q.insert(0, v)
                if not self._tentative_ok(do, undo):
                    self.m["denied_unsafe"] += 1
                    continue
            ne.slots.try_acquire(v.id)
            e.q.pop(0)
            e.slots.release(v.id)
            v.wait += t - v.ready_at
            v.i += 1
            v.hops += 1
            v.enter_tick = t
            v.ready_at = t + (config.AMBULANCE_LEN if v.kind == "ambulance" else ne.length)
            ne.q.append(v)
            n.served += 1
            self.m["cross"] += 1
            if v.plate.startswith("SO") and self.rng.random() < VIOLATIONS["RED_LIGHT"]:
                self._violation(v, "RED_LIGHT", n.id)

        if t - self.last_deadlock_check >= config.DETECT_EVERY or self.params["deadlock_mode"] == "off":
            self.last_deadlock_check = t
            self._detect()

        if t % config.SENSOR_EVERY == 0:
            self._sense()

    def _sense(self):
        t = self.tick
        total_q = 0
        for n in self.nodes.values():
            q = {d: len(e.q) for d, e in n.in_edges.items()}
            total_q += sum(q.values())
            self.emit("sensor", node=n.id, q=q, green=n.green, policy=n.sched.policy)
        recent = sum(1 for x in self.exit_times if x > t - 20)
        done = self.m["exited"]
        self.history.append({
            "tick": t,
            "vehicles": sum(len(e.q) for e in self.edges.values()),
            "throughput": round(recent / 20 * 10, 2),
            "avg_wait": round(self.m["wait_sum"] / done, 2) if done else 0,
            "queue": total_q,
        })

    # -------------------------------------------------------------- snapshots
    def metrics(self) -> dict:
        m = self.m
        done = m["exited"] or 1
        ctx = sum(n.sched.context_switches for n in self.nodes.values())
        pre = sum(n.sched.preemptions for n in self.nodes.values())
        return {
            "tick": self.tick,
            "in_city": sum(len(e.q) for e in self.edges.values()),
            "exited": m["exited"],
            "spawned": m["spawned"],
            "towed": m["towed"],
            "turned_away": m["turned_away"],
            "avg_wait": round(m["wait_sum"] / done, 2),
            "avg_turnaround": round(m["turn_sum"] / done, 2),
            "max_wait": m["max_wait"],
            "throughput": round(m["exited"] / max(1, self.tick) * 100, 1),
            "context_switches": ctx,
            "preemptions": pre,
            "deadlocks": m["deadlocks"],
            "recovered": m["recovered"],
            "denied_unsafe": m["denied_unsafe"],
            "violations": m["violations"],
            "amb_avg_wait": round(m["amb_wait"] / m["amb_exited"], 2) if m["amb_exited"] else None,
            "amb_count": m["amb_exited"],
            "crossings": m["cross"],
        }

    def snapshot(self, selected: Optional[str] = None) -> dict:
        t = self.tick
        sel = selected if selected in self.nodes else nid(config.GRID_ROWS // 2, config.GRID_COLS // 2)
        edges = []
        for e in self.edges.values():
            edges.append({
                "id": e.id, "u": e.u, "v": e.v, "dir": e.dir, "used": len(e.q), "cap": e.cap,
                "stuck": e.stuck, "closed": e.blocked_until > t,
                "cars": [{"id": v.id, "k": v.kind, "f": round(min(1.0, (t - v.enter_tick) / max(1, v.ready_at - v.enter_tick)), 2),
                          "ready": v.ready_at <= t} for v in e.q],
            })
        nodes = []
        for n in self.nodes.values():
            nodes.append({"id": n.id, "r": n.r, "c": n.c, "green": n.green,
                          "clearing": n.sched.pending is not None,
                          "q": {d: len(e.q) for d, e in n.in_edges.items()},
                          "served": n.served, "policy": n.sched.policy})
        sn = self.nodes[sel]
        sched = sn.sched.snapshot()
        sched["node"] = sel
        def lane_blocked(e):
            if not e.q or e.q[0].ready_at > t:
                return False
            nk = e.q[0].next_edge_key()
            return e.blocked_until > t or (nk is not None and self._full(self.edges[nk]))
        sched["lanes"] = {d: {"queue": len(e.q), "ready": sum(1 for v in e.q if v.ready_at <= t),
                              "emergency": any(v.kind == "ambulance" for v in e.q),
                              "blocked": lane_blocked(e)}
                          for d, e in sn.in_edges.items()}
        full_roads = [e for e in self.edges.values() if self._full(e)]
        waiting = [{"road": e.id, "waiters": list(e.slots.waiters)[:4]} for e in self.edges.values() if e.slots.waiters]
        return {
            "tick": t, "params": self.params, "nodes": nodes, "edges": edges,
            "metrics": self.metrics(), "history": list(self.history),
            "scheduler": sched,
            "sync": {
                "buffer": self.buffer.stats(),
                "backlog": len(self.backlog),
                "box": sn.box.snapshot(),
                "full_roads": [e.id for e in full_roads],
                "waiting": waiting[:8],
                "road_sem": {e.id: e.slots.snapshot() for e in self.edges.values() if e.u == sel or e.v == sel},
            },
            "deadlock": {"cycles": self.deadlock_cycles, "mode": self.params["deadlock_mode"],
                         "wait_for": [{"from": a, "to": b} for a, b in self._want_graph().items()]},
            "memory": {**self.parking.snapshot(), "log": list(self.park_log),
                       "refs": list(self.park_refs)[-30:]},
            "log": list(self.log),
        }


# --------------------------------------------------------------- live runner
class LiveRunner:
    """Runs the simulation in real time with producer threads and an RW lock."""

    def __init__(self, sim: CitySimulation):
        self.sim = sim
        self.sim.inline_producers = False
        self.lock = RWLock()
        self._stop = threading.Event()
        self.threads: List[threading.Thread] = []

    def start(self):
        t = threading.Thread(target=self._tick_loop, name="tick", daemon=True)
        self.threads.append(t)
        t.start()
        for p in self.sim.producers:
            th = threading.Thread(target=self._producer_loop, args=(p,), name=f"gate-{p.side}", daemon=True)
            self.threads.append(th)
            th.start()

    def stop(self):
        self._stop.set()

    def _dt(self):
        return config.TICK_SECONDS / max(0.1, float(self.sim.params["speed"]))

    def _tick_loop(self):
        while not self._stop.is_set():
            t0 = time.time()
            if not self.sim.params["paused"]:
                with self.lock.write():
                    self.sim.step()
            time.sleep(max(0.0, self._dt() - (time.time() - t0)))

    def _producer_loop(self, p: GateProducer):
        while not self._stop.is_set():
            if not self.sim.params["paused"]:
                for req in p.generate(self.sim.tick):
                    self.sim.buffer.put(req, timeout=1.0)
            time.sleep(self._dt())

    def snapshot(self, selected=None):
        with self.lock.read():
            return self.sim.snapshot(selected)

    def act(self, fn, *a, **k):
        with self.lock.write():
            return fn(*a, **k)


def run_headless(policy: str, ticks: int = 500, seed: int = 11, demand: float = 2.4,
                 deadlock_mode: str = "detect", emergency: bool = True, ambulance_every: int = 70) -> dict:
    sim = CitySimulation(seed=seed, params={"policy": policy, "demand": demand,
                                            "deadlock_mode": deadlock_mode, "emergency": emergency})
    for t in range(ticks):
        if ambulance_every and t and t % ambulance_every == 0:
            sim.action("ambulance")
        sim.step()
    m = sim.metrics()
    m["policy"] = policy
    m["in_city_end"] = m.pop("in_city")
    return m
