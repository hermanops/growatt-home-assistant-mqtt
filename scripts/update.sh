#!/usr/bin/env bash
# Update solarmon: git pull, sync venv deps, reinstall unit if changed, restart.
# Run as root on the Pi:  /home/pi/solarmon/scripts/update.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/home/pi/solarmon}"
APP_USER="${APP_USER:-pi}"
UNIT=solarmon.service
UNIT_DST="/etc/systemd/system/${UNIT}"

[[ $EUID -eq 0 ]] || { echo "Run as root." >&2; exit 1; }

sudo -u "$APP_USER" git -C "$APP_DIR" pull --ff-only

if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
    sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
fi
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --disable-pip-version-check --no-input \
    -r "$APP_DIR/requirements.txt"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" -c "import pymodbus, serial, paho.mqtt"

if ! cmp -s "$APP_DIR/$UNIT" "$UNIT_DST"; then
    install -m 0644 -o root -g root "$APP_DIR/$UNIT" "$UNIT_DST"
    systemctl daemon-reload
    echo "Unit file updated."
fi

systemctl restart "$UNIT"
systemctl --no-pager --lines=10 status "$UNIT"
