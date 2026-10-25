"""Shared wire format for the reliable-UDP application (Member 1).

Every datagram = fixed header + payload.

    !  B     I     I        d
       type  seq   cum_ack  timestamp

  type       DATA / ACK / FIN / FINACK
  seq        DATA: sequence number of this packet
             ACK : sequence number being acknowledged (selective ACK)
             FIN : total number of DATA packets in the stream
  cum_ack    ACK only: next in-order sequence number the server expects
             (everything below it has been delivered)
  timestamp  DATA/FIN: sender wall-clock time of this transmission
             ACK: echo of the timestamp of the DATA packet being acked
"""
import hashlib
import struct

HDR = struct.Struct("!BIId")
DATA, ACK, FIN, FINACK = 1, 2, 3, 4
DEFAULT_PORT = 5005          # the SDN controller must let UDP/5005 through
DEFAULT_SERVER_IP = "10.0.0.2"   # Mininet default IP of h2


def pack(ptype, seq, cum_ack, ts, payload=b""):
    return HDR.pack(ptype, seq, cum_ack, ts) + payload


def unpack(data):
    if len(data) < HDR.size:
        raise ValueError("short datagram")
    ptype, seq, cum_ack, ts = HDR.unpack_from(data)
    return ptype, seq, cum_ack, ts, data[HDR.size:]


def make_payload(seq, size, seed=1):
    """Deterministic message/video-frame-like payload for stream mode."""
    head = f"MSG {seq:08d} ".encode()
    block = hashlib.sha256(f"{seed}:{seq}".encode()).digest()
    body = (block * (size // len(block) + 1))[: max(0, size - len(head))]
    return (head + body)[:size]
