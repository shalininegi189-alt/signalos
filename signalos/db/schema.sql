-- SignalOS relational schema (SQLite). Normalised to 3NF.
-- DBMS MODULE 1: schema design, keys and integrity constraints.
-- DBMS MODULE 5: triggers and views (bottom of file).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS intersection (
    node_id   TEXT PRIMARY KEY,
    grid_row  INTEGER NOT NULL CHECK (grid_row >= 0),
    grid_col  INTEGER NOT NULL CHECK (grid_col >= 0),
    name      TEXT,                                   -- real Dehradun locality
    zone      TEXT,                                   -- hub, school, gateway, ...
    UNIQUE (grid_row, grid_col)
);

CREATE TABLE IF NOT EXISTS road (
    road_id    TEXT PRIMARY KEY,
    from_node  TEXT NOT NULL REFERENCES intersection(node_id),
    to_node    TEXT NOT NULL REFERENCES intersection(node_id),
    direction  TEXT NOT NULL CHECK (direction IN ('N','E','S','W')),
    length     INTEGER NOT NULL CHECK (length > 0),
    capacity   INTEGER NOT NULL CHECK (capacity > 0),
    street     TEXT,                                  -- e.g. Rajpur Road
    UNIQUE (from_node, to_node),
    CHECK (from_node <> to_node)
);

CREATE TABLE IF NOT EXISTS owner (
    owner_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL,
    email    TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS wallet (
    owner_id INTEGER PRIMARY KEY REFERENCES owner(owner_id) ON DELETE CASCADE,
    balance  REAL NOT NULL CHECK (balance >= 0)       -- consistency: never negative
);

CREATE TABLE IF NOT EXISTS treasury (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    balance REAL NOT NULL DEFAULT 0 CHECK (balance >= 0)
);

CREATE TABLE IF NOT EXISTS vehicle (
    plate    TEXT PRIMARY KEY,
    owner_id INTEGER NOT NULL REFERENCES owner(owner_id),
    kind     TEXT NOT NULL CHECK (kind IN ('car','bus','truck','ambulance'))
);

CREATE TABLE IF NOT EXISTS trip (
    trip_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    plate      TEXT NOT NULL,
    origin     TEXT NOT NULL REFERENCES intersection(node_id),
    dest       TEXT NOT NULL REFERENCES intersection(node_id),
    spawn_tick INTEGER NOT NULL,
    end_tick   INTEGER NOT NULL,
    wait_ticks INTEGER NOT NULL CHECK (wait_ticks >= 0),
    hops       INTEGER NOT NULL,
    towed      INTEGER NOT NULL DEFAULT 0 CHECK (towed IN (0,1))
);

CREATE TABLE IF NOT EXISTS sensor_reading (
    reading_id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id    TEXT NOT NULL REFERENCES intersection(node_id),
    tick       INTEGER NOT NULL,
    q_n        INTEGER NOT NULL DEFAULT 0,
    q_e        INTEGER NOT NULL DEFAULT 0,
    q_s        INTEGER NOT NULL DEFAULT 0,
    q_w        INTEGER NOT NULL DEFAULT 0,
    green      TEXT,
    policy     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_event (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id  TEXT NOT NULL REFERENCES intersection(node_id),
    tick     INTEGER NOT NULL,
    type     TEXT NOT NULL CHECK (type IN ('GREEN','SWITCH','PREEMPT')),
    approach TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS violation_type (
    code      TEXT PRIMARY KEY,
    label     TEXT NOT NULL,
    base_fine REAL NOT NULL CHECK (base_fine > 0)
);

CREATE TABLE IF NOT EXISTS violation (
    violation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    plate        TEXT NOT NULL REFERENCES vehicle(plate),
    node_id      TEXT REFERENCES intersection(node_id),
    code         TEXT NOT NULL REFERENCES violation_type(code),
    tick         INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS fine (
    fine_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    violation_id INTEGER NOT NULL UNIQUE REFERENCES violation(violation_id),
    plate        TEXT NOT NULL REFERENCES vehicle(plate),
    amount       REAL NOT NULL CHECK (amount > 0),
    status       TEXT NOT NULL DEFAULT 'UNPAID' CHECK (status IN ('UNPAID','PAID','WAIVED')),
    issued_tick  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS incident (
    incident_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    road_id     TEXT,
    start_tick  INTEGER NOT NULL,
    end_tick    INTEGER,
    details     TEXT
);

CREATE TABLE IF NOT EXISTS parking_event (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tick     INTEGER NOT NULL,
    plate    TEXT NOT NULL,
    hit      INTEGER NOT NULL CHECK (hit IN (0,1)),
    slot     INTEGER,
    evicted  TEXT,
    policy   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deadlock_event (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tick     INTEGER NOT NULL,
    mode     TEXT NOT NULL,
    cycle    TEXT NOT NULL,
    victim   TEXT,
    resolved INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,
    op         TEXT NOT NULL,
    row_key    TEXT,
    old_value  TEXT,
    new_value  TEXT,
    at         TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ----------------------------------------------------------------- indexes
CREATE INDEX IF NOT EXISTS idx_trip_plate       ON trip(plate);
CREATE INDEX IF NOT EXISTS idx_violation_plate  ON violation(plate);
CREATE INDEX IF NOT EXISTS idx_fine_status      ON fine(status);
CREATE INDEX IF NOT EXISTS idx_signal_node_tick ON signal_event(node_id, tick);

-- ---------------------------------------------------------------- triggers
-- A violation automatically produces a fine; repeat offenders pay +25 % per
-- earlier violation (capped at +100 %).
CREATE TRIGGER IF NOT EXISTS trg_violation_issue_fine
AFTER INSERT ON violation
BEGIN
    INSERT INTO fine(violation_id, plate, amount, status, issued_tick)
    SELECT NEW.violation_id, NEW.plate,
           vt.base_fine * (1 + 0.25 * MIN(4, (SELECT COUNT(*) FROM violation
                                              WHERE plate = NEW.plate AND violation_id < NEW.violation_id))),
           'UNPAID', NEW.tick
    FROM violation_type vt WHERE vt.code = NEW.code;
END;

-- Fine status changes are audited.
CREATE TRIGGER IF NOT EXISTS trg_fine_audit
AFTER UPDATE OF status ON fine
BEGIN
    INSERT INTO audit_log(table_name, op, row_key, old_value, new_value)
    VALUES ('fine', 'UPDATE', NEW.fine_id, OLD.status, NEW.status);
END;

-- A paid fine can never be re-opened (business rule enforced by the DBMS).
CREATE TRIGGER IF NOT EXISTS trg_paid_fine_final
BEFORE UPDATE OF status ON fine
WHEN OLD.status = 'PAID' AND NEW.status <> 'PAID'
BEGIN
    SELECT RAISE(ABORT, 'A paid fine cannot be re-opened');
END;

-- Every wallet movement is audited.
CREATE TRIGGER IF NOT EXISTS trg_wallet_audit
AFTER UPDATE OF balance ON wallet
BEGIN
    INSERT INTO audit_log(table_name, op, row_key, old_value, new_value)
    VALUES ('wallet', 'UPDATE', NEW.owner_id, OLD.balance, NEW.balance);
END;

-- ------------------------------------------------------------------- views
CREATE VIEW IF NOT EXISTS v_intersection_load AS
SELECT i.node_id,
       ROUND(AVG(s.q_n + s.q_e + s.q_s + s.q_w), 2) AS avg_queue,
       MAX(s.q_n + s.q_e + s.q_s + s.q_w)           AS peak_queue,
       COUNT(*)                                      AS samples
FROM intersection i JOIN sensor_reading s ON s.node_id = i.node_id
GROUP BY i.node_id;

CREATE VIEW IF NOT EXISTS v_policy_performance AS
SELECT policy,
       ROUND(AVG(q_n + q_e + q_s + q_w), 3) AS avg_queue,
       COUNT(*)                              AS samples
FROM sensor_reading GROUP BY policy;

CREATE VIEW IF NOT EXISTS v_top_offenders AS
SELECT v.plate, o.name, COUNT(*) AS violations, ROUND(SUM(f.amount), 2) AS total_fines,
       SUM(f.status = 'UNPAID') AS unpaid
FROM violation v
JOIN fine f    ON f.violation_id = v.violation_id
JOIN vehicle ve ON ve.plate = v.plate
JOIN owner o   ON o.owner_id = ve.owner_id
GROUP BY v.plate ORDER BY violations DESC, total_fines DESC;

CREATE VIEW IF NOT EXISTS v_flow_by_bucket AS
SELECT end_tick / 100 AS bucket, COUNT(*) AS trips,
       ROUND(AVG(wait_ticks), 2) AS avg_wait,
       SUM(towed) AS towed
FROM trip GROUP BY bucket ORDER BY bucket;

CREATE VIEW IF NOT EXISTS v_parking_efficiency AS
SELECT policy, COUNT(*) AS accesses, SUM(hit) AS hits,
       ROUND(100.0 * SUM(hit) / COUNT(*), 1) AS hit_pct
FROM parking_event GROUP BY policy;

-- Localities by name (Dehradun chowks), for reports and the SQL console.
CREATE VIEW IF NOT EXISTS v_locality_load AS
SELECT i.node_id, i.name AS locality, i.zone,
       ROUND(AVG(s.q_n + s.q_e + s.q_s + s.q_w), 2) AS avg_queue,
       MAX(s.q_n + s.q_e + s.q_s + s.q_w)           AS peak_queue,
       COUNT(*)                                      AS samples
FROM intersection i JOIN sensor_reading s ON s.node_id = i.node_id
GROUP BY i.node_id;

-- Average queue per approach: the input of the signal-timing planner.
CREATE VIEW IF NOT EXISTS v_signal_plan_input AS
SELECT i.node_id, i.name AS locality, i.zone,
       ROUND(AVG(s.q_n), 3) AS q_n, ROUND(AVG(s.q_e), 3) AS q_e,
       ROUND(AVG(s.q_s), 3) AS q_s, ROUND(AVG(s.q_w), 3) AS q_w, COUNT(*) AS samples
FROM intersection i JOIN sensor_reading s ON s.node_id = i.node_id
GROUP BY i.node_id;
