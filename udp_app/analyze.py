#!/usr/bin/env python3
"""Compute recovery time, packet loss and latency from the client/server logs.

    python3 analyze.py logs/client_log.csv logs/server_log.csv \
        [--markers logs/markers.csv] [--controller-events ../controller/logs/events.csv]

Definitions
  recovery time   an *outage* is a gap with no ACK arriving for longer than
                  --gap-threshold seconds. Recovery time = gap start (last ACK
                  before the failure) -> first ACK after it, i.e. how long the
                  data flow was stalled until the controller's backup path
                  carried traffic again.
  packet loss     forward-path loss = transmissions sent - transmissions that
                  reached the server (needs the server log); the client-side
                  estimate is retransmissions / transmissions.
                  residual loss = packets never acknowledged (should be 0).
  latency         RTT of packets that were never retransmitted (Karn),
                  delivery latency = first send -> first ACK (includes the
                  outage for packets that were in flight), and one-way delay
                  from the server log (hosts share a clock in Mininet).
"""
import argparse
import csv
import json
import statistics


def read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def pct(values, q):
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def stats_ms(values):
    if not values:
        return None
    return {"min": min(values) * 1000, "mean": statistics.mean(values) * 1000,
            "p95": pct(values, 0.95) * 1000, "max": max(values) * 1000, "n": len(values)}


def correlate(outages, markers_path=None, controller_events=None):
    """Attach link-down marker and SDN-controller timings to each outage.

    markers_path       CSV ts,event (run_mininet_demo.py writes link_down/link_up)
    controller_events  controller/logs/events.csv written by the Ryu app
                       (timestamp_epoch,event_type,dpid/link,detail)
    All timestamps are Unix time; Mininet hosts and Ryu share the machine clock.
    """
    downs = [float(r["ts"]) for r in read(markers_path) if r["event"] == "link_down"] if markers_path else []
    events = []
    if controller_events:
        for r in read(controller_events):
            events.append((float(r["timestamp_epoch"]), r["event_type"], r["detail"]))
        events.sort()
    for o in outages:
        ref = o["start"]
        marker = next((m for m in downs if o["start"] - 2.0 <= m <= o["end"]), None)
        if marker is not None:
            ref = marker
            o["link_down_ts"] = marker
            o["link_down_to_resume_s"] = o["end"] - marker
        det = next((e for e in events if ref - 1.0 <= e[0] <= o["end"] and e[1] == "detection_source"), None)
        if det:
            o["controller_detect_source"] = det[2]
            o["failure_to_detect_s"] = det[0] - ref
            inst = next((e for e in events if det[0] <= e[0] <= o["end"] + 1.0 and e[1] == "path_installed"), None)
            if inst:
                o["detect_to_path_installed_s"] = inst[0] - det[0]
                o["path_installed_to_resume_s"] = o["end"] - inst[0]
    return outages


def analyze(client_log, server_log=None, gap_threshold=0.5, markers=None, controller_events=None):
    rows = read(client_log)
    first_send, first_ack, rtts = {}, {}, []
    ack_times, retx_times = [], []
    n_send = n_retx = 0
    for r in rows:
        ts, ev, seq = float(r["ts"]), r["event"], int(r["seq"]) if r["seq"] else -1
        if ev == "SEND":
            n_send += 1
            first_send[seq] = ts
        elif ev == "RETX":
            n_retx += 1
            retx_times.append(ts)
        elif ev == "ACK":
            ack_times.append(ts)
            first_ack.setdefault(seq, ts)
            if r["value"]:
                rtts.append(float(r["value"]))

    ack_times.sort()
    outages = []
    for a, b in zip(ack_times, ack_times[1:]):
        if b - a > gap_threshold:
            outages.append({
                "start": a, "end": b, "recovery_time_s": b - a,
                "retransmissions_during": sum(1 for t in retx_times if a <= t <= b),
            })

    delivery = [first_ack[s] - first_send[s] for s in first_send if s in first_ack]
    total_tx = n_send + n_retx
    res = {
        "packets": n_send,
        "retransmissions": n_retx,
        "total_transmissions": total_tx,
        "residual_loss_packets": n_send - len([s for s in first_send if s in first_ack]),
        "client_loss_estimate_pct": 100.0 * n_retx / total_tx if total_tx else 0.0,
        "duration_s": (ack_times[-1] - min(first_send.values())) if ack_times and first_send else 0.0,
        "rtt_ms": stats_ms(rtts),
        "delivery_latency_ms": stats_ms(delivery),
        "outages": outages,
    }

    correlate(outages, markers, controller_events)

    if server_log:
        srows = read(server_log)
        recv = [r for r in srows if r["event"] == "RECV"]
        dup = [r for r in srows if r["event"] == "DUP"]
        arrived = len(recv) + len(dup)
        res["server"] = {
            "unique_received": len(recv),
            "duplicates": len(dup),
            "forward_loss_pct": 100.0 * (total_tx - arrived) / total_tx if total_tx else 0.0,
            "one_way_delay_ms": stats_ms([float(r["value"]) for r in recv]),
            "integrity": next((r["event"] for r in srows if r["event"].startswith("FIN_")), None),
        }
    return res


def fmt(s):
    return "n/a" if not s else (f"min {s['min']:.2f} / mean {s['mean']:.2f} / "
                                 f"p95 {s['p95']:.2f} / max {s['max']:.2f}  (n={s['n']})")


def report(res):
    print("=== Adaptive UDP recovery report ===")
    print(f"packets sent            : {res['packets']}")
    print(f"retransmissions         : {res['retransmissions']}")
    print(f"client loss estimate    : {res['client_loss_estimate_pct']:.2f} %  (retx / transmissions)")
    print(f"residual loss           : {res['residual_loss_packets']} packets never acknowledged")
    print(f"transfer duration       : {res['duration_s']:.2f} s")
    print(f"RTT (ms)                : {fmt(res['rtt_ms'])}")
    print(f"delivery latency (ms)   : {fmt(res['delivery_latency_ms'])}")
    if "server" in res:
        s = res["server"]
        print(f"server unique/dup       : {s['unique_received']} / {s['duplicates']}")
        print(f"forward-path loss       : {s['forward_loss_pct']:.2f} %  (transmissions that never reached the server)")
        print(f"one-way delay (ms)      : {fmt(s['one_way_delay_ms'])}")
        print(f"stream integrity        : {s['integrity']}")
    if res["outages"]:
        print(f"outages detected        : {len(res['outages'])}")
        for i, o in enumerate(res["outages"], 1):
            print(f"  #{i}: recovery time {o['recovery_time_s']:.3f} s "
                  f"({o['retransmissions_during']} retransmissions while stalled)")
            if "link_down_to_resume_s" in o:
                print(f"       link-down command -> traffic resumed : {o['link_down_to_resume_s']:.3f} s")
            if "failure_to_detect_s" in o:
                print(f"       failure -> controller detection ({o['controller_detect_source']}) : "
                      f"{o['failure_to_detect_s'] * 1000:.1f} ms")
            if "detect_to_path_installed_s" in o:
                print(f"       detection -> backup path installed   : {o['detect_to_path_installed_s'] * 1000:.1f} ms")
                print(f"       backup path installed -> resumed     : {o['path_installed_to_resume_s'] * 1000:.1f} ms "
                      f"(includes the client's retransmission wait)")
    else:
        print("outages detected        : none (no ACK gap above threshold)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("client_log")
    ap.add_argument("server_log", nargs="?")
    ap.add_argument("--gap-threshold", type=float, default=0.5,
                    help="seconds without any ACK that count as an outage (default 0.5)")
    ap.add_argument("--markers", help="CSV ts,event with link_down/link_up times (from run_mininet_demo.py)")
    ap.add_argument("--controller-events",
                    help="controller/logs/events.csv from the Ryu controller, to split recovery into "
                         "detection / rule install / client resume")
    ap.add_argument("--json", help="also write the results to this JSON file")
    args = ap.parse_args()
    res = analyze(args.client_log, args.server_log, args.gap_threshold,
                  args.markers, args.controller_events)
    report(res)
    if args.json:
        with open(args.json, "w") as f:
            json.dump(res, f, indent=2)


if __name__ == "__main__":
    main()
