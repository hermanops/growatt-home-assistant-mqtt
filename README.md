Growatt Home Assistant MQTT
----
A Python application (Solarmon) that reads Growatt PV inverters over RS485/Modbus RTU and publishes telemetry to MQTT, with optional Home Assistant MQTT discovery.

[Protocol Documentation](docs/README.md)

How to use
----
- A Linux-based host running Python 3.9+ (for example, a Raspberry Pi)
- Connect the host to the inverter's RS485 port with an RS485-to-USB adapter
- Copy `solarmon.cfg.example` to `solarmon.cfg` and modify the config values to your setup as needed
- Install as a systemd service (recommended): see [Installation](#installation-raspberry-pi--debian)
- For a quick test without the service:
  ```bash
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
  .venv/bin/python solarmon.py
  ```
- Run the unit tests:
  ```bash
  # Host: development machine, from the repository root
  .venv/bin/python -m unittest discover -s tests -v
  ```

MQTT / Home Assistant
----
Telemetry is published to MQTT only; configure the `[mqtt]` section in `solarmon.cfg`:

| Key | Default | Description |
|---|---|---|
| `enable` | `false` | Enable MQTT publishing; the example config sets this to `true` |
| `host` / `port` | `127.0.0.1` / `1883` | Broker address |
| `username` / `password` | – | Broker credentials (optional) |
| `base_topic` | `solar/inverter` | State topics are `<base_topic>/<inverter name>/<sensor>`; shared availability on `<base_topic>/availability` |
| `retain` | `true` | Retain state messages |
| `ha_discovery` | `true` | Publish Home Assistant MQTT discovery configs |
| `ha_prefix` | `homeassistant` | HA discovery prefix |
| `device_id` / `device_name` | `solar_inverter` / `Solar Inverter` | Base for per-inverter HA device identities and names |

Each `[inverters.<name>]` section has its own MQTT state topics and Home
Assistant device. The name must be non-empty and cannot contain `/`, `+`, `#`
or a null character. The shared retained availability topic starts `offline`
until a poll succeeds, then is `online` if at least one inverter responds in
the latest poll cycle, or `offline` if none do. On MQTT reconnect the latest
polling status is republished; the MQTT last will also reports `offline` on
an unexpected disconnect. Home Assistant marks the sensors unavailable when
offline. Availability is shared, not per inverter: if one inverter responds,
the others' retained sensor values may be stale.

**Migration:** State topics now include the inverter name, even for one
inverter; update any MQTT subscribers using the old topics. Home Assistant
discovers new devices and entities, so dashboards and automations using the old
entities must be updated. Remove old retained discovery configs as described
below; old retained state values at `<base_topic>/<sensor>` may also be
removed by publishing an empty retained message to each old state topic.

The `fault` and `fault_code` sensors report the decoded fault description and
numeric code. They update only when the fault register block is read
successfully; retained values may otherwise be stale.

Home Assistant discovery configs are retained independently of the `retain` setting.
Renaming or removing a sensor does not remove its old discovery config, so its
entity may remain in Home Assistant. To remove it, use an MQTT client to publish
an empty retained message to the old discovery topic:
`<ha_prefix>/sensor/<device_id>_<encoded_inverter_name>_<old_sensor_suffix>/config`.
The inverter name is encoded as UTF-8 bytes in hexadecimal (for example,
`roof` becomes `726f6f66`). For discovery configs created before this change,
the topic is `<ha_prefix>/sensor/<device_id>_<old_sensor_suffix>/config`.
Use the old `ha_prefix` and `device_id` if those have also changed.

With paho-mqtt 2.x, specify the callback API version when creating a client.
Omitting it uses the deprecated VERSION1 API and emits a deprecation warning.
VERSION2 callbacks have different signatures; for example, `on_connect` takes
`(client, userdata, flags, reason_code, properties)`. Make sure callback
signatures match the selected API version. This project already selects
VERSION2.

When upgrading this setup on Raspberry Pi OS 12 (Bookworm), Python 3.11 allows
using pymodbus 3.10 or later. Those versions require changing the Modbus calls
from `slave=` to `device_id=`; the current pymodbus 3.8.6 setup still uses
`slave=`.

Reading from Multiple Units
----
To read from multiple units add a new section to the `solarmon.cfg` config with the unit's id.
```ini
[inverters.<name>]
unit = <id>
```
Example:
```ini
[inverters.unit2]
unit = 2
```

Use your MQTT broker and client (for example Home Assistant MQTT discovery) to view inverter telemetry.

Installation (Raspberry Pi / Debian)
----
The service runs as `pi` from a virtualenv owned by `pi`. Dependencies are
pinned in `requirements.txt` and are **never** installed automatically at
service start; updates arrive as Dependabot PRs and are applied with
`scripts/update.sh`. Commands below run on the Pi as root.

```bash
# Host: solarmon (Pi)
apt install -y git python3-venv
usermod -aG dialout pi                        # serial port access
sudo -u pi git clone https://github.com/hermanops/growatt-home-assistant-mqtt /home/pi/solarmon
cd /home/pi/solarmon
sudo -u pi cp solarmon.cfg.example solarmon.cfg
sudo -u pi nano solarmon.cfg                  # port, unit, MQTT settings
chmod 0640 solarmon.cfg                       # contains MQTT credentials

sudo -u pi python3 -m venv .venv
sudo -u pi .venv/bin/pip install --disable-pip-version-check -r requirements.txt
sudo -u pi .venv/bin/python -c "import pymodbus, serial, paho.mqtt; print(pymodbus.__version__)"

install -m 0644 solarmon.service /etc/systemd/system/solarmon.service
systemctl daemon-reload
systemctl enable --now solarmon
systemctl status solarmon --no-pager
systemd-analyze security solarmon | tail -1
```

If your paths or user differ, edit `User`, `Group`, `WorkingDirectory` and
`ExecStart` in `solarmon.service` before you install it.

Updating
----
```bash
# Host: solarmon (Pi), as user pi (not root)
/home/pi/solarmon/scripts/update.sh
```
The script does a fast-forward `git pull`, syncs the venv with
`requirements.txt`, and restarts the service with `sudo systemctl restart`.
It refuses to run as root and never installs the systemd unit: the checkout
is writable by `pi`, so installing its unit as root would let `pi` gain root.
If `solarmon.service` changed, the script shows the diff and the `install`
command; review the diff and run that command as root.

If `pi` has no general sudo rights, allow only the restart:
```bash
# Host: solarmon (Pi), as root
echo 'pi ALL=(root) NOPASSWD: /usr/bin/systemctl restart solarmon.service, /bin/systemctl restart solarmon.service' \
  > /etc/sudoers.d/solarmon && chmod 0440 /etc/sudoers.d/solarmon && visudo -c
```

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

Service hardening
----
`solarmon.service` uses systemd sandboxing: read-only filesystem
(`ProtectSystem=strict`, `ProtectHome=read-only`), no capabilities, a
syscall filter, IPv4/IPv6/Unix sockets only, and device access limited to
serial device groups from `/proc/devices` (`ttyUSB*`, `ttyACM*`, `ttyAMA*`, `ttyS*`). If the serial port does not
open (`Permission denied` / `Operation not permitted`), check the device with
`ls -l /dev/serial*` and adjust the `DeviceAllow=` lines.

Troubleshooting
----
- `Cleanup recv buffer before send: 0x0 0x0 ...` around sunrise and sunset is
  harmless: the inverter powers its RS485 transceiver on/off and leaves null
  bytes on the line. pymodbus discards them. To hide these warnings, set
  `modbus_log_level = ERROR` in the `[solarmon]` section. If you see them at
  midday, check RS485 termination (120 Ω) and bias resistors.
- At night the inverter has no power and does not answer; this is expected.
- Follow the log: `journalctl -u solarmon -f`
