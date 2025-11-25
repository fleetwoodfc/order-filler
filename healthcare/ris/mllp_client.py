"""
Simple test client to send an HL7 ORM message over MLLP to the listener.
Usage:
  python healthcare/ris/mllp_client.py [host] [port]
"""

import socket
import sys

MLLP_START = b"\x0b"
MLLP_END_1 = b"\x1c"
MLLP_END_2 = b"\x0d"

SAMPLE_ORM = (
    "MSH|^~\\&|SENDER|SENDER_FAC|RECEIVER|RECEIVER_FAC|20251105||ORM^O01|MSG00001|P|2.3\r"
    "PID|1||12345^^^MRN||Doe^John||19800101|M|||123 Main St^^Metropolis^NY^10001||555-0100\r"
    "ORC|NW|ORD0001||||\r"
    "OBR|1|ORD0001||CBC^Complete Blood Count^L|||20251105\r"
)

def send_message(host, port, message):
    with socket.create_connection((host, port), timeout=5) as s:
        payload = MLLP_START + message.encode("utf-8") + MLLP_END_1 + MLLP_END_2
        s.sendall(payload)
        ack = s.recv(8192)
        print("Raw ACK bytes:", ack)
        if ack.startswith(MLLP_START) and ack.endswith(MLLP_END_2):
            inner = ack[1:-2]
            print("ACK content:\n", inner.decode("utf-8", errors="replace"))
        else:
            print("ACK framing unexpected; raw:", ack)

if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 2575
    send_message(host, port, SAMPLE_ORM)