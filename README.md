# Adaptive Communication Network with Failure Recovery

## What this project does

The project sends data from one computer to another over a small virtual network. The network has three different routes between the computers. If a link breaks, the SDN controller notices and chooses another route. The UDP application resends data that did not arrive and checks that the complete file was received correctly.

In simple terms: **it sends data, breaks a network link on purpose, routes around the break, and checks that the data still arrives intact.**

## What's included

- `controller/` — the Ryu controller that discovers switches and links, detects failures, chooses routes, and records controller timing.
- `mem2/` — the Mininet network with three routes and three link-failure scenarios.
- `udp_app/` — the UDP sender and receiver, retransmission logic, demo, and report generator.
- `docs/DESIGN.md` — design details.

The hosts are `h1` (`10.0.0.1`) and `h2` (`10.0.0.2`). Their switch paths are `s1-s2-s4`, `s1-s3-s5-s4`, and `s1-s6-s7-s8-s4`.

## Requirements

Run the network demonstrations in Linux or WSL2 with Mininet and Open vSwitch. The Ryu controller is installed in its own Python 3.9 virtual environment; the UDP programs use the system Python standard library.

### One-time WSL setup

Run these commands in Ubuntu/WSL from the project folder. The Mininet packages may already be installed.

```bash
sudo apt update
sudo apt install mininet openvswitch-switch curl
sudo service openvswitch-switch start

curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
uv python install 3.9
uv venv --python 3.9 .venv-ryu
uv pip install --python .venv-ryu/bin/python "setuptools==59.5.0" wheel pbr
uv pip install --python .venv-ryu/bin/python --no-build-isolation "ryu==4.34"
uv pip install --python .venv-ryu/bin/python -r controller/requirements.txt
```

Check Mininet and Open vSwitch with:

```bash
sudo mn --switch ovsbr --test pingall
```

The check should end with `0% dropped`.

## Run the complete UDP demo

Start with a clean virtual network. Use two WSL terminals.

Run only one controller instance. If Ryu is already running, stop it with **Ctrl+C** first. In **Terminal 1**, clear old Mininet state, then start the controller and leave it running:

```bash
cd /path/to/adaptive-communication-network
sudo mn -c
sudo service openvswitch-switch restart
.venv-ryu/bin/ryu-manager --observe-links controller/adaptive_controller.py
```

In **Terminal 2**, run the demo:

```bash
cd /path/to/adaptive-communication-network
sudo python3 udp_app/run_mininet_demo.py
```

The demo sends 1,500 UDP packets, takes the primary `s1-s2` link down at 8 seconds, and restores it at 18 seconds. It prints delivery, retransmission, latency, loss, and integrity results. If the initial warm-up ping fails, the demo stops with an error instead of leaving the sender retrying indefinitely.

## Run the three controller failure scenarios

With Ryu running in Terminal 1, run this in Terminal 2:

```bash
sudo python3 mem2/failure_simulation.py --case all
```

Expected results:

1. Disable `s1-s2`: traffic uses `s1-s3-s5-s4`; the hosts stay connected.
2. Disable `s1-s3` and `s1-s2`: traffic uses `s1-s6-s7-s8-s4`; the hosts stay connected.
3. Disable `s1-s2`, `s1-s3`, and `s1-s6`: no route remains, so the hosts cannot communicate and the controller marks the flow unreachable.

The script prints a PASS/FAIL result for each scenario. If running a scenario in a separate Mininet session after another demo, clean up Mininet and restart Open vSwitch and Ryu first, as shown above.

## Run checks that don't need Mininet

```bash
python3 udp_app/test_outage.py
```

Controller unit tests (after installing `pytest` and `networkx` in the Python environment used for testing):

```bash
python3 -m pytest controller/tests
```

## Where results are saved

- `udp_app/logs/` — sent/received packet logs, transfer markers, received file, and analysis output.
- `controller/logs/events.csv` — switch/link events, failure detection, and selected paths.
- `controller/logs/recovery.csv` — controller reaction and rule-installation timing in milliseconds.

The UDP analyzer reports packet loss and latency. It only reports an application-level outage duration when the gap between acknowledgements exceeds its default 0.5-second threshold. Controller-level recovery timings are recorded separately in `controller/logs/recovery.csv`.

## Scope

Recovery is reactive: the controller changes routes after it detects a failure. The three alternate routes share the edge switches and host links, so this topology does not survive failure of `s1`, `s4`, or either host-facing link.
