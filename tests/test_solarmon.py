import json
import os
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

    def test_load_settings_missing_file_fails_fast(self):
        missing = os.path.join(tempfile.mkdtemp(), 'nope.cfg')

        with self.assertRaises(SystemExit) as ctx:
            solarmon.load_settings(missing)

        self.assertIn(missing, str(ctx.exception))

    def test_load_settings_default_path_missing_file_fails_fast(self):
        with patch.object(solarmon.os.path, 'isfile', return_value=False):
            with self.assertRaises(SystemExit) as ctx:
                solarmon.load_settings()

        self.assertIn('solarmon.cfg', str(ctx.exception))

    def test_load_settings_unreadable_file_fails_fast(self):
        with tempfile.NamedTemporaryFile(suffix='.cfg') as config_file:
            with patch('builtins.open', side_effect=PermissionError('permission denied')):
                with self.assertRaises(SystemExit) as ctx:
                    solarmon.load_settings(config_file.name)

        self.assertIn(config_file.name, str(ctx.exception))
        self.assertIn('Could not read config file', str(ctx.exception))

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
        self.assertEqual(discovery['payload_available'], 'online')
        self.assertEqual(discovery['payload_not_available'], 'offline')
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

    def test_none_values_are_not_published(self):
        client = RecordingClient()
        solarmon.mqtt_publish_values(client, RawConfigParser(), 'main', {
            'Pac': None, 'Status': 'None', 'StatusCode': None,
            'Fault': 'None', 'FaultCode': None, 'Ppv': 50,
        })
        self.assertEqual(client.published, [('solar/inverter/main/pv_power', '50', 0, True)])

    def test_missing_status_and_fault_registers_are_not_published_as_text(self):
        serial = Mock()
        serial.read_holding_registers.return_value.isError.return_value = False
        serial.read_holding_registers.return_value.registers = [1]
        inverter = growatt.Growatt(serial, 'main', 1)
        core = SimpleNamespace(registers=[None] + [0] * 32)
        fault = SimpleNamespace(registers=[0] * 7 + [None, 0])
        with patch.object(growatt, '_read_input_safe', side_effect=[core, fault, None, None]):
            info = inverter.read()

        client = RecordingClient()
        solarmon.mqtt_publish_values(client, RawConfigParser(), 'main', info)
        self.assertNotIn('None', [payload for _, payload, _, _ in client.published])
        self.assertNotIn('solar/inverter/main/inverter_status',
                         [topic for topic, _, _, _ in client.published])
        self.assertNotIn('solar/inverter/main/fault',
                         [topic for topic, _, _, _ in client.published])

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
        self.assertEqual({entry['availability_topic'] for entry in discovery},
                         {'solar/inverter/availability'})
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
        mqtt_client.solarmon_availability = solarmon.MqttAvailability(
            mqtt_client, 'solar/inverter/availability')

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
            ('solar/inverter/availability', 'online', 0, True),
        ])

    def test_poll_cycles_publish_shared_availability_and_recovery(self):
        cfg = RawConfigParser()
        cfg.read_dict({
            'query': {'interval': '5', 'offline_interval': '60', 'error_interval': '0'},
            'inverters.roof': {'unit': '1'},
            'inverters.garage': {'unit': '2'},
        })
        serial = Mock()
        serial.connect.return_value = True
        roof = Mock()
        roof.read.side_effect = [None, {'Pac': 42}, None, None]
        garage = Mock()
        garage.read.side_effect = [OSError('read failed'), None, {'Pac': 13}, None]
        mqtt_client = RecordingClient()
        mqtt_client.solarmon_availability = solarmon.MqttAvailability(
            mqtt_client, 'solar/inverter/availability')

        with (
            patch.object(solarmon, 'load_settings', return_value=cfg),
            patch.object(solarmon, 'ModbusClient', return_value=serial),
            patch.object(solarmon, 'Growatt', side_effect=[roof, garage]),
            patch.object(solarmon, 'mqtt_setup', return_value=mqtt_client),
            patch.object(solarmon.time, 'sleep', side_effect=[None, None, None, KeyboardInterrupt]) as sleep,
        ):
            with self.assertRaises(KeyboardInterrupt):
                solarmon.main()

        self.assertEqual(mqtt_client.published, [
            ('solar/inverter/availability', 'offline', 0, True),
            ('solar/inverter/roof/power', '42', 0, True),
            ('solar/inverter/availability', 'online', 0, True),
            ('solar/inverter/garage/power', '13', 0, True),
            ('solar/inverter/availability', 'online', 0, True),
            ('solar/inverter/availability', 'offline', 0, True),
        ])
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [60, 5, 5, 60])

    def test_error_backoff_uses_elapsed_time_not_loop_steps(self):
        cfg = RawConfigParser()
        cfg.read_dict({
            'query': {'interval': '5', 'offline_interval': '60', 'error_interval': '30'},
            'inverters.roof': {'unit': '1'},
            'inverters.garage': {'unit': '2'},
        })
        serial = Mock()
        serial.connect.return_value = True
        roof = Mock()
        roof.read.side_effect = [OSError('read failed'), {'Pac': 42}]
        garage = Mock()
        garage.read.side_effect = [OSError('read failed'), {'Pac': 13}]
        mqtt_client = RecordingClient()
        mqtt_client.solarmon_availability = solarmon.MqttAvailability(
            mqtt_client, 'solar/inverter/availability')

        with (
            patch.object(solarmon, 'load_settings', return_value=cfg),
            patch.object(solarmon, 'ModbusClient', return_value=serial),
            patch.object(solarmon, 'Growatt', side_effect=[roof, garage]),
            patch.object(solarmon, 'mqtt_setup', return_value=mqtt_client),
            patch.object(solarmon.time, 'monotonic', side_effect=[0, 10, 20, 30, 60, 70]),
            patch.object(solarmon.time, 'sleep', side_effect=[None, KeyboardInterrupt]) as sleep,
        ):
            with self.assertRaises(KeyboardInterrupt):
                solarmon.main()

        self.assertEqual(roof.read.call_count, 2)
        self.assertEqual(garage.read.call_count, 2)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [60, 5])

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

        client.on_connect(client, None, None, 1, None)
        client.publish.assert_not_called()
        client.on_connect(client, None, None, 0, None)
        client.solarmon_availability.set_online(True)
        client.on_connect(client, None, None, 0, None)
        client.solarmon_availability.set_online(False)
        client.on_connect(client, None, None, 0, None)
        availability = [
            call.args[1] for call in client.publish.call_args_list
            if call.args[0] == 'solar/inverter/availability'
        ]
        self.assertEqual(availability, ['offline', 'online', 'online', 'offline', 'offline'])
        for call in client.publish.call_args_list:
            if call.args[0] == 'solar/inverter/availability':
                self.assertEqual(call.kwargs, {'qos': 0, 'retain': True})
        self.assertEqual(client.publish.call_count, 3 * (1 + 2 * len(solarmon.SENSOR_DEFS)) + 2)
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
