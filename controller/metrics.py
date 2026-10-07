"""Append-only CSV measurement logging."""
import csv
import os
import threading
import time

_lock = threading.Lock()
EVENT_FIELDS = ["timestamp_epoch", "event_type", "dpid/link", "detail"]
RECOVERY_FIELDS = ["t_detect", "t_compute_done", "t_rules_sent", "t_barrier", "controller_reaction_ms", "rule_install_ms"]


def _append(path, fields, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _lock:
        fresh = not os.path.exists(path) or os.path.getsize(path) == 0
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            if fresh:
                writer.writeheader()
            writer.writerow(row)


def event(path, event_type, identity="", detail=""):
    _append(path, EVENT_FIELDS, {"timestamp_epoch": time.time(), "event_type": event_type,
                                 "dpid/link": identity, "detail": detail})


def recovery(path, t_detect, t_compute_done, t_rules_sent, t_barrier):
    _append(path, RECOVERY_FIELDS, {"t_detect": t_detect, "t_compute_done": t_compute_done,
                                   "t_rules_sent": t_rules_sent, "t_barrier": t_barrier,
                                   "controller_reaction_ms": (t_rules_sent-t_detect)*1000,
                                   "rule_install_ms": (t_barrier-t_rules_sent)*1000})
