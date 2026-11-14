"""Dehradun layer: real locality data, demand scenarios, field-count import, signal-plan export."""
import csv
import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app as webapp
import places
import planner
from db import database
from engine.simulation import CitySimulation


class PlacesTests(unittest.TestCase):
    def test_every_grid_cell_has_a_unique_real_name(self):
        ids = {f"{r}-{c}" for r in range(4) for c in range(4)}
        self.assertEqual(set(places.PLACES), ids)
        names = [p[0] for p in places.PLACES.values()]
        self.assertEqual(len(names), len(set(names)))

    def test_known_landmarks_are_present(self):
        names = " | ".join(p[0] for p in places.PLACES.values())
        for must in ("Clock Tower", "ISBT", "Ballupur", "Dilaram", "Rispana", "Haridwar Bypass", "Rajpur", "Saharanpur"):
            self.assertIn(must, names)

    def test_entry_weights_cover_the_border_and_average_one(self):
        self.assertEqual(set(places.BASE_WEIGHT), set(places.BORDER))
        self.assertEqual(len(places.BORDER), 12)
        self.assertAlmostEqual(sum(places.BASE_WEIGHT.values()) / 12, 1.0)

    def test_road_names_and_map_links(self):
        self.assertIn("Chakrata Road", places.road_name("1-0>1-1"))
        self.assertEqual(places.street("1-1", "1-0"), places.street("1-0", "1-1"))   # order-independent
        self.assertIn("Clock+Tower", places.maps_url("Clock Tower (Ghantaghar)"))

    def test_topology_carries_names_to_the_front_end(self):
        t = CitySimulation().topology()
        self.assertTrue(all(n["name"] and n["zone"] for n in t["nodes"]))
        self.assertTrue(all(r["label"] and r["street"] for r in t["roads"]))


class ScenarioTests(unittest.TestCase):
    def share_from(self, scenario, node="0-3", ticks=700):
        trips = []
        sim = CitySimulation(seed=8, params={"demand": 1.6, "scenario": scenario},
                             emit=lambda k, d: trips.append(d) if k == "trip" else None)
        for _ in range(ticks):
            sim.step()
        origins = [t["origin"] for t in trips]
        return origins.count(node) / max(1, len(origins))

    def test_mussoorie_rush_floods_the_rajpur_gate(self):
        self.assertGreater(self.share_from("mussoorie"), 1.8 * self.share_from("normal"))

    def test_school_time_loads_the_school_side(self):
        self.assertGreater(self.share_from("school", "0-1"), 1.8 * self.share_from("normal", "0-1"))

    def test_scenario_multipliers(self):
        sim = CitySimulation(params={"scenario": "mussoorie"})
        base = places.BASE_WEIGHT["0-3"]
        self.assertAlmostEqual(sim.gate_weight("0-3"), base * 3.5)
        sim.set_params(scenario="evening")
        self.assertAlmostEqual(sim.gate_weight("0-0"), places.BASE_WEIGHT["0-0"] * 1.5)

    def test_uploaded_counts_replace_default_weights(self):
        sim = CitySimulation()
        sim.set_real_counts({"3-3": 3000, "0-3": 1000})
        self.assertAlmostEqual(sim.gate_weight("3-3") / sim.gate_weight("0-3"), 3.0)
        self.assertAlmostEqual(sim.gate_weight("3-0"), places.BASE_WEIGHT["3-0"])    # untouched entry
        sim.set_real_counts({})
        self.assertAlmostEqual(sim.gate_weight("3-3"), places.BASE_WEIGHT["3-3"])


class PlannerTests(unittest.TestCase):
    def test_greens_add_up_exactly_and_respect_minimum(self):
        for q in ({"N": 5, "E": 1, "S": 0, "W": 9}, {"N": 0, "E": 0, "S": 0, "W": 0}, {"N": 1, "E": 1, "S": 1, "W": 1}):
            g = planner.green_split(q, cycle=120)
            self.assertEqual(sum(g.values()), 120 - 4 * 3)
            self.assertTrue(all(v >= 10 for v in g.values()))

    def test_longer_queue_gets_more_green(self):
        g = planner.green_split({"N": 10, "E": 2, "S": 2, "W": 2})
        self.assertGreater(g["N"], max(g["E"], g["S"], g["W"]))

    def test_cycle_too_short_is_rejected(self):
        with self.assertRaises(ValueError):
            planner.green_split({}, cycle=40)


class MigrationTests(unittest.TestCase):
    def test_old_database_gets_names_and_new_views(self):
        d = Path(tempfile.mkdtemp())
        try:
            conn = database.connect(d / "old.db")
            database.init_schema(conn)
            topo = CitySimulation().topology()
            database.seed_static(conn, topo)
            for v in ("v_locality_load", "v_signal_plan_input"):          # make it look like the pre-Dehradun schema
                conn.execute(f"DROP VIEW {v}")
            conn.execute("ALTER TABLE intersection DROP COLUMN name")
            conn.execute("ALTER TABLE intersection DROP COLUMN zone")
            conn.execute("ALTER TABLE road DROP COLUMN street")
            database.migrate(conn, topo)
            name = conn.execute("SELECT name FROM intersection WHERE node_id='1-2'").fetchone()[0]
            self.assertIn("Clock Tower", name)
            self.assertIsNotNone(conn.execute("SELECT street FROM road LIMIT 1").fetchone()[0])
            conn.execute("SELECT * FROM v_locality_load").fetchall()      # views exist again
            conn.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)


class FakeRunner:
    def act(self, fn, *a, **k):
        return fn(*a, **k)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp())
        conn = database.connect(cls.dir / "t.db")
        database.init_schema(conn)
        sim = CitySimulation(seed=3)
        database.seed_static(conn, sim.topology())
        events = []
        live = CitySimulation(seed=3, emit=lambda k, d: events.append((k, d)))
        for _ in range(120):
            live.step()
        database.apply_events(conn, events)
        conn.close()
        cls.path = cls.dir / "t.db"
        cls.sim = CitySimulation(seed=3)
        cls.patches = [mock.patch.dict(webapp.STATE, {"sim": cls.sim, "runner": FakeRunner()}),
                       mock.patch.object(webapp, "db", lambda readonly=False: database.connect(cls.path, readonly))]
        [p.start() for p in cls.patches]
        cls.client = webapp.app.test_client()

    @classmethod
    def tearDownClass(cls):
        [p.stop() for p in cls.patches]
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_scenarios_listed_and_selectable(self):
        ids = [s["id"] for s in self.client.get("/api/scenarios").get_json()]
        self.assertIn("mussoorie", ids)
        r = self.client.post("/api/control", json={"scenario": "mussoorie"}).get_json()
        self.assertEqual(r["params"]["scenario"], "mussoorie")
        self.client.post("/api/control", json={"scenario": "not-a-scenario"})
        self.assertEqual(self.sim.params["scenario"], "mussoorie")          # invalid value ignored
        self.client.post("/api/control", json={"scenario": "normal"})

    def test_template_lists_every_entry_point(self):
        rows = list(csv.reader(io.StringIO(self.client.get("/api/real/template.csv").get_data(as_text=True))))
        self.assertEqual(rows[0], ["locality", "vehicles_per_hour"])
        self.assertEqual(len(rows) - 1, 12)

    def test_upload_counts_accepts_names_ids_and_reports_problems(self):
        body = "locality,vehicles_per_hour\nHaridwar Bypass,2400\nrajpur,800\nClock Tower (Ghantaghar),500\nNowhere,10\nPrem Nagar,abc\n"
        r = self.client.post("/api/real/counts", json={"csv": body}).get_json()
        self.assertTrue(r["ok"])
        self.assertEqual(set(r["applied"]), {"Haridwar Bypass", "Rajpur / Mussoorie Road"})
        self.assertEqual(len(r["ignored"]), 3)                              # interior, unknown, not-a-number
        self.assertAlmostEqual(self.sim.gate_weight("3-3") / self.sim.gate_weight("0-3"), 3.0)
        self.assertTrue(self.client.get("/api/real/counts").get_json()["active"])
        self.client.post("/api/real/counts", json={"clear": True})
        self.assertFalse(self.client.get("/api/real/counts").get_json()["active"])

    def test_upload_without_usable_rows_is_a_400(self):
        self.assertEqual(self.client.post("/api/real/counts", json={"csv": "hello\nworld"}).status_code, 400)

    def test_signal_plan_export(self):
        resp = self.client.get("/api/export/signal-plan.csv?cycle=120")
        self.assertIn("attachment", resp.headers["Content-Disposition"])
        rows = [r for r in csv.DictReader(io.StringIO(resp.get_data(as_text=True))) if r["node_id"] and r["cycle_s"]]
        self.assertEqual(len(rows), 16)
        for r in rows:
            greens = [int(r[f"green_{s}_s"]) for s in "NESW"]
            self.assertEqual(sum(greens), 108)
            self.assertTrue(r["locality"])
        self.assertEqual(self.client.get("/api/export/signal-plan.csv?cycle=abc").status_code, 400)


if __name__ == "__main__":
    unittest.main()
