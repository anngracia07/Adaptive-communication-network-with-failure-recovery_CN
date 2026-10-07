"""Ryu-independent topology graph and shortest-path helpers."""
import networkx as nx


class TopologyManager:
    def __init__(self, default_cost=1.0):
        self.graph = nx.Graph()
        self.default_cost = float(default_cost)
        self.switches = set()
        self.known_links = {}

    def add_switch(self, dpid):
        self.switches.add(dpid)
        self.graph.add_node(dpid)

    def remove_switch(self, dpid):
        self.switches.discard(dpid)
        self.graph.remove_node(dpid) if dpid in self.graph else None
        for key in [k for k in self.known_links if dpid in k[:2]]:
            self.known_links.pop(key, None)

    @staticmethod
    def link_key(src, dst):
        return (src, dst) if src <= dst else (dst, src)

    def add_link(self, src, dst, src_port, dst_port, cost=None, activate=True):
        self.add_switch(src)
        self.add_switch(dst)
        key = self.link_key(src, dst)
        edge = {"src_port": src_port, "dst_port": dst_port,
                "cost": self.default_cost if cost is None else float(cost),
                "endpoints": ((src, src_port), (dst, dst_port))}
        self.known_links[key] = edge
        if activate:
            self.graph.add_edge(src, dst, **edge)
        return key

    def activate_link(self, key):
        edge = self.known_links.get(key)
        if edge is None:
            return False
        a, b = key
        self.graph.add_edge(a, b, **edge)
        return True

    def remove_link(self, src, dst, forget=False):
        key = self.link_key(src, dst)
        existed = self.graph.has_edge(*key)
        if existed:
            self.graph.remove_edge(*key)
        if forget:
            self.known_links.pop(key, None)
        return existed

    def remove_port(self, dpid, port):
        removed = []
        for a, b, data in list(self.graph.edges(data=True)):
            if (dpid, port) in data.get("endpoints", ()):
                removed.append((a, b))
                self.graph.remove_edge(a, b)
        return removed

    def shortest_path(self, src, dst):
        if src not in self.graph or dst not in self.graph:
            return None
        try:
            return nx.shortest_path(self.graph, src, dst, weight="cost")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def path_cost(self, path):
        if not path or len(path) < 2:
            return 0.0
        return sum(self.graph[a][b]["cost"] for a, b in zip(path, path[1:]))

    def output_port(self, src, dst):
        data = self.graph[src][dst]
        if src == data["endpoints"][0][0]:
            return data["src_port"]
        return data["dst_port"]

    def port_between(self, src, dst):
        return self.output_port(src, dst)

    def is_link_up(self, key):
        return self.graph.has_edge(*key)
