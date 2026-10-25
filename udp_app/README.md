# udp_app – Member 1: UDP Application & Recovery

Part of **Adaptive Communication Network with Failure Recovery**.
This folder contains the traffic source/sink and the application-level recovery.
The Ryu controller (Member 3) is in `../controller`; Member 2's topology is the Mininet network the app runs on.

| File | Purpose |
|---|---|
| `protocol.py` | Packet format (type, seq, cumulative ACK, timestamp) |
| `server.py` | Runs on **h2**. ACKs, re-orders, de-duplicates, writes the stream, verifies SHA-256, logs |
| `client.py` | Runs on **h1**. Paced stream, sliding window, retransmission with adaptive RTO, logs |
| `analyze.py` | Recovery time, packet loss, latency from the logs |
| `run_mininet_demo.py` | One command: Mininet topology + stream + link failure + analysis (needs root, Mininet, running controller) |
| `test_outage.py` | Self-test with a simulated dead link – no Mininet or root needed |

Pure Python 3 standard library. No installs.

## How recovery works

* Every packet has a **sequence number**; the server buffers out-of-order packets and writes them to disk strictly in order.
* The server ACKs every packet (selective ACK + cumulative "next expected").
* The client keeps a **window** of unacknowledged packets and **retransmits** any packet whose timer expires (adaptive RTO from measured RTT, Karn's rule, capped exponential back-off – default cap 1 s so the flow resumes quickly once the controller installs the backup path).
* Duplicates caused by retransmission are discarded, so a failure never corrupts the stream.
* At the end the client sends FIN with the packet count and a **SHA-256** of the whole stream; the server confirms `OK`/`BAD`.

## Run the full demo (controller + Mininet)

```sh
# terminal 1, repo root
ryu-manager --observe-links controller/adaptive_controller.py
# terminal 2, repo root
sudo python3 udp_app/run_mininet_demo.py          # --count 1500 --rate 50 --fail-at 8 --restore-at 18
```

It uses the controller's own topology (`controller/test_support/test_topology.py`), warms up the flows with a
ping, starts the server on h2 and the client on h1, takes `s1-s2` down at `--fail-at` and up at `--restore-at`,
then runs the analyzer. If `controller/logs/events.csv` exists the report also splits the recovery time into
*failure → controller detection → backup path installed → traffic resumed*. The run lasts ~30 s, which covers
failure, reroute, link restore and the controller's 5 s fail-back hold-down.

The apps are standard-library Python 3, so they run with the system `python3` that Mininet hosts use – the
Python 3.8/3.9 virtualenv needed for Ryu is not required for `udp_app`.

## Run by hand inside Mininet

Defaults assume the usual Mininet addressing: **h1 = 10.0.0.1, h2 = 10.0.0.2**, UDP port **5005**.

```
mininet> h2 python3 udp_app/server.py --port 5005 --out received.bin --log udp_app/logs/server_log.csv &
mininet> h1 python3 udp_app/client.py --server 10.0.0.2 --port 5005 --count 1000 --rate 50 --log udp_app/logs/client_log.csv
```

While the client runs, fail a link from the Mininet CLI (Member 2's scripts do the same):

```
mininet> link s1 s2 down      # break the primary path
mininet> link s1 s2 up        # optional: restore it later
```

Then:

```
python3 udp_app/analyze.py udp_app/logs/client_log.csv udp_app/logs/server_log.csv
# with the controller's timings and the link-down marker (run_mininet_demo.py writes markers.csv):
python3 udp_app/analyze.py udp_app/logs/client_log.csv udp_app/logs/server_log.csv \
    --markers udp_app/logs/markers.csv --controller-events controller/logs/events.csv
```

(Commands are run from the repo root.) Useful client options: `--rate` (packets/s), `--size` (payload bytes, keep < 1400), `--window`, `--file <path>` (send a real file), `--max-rto`.
A 1000-packet run at 50 pkt/s lasts about 20 s – long enough to fail a link mid-transfer.

Client and server both run on the Mininet host, so their timestamps share one clock; this is what makes the one-way-delay numbers valid. Timestamps are Unix wall-clock time, so they line up with Wireshark captures (`h1 tcpdump`/`s1 tcpdump`) too.

### Quick throughput/loss cross-check with iperf (optional)

```
mininet> h2 iperf -s -u &
mininet> h1 iperf -c 10.0.0.2 -u -b 1M -t 20
```

## Test without Mininet

```
python3 udp_app/test_outage.py
```

Sends a 300 KB file through a relay that drops everything for 1 s (plus 5 % random loss) and checks that the file arrives identical and the outage and recovery time are detected.

## Interface contract with the other members

* **Controller (Member 3):** it matches IPv4 `src,dst` only, installs both directions, answers ARP itself and learns hosts from their first ARP/IPv4 packet, so any UDP port works (default 5005) and no change was needed on either side. Its logs use Unix timestamps, the same clock as this app's logs.
* **Topology (Member 2):** `h1` = client, `h2` = server, 10.0.0.1 / 10.0.0.2 as in the controller's test topology. Other IPs → `--server <ip>`. Run the OVS bridges with OpenFlow 1.3 (`protocols=OpenFlow13`), as the controller requires.
* Keep `--size` below the MTU (default 1000 B) so no IP fragmentation is involved.
* Logs are written to `udp_app/logs/` (ignored by git).

## Log formats

`logs/client_log.csv` – `ts,event,seq,value` with events `SEND, RETX, ACK, FIN, FINACK`
`logs/server_log.csv` – `ts,event,seq,value` with events `RECV, DUP, DELIVER, FIN_OK, FIN_BAD`

## Put it on your branch

From the root of the team repository (the controller files in this package are unchanged from Member 3's version,
so the diff will only show the new `udp_app/`, the root `README.md` and the `.gitignore` update):

```
git checkout main && git pull
git checkout -b member1-udp-app
# copy udp_app/, README.md and .gitignore from this package into the repo root
git add udp_app README.md .gitignore
git commit -m "Member 1: reliable UDP client/server, logging, analyzer and Mininet demo"
git push -u origin member1-udp-app
```
