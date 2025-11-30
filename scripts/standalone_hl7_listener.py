#!/usr/bin/env python3
"""
Standalone MLLP HL7 listener (no Frappe dependency).

- Uses hl7apy for parsing: pip install hl7apy
- Default host: 0.0.0.0
- Default port: 2575
- Usage:
    python examples/standalone_hl7_listener.py [host] [port]

This is a drop-in lightweight replacement for healthcare/ris/hl7_listener.py
when you want to run the listener in a plain Python environment.
"""
import socket
import threading
import logging
import time
import os
from datetime import datetime
from typing import Callable, Optional

from hl7apy.parser import parse_message

logger = logging.getLogger("standalone_hl7_listener")
logger.setLevel(logging.INFO)


MLLP_START = b"\x0b"  # VT
MLLP_END_1 = b"\x1c"  # FS
MLLP_END_2 = b"\x0d"  # CR

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 2575


def get_config():
    """Get host/port from environment variables or use defaults."""
    host = os.environ.get("HL7_LISTENER_HOST", DEFAULT_HOST)
    port = int(os.environ.get("HL7_LISTENER_PORT", DEFAULT_PORT))
    return host, port


class MLLPServer(threading.Thread):
    """
    MLLPServer accepts connections and calls a handler for each HL7 message.

    handler: Callable[[hl7_payload_str, client_addr], bool]
      - Should return True on success (ACK AA) or False on failure (ACK AE).
      - If handler raises, the server will log and send AE.
    """

    def __init__(self, host: Optional[str] = None, port: Optional[int] = None, handler: Optional[Callable] = None):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.sock = None
        self.should_stop = threading.Event()
        # default handler: prints message and returns True
        self.handler = handler or self.default_handler

    def default_handler(self, message_obj, client_addr):
        try:
            print("=== Received HL7 message from", client_addr, "===\n")
            print(str(message_obj))
            # default success
            return True
        except Exception:
            logger.exception("Default handler failed")
            return False

    def run(self):
        self.start_server()

    def start_server(self):
        host, port = (self.host, self.port) if (self.host and self.port) else get_config()
        logger.info("Starting standalone MLLP HL7 listener on %s:%s", host, port)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            s.listen(5)
            self.sock = s
            while not self.should_stop.is_set():
                try:
                    s.settimeout(1.0)
                    conn, addr = s.accept()
                except socket.timeout:
                    continue
                logger.info("Accepted connection from %s", addr)
                threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()

    def handle_client(self, conn: socket.socket, addr):
        with conn:
            buffer = b""
            while not self.should_stop.is_set():
                try:
                    data = conn.recv(8192)
                except ConnectionResetError:
                    break
                if not data:
                    break
                buffer += data
                while True:
                    start = buffer.find(MLLP_START)
                    if start == -1:
                        break
                    end = buffer.find(MLLP_END_1, start + 1)
                    if end == -1 or end + 1 >= len(buffer):
                        # incomplete; wait for more bytes
                        break
                    # extract message between VT and FS (skip trailing CR)
                    hl7_payload = buffer[start + 1 : end]
                    # consume bytes including the trailing CR
                    buffer = buffer[end + 2 :]
                    try:
                        self.process_hl7_message(hl7_payload, conn, addr)
                    except Exception:
                        logger.exception("Failed to process HL7 message")

    def process_hl7_message(self, payload_bytes: bytes, conn: socket.socket, addr):
        try:
            payload = payload_bytes.decode("utf-8", errors="replace")
            logger.info("Received HL7 payload from %s:\n%s", addr, payload)
            message = parse_message(payload, validation_level=0)
            try:
                success = bool(self.handler(message, addr))
            except Exception:
                logger.exception("Handler raised exception")
                success = False

            ack_text = "AA" if success else "AE"
            ack_msg = self.build_ack(message, ack_text=ack_text)
            framed = MLLP_START + ack_msg.encode("utf-8") + MLLP_END_1 + MLLP_END_2
            try:
                conn.sendall(framed)
                logger.info("Sent ACK (%s) to %s", ack_text, addr)
            except Exception:
                logger.exception("Failed sending ACK")
        except Exception:
            logger.exception("Error processing incoming HL7 payload")

    def build_ack(self, incoming_msg, ack_text="AA"):
        """Build a simple MSH+MSA ACK; forgiving if incoming message missing segments."""
        try:
            msh = incoming_msg.MSH
            sending_app = msh.MSH3.value if hasattr(msh, "MSH3") else "SENDER"
            sending_fac = msh.MSH4.value if hasattr(msh, "MSH4") else ""
            recv_app = msh.MSH5.value if hasattr(msh, "MSH5") else "RECEIVER"
            recv_fac = msh.MSH6.value if hasattr(msh, "MSH6") else ""
            msg_control_id = msh.MSH10.value if hasattr(msh, "MSH10") else ""
            ts = datetime.now().strftime("%Y%m%d%H%M%S")
            ack_lines = [
                f"MSH|^~\\&|{recv_app}|{recv_fac}|{sending_app}|{sending_fac}|{ts}||ACK|{msg_control_id}|P|2.3",
                f"MSA|{ack_text}|{msg_control_id}",
            ]
            return "\r".join(ack_lines) + "\r"
        except Exception:
            # fallback minimal ACK
            return "MSH|^~\\&||||||ACK||P|2.3\rMSA|AE|\r"


def start_listener_thread(host: Optional[str] = None, port: Optional[int] = None, handler: Optional[Callable] = None):
    server = MLLPServer(host=host, port=port, handler=handler)
    server.start()
    return server


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    host = sys.argv[1] if len(sys.argv) > 1 else None
    port = int(sys.argv[2]) if len(sys.argv) > 2 else None

    server = MLLPServer(host=host, port=port)
    try:
        server.start_server()
    except KeyboardInterrupt:
        server.should_stop.set()
        time.sleep(0.5)
        print("Listener stopped")