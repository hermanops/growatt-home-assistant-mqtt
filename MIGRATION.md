Migrating an existing install
----

This file is only for installs created before November 2026. A fresh install
follows [README.md](README.md) and needs nothing here.

Migrate in two steps: first the service (new venv-based unit), then the MQTT
topics (state topics and Home Assistant devices now include the inverter name).

Migrating from the old unit (pip at every start)
----
Older versions of `solarmon.service` ran `pip install` as root in
`ExecStartPre` on every start. To migrate:

```bash
# Host: solarmon (Pi)
cd /home/pi/solarmon
chown -R pi:pi /home/pi/solarmon              # if git was ever run as root
sudo -u pi git pull --ff-only
sudo -u pi python3 -m venv .venv
sudo -u pi .venv/bin/pip install --disable-pip-version-check -r requirements.txt
install -m 0644 solarmon.service /etc/systemd/system/solarmon.service
ls -l /etc/systemd/system/solarmon.service.d/ 2>/dev/null  # inspect drop-ins first
systemctl daemon-reload
systemctl restart solarmon
systemctl cat solarmon                        # check the effective unit
```

Drop-ins in `/etc/systemd/system/solarmon.service.d/` override the new unit.
Remove only a legacy drop-in that you no longer need, for example an
`override.conf` that only sets `WatchdogSec=0`, `Type=simple` or
`NotifyAccess=none`:
```bash
# Host: solarmon (Pi), as root
cat /etc/systemd/system/solarmon.service.d/override.conf
rm /etc/systemd/system/solarmon.service.d/override.conf
systemctl daemon-reload && systemctl restart solarmon
```

The old unit installed `pymodbus` and `paho-mqtt` with root pip into the
system interpreter. The new service does not use them. Leaving them installed
is safe. Remove them only if they are in `/usr/local` (installed by pip, not
by apt) and no other application on the host uses them:
```bash
# Host: solarmon (Pi), as root
python3 -m pip show pymodbus paho-mqtt | grep -E '^(Name|Location|Required-by)'
# Only if Location is /usr/local/... and Required-by is empty:
python3 -m pip uninstall pymodbus paho-mqtt
```

Migrating MQTT topics and Home Assistant entities
----
State topics now include the inverter name, even for one inverter; update any
MQTT subscribers using the old topics. Home Assistant discovers new devices
and entities, so dashboards and automations using the old entities must be
updated.

Old retained values and discovery configs are not removed automatically.
Publish an empty retained message to clean them up:

- Old state topics: `<base_topic>/<sensor>`
- Old discovery configs: `<ha_prefix>/sensor/<device_id>_<old_sensor_suffix>/config`

Use the old `base_topic`, `ha_prefix` and `device_id` if those have also
changed. See the README for the current topic layout and for removing
discovery configs of renamed or removed sensors.
