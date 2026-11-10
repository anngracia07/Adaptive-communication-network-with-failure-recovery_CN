#!/usr/bin/env python3
"""Run the three required Mininet link-failure demonstrations.

Start the Ryu controller separately from the repository root, for example:

    ryu-manager --observe-links controller/adaptive_controller.py

Then run this script as root from the repository root:

    sudo python3 mem2/failure_simulation.py --case all

Each case starts from a restored topology, sends continuous ICMP probes between
h1 and h2, disables the specified links, and checks the expected outcome:
rerouting for Scenarios 1 and 2, and loss of all paths for Scenario 3.
"""

import argparse
import csv
import os
import re
import subprocess
import sys
import time

from network_topology import FAILURE_CASES, HOST_LINKS, ROUTES, SWITCH_LINKS, ThreeRouteTopo


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_EVENTS_DIR = os.environ.get(
    "ADAPTIVE_LOG_DIR", os.path.join(PROJECT_ROOT, "controller", "logs")
)
DEFAULT_EVENTS_FILE = os.path.join(DEFAULT_EVENTS_DIR, "events.csv")
FORWARD_FLOW_KEY = "10.0.0.1|10.0.0.2"


def parse_path_from_detail(detail):
    """Return the DPID list from a controller detail string, or None."""
    match = re.search(r"\bpath=\[([0-9, ]+)\]", detail or "")
    if not match:
        return None
    try:
        return [int(part.strip()) for part in match.group(1).split(",") if part.strip()]
    except ValueError:
        return None


def latest_installed_path(events_file, since_epoch, flow_key=FORWARD_FLOW_KEY):
    """Find the latest forward-flow path installation after a test began."""
    if not os.path.isfile(events_file):
        return None

    latest = None
    try:
        with open(events_file, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if row.get("event_type") != "path_installed":
                    continue
                if row.get("dpid/link") != flow_key:
                    continue
                try:
                    timestamp = float(row.get("timestamp_epoch", ""))
                except (TypeError, ValueError):
                    continue
                if timestamp < since_epoch:
                    continue
                path = parse_path_from_detail(row.get("detail", ""))
                if path:
                    latest = (timestamp, path, row.get("detail", ""))
    except OSError as exc:
        print("[warning] Could not read controller events file: %s" % exc)
        return None

    return latest


def wait_for_connectivity(h1, h2, timeout_seconds):
    """Wait until a single ICMP echo from h1 to h2 receives a reply."""
    deadline = time.time() + timeout_seconds
    last_output = ""
    while time.time() < deadline:
        last_output = h1.cmd("ping -n -c 1 -W 1 %s" % h2.IP())
        if "0% packet loss" in last_output or re.search(r"1 received", last_output):
            return True, last_output
        time.sleep(0.5)
    return False, last_output


def parse_ping_summary(output):
    """Extract transmitted/received/loss from standard Linux ping output."""
    match = re.search(
        r"(?P<tx>\d+) packets transmitted,\s*(?P<rx>\d+) (?:packets )?received,\s*(?P<loss>[0-9.]+)% packet loss",
        output,
    )
    if not match:
        return None
    return {
        "transmitted": int(match.group("tx")),
        "received": int(match.group("rx")),
        "loss_percent": float(match.group("loss")),
    }


def set_links(net, links, status):
    """Change Mininet link status for each (node_a, node_b) pair."""
    state = "up" if status else "down"
    for left, right in links:
        print("[topology] %s: %s <-> %s" % (state.upper(), left, right), flush=True)
        net.configLinkStatus(left, right, state)


def restore_all_links(net):
    """Restore host-facing and inter-switch links before the next test case."""
    set_links(net, HOST_LINKS + SWITCH_LINKS, True)


def run_case(net, case_name, args, events_file):
    case = FAILURE_CASES[case_name]
    h1, h2 = net.get("h1", "h2")
    expected_route = case["expected_route"]
    expected_dpids = [int(switch[1:]) for switch in expected_route] if expected_route else None

    print("\n" + "=" * 72)
    print(case["title"])
    print("Expected route: %s" % (" -> ".join(expected_route) if expected_route else "NO AVAILABLE PATH"))
    print("Links to disable: %s" % ", ".join("%s-%s" % link for link in case["failed_links"]))

    # Every case is isolated: first restore every link and allow the
    # controller's configured failback hold-down to expire.
    restore_all_links(net)
    time.sleep(args.restore_wait)

    connected, output = wait_for_connectivity(h1, h2, args.connectivity_timeout)
    if not connected:
        print("[FAIL] Hosts cannot communicate before the test case.")
        print(output)
        return False

    # The warm-up packets ensure the controller has learned both host locations
    # and installed the initial forward/reverse IPv4 flows before the failure.
    time.sleep(1.0)
    test_started = time.time()
    ping_process = h1.popen(
        ["ping", "-n", "-i", str(args.ping_interval), h2.IP()],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        for index, link in enumerate(case["failed_links"]):
            set_links(net, (link,), False)
            if index < len(case["failed_links"]) - 1:
                time.sleep(args.between_failures)

        print("[test] Waiting %.1f seconds for failure detection and rerouting..." % args.failure_wait)
        time.sleep(args.failure_wait)

        check_output = h1.cmd("ping -n -c 4 -W 1 %s" % h2.IP())
        reachable = "0% packet loss" in check_output or bool(
            re.search(r"\d+ received", check_output)
        ) and not "100% packet loss" in check_output

        route_event = latest_installed_path(events_file, test_started)
        if expected_dpids is None:
            # For the no-path boundary test, success means the hosts are
            # unreachable and the controller did not install a new path.
            route_verified = route_event is None
        else:
            route_verified = route_event is not None and route_event[1] == expected_dpids

        print("\n[connectivity check]\n%s" % check_output.strip())
        if route_event:
            print("[controller] Latest installed path: %s" % route_event[1])
            print("[controller] Event detail: %s" % route_event[2])
        else:
            print("[controller] No matching path_installed event found in %s" % events_file)

        # Also show S1's forwarding entries as supplementary evidence. The
        # controller path event above is the authoritative route assertion.
        flow_dump = net.get("s1").cmd("ovs-ofctl -O OpenFlow13 dump-flows s1")
        print("\n[S1 OpenFlow 1.3 flow table]\n%s" % flow_dump.strip())

        if expected_dpids is None:
            connectivity_verified = not reachable
            if connectivity_verified:
                print("[PASS] H1 cannot reach H2: all three route-entry links are down.")
            else:
                print("[FAIL] H1 can still reach H2 although all route-entry links should be down.")
            if route_verified:
                print("[PASS] No new path_installed event was recorded for this test.")
            else:
                print("[FAIL] Controller installed a path when no route should exist.")
        else:
            connectivity_verified = reachable
            if reachable:
                print("[PASS] H1 can still reach H2 after the configured failures.")
            else:
                print("[FAIL] H1 cannot reach H2 after the configured failures.")
            if route_verified:
                print("[PASS] Controller selected the expected route: %s" % " -> ".join(expected_route))
            elif route_event:
                print("[FAIL] Controller selected %s; expected %s." % (
                    " -> ".join(str(value) for value in route_event[1]),
                    " -> ".join(str(value) for value in expected_dpids),
                ))
            else:
                print("[FAIL] Route selection could not be verified from controller events.")

        return connectivity_verified and route_verified
    finally:
        if ping_process.poll() is None:
            ping_process.terminate()
        try:
            ping_output, _ = ping_process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            ping_process.kill()
            ping_output, _ = ping_process.communicate()
        summary = parse_ping_summary(ping_output or "")
        if summary:
            print("\n[continuous ping] %d transmitted, %d received, %.1f%% loss" % (
                summary["transmitted"], summary["received"], summary["loss_percent"]
            ))
        else:
            print("\n[continuous ping] Summary unavailable; inspect the ping output if needed.")

        # Restore only the links this case disabled. The next case also performs
        # a full restoration as a defensive reset.
        set_links(net, case["failed_links"], True)
        print("[test] Waiting %.1f seconds for controller failback." % args.restore_wait)
        time.sleep(args.restore_wait)


def create_network(args):
    """Build the Mininet network and attach it to the external Ryu controller."""
    try:
        from mininet.link import TCLink
        from mininet.net import Mininet
        from mininet.node import OVSSwitch, RemoteController
    except ImportError as exc:
        raise RuntimeError(
            "Mininet is not importable in this Python environment. Run this script "
            "on the Mininet machine with the system Python."
        ) from exc

    net = Mininet(
        topo=ThreeRouteTopo(),
        controller=None,
        switch=OVSSwitch,
        link=TCLink,
        autoSetMacs=True,
        build=False,
    )
    net.addController(
        "c0",
        controller=RemoteController,
        ip=args.controller_ip,
        port=args.controller_port,
    )
    net.build()

    for switch in net.switches:
        switch.cmd("ovs-vsctl set bridge %s protocols=OpenFlow13" % switch.name)

    net.start()
    try:
        net.waitConnected(timeout=args.connect_timeout)
    except Exception as exc:
        print("[warning] Mininet did not confirm controller connection: %s" % exc)
        print("[warning] Continuing with active ping checks; verify Ryu is running.")
    return net


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--case", choices=("one_link", "two_link", "no_link", "all"), default="all",
                        help="run one required scenario or all three (default: all)")
    parser.add_argument("--controller-ip", default="127.0.0.1")
    parser.add_argument("--controller-port", type=int, default=6653)
    parser.add_argument("--controller-events", default=DEFAULT_EVENTS_FILE,
                        help="Ryu events.csv path used to verify the selected path")
    parser.add_argument("--connect-timeout", type=float, default=20.0)
    parser.add_argument("--connectivity-timeout", type=float, default=20.0)
    parser.add_argument("--discovery-wait", type=float, default=5.0,
                        help="seconds to allow initial LLDP topology discovery")
    parser.add_argument("--failure-wait", type=float, default=12.0,
                        help="seconds after link shutdown for controller detection/rerouting")
    parser.add_argument("--restore-wait", type=float, default=8.0,
                        help="seconds after link restoration for the 5-second controller hold-down")
    parser.add_argument("--between-failures", type=float, default=0.5,
                        help="pause between link shutdown commands in a multi-link case")
    parser.add_argument("--ping-interval", type=float, default=0.2)
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        net = create_network(args)
    except RuntimeError as exc:
        print("[error] %s" % exc, file=sys.stderr)
        return 2

    results = {}
    try:
        print("[setup] Allowing %.1f seconds for initial LLDP discovery." % args.discovery_wait)
        time.sleep(args.discovery_wait)
        h1, h2 = net.get("h1", "h2")
        connected, output = wait_for_connectivity(h1, h2, args.connectivity_timeout)
        if not connected:
            print("[error] Initial H1-to-H2 connectivity failed. Is Ryu running with --observe-links?")
            print(output)
            return 2

        selected_cases = list(FAILURE_CASES) if args.case == "all" else [args.case]
        for case_name in selected_cases:
            results[case_name] = run_case(net, case_name, args, args.controller_events)
    finally:
        net.stop()

    print("\n" + "=" * 72)
    print("MEMBER 2 FAILURE-SIMULATION SUMMARY")
    for case_name, passed in results.items():
        print("%s: %s" % (case_name, "PASS" if passed else "FAIL"))

    return 0 if results and all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
