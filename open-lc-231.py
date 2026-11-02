#!/usr/bin/env python3
"""Standalone read-only dump client for the Grundfos LC-231."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import struct
import sys
from contextlib import asynccontextmanager
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, AsyncIterator, Mapping, Sequence

REQUEST_START = 0x27
REPLY_START = 0x24
MESSAGE_START = 0x26
KNOWN_STARTS = frozenset((REQUEST_START, REPLY_START, MESSAGE_START))

CLIENT_ADDRESS = 0xF8
SERVICE_ADDRESS = 0xE7
BROADCAST_ADDRESS = 0xFF
DATA_OBJECT_CLASS = 0x0A
READ_OPERATION = 0x03
STRING_CLASS = 0x07
STRING_READ_OPERATION = 0x01
GROUPED_DATA_CLASSES = frozenset((0x02, 0x04, 0x05, 0x0B, 0x0E, 0x0F))
VENDOR_CHARACTERISTIC_UUID = "859cffd1-036e-432a-aa28-1a0085b87ba9"
GRUNDFOS_SERVICE_UUID = "0000fe5d-0000-1000-8000-00805f9b34fb"
ATT_CHUNK_SIZE = 20
ZERO_UTC_OFFSET_WARNING = (
    "5e0067 is 0000; interpreting it as UTC+00:00, but LC-231 can also "
    "report 0000 for negative UTC offsets"
)
GROUPED_DATA_ITEM_NAMES = MappingProxyType(
    {
        (0x02, 0x00, 0x94): "unit family",
        (0x02, 0x00, 0x95): "unit type",
        (0x02, 0x00, 0x96): "unit version",
        (0x0E, 0x00, 0x1E): "controller local clock seconds",
    }
)

CONFIRMED_READ_OBJECTS = MappingProxyType(
    {
        "560000": "current pump control selections",
        "580000": "active alarm codes; zero state confirmed",
        "58000b": "active warning codes; zero state confirmed",
        "520c00": "CIO1 analog level-input status",
        "520c01": "CIO2 digital-input state",
        "520c02": "DI1 digital-input state",
        "520c03": "DI2 digital-input state",
        "520c04": "relay 1 output state",
        "520c05": "relay 2 output state",
        "520c06": "DIO1 digital-input state",
        "520c07": "DIO2 digital-input state",
        "5d0001": "pump 1 aggregate status and counters",
        "5d0002": "pump 2 aggregate status and counters",
        "5d0051": "pump 1 today and yesterday counters",
        "5d0052": "pump 2 today and yesterday counters",
        "5a0003": "water-level status",
        "5d0000": "controller aggregate runtime counters",
        "5d0093": "mains-voltage status",
        "5d00d3": "pump 1 latched electrical values",
        "5d00d4": "pump 2 latched electrical values",
        "570001": "pump temperatures and one unidentified temperature",
        "570045": "pump 1 live voltage, current, and power",
        "570047": "pump 1 live power factor",
        "570085": "pump 2 live voltage, current, and power",
        "570087": "pump 2 live power factor",
    }
)

CANDIDATE_READ_OBJECTS = MappingProxyType(
    {
        "580019": "unmapped Type 266 record; zero in normal state",
        "5d0096": "unmapped Type 432 record; not ordinary live pump telemetry",
        "560069": "limit 1 status with a level-correlated value",
        "56006a": "limit 2 status with a level-correlated value",
        "570043": "unmapped Pump 1 parallel status record",
        "570083": "unmapped Pump 2 parallel status record",
        "5d0041": "service-configuration candidate",
        "5d0044": "service-status candidate",
        "5d0091": "unavailable-value candidate",
        "524e21": "unmapped 22-byte structured record",
        "524e61": "unmapped 46-byte structured record",
        "5e0000": "unmapped clock-family record 0",
        "5e0001": "unmapped clock-family record 1",
        "5e0002": "unmapped clock-family record 2",
        "5e0003": "unmapped clock-family record 3",
        "5e0004": "unmapped clock-family record 4",
    }
)

SOURCE_DERIVED_READ_OBJECTS = MappingProxyType(
    {
        "5d0122": "ALPHA HWR flow/pressure candidate; unverified on LC-231",
        "5d012c": "ALPHA HWR temperature candidate; unverified on LC-231",
        "560700": "ALPHA HWR operation-mode candidate; unverified on LC-231",
    }
)

AUXILIARY_READ_OBJECTS = MappingProxyType(
    {
        "560001": "manual-start return configuration",
        "560002": "anti-seizing timing configuration",
        "560003": "unmapped two-byte configuration record",
        "570004": "automatic alarm-reset configuration",
        "570005": "automatic-restart limit configuration",
        "570007": "mains-connection configuration",
        "570008": "starts-per-hour limit configuration",
        "570074": "pump 1 motor-protection configuration",
        "570075": "pump 1 motor-protection configuration mirror",
        "5700b4": "pump 2 motor-protection configuration",
        "5700b5": "pump 2 motor-protection configuration mirror",
        "5700d2": "blocked-pump restart limit",
        "5801cc": "under-voltage detection configuration",
        "5801cd": "over-voltage detection configuration",
        "5a0000": "level-control configuration",
        "5b0001": "multi-pump configuration",
        "5b0002": "run-and-pause configuration",
        "5e0064": "clock configuration",
        "5e0065": "extended standard-format controller real-time clock",
        "5e0066": "daylight-saving schedule",
        "5e0067": "standard UTC offset; partially usable",
        "560067": "limit 1 configuration",
        "560068": "limit 2 configuration",
        "520800": "CIO1 analog-input assignment",
        "520801": "CIO2 digital-input assignment",
        "520802": "DI1 digital-input assignment",
        "520803": "DI2 digital-input assignment",
        "520804": "relay 1 output assignment",
        "520805": "relay 2 output assignment",
        "520806": "DIO1 digital-input assignment",
        "520807": "DIO2 digital-input assignment",
        "5827d7": "event-log metadata",
        **{
            f"5827{sub_id:02x}": f"event-log ring slot {sub_id - 0xd7}"
            for sub_id in range(0xD8, 0x100)
        },
    }
)

READ_OBJECTS = MappingProxyType(
    {
        **CONFIRMED_READ_OBJECTS,
        **AUXILIARY_READ_OBJECTS,
        **CANDIDATE_READ_OBJECTS,
        **SOURCE_DERIVED_READ_OBJECTS,
    }
)

OBJECT_REPLY_SHAPES = MappingProxyType(
    {
        "560000": ("0000", "e401", 6),
        "5a0003": ("0001", "0901", 13),
        "5d0000": ("0000", "f701", 12),
        "5d0001": ("0000", "f802", 28),
        "5d0002": ("0000", "f802", 28),
        "5d0051": ("0001", "6001", 12),
        "5d0052": ("0001", "6001", 12),
        "5d0093": ("0001", "1403", 13),
        "5d00d3": ("0001", "1103", 12),
        "5d00d4": ("0001", "1103", 12),
        "570045": ("0001", "0002", 41),
        "570047": ("0002", "6801", 8),
        "570085": ("0001", "0002", 41),
        "570087": ("0002", "6801", 8),
        "5d0096": ("0003", "b001", 24),
        "580000": (None, "3a01", None),
        "58000b": (None, "3a01", None),
        "580019": ("0001", "0a02", 10),
        "520c00": ("0000", "f502", 21),
        **{f"520c{sub_id:02x}": ("0000", "d301", 1) for sub_id in range(1, 8)},
        "520800": ("0000", "ee01", 16),
        **{
            f"5208{sub_id:02x}": (
                "0000",
                "f101" if sub_id in (4, 5) else "f001",
                4,
            )
            for sub_id in range(1, 8)
        },
        "560001": ("0000", "e301", 4),
        "560002": ("0000", "e502", 7),
        "560003": ("0001", "0201", 2),
        "570004": ("0000", "fb01", 11),
        "570005": ("0000", "e601", 3),
        "570007": ("0001", "1301", 25),
        "570008": ("0002", "5901", 5),
        "570074": ("0001", "0701", 15),
        "570075": ("0001", "1501", 16),
        "5700b4": ("0001", "0701", 15),
        "5700b5": ("0001", "1501", 16),
        "5700d2": ("0001", "5e01", 1),
        "5801cc": ("0003", "6103", 19),
        "5801cd": ("0003", "6203", 19),
        "5a0000": ("0000", "ed05", 37),
        "5b0001": ("0001", "0301", 3),
        "5b0002": ("0001", "7c01", 9),
        "5e0064": ("0001", "4101", 10),
        "5e0065": ("0001", "4201", 12),
        "560067": ("0001", "a002", 22),
        "560068": ("0001", "a002", 22),
        "560069": ("0000", "d901", 19),
        "56006a": ("0000", "d901", 19),
        "570001": ("0000", "e801", 38),
        "570043": ("0000", "fc01", 5),
        "570083": ("0000", "fc01", 5),
        "5d0041": ("0000", "f901", 7),
        "5d0044": ("0000", "fa01", 18),
        "5d0091": ("0001", "8f01", 4),
        "524e21": ("0001", "a801", 22),
        "524e61": ("0001", "a801", 46),
        "5e0000": ("0001", "0801", 2),
        "5e0001": ("0001", "0b01", 2),
        "5e0002": ("0001", "0d01", 3),
        "5e0003": ("0001", "0d01", 7),
        "5e0004": ("0001", "1701", 1),
        "5e0066": ("0001", "4301", 10),
        "5e0067": ("0002", "4d01", 2),
        "5827d7": ("0000", "f301", 7),
        **{
            f"5827{sub_id:02x}": ("0000", "f402", 13)
            for sub_id in range(0xD8, 0x100)
        },
    }
)

EVENT_SOURCE_NAMES = MappingProxyType({0: "system", 6: "pump"})
EVENT_CONDITION_NAMES = MappingProxyType(
    {
        0: "none",
        1: "alarm appeared",
        2: "alarm disappeared",
        3: "warning appeared",
        4: "warning disappeared",
    }
)
EVENT_CODE_NAMES = MappingProxyType(
    {
        9: "phase sequence reversal",
        25: "setup conflict",
    }
)
# Numeric event codes listed by the official LC 2xx Modbus profile.  These are
# semantic candidates for an LC BLE event record until the same name is
# confirmed in Grundfos GO or by a controlled LC observation.
MODBUS_EVENT_CODE_CANDIDATE_NAMES = MappingProxyType(
    {
        2: "motor missing phase",
        3: "external fault signal",
        4: "too many automatic pump restarts",
        12: "motor service time exceeded",
        17: "performance requirement not met",
        21: "maximum starts per hour exceeded",
        22: "motor moisture",
        26: "load continues with motor relay off",
        32: "motor voltage high",
        40: "motor voltage low",
        46: "external warning signal",
        48: "motor overload",
        56: "motor underload",
        57: "dry running",
        69: "motor temperature PTC1",
        72: "general hardware fault",
        76: "internal communication failure",
        84: "memory access error",
        85: "EEPROM parameter error",
        94: "limit 1 exceeded",
        95: "limit 2 exceeded",
        117: "intrusion",
        130: "cable theft",
        142: "operating on battery backup",
        157: "real-time clock out of order",
        159: "controller-to-CIM communication fault",
        163: "motor-protection measurement or setup fault",
        165: "analog-input sensor 1 signal fault",
        181: "PTC sensor signal fault",
        191: "high level",
        196: "specific energy high",
        205: "float-switch sequence inconsistency",
        220: "motor contactor feedback fault",
        225: "pump-module communication fault",
        226: "I/O-module communication fault",
        229: "water on floor",
        248: "battery warning",
        249: "user event 1",
        250: "user event 2",
    }
)
EVENT_DETAIL_NAMES = MappingProxyType(
    {
        (25, 1005): "level control settings wrong",
    }
)

PUMP_CONTROL_SELECTION_NAMES = MappingProxyType(
    {
        b"\x00\x00": "auto",
        b"\x02\x01": "off",
        b"\x02\x05": "on",
    }
)
MANUAL_RETURN_MODE_NAMES = MappingProxyType({0: "auto", 1: "off"})
AUTOMATIC_ALARM_RESET_NAMES = MappingProxyType(
    {0: "no automatic reset", 1: "all except pump critical", 2: "all alarms"}
)
MAINS_CONNECTION_NAMES = MappingProxyType({3: "three phase with neutral"})
IO_TERMINAL_NAMES = MappingProxyType(
    {
        0: "CIO1",
        1: "CIO2",
        2: "DI1",
        3: "DI2",
        4: "relay 1",
        5: "relay 2",
        6: "DIO1",
        7: "DIO2",
    }
)
IO_ASSIGNMENT_KIND_NAMES = MappingProxyType(
    {
        0x1E: "digital input, normally open",
        0x40: "relay output",
    }
)
IO_FUNCTION_NAMES = MappingProxyType(
    {
        0x0007: "all alarms and warnings",
        0x000A: "dry running",
        0x003C: "high level",
        0x003D: "stop Pump 1",
        0x003E: "start Pump 1",
        0x003F: "start Pump 2",
        0x0180: "high-level alarm",
    }
)

BUNDLE_HEAD_ID = "59000f"
BUNDLE_CONTINUATION_IDS = ("590010", "590011")
BUNDLE_OBJECT_IDS = (BUNDLE_HEAD_ID, *BUNDLE_CONTINUATION_IDS)

# Empty and still-unknown strings are intentionally included in the dump.
STRING_IDS = (
    (0x01, "product_type", "confirmed"),
    (0x08, "product_number", "confirmed"),
    (0x09, "serial_number", "confirmed"),
    (0x0A, "production_code", "confirmed"),
    (0x0E, "field_0e", "unknown"),
    (0x11, "installation_name", "confirmed"),
    (0x14, "gsc_version", "hypothesis"),
    (0x32, "application_software", "confirmed"),
    (0x34, "motor_control_software", "confirmed"),
    (0x37, "field_37", "unknown"),
    (0x3A, "ble_software", "confirmed"),
    (0x61, "field_61", "unknown"),
    (0x65, "field_65", "unknown"),
)


@dataclass(frozen=True)
class GroupedQuery:
    name: str
    destination: int
    groups: tuple[tuple[int, int, tuple[int, ...]], ...]


# Complete read-only grouped queries currently understood structurally.
GROUPED_QUERIES = (
    GroupedQuery(
        "broadcast_clock_and_metadata",
        BROADCAST_ADDRESS,
        (
            (0x0E, 0xC0, (0x1E,)),
            (0x0B, 0xC0, (0x5F, 0x41)),
            (0x04, 0xC0, (0x11, 0x05, 0x10, 0x2F, 0x06)),
        ),
    ),
    GroupedQuery(
        "broadcast_class_04_0e_0f",
        BROADCAST_ADDRESS,
        ((0x04, 0xC0, (0x0E, 0x0F)),),
    ),
    GroupedQuery(
        "aggregate_metadata",
        BROADCAST_ADDRESS,
        (
            (0x05, 0x00, (0x39,)),
            (0x0E, 0x00, (0x1E,)),
            (0x02, 0x00, (0x94, 0x95, 0x96)),
            (0x0B, 0x00, (0x41, 0x5F)),
            (0x0F, 0x00, (0x05, 0x14, 0x16, 0x17)),
        ),
    ),
    GroupedQuery(
        "class_0f_and_class_04",
        BROADCAST_ADDRESS,
        (
            (0x0F, 0x00, (0x18, 0x1A, 0x1C, 0x1B, 0x15, 0x19)),
            (
                0x04,
                0x00,
                (
                    0x05,
                    0x06,
                    0x07,
                    0x08,
                    0x09,
                    0x0A,
                    0x0B,
                    0x0C,
                    0x0D,
                    0x0E,
                    0x0F,
                    0x11,
                    0x10,
                ),
            ),
        ),
    ),
    GroupedQuery(
        "broadcast_class_04_2b_2e_2f_59",
        BROADCAST_ADDRESS,
        ((0x04, 0x00, (0x2B, 0x2E, 0x2F, 0x59)),),
    ),
    GroupedQuery(
        "service_unit_family",
        SERVICE_ADDRESS,
        ((0x02, 0x00, (0x94,)),),
    ),
    GroupedQuery(
        "service_clock_flagged",
        SERVICE_ADDRESS,
        ((0x0E, 0xC0, (0x1E,)),),
    ),
    GroupedQuery(
        "service_clock_and_class_04",
        SERVICE_ADDRESS,
        ((0x0E, 0x00, (0x1E,)), (0x04, 0x00, (0x2E, 0x59))),
    ),
    GroupedQuery(
        "service_class_04_59",
        SERVICE_ADDRESS,
        ((0x04, 0x00, (0x59,)),),
    ),
)


class SnapshotError(ValueError):
    """Raised when a GO snapshot cannot be used as an object catalogue."""


@dataclass(frozen=True)
class SnapshotObject:
    object_id: str
    data_id: int
    sub_id: int
    object_length: int
    object_type: int
    object_version: int
    data: bytes

    @property
    def reply_shape(self) -> tuple[str, str, int]:
        return (
            f"00{self.object_type >> 8:02x}",
            f"{self.object_type & 0xFF:02x}{self.object_version:02x}",
            self.object_length,
        )


def _bounded_int(row: Mapping[str, Any], key: str, maximum: int) -> int:
    value = row.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise SnapshotError(f"snapshot field {key!r} must be an integer")
    if not 0 <= value <= maximum:
        raise SnapshotError(
            f"snapshot field {key!r} must be between 0 and {maximum}"
        )
    return value


def load_snapshot_objects(path: Path) -> Mapping[str, SnapshotObject]:
    """Load populated Class 0x0A objects and reject inconsistent metadata."""

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SnapshotError(f"cannot read snapshot {path}: {error}") from error

    rows = document.get("class10") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        raise SnapshotError("snapshot does not contain a Class-10 object list")

    objects: dict[str, SnapshotObject] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise SnapshotError(f"Class-10 record {index} is not an object")

        raw_hex = row.get("rawbytes")
        if raw_hex in (None, ""):
            continue
        if not isinstance(raw_hex, str):
            raise SnapshotError(f"Class-10 record {index} rawbytes is not text")

        data_id = _bounded_int(row, "dataId", 0xFF)
        sub_id = _bounded_int(row, "subId", 0xFFFF)
        object_length = _bounded_int(row, "objectLength", 0xFF)
        object_type = _bounded_int(row, "objectType", 0xFFFF)
        object_version = _bounded_int(row, "objectVersion", 0xFF)
        try:
            data = bytes.fromhex(raw_hex)
        except ValueError as error:
            raise SnapshotError(
                f"Class-10 record {index} contains invalid hex"
            ) from error
        if len(data) != object_length:
            raise SnapshotError(
                f"object {data_id:02x}{sub_id:04x} declares {object_length} "
                f"bytes but contains {len(data)}"
            )

        object_id = f"{data_id:02x}{sub_id:04x}"
        if object_id in objects:
            raise SnapshotError(f"duplicate populated object {object_id}")
        objects[object_id] = SnapshotObject(
            object_id=object_id,
            data_id=data_id,
            sub_id=sub_id,
            object_length=object_length,
            object_type=object_type,
            object_version=object_version,
            data=data,
        )

    if not objects:
        raise SnapshotError("snapshot contains no populated Class-10 objects")
    return MappingProxyType(objects)


class FrameError(ValueError):
    """Raised when bytes do not contain one complete GENI-like frame."""


def finite_float(value: float) -> float | None:
    """Return JSON-safe finite telemetry, or ``None`` for unavailable values."""

    return value if math.isfinite(value) else None


def crc16_ccitt(data: bytes, *, init: int = 0xFFFF, xorout: int = 0xFFFF) -> int:
    """Return the observed non-reflected CRC-16/CCITT value."""

    register = init & 0xFFFF
    for byte in data:
        register ^= byte << 8
        for _ in range(8):
            if register & 0x8000:
                register = ((register << 1) ^ 0x1021) & 0xFFFF
            else:
                register = (register << 1) & 0xFFFF
    return register ^ (xorout & 0xFFFF)


@dataclass(frozen=True)
class Frame:
    """A length-complete frame with its CRC result and decoded header."""

    raw: bytes
    start: int
    length: int
    destination: int
    source: int
    apdu: bytes
    crc_seen: int
    crc_calculated: int

    @property
    def crc_valid(self) -> bool:
        return self.crc_seen == self.crc_calculated

    def normalized(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "start": f"{self.start:02x}",
            "length": self.length,
            "destination": f"{self.destination:02x}",
            "source": f"{self.source:02x}",
            "apdu_hex": self.apdu.hex(),
            "crc_seen": f"{self.crc_seen:04x}",
            "crc_calculated": f"{self.crc_calculated:04x}",
            "crc_valid": self.crc_valid,
        }
        if self.crc_valid:
            class_0a = decode_class_0a(self.apdu)
            if class_0a is not None:
                result["class_0a"] = class_0a
            request_groups = decode_grouped_data_request(self)
            if request_groups is not None:
                result["grouped_data_request"] = request_groups
            reply_groups = decode_grouped_data_reply(self)
            if reply_groups is not None:
                result["grouped_data_reply"] = reply_groups
        return result


def inspect_frame(raw: bytes) -> Frame:
    """Parse one exact-length frame without rejecting a bad CRC."""

    if len(raw) < 6:
        raise FrameError("frame is shorter than the minimum header and CRC")
    if raw[0] not in KNOWN_STARTS:
        raise FrameError(f"unsupported start byte 0x{raw[0]:02x}")

    length = raw[1]
    expected_size = length + 4
    if len(raw) != expected_size:
        raise FrameError(
            f"LEN requires {expected_size} total bytes, received {len(raw)}"
        )
    if length < 2:
        raise FrameError("LEN is too short to contain destination and source")

    return Frame(
        raw=raw,
        start=raw[0],
        length=length,
        destination=raw[2],
        source=raw[3],
        apdu=raw[4:-2],
        crc_seen=int.from_bytes(raw[-2:], "big"),
        crc_calculated=crc16_ccitt(raw[1:-2]),
    )


def build_object_read(
    object_id: str,
    *,
    destination: int = SERVICE_ADDRESS,
) -> bytes:
    """Build one allowlisted Class 0x0A read for an observed destination."""

    normalized_id = object_id.lower().replace(" ", "")
    if normalized_id not in READ_OBJECTS:
        allowed = ", ".join(READ_OBJECTS)
        raise ValueError(f"object {object_id!r} is not allowlisted; choose from {allowed}")
    return _build_class_0a_read(normalized_id, destination=destination)


def build_observed_object_read(
    object_id: str,
    observed_object_ids: Collection[str],
    *,
    destination: int = SERVICE_ADDRESS,
) -> bytes:
    """Build a read only when the object came from an observed catalogue."""

    normalized_id = object_id.lower().replace(" ", "")
    normalized_observed = {
        value.lower().replace(" ", "") for value in observed_object_ids
    }
    if normalized_id not in normalized_observed:
        raise ValueError(
            f"object {object_id!r} is not present in the observed catalogue"
        )
    return _build_class_0a_read(normalized_id, destination=destination)


def _build_class_0a_read(object_id: str, *, destination: int) -> bytes:
    if len(object_id) != 6:
        raise ValueError("Class-10 object ID must contain exactly three bytes")
    try:
        object_bytes = bytes.fromhex(object_id)
    except ValueError as error:
        raise ValueError("Class-10 object ID must be hexadecimal") from error
    if destination not in (SERVICE_ADDRESS, BROADCAST_ADDRESS):
        raise ValueError("destination must be the observed service or broadcast address")

    body = bytes(
        (
            destination,
            CLIENT_ADDRESS,
            DATA_OBJECT_CLASS,
            READ_OPERATION,
        )
    ) + object_bytes
    without_crc = bytes((REQUEST_START, len(body))) + body
    crc = crc16_ccitt(without_crc[1:])
    return without_crc + crc.to_bytes(2, "big")


def build_string_read(
    string_id: int,
    *,
    destination: int = SERVICE_ADDRESS,
) -> bytes:
    """Build one observed Class 0x07 string read request."""

    if not 0 <= string_id <= 0xFF:
        raise ValueError("string ID must fit in one byte")
    if destination not in (SERVICE_ADDRESS, BROADCAST_ADDRESS):
        raise ValueError("destination must be the observed service or broadcast address")

    body = bytes(
        (
            destination,
            CLIENT_ADDRESS,
            STRING_CLASS,
            STRING_READ_OPERATION,
            string_id,
        )
    )
    without_crc = bytes((REQUEST_START, len(body))) + body
    crc = crc16_ccitt(without_crc[1:])
    return without_crc + crc.to_bytes(2, "big")


def decode_string_reply(frame: Frame) -> str | None:
    """Decode the observed length-prefixed, NUL-terminated Class 0x07 reply."""

    if (
        not frame.crc_valid
        or frame.start != REPLY_START
        or frame.destination != CLIENT_ADDRESS
        or frame.source != SERVICE_ADDRESS
        or len(frame.apdu) < 3
        or frame.apdu[0] != STRING_CLASS
    ):
        return None

    encoded_length = frame.apdu[1]
    encoded = frame.apdu[2:]
    if encoded_length != len(encoded) or not encoded.endswith(b"\x00"):
        return None
    try:
        return encoded[:-1].decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None


def decode_grouped_data_request(frame: Frame) -> list[dict[str, Any]] | None:
    """Decode the observed grouped data-item request structure.

    The supported request classes encode one-byte selector IDs as
    ``<class> <control_and_count> <selectors...>``. The low six bits hold the
    selector count; the two high bits are retained as control flags. Multiple
    groups may share a request.
    """

    if (
        not frame.crc_valid
        or frame.start != REQUEST_START
        or frame.destination not in (SERVICE_ADDRESS, BROADCAST_ADDRESS)
        or frame.source != CLIENT_ADDRESS
        or not frame.apdu
    ):
        return None

    groups: list[dict[str, Any]] = []
    offset = 0
    while offset < len(frame.apdu):
        if offset + 2 > len(frame.apdu):
            return None
        class_id = frame.apdu[offset]
        control_and_count = frame.apdu[offset + 1]
        control_flags = control_and_count & 0xC0
        selector_count = control_and_count & 0x3F
        offset += 2
        if class_id not in GROUPED_DATA_CLASSES or selector_count == 0:
            return None
        end = offset + selector_count
        if end > len(frame.apdu):
            return None
        selectors = frame.apdu[offset:end]
        offset = end
        groups.append(
            {
                "class": f"{class_id:02x}",
                "control_hex": f"{control_and_count:02x}",
                "control_flags": control_flags,
                "selector_count": selector_count,
                "selectors": list(selectors),
                "selectors_hex": selectors.hex(),
            }
        )
    return groups


def decode_grouped_data_reply(frame: Frame) -> list[dict[str, Any]] | None:
    """Decode reply groups encoded as ``<class> <byte_length> <data...>``."""

    if (
        not frame.crc_valid
        or frame.start != REPLY_START
        or frame.destination != CLIENT_ADDRESS
        or frame.source != SERVICE_ADDRESS
        or not frame.apdu
    ):
        return None

    groups: list[dict[str, Any]] = []
    offset = 0
    while offset < len(frame.apdu):
        if offset + 2 > len(frame.apdu):
            return None
        class_id = frame.apdu[offset]
        data_length = frame.apdu[offset + 1]
        offset += 2
        if class_id not in GROUPED_DATA_CLASSES:
            return None
        end = offset + data_length
        if end > len(frame.apdu):
            return None
        data = frame.apdu[offset:end]
        offset = end
        groups.append(
            {
                "class": f"{class_id:02x}",
                "data_length": data_length,
                "data_hex": data.hex(),
            }
        )
    return groups


def decode_grouped_data_exchange(
    request: Frame,
    reply: Frame,
) -> list[dict[str, Any]] | None:
    """Pair selector IDs with fixed-width raw values from one exchange.

    A group is paired only when request and reply class order agrees and the
    reply byte length divides evenly across its requested selectors. The
    resulting values remain hexadecimal so width and identity are preserved
    without guessing signedness, scaling, units, or semantics.
    """

    request_groups = decode_grouped_data_request(request)
    reply_groups = decode_grouped_data_reply(reply)
    if (
        request_groups is None
        or reply_groups is None
        or len(request_groups) != len(reply_groups)
    ):
        return None

    paired: list[dict[str, Any]] = []
    for requested, returned in zip(request_groups, reply_groups):
        if requested["class"] != returned["class"]:
            return None
        selector_count = requested["selector_count"]
        control_flags = requested["control_flags"]
        data = bytes.fromhex(returned["data_hex"])
        if not data or len(data) % selector_count != 0:
            return None
        item_width = len(data) // selector_count
        values = [
            data[offset : offset + item_width].hex()
            for offset in range(0, len(data), item_width)
        ]
        items: list[dict[str, Any]] = []
        for selector, value in zip(requested["selectors"], values):
            item: dict[str, Any] = {"selector": selector, "value_hex": value}
            meaning = GROUPED_DATA_ITEM_NAMES.get(
                (int(requested["class"], 16), control_flags, selector)
            )
            if meaning is not None:
                item["meaning"] = meaning
            items.append(item)
        paired.append(
            {
                "class": requested["class"],
                "control_flags": control_flags,
                "item_width": item_width,
                "items": items,
            }
        )
    return paired


def decode_segmented_object_head(frame: Frame) -> dict[str, Any] | None:
    """Decode a Class 0x0A object head whose declared data continues later.

    Normal object replies carry all declared data in one frame. The observed
    ``59000f`` reply instead declares the complete object length and carries
    only its first bytes. This helper recognizes that distinct shape without
    weakening the normal object-reply checks.
    """

    if not is_sequential_read_reply(frame):
        return None
    payload = frame.apdu[2:]
    if len(payload) < 7 or payload[4:6] != b"\x00\x00":
        return None

    declared_length = payload[6]
    first_chunk = payload[7:]
    if declared_length <= len(first_chunk):
        return None
    return {
        "meta0": payload[0:2].hex(),
        "type": payload[2:4].hex(),
        "declared_data_length": declared_length,
        "data_hex": first_chunk.hex(),
    }


def decode_segmented_object_continuation(frame: Frame) -> bytes | None:
    """Return data from an observed Class 0x0A continuation reply."""

    if not is_sequential_read_reply(frame):
        return None
    payload = frame.apdu[2:]
    if not payload or payload[0] != 0:
        return None
    return payload[1:]


def decode_identification_bundle(data: bytes) -> dict[str, Any] | None:
    """Decode the structural fields of the 123-byte ``59000f`` object.

    The first ten bytes remain unnamed. They are followed by big-endian 16-bit
    lengths and NUL-terminated UTF-8 strings, then one observed trailing byte.
    Only the first three string meanings are independently corroborated by
    Class 0x07 reads; later strings are retained under neutral field numbers.
    """

    if len(data) < 10:
        return None

    offset = 10
    fields: list[str] = []
    while offset + 2 <= len(data):
        encoded_length = int.from_bytes(data[offset : offset + 2], "big")
        offset += 2
        if offset + encoded_length > len(data):
            return None
        encoded = data[offset : offset + encoded_length]
        offset += encoded_length
        if encoded_length == 0:
            fields.append("")
            continue
        if not encoded.endswith(b"\x00"):
            return None
        try:
            fields.append(encoded[:-1].decode("utf-8", errors="strict"))
        except UnicodeDecodeError:
            return None

    decoded: dict[str, Any] = {
        "header_hex": data[:10].hex(),
        "string_fields": fields,
        "trailing_hex": data[offset:].hex(),
    }
    if len(fields) >= 3:
        decoded.update(
            {
                "product_type": fields[0],
                "application_software": fields[1],
                "product_number": fields[2],
            }
        )
    return decoded


def decode_class_0a(apdu: bytes) -> dict[str, Any] | None:
    """Normalize Class 0x0A payload shape without assigning field semantics."""

    if not apdu or apdu[0] != DATA_OBJECT_CLASS:
        return None
    if len(apdu) < 2:
        return {"payload_length": None, "payload_hex": "", "length_valid": False}

    payload_length = apdu[1]
    payload = apdu[2:]
    result: dict[str, Any] = {
        "payload_length": payload_length,
        "payload_hex": payload.hex(),
        "length_valid": payload_length == len(payload),
    }

    if apdu.startswith(b"\x0a\x81") or apdu == b"\x0a\x01\x04":
        result["response_kind"] = "unclassified_short_response"
        result["response_apdu_hex"] = apdu.hex()

    if (
        len(payload) >= 7
        and payload[4:6] == b"\x00\x00"
        and payload[6] == len(payload) - 7
    ):
        result["envelope"] = {
            "meta0": payload[0:2].hex(),
            "type": payload[2:4].hex(),
            "data_length": payload[6],
            "data_hex": payload[7:].hex(),
        }
    return result


def decode_object_data(object_id: str, data: bytes) -> dict[str, Any] | None:
    """Decode only fields supported by the current LC-231 evidence set.

    Unknown offsets remain explicitly named so exploratory output does not turn
    a stable byte position into an unsupported semantic claim.
    """

    normalized_id = object_id.lower().replace(" ", "")

    if normalized_id == "5d0000" and len(data) == 12:
        return {
            "shared_controller_counter": int.from_bytes(data[0:4], "big"),
            "total_on_time_seconds": int.from_bytes(data[4:8], "big"),
            "active_pumping_time_seconds": int.from_bytes(data[8:12], "big"),
        }

    if normalized_id in ("5d0001", "5d0002") and len(data) == 28:
        return {
            "total_starts": int.from_bytes(data[0:4], "big"),
            "starts_latest_hour": int.from_bytes(data[4:6], "big"),
            "starts_latest_24_hours": int.from_bytes(
                data[6:8], "big"
            ),
            "total_runtime_seconds": int.from_bytes(data[8:12], "big"),
            "runtime_latest_24_hours_seconds": int.from_bytes(
                data[12:16], "big"
            ),
            "latest_runtime_seconds": int.from_bytes(data[16:20], "big"),
            "shared_controller_counter": int.from_bytes(data[20:24], "big"),
            "controller_total_on_time_seconds": int.from_bytes(data[24:28], "big"),
        }

    if normalized_id in ("5d0051", "5d0052") and len(data) == 12:
        return {
            "today_starts": int.from_bytes(data[0:2], "big"),
            "yesterday_starts": int.from_bytes(data[2:4], "big"),
            "today_runtime_seconds": int.from_bytes(data[4:8], "big"),
            "yesterday_runtime_seconds": int.from_bytes(data[8:12], "big"),
        }

    if normalized_id in ("5d00d3", "5d00d4") and len(data) == 12:
        latched_current, latched_power_factor, latched_power = struct.unpack(
            ">fff", data
        )
        return {
            "latched_current_amperes": finite_float(latched_current),
            "latched_power_factor": finite_float(latched_power_factor),
            "latched_power_watts": finite_float(latched_power),
        }

    if normalized_id == "5d0093" and len(data) == 13:
        voltage_0, voltage_1, voltage_2, mode = struct.unpack(">fffB", data)
        return {
            "phase_to_neutral_voltage": finite_float(voltage_0),
            "phase_to_phase_voltage": finite_float(voltage_1),
            "field_08_voltage": finite_float(voltage_2),
            "field_12_u8": mode,
            "mains_connection_name": MAINS_CONNECTION_NAMES.get(mode),
        }

    if normalized_id == "5a0003" and len(data) == 13:
        return {
            "water_level_metres": finite_float(struct.unpack(">f", data[:4])[0]),
            "field_04_09_hex": data[4:10].hex(),
            "water_above_stop_level": bool(data[10]),
            "field_10_u8": data[10],
            "field_04_10_hex": data[4:11].hex(),
            "running_flags_raw": data[11],
            "pump_1_running": bool(data[11] & 0x20),
            "pump_2_running": bool(data[11] & 0x40),
            "field_12_u8": data[12],
            "field_04_12_hex": data[4:].hex(),
        }

    if normalized_id == "520c00" and len(data) == 21:
        field_08, level, input_current = struct.unpack(">fff", data[8:20])
        return {
            "field_00_07_hex": data[0:8].hex(),
            "field_08_float": finite_float(field_08),
            "level_metres": finite_float(level),
            "analog_input_current_milliamperes": finite_float(input_current),
            "field_20_u8": data[20],
        }

    if normalized_id.startswith("520c") and len(data) == 1:
        terminal_index = int(normalized_id[-2:], 16)
        if terminal_index in IO_TERMINAL_NAMES:
            return {
                "terminal": IO_TERMINAL_NAMES[terminal_index],
                "active": bool(data[0]),
                "state_raw": data[0],
            }

    if normalized_id == "520800" and len(data) == 16:
        minimum, maximum = struct.unpack(">ff", data[6:14])
        return {
            "terminal": IO_TERMINAL_NAMES[0],
            "enabled": bool(data[0]),
            "field_01_u8": data[1],
            "field_02_u16": int.from_bytes(data[2:4], "big"),
            "field_04_u16": int.from_bytes(data[4:6], "big"),
            "minimum_metres": finite_float(minimum),
            "maximum_metres": finite_float(maximum),
            "field_14_u16": int.from_bytes(data[14:16], "big"),
        }

    if normalized_id.startswith("5208") and len(data) == 4:
        terminal_index = int(normalized_id[-2:], 16)
        if terminal_index in IO_TERMINAL_NAMES:
            assignment_kind = data[1]
            function_code = int.from_bytes(data[2:4], "big")
            return {
                "terminal": IO_TERMINAL_NAMES[terminal_index],
                "enabled": bool(data[0]),
                "assignment_kind_code": assignment_kind,
                "assignment_kind_name": IO_ASSIGNMENT_KIND_NAMES.get(
                    assignment_kind
                ),
                "function_code": function_code,
                "function_name": IO_FUNCTION_NAMES.get(function_code),
            }

    if normalized_id == "522400" and len(data) >= 2:
        record_count = int.from_bytes(data[0:2], "big")
        if len(data) == 2 + record_count * 2:
            return {
                "record_count": record_count,
                "records_u16": [
                    int.from_bytes(data[offset : offset + 2], "big")
                    for offset in range(2, len(data), 2)
                ],
                "evidence_status": "count-prefixed structure only",
            }

    if normalized_id in ("524e21", "524e61") and len(data) >= 2:
        record_count = int.from_bytes(data[0:2], "big")
        if len(data) == 2 + record_count * 4:
            return {
                "record_count": record_count,
                "records_u16_pairs": [
                    {
                        "field_00": int.from_bytes(data[offset : offset + 2], "big"),
                        "field_02": int.from_bytes(
                            data[offset + 2 : offset + 4], "big"
                        ),
                    }
                    for offset in range(2, len(data), 4)
                ],
                "evidence_status": "count-prefixed structure only",
            }

    if normalized_id in ("570045", "570085") and len(data) == 41:
        values = struct.unpack(">fffffffff", data[:36])
        return {
            "voltage_ac_v": finite_float(values[0]),
            "field_04_float": finite_float(values[1]),
            "current_amperes": finite_float(values[2]),
            "power_watts": finite_float(values[3]),
            "field_16_float": finite_float(values[4]),
            "field_20_float": finite_float(values[5]),
            "field_24_float": finite_float(values[6]),
            "field_28_float": finite_float(values[7]),
            "field_32_float": finite_float(values[8]),
            "field_36_40_hex": data[36:].hex(),
        }

    if normalized_id in ("570047", "570087") and len(data) == 8:
        value = finite_float(struct.unpack(">f", data[0:4])[0])
        return {
            "live_power_factor": value,
            "field_04_u32": int.from_bytes(data[4:8], "big"),
            "field_04_07_hex": data[4:8].hex(),
        }

    if normalized_id == "5d0096" and len(data) == 24:
        values = struct.unpack(">ffffff", data)
        return {
            f"field_{offset:02d}_float": finite_float(value)
            for offset, value in zip(range(0, 24, 4), values)
        }

    if normalized_id == "5d0091" and len(data) == 4:
        value = int.from_bytes(data, "big")
        return {
            "field_00_u32": value,
            "unavailable_value_candidate": value == 0x7FFFFFFF,
        }

    if normalized_id == "570001" and len(data) == 38:
        return {
            "field_00_unidentified_temperature": finite_float(
                struct.unpack(">f", data[0:4])[0]
            ),
            "field_04_17_hex": data[4:18].hex(),
            "field_18_pump_1_temperature": finite_float(
                struct.unpack(">f", data[18:22])[0]
            ),
            "field_22_29_hex": data[22:30].hex(),
            "field_30_pump_2_temperature": finite_float(
                struct.unpack(">f", data[30:34])[0]
            ),
            "field_34_37_hex": data[34:38].hex(),
            "evidence_status": "pump temperatures mapped; offset 0 source unknown",
        }

    if normalized_id in ("570043", "570083") and len(data) == 5:
        return {
            "pump_number": 1 if normalized_id == "570043" else 2,
            "field_00_u8": data[0],
            "field_01_float": finite_float(struct.unpack(">f", data[1:5])[0]),
        }

    if normalized_id == "5d0041" and len(data) == 7:
        return {
            "field_00_u8_service_enabled_candidate": data[0],
            "field_01_u8": data[1],
            "field_02_u8_pump_count_candidate": data[2],
            "field_03_u16_service_hours_candidate": int.from_bytes(
                data[3:5], "big"
            ),
            "field_05_u16_service_hours_candidate": int.from_bytes(
                data[5:7], "big"
            ),
            "evidence_status": "service hypothesis from GO and Modbus vocabulary",
        }

    if normalized_id == "5d0044" and len(data) >= 2:
        record_count = int.from_bytes(data[0:2], "big")
        if len(data) == 2 + record_count * 8:
            return {
                "record_count": record_count,
                "records_u32": [
                    {
                        "field_00": int.from_bytes(
                            data[offset : offset + 4], "big"
                        ),
                        "field_04": int.from_bytes(
                            data[offset + 4 : offset + 8], "big"
                        ),
                    }
                    for offset in range(2, len(data), 8)
                ],
                "evidence_status": (
                    "service hypothesis; 60000 values match 1000 hours in minutes"
                ),
            }

    if normalized_id == "580019" and len(data) == 10:
        return {
            "fields_u16": [
                int.from_bytes(data[offset : offset + 2], "big")
                for offset in range(0, 10, 2)
            ],
            "field_00_09_hex": data.hex(),
        }

    if normalized_id in ("580000", "58000b") and len(data) % 2 == 0:
        raw_codes = [
            int.from_bytes(data[offset : offset + 2], "big")
            for offset in range(0, len(data), 2)
        ]
        return {
            "active_codes": [code for code in raw_codes if code != 0],
            "raw_codes_u16": raw_codes,
            "data_hex": data.hex(),
        }

    if normalized_id == "5e0064" and len(data) == 10:
        prefix = 1
        year = int.from_bytes(data[prefix : prefix + 2], "big")
        month, day, hour, minute, second = data[prefix + 2 : prefix + 7]
        centiseconds = data[8]
        decoded: dict[str, Any] = {
            "year": year,
            "month": month,
            "day": day,
            "hour": hour,
            "minute": minute,
            "second": second,
            "centiseconds": centiseconds,
            "field_09_u8": data[9],
            "automatic_time_setting_enabled": bool(data[0]),
        }
        try:
            decoded["last_clock_synchronization"] = datetime(
                year,
                month,
                day,
                hour,
                minute,
                second,
                microsecond=centiseconds * 10_000,
            ).isoformat(timespec="milliseconds")
        except ValueError:
            decoded["last_clock_synchronization"] = None
        return decoded

    if normalized_id == "5e0065" and len(data) == 12:
        year = int.from_bytes(data[0:2], "big")
        month, day, hour, minute, second = data[2:7]
        centiseconds = data[7]
        weekday_iso = data[9]
        weekday_names = (
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        )
        decoded = {
            "year": year,
            "month": month,
            "day": day,
            "hour": hour,
            "minute": minute,
            "second": second,
            "centiseconds": centiseconds,
            "field_08_u8": data[8],
            "weekday_iso": weekday_iso,
            "weekday_name": (
                weekday_names[weekday_iso - 1]
                if 1 <= weekday_iso <= len(weekday_names)
                else None
            ),
            "field_10_u8": data[10],
            "field_11_u8": data[11],
            "automatic_time_setting_enabled_mirror": {0: False, 1: True}.get(
                data[11]
            ),
        }
        try:
            decoded["local_datetime"] = datetime(
                year,
                month,
                day,
                hour,
                minute,
                second,
                microsecond=centiseconds * 10_000,
            ).isoformat(timespec="milliseconds")
        except ValueError:
            decoded["local_datetime"] = None
        return decoded

    if normalized_id == "5e0066" and len(data) == 10:
        month_names = (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        )
        weekday_names = (
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        )
        week_names = ("first", "second", "third", "fourth", "last")
        start_month, start_weekday, start_week, start_hour = data[1:5]
        end_month, end_weekday, end_week, end_hour = data[5:9]
        fields_valid = (
            1 <= start_month <= len(month_names)
            and 1 <= start_weekday <= len(weekday_names)
            and 1 <= start_week <= len(week_names)
            and start_hour <= 23
            and 1 <= end_month <= len(month_names)
            and 1 <= end_weekday <= len(weekday_names)
            and 1 <= end_week <= len(week_names)
            and end_hour <= 23
        )
        decoded = {
            "daylight_saving_schedule_enabled": bool(data[0]),
            "start_month": start_month,
            "start_month_name": (
                month_names[start_month - 1]
                if 1 <= start_month <= len(month_names)
                else None
            ),
            "start_weekday_iso": start_weekday,
            "start_weekday_name": (
                weekday_names[start_weekday - 1]
                if 1 <= start_weekday <= len(weekday_names)
                else None
            ),
            "start_week_selector": start_week,
            "start_week_name": (
                week_names[start_week - 1]
                if 1 <= start_week <= len(week_names)
                else None
            ),
            "start_hour": start_hour,
            "end_month": end_month,
            "end_month_name": (
                month_names[end_month - 1]
                if 1 <= end_month <= len(month_names)
                else None
            ),
            "end_weekday_iso": end_weekday,
            "end_weekday_name": (
                weekday_names[end_weekday - 1]
                if 1 <= end_weekday <= len(weekday_names)
                else None
            ),
            "end_week_selector": end_week,
            "end_week_name": (
                week_names[end_week - 1]
                if 1 <= end_week <= len(week_names)
                else None
            ),
            "end_hour": end_hour,
            "adjustment_minutes": data[9],
            "fields_u8": list(data),
            "data_hex": data.hex(),
            "evidence_status": "confirmed daylight-saving schedule",
        }
        decoded["dst_schedule"] = (
            (
                f"{'Enabled' if data[0] else 'Disabled'} | "
                f"{decoded['start_week_name']} {decoded['start_weekday_name']} "
                f"in {decoded['start_month_name']} at {start_hour:02d}:00 | "
                f"{decoded['end_week_name']} {decoded['end_weekday_name']} "
                f"in {decoded['end_month_name']} at {end_hour:02d}:00 | "
                f"Shift: {data[9]} min"
            )
            if fields_valid
            else None
        )
        return decoded

    if normalized_id == "5e0067" and len(data) == 2:
        hours = int.from_bytes(data[0:1], "big", signed=True)
        minutes = data[1]
        decoded = {
            "standard_utc_offset_hours": hours,
            "standard_utc_offset_minutes": minutes,
            "standard_utc_offset": (
                f"UTC{'-' if hours < 0 else '+'}{abs(hours):02d}:{minutes:02d}"
                if minutes <= 59
                else None
            ),
            "data_hex": data.hex(),
            "evidence_status": (
                "confirmed for non-negative offsets; negative offsets unreliable"
            ),
        }
        if data == b"\x00\x00":
            decoded["warning"] = ZERO_UTC_OFFSET_WARNING
        return decoded

    if normalized_id == "5a0000" and len(data) == 37:
        levels = struct.unpack(">fffff", data[9:29])
        return {
            "field_00_08_hex": data[0:9].hex(),
            "dry_running_level_metres": finite_float(levels[0]),
            "pump_1_stop_level_metres": finite_float(levels[1]),
            "pump_1_start_level_metres": finite_float(levels[2]),
            "pump_2_start_level_metres": finite_float(levels[3]),
            "high_level_metres": finite_float(levels[4]),
            "high_level_after_run_seconds": int.from_bytes(data[29:31], "big"),
            "stop_delay_seconds": int.from_bytes(data[31:33], "big"),
            "field_33_u32": int.from_bytes(data[33:37], "big"),
        }

    if normalized_id == "560001" and len(data) == 4:
        return {
            "automatic_return_enabled": bool(data[0]),
            "forced_start_seconds": int.from_bytes(data[1:3], "big"),
            "return_mode": data[3],
            "return_mode_name": MANUAL_RETURN_MODE_NAMES.get(data[3]),
        }

    if normalized_id == "560002" and len(data) == 7:
        return {
            "field_00_u8": data[0],
            "anti_seizing_interval_seconds": int.from_bytes(data[1:5], "big"),
            "anti_seizing_run_seconds": int.from_bytes(data[5:7], "big"),
        }

    if normalized_id == "560000" and len(data) == 6:
        pump_1_mode = data[2:4]
        pump_2_mode = data[4:6]
        return {
            "field_00_01_hex": data[0:2].hex(),
            "pump_1_selection_hex": pump_1_mode.hex(),
            "pump_1_selection_name": PUMP_CONTROL_SELECTION_NAMES.get(pump_1_mode),
            "pump_2_selection_hex": pump_2_mode.hex(),
            "pump_2_selection_name": PUMP_CONTROL_SELECTION_NAMES.get(pump_2_mode),
        }

    if normalized_id == "560003" and len(data) == 2:
        return {
            "field_00_u8": data[0],
            "field_01_u8": data[1],
            "field_00_01_hex": data.hex(),
        }

    if normalized_id == "5b0001" and len(data) == 3:
        return {
            "field_00_u8": data[0],
            "inter_pump_delay_seconds": data[1],
            "max_running_pumps": data[2],
        }

    if normalized_id == "5b0002" and len(data) == 9:
        return {
            "run_and_pause_enabled": bool(data[0]),
            "max_run_seconds": int.from_bytes(data[1:5], "big"),
            "field_05_u16": int.from_bytes(data[5:7], "big"),
            "pump_pause_seconds": int.from_bytes(data[7:9], "big"),
        }

    if normalized_id == "570005" and len(data) == 3:
        return {
            "restart_limit_enabled": bool(data[0]),
            "allowed_restarts": data[1],
            "interval_hours": data[2],
        }

    if normalized_id == "570004" and len(data) == 11:
        return {
            "automatic_alarm_reset_code": data[0],
            "automatic_alarm_reset_name": AUTOMATIC_ALARM_RESET_NAMES.get(
                data[0]
            ),
            "field_01_10_hex": data[1:].hex(),
        }

    if normalized_id == "570007" and len(data) == 25:
        values = struct.unpack(">ffffff", data[1:])
        return {
            "mains_connection_code": data[0],
            "mains_connection_name": MAINS_CONNECTION_NAMES.get(data[0]),
            **{
                f"field_{offset:02d}_float": finite_float(value)
                for offset, value in zip(range(1, 25, 4), values)
            },
        }

    if normalized_id in ("560067", "560068") and len(data) == 22:
        return {
            "limit_number": 1 if normalized_id == "560067" else 2,
            "enabled": bool(data[0]),
            "field_01_21_hex": data[1:].hex(),
        }

    if normalized_id in ("560069", "56006a") and len(data) == 19:
        field_09, level = struct.unpack(">ff", data[9:17])
        return {
            "limit_number": 1 if normalized_id == "560069" else 2,
            "field_00_08_hex": data[0:9].hex(),
            "field_09_float": finite_float(field_09),
            "level_correlated_metres": finite_float(level),
            "field_17_u16": int.from_bytes(data[17:19], "big"),
        }

    if normalized_id == "570008" and len(data) == 5:
        return {
            "starts_per_hour_limits_enabled": bool(data[0]),
            "warning_limit": int.from_bytes(data[1:3], "big"),
            "alarm_limit": int.from_bytes(data[3:5], "big"),
        }

    if normalized_id == "5700d2" and len(data) == 1:
        return {"allowed_blocked_pump_restarts": data[0]}

    if normalized_id in ("570074", "5700b4") and len(data) == 15:
        return {
            "field_00_02_hex": data[0:3].hex(),
            "nominal_current_amperes": finite_float(
                struct.unpack(">f", data[3:7])[0]
            ),
            "trip_iec_class": data[7],
            "overheat_protection_code": data[8],
            "field_09_14_hex": data[9:15].hex(),
        }

    if normalized_id in ("570075", "5700b5") and len(data) == 16:
        return {
            "field_00_u8": data[0],
            "field_01_03_hex": data[1:4].hex(),
            "nominal_current_amperes": finite_float(
                struct.unpack(">f", data[4:8])[0]
            ),
            "trip_iec_class": data[8],
            "overheat_protection_code": data[9],
            "field_10_15_hex": data[10:16].hex(),
        }

    if normalized_id in ("5801cc", "5801cd") and len(data) == 19:
        values = struct.unpack(">ffHff", data[1:])
        qualifier = "under" if normalized_id == "5801cc" else "over"
        return {
            f"{qualifier}_voltage_detection_enabled": bool(data[0]),
            "phase_to_neutral_warning_voltage": finite_float(values[0]),
            "phase_to_neutral_alarm_voltage": finite_float(values[1]),
            "detection_time_seconds": values[2],
            "phase_to_phase_warning_voltage": finite_float(values[3]),
            "phase_to_phase_alarm_voltage": finite_float(values[4]),
        }

    if normalized_id == "5827d7" and len(data) == 7:
        return {
            "latest_event_id": int.from_bytes(data[0:2], "big"),
            "event_count": int.from_bytes(data[2:4], "big"),
            "event_capacity": int.from_bytes(data[4:6], "big"),
            "field_06_u8": data[6],
        }

    if (
        normalized_id.startswith("5827")
        and normalized_id != "5827d7"
        and len(data) == 13
    ):
        event_code = int.from_bytes(data[2:4], "big")
        event_source = data[4]
        condition = data[6]
        timestamp = int.from_bytes(data[7:11], "big")
        detail = int.from_bytes(data[11:13], "big")
        return {
            "event_id": int.from_bytes(data[0:2], "big"),
            "event_code": event_code,
            "event_code_name": EVENT_CODE_NAMES.get(event_code),
            "event_code_modbus_candidate_name": (
                MODBUS_EVENT_CODE_CANDIDATE_NAMES.get(event_code)
            ),
            "event_source": event_source,
            "event_source_name": EVENT_SOURCE_NAMES.get(event_source),
            "device_number": data[5],
            "event_type_and_condition": condition,
            "event_condition_name": EVENT_CONDITION_NAMES.get(condition),
            "timestamp_scalar": timestamp,
            "controller_local_time": datetime.fromtimestamp(
                timestamp, timezone.utc
            ).replace(tzinfo=None).isoformat(),
            "detail_u16": detail,
            "detail_name": EVENT_DETAIL_NAMES.get((event_code, detail)),
        }

    return None


def decode_object_reply(frame: Frame, object_id: str) -> dict[str, Any] | None:
    """Decode an allowlisted sequential Class 0x0A read reply."""

    if not matches_object_reply(frame, object_id):
        return None
    class_0a = decode_class_0a(frame.apdu)
    if class_0a is None:
        return None
    envelope = class_0a.get("envelope")
    if not isinstance(envelope, dict):
        return None
    data_hex = envelope.get("data_hex")
    if not isinstance(data_hex, str):
        return None
    return decode_object_data(object_id, bytes.fromhex(data_hex))


def matches_object_reply(frame: Frame, object_id: str) -> bool:
    """Match a sequential reply using its object-specific envelope shape."""

    normalized_id = object_id.lower().replace(" ", "")
    expected = OBJECT_REPLY_SHAPES.get(normalized_id)
    if expected is None:
        return False
    return matches_reply_shape(frame, expected)


def matches_reply_shape(
    frame: Frame,
    expected_shape: tuple[str | None, str | None, int | None],
) -> bool:
    """Match a sequential Class-10 reply to an observed envelope shape."""

    if not is_sequential_read_reply(frame):
        return False
    class_0a = decode_class_0a(frame.apdu)
    if class_0a is None:
        return False
    envelope = class_0a.get("envelope")
    if not isinstance(envelope, dict):
        return False
    actual = (
        envelope.get("meta0"),
        envelope.get("type"),
        envelope.get("data_length"),
    )
    return all(
        expected is None or seen == expected
        for seen, expected in zip(actual, expected_shape)
    )


def is_sequential_read_reply(frame: Frame) -> bool:
    """Return whether a frame can be associated with the current read by timing."""

    return (
        frame.crc_valid
        and frame.start == REPLY_START
        and frame.destination == CLIENT_ADDRESS
        and frame.source == SERVICE_ADDRESS
        and len(frame.apdu) >= 2
        and frame.apdu[0] == DATA_OBJECT_CLASS
        and frame.apdu[1] == len(frame.apdu) - 2
    )


class FrameStream:
    """Reassemble fragmented or coalesced notification bytes using GENI LEN."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    @property
    def pending(self) -> bytes:
        return bytes(self._buffer)

    def feed(self, chunk: bytes) -> list[bytes]:
        self._buffer.extend(chunk)
        frames: list[bytes] = []

        while self._buffer:
            if self._buffer[0] not in KNOWN_STARTS:
                next_start = next(
                    (
                        index
                        for index, byte in enumerate(self._buffer[1:], start=1)
                        if byte in KNOWN_STARTS
                    ),
                    None,
                )
                if next_start is None:
                    self._buffer.clear()
                    break
                del self._buffer[:next_start]

            if len(self._buffer) < 2:
                break

            total_size = self._buffer[1] + 4
            if len(self._buffer) < total_size:
                break

            frames.append(bytes(self._buffer[:total_size]))
            del self._buffer[:total_size]

        return frames


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def redact_identifier(message: str, identifier: str | None) -> str:
    """Keep an explicit private BLE identifier out of output and errors."""

    if not identifier:
        return message
    return message.replace(identifier, "<device identifier>")


class MemoryLog:
    """Collect transport diagnostics inside the final JSON document."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def write(self, event: str, **values: Any) -> None:
        self.rows.append({"timestamp": utc_now(), "event": event, **values})


def require_vendor_characteristic(client: Any) -> Any:
    characteristic = client.services.get_characteristic(VENDOR_CHARACTERISTIC_UUID)
    if characteristic is None:
        raise RuntimeError("Grundfos vendor characteristic was not discovered")

    properties = set(characteristic.properties)
    if "notify" not in properties:
        raise RuntimeError("Grundfos vendor characteristic does not support notify")
    if not properties.intersection(("write", "write-without-response")):
        raise RuntimeError("Grundfos vendor characteristic is not writable")
    return characteristic


def write_with_response(characteristic: Any) -> bool:
    properties = set(characteristic.properties)
    return "write-without-response" not in properties and "write" in properties


def select_discovered_target(
    discovered: dict[str, tuple[Any, Any]],
    expected_name: str | None,
) -> tuple[Any, str, int, int]:
    """Select one LC-231 advertisement without logging nearby identifiers."""

    name_matches: list[tuple[Any, bool]] = []
    service_matches: list[Any] = []
    for device, advertisement in discovered.values():
        advertised_name = (advertisement.local_name or device.name or "").strip()
        advertised_services = {
            value.lower() for value in (advertisement.service_uuids or [])
        }
        service_data_uuids = {
            value.lower() for value in (advertisement.service_data or {})
        }
        has_service = (
            GRUNDFOS_SERVICE_UUID in advertised_services
            or GRUNDFOS_SERVICE_UUID in service_data_uuids
        )
        if expected_name is not None and advertised_name == expected_name:
            name_matches.append((device, has_service))
        if has_service:
            service_matches.append(device)

    exact_name_and_service = [
        device for device, has_service in name_matches if has_service
    ]
    if len(exact_name_and_service) == 1:
        selection = "exact_name_and_service"
        device = exact_name_and_service[0]
    elif expected_name is not None and len(name_matches) == 1:
        selection = "unique_exact_name"
        device = name_matches[0][0]
    elif len(service_matches) == 1:
        selection = "unique_grundfos_service"
        device = service_matches[0]
    elif not name_matches and not service_matches:
        name_detail = f"exact {expected_name!r} or " if expected_name else ""
        raise RuntimeError(
            f"no {name_detail}Grundfos 0xFE5D advertisement found"
        )
    else:
        raise RuntimeError(
            f"ambiguous advertisements: {len(name_matches)} exact-name and "
            f"{len(service_matches)} Grundfos-service matches; "
            "use --address to select one"
        )

    return device, selection, len(name_matches), len(service_matches)


async def scan_target(
    args: argparse.Namespace,
    log: MemoryLog,
    *,
    fallback_from: str | None = None,
) -> Any:
    """Resolve one current advertisement, failing closed on ambiguity."""

    try:
        from bleak import BleakScanner
    except ImportError as error:
        raise RuntimeError("Bleak is not installed; run: python -m pip install bleak") from error

    expected_name = args.scan_name.strip() if args.scan_name else None
    discovered = await BleakScanner.discover(
        timeout=args.scan_timeout,
        return_adv=True,
    )
    device, selection, name_count, service_count = select_discovered_target(
        discovered,
        expected_name,
    )
    log.write(
        "target",
        selection=selection,
        name_filter=expected_name,
        exact_name_matches=name_count,
        grundfos_service_matches=service_count,
        fallback_from=fallback_from,
    )
    return device


async def resolve_target(args: argparse.Namespace, log: MemoryLog) -> Any:
    if args.address:
        log.write("target", selection="explicit_identifier")
        return args.address
    return await scan_target(args, log)


@asynccontextmanager
async def connect_with_discovery_fallback(
    client_factory: Any,
    target: Any,
    args: argparse.Namespace,
    log: MemoryLog,
) -> AsyncIterator[tuple[Any, Any]]:
    """Connect to the selected target, then retry once through discovery."""

    def new_client(selected_target: Any) -> Any:
        return client_factory(
            selected_target,
            timeout=args.connect_timeout,
            pair=True,
        )

    entered = False
    try:
        async with new_client(target) as client:
            entered = True
            yield client, target
        return
    except Exception as error:
        if entered:
            raise
        log.write(
            "connection_target_unresolved",
            error_type=type(error).__name__,
            message=redact_identifier(str(error), args.address),
            action="scan_for_current_advertisement",
        )

    scanned_target = await scan_target(
        args,
        log,
        fallback_from="unresolved_saved_target",
    )
    async with new_client(scanned_target) as client:
        yield client, scanned_target


def default_output_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    return Path(".local") / "dumps" / f"lc231-dump-{stamp}.json"


def build_request(apdu: bytes, *, destination: int) -> bytes:
    """Wrap one read APDU in an LC-231 GENI frame and CRC."""

    if destination not in (SERVICE_ADDRESS, BROADCAST_ADDRESS):
        raise ValueError("unsupported destination")
    body = bytes((destination, CLIENT_ADDRESS)) + apdu
    without_crc = bytes((REQUEST_START, len(body))) + body
    return without_crc + crc16_ccitt(without_crc[1:]).to_bytes(2, "big")


def build_grouped_request(query: GroupedQuery) -> bytes:
    apdu = bytearray()
    for class_id, control_flags, selectors in query.groups:
        if not selectors or len(selectors) > 0x3F:
            raise ValueError(f"invalid selector count in {query.name}")
        if control_flags not in (0x00, 0xC0):
            raise ValueError(f"invalid control flags in {query.name}")
        apdu.extend((class_id, control_flags | len(selectors), *selectors))
    return build_request(bytes(apdu), destination=query.destination)


def reply_matches(frame: Frame, expected_class: int) -> bool:
    return (
        frame.crc_valid
        and frame.start == REPLY_START
        and frame.destination == CLIENT_ADDRESS
        and frame.source == SERVICE_ADDRESS
        and bool(frame.apdu)
        and frame.apdu[0] == expected_class
    )


async def exchange(
    client: Any,
    characteristic: Any,
    queue: asyncio.Queue[tuple[str, bytes]],
    log: MemoryLog,
    *,
    name: str,
    request_raw: bytes,
    timeout: float,
) -> tuple[Frame | None, dict[str, Any]]:
    """Send one serialized request and return its structurally matching reply."""

    while not queue.empty():
        received_at, stale_raw = queue.get_nowait()
        log.write(
            "rx_unassociated",
            received_at=received_at,
            raw_hex=stale_raw.hex(),
        )

    request = inspect_frame(request_raw)
    record: dict[str, Any] = {
        "name": name,
        "request_raw_hex": request_raw.hex(),
        "request": request.normalized(),
    }
    chunks = tuple(
        request_raw[offset : offset + ATT_CHUNK_SIZE]
        for offset in range(0, len(request_raw), ATT_CHUNK_SIZE)
    )
    record["request_att_chunks_hex"] = [chunk.hex() for chunk in chunks]
    log.write(
        "tx",
        name=name,
        raw_hex=request_raw.hex(),
        att_chunks_hex=record["request_att_chunks_hex"],
    )

    for chunk in chunks:
        await client.write_gatt_char(
            characteristic,
            chunk,
            response=write_with_response(characteristic),
        )

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            record["error"] = "timeout"
            log.write("timeout", name=name, timeout_seconds=timeout)
            return None, record
        try:
            received_at, response_raw = await asyncio.wait_for(
                queue.get(), remaining
            )
        except asyncio.TimeoutError:
            record["error"] = "timeout"
            log.write("timeout", name=name, timeout_seconds=timeout)
            return None, record

        try:
            response = inspect_frame(response_raw)
        except FrameError as error:
            log.write(
                "rx_invalid",
                name=name,
                received_at=received_at,
                raw_hex=response_raw.hex(),
                error=str(error),
            )
            continue

        if not reply_matches(response, request.apdu[0]):
            log.write(
                "rx_unassociated",
                name=name,
                received_at=received_at,
                raw_hex=response_raw.hex(),
                normalized=response.normalized(),
            )
            continue

        record["response_raw_hex"] = response_raw.hex()
        record["response"] = response.normalized()
        return response, record


def print_values(values: dict[str, Any] | None) -> None:
    if not values:
        return
    for key, value in values.items():
        if value is None:
            continue
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
        print(f"  {key} = {rendered}")


async def pause(interval: float) -> None:
    if interval:
        await asyncio.sleep(interval)


async def dump_strings(
    client: Any,
    characteristic: Any,
    queue: asyncio.Queue[tuple[str, bytes]],
    log: MemoryLog,
    document: dict[str, Any],
    args: argparse.Namespace,
) -> None:
    print("\n=== Product strings ===")
    for string_id, label, status in STRING_IDS:
        response, exchange_record = await exchange(
            client,
            characteristic,
            queue,
            log,
            name=f"string_{string_id:02x}",
            request_raw=build_string_read(
                string_id,
                destination=BROADCAST_ADDRESS,
            ),
            timeout=args.response_timeout,
        )
        value = decode_string_reply(response) if response is not None else None
        row = {
            "string_id": f"{string_id:02x}",
            "label": label,
            "status": status,
            "value": value,
            "exchange": exchange_record,
        }
        if response is not None and value is None:
            row["decode_error"] = "invalid Class 0x07 string reply"
        document["strings"].append(row)
        print(f"07/{string_id:02x} {label} [{status}] = {value!r}")
        await pause(args.interval)


async def dump_identification_bundle(
    client: Any,
    characteristic: Any,
    queue: asyncio.Queue[tuple[str, bytes]],
    log: MemoryLog,
    document: dict[str, Any],
    args: argparse.Namespace,
) -> None:
    print("\n=== Segmented product-identification object ===")
    exchanges: list[dict[str, Any]] = []
    response, exchange_record = await exchange(
        client,
        characteristic,
        queue,
        log,
        name=BUNDLE_HEAD_ID,
        request_raw=build_observed_object_read(
            BUNDLE_HEAD_ID,
            BUNDLE_OBJECT_IDS,
            destination=BROADCAST_ADDRESS,
        ),
        timeout=args.response_timeout,
    )
    exchanges.append(exchange_record)
    head = decode_segmented_object_head(response) if response is not None else None
    if head is None:
        document["identification_bundle"] = {
            "error": "invalid or missing head",
            "exchanges": exchanges,
        }
        print("59000f: invalid or missing head")
        return

    chunks = [bytes.fromhex(head["data_hex"])]
    for object_id in BUNDLE_CONTINUATION_IDS:
        response, exchange_record = await exchange(
            client,
            characteristic,
            queue,
            log,
            name=object_id,
            request_raw=build_observed_object_read(
                object_id,
                BUNDLE_OBJECT_IDS,
                destination=SERVICE_ADDRESS,
            ),
            timeout=args.response_timeout,
        )
        exchanges.append(exchange_record)
        chunk = (
            decode_segmented_object_continuation(response)
            if response is not None
            else None
        )
        if chunk is None:
            document["identification_bundle"] = {
                "error": f"invalid or missing continuation {object_id}",
                "head": head,
                "exchanges": exchanges,
            }
            print(f"{object_id}: invalid or missing continuation")
            return
        chunks.append(chunk)

    data = b"".join(chunks)
    decoded = decode_identification_bundle(data)
    result = {
        "declared_length": head["declared_data_length"],
        "assembled_length": len(data),
        "data_hex": data.hex(),
        "decoded": decoded,
        "exchanges": exchanges,
    }
    if len(data) != head["declared_data_length"]:
        result["error"] = "assembled length does not match declared length"
    elif decoded is None:
        result["error"] = "bundle structure did not decode"
    document["identification_bundle"] = result
    print_values({"data_hex": data.hex(), "decoded": decoded})


def enrich_grouped_values(groups: list[dict[str, Any]]) -> None:
    for group in groups:
        class_id = int(group["class"], 16)
        for item in group["items"]:
            raw = bytes.fromhex(item["value_hex"])
            item["value_unsigned"] = int.from_bytes(raw, "big")
            if class_id == 0x0E and item["selector"] == 0x1E and len(raw) == 4:
                scalar = int.from_bytes(raw, "big")
                item["controller_local_time"] = datetime.fromtimestamp(
                    scalar, timezone.utc
                ).replace(tzinfo=None).isoformat()


async def dump_grouped_queries(
    client: Any,
    characteristic: Any,
    queue: asyncio.Queue[tuple[str, bytes]],
    log: MemoryLog,
    document: dict[str, Any],
    args: argparse.Namespace,
) -> None:
    print("\n=== Grouped data-item reads ===")
    for query in GROUPED_QUERIES:
        request_raw = build_grouped_request(query)
        response, exchange_record = await exchange(
            client,
            characteristic,
            queue,
            log,
            name=query.name,
            request_raw=request_raw,
            timeout=args.response_timeout,
        )
        decoded = None
        if response is not None:
            decoded = decode_grouped_data_exchange(
                inspect_frame(request_raw), response
            )
            if decoded is not None:
                enrich_grouped_values(decoded)
        row = {
            "name": query.name,
            "decoded": decoded,
            "exchange": exchange_record,
        }
        if response is not None and decoded is None:
            row["decode_error"] = "grouped reply does not match request layout"
        document["grouped_reads"].append(row)
        print(f"\n{query.name}")
        print_values({"decode_error": row.get("decode_error"), "groups": decoded})
        await pause(args.interval)


def object_sections() -> tuple[tuple[str, str, dict[str, str]], ...]:
    configuration = {
        object_id: label
        for object_id, label in AUXILIARY_READ_OBJECTS.items()
        if not object_id.startswith("5827")
    }
    event_log = {
        object_id: label
        for object_id, label in AUXILIARY_READ_OBJECTS.items()
        if object_id.startswith("5827")
    }
    return (
        (
            "Confirmed status, counters, alarms, and warnings",
            "confirmed",
            dict(CONFIRMED_READ_OBJECTS),
        ),
        ("Configuration", "confirmed_or_partial", configuration),
        ("Alarm and warning history", "confirmed_or_partial", event_log),
        ("Hypotheses and raw unknowns", "candidate", dict(CANDIDATE_READ_OBJECTS)),
        (
            "Related-product candidates",
            "related_product_only",
            dict(SOURCE_DERIVED_READ_OBJECTS),
        ),
    )


async def dump_one_object(
    client: Any,
    characteristic: Any,
    queue: asyncio.Queue[tuple[str, bytes]],
    log: MemoryLog,
    *,
    object_id: str,
    label: str,
    evidence: str,
    timeout: float,
    snapshot_object: SnapshotObject | None = None,
    snapshot_ids: Sequence[str] = (),
) -> dict[str, Any]:
    request_raw = (
        build_observed_object_read(
            object_id,
            snapshot_ids,
            destination=SERVICE_ADDRESS,
        )
        if snapshot_object is not None
        else build_object_read(object_id, destination=SERVICE_ADDRESS)
    )
    response, exchange_record = await exchange(
        client,
        characteristic,
        queue,
        log,
        name=object_id,
        request_raw=request_raw,
        timeout=timeout,
    )
    result: dict[str, Any] = {
        "object_id": object_id,
        "label": label,
        "evidence": evidence,
        "exchange": exchange_record,
    }
    if response is None:
        result["error"] = exchange_record.get("error", "missing response")
        return result

    class_0a = decode_class_0a(response.apdu)
    result["class_0a"] = class_0a
    if snapshot_object is not None:
        shape_valid: bool | None = matches_reply_shape(
            response, snapshot_object.reply_shape
        )
    elif object_id in SOURCE_DERIVED_READ_OBJECTS:
        shape_valid = None
    else:
        shape_valid = matches_object_reply(response, object_id)
    result["expected_shape_valid"] = shape_valid
    if shape_valid is False:
        result["validation_error"] = "reply envelope does not match expected shape"

    envelope = class_0a.get("envelope") if class_0a else None
    if isinstance(envelope, dict):
        data_hex = envelope.get("data_hex")
        if isinstance(data_hex, str):
            result["data_hex"] = data_hex
            if shape_valid is not False:
                result["decoded"] = decode_object_data(
                    object_id, bytes.fromhex(data_hex)
                )
    elif class_0a is not None:
        result["response_kind"] = class_0a.get("response_kind", "unknown")
    return result


async def dump_objects(
    client: Any,
    characteristic: Any,
    queue: asyncio.Queue[tuple[str, bytes]],
    log: MemoryLog,
    document: dict[str, Any],
    args: argparse.Namespace,
) -> None:
    for heading, evidence, objects in object_sections():
        print(f"\n=== {heading} ===")
        for object_id, label in objects.items():
            row = await dump_one_object(
                client,
                characteristic,
                queue,
                log,
                object_id=object_id,
                label=label,
                evidence=evidence,
                timeout=args.response_timeout,
            )
            document["objects"].append(row)
            if object_id == "5e0067" and row.get("data_hex") == "0000":
                log.write(
                    "warning",
                    name=object_id,
                    message=ZERO_UTC_OFFSET_WARNING,
                )
                print(f"WARNING: {ZERO_UTC_OFFSET_WARNING}", file=sys.stderr)
            print(f"\n{object_id} {label} [{evidence}]")
            if "error" in row:
                print_values({"error": row["error"]})
            else:
                print_values(
                    {
                        "validation_error": row.get("validation_error"),
                        "expected_shape_valid": row.get("expected_shape_valid"),
                        "data_hex": row.get("data_hex"),
                        "response_kind": row.get("response_kind"),
                        "decoded": row.get("decoded"),
                    }
                )
            await pause(args.interval)

    if not args.snapshot_objects:
        return
    extras = {
        object_id: value
        for object_id, value in args.snapshot_objects.items()
        if object_id not in READ_OBJECTS and object_id not in BUNDLE_OBJECT_IDS
    }
    if not extras:
        return

    print("\n=== Additional snapshot-catalogue objects (raw) ===")
    for object_id, snapshot_object in sorted(extras.items()):
        label = (
            f"snapshot Type {snapshot_object.object_type} "
            f"version {snapshot_object.object_version}"
        )
        row = await dump_one_object(
            client,
            characteristic,
            queue,
            log,
            object_id=object_id,
            label=label,
            evidence="snapshot_catalogue_raw",
            timeout=args.response_timeout,
            snapshot_object=snapshot_object,
            snapshot_ids=tuple(args.snapshot_objects),
        )
        document["objects"].append(row)
        print(f"\n{object_id} {label} [snapshot_catalogue_raw]")
        print_values(
            {
                "error": row.get("error"),
                "validation_error": row.get("validation_error"),
                "expected_shape_valid": row.get("expected_shape_valid"),
                "data_hex": row.get("data_hex"),
                "decoded": row.get("decoded"),
            }
        )
        await pause(args.interval)


def save_dump(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        json.dump(
            document,
            output,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        output.write("\n")


async def run_dump(args: argparse.Namespace) -> int:
    try:
        from bleak import BleakClient
    except ImportError as error:
        raise RuntimeError(
            "Bleak is not installed; run: python -m pip install bleak"
        ) from error

    output = Path(args.output)
    if output.exists():
        raise RuntimeError(f"output already exists: {output}")

    document: dict[str, Any] = {
        "started_at": utc_now(),
        "finished_at": None,
        "scope": "read_only",
        "transport": [],
        "strings": [],
        "identification_bundle": None,
        "grouped_reads": [],
        "objects": [],
    }
    log = MemoryLog(document["transport"])
    failures = 0
    stream = FrameStream()
    queue: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()

    def on_notify(_characteristic: Any, data: bytearray) -> None:
        received_at = utc_now()
        for raw in stream.feed(bytes(data)):
            queue.put_nowait((received_at, raw))

    try:
        target = await resolve_target(args, log)
        async with connect_with_discovery_fallback(
            BleakClient,
            target,
            args,
            log,
        ) as (client, _connected_target):
            log.write("connected", connected=client.is_connected)
            characteristic = require_vendor_characteristic(client)
            log.write(
                "characteristic",
                uuid=characteristic.uuid,
                properties=sorted(characteristic.properties),
            )
            await client.start_notify(characteristic, on_notify)
            try:
                await dump_strings(
                    client, characteristic, queue, log, document, args
                )
                await dump_identification_bundle(
                    client, characteristic, queue, log, document, args
                )
                await pause(args.interval)
                await dump_grouped_queries(
                    client, characteristic, queue, log, document, args
                )
                await dump_objects(
                    client, characteristic, queue, log, document, args
                )
            finally:
                try:
                    await client.stop_notify(characteristic)
                except Exception as error:
                    log.write(
                        "notify_stop_error",
                        error_type=type(error).__name__,
                        message=redact_identifier(str(error), args.address),
                    )
    except Exception as error:
        failures += 1
        log.write(
            "fatal_error",
            error_type=type(error).__name__,
            message=redact_identifier(str(error), args.address),
        )
        raise
    finally:
        if stream.pending:
            log.write("rx_incomplete", raw_hex=stream.pending.hex())
        failures += sum(
            1
            for row in document["objects"]
            if row.get("error") or row.get("validation_error")
        )
        failures += sum(
            1
            for row in document["strings"]
            if row["exchange"].get("error") or row.get("decode_error")
        )
        failures += sum(
            1
            for row in document["grouped_reads"]
            if row["exchange"].get("error") or row.get("decode_error")
        )
        if isinstance(document["identification_bundle"], dict) and document[
            "identification_bundle"
        ].get("error"):
            failures += 1
        document["finished_at"] = utc_now()
        document["failure_count"] = failures
        save_dump(output, document)
        print(f"\nraw and decoded dump: {output}")

    return 1 if failures else 0


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Dump confirmed, hypothetical, and raw unknown LC-231 values "
            "using read-only BLE requests."
        )
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--address",
        help="BLE MAC address or platform-specific device identifier",
    )
    target.add_argument(
        "--scan-name",
        help="exact advertised name; otherwise require one unique FE5D device",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        help=(
            "optional GO profile snapshot; populated objects outside the "
            "built-in map are also read and retained as raw values"
        ),
    )
    parser.add_argument(
        "--response-timeout",
        type=positive_float,
        default=2.0,
        help="seconds to wait for each reply (default: 2.0)",
    )
    parser.add_argument(
        "--connect-timeout",
        type=positive_float,
        default=20.0,
        help="seconds to allow for connection (default: 20.0)",
    )
    parser.add_argument(
        "--scan-timeout",
        type=positive_float,
        default=15.0,
        help="seconds to scan without an explicit identifier (default: 15.0)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.05,
        help="seconds between ordinary requests (default: 0.05)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_output_path(),
        help="new JSON dump path (default: ignored .local/dumps directory)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.interval < 0:
        parser.error("--interval must be zero or greater")
    args.snapshot_objects = {}
    if args.snapshot is not None:
        try:
            args.snapshot_objects = dict(load_snapshot_objects(args.snapshot))
        except SnapshotError as error:
            parser.error(str(error))

    try:
        exit_code = asyncio.run(run_dump(args))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        exit_code = 130
    except Exception as error:
        print(
            f"dump failed: {redact_identifier(str(error), args.address)}",
            file=sys.stderr,
        )
        exit_code = 2
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
