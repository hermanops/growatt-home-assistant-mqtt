import unittest

import growatt


class RegisterRow:
    def __init__(self, registers):
        self.registers = registers


class FakeResponse:
    """Minimal Modbus register response stub."""

    def __init__(self, registers):
        self.registers = registers

    def isError(self):
        return False


class FakeClient:
    """Modbus client stub returning zero-filled blocks, overridden per start address.

    The signatures match pymodbus 3.10+: `count` and `device_id` are keyword-only
    and there is no `slave` parameter. A call that still passes `slave=` raises
    TypeError, as it does against the real client. Every call is recorded.
    """

    def __init__(self, blocks=None):
        self.blocks = blocks or {}
        self.calls = []

    def read_holding_registers(self, address, *, count=1, device_id=1):
        self.calls.append(("holding", address, count, device_id))
        return FakeResponse(self.blocks.get(address, [0] * count))

    def read_input_registers(self, address, *, count=1, device_id=1):
        self.calls.append(("input", address, count, device_id))
        return FakeResponse(self.blocks.get(address, [0] * count))


class GrowattDecodeTests(unittest.TestCase):
    def test_signed_temperature_register(self):
        self.assertEqual(growatt.read_single(RegisterRow([65530]), 0, signed=True), -0.6)

    def test_signed_double_register(self):
        row = RegisterRow([65535, 65535])
        self.assertEqual(growatt.read_double(row, 0, signed=True), -0.1)

    def test_invalid_register_is_none(self):
        self.assertIsNone(growatt.read_single(RegisterRow([]), 0))


class ModbusCallContractTests(unittest.TestCase):
    """The inverter unit must reach pymodbus as `device_id=` (3.10+), never `slave=`."""

    UNIT = 7

    def test_read_info_passes_unit_as_device_id(self):
        client = FakeClient({73: [3]})
        inverter = growatt.Growatt(client, "main", self.UNIT)
        self.assertEqual(inverter.modbusVersion, 3)
        self.assertEqual(client.calls, [("holding", 73, 1, self.UNIT)])

    def test_read_input_safe_passes_unit_as_device_id(self):
        client = FakeClient({0: [1, 2, 3]})
        resp = growatt._read_input_safe(client, self.UNIT, 0, 3)
        self.assertEqual(resp.registers, [1, 2, 3])
        self.assertEqual(client.calls, [("input", 0, 3, self.UNIT)])

    def test_read_polls_every_block_with_device_id(self):
        client = FakeClient()
        inverter = growatt.Growatt(client, "main", self.UNIT)
        client.calls.clear()
        inverter.read()
        input_calls = [c for c in client.calls if c[0] == "input"]
        self.assertTrue(input_calls)
        self.assertTrue(all(c[3] == self.UNIT for c in client.calls))

    def test_legacy_slave_keyword_is_rejected_by_the_fake(self):
        with self.assertRaises(TypeError):
            FakeClient().read_input_registers(0, count=1, slave=1)


if __name__ == '__main__':
    unittest.main()
