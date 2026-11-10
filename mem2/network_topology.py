#!/usr/bin/env python3
"""Three-route Mininet topology for the SDN-controlled UDP recovery project.

The two end hosts attach to common edge switches.  Three internally disjoint
switch paths connect those edge switches:

    Route 1 (1 intermediate switch): s1 -- s2 -- s4
    Route 2 (2 intermediate switches): s1 -- s3 -- s5 -- s4
    Route 3 (3 intermediate switches): s1 -- s6 -- s7 -- s8 -- s4

The controller learns the host attachment points and discovers inter-switch
links through Ryu's LLDP topology service.  Switch DPIDs are explicitly set to
1..8 so the controller's path-installed log entries can be read unambiguously.
"""

from mininet.topo import Topo

HOSTS = {
    "h1": "10.0.0.1/24",
    "h2": "10.0.0.2/24",
}

SWITCHES = ("s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8")

# The host-facing links are deliberately common to all three routes.
HOST_LINKS = (("h1", "s1"), ("s4", "h2"))

# Each route uses its own intermediate switches and its own inter-switch links.
ROUTES = {
    "route1": ("s1", "s2", "s4"),
    "route2": ("s1", "s3", "s5", "s4"),
    "route3": ("s1", "s6", "s7", "s8", "s4"),
}

SWITCH_LINKS = (
    ("s1", "s2"),
    ("s2", "s4"),
    ("s1", "s3"),
    ("s3", "s5"),
    ("s5", "s4"),
    ("s1", "s6"),
    ("s6", "s7"),
    ("s7", "s8"),
    ("s8", "s4"),
)

ALL_LINKS = HOST_LINKS + SWITCH_LINKS

# The three required evaluation scenarios. Every scenario starts from a
# fully restored topology before its listed links are disabled.
# 1) Disable Route 1's entry link: Route 2 is the shortest surviving route.
# 2) Disable Route 1 and Route 2 entry links: only Route 3 remains.
# 3) Disable all three route-entry links: no H1-to-H2 path remains.
FAILURE_CASES = {
    "one_link": {
        "title": "Scenario 1 - ONE LINK FAILURE",
        "failed_links": (("s1", "s2"),),
        "expected_route": ROUTES["route2"],
    },
    "two_link": {
        "title": "Scenario 2 - 2 LINK FAILURE",
        "failed_links": (("s1", "s3"), ("s1", "s2")),
        "expected_route": ROUTES["route3"],
    },
    "no_link": {
        "title": "Scenario 3 - NO LINK TEST",
        "failed_links": (("s1", "s2"), ("s1", "s3"), ("s1", "s6")),
        "expected_route": None,
    },
}


class ThreeRouteTopo(Topo):
    """Two hosts and eight switches connected by three alternative routes."""

    def build(self):
        hosts = {name: self.addHost(name, ip=ip) for name, ip in HOSTS.items()}

        switches = {}
        for name in SWITCHES:
            # Stable DPIDs: s1 -> 1, s2 -> 2, ..., s8 -> 8.
            dpid = "%016x" % int(name[1:])
            switches[name] = self.addSwitch(name, dpid=dpid)

        self.addLink(hosts["h1"], switches["s1"])
        self.addLink(switches["s4"], hosts["h2"])

        for left, right in SWITCH_LINKS:
            self.addLink(switches[left], switches[right])


# The existing Member 1 and Member 3 scripts import a class named RecoveryTopo.
# This alias lets the integration shim keep that import name while using this
# new, single authoritative topology definition.
RecoveryTopo = ThreeRouteTopo


if __name__ == "__main__":
    print("Three-route Mininet topology definition")
    print("Hosts:", HOSTS)
    print("Switches:", ", ".join(SWITCHES))
    for name, route in ROUTES.items():
        print("%s: %s" % (name, " -> ".join(route)))
    print("Total links: %d (2 host-facing + 9 inter-switch)" % len(ALL_LINKS))
