"""DBMS MODULE 2 - Transactions and ACID.

Paying a traffic fine moves money between three tables. It must be atomic:
either wallet, treasury and fine all change, or nothing does.
"""
from __future__ import annotations

import sqlite3


class SimulatedFailure(Exception):
    pass


def _state(conn, fine_id):
    row = conn.execute(
        """SELECT f.fine_id, f.amount, f.status, f.plate, ve.owner_id, w.balance AS wallet
           FROM fine f JOIN vehicle ve ON ve.plate = f.plate JOIN wallet w ON w.owner_id = ve.owner_id
           WHERE f.fine_id = ?""", (fine_id,)).fetchone()
    treasury = conn.execute("SELECT balance FROM treasury WHERE id = 1").fetchone()[0]
    return row, treasury


def snapshot(conn, fine_id) -> dict:
    row, treasury = _state(conn, fine_id)
    if row is None:
        raise ValueError(f"Fine {fine_id} does not exist")
    return {"fine_status": row["status"], "wallet": round(row["wallet"], 2),
            "treasury": round(treasury, 2), "amount": row["amount"], "owner_id": row["owner_id"]}


def pay_fine(conn: sqlite3.Connection, fine_id: int, fail_at: str | None = None) -> dict:
    """Pay one fine atomically.

    fail_at: None | 'after_debit' | 'after_credit' | 'overdraw'
    """
    before = snapshot(conn, fine_id)
    steps = []
    error = None
    conn.execute("BEGIN IMMEDIATE")
    steps.append("BEGIN IMMEDIATE - take the write lock")
    try:
        row, _ = _state(conn, fine_id)
        if row["status"] != "UNPAID":
            raise ValueError(f"Fine {fine_id} is already {row['status']}")
        amount = row["amount"]
        debit = amount + row["wallet"] if fail_at == "overdraw" else amount
        conn.execute("UPDATE wallet SET balance = balance - ? WHERE owner_id = ?", (debit, row["owner_id"]))
        steps.append(f"UPDATE wallet: debit {debit:.2f}")
        if fail_at == "after_debit":
            raise SimulatedFailure("Power cut after the debit")
        conn.execute("UPDATE treasury SET balance = balance + ? WHERE id = 1", (amount,))
        steps.append(f"UPDATE treasury: credit {amount:.2f}")
        if fail_at == "after_credit":
            raise SimulatedFailure("Power cut after the credit")
        conn.execute("UPDATE fine SET status = 'PAID' WHERE fine_id = ?", (fine_id,))
        steps.append("UPDATE fine: status = PAID (audit trigger fires)")
        conn.execute("COMMIT")
        steps.append("COMMIT - all three changes become durable together")
        ok = True
    except Exception as exc:
        conn.execute("ROLLBACK")
        steps.append("ROLLBACK - every change above is undone")
        error = f"{type(exc).__name__}: {exc}"
        ok = False
    after = snapshot(conn, fine_id)
    return {"ok": ok, "error": error, "steps": steps, "before": before, "after": after,
            "invariant_holds": abs((before["wallet"] + before["treasury"]) - (after["wallet"] + after["treasury"])) < 0.01}


def pay_all(conn: sqlite3.Connection, owner_id: int) -> dict:
    """Pay all unpaid fines of an owner in ONE transaction using a SAVEPOINT per fine.

    A fine the wallet cannot afford rolls back to its savepoint only; the rest commit.
    """
    fines = conn.execute(
        """SELECT f.fine_id, f.amount FROM fine f JOIN vehicle v ON v.plate = f.plate
           WHERE v.owner_id = ? AND f.status = 'UNPAID' ORDER BY f.amount DESC""", (owner_id,)).fetchall()
    results = []
    conn.execute("BEGIN IMMEDIATE")
    try:
        for k, f in enumerate(fines):
            sp = f"sp{k}"
            conn.execute(f"SAVEPOINT {sp}")
            try:
                conn.execute("UPDATE wallet SET balance = balance - ? WHERE owner_id = ?", (f["amount"], owner_id))
                conn.execute("UPDATE treasury SET balance = balance + ? WHERE id = 1", (f["amount"],))
                conn.execute("UPDATE fine SET status = 'PAID' WHERE fine_id = ?", (f["fine_id"],))
                conn.execute(f"RELEASE {sp}")
                results.append({"fine_id": f["fine_id"], "amount": f["amount"], "paid": True})
            except sqlite3.IntegrityError as exc:
                conn.execute(f"ROLLBACK TO {sp}")
                conn.execute(f"RELEASE {sp}")
                results.append({"fine_id": f["fine_id"], "amount": f["amount"], "paid": False, "reason": str(exc)})
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    bal = conn.execute("SELECT balance FROM wallet WHERE owner_id = ?", (owner_id,)).fetchone()[0]
    return {"owner_id": owner_id, "results": results, "wallet_after": round(bal, 2)}


def issue_test_violation(conn: sqlite3.Connection, plate: str | None = None, code: str = "RED_LIGHT") -> dict:
    """Insert one violation and show what the trigger produced (DBMS module 5 demo)."""
    if plate is None:
        plate = conn.execute("SELECT plate FROM vehicle ORDER BY RANDOM() LIMIT 1").fetchone()[0]
    prior = conn.execute("SELECT COUNT(*) FROM violation WHERE plate = ?", (plate,)).fetchone()[0]
    conn.execute("BEGIN")
    cur = conn.execute("INSERT INTO violation(plate, node_id, code, tick) VALUES (?, '1-1', ?, "
                       "COALESCE((SELECT MAX(tick) FROM sensor_reading), 0))", (plate, code))
    vid = cur.lastrowid
    conn.execute("COMMIT")
    fine = conn.execute("SELECT * FROM fine WHERE violation_id = ?", (vid,)).fetchone()
    base = conn.execute("SELECT base_fine FROM violation_type WHERE code = ?", (code,)).fetchone()[0]
    return {"plate": plate, "code": code, "prior_violations": prior, "base_fine": base,
            "violation_id": vid, "fine": dict(fine) if fine else None}
