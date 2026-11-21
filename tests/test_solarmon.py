import json
import tempfile
import unittest
from configparser import RawConfigParser
from unittest.mock import Mock, patch

import solarmon


class RecordingClient:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))


class SolarmonTests(unittest.TestCase):
    def test_load_settings_from_explicit_path(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.cfg') as config_file:
            config_file.write('[query]\ninterval = 5\n')
            config_file.flush()

            settings = solarmon.load_settings(config_file.name)

        self.assertEqual(settings.getint('query', 'interval'), 5)

    def test_main_polls_configured_inverter_and_publishes_values(self):
        cfg = RawConfigParser()
        cfg.read_dict({
            'query': {'interval': '5', 'offline_interval': '60'},
            'solarmon': {'port': '/dev/test'},
            'inverters.roof': {'unit': '7'},
        })
        serial = Mock()
        serial.connect.return_value = True
        inverter = Mock()
        inverter.read.return_value = {'Pac': 42}
        mqtt_client = Mock()
        with (
            patch.object(solarmon, 'load_settings', return_value=cfg),
            patch.object(solarmon, 'ModbusClient', return_value=serial) as serial_factory,
            patch.object(solarmon, 'Growatt', return_value=inverter) as inverter_factory,
            patch.object(solarmon, 'mqtt_setup', return_value=mqtt_client),
            patch.object(solarmon, 'mqtt_publish_values') as publish,
            patch.object(solarmon.time, 'sleep', side_effect=KeyboardInterrupt) as sleep,
        ):
            with self.assertRaises(KeyboardInterrupt):
                solarmon.main()

        serial_factory.assert_called_once_with(
            port='/dev/test', baudrate=9600, stopbits=1, parity='N', bytesize=8, timeout=1)
        inverter_factory.assert_called_once_with(serial, 'roof', 7)
        publish.assert_called_once_with(mqtt_client, cfg, {'Pac': 42})
        sleep.assert_called_once_with(5)

    def test_mqtt_discovery_payload(self):
        cfg = RawConfigParser()
        cfg.read_dict({
            'mqtt': {
                'enable': 'true',
                'ha_discovery': 'true',
                'base_topic': 'solar/inverter',
                'ha_prefix': 'homeassistant',
                'device_id': 'solar_inverter',
                'device_name': 'Solar Inverter',
            },
        })
        client = RecordingClient()

        solarmon.mqtt_publish_discovery(client, cfg)

        self.assertTrue(client.published)
        topic, payload, qos, retain = client.published[0]
        discovery = json.loads(payload)
        self.assertEqual(topic, 'homeassistant/sensor/solar_inverter_power/config')
        self.assertEqual(discovery['unique_id'], 'solar_inverter_power')
        self.assertEqual(discovery['state_topic'], 'solar/inverter/power')
        self.assertEqual(discovery['availability_topic'], 'solar/inverter/availability')
        self.assertEqual(discovery['device_class'], 'power')
        self.assertEqual(discovery['state_class'], 'measurement')
        self.assertEqual(qos, 0)
        self.assertTrue(retain)


if __name__ == '__main__':
    unittest.main()
