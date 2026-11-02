# Open LC-231

Open LC-231 is an independent, early-stage effort to understand the Bluetooth interface of the Grundfos LC 231 level controller and make its monitoring data available to open-source systems.

The project is working toward:

- a documented LC 231 BLE/GENI protocol;
- a reusable read-only Python client;
- an ESPHome integration for local monitoring;
- control support only if the protocol and safety behavior can be established with sufficient evidence.

This project is not affiliated with or endorsed by Grundfos.

## Current Status

Known today:

- The controller exposes a Grundfos vendor BLE characteristic carrying GENI-like frames.
- The frame structure, length field, addressing, and CRC are understood.
- Several read-only data objects have been identified for controller runtime,
  water level, pump counters, pump runtime, mains voltage, live and last-run
  electrical values, controller time, configuration, and event history.
- Comparisons with Grundfos GO, an exported profile snapshot, official Modbus
  terminology, and natural controller operation provide semantic oracles for
  those fields.
- Natural cycles confirm both pumps' live voltage, current, power, power
  factor, and running bits. Last-hour, last-24-hour, daily, and lifetime
  counter behavior is also mapped.
- The current local control selection for each pump can be read as Auto, Off,
  or On. This is status monitoring only and does not provide a control
  operation.
- Active-alarm and active-warning objects are identified and return a confirmed
  empty state when Grundfos GO reports no current conditions; nonzero LC-231
  replies remain to be observed.
- The configured CIO, DI, DIO, and relay functions are mapped, together with
  the inactive logic-state representation and CIO1's 4-20 mA level input.
- Product metadata can be read through individual strings, a segmented
  identification object, and grouped unit-family/type/version data items.
- Initial BLE commissioning uses the controller's button-triggered advertising
  window, and bidirectional vendor traffic requires a paired connection.

Still unknown or incomplete:

- The mapped fields still need confirmation across additional controller
  states and installations; substantial portions of the payloads remain unnamed.
- The Python client provides a one-shot read-only dump; it is not a long-running
  monitor or control client.
- The ESPHome component currently exposes the confirmed water-level, pump,
  electrical, runtime, counter, control-selection, alarm, warning, and event
  fields as Home Assistant entities.
- No write or pump-control operation is considered safe for implementation.

## Python Dump Client

The repository includes one standalone Python client. It discovers an LC 231,
requests pairing, enables notifications before sending requests, and performs
a serialized read-only dump of:

- product-identification strings and the segmented identification record;
- grouped metadata and clock values;
- confirmed status, counters, configuration, alarms, warnings, and event
  history;
- suspected and still-unknown objects, preserving their raw values.

Python 3.10 or newer and [Bleak](https://github.com/hbldh/bleak) are required.
From the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
./open-lc-231.py
```

With no target option, the client requires one unique Grundfos `0xFE5D`
advertisement. Use `--scan-name` or `--address` when explicit selection is
needed. Press the controller's connect button before discovery if it is not
advertising.

An exported Grundfos GO profile snapshot can be supplied to include populated
objects outside the built-in catalogue:

```sh
./open-lc-231.py --snapshot PumpProfileSnapshot.gfpss
```

The terminal shows decoded and raw values while the complete JSON dump is
written under the ignored `.local/dumps/` directory by default. The dump can
contain controller- and installation-specific identifiers and should be
reviewed before sharing.

## ESPHome

[`open-lc-231.yaml`](open-lc-231.yaml) is a complete example for an ESP32-based
BLE receiver. It uses the local `grundfos_lc231` external component and keeps the
existing Device Information Service entities alongside the decoded protocol
entities.

Copy the placeholder secrets file, replace the MAC address, and validate the
configuration from the repository root:

```sh
cp secrets.example.yaml secrets.yaml
esphome config open-lc-231.yaml
```

The receiver remains connected and polls one request at a time. Fast, regular,
and slow poll intervals are substitutions at the top of the example. The
component also reads identification, static timing/configuration, most
clock-family records once per BLE session. The advancing controller-local clock
and temperature measurements are part of the regular poll group. Raw candidate
entities retain their object IDs so they can be compared without assigning
unsupported meanings. The `LC-231 BLE Client` switch manually disconnects the
receiver so Grundfos GO can connect; turning it on resumes the BLE client. This
switch does not control the controller or either pump.

## Documentation

- [Protocol reference](docs/protocol.md): BLE transport, frame format, CRC,
  discovery and pairing, transaction sequencing, object layouts, evidence
  labels, and source material.

## Safety

The current public scope is read-only. An LC 231 can control pumps and alarms in wastewater or tank applications; guessed writes or replayed commands can have physical consequences. Protocol similarity alone is not sufficient evidence for control behavior.

Product names are used only to identify compatibility and source material.

## License

Original project code and documentation use the [MIT License](LICENSE).
The grant applies to rights held by contributors, where copyright applies;
it does not claim third-party rights or guarantee that every AI-assisted
fragment is independently copyrightable. Third-party material and dependencies
retain their own licenses.
