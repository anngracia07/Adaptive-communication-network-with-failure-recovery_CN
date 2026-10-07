"""Manual Mininet recovery scenario. Run with sudo python3 controller/test_support/run_scenario.py."""
import csv
import os
import re
import time
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController
from mininet.topo import Topo
from mininet.link import TCLink


class RecoveryTopo(Topo):
    def build(self):
        h1, h2 = self.addHost("h1", ip="10.0.0.1/24"), self.addHost("h2", ip="10.0.0.2/24")
        sw = {n: self.addSwitch(n) for n in ("s1", "s2", "s3", "s4", "s5")}
        for a, b in ((h1, "s1"), ("s4", h2), ("s1", "s2"), ("s2", "s4"),
                     ("s1", "s3"), ("s3", "s5"), ("s5", "s4")):
            self.addLink(a if a in (h1, h2) else sw[a], b if b in (h1, h2) else sw[b])


def main():
    net = Mininet(topo=RecoveryTopo(), controller=None, switch=OVSSwitch,
                  link=TCLink, autoSetMacs=True, build=False)
    net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=6653)
    net.build()
    for sw in net.switches:
        sw.cmd("ovs-vsctl set bridge %s protocols=OpenFlow13" % sw.name)
    net.start()
    h1, h2 = net.get("h1", "h2")
    try:
        print(net.pingAll())
        ping = h1.popen(["ping", "-n", "10.0.0.2"])
        time.sleep(5)
        print("Bringing s1-s2 down")
        net.configLinkStatus("s1", "s2", "down")
        time.sleep(8)
        backup = net.get("s1").cmd("ovs-ofctl -O OpenFlow13 dump-flows s1")
        print("Backup path flow table:\n", backup)
        assert "10.0.0.2" in backup, "No destination IPv4 flow found on s1 after failure"
        print("Bringing s1-s2 up")
        net.configLinkStatus("s1", "s2", "up")
        time.sleep(8)
        primary = net.get("s1").cmd("ovs-ofctl -O OpenFlow13 dump-flows s1")
        print("Post-hold-down flow table:\n", primary)
        ping.terminate()
        output = ping.communicate()[0].decode(errors="replace")
        match = re.search(r"(\d+) packets transmitted, (\d+) received", output)
        lost = (int(match.group(1)) - int(match.group(2))) if match else "unknown"
        print("Ping packet loss:", lost)
        path = os.path.join(os.path.dirname(__file__), "..", "logs", "recovery.csv")
        try:
            with open(path, newline="") as f:
                rows = list(csv.DictReader(f))
            print("Controller recovery measurements:", rows)
        except OSError:
            print("No recovery.csv found at", path)
    finally:
        net.stop()


if __name__ == "__main__":
    main()
