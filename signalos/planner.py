"""Turn measured queues into a signal-timing proposal (green seconds per approach).

Method: proportional split of the usable cycle (like a simplified Webster plan).
usable = cycle - lost_time * 4 phases; each approach gets at least `min_green`, the rest is shared in
proportion to its average queue. Integer seconds are assigned with the largest-remainder method so the
greens always add up exactly to `usable`.
"""
from __future__ import annotations

SIDES = ("N", "E", "S", "W")


def green_split(queues: dict, cycle: int = 120, min_green: int = 10, lost_time: int = 3) -> dict:
    usable = cycle - lost_time * len(SIDES)
    if usable < min_green * len(SIDES):
        raise ValueError(f"A {cycle}s cycle is too short for 4 approaches with {min_green}s minimum green.")
    q = {s: max(0.0, float(queues.get(s, 0) or 0)) for s in SIDES}
    spare = usable - min_green * len(SIDES)
    total = sum(q.values())
    share = {s: (q[s] / total if total > 0 else 0.25) for s in SIDES}
    raw = {s: min_green + spare * share[s] for s in SIDES}
    out = {s: int(raw[s]) for s in SIDES}
    leftover = usable - sum(out.values())
    for s in sorted(SIDES, key=lambda k: raw[k] - out[k], reverse=True)[:leftover]:
        out[s] += 1
    return out
