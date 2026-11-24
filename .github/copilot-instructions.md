# Growatt Home Assistant MQTT repository instructions

## Development and validation

- This is a small Python application with two runtime modules, `solarmon.py` and `growatt.py`.
- Use the repository virtual environment. The documented full unit-test command is:
  `.venv/bin/python -m unittest discover -s tests -v`
- For a quick single test module, run:
  `.venv/bin/python -m unittest tests.test_solarmon -v`
- CI runs Ruff, byte compilation, unittest discovery, and an import check on Python 3.10 and 3.13. Before finishing Python changes, run:
  - `.venv/bin/ruff check .`
  - `.venv/bin/python -m py_compile solarmon.py growatt.py`
  - `.venv/bin/python -m unittest discover -s tests -v`
- Report lint and unit-test results separately; CI jobs may be named `lint (3.10)` or `lint (3.13)` while also running unit tests.
- Dependencies are pinned in `requirements.txt`: pymodbus 3.10+ (pinned 3.16.2) requires Python 3.10+ and uses `device_id=`; `slave=` is no longer supported.

## Architecture

- `solarmon.py` loads configuration, creates the Modbus and MQTT clients, polls configured inverters, and publishes MQTT/Home Assistant values.
- `growatt.py` decodes Growatt Modbus register blocks and exposes decoded fields through `Growatt.read()`.
- Inverter configuration uses `[inverters.<name>]` sections. Names must be non-empty and cannot contain `/`, `+`, `#`, or a null character.
- MQTT state topics are `<base_topic>/<inverter_name>/<sensor>`. Shared availability is `<base_topic>/availability`.
- Home Assistant discovery topics are retained independently of normal sensor retention and use per-inverter device identifiers.
- `solarmon.cfg` is local credentials/configuration and must not be committed. Copy `solarmon.cfg.example` for local setup.
- The production service runs as `pi` from `.venv` under systemd; installation and update details are in `README.md`.

## Project-specific conventions

- Preserve compatibility with existing MQTT topic and discovery behavior unless the task explicitly requests a migration. If names or topics change, update README migration guidance and tests that cover old/new retained values or discovery cleanup.
- MQTT sensor values must not publish Python or string `"None"` values. Missing decoded fields should be omitted so Home Assistant can mark or retain the appropriate state correctly.
- MQTT availability remains shared across inverters: it is `offline` until a successful poll, `online` when at least one inverter responds, and `offline` otherwise. On MQTT reconnect, republish the latest polling status and preserve the offline MQTT last will.
- Each inverter must have distinct MQTT state topics and Home Assistant discovery IDs. Test multiple configured inverters together.
- Modbus reads can fail, especially at night or while the inverter sleeps. Keep reads and decoding non-fatal where partial register blocks are unavailable.
- Configuration reads must fail fast with a clear error for missing or unreadable `solarmon.cfg`; do not silently fall back to defaults for startup-critical configuration.
- The unit-test suite under `tests/` is part of the repository and CI. Add focused mock-based tests for Modbus failures, MQTT reconnect/availability, multiple inverters, and missing or unreadable configuration.
- Update `README.md` for user-visible topic, discovery, configuration, callback API, or upgrade behavior changes.

## GitHub and pull requests

- The repository is `hermanops/growatt-home-assistant-mqtt`; target pull requests to `master`.
- Do not assume a fork or a second remote account is the branch owner. Inspect remotes and explicitly use the repository owner/head branch when pushing or creating a PR.
- Before opening or updating a PR, check whether a PR already exists for the current branch.
- When addressing PR review comments, verify the comment's exact request and the relevant CI evidence. Fix code when requested, reply with evidence when no change is needed, and resolve only the addressed thread.
