# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The version number is the `vX.Y.Z` git tag; see [RELEASING.md](RELEASING.md) for
the release process.

## [Unreleased]

### Changed

- **Breaking:** require Python 3.10 or later and pymodbus 3.10 or later
  (pinned to 3.16.2). The Modbus calls use `device_id=` instead of `slave=`.
  Debian 11 (Python 3.9) hosts must upgrade the OS and rebuild the virtualenv;
  see [MIGRATION.md](MIGRATION.md).
- CI tests Python 3.10 (floor) and 3.13 (Debian 13).
- Dependabot now proposes pymodbus updates again.

## [1.0.1] - 2026-11-21

### Fixed

- Keep Power, PV Power, PV1 Current, and energy counters available separately
  from inverter polling status. Publish zero for the three live generation
  sensors when an inverter does not respond, while preserving the last energy
  counter readings.
- Use one service-topic Last Will and combine service plus polling availability
  for sensors that require a live inverter reading.

## [1.0.0] - 2026-11-21

First tagged release. Reads Growatt PV inverters over RS485/Modbus RTU and
publishes telemetry to MQTT with Home Assistant MQTT discovery.

### Added

- MQTT telemetry publishing with Home Assistant MQTT discovery (retained state,
  shared availability topic).
- Separate MQTT state topics and discovery IDs per inverter for multi-inverter
  setups.
- Inverter fault codes and human-readable fault descriptions published as
  sensors; a zero fault is labelled "no fault".
- Home Assistant `device_class`/`state_class` metadata on sensors (for example
  `frequency` on `grid_frequency`).
- Signed Modbus register decoding.
- MQTT availability reflects live inverter polling: `online` when at least one
  inverter responds, `offline` otherwise, restored on reconnect.
- Unit-test suite with a testable entry point (`tests/`).
- Dependabot for weekly pip and GitHub Actions updates.

### Changed

- Pinned dependencies in `requirements.txt`; the service runs from a pinned
  `.venv` under a hardened systemd unit instead of installing as root at start.
- Ported to pymodbus 3.8.6 (Python 3.9 compatible) and paho-mqtt 2.x with the
  VERSION2 callback API.
- Configuration reads fail fast with a clear error when `solarmon.cfg` is
  missing or unreadable.
- Consistent error backoff timing between polls.
- Wait for the network to be online before starting the service.

### Removed

- InfluxDB and Grafana artifacts; the workflow is MQTT-only.
- Deprecated `default_entity_id` from Home Assistant discovery payloads.

[Unreleased]: https://github.com/hermanops/growatt-home-assistant-mqtt/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/hermanops/growatt-home-assistant-mqtt/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/hermanops/growatt-home-assistant-mqtt/releases/tag/v1.0.0
