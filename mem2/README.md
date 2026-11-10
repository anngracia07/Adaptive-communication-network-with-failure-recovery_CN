# Member 2 — Mininet topology and link-failure simulation

This folder contains Member 2's Mininet topology and the three required link-failure scenarios. The scenarios below are implemented exactly as specified:

1. **ONE LINK FAILURE:** `s1-s2` is disabled.
2. **2 LINK FAILURE:** `s1-s3` and `s1-s2` are disabled.
3. **NO LINK TEST:** `s1-s2`, `s1-s3`, and `s1-s6` are disabled.

## 1. Network topology

There are two hosts and eight Open vSwitches:

- `h1` — `10.0.0.1/24`, connected to `s1`.
- `h2` — `10.0.0.2/24`, connected to `s4`.

Three branch routes connect the common switches `s1` and `s4`:

| Route | Switch path | Intermediate switches |
|---|---|---:|
| Route 1 | `s1 -> s2 -> s4` | 1 |
| Route 2 | `s1 -> s3 -> s5 -> s4` | 2 |
| Route 3 | `s1 -> s6 -> s7 -> s8 -> s4` | 3 |

The complete paths are `h1 -> s1 -> [branch route] -> s4 -> h2`. The topology has 2 hosts, 8 switches and 11 links (2 host-facing links and 9 inter-switch links). `network_topology.py` is the single authoritative topology definition. Switch DPIDs are fixed from 1 to 8.

## 2. Required failure scenarios

Every scenario begins by restoring **all** links. The test then disables only the links listed in that scenario.

| Scenario | Links disabled | Remaining route(s) | Expected result |
|---|---|---|---|
| 1 — ONE LINK FAILURE | `s1-s2` | Route 2 and Route 3 | Controller selects Route 2; H1 and H2 remain connected. |
| 2 — 2 LINK FAILURE | `s1-s3`, `s1-s2` | Route 3 only | Controller selects Route 3; H1 and H2 remain connected. |
| 3 — NO LINK TEST | `s1-s2`, `s1-s3`, `s1-s6` | No route | No path exists between H1 and H2; communication is expected to fail. |

### Why these scenarios are different

- **Scenario 1:** Route 1 is blocked at its entry (`s1-s2`). Routes 2 and 3 are still available. Route 2 is expected because it is shorter.
- **Scenario 2:** The entry links of both Route 1 and Route 2 are blocked (`s1-s2` and `s1-s3`). Only Route 3 remains, so the controller must use the longest available branch.
- **Scenario 3:** All three branch-entry links (`s1-s2`, `s1-s3`, `s1-s6`) are blocked. The hosts are separated: there is no valid path to reroute onto. This is the boundary/no-path test, not a successful-rerouting case.

The two host-facing links (`h1-s1`, `s4-h2`) and common switches (`s1`, `s4`) are shared by every route. This topology does not claim to survive failure of either host-facing link or either common switch.

## 3. Files

| File | Purpose |
|---|---|
| `network_topology.py` | Hosts, switches, links, route definitions, scenario definitions and `ThreeRouteTopo`. Exposes `RecoveryTopo` as a compatibility alias. |
| `failure_simulation.py` | Starts Mininet, attaches to the external Ryu controller, disables links, checks ICMP reachability and checks controller path-installation logs. |
| `README.md` | Topology, exact scenario definitions and run instructions. |

## 4. Run the scenarios

Start Ryu separately from the repository root:

```bash
ryu-manager --observe-links controller/adaptive_controller.py
```

In another terminal, run all three scenarios:

```bash
sudo python3 mem2/failure_simulation.py --case all
```

Or run each one separately for screenshots:

```bash
sudo python3 mem2/failure_simulation.py --case one_link
sudo python3 mem2/failure_simulation.py --case two_link
sudo python3 mem2/failure_simulation.py --case no_link
```

The script looks for the controller event log at `controller/logs/events.csv` by default. If the controller uses another log directory, pass it explicitly:

```bash
sudo python3 mem2/failure_simulation.py --case all \
  --controller-events /path/to/controller/logs/events.csv
```

Wait durations can be adjusted using `--discovery-wait`, `--failure-wait`, and `--restore-wait` if required by the machine's LLDP and controller timing.

### How the script decides PASS/FAIL

- **Scenarios 1 and 2:** PASS requires H1-to-H2 ICMP connectivity and a new `path_installed` event matching the expected route (Route 2 for Scenario 1; Route 3 for Scenario 2).
- **Scenario 3:** PASS requires H1-to-H2 ICMP connectivity to be unavailable and no new forward-flow `path_installed` event after the test starts. In this scenario, communication failure is the expected network outcome, so it is not automatically a failed test.

The script also prints S1's OpenFlow 1.3 flow table and a continuous-ping summary. The controller event check is used to verify the route; a successful ping alone does not prove which route was selected.

## 5. Important scope limitation: UDP application testing

`failure_simulation.py` currently checks **ICMP ping and controller path logs**. It does not itself send application UDP data or verify UDP acknowledgements/retries. Therefore, this Member 2 script validates the Mininet topology and the network-level failure scenarios; it is not, by itself, proof that the complete UDP recovery application works. End-to-end UDP verification must be run using the Member 1 UDP client/server as part of the integrated project.

## 6. Integration with the other members' code

The topology class is also exported as `RecoveryTopo` to preserve the existing import name used by the other project files. To make the whole repository use this topology, the old duplicate topology definitions in the controller test-support files should be replaced or redirected to this module. For example, `controller/test_support/test_topology.py` can contain:

```python
import os
import sys

MEM2_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "mem2")
)
sys.path.insert(0, MEM2_DIR)

from network_topology import ThreeRouteTopo as RecoveryTopo
```

Do not retain a second, conflicting topology class in another test-support file. Update any existing scenario runner to import the shared `RecoveryTopo` and use the three exact failure sets listed in this README.
