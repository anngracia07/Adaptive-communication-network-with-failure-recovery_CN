"""Ryu event adapter for topology discovery, learning, and path recovery."""
import time

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER, set_ev_cls
from ryu.lib import hub
from ryu.lib.packet import packet, ethernet, arp, ipv4, ether_types
from ryu.ofproto import ofproto_v1_3
from ryu.topology import event
from ryu.topology.api import get_switch

from config import (CONTROLLER_PRIORITY, TABLE_MISS_PRIORITY, DEFAULT_LINK_COST,
                    FAILBACK_HOLD_DOWN_SECONDS, FLOW_IDLE_TIMEOUT, ENABLE_PORT_STATS,
                    PORT_STATS_INTERVAL, EVENTS_CSV, RECOVERY_CSV)
from flow_manager import FlowManager, flow_cookie, flow_id
from metrics import event as log_event, recovery as log_recovery
from topology_manager import TopologyManager


class AdaptiveController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(AdaptiveController, self).__init__(*args, **kwargs)
        self.topology = TopologyManager(DEFAULT_LINK_COST)
        self.flow_manager = FlowManager()
        self.datapaths = {}
        self.switch_ports = {}
        self.hosts = {}  # IP -> {mac, dpid, port}; MAC is retained for proxy ARP.
        self.pending_failbacks = {}
        self.recovery_context = {}
        if ENABLE_PORT_STATS:
            self.stats_thread = hub.spawn(self._poll_port_stats)

    def add_flow(self, datapath, priority, match, actions, cookie=0, idle_timeout=0):
        """Build and send a flow-mod, keeping flow installation consistent."""
        parser, ofproto = datapath.ofproto_parser, datapath.ofproto
        instructions = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, cookie=cookie, priority=priority,
                                match=match, instructions=instructions, idle_timeout=idle_timeout)
        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """Install a table-miss rule so unknown packets reach this controller."""
        dp = ev.msg.datapath
        self.datapaths[dp.id] = dp
        self.topology.add_switch(dp.id)
        self.switch_ports[dp.id] = {p.port_no for p in dp.ports.values()} if isinstance(dp.ports, dict) else {p.port_no for p in dp.ports}
        parser, ofp = dp.ofproto_parser, dp.ofproto
        self.add_flow(dp, TABLE_MISS_PRIORITY, parser.OFPMatch(),
                      [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)])
        log_event(EVENTS_CSV, "switch_connect", dp.id, "OpenFlow 1.3")
        # Reconcile links that Ryu may have discovered before this switch connected.
        self._sync_discovered_topology()

    def _sync_discovered_topology(self):
        for sw in get_switch(self, None):
            self.topology.add_switch(sw.dp.id)
            self.switch_ports[sw.dp.id] = {p.port_no for p in sw.ports}
        for link in self._discovered_links():
            key = self.topology.link_key(link.src.dpid, link.dst.dpid)
            if key not in self.topology.known_links:
                self.topology.add_link(link.src.dpid, link.dst.dpid, link.src.port_no, link.dst.port_no)

    def _discovered_links(self):
        try:
            from ryu.topology.api import get_link
            return get_link(self, None)
        except Exception:
            return []

    @set_ev_cls(event.EventSwitchEnter)
    def switch_enter_handler(self, ev):
        """Register discovered switches and reconcile links around late arrivals."""
        sw = ev.switch
        self.topology.add_switch(sw.dp.id)
        self.switch_ports[sw.dp.id] = {p.port_no for p in sw.ports}
        self._sync_discovered_topology()

    @set_ev_cls(event.EventSwitchLeave)
    def switch_leave_handler(self, ev):
        """Remove a departed switch and recompute paths for its affected traffic."""
        dpid = ev.switch.dp.id
        self.topology.remove_switch(dpid)
        self.datapaths.pop(dpid, None)
        self.switch_ports.pop(dpid, None)
        for h in self.hosts.values():
            if h["dpid"] == dpid:
                h["dpid"] = None
        self.recompute_paths("switch_leave")

    @set_ev_cls(event.EventLinkAdd)
    def link_add_handler(self, ev):
        """Record new links immediately; recovered known links pass through hold-down."""
        link = ev.link
        key = self.topology.link_key(link.src.dpid, link.dst.dpid)
        known = key in self.topology.known_links
        self.topology.add_link(link.src.dpid, link.dst.dpid, link.src.port_no, link.dst.port_no, activate=not known)
        log_event(EVENTS_CSV, "link_add", key, "discovered")
        if not known:
            self.recompute_paths("link_add")
            return
        previous = self.pending_failbacks.pop(key, None)
        if previous:
            previous.kill()
        log_event(EVENTS_CSV, "failback_scheduled", key, "hold_down=%s" % FAILBACK_HOLD_DOWN_SECONDS)
        self.pending_failbacks[key] = hub.spawn_after(FAILBACK_HOLD_DOWN_SECONDS, self._finish_failback, key)

    def _finish_failback(self, key):
        self.pending_failbacks.pop(key, None)
        if key not in self.topology.known_links:
            return
        self.topology.activate_link(key)
        log_event(EVENTS_CSV, "failback_done", key, "hold-down elapsed")
        self.recompute_paths("failback")

    @set_ev_cls(event.EventLinkDelete)
    def link_delete_handler(self, ev):
        """Remove a failed link and reroute affected flows as soon as LLDP reports it."""
        link = ev.link
        key = self.topology.link_key(link.src.dpid, link.dst.dpid)
        timer = self.pending_failbacks.pop(key, None)
        if timer:
            timer.kill()
        if self.topology.remove_link(*key):
            self._link_failed(key, "lldp")

    def _link_failed(self, key, source):
        t_detect = time.time()
        log_event(EVENTS_CSV, "detection_source", key, source)
        log_event(EVENTS_CSV, "link_delete", key, "detected_by=" + source)
        self.recovery_context[key] = {"t_detect": t_detect, "barriers": set(), "t_compute_done": None, "t_rules_sent": None}
        self.recompute_paths("link_down", failed_link=key)

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def port_status_handler(self, ev):
        """Remove links touching a down port immediately, ahead of LLDP timeout."""
        msg, ofp = ev.msg, ev.msg.datapath.ofproto
        port = msg.desc.port_no
        self.switch_ports.setdefault(msg.datapath.id, set()).add(port)
        if msg.reason == ofp.OFPPR_DELETE or (msg.desc.state & ofp.OFPPS_LINK_DOWN):
            for key in self.topology.remove_port(msg.datapath.id, port):
                self._link_failed(key, "port-status")
            log_event(EVENTS_CSV, "port_down", msg.datapath.id, "port=%s" % port)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def state_change_handler(self, ev):
        """Track datapath liveness; Ryu echo keepalive detects silent dead switches."""
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
        elif ev.state == DEAD_DISPATCHER and self.datapaths.get(dp.id) is dp:
            self.datapaths.pop(dp.id, None)
            self.topology.remove_switch(dp.id)
            self.recompute_paths("switch_dead")

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        """Ignore LLDP, learn hosts only at edge ports, proxy ARP, and route IPv4."""
        msg, dp = ev.msg, ev.msg.datapath
        in_port = msg.match.get("in_port")
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None or eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        interswitch = self._interswitch_ports(dp.id)
        if in_port not in interswitch:
            self.switch_ports.setdefault(dp.id, set()).add(in_port)
            a = pkt.get_protocol(arp.arp)
            ip4 = pkt.get_protocol(ipv4.ipv4)
            if a:
                self._learn_host(a.src_ip, a.src_mac, dp.id, in_port)
                if a.opcode == arp.ARP_REQUEST and a.dst_ip in self.hosts:
                    self._proxy_arp(dp, in_port, a, eth.src)
                    return
                if a.opcode == arp.ARP_REQUEST:
                    self._flood_edge_arp(msg, in_port)
                return
            if ip4:
                self._learn_host(ip4.src, eth.src, dp.id, in_port)
                self._ensure_bidirectional(ip4.src, ip4.dst)
        ip4 = pkt.get_protocol(ipv4.ipv4)
        if ip4:
            self._forward_ipv4(msg, ip4.src, ip4.dst)

    def _learn_host(self, ip, mac, dpid, port):
        self.hosts[ip] = {"mac": mac, "dpid": dpid, "port": port}

    def _interswitch_ports(self, dpid):
        result = set()
        if dpid in self.topology.graph:
            for a, b, data in self.topology.graph.edges(data=True):
                if a == dpid:
                    result.add(data["src_port"] if data["endpoints"][0][0] == a else data["dst_port"])
                elif b == dpid:
                    result.add(data["src_port"] if data["endpoints"][0][0] == b else data["dst_port"])
        return result

    def _edge_ports(self, dpid):
        return self.switch_ports.get(dpid, set()) - self._interswitch_ports(dpid)

    def _proxy_arp(self, dp, in_port, request, source_mac):
        target = self.hosts[request.dst_ip]
        p = packet.Packet()
        p.add_protocol(ethernet.ethernet(dst=source_mac, src=target["mac"], ethertype=ether_types.ETH_TYPE_ARP))
        p.add_protocol(arp.arp(hwtype=1, proto=0x0800, hlen=6, plen=4, opcode=arp.ARP_REPLY,
                               src_mac=target["mac"], src_ip=request.dst_ip,
                               dst_mac=source_mac, dst_ip=request.src_ip))
        p.serialize()
        action = [dp.ofproto_parser.OFPActionOutput(in_port)]
        dp.send_msg(dp.ofproto_parser.OFPPacketOut(datapath=dp, buffer_id=dp.ofproto.OFP_NO_BUFFER,
                    in_port=dp.ofproto.OFPP_CONTROLLER, actions=action, data=p.data))

    def _flood_edge_arp(self, msg, ingress_port):
        for dpid, dp in list(self.datapaths.items()):
            for port in self._edge_ports(dpid):
                if dpid == msg.datapath.id and port == ingress_port:
                    continue
                action = [dp.ofproto_parser.OFPActionOutput(port)]
                # A buffer id belongs only to the switch that produced the Packet-In.
                # Send the captured frame unbuffered when replicating to other switches.
                dp.send_msg(dp.ofproto_parser.OFPPacketOut(datapath=dp, buffer_id=dp.ofproto.OFP_NO_BUFFER,
                    in_port=dp.ofproto.OFPP_CONTROLLER, actions=action, data=msg.data))

    def _ensure_bidirectional(self, src, dst):
        a, b = self.hosts.get(src), self.hosts.get(dst)
        if not a or not b or a["dpid"] is None or b["dpid"] is None:
            return
        for s, d in ((src, dst), (dst, src)):
            key = flow_id(s, d)
            if key not in self.flow_manager.flows or self.flow_manager.flows[key]["status"] != "active":
                self.flow_manager.learn(s, d)
                self.recompute_paths("packet_in")

    def _forward_ipv4(self, msg, src, dst):
        key = flow_id(src, dst)
        flow = self.flow_manager.flows.get(key)
        if not flow or flow["status"] != "active":
            return
        host = self.hosts.get(dst)
        if not host:
            return
        path = flow["current_path"]
        dpid = msg.datapath.id
        if dpid not in path:
            return
        idx = path.index(dpid)
        out_port = host["port"] if idx == len(path)-1 else self.topology.port_between(dpid, path[idx+1])
        actions = [msg.datapath.ofproto_parser.OFPActionOutput(out_port)]
        msg.datapath.send_msg(msg.datapath.ofproto_parser.OFPPacketOut(datapath=msg.datapath,
            buffer_id=msg.buffer_id, in_port=msg.match.get("in_port"), actions=actions,
            data=None if msg.buffer_id != msg.datapath.ofproto.OFP_NO_BUFFER else msg.data))

    def recompute_paths(self, reason, failed_link=None):
        """Compute route changes and install new routes before deleting stale rules."""
        now = time.time()
        for key, flow in list(self.flow_manager.flows.items()):
            src_host, dst_host = self.hosts.get(flow["src"]), self.hosts.get(flow["dst"])
            if not src_host or not dst_host or src_host["dpid"] is None or dst_host["dpid"] is None:
                continue
            old = flow.get("current_path")
            new = self.topology.shortest_path(src_host["dpid"], dst_host["dpid"])
            if new is None:
                if old:
                    self._delete_flow_cookie(flow["src"], flow["dst"])
                self.flow_manager.set_path(key, None)
                log_event(EVENTS_CSV, "flow_unreachable", key, "reason=" + reason)
                continue
            if old and reason == "failback" and self.topology.path_cost(new) >= self._path_cost_if_available(old):
                continue
            if old == new and flow["status"] == "active":
                continue
            t_compute_done = time.time()
            touched = self._install_path(flow["src"], flow["dst"], new, old)
            t_rules_sent = time.time()
            self.flow_manager.set_path(key, new)
            log_event(EVENTS_CSV, "path_installed", key, "path=%s reason=%s" % (new, reason))
            ctx = self.recovery_context.get(failed_link)
            if ctx:
                ctx.update(t_compute_done=t_compute_done, t_rules_sent=t_rules_sent)
                ctx["barriers"].update(touched)
                if not touched:
                    log_recovery(RECOVERY_CSV, ctx["t_detect"], t_compute_done, t_rules_sent, t_rules_sent)
                    self.recovery_context.pop(failed_link, None)

    def _path_cost_if_available(self, path):
        try:
            return self.topology.path_cost(path)
        except (KeyError, TypeError):
            return float("inf")

    def _install_path(self, src, dst, path, old_path):
        src_host, dst_host = self.hosts[src], self.hosts[dst]
        cookie = flow_cookie(src, dst)
        match_by_switch = {}
        for i, dpid in enumerate(path):
            out_port = dst_host["port"] if i == len(path)-1 else self.topology.port_between(dpid, path[i+1])
            match_by_switch[dpid] = out_port
        ingress = src_host["dpid"]
        order = [d for d in reversed(path) if d != ingress] + [ingress]
        touched = set()
        for dpid in order:
            dp = self.datapaths.get(dpid)
            if not dp:
                continue
            parser = dp.ofproto_parser
            match = parser.OFPMatch(eth_type=0x0800, ipv4_src=src, ipv4_dst=dst)
            self.add_flow(dp, CONTROLLER_PRIORITY, match, [parser.OFPActionOutput(match_by_switch[dpid])], cookie, FLOW_IDLE_TIMEOUT)
            touched.add(dpid)
        stale = self.flow_manager.stale_switches(old_path, path)
        for dpid in stale:
            dp = self.datapaths.get(dpid)
            if dp:
                self._delete_cookie_on(dp, cookie)
                touched.add(dpid)
        # Packet-out the initial Packet-In where appropriate; switch buffers are handled by Packet-In forwarding.
        for dpid in touched:
            dp = self.datapaths[dpid]
            dp.send_msg(dp.ofproto_parser.OFPBarrierRequest(dp))
        return touched

    def _delete_cookie_on(self, dp, cookie):
        parser, ofp = dp.ofproto_parser, dp.ofproto
        dp.send_msg(parser.OFPFlowMod(datapath=dp, cookie=cookie, cookie_mask=0xffffffffffffffff,
            command=ofp.OFPFC_DELETE, table_id=ofp.OFPTT_ALL, out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY,
            match=parser.OFPMatch()))

    def _delete_flow_cookie(self, src, dst):
        cookie = flow_cookie(src, dst)
        for dp in self.datapaths.values():
            self._delete_cookie_on(dp, cookie)

    @set_ev_cls(ofp_event.EventOFPBarrierReply, MAIN_DISPATCHER)
    def barrier_reply_handler(self, ev):
        """Record when all switches touched by a recovery confirm their flow-mods."""
        dpid, now = ev.msg.datapath.id, time.time()
        for key, ctx in list(self.recovery_context.items()):
            if dpid not in ctx["barriers"]:
                continue
            ctx["barriers"].discard(dpid)
            if not ctx["barriers"] and ctx.get("t_rules_sent") is not None:
                log_event(EVENTS_CSV, "barrier_confirmed", key, "dpid=%s" % dpid)
                log_recovery(RECOVERY_CSV, ctx["t_detect"], ctx["t_compute_done"], ctx["t_rules_sent"], now)
                self.recovery_context.pop(key, None)

    def _poll_port_stats(self):
        while True:
            for dp in list(self.datapaths.values()):
                dp.send_msg(dp.ofproto_parser.OFPPortStatsRequest(dp, 0, dp.ofproto.OFPP_ANY))
            hub.sleep(PORT_STATS_INTERVAL)
