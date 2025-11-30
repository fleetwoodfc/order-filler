#!/usr/bin/env python3
"""
Send the repository sample ORM message to a running listener.
"""
from healthcare.ris import mllp_client

if __name__ == "__main__":
    host = "127.0.0.1"
    port = 2575
    # mllp_client.SAMPLE_ORM is defined in the repo; send_message will frame with MLLP and print the ACK
    mllp_client.send_message(host, port, mllp_client.SAMPLE_ORM)