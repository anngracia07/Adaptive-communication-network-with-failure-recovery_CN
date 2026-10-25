# Adaptive Communication Network with Failure Recovery

An SDN network that keeps a UDP data stream alive when a link fails: a Ryu controller
detects the failure and reroutes traffic over a backup path, while the application
retransmits whatever was lost in the meantime.

| Folder | Owner | Contents |
|---|---|---|
| `controller/` | Member 3 | Ryu OpenFlow 1.3 controller (topology discovery, failure detection, rerouting, metrics) and a Mininet test topology |
| `udp_app/` | Member 1 | Reliable-UDP client/server (sequence numbers, ACKs, retransmission), timestamp logging, analyzer, end-to-end demo |
| `docs/` | Member 3 | Controller design notes |

(Member 2's Mininet topology goes in its own folder; `controller/test_support/test_topology.py`
is the topology the other two parts have been tested against: h1-s1, s4-h2, primary path
s1-s2-s4, backup path s1-s3-s5-s4.)

## Run everything together

Terminal 1 – controller (set up as described in `controller/README.md`):

```sh
ryu-manager --observe-links controller/adaptive_controller.py
```

Terminal 2 – Mininet + UDP stream + link failure + analysis:

```sh
sudo python3 udp_app/run_mininet_demo.py
```

Results are printed and saved in `udp_app/logs/`. See `udp_app/README.md` for details,
manual runs from the Mininet CLI and the self-test that needs no Mininet.
