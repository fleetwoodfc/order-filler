#!/usr/bin/env python3
"""
Start the repository's MLLP HL7 listener (uses healthcare.ris.hl7_listener.MLLPServer).
Run this inside your frappe/bench environment.
"""
import logging
import time
from healthcare.ris.hl7_listener import MLLPServer

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    server = MLLPServer()  # will pick host/port from site_config or defaults (0.0.0.0:2575)
    try:
        # start_server blocks; this matches the module's intended use
        server.start_server()
    except KeyboardInterrupt:
        server.should_stop.set()
        time.sleep(0.5)
        print("Listener stopped")
