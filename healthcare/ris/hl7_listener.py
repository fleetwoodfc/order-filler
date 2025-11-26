"""
MLLP HL7 listener updated to use pyHL7 (the `hl7` package) for parsing.

Notes:
- Requires pyHL7: pip install hl7
- Still compatible with Frappe bench/site environment (imports frappe).
- The listener passes both the parsed pyHL7 message and the raw message text to the processor.
- ACK construction is done from the raw MSH line (robust against parser differences).
"""
import socket
import threading
import logging
import time
from typing import Optional

import hl7  # pyHL7 (pip package name: hl7)

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


def _find_msh_line(payload: str) -> Optional[str]:
    """
    Return the first MSH line from the payload (ER7, lines delimited by \r).
    """
    try:
        for line in payload.split("\r"):
            if line.startswith("MSH"):
                return line
    except Exception:
        pass
    return None


def _build_ack_from_msh_line(msh_line: Optional[str], ack_text: str = "AA") -> str:
    """
    Build a simple MSH+MSA ACK using values parsed from the MSH ER7 line.
    Falls back to defaults when fields are missing.
    """
    try:
        if not msh_line:
            # minimal ACK
            return "MSH|^~\\&||||||ACK||P|2.3\rMSA|AE|\r"

        fields = msh_line.split("|")
        # fields[0] == 'MSH'
        # MSH-3 (sending application) -> fields[2]
        # MSH-4 (sending facility) -> fields[3]
        # MSH-5 (receiving application) -> fields[4]
        # MSH-6 (receiving facility) -> fields[5]
        # MSH-10 (message control id) -> fields[9]
        sending_app = fields[2] if len(fields) > 2 else "SENDER"
        sending_fac = fields[3] if len(fields) > 3 else ""
        recv_app = fields[4] if len(fields) > 4 else "RECEIVER"
        recv_fac = fields[5] if len(fields) > 5 else ""
        msg_control_id = fields[9] if len(fields) > 9 else ""
        ts = frappe.utils.now_datetime().strftime("%Y%m%d%H%M%S")
        ack_lines = [
            f"MSH|^~\\&|{recv_app}|{recv_fac}|{sending_app}|{sending_fac}|{ts}||ACK|{msg_control_id}|P|2.3",
            f"MSA|{ack_text}|{msg_control_id}",
        ]
        return "\r".join(ack_lines) + "\r"
    except Exception:
        return "MSH|^~\\&||||||ACK||P|2.3\rMSA|AE|\r"


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

            # Parse using pyHL7
            try:
                parsed = hl7.parse(payload)
            except Exception:
                # If parsing fails, parsed will be None and we still pass raw text to processor
                parsed = None

            # delegate processing; processor will accept either pyHL7 message or raw text
            from healthcare.ris.processor import process_hl7_message

            try:
                success = process_hl7_message(parsed, raw_message_text=payload)
            except TypeError:
                # older processor signature may expect only a parsed message
                success = process_hl7_message(parsed)

            # build ACK from raw MSH line for robustness
            msh_line = _find_msh_line(payload)
            ack_text = "AA" if success else "AE"
            ack_msg = _build_ack_from_msh_line(msh_line, ack_text=ack_text)
            framed = MLLP_START + ack_msg.encode("utf-8") + MLLP_END_1 + MLLP_END_2
            try:
                conn.sendall(framed)
                logger.info("Sent ACK (%s) to %s", ack_text, conn)
            except Exception:
                logger.exception("Failed sending ACK")
        except Exception:
            logger.exception("Error processing incoming HL7 payload")


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