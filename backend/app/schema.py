"""Hardcoded Redis + Postgres field schema for export field picker."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class FieldDef:
    id: str
    label: str
    group: str
    source: str  # postgres | top.<key> | numeric_io_data.<key> | boolean_io_data.<key> | text_io_data.<key>
    always: bool = False
    default: bool = False


def _f(
    id_: str,
    group: str,
    source: str,
    *,
    label: str | None = None,
    always: bool = False,
    default: bool = False,
) -> FieldDef:
    return FieldDef(
        id=id_,
        label=label or id_,
        group=group,
        source=source,
        always=always,
        default=default,
    )


IDENTITY_FIELDS: list[FieldDef] = [
    _f("battery_number", "Identity", "postgres", always=True),
    _f("imei", "Identity", "postgres", always=True),
]

POSTGRES_FIELDS: list[FieldDef] = [
    _f("center_id", "Center (Postgres)", "postgres", default=True),
    _f("center_name", "Center (Postgres)", "postgres", default=True),
    _f("center_lat", "Center (Postgres)", "postgres", default=True),
    _f("center_long", "Center (Postgres)", "postgres", default=True),
]

TOP_KEYS = [
    "latitude",
    "longitude",
    "device_id",
    "speed",
    "orientation",
    "distance",
    "received_at",
    "created_at",
    "valid_location",
]

TOP_DEFAULTS = {"latitude", "longitude", "valid_location"}

NUMERIC_KEYS = [
    "accelerometer_x_axis",
    "accelerometer_y_axis",
    "accelerometer_z_axis",
    *[f"bms_cell_temperature_{i}" for i in range(1, 7)],
    *[f"bms_cell_voltage_{i}" for i in range(1, 31)],
    "bms_current",
    "bms_number_of_battery",
    "bms_number_of_discharge_cycles",
    "bms_number_of_ntc_probes",
    "bms_remaining_capacity",
    "bms_total_voltage",
    "device_battery_voltage",
    "full_charge_capacity",
    "gsm_signal_strength",
    "number_of_satellites",
    "soc",
    "vehicle_battery_voltage",
]

NUMERIC_DEFAULTS = {"number_of_satellites"}

BOOLEAN_KEYS = [
    "bms_allow_charging",
    "bms_allow_discharging",
    *[f"bms_balance_status_flag_cell_{i}" for i in range(1, 33)],
    "can_status",
    "charge_low_temperature",
    "charge_over_current",
    "charge_over_temperature",
    "digital_output_1",
    "digital_output_2",
    "discharge_low_temperature",
    "discharge_over_current",
    "discharge_over_temperature",
    "external_power_connection_status",
    "front_detection_ic_error",
    "ignition",
    "pack_over_voltage",
    "pack_under_voltage",
    "short_circuit",
    "single_over_voltage",
    "single_under_voltage",
    "software_lock_mos",
]

TEXT_KEYS = [
    "hardware_version",
    "packet_status",
    "software_version",
]


def _build_fields() -> list[FieldDef]:
    fields: list[FieldDef] = []
    fields.extend(IDENTITY_FIELDS)
    fields.extend(POSTGRES_FIELDS)
    for key in TOP_KEYS:
        fields.append(
            _f(
                key,
                "Redis — top-level",
                f"top.{key}",
                default=key in TOP_DEFAULTS,
            )
        )
    for key in NUMERIC_KEYS:
        fields.append(
            _f(
                f"numeric_io_data.{key}",
                "Redis — numeric_io_data",
                f"numeric_io_data.{key}",
                label=key,
                default=key in NUMERIC_DEFAULTS,
            )
        )
    for key in BOOLEAN_KEYS:
        fields.append(
            _f(
                f"boolean_io_data.{key}",
                "Redis — boolean_io_data",
                f"boolean_io_data.{key}",
                label=key,
            )
        )
    for key in TEXT_KEYS:
        fields.append(
            _f(
                f"text_io_data.{key}",
                "Redis — text_io_data",
                f"text_io_data.{key}",
                label=key,
            )
        )
    return fields


FIELDS: list[FieldDef] = _build_fields()
FIELDS_BY_ID: dict[str, FieldDef] = {f.id: f for f in FIELDS}


def list_fields() -> list[dict[str, Any]]:
    return [asdict(f) for f in FIELDS]


def resolve_selected(field_ids: list[str]) -> list[FieldDef]:
    """Always include identity fields; then unique selected optional fields in schema order."""
    selected_ids = set(field_ids)
    out: list[FieldDef] = []
    seen: set[str] = set()
    for f in FIELDS:
        if f.always or f.id in selected_ids:
            if f.id not in seen:
                out.append(f)
                seen.add(f.id)
    return out


def default_field_ids() -> list[str]:
    return [f.id for f in FIELDS if f.default or f.always]
