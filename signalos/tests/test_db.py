"""DBMS modules: schema, triggers, ACID, concurrency control, indexing, recovery."""
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import config
from db import concurrency, database, optimizer, recovery, transactions
from engine.simulation import CitySimulation


class TempDB(unittest.TestCase):
    """Each test gets a fresh, seeded database in a temp folder."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="signalos-test-"))
        self.conn = database.connect(self.dir / "t.db")
        database.init_schema(self.conn)
        database.seed_static(self.conn, CitySimulation(seed=1).topology())

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def violation(self, plate="SO001", code="RED_LIGHT"):
        self.conn.execute("INSERT INTO violation(plate, node_id, code, tick) VALUES (?, '1-1', ?, 1)", (plate, code))
        return self.conn.execute("SELECT * FROM fine ORDER BY fine_id DESC LIMIT 1").fetchone()

    def balance(self, owner):
        return self.conn.execute("SELECT balance FROM wallet WHERE owner_id=?", (owner,)).fetchone()[0]

    def treasury(self):
        return self.conn.execute("SELECT balance FROM treasury").fetchone()[0]


class SchemaTests(TempDB):
    def test_schema_objects_exist(self):
        kinds = {}
        for r in self.conn.execute("SELECT type, COUNT(*) n FROM sqlite_master GROUP BY type"):
            kinds[r["type"]] = r["n"]
        self.assertGreaterEqual(kinds["table"], 14)
        self.assertGreaterEqual(kinds["trigger"], 3)
        self.assertGreaterEqual(kinds["view"], 4)

    def test_foreign_keys_are_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO vehicle VALUES ('GHOST', 9999, 'car')")

    def test_check_constraint_blocks_negative_balance(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE wallet SET balance = -1 WHERE owner_id = 1")


class TriggerTests(TempDB):
    def test_violation_creates_a_fine_at_base_rate(self):
        fine = self.violation()
        self.assertEqual(fine["amount"], 500.0)
        self.assertEqual(fine["status"], "UNPAID")

    def test_repeat_offenders_pay_more_up_to_double(self):
        amounts = [self.violation()["amount"] for _ in range(7)]
        self.assertEqual(amounts[:3], [500.0, 625.0, 750.0])
        self.assertEqual(max(amounts), 1000.0)              # capped at +100%

    def test_paid_fine_cannot_be_reopened(self):
        fine = self.violation()
        self.conn.execute("UPDATE fine SET status='PAID' WHERE fine_id=?", (fine["fine_id"],))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE fine SET status='UNPAID' WHERE fine_id=?", (fine["fine_id"],))

    def test_status_change_is_audited(self):
        fine = self.violation()
        self.conn.execute("UPDATE fine SET status='PAID' WHERE fine_id=?", (fine["fine_id"],))
        row = self.conn.execute("SELECT * FROM audit_log WHERE table_name='fine'").fetchone()
        self.assertEqual((row["old_value"], row["new_value"]), ("UNPAID", "PAID"))


class TransactionTests(TempDB):
    def setUp(self):
        super().setUp()
        self.fine = self.violation("SO001")
        self.owner = self.conn.execute("SELECT owner_id FROM vehicle WHERE plate='SO001'").fetchone()[0]
        self.conn.execute("UPDATE wallet SET balance = 2000 WHERE owner_id=?", (self.owner,))

    def total(self):
        return self.balance(self.owner) + self.treasury()

    def test_commit_moves_money_and_marks_paid(self):
        before = self.total()
        r = transactions.pay_fine(self.conn, self.fine["fine_id"])
        self.assertTrue(r["ok"])
        self.assertEqual(self.balance(self.owner), 1500.0)
        self.assertEqual(self.treasury(), 500.0)
        self.assertEqual(self.total(), before)

    def test_failure_at_every_step_rolls_everything_back(self):
        for mode in ("after_debit", "after_credit", "overdraw"):
            r = transactions.pay_fine(self.conn, self.fine["fine_id"], mode)
            self.assertFalse(r["ok"], mode)
            self.assertTrue(r["invariant_holds"], mode)
            self.assertEqual(self.balance(self.owner), 2000.0, mode)     # atomicity
            self.assertEqual(self.treasury(), 0.0, mode)
            status = self.conn.execute("SELECT status FROM fine WHERE fine_id=?", (self.fine["fine_id"],)).fetchone()[0]
            self.assertEqual(status, "UNPAID", mode)

    def test_cannot_pay_twice(self):
        transactions.pay_fine(self.conn, self.fine["fine_id"])
        again = transactions.pay_fine(self.conn, self.fine["fine_id"])
        self.assertFalse(again["ok"])
        self.assertIn("already PAID", again["error"])
        self.assertEqual(self.balance(self.owner), 1500.0)        # charged exactly once
        self.assertEqual(self.treasury(), 500.0)

    def test_unknown_fine_is_rejected(self):
        with self.assertRaises(ValueError):
            transactions.pay_fine(self.conn, 999999)

    def test_savepoints_pay_what_the_wallet_can_afford(self):
        for _ in range(5):                                   # five more fines of 500+
            self.violation("SO001")
        self.conn.execute("UPDATE wallet SET balance = 1300 WHERE owner_id=?", (self.owner,))
        r = transactions.pay_all(self.conn, self.owner)
        paid = [x for x in r["results"] if x["paid"]]
        failed = [x for x in r["results"] if not x["paid"]]
        self.assertTrue(paid and failed)                     # partial success
        self.assertGreaterEqual(r["wallet_after"], 0)
        self.assertAlmostEqual(self.treasury(), sum(x["amount"] for x in paid))


class ConcurrencyTests(unittest.TestCase):
    def test_serializable_schedule(self):
        r = concurrency.analyse("R1(A) W1(A) R2(A) W2(A) C1 C2")["serializability"]
        self.assertTrue(r["serializable"])
        self.assertEqual(r["serial_order"], [1, 2])

    def test_lost_update_schedule_has_a_cycle(self):
        r = concurrency.analyse("R1(A) R2(A) W1(A) W2(A) C1 C2")["serializability"]
        self.assertFalse(r["serializable"])
        self.assertTrue(r["cycles"])

    def test_two_phase_locking_detects_deadlock_and_aborts_youngest(self):
        r = concurrency.analyse("W1(A) W2(B) W1(B) W2(A) C1 C2")["locking"]
        self.assertEqual(len(r["deadlocks"]), 1)
        self.assertEqual(r["aborted"], [2])

    def test_bad_schedule_text_is_rejected(self):
        with self.assertRaises(ValueError):
            concurrency.analyse("hello world")

    def test_lost_update_experiment(self):
        old = config.DATA_DIR
        config.DATA_DIR = Path(tempfile.mkdtemp(prefix="signalos-lu-"))
        try:
            r = concurrency.lost_update_demo(threads=4, iterations=25)
        finally:
            shutil.rmtree(config.DATA_DIR, ignore_errors=True)
            config.DATA_DIR = old
        self.assertEqual(r["transactional"]["lost"], 0)      # isolation works
        self.assertGreater(r["unsafe"]["lost"], 0)           # no isolation loses updates


class OptimizerTests(unittest.TestCase):
    def test_index_changes_the_plan_and_speeds_up_the_query(self):
        b = optimizer.benchmark()
        scan, composite, covering = b["stages"]
        self.assertIn("SCAN", scan["plan"][0])
        self.assertIn("USING INDEX", composite["plan"][0])
        self.assertIn("COVERING INDEX", covering["plan"][0])
        self.assertGreater(composite["speedup"], 3)
        self.assertGreaterEqual(covering["speedup"], composite["speedup"] * 0.8)
        self.assertGreaterEqual(b["rows"], 100000)


class RecoveryTests(unittest.TestCase):
    def test_aries_style_recovery(self):
        r = recovery.run_scenario()
        self.assertTrue(r["correct"])
        self.assertEqual(r["final"], r["expected"])
        self.assertEqual(r["losers"], [2])
        self.assertEqual(r["winners"], [1, 3])
        self.assertNotEqual(r["disk_at_crash"], r["expected"])   # the crash really did leave a mess
        phases = [s["phase"] for s in r["steps"]]
        self.assertEqual(phases[0], "ANALYSIS")
        self.assertIn("REDO", phases)
        self.assertIn("UNDO", phases)


if __name__ == "__main__":
    unittest.main()
