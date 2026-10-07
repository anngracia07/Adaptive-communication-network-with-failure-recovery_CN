# Adaptive communication network controller

This Ryu application discovers switches and links through OpenFlow and LLDP, learns end hosts dynamically, answers known ARP requests locally, and installs bidirectional IPv4 routes. When a link or switch fails, it recomputes affected paths and installs replacement rules before removing obsolete rules. It is designed to be used independently of the member-owned application and Mininet topology.

## Architecture

```text
OpenFlow switches -- events/Packet-In --> adaptive_controller.py
                                             |       |       |
                                  topology_manager  flow_manager  metrics
                                      graph          registry     CSV logs
```

## Install

Use Python 3.8 or 3.9. Ryu has compatibility issues with newer Python and eventlet; `eventlet==0.30.2` is pinned for that reason.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r controller/requirements.txt
```

Install Mininet and Open vSwitch separately for integration testing. The controller requires Ryu's `--observe-links` discovery. Ryu echo keepalive can detect silent switch death; configure `--echo-request-interval` and `--maximum-unreplied-echo-requests` on `ryu-manager` to tune its detection window.

## Run

Terminal 1, from the repository root:

```sh
ryu-manager --observe-links controller/adaptive_controller.py
```

Terminal 2 (requires root and Mininet):

```sh
sudo python3 controller/test_support/run_scenario.py
```

The test topology defines h1-s1, s4-h2, primary s1-s2-s4, and backup s1-s3-s5-s4. Its primary route is shorter so recovery and failback can be observed.

## Verify

```sh
sudo ovs-vsctl show
sudo ovs-ofctl -O OpenFlow13 dump-flows s1
```

The default logs are `controller/logs/events.csv` and `controller/logs/recovery.csv`. Set `ADAPTIVE_LOG_DIR` before starting Ryu to use a different directory. `logs/` is ignored by Git.

## Integration contract for Members 1 and 2

- The controller listens on TCP port 6653.
- Switches must speak OpenFlow 1.3. The controller discovers topology; it does not assume switch IDs, inter-switch ports, or host attachment ports.
- Host IPv4 addresses and MAC addresses are learned dynamically from ARP and IPv4 packets arriving on edge ports.
- Keep application traffic IPv4. The controller installs one directional match/rule per source and destination IP pair and mirrors it for the reverse direction.
- The controller and topology test files are under `controller/`; logs are written under `controller/logs/` unless `ADAPTIVE_LOG_DIR` overrides it.

## Limitations and future work

The controller is a single point of failure. LLDP-based link detection has a discovery delay, and the current implementation installs one flow rule per IP pair. There is no multi-controller support. Future work could add OpenFlow fast-failover groups and BFD.
