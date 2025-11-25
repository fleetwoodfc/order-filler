"""
Simple MLLP HL7 listener for receiving ORM messages and forwarding to processor.

Notes:
- Requires hl7apy: pip install hl7apy
- Run under bench/site environment (so frappe import works).
- Configurable host/port via site_config.json keys:
    HL7_LISTENER_HOST (default 0.0.0.0)
    HL7_LISTENER_PORT (default 2575)
- Listener auto-sends an application ACK after processing.
"""

import socket
import threading
import logging
import time

from hl7apy.parser import parse_message

import frappe

logger = logging.getLogger("healthcare.ris.hl7_listener")
logger.setLevel(logging.INFO)

MLLP_START = b"\x0b"  # VT
MLLP_END_1 = b"\x1c"  # FS
MLLP_END_2 = b"\x0d"  # CR

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 2575

def get_config():
    site_config = {}
    try:
        site_config = frappe.get_site_config() or {}
    except Exception:
        site_config = {}
    host = site_config.get("HL7_LISTENER_HOST") or DEFAULT_HOST
    port = int(site_config.get("HL7_LISTENER_PORT") or DEFAULT_PORT)
    return host, port


class MLLPServer(threading.Thread):
    def __init__(self, host=None, port=None):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.sock = None
        self.should_stop = threading.Event()

    def run(self):
        self.start_server()

    def start_server(self):
        host, port = (self.host, self.port) if (self.host and self.port) else get_config()
        logger.info("Starting MLLP HL7 listener on %s:%s", host, port)
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
                threading.Thread(
                    target=self.handle_client, args=(conn, addr), daemon=True
                ).start()

    def handle_client(self, conn, addr):
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
                        self.process_hl7_message(hl7_payload, conn)
                    except Exception:
                        logger.exception("Failed to process HL7 message")

    def process_hl7_message(self, payload_bytes, conn):
        try:
            payload = payload_bytes.decode("utf-8", errors="replace")
            logger.info("Received HL7 payload:\n%s", payload)
            message = parse_message(payload, validation_level=0)
            # delegate processing
            from healthcare.ris.processor import process_hl7_message

            success = process_hl7_message(message)

            # build ACK
            ack_text = "AA" if success else "AE"
            ack_msg = self.build_ack(message, ack_text=ack_text)
            framed = MLLP_START + ack_msg.encode("utf-8") + MLLP_END_1 + MLLP_END_2
            try:
                conn.sendall(framed)
                logger.info("Sent ACK (%s) to %s", ack_text, conn)
            except Exception:
                logger.exception("Failed sending ACK")
        except Exception:
            logger.exception("Error processing incoming HL7 payload")

    def build_ack(self, incoming_msg, ack_text="AA"):
        try:
            msh = incoming_msg.MSH
            sending_app = msh.MSH3.value if hasattr(msh, "MSH3") else "RIS"
            sending_fac = msh.MSH4.value if hasattr(msh, "MSH4") else ""
            recv_app = msh.MSH5.value if hasattr(msh, "MSH5") else "OUR_APP"
            recv_fac = msh.MSH6.value if hasattr(msh, "MSH6") else ""
            msg_control_id = msh.MSH10.value if hasattr(msh, "MSH10") else ""
            ts = frappe.utils.now_datetime().strftime("%Y%m%d%H%M%S")
            ack_lines = [
                f"MSH|^~\\&|{recv_app}|{recv_fac}|{sending_app}|{sending_fac}|{ts}||ACK|{msg_control_id}|P|2.3",
                f"MSA|{ack_text}|{msg_control_id}",
            ]
            return "\r".join(ack_lines) + "\r"
        except Exception:
            return "MSH|^~\\&||||||ACK||P|2.3\rMSA|AE|\r"


def start_listener_thread(host=None, port=None):
    server = MLLPServer(host=host, port=port)
    server.start()
    return server

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    server = MLLPServer()
    try:
        server.start_server()
    except KeyboardInterrupt:
        server.should_stop.set()
        time.sleep(0.5)