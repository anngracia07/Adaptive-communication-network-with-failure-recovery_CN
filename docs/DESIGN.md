# Design: adaptive communication network

## Problem and approach

A path between communicating hosts can stop working when a link or switch fails. A conventional fixed route may leave traffic on that broken path. This project uses a software-defined networking (SDN) controller to keep a view of the switches and links, calculate a new route, and update switch forwarding rules when the network changes.

SDN separates the forwarding performed by switches from the control logic that chooses routes. OpenFlow gives this controller a way to install IPv4 forwarding rules in switches while Mininet and Open vSwitch provide a repeatable network for evaluation.

## Why UDP

UDP is useful for testing recovery because it does not retransmit lost packets or hide a short outage with transport-level retransmissions. The application can observe interruption and packet loss directly. The controller forwards IPv4 traffic independently of whether the application uses UDP or TCP; UDP is an evaluation choice, not a special controller requirement.

## Reactive recovery

This implementation is reactive. It learns host locations and installs routes when traffic arrives, then calculates replacement paths after a failure notification. Proactive recovery would preinstall backup actions before a failure. OpenFlow fast-failover groups are a suitable future improvement because switches can select a live backup port locally, reducing controller involvement and recovery delay.

## Failure detection

Three signals cover different failure cases:

- **Port status:** a switch reports a port down. The controller removes links using that port immediately, without waiting for discovery expiry.
- **LLDP link-delete:** Ryu's topology discovery no longer sees a neighbor. This can detect a broken adjacency but waits for the LLDP discovery timeout.
- **Echo keepalive:** Ryu sends OpenFlow echo requests and can mark an unresponsive switch dead after configured unanswered requests. This detects switch/controller-channel failure, rather than just one link. Tune `--echo-request-interval` and `--maximum-unreplied-echo-requests` to trade detection speed against tolerance for transient control-channel delay.

The controller logs which of port status or LLDP reported a link failure first, so measured detection can be compared with the source.

## Route replacement and flapping

For a failure, the controller calculates routes using link cost (one by default, representing hop count). It sends rules to downstream and transit switches before the ingress switch. Replacing the ingress match last keeps traffic on the old path until the new downstream rules are ready. Once the ingress rule points to the new path, rules on switches no longer needed can be deleted by the flow cookie. Barriers provide confirmation that touched switches processed their updates.

Recovered links are held down for five seconds before they are returned to the active graph. If they fail again during that period, the pending timer is cancelled. This hold-down prevents repeated path changes when a link flaps. After the timer, a flow moves back only if its new path has strictly lower cost, or if it was unreachable.

## ARP loop avoidance

Redundant links create cycles, so a broadcast sent through every switch could circulate. The controller never floods ARP across inter-switch links. For an unknown target, it sends requests only on host-facing ports. For a known target, it replies directly using proxy ARP. LLDP packets are ignored by the packet handler because Ryu's topology service processes them.

## Measuring recovery time

`events.csv` records topology events, detection source, route installations, and barrier confirmations using epoch timestamps. `recovery.csv` records `t_detect`, computation completion, rule-send time, and barrier completion. Controller reaction time is measured from detection to rule-send; rule-install time is measured from rule-send to barrier confirmation. These are controller/switch control-plane measurements. Packet loss from the continuous ping in the scenario gives a separate view of user-visible interruption.
