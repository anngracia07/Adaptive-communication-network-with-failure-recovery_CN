#!/usr/bin/env python3
"""Reliable-UDP client (run on h1 in Mininet).

Streams numbered packets (messages / video-frame-like chunks, or a file) at a
fixed rate to the server. Packets lost during a link failure are retransmitted
until acknowledged, so the stream arrives complete and in order once the
controller has rerouted traffic.

Recovery mechanism: sliding window + per-packet sequence numbers + selective
and cumulative ACKs + adaptive retransmission timeout (RFC 6298 style, Karn's
rule, capped exponential back-off).

Log (CSV: ts,event,seq,value):
    SEND  first transmission
    RETX  retransmission               value = transmission count
    ACK   packet acknowledged          value = RTT in seconds (blank if the
                                       packet had been retransmitted)
    FIN / FINACK
"""
import argparse
import csv
import hashlib
import os
import select
import socket
import sys
import time

from protocol import (ACK, DATA, DEFAULT_PORT, DEFAULT_SERVER_IP, FIN, FINACK,
                      make_payload, pack, unpack)


class InFlight:
    __slots__ = ("seq", "last", "tx")

    def __init__(self, seq, now):
        self.seq, self.last, self.tx = seq, now, 1


def build_chunks(args):
    if args.file:
        with open(args.file, "rb") as f:
            data = f.read()
        return [data[i:i + args.size] for i in range(0, len(data), args.size)] or [b""]
    return [make_payload(i, args.size, args.seed) for i in range(args.count)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--server", default=DEFAULT_SERVER_IP, help="server IP (Mininet h2 = 10.0.0.2)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--count", type=int, default=500, help="number of packets (stream mode)")
    ap.add_argument("--size", type=int, default=1000, help="payload bytes per packet (keep < MTU)")
    ap.add_argument("--rate", type=float, default=50.0, help="packets per second")
    ap.add_argument("--window", type=int, default=32, help="max unacknowledged packets in flight")
    ap.add_argument("--file", help="send this file instead of generated messages")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--min-rto", type=float, default=0.05)
    ap.add_argument("--max-rto", type=float, default=1.0)
    ap.add_argument("--log", default="logs/client_log.csv")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    chunks = build_chunks(args)
    total = len(chunks)
    digest = hashlib.sha256(b"".join(chunks)).digest()
    server = (args.server, args.port)

    os.makedirs(os.path.dirname(args.log) or ".", exist_ok=True)
    logf = open(args.log, "w", newline="")
    log = csv.writer(logf)
    log.writerow(["ts", "event", "seq", "value"])

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)

    inflight = {}
    next_seq = 0
    interval = 1.0 / args.rate
    next_send = time.time()
    srtt = rttvar = None
    rto = 0.2
    n_retx = 0
    start = last_report = time.time()

    def transmit(p, now):
        sock.sendto(pack(DATA, p.seq, 0, now, chunks[p.seq]), server)
        p.last = now

    try:
        while next_seq < total or inflight:
            now = time.time()
            base = min(inflight) if inflight else next_seq

            # 1) new packets, paced, limited by the window
            while next_seq < total and next_seq - base < args.window and now >= next_send:
                p = InFlight(next_seq, now)
                inflight[next_seq] = p
                transmit(p, now)
                log.writerow([f"{now:.6f}", "SEND", next_seq, ""])
                next_seq += 1
                next_send = max(next_send + interval, now)

            # 2) retransmit anything whose (backed-off) timer expired
            for p in list(inflight.values()):
                if now - p.last >= min(rto * (2 ** (p.tx - 1)), args.max_rto):
                    p.tx += 1
                    n_retx += 1
                    transmit(p, now)
                    log.writerow([f"{now:.6f}", "RETX", p.seq, p.tx])

            # 3) read ACKs
            wait = 0.005
            if next_seq < total and next_seq - base < args.window:
                wait = min(wait, max(0.0, next_send - time.time()))
            select.select([sock], [], [], wait)
            while True:
                try:
                    data, addr = sock.recvfrom(65535)
                except BlockingIOError:
                    break
                if addr[0] != server[0]:
                    continue
                now = time.time()
                try:
                    ptype, seq, cum, ts, _ = unpack(data)
                except ValueError:
                    continue
                if ptype != ACK:
                    continue
                p = inflight.pop(seq, None)
                if p is not None:
                    rtt = ""
                    if p.tx == 1:                       # Karn's rule
                        r = now - ts
                        rtt = f"{r:.6f}"
                        if srtt is None:
                            srtt, rttvar = r, r / 2
                        else:
                            rttvar = 0.75 * rttvar + 0.25 * abs(srtt - r)
                            srtt = 0.875 * srtt + 0.125 * r
                        rto = min(max(srtt + 4 * rttvar, args.min_rto), args.max_rto)
                    log.writerow([f"{now:.6f}", "ACK", seq, rtt])
                for s in [s for s in inflight if s < cum]:   # cumulative ACK
                    del inflight[s]
                    log.writerow([f"{now:.6f}", "ACK", s, ""])

            if not args.quiet and time.time() - last_report >= 1.0:
                last_report = time.time()
                print(f"[client] t={last_report - start:5.1f}s sent={next_seq}/{total} "
                      f"in_flight={len(inflight)} retx={n_retx} rto={rto * 1000:.0f}ms", flush=True)

        # all data acknowledged -> close with FIN carrying total count + SHA-256
        ok = None
        for _ in range(50):
            now = time.time()
            sock.sendto(pack(FIN, total, 0, now, digest), server)
            log.writerow([f"{now:.6f}", "FIN", total, ""])
            end = time.time() + 0.2
            while ok is None and time.time() < end:
                select.select([sock], [], [], 0.05)
                try:
                    data, addr = sock.recvfrom(65535)
                    ptype, _s, _c, _t, payload = unpack(data)
                    if ptype == FINACK:
                        ok = payload == b"OK"
                        log.writerow([f"{time.time():.6f}", "FINACK", total, payload.decode()])
                except (BlockingIOError, ValueError):
                    pass
            if ok is not None:
                break
    finally:
        logf.close()

    dur = time.time() - start
    nbytes = sum(len(c) for c in chunks)
    print(f"[client] done: {total} packets, {nbytes} bytes in {dur:.2f}s, "
          f"{n_retx} retransmissions, {nbytes * 8 / dur / 1e6:.2f} Mbit/s")
    if ok:
        print("[client] INTEGRITY OK - server confirmed the full stream")
        return 0
    print("[client] INTEGRITY FAILED or no FINACK from server")
    return 1


if __name__ == "__main__":
    sys.exit(main())
