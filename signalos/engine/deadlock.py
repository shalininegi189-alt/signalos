"""OS MODULE 3 - Deadlock handling.

Gridlock is a real deadlock: every road segment is a counting semaphore, a car
holds its current segment while waiting for the next one (hold-and-wait, no
preemption, circular wait).

* detection  : cycle search in the wait-for graph (functional graph + Tarjan SCC)
* recovery   : victim selection (lowest priority / least work lost) and tow-away
* avoidance  : the simulation only grants a move if the resulting state has no
               new cycle (safe-state test) - see CitySimulation._move_is_safe
* Banker's algorithm: full textbook implementation used by the lab page
"""
from __future__ import annotations

from typing import Dict, Hashable, Iterable, List, Sequence, Tuple


def functional_cycles(want: Dict[Hashable, Hashable]) -> List[List[Hashable]]:
    """Cycles in a graph where every node has at most one outgoing edge.

    Used for gridlock: a blocked head car waits for exactly one next road.
    """
    state: Dict[Hashable, int] = {}      # 0/missing = unseen, 1 = on path, 2 = done
    cycles: List[List[Hashable]] = []
    for start in want:
        if state.get(start):
            continue
        path: List[Hashable] = []
        node = start
        while node is not None and not state.get(node):
            state[node] = 1
            path.append(node)
            node = want.get(node)
        if node is not None and state.get(node) == 1:
            cycles.append(path[path.index(node):])
        for n in path:
            state[n] = 2
    return cycles


def find_cycles(graph: Dict[Hashable, Iterable[Hashable]]) -> List[List[Hashable]]:
    """General wait-for graph: strongly connected components that contain a cycle (Tarjan)."""
    index: Dict[Hashable, int] = {}
    low: Dict[Hashable, int] = {}
    on_stack: set = set()
    stack: List[Hashable] = []
    result: List[List[Hashable]] = []
    counter = [0]

    nodes = set(graph)
    for targets in graph.values():
        nodes.update(targets)

    def strong(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in graph.get(v, ()):
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1 or v in graph.get(v, ()):
                result.append(comp[::-1])

    for v in sorted(nodes, key=str):
        if v not in index:
            strong(v)
    return result


# --------------------------------------------------------------- Banker's -----
def bankers_safety(available: Sequence[int], maximum: Sequence[Sequence[int]],
                   allocation: Sequence[Sequence[int]]) -> dict:
    """Safety algorithm. Returns {'safe', 'sequence', 'need', 'steps'}."""
    n, m = len(allocation), len(available)
    need = [[maximum[i][j] - allocation[i][j] for j in range(m)] for i in range(n)]
    work = list(available)
    finished = [False] * n
    sequence: List[int] = []
    steps = []
    progress = True
    while progress:
        progress = False
        for i in range(n):
            if not finished[i] and all(need[i][j] <= work[j] for j in range(m)):
                before = list(work)
                for j in range(m):
                    work[j] += allocation[i][j]
                finished[i] = True
                sequence.append(i)
                steps.append({"process": i, "work_before": before, "work_after": list(work)})
                progress = True
    return {"safe": all(finished), "sequence": sequence, "need": need, "steps": steps}


def bankers_request(available, maximum, allocation, pid: int, request: Sequence[int]) -> dict:
    """Resource-request algorithm: grant only if the resulting state is safe."""
    m = len(available)
    need = [maximum[pid][j] - allocation[pid][j] for j in range(m)]
    if any(request[j] > need[j] for j in range(m)):
        return {"granted": False, "reason": "Request exceeds the process's declared maximum claim."}
    if any(request[j] > available[j] for j in range(m)):
        return {"granted": False, "reason": "Not enough resources available: the process must wait."}
    new_avail = [available[j] - request[j] for j in range(m)]
    new_alloc = [list(r) for r in allocation]
    for j in range(m):
        new_alloc[pid][j] += request[j]
    res = bankers_safety(new_avail, maximum, new_alloc)
    if res["safe"]:
        return {"granted": True, "reason": "Resulting state is safe.", "sequence": res["sequence"]}
    return {"granted": False, "reason": "Granting the request would leave the system in an unsafe state."}


def road_deadlock_demo() -> dict:
    """Preset for the lab: 4 cars, 1 resource type (free road slots)."""
    return {
        "available": [1, 0],
        "maximum": [[3, 2], [2, 2], [2, 3], [4, 2]],
        "allocation": [[1, 0], [1, 1], [1, 1], [0, 1]],
    }
