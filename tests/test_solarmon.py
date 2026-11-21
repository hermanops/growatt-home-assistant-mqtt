import json
import tempfile
import unittest
from configparser import RawConfigParser
from types import SimpleNamespace
from unittest.mock import Mock, patch

import growatt
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

    def test_decoded_fault_values_are_published(self):
        cfg = RawConfigParser()
        serial = Mock()
        serial.read_holding_registers.return_value.isError.return_value = False
        serial.read_holding_registers.return_value.registers = [1]
        inverter = growatt.Growatt(serial, 'main', 1)

        for code, text in ((0, 'No Fault'), (29, 'AC Voltage Outrange'), (999, '999')):
            with self.subTest(code=code):
                core = SimpleNamespace(registers=[0] * 33)
                fault = SimpleNamespace(registers=[0] * 7 + [code, 0])
                with patch.object(growatt, '_read_input_safe', side_effect=[core, fault, None, None]):
                    info = inverter.read()
                client = RecordingClient()
                solarmon.mqtt_publish_values(client, cfg, info)

                self.assertIn(('solar/inverter/fault', text, 0, True), client.published)
                self.assertIn(('solar/inverter/fault_code', str(code), 0, True), client.published)

    def test_fault_discovery_matches_state_topics(self):
        cfg = RawConfigParser()
        client = RecordingClient()
        solarmon.mqtt_publish_discovery(client, cfg)

        discovery = {topic: json.loads(payload) for topic, payload, _, _ in client.published}
        for suffix in ('fault', 'fault_code'):
            with self.subTest(suffix=suffix):
                topic = f'homeassistant/sensor/solar_inverter_{suffix}/config'
                self.assertEqual(discovery[topic]['state_topic'], f'solar/inverter/{suffix}')
                self.assertNotIn('state_class', discovery[topic])

    def test_missing_fault_values_are_not_published(self):
        client = RecordingClient()
        solarmon.mqtt_publish_values(client, RawConfigParser(), {'Pac': 42})
        self.assertNotIn('solar/inverter/fault', [topic for topic, _, _, _ in client.published])
        self.assertNotIn('solar/inverter/fault_code', [topic for topic, _, _, _ in client.published])


if __name__ == '__main__':
    unittest.main()
