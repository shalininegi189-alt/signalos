"""Dehradun layout for SignalOS.

The simulation runs on a 4 x 4 grid of junctions. Each grid cell is given the name of a real Dehradun
chowk, laid out roughly by compass direction (north = top). THIS IS A SCHEMATIC: the names are real, the
positions are only approximate and distances are not to scale. Before using the model for any real
decision, check the layout against a survey or OpenStreetMap and calibrate demand with field counts
(see the "Upload field counts" feature).
"""
from __future__ import annotations

from urllib.parse import quote_plus

CITY = "Dehradun"

# node id "row-col" -> (name, short label, zone, note)
PLACES = {
    "0-0": ("Kaulagarh Chowk", "Kaulagarh", "residential", "Residential and office belt in the north-west; school-run traffic in the morning."),
    "0-1": ("Dalanwala Chowk", "Dalanwala", "school", "Cluster of schools and colleges; sharp peaks at school start and end."),
    "0-2": ("Dilaram Chowk", "Dilaram", "tourist", "On the Rajpur Road corridor towards Mussoorie."),
    "0-3": ("Rajpur / Mussoorie Road", "Rajpur", "gateway", "Northern gateway: weekend tourist surge towards Mussoorie."),
    "1-0": ("Ballupur Chowk", "Ballupur", "school", "Chakrata Road: schools, colleges and commuters from the west."),
    "1-1": ("Parade Ground", "Parade Gr.", "commercial", "Events, markets and offices next to the city centre."),
    "1-2": ("Clock Tower (Ghantaghar)", "Clock Tower", "hub", "City centre. The ISBT to Clock Tower route is a known congestion corridor."),
    "1-3": ("Jakhan / Raipur Road", "Jakhan", "residential", "Eastern residential belt along Raipur Road."),
    "2-0": ("Prem Nagar", "Prem Nagar", "gateway", "Western gateway on Chakrata Road (Vikasnagar side)."),
    "2-1": ("ISBT Chowk", "ISBT", "hub", "Inter-State Bus Terminal: buses and pick-up traffic; part of the ISBT to Clock Tower corridor."),
    "2-2": ("Railway Station / Gandhi Road", "Rly Station", "hub", "Railway station traffic, autos and taxis."),
    "2-3": ("Sahastradhara Road / IT Park", "Sahastradhara", "gateway", "Eastern gateway: IT Park commuters and Sahastradhara visitors."),
    "3-0": ("Saharanpur Chowk", "Saharanpur", "gateway", "South-western gateway for traffic from Delhi / Saharanpur side."),
    "3-1": ("Race Course Chowk", "Race Course", "commercial", "Commercial and residential area in the south."),
    "3-2": ("Rispana / Araghar (Haridwar Road)", "Rispana", "commercial", "Rispana to Araghar Chowk stretch on Haridwar Road is a known bottleneck."),
    "3-3": ("Haridwar Bypass", "Haridwar Byp.", "gateway", "Southern gateway for Haridwar / Rishikesh traffic."),
}

# Streets between neighbouring junctions (order does not matter). Anything else is "link road".
_STREETS = {
    ("1-0", "1-1"): "Chakrata Road", ("1-0", "2-0"): "Chakrata Road", ("0-0", "1-0"): "Chakrata Road",
    ("1-2", "0-2"): "Rajpur Road", ("0-2", "0-3"): "Rajpur Road (to Mussoorie)",
    ("1-2", "1-3"): "Raipur Road", ("1-3", "2-3"): "Sahastradhara Road",
    ("1-2", "2-2"): "Gandhi Road", ("2-2", "3-2"): "Haridwar Road", ("3-2", "3-3"): "Haridwar Bypass",
    ("2-1", "2-2"): "Station Road (ISBT-Clock Tower route)", ("1-1", "1-2"): "Paltan Bazaar side",
    ("3-0", "3-1"): "Saharanpur Road", ("3-1", "2-1"): "Saharanpur Road", ("3-1", "3-2"): "Race Course Road",
    ("0-1", "0-2"): "Subhash Road", ("0-0", "0-1"): "Kaulagarh Road", ("0-1", "1-1"): "Subhash Road",
}
STREETS = {frozenset(k): v for k, v in _STREETS.items()}

GRID_ROWS, GRID_COLS = 4, 4


def is_border(node: str) -> bool:
    r, c = map(int, node.split("-"))
    return r in (0, GRID_ROWS - 1) or c in (0, GRID_COLS - 1)


BORDER = [n for n in PLACES if is_border(n)]

# Relative importance of each entry point on a normal day (normalised so the mean over entries is 1).
_RAW_WEIGHT = {"3-3": 1.5, "3-0": 1.4, "2-0": 1.1, "0-3": 1.0, "2-3": 1.0, "1-0": 1.0,
               "1-3": 0.8, "0-0": 0.8, "0-1": 0.8, "0-2": 0.8, "3-1": 0.8, "3-2": 0.9}
_mean = sum(_RAW_WEIGHT.get(n, 1.0) for n in BORDER) / len(BORDER)
BASE_WEIGHT = {n: _RAW_WEIGHT.get(n, 1.0) / _mean for n in BORDER}

SCENARIOS = {
    "normal": {"label": "Normal day", "factor": 1.0, "mult": {},
               "desc": "Entry demand follows the usual weights: Haridwar Bypass and Saharanpur Chowk are the busiest gateways."},
    "school": {"label": "School time", "factor": 1.0,
               "mult": {"0-0": 2.4, "0-1": 2.6, "0-2": 1.6, "1-0": 2.4, "2-0": 1.6},
               "desc": "Morning school run: the Kaulagarh, Dalanwala and Ballupur side of the city floods."},
    "evening": {"label": "Evening peak", "factor": 1.5, "mult": {"3-0": 1.2, "3-3": 1.2},
                "desc": "Office hours end: demand is 1.5x everywhere, higher on the Delhi and Haridwar sides."},
    "mussoorie": {"label": "Mussoorie weekend rush", "factor": 1.0,
                  "mult": {"0-3": 3.5, "0-2": 1.6, "3-0": 1.8, "3-3": 1.8},
                  "desc": "Tourists from Delhi / Haridwar heading up Rajpur Road. Reported as a recurring weekend problem."},
    "haridwar": {"label": "Haridwar / festival surge", "factor": 1.0, "mult": {"3-3": 3.0, "3-2": 1.8},
                 "desc": "Heavy traffic from the Haridwar side pushes through Rispana and Araghar."},
}


def place(node: str) -> dict:
    name, short, zone, note = PLACES[node]
    return {"id": node, "name": name, "short": short, "zone": zone, "note": note,
            "gate": node in BASE_WEIGHT, "maps": maps_url(name)}


def name(node: str) -> str:
    return PLACES[node][0] if node in PLACES else node


def street(a: str, b: str) -> str:
    return STREETS.get(frozenset((a, b)), "link road")


def road_name(edge_id: str) -> str:
    """'1-0>1-1' -> 'Ballupur Chowk to Parade Ground (Chakrata Road)'"""
    if ">" not in edge_id:
        return edge_id
    a, b = edge_id.split(">")
    return f"{name(a)} to {name(b)} ({street(a, b)})"


def maps_url(place_name: str) -> str:
    """Search link, so no coordinates have to be stored (and none can be wrong)."""
    return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(f"{place_name} {CITY}")


def scenario_multiplier(scenario: str, node: str) -> float:
    s = SCENARIOS.get(scenario, SCENARIOS["normal"])
    return s["factor"] * s["mult"].get(node, 1.0)
