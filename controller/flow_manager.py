"""Ryu-independent flow registry and path-diff helpers."""
import hashlib


def flow_id(src, dst):
    return "%s|%s" % (src, dst)


def flow_cookie(src, dst):
    # Reserve a stable 64-bit identifier derived from both IPs.
    return int.from_bytes(hashlib.blake2b(flow_id(src, dst).encode(), digest_size=8).digest(), "big")


class FlowManager:
    def __init__(self):
        self.flows = {}

    def learn(self, src, dst, path=None):
        key = flow_id(src, dst)
        item = self.flows.setdefault(key, {"src": src, "dst": dst, "current_path": None, "status": "pending"})
        if path is not None:
            item["current_path"] = list(path)
            item["status"] = "active"
        return item

    def set_path(self, key, path):
        item = self.flows[key]
        item["current_path"] = list(path) if path else None
        item["status"] = "active" if path else "unreachable"

    def affected_by_link(self, a, b):
        pair = {a, b}
        return [key for key, f in self.flows.items() if f.get("current_path") and
                any({x, y} == pair for x, y in zip(f["current_path"], f["current_path"][1:]))]

    @staticmethod
    def stale_switches(old_path, new_path):
        return set(old_path or ()) - set(new_path or ())

    @staticmethod
    def should_move(old_path, new_path, old_cost, new_cost):
        return new_path is not None and (old_path is None or new_cost < old_cost)

    @staticmethod
    def links(path):
        return {frozenset((a, b)) for a, b in zip(path or (), (path or ())[1:])}
