"""The live city: invariants that must hold on every tick, and the three gridlock strategies."""
import unittest

from engine.simulation import CitySimulation


def run(sim, n):
    for _ in range(n):
        sim.step()
    return sim


class InvariantTests(unittest.TestCase):
    def test_roads_never_exceed_capacity_and_semaphores_agree(self):
        sim = CitySimulation(seed=5, params={"demand": 3.0})
        for _ in range(300):
            sim.step()
            for e in sim.edges.values():
                self.assertLessEqual(len(e.q), e.cap, e.id)
                self.assertGreaterEqual(e.slots.value, 0, e.id)
                self.assertLessEqual(e.slots.value, e.cap, e.id)

    def test_every_car_is_on_exactly_one_road(self):
        sim = run(CitySimulation(seed=9, params={"demand": 2.5}), 200)
        ids = [v.id for e in sim.edges.values() for v in e.q]
        self.assertEqual(len(ids), len(set(ids)))

    def test_ticks_advance_and_cars_complete_trips(self):
        sim = run(CitySimulation(seed=3), 150)
        self.assertEqual(sim.tick, 150)
        self.assertGreater(sim.metrics()["exited"], 0)

    def test_same_seed_gives_the_same_city(self):
        a, b = (run(CitySimulation(seed=4, params={"demand": 2.0}), 200).metrics() for _ in range(2))
        self.assertEqual(a, b)

    def test_zero_demand_means_an_empty_city(self):
        sim = run(CitySimulation(seed=2, params={"demand": 0.0}), 100)
        self.assertEqual(sim.metrics()["spawned"], 0)


class GridlockTests(unittest.TestCase):
    def scenario(self, mode):
        sim = CitySimulation(seed=21, params={"deadlock_mode": mode, "demand": 0.8})
        run(sim, 25)
        sim.action("gridlock")
        run(sim, 90)
        return sim

    def test_ignoring_gridlock_leaves_roads_frozen(self):
        sim = self.scenario("off")
        self.assertGreaterEqual(sum(e.stuck for e in sim.edges.values()), 4)
        self.assertEqual(sim.metrics()["recovered"], 0)

    def test_detect_and_recover_breaks_the_cycle_by_towing(self):
        sim = self.scenario("detect")
        m = sim.metrics()
        self.assertGreaterEqual(m["deadlocks"], 1)
        self.assertGreaterEqual(m["recovered"], 1)
        self.assertGreaterEqual(m["towed"], 1)
        self.assertEqual(sum(e.stuck for e in sim.edges.values()), 0)

    def test_avoidance_prevents_the_cycle_without_towing(self):
        sim = self.scenario("avoid")
        m = sim.metrics()
        self.assertEqual(m["towed"], 0)
        self.assertEqual(sum(e.stuck for e in sim.edges.values()), 0)

    def test_closed_lane_blocks_then_reopens(self):
        sim = run(CitySimulation(seed=6, params={"demand": 1.5}), 40)
        sim.action("accident")
        blocked = [e for e in sim.edges.values() if e.blocked_until > sim.tick]
        self.assertEqual(len(blocked), 1)
        run(sim, 40)
        self.assertTrue(all(e.blocked_until <= sim.tick for e in sim.edges.values()))


if __name__ == "__main__":
    unittest.main()
