"""SignalOS - a city traffic network run like an operating system on top of a real DBMS.

Run:  python app.py      then open http://127.0.0.1:5000
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3
import time
from functools import lru_cache

from flask import Flask, Response, jsonify, request, send_from_directory

import config
import places
import planner
from db import concurrency, database, optimizer, recovery, transactions
from engine import deadlock, memory
from engine.scheduler import POLICIES, POLICY_LABELS
from engine.simulation import CitySimulation, LiveRunner, run_headless
from modules import MODULES

app = Flask(__name__, static_folder="static", static_url_path="/static")
STATE: dict = {}


def boot() -> None:
    top = database.ensure_database()
    writer = database.DBWriter()
    sim = CitySimulation(seed=7, emit=writer.emit)
    sim.tick_base = top
    runner = LiveRunner(sim)
    writer.start()
    runner.start()
    STATE.update(sim=sim, runner=runner, writer=writer)


def db(readonly=False):
    return database.connect(readonly=readonly)


def err(msg, code=400):
    return jsonify({"error": msg}), code


# ----------------------------------------------------------------- pages ----
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/topology")
def topology():
    return jsonify(STATE["sim"].topology() | {"rows": config.GRID_ROWS, "cols": config.GRID_COLS,
                                              "policies": [{"id": p, "label": POLICY_LABELS[p]} for p in POLICIES]})


@app.get("/api/modules")
def modules():
    return jsonify(MODULES)


# ------------------------------------------------------------ live control --
@app.get("/api/state")
def state():
    snap = STATE["runner"].snapshot(request.args.get("node"))
    snap["db_written"] = STATE["writer"].written
    snap["db_queue"] = STATE["writer"].q.qsize()
    return jsonify(snap)


@app.post("/api/control")
def control():
    body = request.get_json(force=True) or {}
    allowed = {}
    if "policy" in body and body["policy"] in POLICIES:
        allowed["policy"] = body["policy"]
    if "quantum" in body:
        allowed["quantum"] = max(1, min(10, int(body["quantum"])))
    if "emergency" in body:
        allowed["emergency"] = bool(body["emergency"])
    if body.get("deadlock_mode") in ("off", "detect", "avoid"):
        allowed["deadlock_mode"] = body["deadlock_mode"]
    if body.get("parking_policy") in memory.POLICIES:
        allowed["parking_policy"] = body["parking_policy"]
    if body.get("scenario") in places.SCENARIOS:
        allowed["scenario"] = body["scenario"]
    if "demand" in body:
        allowed["demand"] = max(0.0, min(5.0, float(body["demand"])))
    if "speed" in body:
        allowed["speed"] = max(0.25, min(6.0, float(body["speed"])))
    if "paused" in body:
        allowed["paused"] = bool(body["paused"])
    STATE["runner"].act(STATE["sim"].set_params, **allowed)
    return jsonify({"ok": True, "params": STATE["sim"].params})


@app.post("/api/action")
def action():
    kind = (request.get_json(force=True) or {}).get("type", "")
    msg = STATE["runner"].act(STATE["sim"].action, kind)
    return jsonify({"ok": True, "message": msg})


# ------------------------------------------------------- Dehradun real-life hooks --
@app.get("/api/places")
def get_places():
    return jsonify({"city": places.CITY, "places": [places.place(n) for n in places.PLACES]})


@app.get("/api/scenarios")
def scenarios():
    return jsonify([{"id": k, "label": v["label"], "desc": v["desc"]} for k, v in places.SCENARIOS.items()])


def _gate_lookup() -> dict:
    """Every accepted spelling of an entry point (name, short name, node id) -> node id."""
    out = {}
    for n in places.BORDER:
        name, short = places.PLACES[n][0], places.PLACES[n][1]
        for key in (name, short, n):
            out[key.strip().lower()] = n
    return out


@app.get("/api/real/template.csv")
def counts_template():
    rows = ["locality,vehicles_per_hour"] + [f"{places.name(n)}," for n in places.BORDER]
    return Response("\n".join(rows) + "\n", mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=dehradun_counts_template.csv"})


@app.get("/api/real/counts")
def counts_state():
    w = STATE["sim"].custom_weights
    return jsonify({"active": bool(w), "weights": {places.name(n): round(v, 2) for n, v in w.items()}})


@app.post("/api/real/counts")
def counts_upload():
    """Accepts {"csv": "locality,vehicles_per_hour\n..."} or {"clear": true}."""
    body = request.get_json(force=True) or {}
    if body.get("clear"):
        STATE["runner"].act(STATE["sim"].set_real_counts, {})
        return jsonify({"ok": True, "applied": {}, "ignored": [], "message": "Back to the default weights."})
    lookup, counts, ignored = _gate_lookup(), {}, []
    for row in csv.reader(io.StringIO(str(body.get("csv", "")))):
        if len(row) < 2 or not row[0].strip():
            continue
        key, raw = row[0].strip(), row[1].strip()
        if raw == "" or key.lower() == "locality":
            continue
        try:
            vph = float(raw)
        except ValueError:
            ignored.append(f"{key}: '{raw}' is not a number")
            continue
        node = lookup.get(key.lower())
        if node is None:
            known = key.lower() in {v[0].lower() for v in places.PLACES.values()}
            ignored.append(f"{key}: " + ("is inside the city, counts are taken at entry points only" if known else "unknown locality"))
        elif vph <= 0:
            ignored.append(f"{key}: count must be above 0")
        else:
            counts[node] = vph
    if not counts:
        return err("No usable rows. Use the template: one entry point per line, vehicles per hour in column 2. "
                   + ("; ".join(ignored[:3]) if ignored else ""))
    STATE["runner"].act(STATE["sim"].set_real_counts, counts)
    return jsonify({"ok": True, "applied": {places.name(n): v for n, v in counts.items()}, "ignored": ignored,
                    "message": f"{len(counts)} entry points now follow your counts."})


@app.get("/api/export/signal-plan.csv")
def export_signal_plan():
    try:
        cycle = max(60, min(240, int(request.args.get("cycle", 120))))
    except ValueError:
        return err("cycle must be a whole number of seconds")
    conn = db(readonly=True)
    rows = conn.execute("SELECT * FROM v_signal_plan_input ORDER BY node_id").fetchall()
    conn.close()
    if not rows:
        return err("No sensor data stored yet. Let the simulation run for a minute.")
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["node_id", "locality", "zone", "cycle_s", "green_N_s", "green_E_s", "green_S_s", "green_W_s",
                "avg_queue_N", "avg_queue_E", "avg_queue_S", "avg_queue_W", "samples", "map_link"])
    for r in rows:
        q = {"N": r["q_n"], "E": r["q_e"], "S": r["q_s"], "W": r["q_w"]}
        g = planner.green_split(q, cycle=cycle)
        w.writerow([r["node_id"], r["locality"], r["zone"], cycle, g["N"], g["E"], g["S"], g["W"],
                    q["N"], q["E"], q["S"], q["W"], r["samples"], places.maps_url(r["locality"] or "")])
    w.writerow([])
    w.writerow(["NOTE: proposal computed from simulated queues. Validate against field counts and local traffic "
                "engineering standards before use. 3 s lost time per phase, 10 s minimum green."])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=dehradun_signal_plan_{cycle}s.csv"})


# ------------------------------------------------------------------ OS lab --
@lru_cache(maxsize=16)
def _arena(ticks: int, demand: float, mode: str):
    return [run_headless(p, ticks=ticks, demand=demand, deadlock_mode=mode) for p in POLICIES]


@app.get("/api/os/arena")
def arena():
    ticks = max(100, min(1200, int(request.args.get("ticks", 600))))
    demand = max(0.5, min(5.0, float(request.args.get("demand", 2.8))))
    t0 = time.time()
    rows = _arena(ticks, round(demand, 2), "detect")
    return jsonify({"ticks": ticks, "demand": demand, "seconds": round(time.time() - t0, 2),
                    "rows": [{k: r[k] for k in ("policy", "exited", "avg_wait", "max_wait", "avg_turnaround",
                                                "throughput", "context_switches", "preemptions",
                                                "amb_avg_wait", "in_city_end")} for r in rows]})


@app.get("/api/os/deadlock-modes")
def deadlock_modes():
    out = []
    for mode in ("off", "detect", "avoid"):
        sim = CitySimulation(seed=21, params={"deadlock_mode": mode, "demand": 0.8})
        for _ in range(25):
            sim.step()
        sim.action("gridlock")
        for _ in range(90):
            sim.step()
        m = sim.metrics()
        out.append({"mode": mode, "stuck_roads": sum(e.stuck for e in sim.edges.values()),
                    "deadlocks": m["deadlocks"], "recovered": m["recovered"], "towed": m["towed"],
                    "denied": m["denied_unsafe"], "in_city": m["in_city"], "exited": m["exited"]})
    return jsonify(out)


@app.get("/api/os/banker/preset")
def banker_preset():
    return jsonify(deadlock.road_deadlock_demo())


@app.post("/api/os/banker")
def banker():
    b = request.get_json(force=True)
    try:
        av, mx, al = b["available"], b["maximum"], b["allocation"]
        out = deadlock.bankers_safety(av, mx, al)
        if b.get("request") is not None:
            out["request_result"] = deadlock.bankers_request(av, mx, al, int(b.get("pid", 0)), b["request"])
        return jsonify(out)
    except (KeyError, IndexError, TypeError, ValueError):
        return err("Matrices must be numbers with matching sizes: available (m), maximum and allocation (n x m).")


@app.post("/api/os/pages")
def pages():
    b = request.get_json(force=True)
    try:
        refs = [int(x) for x in re.split(r"[\s,]+", str(b.get("refs", "")).strip()) if x != ""]
        frames = int(b.get("frames", 3))
    except ValueError:
        return err("The reference string must be whole numbers separated by commas or spaces.")
    if not refs or not (1 <= frames <= 12) or len(refs) > 200:
        return err("Give 1-200 page numbers and 1-12 frames.")
    return jsonify({p: memory.simulate(refs, frames, p) for p in memory.POLICIES + ["OPT"]})


@app.get("/api/os/belady")
def belady():
    return jsonify(memory.belady_demo())


@app.get("/api/os/parking-replay")
def parking_replay():
    refs = list(STATE["sim"].park_refs)
    if len(refs) < 20:
        return err("Not enough parking activity yet. Let the simulation run for a minute.")
    out = memory.compare(refs, config.PARKING_FRAMES)
    return jsonify({"references": len(refs), "frames": config.PARKING_FRAMES, "results": out})


# ----------------------------------------------------------------- DB lab ---
@app.get("/api/db/schema")
def schema():
    conn = db(readonly=True)
    tables = []
    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        name = r["name"]
        cols = [dict(c) for c in conn.execute(f"PRAGMA table_info({name})")]
        fks = [dict(f) for f in conn.execute(f"PRAGMA foreign_key_list({name})")]
        count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        tables.append({"name": name, "rows": count,
                       "columns": [{"name": c["name"], "type": c["type"], "pk": bool(c["pk"]),
                                    "notnull": bool(c["notnull"])} for c in cols],
                       "fks": [{"from": f["from"], "table": f["table"], "to": f["to"]} for f in fks]})
    objs = conn.execute("SELECT type, name, tbl_name FROM sqlite_master WHERE type IN ('index','trigger','view') "
                        "AND name NOT LIKE 'sqlite_%' ORDER BY type, name").fetchall()
    conn.close()
    return jsonify({"tables": tables, "indexes": [dict(o) for o in objs if o["type"] == "index"],
                    "triggers": [dict(o) for o in objs if o["type"] == "trigger"],
                    "views": [dict(o) for o in objs if o["type"] == "view"]})


@app.post("/api/db/query")
def query():
    sql = ((request.get_json(force=True) or {}).get("sql") or "").strip().rstrip(";").strip()
    if not sql:
        return err("Type a query first.")
    if ";" in sql:
        return err("Run one statement at a time.")
    if not re.match(r"^(select|with|explain)\b", sql, re.IGNORECASE):
        return err("The console is read-only: start with SELECT, WITH or EXPLAIN.")
    conn = db(readonly=True)
    try:
        t0 = time.perf_counter()
        cur = conn.execute(sql)
        rows = cur.fetchmany(200)
        cols = [d[0] for d in cur.description or []]
        ms = round((time.perf_counter() - t0) * 1000, 2)
        return jsonify({"columns": cols, "rows": [list(r) for r in rows], "ms": ms, "truncated": len(rows) == 200})
    except sqlite3.Error as exc:
        return err(f"SQL error: {exc}")
    finally:
        conn.close()


@app.get("/api/db/acid/candidates")
def acid_candidates():
    conn = db(readonly=True)
    rows = conn.execute(
        """SELECT f.fine_id, f.plate, f.amount, vt.label, w.balance
           FROM fine f JOIN violation v ON v.violation_id = f.violation_id
           JOIN violation_type vt ON vt.code = v.code
           JOIN vehicle ve ON ve.plate = f.plate JOIN wallet w ON w.owner_id = ve.owner_id
           WHERE f.status = 'UNPAID' ORDER BY f.fine_id DESC LIMIT 12""").fetchall()
    owners = conn.execute(
        """SELECT o.owner_id, o.name, COUNT(*) AS unpaid, ROUND(SUM(f.amount),2) AS owed, ROUND(w.balance,2) AS balance
           FROM fine f JOIN vehicle ve ON ve.plate = f.plate JOIN owner o ON o.owner_id = ve.owner_id
           JOIN wallet w ON w.owner_id = o.owner_id WHERE f.status='UNPAID'
           GROUP BY o.owner_id HAVING unpaid >= 2 ORDER BY owed DESC LIMIT 6""").fetchall()
    conn.close()
    return jsonify({"fines": [dict(r) for r in rows], "owners": [dict(r) for r in owners]})


@app.post("/api/db/acid")
def acid():
    b = request.get_json(force=True)
    conn = db()
    try:
        fail = b.get("fail_at") or None
        if fail not in (None, "after_debit", "after_credit", "overdraw"):
            return err("Unknown failure mode.")
        return jsonify(transactions.pay_fine(conn, int(b["fine_id"]), fail))
    except (ValueError, KeyError, TypeError) as exc:
        return err(str(exc))
    finally:
        conn.close()


@app.post("/api/db/pay-all")
def pay_all():
    b = request.get_json(force=True)
    conn = db()
    try:
        return jsonify(transactions.pay_all(conn, int(b["owner_id"])))
    except (ValueError, KeyError, TypeError) as exc:
        return err(str(exc))
    finally:
        conn.close()


@app.post("/api/db/trigger-demo")
def trigger_demo():
    code = (request.get_json(force=True) or {}).get("code", "RED_LIGHT")
    conn = db()
    try:
        return jsonify(transactions.issue_test_violation(conn, code=code))
    except sqlite3.Error as exc:
        return err(str(exc))
    finally:
        conn.close()


@app.post("/api/db/concurrency")
def concurrency_check():
    try:
        return jsonify(concurrency.analyse((request.get_json(force=True) or {}).get("schedule", "")))
    except ValueError as exc:
        return err(str(exc))


@app.get("/api/db/lost-update")
def lost_update():
    return jsonify(concurrency.lost_update_demo())


@app.get("/api/db/benchmark")
def benchmark():
    return jsonify(optimizer.benchmark())


@app.get("/api/db/recovery")
def recovery_demo():
    return jsonify(recovery.run_scenario())


@app.get("/api/db/wal")
def wal_info():
    conn = db()
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    ckpt = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    size = config.DB_PATH.with_name(config.DB_PATH.name + "-wal")
    conn.close()
    return jsonify({"journal_mode": mode, "wal_pages": ckpt[1], "checkpointed": ckpt[2],
                    "wal_bytes": size.stat().st_size if size.exists() else 0})


ANALYTICS = {
    "policy": "SELECT policy, avg_queue, samples, RANK() OVER (ORDER BY avg_queue) AS rank FROM v_policy_performance ORDER BY rank",
    "load": "SELECT node_id, avg_queue, peak_queue, RANK() OVER (ORDER BY avg_queue DESC) AS busiest FROM v_intersection_load ORDER BY node_id",
    "flow": "SELECT bucket, trips, avg_wait, towed FROM v_flow_by_bucket",
    "offenders": "SELECT * FROM v_top_offenders LIMIT 8",
    "parking": "SELECT * FROM v_parking_efficiency ORDER BY hit_pct DESC",
    "fines": "SELECT status, COUNT(*) AS fines, ROUND(SUM(amount),2) AS total FROM fine GROUP BY status",
    "violations": "SELECT code, COUNT(*) AS n FROM violation GROUP BY code ORDER BY n DESC",
    "deadlocks": "SELECT mode, COUNT(*) AS events, SUM(resolved) AS resolved FROM deadlock_event GROUP BY mode",
    "audit": "SELECT audit_id, table_name, row_key, old_value, new_value, at FROM audit_log ORDER BY audit_id DESC LIMIT 8",
    "totals": ("SELECT (SELECT COUNT(*) FROM trip) AS trips, (SELECT COUNT(*) FROM sensor_reading) AS readings, "
               "(SELECT COUNT(*) FROM violation) AS violations, (SELECT ROUND(balance,2) FROM treasury) AS treasury"),
}


@app.get("/api/db/analytics")
def analytics():
    conn = db(readonly=True)
    out = {k: [dict(r) for r in conn.execute(q)] for k, q in ANALYTICS.items()}
    conn.close()
    return jsonify(out)


if __name__ == "__main__":
    boot()
    print("\n  SignalOS (Dehradun) is running -> http://127.0.0.1:5000\n")
    app.run(host="127.0.0.1", port=5000, threaded=True, debug=False)
