#!/usr/bin/env python3
"""
Start the listener but override healthcare.ris.processor.process_hl7_message
so you can implement custom business logic in Python without editing the repo files.

This script starts the server in a background thread using start_listener_thread.
"""
import time
import logging
from healthcare.ris import processor
from healthcare.ris.hl7_listener import start_listener_thread

logging.basicConfig(level=logging.INFO)

def my_process_hl7_message(message_obj):
    """
    message_obj is the hl7apy parsed message returned by hl7apy.parser.parse_message.
    The function must return True on success (ACK AA) or False on error (ACK AE).
    """
    try:
        # Simple example: print the incoming message in ER7/pipe format and do minimal checks.
        # Use str(message_obj) which returns the ER7/pipe representation in most hl7apy versions.
        print("== Received HL7 message ==")
        print(str(message_obj))
        # Example: read MSH-10 (message control id) if available
        try:
            msg_ctrl_id = message_obj.MSH.MSH10.value
            print("Message control ID:", msg_ctrl_id)
        except Exception:
            pass

        # TODO: map to your internal order model, save to DB, enqueue, etc.
        # Return True to indicate success (will cause listener to send MSA|AA)
        return True
    except Exception as e:
        # On exception, return False to indicate failure (listener sends MSA|AE)
        logging.exception("Processing failed")
        return False

# Monkeypatch the processor function used by the listener
processor.process_hl7_message = my_process_hl7_message

if __name__ == "__main__":
    server = start_listener_thread(host="0.0.0.0", port=2575)
    print("Custom listener running on 0.0.0.0:2575. Press Ctrl-C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.should_stop.set()
        print("Stopping custom listener...")
        time.sleep(0.5)