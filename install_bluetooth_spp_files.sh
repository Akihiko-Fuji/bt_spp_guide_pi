#!/bin/bash
set -euo pipefail
# install_bluetooth_spp_files.sh
# Usage: sudo bash install_bluetooth_spp_files.sh
PKG_DIR="/mnt/data/bt_spp_package"
if [ "$(id -u)" -ne 0 ]; then
  echo "Please run as root (sudo)"
  exit 1
fi
# Backup targets if present
BACKUP_DIR="/root/bt_spp_backup_$(date +%Y%m%d_%H%M%S)"
mkdir -p "${BACKUP_DIR}"
for f in /usr/local/bin/bt_auto_connect.py /etc/systemd/system/bt-auto-connect.service /etc/bluetooth/rfcomm.conf /usr/local/bin/event_reconnect.py; do
  if [ -e "$f" ]; then
    echo "Backing up $f -> ${BACKUP_DIR}"
    mkdir -p "${BACKUP_DIR}/$(dirname "$f")"
    cp -a "$f" "${BACKUP_DIR}/$(basename "$f").bak"
  fi
done
# Copy files into place
echo "Installing files..."
cp "${PKG_DIR}/bt_auto_connect.py" /usr/local/bin/bt_auto_connect.py
cp "${PKG_DIR}/event_reconnect.py" /usr/local/bin/event_reconnect.py
cp "${PKG_DIR}/bt-auto-connect.service" /etc/systemd/system/bt-auto-connect.service
cp "${PKG_DIR}/rfcomm.conf" /etc/bluetooth/rfcomm.conf
chmod 755 /usr/local/bin/bt_auto_connect.py /usr/local/bin/event_reconnect.py
# Reload systemd and enable service
systemctl daemon-reload
systemctl enable bt-auto-connect.service
systemctl start bt-auto-connect.service
echo "Done. Inspect logs: sudo journalctl -u bt-auto-connect -f"
echo "If using event_reconnect.py, install dbus-next: pip3 install dbus-next and run it via systemd (not included by default)"
