#!/usr/bin/env python3
\"\"\"
bt_auto_connect.py

Simple, robust rfcomm auto-reconnect script.
- Attempts rfcomm bind, uses backoff and jitter on failures.
- Logs to /var/log/bt_auto_connect.log
- Intended to be run as root (or via systemd with User=root)
Configure BT_ADDR and CHANNEL below.
\"\"\"
import subprocess
import time
import logging
import os
import random

# ----- Configuration -----
BT_ADDR = "00:11:22:33:44:55"  # Replace with your device's MAC address
CHANNEL = "1"                  # Replace with SDP-discovered RFCOMM channel
RFCOMM_IDX = 0                 # /dev/rfcomm{RFCOMM_IDX}
RFCOMM_DEV = f"/dev/rfcomm{RFCOMM_IDX}"
RETRY_INTERVAL = 10            # base retry interval (seconds)
MAX_RETRIES_BEFORE_BACKOFF = 6
MAX_RETRIES = 0                # 0 = unlimited
LOGFILE = "/var/log/bt_auto_connect.log"

# ----- Logging -----
logging.basicConfig(
    filename=LOGFILE,
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

def is_bound():
    \"\"\"Return True if rfcomm device exists\"\"\"
    return os.path.exists(RFCOMM_DEV)

def bind_rfcomm():
    \"\"\"Try rfcomm bind. Return True on success\"\"\"
    try:
        subprocess.run([\"rfcomm\", \"release\", str(RFCOMM_IDX)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    try:
        subprocess.run([\"rfcomm\", \"bind\", str(RFCOMM_IDX), BT_ADDR, CHANNEL], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logging.info(f\"rfcomm bind succeeded: {RFCOMM_DEV}\")
        return True
    except subprocess.CalledProcessError as e:
        logging.warning(f\"rfcomm bind failed: {e}\")
        return False

def release_rfcomm():
    try:
        subprocess.run([\"rfcomm\", \"release\", str(RFCOMM_IDX)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logging.info(\"rfcomm released (clean)\")
    except Exception:
        pass

def auto_reconnect():
    retries = 0
    while True:
        if is_bound():
            # already bound, sleep
            logging.debug(\"Device appears bound; sleeping\")
            time.sleep(5)
            continue

        logging.info(\"Device not bound; attempt to bind...\")
        success = bind_rfcomm()
        if success:
            retries = 0
            time.sleep(2)
            continue

        retries += 1
        logging.warning(f\"Bind attempt failed (#{retries})\")\

        if MAX_RETRIES > 0 and retries >= MAX_RETRIES:
            logging.error(\"Reached MAX_RETRIES; exiting\")
            break

        backoff = min(60, RETRY_INTERVAL * (2 ** max(0, (retries // MAX_RETRIES_BEFORE_BACKOFF))))
        jitter = random.uniform(0, 2)
        sleep_time = backoff + jitter
        logging.info(f\"Sleeping for {sleep_time:.1f}s before next retry\")
        time.sleep(sleep_time)

if __name__ == '__main__':
    logging.info(\"Starting bt_auto_connect service\")
    release_rfcomm()
    try:
        auto_reconnect()
    except KeyboardInterrupt:
        logging.info(\"bt_auto_connect interrupted by user\")
        release_rfcomm()
