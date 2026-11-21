#!/usr/bin/env python3

import time
import os
import json
import socket
import logging
import threading

from configparser import RawConfigParser
from pymodbus.client import ModbusSerialClient as ModbusClient
from growatt import Growatt

# --- Optional MQTT (paho-mqtt) ---
try:
    import paho.mqtt.client as mqtt
except Exception:
    mqtt = None


class MqttAvailability:
    def __init__(self, client, topic):
        self.client = client
        self.topic = topic
        self.online = False
        self.lock = threading.Lock()

    def publish(self):
        with self.lock:
            self._publish()

    def set_online(self, online):
        with self.lock:
            self.online = online
            self._publish()

    def _publish(self):
        self.client.publish(self.topic, "online" if self.online else "offline", qos=0, retain=True)


def mqtt_setup(cfg):
    """Create and connect a single MQTT client (or return None if disabled/missing)."""
    if not cfg.getboolean('mqtt', 'enable', fallback=False):
        return None
    if mqtt is None:
        print("MQTT enabled but paho-mqtt not installed; continuing without MQTT.")
        return None

    mqtt_inverter_names(cfg)
    host = cfg.get('mqtt', 'host', fallback='127.0.0.1')
    port = cfg.getint('mqtt', 'port', fallback=1883)
    username = cfg.get('mqtt', 'username', fallback=None)
    password = cfg.get('mqtt', 'password', fallback=None)
    base = cfg.get('mqtt', 'base_topic', fallback='solar/inverter').rstrip('/')

    client_id = f"solarmon-{socket.gethostname()}"
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=True)
    if username:
        client.username_pw_set(username, password or None)

    # LWT / availability
    avail_topic = f"{base}/availability"
    client.will_set(avail_topic, payload="offline", qos=0, retain=True)
    client.solarmon_availability = MqttAvailability(client, avail_topic)

    def on_connect(c, userdata, flags, reason_code, properties):
        if reason_code != 0:
            print(f"MQTT connect failed: {reason_code}")
            return
        client.solarmon_availability.publish()
        mqtt_publish_discovery(c, cfg)

    client.on_connect = on_connect
    client.reconnect_delay_set(min_delay=1, max_delay=60)

    # Non-blocking: the loop thread connects and retries, so a broker that is
    # not yet up at boot does not crash the service.
    client.connect_async(host, port, keepalive=30)
    client.loop_start()
    return client


# ---------------------------------------------------------------------------
# Sensor definitions: topic_suffix -> (name, unit, device_class, state_class)
# ---------------------------------------------------------------------------
SENSOR_DEFS = {
    "power":              ("Power",              "W",   "power",       "measurement"),
    "pv_power":           ("PV Power",           "W",   "power",       "measurement"),
    "pv1_voltage":        ("PV1 Voltage",        "V",   "voltage",     "measurement"),
    "pv1_current":        ("PV1 Current",        "A",   "current",     "measurement"),
    "ac_voltage":         ("AC Voltage",         "V",   "voltage",     "measurement"),
    "ac_current":         ("AC Current",         "A",   "current",     "measurement"),
    "grid_frequency":     ("Grid Frequency",     "Hz",  "frequency",   "measurement"),
    "inverter_temp":      ("Inverter Temperature","°C", "temperature", "measurement"),
    "energy_today":       ("Energy Today",       "kWh", "energy",      "total_increasing"),
    "energy_total":       ("Energy Total",       "kWh", "energy",      "total_increasing"),
    "inverter_status":    ("Inverter Status",    None,  None,          None),
    "inverter_status_code":("Inverter Status Code",None, None,         None),
    "fault":              ("Inverter Fault",     None,  None,          None),
    "fault_code":         ("Inverter Fault Code",None,  None,          None),
}

# Modbus field -> (topic_suffix, formatter)
FIELD_MAP = {
    "Pac":        ("power",              lambda v: f"{float(v):.0f}"),
    "Ppv":        ("pv_power",           lambda v: f"{float(v):.0f}"),
    "Vpv1":       ("pv1_voltage",        lambda v: f"{float(v):.1f}"),
    "PV1Curr":    ("pv1_current",        lambda v: f"{float(v):.2f}"),
    "Vac1":       ("ac_voltage",         lambda v: f"{float(v):.1f}"),
    "Iac1":       ("ac_current",         lambda v: f"{float(v):.2f}"),
    "Fac":        ("grid_frequency",     lambda v: f"{float(v):.2f}"),
    "Temp":       ("inverter_temp",      lambda v: f"{float(v):.1f}"),
    "EnergyToday":("energy_today",       lambda v: f"{float(v):.3f}"),
    "EnergyTotal":("energy_total",       lambda v: f"{float(v):.3f}"),
    "Status":     ("inverter_status",    lambda v: str(v)),
    "StatusCode": ("inverter_status_code",lambda v: str(int(v))),
    "Fault":      ("fault",              lambda v: str(v)),
    "FaultCode":  ("fault_code",         lambda v: str(int(v))),
}


def mqtt_inverter_names(cfg):
    names = [section[10:] for section in cfg.sections() if section.startswith('inverters.')]
    for name in names:
        if not name or any(char in name for char in '/+#\0'):
            raise ValueError(f"Invalid inverter name {name!r}: must be a non-empty MQTT topic level")
    return names


def mqtt_publish_discovery(client, cfg):
    """Publish HA MQTT Discovery for all sensors."""
    if not client or not cfg.getboolean('mqtt', 'ha_discovery', fallback=True):
        return

    base = cfg.get('mqtt', 'base_topic', fallback='solar/inverter').rstrip('/')
    prefix = cfg.get('mqtt', 'ha_prefix', fallback='homeassistant').rstrip('/')
    device_id_base = cfg.get('mqtt', 'device_id', fallback='solar_inverter')
    device_name = cfg.get('mqtt', 'device_name', fallback='Solar Inverter')
    avail_topic = f"{base}/availability"

    for inverter_name in mqtt_inverter_names(cfg):
        # Hex encoding keeps names reversible and avoids discovery ID collisions.
        device_id = f"{device_id_base}_{inverter_name.encode('utf-8').hex()}"
        device = {
            "identifiers": [device_id],
            "manufacturer": "Growatt",
            "model": "1000S",
            "name": f"{device_name} {inverter_name}",
        }

        for suffix, (name, uom, dev_class, state_class) in SENSOR_DEFS.items():
            uid = f"{device_id}_{suffix}"
            cfg_topic = f"{prefix}/sensor/{uid}/config"
            state_topic = f"{base}/{inverter_name}/{suffix}"

            payload = {
                "name": name,
                "unique_id": uid,
                "state_topic": state_topic,
                "availability_topic": avail_topic,
                "payload_available": "online",
                "payload_not_available": "offline",
                "device": device,
            }
            if uom:
                payload["unit_of_measurement"] = uom
            if dev_class:
                payload["device_class"] = dev_class
            if state_class:
                payload["state_class"] = state_class

            client.publish(cfg_topic, json.dumps(payload), qos=0, retain=True)


def mqtt_publish_values(client, cfg, inverter_name, info):
    """Publish all sensor values from a Growatt read."""
    if not client or not info:
        return

    base = cfg.get('mqtt', 'base_topic', fallback='solar/inverter').rstrip('/')
    retain = cfg.getboolean('mqtt', 'retain', fallback=True)

    for field, (suffix, formatter) in FIELD_MAP.items():
        val = info.get(field)
        if val is None or val == "None":
            continue
        try:
            payload = formatter(val)
            client.publish(f"{base}/{inverter_name}/{suffix}", payload, qos=0, retain=retain)
        except Exception:
            continue


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def load_settings(config_path=None):
    """Load the application config from config_path or the default config file."""
    if config_path is None:
        config_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'solarmon.cfg')

    settings = RawConfigParser()
    settings.read(config_path)
    return settings


def main(config_path=None):
    settings = load_settings(config_path)

    interval = settings.getint('query', 'interval', fallback=1)
    offline_interval = settings.getint('query', 'offline_interval', fallback=60)
    error_interval = settings.getint('query', 'error_interval', fallback=60)

    # pymodbus log level. WARNING shows e.g. "Cleanup recv buffer before send" at
    # dawn/dusk when the inverter powers its RS485 transceiver on/off (harmless).
    logging.getLogger('pymodbus').setLevel(
        settings.get('solarmon', 'modbus_log_level', fallback='WARNING').upper())

    print('Setup Serial Connection... ', end='')
    port = settings.get('solarmon', 'port', fallback='/dev/ttyUSB0')
    client = ModbusClient(port=port, baudrate=9600, stopbits=1, parity='N', bytesize=8, timeout=1)
    if not client.connect():
        raise SystemExit(f"Could not open serial port {port}")
    print('Done!')

    mqtt_client = mqtt_setup(settings)

    print('Loading inverters... ')
    inverters = []
    for section in settings.sections():
        if not section.startswith('inverters.'):
            continue

        name = section[10:]
        unit = settings.getint(section, 'unit')
        growatt = Growatt(client, name, unit)
        growatt.print_info()
        inverters.append({
            'name': name,
            'error_until': 0.0,
            'growatt': growatt,
        })

    print('Done!')

    while True:
        online = False
        for inverter in inverters:
            if time.monotonic() < inverter['error_until']:
                continue

            growatt = inverter['growatt']
            try:
                info = growatt.read()

                if info is None:
                    continue

                online = True

                if mqtt_client:
                    mqtt_publish_values(mqtt_client, settings, inverter['name'], info)

            except Exception as err:
                print(growatt.name)
                print(err)
                inverter['error_until'] = time.monotonic() + error_interval

        if mqtt_client:
            mqtt_client.solarmon_availability.set_online(online)

        if online:
            time.sleep(interval)
        else:
            time.sleep(offline_interval)


if __name__ == '__main__':
    main()
