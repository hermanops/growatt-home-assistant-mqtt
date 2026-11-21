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
        publish.assert_called_once_with(mqtt_client, cfg, 'roof', {'Pac': 42})
        sleep.assert_called_once_with(5)

    def test_mqtt_discovery_payload(self):
        cfg = RawConfigParser()
        cfg.read_dict({
            'inverters.roof': {'unit': '1'},
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
        self.assertEqual(topic, 'homeassistant/sensor/solar_inverter_726f6f66_power/config')
        self.assertEqual(discovery['unique_id'], 'solar_inverter_726f6f66_power')
        self.assertEqual(discovery['state_topic'], 'solar/inverter/roof/power')
        self.assertEqual(discovery['availability_topic'], 'solar/inverter/availability')
        self.assertEqual(discovery['device']['identifiers'], ['solar_inverter_726f6f66'])
        self.assertEqual(discovery['device']['name'], 'Solar Inverter roof')
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
                solarmon.mqtt_publish_values(client, cfg, 'main', info)

                self.assertIn(('solar/inverter/main/fault', text, 0, True), client.published)
                self.assertIn(('solar/inverter/main/fault_code', str(code), 0, True), client.published)

    def test_fault_discovery_matches_state_topics(self):
        cfg = RawConfigParser()
        cfg.read_dict({'inverters.main': {'unit': '1'}})
        client = RecordingClient()
        solarmon.mqtt_publish_discovery(client, cfg)

        discovery = {topic: json.loads(payload) for topic, payload, _, _ in client.published}
        for suffix in ('fault', 'fault_code'):
            with self.subTest(suffix=suffix):
                topic = f'homeassistant/sensor/solar_inverter_6d61696e_{suffix}/config'
                self.assertEqual(discovery[topic]['state_topic'], f'solar/inverter/main/{suffix}')
                self.assertNotIn('state_class', discovery[topic])

    def test_missing_fault_values_are_not_published(self):
        client = RecordingClient()
        solarmon.mqtt_publish_values(client, RawConfigParser(), 'main', {'Pac': 42})
        self.assertNotIn('solar/inverter/main/fault', [topic for topic, _, _, _ in client.published])
        self.assertNotIn('solar/inverter/main/fault_code', [topic for topic, _, _, _ in client.published])

    def test_multiple_inverters_have_distinct_state_and_discovery(self):
        cfg = RawConfigParser()
        cfg.read_dict({
            'inverters.roof': {'unit': '1'},
            'inverters.roof_pv': {'unit': '2'},
        })
        client = RecordingClient()

        solarmon.mqtt_publish_discovery(client, cfg)
        discovery = [json.loads(payload) for _, payload, _, _ in client.published]
        self.assertEqual(len(discovery), 2 * len(solarmon.SENSOR_DEFS))
        self.assertEqual(len({entry['unique_id'] for entry in discovery}), len(discovery))
        devices = {entry['device']['identifiers'][0] for entry in discovery}
        self.assertEqual(len(devices), 2)

        client.published.clear()
        solarmon.mqtt_publish_values(client, cfg, 'roof', {'Pac': 42, 'Ppv': 50})
        solarmon.mqtt_publish_values(client, cfg, 'roof_pv', {'Pac': 13})
        self.assertEqual(
            client.published,
            [('solar/inverter/roof/power', '42', 0, True),
             ('solar/inverter/roof/pv_power', '50', 0, True),
             ('solar/inverter/roof_pv/power', '13', 0, True)],
        )
        self.assertTrue({topic for topic, _, _, _ in client.published}.issubset(
            {entry['state_topic'] for entry in discovery}))

    def test_main_publishes_values_for_each_inverter(self):
        cfg = RawConfigParser()
        cfg.read_dict({'inverters.roof': {'unit': '1'}, 'inverters.garage': {'unit': '2'}})
        serial = Mock()
        serial.connect.return_value = True
        roof = Mock()
        roof.read.return_value = {'Pac': 42}
        garage = Mock()
        garage.read.return_value = {'Pac': 13}
        mqtt_client = RecordingClient()

        with (
            patch.object(solarmon, 'load_settings', return_value=cfg),
            patch.object(solarmon, 'ModbusClient', return_value=serial),
            patch.object(solarmon, 'Growatt', side_effect=[roof, garage]),
            patch.object(solarmon, 'mqtt_setup', return_value=mqtt_client),
            patch.object(solarmon.time, 'sleep', side_effect=KeyboardInterrupt),
        ):
            with self.assertRaises(KeyboardInterrupt):
                solarmon.main()

        self.assertEqual(mqtt_client.published, [
            ('solar/inverter/roof/power', '42', 0, True),
            ('solar/inverter/garage/power', '13', 0, True),
        ])

    def test_mqtt_reconnect_republishes_discovery_for_all_inverters(self):
        cfg = RawConfigParser()
        cfg.read_dict({
            'mqtt': {'enable': 'true'},
            'inverters.roof': {'unit': '1'},
            'inverters.garage': {'unit': '2'},
        })
        client = Mock()
        with patch.object(solarmon.mqtt, 'Client', return_value=client):
            solarmon.mqtt_setup(cfg)
        client.will_set.assert_called_once_with(
            'solar/inverter/availability', payload='offline', qos=0, retain=True)

        client.on_connect(client, None, None, 0, None)
        client.on_connect(client, None, None, 0, None)
        self.assertEqual(client.publish.call_count, 2 * (1 + 2 * len(solarmon.SENSOR_DEFS)))
        discoveries = [
            json.loads(call.args[1])
            for call in client.publish.call_args_list
            if call.args[0].endswith('/config')
        ]
        self.assertEqual(
            {entry['device']['name'] for entry in discoveries},
            {'Solar Inverter roof', 'Solar Inverter garage'},
        )

    def test_invalid_mqtt_inverter_names_fail_before_connecting(self):
        for name in ('', 'roof/garage', 'roof+', 'roof#', 'roof\0'):
            with self.subTest(name=name):
                cfg = RawConfigParser()
                cfg.read_dict({'mqtt': {'enable': 'true'}, f'inverters.{name}': {'unit': '1'}})
                with patch.object(solarmon.mqtt, 'Client') as client:
                    with self.assertRaisesRegex(ValueError, 'Invalid inverter name'):
                        solarmon.mqtt_setup(cfg)
                    client.assert_not_called()


if __name__ == '__main__':
    unittest.main()
