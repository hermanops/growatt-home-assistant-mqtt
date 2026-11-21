import unittest

import growatt


class RegisterRow:
    def __init__(self, registers):
        self.registers = registers


class GrowattDecodeTests(unittest.TestCase):
    def test_signed_temperature_register(self):
        self.assertEqual(growatt.read_single(RegisterRow([65530]), 0, signed=True), -0.6)

    def test_signed_double_register(self):
        row = RegisterRow([65535, 65535])
        self.assertEqual(growatt.read_double(row, 0, signed=True), -0.1)

    def test_invalid_register_is_none(self):
        self.assertIsNone(growatt.read_single(RegisterRow([]), 0))


if __name__ == '__main__':
    unittest.main()
