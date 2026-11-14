# SignalOS: a city's traffic, run like an operating system

Third-year B.Tech CSE project combining **Operating Systems** and **DBMS**.

Most traffic projects switch lights on a timer. SignalOS treats the whole city as a computer:

| City | Operating-system idea |
|---|---|
| Intersection | CPU |
| Lane | Process |
| Green light | CPU time (quantum) |
| All-red clearance | Context-switch cost |
| Road slot | Counting semaphore |
| Intersection box | Mutex |
| Gridlock | Deadlock (all four Coffman conditions) |
| Downtown garage | Virtual memory (bays = frames, permits = pages) |
| Ambulance | Real-time, pre-emptive process |

Everything the city does (trips, signal changes, fines, parking, deadlocks) is stored in a SQLite database, so scheduling policies are compared with SQL.

## Run it

```bash
pip install -r requirements.txt
python app.py            # open http://127.0.0.1:5000
python -m unittest discover -s tests -t .      # 48 tests
```
Python 3.10+. No other dependencies; charts are hand-written SVG and work offline.

## Syllabus coverage (need 3 + 3, delivered 4 + 6)

**OS**: CPU scheduling (`engine/scheduler.py`: FCFS, Round Robin, SRTF with aging, Priority with aging, MLFQ),
process synchronisation (`engine/sync.py`: semaphores, mutex, bounded buffer, readers-writer lock, real threads),
deadlock (`engine/deadlock.py`: wait-for graph, detect and recover, avoid, Banker's algorithm),
memory management (`engine/memory.py`: FIFO, LRU, LFU, Clock, OPT, Belady's anomaly).

**DBMS**: schema design in 3NF (`db/schema.sql`), transactions and ACID with savepoints (`db/transactions.py`),
concurrency control (`db/concurrency.py`: precedence graph, strict 2PL, lost-update experiment),
indexing and query optimisation (`db/optimizer.py`), triggers and views (`db/schema.sql`),
write-ahead logging and ARIES-style crash recovery (`db/recovery.py`).

The **Syllabus map** tab in the app lists each module with the viva questions it answers.

## What to try in a demo

1. Command centre: switch scheduling policy, press **Send an ambulance**, watch the Gantt strip and the signals yield.
2. Set gridlock handling to **Ignore**, press **Force a gridlock**: four roads freeze. Switch to **Detect and recover**: a victim is towed. Try **Avoid**.
3. OS lab: run the policy arena, Banker's workbench, Belady's anomaly.
4. Database lab: inject a power cut into a fine payment and watch the rollback; run the lost-update experiment; compare index plans; pull the plug on the recovery demo.

## Layout

```
app.py          Flask API and entry point        config.py   tunable constants
engine/         OS modules and the simulation    db/         DBMS modules and schema
static/         front end (HTML, CSS, JS)        tests/      48 unit tests
modules.py      syllabus map shown in the app    data/       SQLite file, created on first run
```
`data/signalos.db` is created and pre-filled on first start. Delete the `data/` contents to start fresh.
