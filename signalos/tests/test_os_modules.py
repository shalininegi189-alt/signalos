"""OS modules: synchronisation, deadlock, memory management and scheduling."""
import threading
import time
import unittest

from engine import deadlock, memory
from engine.scheduler import POLICIES
from engine.simulation import run_headless
from engine.sync import BoundedBuffer, CountingSemaphore, Mutex, RWLock


class SemaphoreTests(unittest.TestCase):
    def test_counting_semaphore_blocks_when_exhausted(self):
        s = CountingSemaphore("road", 2)
        self.assertTrue(s.try_acquire("a"))
        self.assertTrue(s.try_acquire("b"))
        self.assertFalse(s.try_acquire("c"))           # would block
        self.assertEqual(list(s.waiters), ["c"])
        self.assertTrue(s.release("a"))                # V wakes a slot up
        self.assertTrue(s.try_acquire("c"))
        self.assertEqual(list(s.waiters), [])

    def test_release_by_non_holder_is_ignored(self):
        s = CountingSemaphore("road", 1)
        self.assertFalse(s.release("ghost"))
        self.assertEqual(s.value, 1)

    def test_mutex_has_single_owner(self):
        m = Mutex("box")
        self.assertTrue(m.try_acquire("lane N"))
        self.assertFalse(m.try_acquire("lane E"))
        self.assertEqual(m.owner, "lane N")
        m.release("lane N")
        self.assertIsNone(m.owner)


class BufferAndLockTests(unittest.TestCase):
    def test_producer_consumer_loses_nothing(self):
        buf = BoundedBuffer(5)
        produced, consumed = list(range(200)), []

        def producer():
            for x in produced:
                buf.put(x)

        def consumer():
            while len(consumed) < len(produced):
                item = buf.get(timeout=2)
                if item is not None:
                    consumed.append(item)

        ts = [threading.Thread(target=producer), threading.Thread(target=consumer)]
        [t.start() for t in ts]
        [t.join(10) for t in ts]
        self.assertEqual(consumed, produced)           # FIFO and complete
        self.assertLessEqual(len(buf), 5)

    def test_put_nowait_refuses_when_full(self):
        buf = BoundedBuffer(2)
        self.assertTrue(buf.put_nowait(1))
        self.assertTrue(buf.put_nowait(2))
        self.assertFalse(buf.put_nowait(3))

    def test_rwlock_readers_share_writer_is_exclusive(self):
        lock = RWLock()
        inside, peak_readers, violations = {"r": 0, "w": 0}, [0], []
        guard = threading.Lock()

        def reader():
            with lock.read():
                with guard:
                    inside["r"] += 1
                    peak_readers[0] = max(peak_readers[0], inside["r"])
                    if inside["w"]:
                        violations.append("reader inside with writer")
                time.sleep(0.03)
                with guard:
                    inside["r"] -= 1

        def writer():
            with lock.write():
                with guard:
                    inside["w"] += 1
                    if inside["r"] or inside["w"] > 1:
                        violations.append("writer not alone")
                time.sleep(0.02)
                with guard:
                    inside["w"] -= 1

        ts = [threading.Thread(target=reader) for _ in range(5)] + [threading.Thread(target=writer) for _ in range(2)]
        [t.start() for t in ts]
        [t.join(10) for t in ts]
        self.assertEqual(violations, [])
        self.assertGreater(peak_readers[0], 1)         # readers really did overlap


class DeadlockTests(unittest.TestCase):
    def test_find_cycles(self):
        self.assertEqual(len(deadlock.find_cycles({"a": ["b"], "b": ["c"], "c": ["a"]})), 1)
        self.assertEqual(deadlock.find_cycles({"a": ["b"], "b": ["c"]}), [])

    def test_functional_cycles_ring_of_four(self):
        cyc = deadlock.functional_cycles({"r1": "r2", "r2": "r3", "r3": "r4", "r4": "r1", "x": "r1"})
        self.assertEqual(len(cyc), 1)
        self.assertEqual(set(cyc[0]), {"r1", "r2", "r3", "r4"})   # the tail "x" is not part of the cycle

    # Textbook example (Silberschatz): 5 processes, 3 resource types.
    AV = [3, 3, 2]
    MX = [[7, 5, 3], [3, 2, 2], [9, 0, 2], [2, 2, 2], [4, 3, 3]]
    AL = [[0, 1, 0], [2, 0, 0], [3, 0, 2], [2, 1, 1], [0, 0, 2]]

    def test_bankers_safe_state_textbook(self):
        r = deadlock.bankers_safety(self.AV, self.MX, self.AL)
        self.assertTrue(r["safe"])
        self.assertEqual(sorted(r["sequence"]), [0, 1, 2, 3, 4])
        self.assertEqual(r["need"][1], [1, 2, 2])

    def test_bankers_request_granted_then_denied(self):
        ok = deadlock.bankers_request(self.AV, self.MX, self.AL, 1, [1, 0, 2])
        self.assertTrue(ok["granted"])
        # P4 asking for (3,3,0) exceeds what is available
        bad = deadlock.bankers_request(self.AV, self.MX, self.AL, 4, [3, 3, 0])
        self.assertFalse(bad["granted"])

    def test_road_preset_is_unsafe(self):
        p = deadlock.road_deadlock_demo()
        self.assertFalse(deadlock.bankers_safety(p["available"], p["maximum"], p["allocation"])["safe"])


class MemoryTests(unittest.TestCase):
    REFS = [7, 0, 1, 2, 0, 3, 0, 4, 2, 3, 0, 3, 2, 1, 2, 0, 1, 7, 0, 1]

    def test_textbook_fault_counts(self):
        self.assertEqual(memory.simulate(self.REFS, 3, "FIFO")["faults"], 15)
        self.assertEqual(memory.simulate(self.REFS, 3, "LRU")["faults"], 12)
        self.assertEqual(memory.simulate(self.REFS, 3, "OPT")["faults"], 9)

    def test_opt_is_a_lower_bound(self):
        res = memory.compare(self.REFS, 3)
        for p in memory.POLICIES:
            self.assertGreaterEqual(res[p]["faults"], res["OPT"]["faults"], p)

    def test_beladys_anomaly_for_fifo(self):
        b = memory.belady_demo()
        self.assertEqual(b["frames3"]["faults"], 9)
        self.assertEqual(b["frames4"]["faults"], 10)        # more frames, more faults

    def test_lru_never_suffers_beladys_anomaly(self):
        refs = memory.belady_demo()["refs"]
        faults = [memory.simulate(refs, f, "LRU")["faults"] for f in range(1, 8)]
        self.assertEqual(faults, sorted(faults, reverse=True))

    def test_frames_never_overflow(self):
        for p in memory.POLICIES:
            for step in memory.simulate(self.REFS, 3, p)["steps"]:
                self.assertLessEqual(len([f for f in step["frames"] if f is not None]), 3)


class SchedulingTests(unittest.TestCase):
    def test_every_policy_makes_progress_and_is_deterministic(self):
        for p in POLICIES:
            a = run_headless(p, ticks=250, demand=1.5)
            b = run_headless(p, ticks=250, demand=1.5)
            self.assertGreater(a["exited"], 0, p)
            self.assertEqual({k: a[k] for k in ("exited", "avg_wait", "max_wait")},
                             {k: b[k] for k in ("exited", "avg_wait", "max_wait")}, p)

    @staticmethod
    def _amb_wait(policy, emergency):
        runs = [run_headless(policy, ticks=600, demand=2.0, emergency=emergency, seed=s) for s in (11, 12, 13)]
        waits = [r["amb_avg_wait"] for r in runs if r["amb_avg_wait"] is not None]
        return sum(waits) / len(waits)

    def test_emergency_preemption_lowers_ambulance_wait(self):
        for policy in ("RR", "SRTF"):
            self.assertLess(self._amb_wait(policy, True), self._amb_wait(policy, False), policy)

    def test_priority_policy_favours_ambulances_even_without_preemption(self):
        pri = self._amb_wait("PRIORITY", False)
        self.assertLess(pri, self._amb_wait("RR", False))
        self.assertLess(pri, self._amb_wait("SRTF", False))

    def test_context_switches_are_counted(self):
        self.assertGreater(run_headless("RR", ticks=200, demand=1.5)["context_switches"], 0)


if __name__ == "__main__":
    unittest.main()
