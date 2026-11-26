#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Growatt 1000-S (S-series) over RS485 Modbus RTU.
# Register map: Growatt Inverter Modbus RTU Protocol V3.04/V3.05.
# Tested API: pymodbus 3.16.2 (Python 3.13, Debian 13). Uses `device_id=`.
# NOTE: pymodbus <3.10 used `slave=`; this code no longer supports those versions.

import time
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Codes
# ---------------------------------------------------------------------------

# S-series reports 0, 1 and 3 only. 2 is for storage models.
StateCodes = {
    0: 'Waiting',
    1: 'Normal',
    2: 'Discharging',
    3: 'Fault'
}

# Input register 40 (fault code). Texts follow table &*1 of the Growatt PV
# Inverter Modbus RS485 RTU Protocol V3.14 (docs/). Values 1-23 are shown on
# the inverter as "Error: 99+x"; see fault_text(). The protocol defines no
# other values for the S-series.
ErrorCodes = {
    0: 'No Fault',
    24: 'Auto Test Failed',
    25: 'No AC Connection',
    26: 'PV Isolation Low',
    27: 'Residual I High',
    28: 'Output High DCI',
    29: 'PV Voltage High',
    30: 'AC V Outrange',
    31: 'AC F Outrange',
    32: 'Module Hot',
}


def fault_text(code):
    """Return the inverter's fault description for an input register 40 value."""
    if code in ErrorCodes:
        return ErrorCodes[code]
    if isinstance(code, int) and 1 <= code <= 23:
        return 'Error: %d' % (99 + code)
    return str(code)


# ---------------------------------------------------------------------------
# Helpers for register decoding
# ---------------------------------------------------------------------------

def _to_signed16(value):
    """Two's-complement conversion of an unsigned 16-bit register value."""
    value = int(value)
    return value - 65536 if value >= 32768 else value


def _to_signed32(value):
    """Two's-complement conversion of an unsigned 32-bit value."""
    value = int(value)
    return value - 4294967296 if value >= 2147483648 else value


def read_single(row, index, scale=10, signed=False):
    """
    Read a single 16-bit register with a default scale of 0.1 (scale=10).
    Pass scale=1 for raw integer, scale=100 for 0.01, etc.
    Pass signed=True for fields that can go negative (e.g. temperature).
    """
    try:
        val = int(row.registers[index])
        if signed:
            val = _to_signed16(val)
        return val / float(scale)
    except (IndexError, TypeError, ValueError, AttributeError):
        return None


def read_double(row, index, scale=10, signed=False):
    """
    Read a 32-bit value composed of two 16-bit registers (index, index+1).
    High word first (big-endian). Default scale 0.1 (scale=10).
    """
    try:
        hi = int(row.registers[index])
        lo = int(row.registers[index + 1])
        val = (hi << 16) | lo
        if signed:
            val = _to_signed32(val)
        return val / float(scale)
    except (IndexError, TypeError, ValueError, AttributeError):
        return None


def merge(d1, d2):
    out = dict(d1)
    out.update(d2)
    return out


# ---------------------------------------------------------------------------
# Safe Modbus read helpers
# ---------------------------------------------------------------------------

def _resp_ok(resp):
    """True if resp is a valid (non-error) register response."""
    return (
        resp is not None
        and not resp.isError()
        and hasattr(resp, "registers")
    )


def _read_input_safe(client, unit, start, count, retries=2, delay=0.05):
    """
    Read input registers with a few quick retries.
    Returns a response with .registers or None if all attempts fail.
    """
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = client.read_input_registers(start, count=count, device_id=unit)
            if _resp_ok(resp):
                return resp
            last_err = resp
        except Exception as e:  # serial/IO errors, timeouts
            last_err = e
        if attempt < retries:  # no point sleeping after the final attempt
            time.sleep(delay)
    logger.debug("Input read %d..%d failed after %d tries: %s",
                 start, start + count - 1, retries + 1, last_err)
    return None


# ---------------------------------------------------------------------------
# Growatt class
# ---------------------------------------------------------------------------

class Growatt:
    def __init__(self, client, name, unit):
        self.client = client
        self.name = name
        self.unit = unit
        self.modbusVersion = None
        self.read_info()

    def read_info(self):
        """Read modbus version. Non-fatal if inverter is offline (e.g. nighttime)."""
        try:
            row = self.client.read_holding_registers(73, count=1, device_id=self.unit)
            if _resp_ok(row):
                self.modbusVersion = row.registers[0]
            else:
                self.modbusVersion = None
                logger.warning("[%s] Inverter not responding (offline/sleeping)", self.name)
        except Exception as e:
            self.modbusVersion = None
            logger.warning("[%s] Could not read inverter info: %s", self.name, e)

    def print_info(self):
        print('Growatt:')
        print('\tName: ' + str(self.name))
        print('\tUnit: ' + str(self.unit))
        print('\tModbus Version: ' + str(self.modbusVersion))

    def read(self):
        """
        Read the main blocks of input registers.
        Each block is read safely; if one fails we still return what we have.
        Returns None if the inverter is not responding.
        """

        # ---- Block 0..32 (core live values) ----
        row = _read_input_safe(self.client, self.unit, 0, 33)
        if row is None:
            return None

        info = {
            'StatusCode': row.registers[0],
            'Status': StateCodes.get(row.registers[0], str(row.registers[0])),
            'Ppv': read_double(row, 1),                   # W (0.1)
            'Vpv1': read_single(row, 3),                  # V (0.1)
            'PV1Curr': read_single(row, 4),               # A (0.1)
            'PV1Watt': read_double(row, 5),               # W (0.1)
            'Vpv2': read_single(row, 7),                  # V (0.1)
            'PV2Curr': read_single(row, 8),               # A (0.1)
            'PV2Watt': read_double(row, 9),               # W (0.1)
            'Pac': read_double(row, 11, signed=True),     # W (0.1)
            'Fac': read_single(row, 13, 100),             # Hz (0.01)
            'Vac1': read_single(row, 14),                 # V (0.1)
            'Iac1': read_single(row, 15, signed=True),    # A (0.1)
            'Pac1': read_double(row, 16, signed=True),    # W (0.1)
            'Vac2': read_single(row, 18),                 # V (0.1)
            'Iac2': read_single(row, 19, signed=True),    # A (0.1)
            'Pac2': read_double(row, 20, signed=True),    # W (0.1)
            'Vac3': read_single(row, 22),                 # V (0.1)
            'Iac3': read_single(row, 23, signed=True),    # A (0.1)
            'Pac3': read_double(row, 24, signed=True),    # W (0.1)
            'EnergyToday': read_double(row, 26),          # kWh (0.1)
            'EnergyTotal': read_double(row, 28),          # kWh (0.1)
            'TimeTotal': read_double(row, 30, 7200),      # hours (raw unit 0.5 s)
            'Temp': read_single(row, 32, signed=True),    # °C (0.1)
        }

        # ---- Block 33..41 (fault values + code + IPM temp) ----
        row = _read_input_safe(self.client, self.unit, 33, 9)
        if row is not None:
            fault_code = row.registers[7]
            info = merge(info, {
                'ISOFault': read_single(row, 0),                # V (0.1)
                'GFCIFault': read_single(row, 1, 1),            # mA (1)
                'DCIFault': read_single(row, 2, 100),           # A (0.01)
                'VpvFault': read_single(row, 3),                # V (0.1)
                'VavFault': read_single(row, 4),                # V (0.1) Vac fault (key kept for MQTT compat)
                'FacFault': read_single(row, 5, 100),           # Hz (0.01)
                'TempFault': read_single(row, 6, signed=True),  # °C (0.1)
                'FaultCode': fault_code,
                'Fault': fault_text(fault_code),
                'IPMTemp': read_single(row, 8, signed=True),    # °C (0.1)
            })

        # ---- Block 42..43 (bus voltages) ----
        row = _read_input_safe(self.client, self.unit, 42, 2)
        if row is not None:
            info = merge(info, {
                'PBusV': read_single(row, 0),   # V (0.1)
                'NBusV': read_single(row, 1),   # V (0.1)
            })

        # ---- Block 48..63 (energies & reactive) ----
        row = _read_input_safe(self.client, self.unit, 48, 16)
        if row is not None:
            info = merge(info, {
                'Epv1_today': read_double(row, 0),    # kWh (0.1)
                'Epv1_total': read_double(row, 2),    # kWh (0.1)
                'Epv2_today': read_double(row, 4),    # kWh (0.1)
                'Epv2_total': read_double(row, 6),    # kWh (0.1)
                'Epv_total': read_double(row, 8),     # kWh (0.1)
                'Rac': read_double(row, 10),          # Var (0.1)
                'E_rac_today': read_double(row, 12),  # kVarh (0.1)
                'E_rac_total': read_double(row, 14),  # kVarh (0.1)
            })

        return info
