#!/usr/bin/env python3
"""Reliable-UDP server (run on h2 in Mininet).

- ACKs every DATA packet (selective ACK + cumulative ACK)
- re-orders and de-duplicates, so retransmissions never corrupt the stream
- writes the stream in order to --out
- verifies the SHA-256 the client sends in FIN
- logs timestamps to CSV: ts,event,seq,value
    RECV     value = one-way delay in seconds (valid when both hosts share a
             clock, which is true in Mininet)
    DUP      duplicate arrival (retransmission of something already received)
    DELIVER  packet written in order
    FIN_OK / FIN_BAD
"""
import argparse
import csv
import hashlib
import os
import socket
import time

from protocol import ACK, DATA, DEFAULT_PORT, FIN, FINACK, pack, unpack


def serve_one(sock, args, log):
    expected = 0
    buffer = {}
    sha = hashlib.sha256()
    fin_time = None
    result = None
    out = open(args.out, "wb")
    sock.settimeout(0.2)
    try:
        while True:
            if fin_time is not None and time.time() - fin_time > args.linger:
                break
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            now = time.time()
            try:
                ptype, seq, _cum, ts, payload = unpack(data)
            except ValueError:
                continue

            if ptype == DATA:
                if seq < expected or seq in buffer:
                    log.writerow([f"{now:.6f}", "DUP", seq, ""])
                else:
                    buffer[seq] = payload
                    log.writerow([f"{now:.6f}", "RECV", seq, f"{now - ts:.6f}"])
                    while expected in buffer:
                        chunk = buffer.pop(expected)
                        out.write(chunk)
                        sha.update(chunk)
                        log.writerow([f"{now:.6f}", "DELIVER", expected, len(chunk)])
                        expected += 1
                # always ACK, even duplicates (the earlier ACK may have been lost)
                sock.sendto(pack(ACK, seq, expected, ts), addr)

            elif ptype == FIN:
                ok = (expected == seq) and (payload == sha.digest())
                sock.sendto(pack(FINACK, seq, expected, ts, b"OK" if ok else b"BAD"), addr)
                if fin_time is None:
                    fin_time = now
                    result = ok
                    log.writerow([f"{now:.6f}", "FIN_OK" if ok else "FIN_BAD", seq, expected])
                    print(f"[server] FIN: {expected}/{seq} packets delivered, "
                          f"integrity {'OK' if ok else 'FAILED'}", flush=True)
    finally:
        out.close()
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--out", default="received.bin", help="where the received stream is written")
    ap.add_argument("--log", default="logs/server_log.csv")
    ap.add_argument("--linger", type=float, default=2.0,
                    help="seconds to keep answering a retransmitted FIN after finishing")
    ap.add_argument("--forever", action="store_true", help="serve one transfer after another")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.log) or ".", exist_ok=True)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.bind, args.port))
    print(f"[server] listening on {args.bind}:{args.port}", flush=True)

    with open(args.log, "w", newline="") as f:
        log = csv.writer(f)
        log.writerow(["ts", "event", "seq", "value"])
        try:
            while True:
                serve_one(sock, args, log)
                f.flush()
                if not args.forever:
                    break
        except KeyboardInterrupt:
            pass
    sock.close()


if __name__ == "__main__":
    main()
