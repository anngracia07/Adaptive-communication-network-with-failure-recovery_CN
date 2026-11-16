"""Compatibility import for the shared Member 2 three-route topology."""
import os
import sys

MEM2_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "mem2"))
if MEM2_DIR not in sys.path:
    sys.path.insert(0, MEM2_DIR)

from network_topology import ThreeRouteTopo as RecoveryTopo  # noqa: E402,F401


if __name__ == "__main__":
    from mininet.cli import CLI
    from mininet.link import TCLink
    from mininet.net import Mininet
    from mininet.node import OVSSwitch, RemoteController

    net = Mininet(topo=RecoveryTopo(), controller=None, switch=OVSSwitch,
                  link=TCLink, autoSetMacs=True, build=False)
    net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=6653)
    net.build()
    for sw in net.switches:
        sw.cmd("ovs-vsctl set bridge %s protocols=OpenFlow13" % sw.name)
    net.start()
    try:
        CLI(net)
    finally:
        net.stop()
