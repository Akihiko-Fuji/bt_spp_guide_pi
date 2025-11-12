#!/usr/bin/env python3
\"\"\"event_reconnect.py

A dbus-next based example that listens for BlueZ Device1 PropertiesChanged signals
and attempts rfcomm bind when the target device becomes available or disconnects.

Dependencies: pip3 install dbus-next
Run as root (or via sudo) because it runs rfcomm.
Configure TARGET_MAC and CHANNEL.
\"\"\"
import asyncio
import subprocess
import logging
import os
from dbus_next.aio import MessageBus
from dbus_next.constants import BusType, MessageType
from dbus_next import Variant

# Config
TARGET_MAC = "00:11:22:33:44:55"  # Replace with your device MAC
CHANNEL = "1"
RFCOMM_IDX = 0
RFCOMM_DEV = f"/dev/rfcomm{RFCOMM_IDX}"
LOGFILE = "/var/log/event_reconnect.log"

logging.basicConfig(filename=LOGFILE, level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

async def main():
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    await bus.add_match("type='signal',interface='org.freedesktop.DBus.Properties',member='PropertiesChanged'")
    logging.info("Connected to system bus, listening for PropertiesChanged signals (BlueZ)")
    while True:
        msg = await bus.wait_for_message()
        try:
            if msg.message_type != MessageType.SIGNAL:
                continue
            if msg.interface != 'org.freedesktop.DBus.Properties' or msg.member != 'PropertiesChanged':
                continue
            # msg.path looks like /org/bluez/hci0/dev_XX_XX_...
            if TARGET_MAC.replace(':', '_').upper() not in msg.path.upper():
                continue
            body = msg.body
            if not body or len(body) < 2:
                continue
            interface = body[0]
            changed = body[1]
            if interface == 'org.bluez.Device1':
                if 'Connected' in changed:
                    val = changed['Connected']
                    if isinstance(val, bool):
                        connected = val
                    elif hasattr(val, 'value'):
                        connected = bool(val.value)
                    else:
                        connected = bool(val)
                    logging.info(f"Device {TARGET_MAC} Connected -> {connected}")
                    if not connected:
                        logging.info("Device disconnected; attempting rfcomm bind")
                        try:
                            subprocess.run(["rfcomm", "bind", str(RFCOMM_IDX), TARGET_MAC, CHANNEL], check=True)
                            logging.info("rfcomm bind succeeded after disconnect event")
                        except Exception as e:
                            logging.warning(f"rfcomm bind failed after disconnect event: {e}")
        except Exception as e:
            logging.warning(f"Error handling message: {e}")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
