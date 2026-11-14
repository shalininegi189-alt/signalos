"""Global configuration for SignalOS."""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "signalos.db"
BENCH_DB_PATH = DATA_DIR / "bench.db"

# --- City layout -----------------------------------------------------------
GRID_ROWS = 4
GRID_COLS = 4
EDGE_LEN = 4          # ticks a car needs to drive down one road
EDGE_CAP = 4          # cars a road segment can hold (counting semaphore)
AMBULANCE_LEN = 2     # ambulances drive faster

# --- Simulation ------------------------------------------------------------
TICK_SECONDS = 0.30   # real seconds per tick at speed 1x
DEFAULT_DEMAND = 1.6
BASE_SPAWN_RATE = 0.07        # per border node per tick, multiplied by demand
BUFFER_CAPACITY = 30          # producer/consumer entry buffer
BACKLOG_PATIENCE = 30         # ticks a car waits to enter before turning away
DETECT_EVERY = 6              # deadlock detector period (ticks)
COMMUTERS = 60                # registered vehicles (the "pages" of the garage)
PARKING_FRAMES = 12           # parking bays = physical frames
SENSOR_EVERY = 5

DEFAULTS = {
    "policy": "RR",
    "quantum": 4,
    "emergency": True,
    "deadlock_mode": "detect",
    "parking_policy": "LRU",
    "demand": DEFAULT_DEMAND,
    "scenario": "normal",
    "speed": 1.0,
    "paused": False,
}
