#!/usr/bin/env python3
"""Self-test that needs NO Mininet or root.

A small UDP relay sits between client and server and behaves like a dead link:
it drops everything (both directions) for a window, plus some random loss.
The test passes if the client/server still deliver the file byte-for-byte and
the analyzer detects the outage and reports a sensible recovery time.

    python3 test_outage.py
"""
import os
import random
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import analyze  # noqa: E402

OUTAGE_START, OUTAGE_LEN, RANDOM_LOSS = 1.0, 1.0, 0.05


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Relay(threading.Thread):
    def __init__(self, listen_port, server_port):
        super().__init__(daemon=True)
        self.ls = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.ls.bind(("127.0.0.1", listen_port))
        self.us = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.server = ("127.0.0.1", server_port)
        self.client = None
        self.t0 = None
        self.stop = False
        self.rng = random.Random(7)

    def dropped(self):
        t = time.time() - self.t0
        return (OUTAGE_START <= t < OUTAGE_START + OUTAGE_LEN) or self.rng.random() < RANDOM_LOSS

    def run(self):
        while not self.stop:
            r, _, _ = select.select([self.ls, self.us], [], [], 0.1)
            for s in r:
                data, addr = s.recvfrom(65535)
                if s is self.ls:
                    self.client = addr
                    if self.t0 is None:
                        self.t0 = time.time()
                    if not self.dropped():
                        self.us.sendto(data, self.server)
                elif self.client and not self.dropped():
                    self.ls.sendto(data, self.client)


def main():
    tmp = tempfile.mkdtemp(prefix="udp_recovery_test_")
    src, dst = os.path.join(tmp, "src.bin"), os.path.join(tmp, "dst.bin")
    with open(src, "wb") as f:
        f.write(os.urandom(300 * 1000))          # 300 packets @ 1000 B
    clog, slog = os.path.join(tmp, "client.csv"), os.path.join(tmp, "server.csv")

    sport, rport = free_port(), free_port()
    server = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py"),
                               "--bind", "127.0.0.1", "--port", str(sport),
                               "--out", dst, "--log", slog, "--linger", "1"])
    time.sleep(0.4)
    relay = Relay(rport, sport)
    relay.start()
    client = subprocess.run([sys.executable, os.path.join(HERE, "client.py"),
                             "--server", "127.0.0.1", "--port", str(rport),
                             "--file", src, "--rate", "100", "--window", "32",
                             "--log", clog, "--quiet"], timeout=60)
    server.wait(timeout=15)
    relay.stop = True

    same = open(src, "rb").read() == open(dst, "rb").read()
    res = analyze.analyze(clog, slog, gap_threshold=0.5)
    analyze.report(res)

    checks = {
        "client exit code 0 (integrity confirmed)": client.returncode == 0,
        "received file identical to sent file": same,
        "no residual loss": res["residual_loss_packets"] == 0,
        "retransmissions happened": res["retransmissions"] > 0,
        "outage detected": len(res["outages"]) >= 1,
        "recovery time plausible (0.8 - 2.5 s)":
            bool(res["outages"]) and 0.8 <= res["outages"][0]["recovery_time_s"] <= 2.5,
    }
    print()
    for name, ok in checks.items():
        print(("PASS  " if ok else "FAIL  ") + name)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
