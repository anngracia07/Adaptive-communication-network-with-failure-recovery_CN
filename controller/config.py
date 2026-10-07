"""Configuration defaults for the adaptive SDN controller."""
import os

CONTROLLER_PRIORITY = 100
TABLE_MISS_PRIORITY = 0
DEFAULT_LINK_COST = 1.0
FAILBACK_HOLD_DOWN_SECONDS = 5.0
FLOW_IDLE_TIMEOUT = 0
ENABLE_PORT_STATS = False
PORT_STATS_INTERVAL = 10.0
LOG_DIR = os.environ.get("ADAPTIVE_LOG_DIR", os.path.join(os.path.dirname(__file__), "logs"))
EVENTS_CSV = os.path.join(LOG_DIR, "events.csv")
RECOVERY_CSV = os.path.join(LOG_DIR, "recovery.csv")
