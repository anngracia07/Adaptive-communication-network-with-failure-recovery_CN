#!/usr/bin/env python3
"""End-to-end demo: reliable UDP app over the Ryu controller's Mininet topology.

Needs root, Mininet, Open vSwitch and the controller already running:

    Terminal 1 (repo root):  ryu-manager --observe-links controller/adaptive_controller.py
    Terminal 2 (repo root):  sudo python3 udp_app/run_mininet_demo.py

Timeline (defaults): the client streams 1500 packets at 50 pkt/s (~30 s).
At --fail-at the primary link s1-s2 is taken down, the controller reroutes via
s3-s5, and at --restore-at the link comes back (the controller fails back after
its 5 s hold-down). The client retransmits whatever was lost in between.
Results are written to udp_app/logs/ and summarised by analyze.py, including the
controller's detection / rule-install timings from controller/logs/events.csv.
"""
import argparse
import csv
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import analyze  # noqa: E402


def run_transfer(h1, h2, set_link, args):
    """Run server on h2 and client on h1; toggle the link while the client runs."""
    logdir = args.logdir
    os.makedirs(logdir, exist_ok=True)
    py = sys.executable or "python3"
    paths = {k: os.path.join(logdir, k) for k in
             ("server_log.csv", "client_log.csv", "markers.csv", "received.bin",
              "server_stdout.txt", "client_stdout.txt")}

    sout = open(paths["server_stdout.txt"], "w")
    cout = open(paths["client_stdout.txt"], "w")
    server = h2.popen([py, "-u", os.path.join(HERE, "server.py"), "--port", str(args.port),
                       "--out", paths["received.bin"], "--log", paths["server_log.csv"],
                       "--linger", "2"], stdout=sout, stderr=subprocess.STDOUT)
    time.sleep(1.0)
    client = h1.popen([py, "-u", os.path.join(HERE, "client.py"), "--server", h2.IP(),
                       "--port", str(args.port), "--count", str(args.count),
                       "--rate", str(args.rate), "--window", str(args.window),
                       "--log", paths["client_log.csv"]], stdout=cout, stderr=subprocess.STDOUT)

    t0 = time.time()
    schedule = [(args.fail_at, "link_down"), (args.restore_at, "link_up")]
    markers = []
    while client.poll() is None:
        elapsed = time.time() - t0
        while schedule and elapsed >= schedule[0][0]:
            _, name = schedule.pop(0)
            markers.append((time.time(), name))      # timestamp taken just before the command
            print(f"[demo] t={elapsed:5.1f}s  {name}  (s1-s2)", flush=True)
            set_link(name == "link_up")
        time.sleep(0.05)
    try:
        server.wait(timeout=15)
    except subprocess.TimeoutExpired:
        server.terminate()
    sout.close()
    cout.close()

    with open(paths["markers.csv"], "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "event"])
        for ts, name in markers:
            w.writerow([f"{ts:.6f}", name])
    return client.returncode, paths


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--count", type=int, default=1500)
    ap.add_argument("--rate", type=float, default=50.0)
    ap.add_argument("--window", type=int, default=32)
    ap.add_argument("--port", type=int, default=5005)
    ap.add_argument("--fail-at", type=float, default=8.0, help="seconds after start to bring s1-s2 down")
    ap.add_argument("--restore-at", type=float, default=18.0, help="seconds after start to bring it back up")
    ap.add_argument("--logdir", default=os.path.join(HERE, "logs"))
    ap.add_argument("--controller-events",
                    default=os.path.join(os.environ.get("ADAPTIVE_LOG_DIR",
                                                        os.path.join(ROOT, "controller", "logs")), "events.csv"))
    args = ap.parse_args()

    from mininet.link import TCLink
    from mininet.net import Mininet
    from mininet.node import OVSSwitch, RemoteController
    sys.path.insert(0, os.path.join(ROOT, "controller", "test_support"))
    from test_topology import RecoveryTopo      # same topology the controller was built against

    net = Mininet(topo=RecoveryTopo(), controller=None, switch=OVSSwitch,
                  link=TCLink, autoSetMacs=True, build=False)
    net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=6653)
    net.build()
    for sw in net.switches:
        sw.cmd("ovs-vsctl set bridge %s protocols=OpenFlow13" % sw.name)
    net.start()
    try:
        try:
            net.waitConnected(timeout=20)
        except Exception:
            pass
        h1, h2 = net.get("h1", "h2")
        # warm-up: lets the controller learn both hosts and install the IPv4 flows
        for _ in range(5):
            if " 0% packet loss" in h1.cmd("ping -c 2 -W 1 %s" % h2.IP()):
                break
            time.sleep(1)
        else:
            print("[demo] WARNING: warm-up ping never succeeded - is the controller running?")

        code, paths = run_transfer(
            h1, h2, lambda up: net.configLinkStatus("s1", "s2", "up" if up else "down"), args)
        print(open(paths["client_stdout.txt"]).read())
        print(open(paths["server_stdout.txt"]).read())
        ev = args.controller_events if os.path.exists(args.controller_events) else None
        res = analyze.analyze(paths["client_log.csv"], paths["server_log.csv"], 0.5,
                              paths["markers.csv"], ev)
        analyze.report(res)
        if ev is None:
            print("(controller events.csv not found - pass --controller-events for the detection breakdown)")
        same = os.path.getsize(paths["received.bin"]) > 0
        print("client exit code:", code, "| received.bin written:", same)
    finally:
        net.stop()
        # logs were written as root; give them back to the invoking user so git can add them
        uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
        if uid and gid and os.path.isdir(args.logdir):
            for name in os.listdir(args.logdir):
                try:
                    os.chown(os.path.join(args.logdir, name), int(uid), int(gid))
                except OSError:
                    pass


if __name__ == "__main__":
    main()
