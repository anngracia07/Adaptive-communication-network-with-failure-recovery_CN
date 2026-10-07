"""Standalone Mininet topology for exercising the controller."""
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController
from mininet.topo import Topo
from mininet.link import TCLink


class RecoveryTopo(Topo):
    def build(self):
        h1 = self.addHost("h1", ip="10.0.0.1/24")
        h2 = self.addHost("h2", ip="10.0.0.2/24")
        switches = {name: self.addSwitch(name) for name in ("s1", "s2", "s3", "s4", "s5")}
        self.addLink(h1, switches["s1"])
        self.addLink(switches["s4"], h2)
        self.addLink(switches["s1"], switches["s2"])
        self.addLink(switches["s2"], switches["s4"])
        self.addLink(switches["s1"], switches["s3"])
        self.addLink(switches["s3"], switches["s5"])
        self.addLink(switches["s5"], switches["s4"])


def main():
    net = Mininet(topo=RecoveryTopo(), controller=None, switch=OVSSwitch,
                  link=TCLink, autoSetMacs=True, build=False)
    net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=6653)
    net.build()
    for sw in net.switches:
        sw.cmd("ovs-vsctl set bridge %s protocols=OpenFlow13" % sw.name)
    net.start()
    try:
        CLI = __import__("mininet.cli", fromlist=["CLI"]).CLI
        CLI(net)
    finally:
        net.stop()


if __name__ == "__main__":
    main()
