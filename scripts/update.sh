#!/usr/bin/env bash
# Update solarmon: git pull, sync venv deps, restart the service.
#
# Run as the user that owns the checkout (e.g. pi), NOT as root:
#   /home/pi/solarmon/scripts/update.sh
#
# This script never installs the systemd unit. The checkout is writable by
# an unprivileged user, so copying its unit file into /etc as root would let
# that user gain root. If the unit in the repo changed, the script shows the
# diff; review it and install it yourself as root.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT=solarmon.service
UNIT_DST="/etc/systemd/system/${UNIT}"

if [[ $EUID -eq 0 ]]; then
    echo "Do not run as root. Run as the owner of $APP_DIR." >&2
    exit 1
fi

git -C "$APP_DIR" pull --ff-only

if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
    python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install --disable-pip-version-check --no-input \
    -r "$APP_DIR/requirements.txt"
"$APP_DIR/.venv/bin/python" -c "import pymodbus, serial, paho.mqtt"

if ! cmp -s "$APP_DIR/$UNIT" "$UNIT_DST"; then
    echo
    echo "WARNING: $UNIT in the repo differs from $UNIT_DST:"
    diff -uN "$UNIT_DST" "$APP_DIR/$UNIT" || true
    echo
    echo "Review the diff. To install it, run as root:"
    echo "  install -m 0644 -o root -g root $APP_DIR/$UNIT $UNIT_DST && systemctl daemon-reload"
    echo
fi

sudo systemctl restart "$UNIT"
systemctl --no-pager --lines=10 status "$UNIT"
