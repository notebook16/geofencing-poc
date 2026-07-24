"""Postgres + Redis export logic for redis-data-downloader."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable, Optional

import psycopg2
import redis
from openpyxl import Workbook

from .schema import FieldDef, resolve_selected

BATTERY_EXPORT_SQL = """
SELECT DISTINCT ON (b.inv_battery_id)
    b.serial AS battery_number,
    b.inv_battery_id,
    b.inv_iot_id,
    i.imei,
    b.xref_install_prod_id,
    x.installation_id,
    la.application_id,
    la.center_id,
    c.center_name,
    c.geo_location_lat AS center_lat,
    c.geo_location_lng AS center_long
FROM inv_batteries b
LEFT JOIN inv_iots i
    ON i.inv_iot_id = b.inv_iot_id
LEFT JOIN xref_installation_product x
    ON x.xref_id = b.xref_install_prod_id
LEFT JOIN loan_applications la
    ON la.installation_id = x.installation_id
LEFT JOIN centers c
    ON c.center_id = la.center_id
ORDER BY b.inv_battery_id, la.application_id DESC NULLS LAST
"""


@dataclass
class BatteryRow:
    battery_number: str
    inv_battery_id: int
    inv_iot_id: Optional[int]
    imei: Optional[str]
    xref_install_prod_id: Optional[int]
    installation_id: Optional[int]
    application_id: Optional[int]
    center_id: Optional[int]
    center_name: Optional[str]
    center_lat: Optional[float]
    center_long: Optional[float]


def env(name: str, default: Optional[str] = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def connect_postgres():
    return psycopg2.connect(
        host=env("DB_POSTGRES_URL"),
        port=int(os.getenv("DB_POSTGRES_PORT", "5432")),
        dbname=env("DB_POSTGRES_DBNAME"),
        user=env("DB_POSTGRES_USERNAME"),
        password=env("DB_POSTGRES_PASS"),
    )


def connect_redis() -> redis.Redis:
    raw = env("REDIS_URL")
    if "://" in raw:
        return redis.from_url(raw, password=os.getenv("REDIS_PASS") or None, decode_responses=True)

    if ":" in raw:
        host, port_s = raw.rsplit(":", 1)
        port = int(port_s)
    else:
        host, port = raw, 6379

    return redis.Redis(
        host=host,
        port=port,
        password=os.getenv("REDIS_PASS") or None,
        decode_responses=True,
    )


def to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_batteries(conn) -> list[BatteryRow]:
    with conn.cursor() as cur:
        cur.execute(BATTERY_EXPORT_SQL)
        rows = cur.fetchall()

    out: list[BatteryRow] = []
    for row in rows:
        imei = row[3]
        if isinstance(imei, str):
            imei = imei.strip() or None
        out.append(
            BatteryRow(
                battery_number=str(row[0]),
                inv_battery_id=row[1],
                inv_iot_id=row[2],
                imei=imei,
                xref_install_prod_id=row[4],
                installation_id=row[5],
                application_id=row[6],
                center_id=row[7],
                center_name=row[8],
                center_lat=to_float(row[9]),
                center_long=to_float(row[10]),
            )
        )
    return out


def batched(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def is_telemetry_payload(payload: dict) -> bool:
    return any(
        k in payload
        for k in ("speed", "received_at", "numeric_io_data", "boolean_io_data", "text_io_data")
    )


def resolve_telemetry_root(root: dict) -> dict:
    inner = root.get("data")
    if isinstance(inner, dict) and is_telemetry_payload(inner):
        return inner
    return root


def resolve_io_map(root: dict, key: str) -> dict:
    telemetry = resolve_telemetry_root(root)
    for candidate in (telemetry.get(key), root.get(key)):
        if isinstance(candidate, dict):
            return candidate
    return {}


def parse_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    if isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return None
        try:
            f = float(trimmed)
        except ValueError:
            return None
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    return None


def fits_decimal_10_6(v: float) -> bool:
    return abs(v) < 10_000


def normalize_coordinate(v: float, is_latitude: bool) -> Optional[float]:
    max_abs = 90.0 if is_latitude else 180.0
    if abs(v) <= max_abs and fits_decimal_10_6(v):
        return v
    if fits_decimal_10_6(v):
        return None
    scaled = v
    for _ in range(10):
        scaled /= 10.0
        if abs(scaled) <= max_abs and fits_decimal_10_6(scaled):
            return scaled
    return None


def parse_raw_json(raw: Any) -> Optional[dict]:
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict):
        return None
    return raw


def extract_field_value(root: dict, field: FieldDef) -> Any:
    source = field.source
    if source == "postgres":
        return None

    telemetry = resolve_telemetry_root(root)

    if source.startswith("top."):
        key = source[len("top.") :]
        value = telemetry.get(key)
        if key == "latitude":
            numeric_io = resolve_io_map(root, "numeric_io_data")
            lat = parse_float(value if value is not None else numeric_io.get("latitude"))
            return normalize_coordinate(lat, True) if lat is not None else None
        if key == "longitude":
            numeric_io = resolve_io_map(root, "numeric_io_data")
            lon = parse_float(value if value is not None else numeric_io.get("longitude"))
            return normalize_coordinate(lon, False) if lon is not None else None
        return value

    if source.startswith("numeric_io_data."):
        key = source[len("numeric_io_data.") :]
        return resolve_io_map(root, "numeric_io_data").get(key)

    if source.startswith("boolean_io_data."):
        key = source[len("boolean_io_data.") :]
        return resolve_io_map(root, "boolean_io_data").get(key)

    if source.startswith("text_io_data."):
        key = source[len("text_io_data.") :]
        return resolve_io_map(root, "text_io_data").get(key)

    return None


def postgres_value(row: BatteryRow, field_id: str) -> Any:
    mapping = {
        "battery_number": row.battery_number,
        "imei": row.imei,
        "center_id": row.center_id,
        "center_name": row.center_name,
        "center_lat": row.center_lat,
        "center_long": row.center_long,
    }
    return mapping.get(field_id)


def fetch_raw_from_redis(
    client: redis.Redis, imeis: list[str], batch_size: int
) -> dict[str, Optional[dict]]:
    out: dict[str, Optional[dict]] = {}
    unique = list(dict.fromkeys(imeis))
    for batch in batched(unique, batch_size):
        values = client.mget(batch)
        for imei, raw in zip(batch, values):
            out[imei] = parse_raw_json(raw)
    return out


def column_header(field: FieldDef) -> str:
    if field.source.startswith("numeric_io_data."):
        return field.source
    if field.source.startswith("boolean_io_data."):
        return field.source
    if field.source.startswith("text_io_data."):
        return field.source
    return field.id


def build_export_xlsx(
    *,
    rows: list[BatteryRow],
    redis_by_imei: dict[str, Optional[dict]],
    fields: list[FieldDef],
    username: str,
    started_at: datetime,
    scope: str,
    selected_field_ids: list[str],
) -> bytes:
    headers = [column_header(f) for f in fields]
    wb = Workbook()
    ws = wb.active
    ws.title = "data"
    ws.append(headers)

    for row in rows:
        raw = redis_by_imei.get(row.imei) if row.imei else None
        values: list[Any] = []
        for field in fields:
            if field.source == "postgres":
                values.append(postgres_value(row, field.id))
            elif raw is None:
                values.append(None)
            else:
                values.append(extract_field_value(raw, field))
        ws.append(values)

    info = wb.create_sheet("run_info")
    info.append(["key", "value"])
    info.append(["username", username])
    info.append(["started_at", started_at.astimezone(timezone.utc).isoformat()])
    info.append(["scope", scope])
    info.append(["row_count", len(rows)])
    info.append(["fields", ", ".join(selected_field_ids)])

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


@dataclass
class ExportResult:
    content: bytes
    filename: str
    row_count: int
    started_at: datetime
    scope: str
    fields: list[str]


def run_export(
    *,
    field_ids: list[str],
    imeis: Optional[list[str]],
    username: str,
) -> ExportResult:
    started_at = datetime.now(timezone.utc)
    fields = resolve_selected(field_ids)
    selected_ids = [f.id for f in fields]
    batch_size = int(os.getenv("REDIS_BATCH_SIZE", "50"))

    with connect_postgres() as conn:
        rows = fetch_batteries(conn)

    scope = "all"
    if imeis:
        wanted = {i.strip() for i in imeis if i and i.strip()}
        rows = [r for r in rows if r.imei and r.imei in wanted]
        scope = "specific"

    imei_list = [r.imei for r in rows if r.imei]
    rclient = connect_redis()
    rclient.ping()
    redis_by_imei = fetch_raw_from_redis(rclient, imei_list, batch_size)

    content = build_export_xlsx(
        rows=rows,
        redis_by_imei=redis_by_imei,
        fields=fields,
        username=username,
        started_at=started_at,
        scope=scope,
        selected_field_ids=selected_ids,
    )

    stamp = started_at.strftime("%Y%m%d_%H%M%S")
    safe_user = "".join(c if c.isalnum() or c in "-_" else "_" for c in username.strip()) or "user"
    filename = f"{safe_user}_{stamp}.xlsx"

    output_dir = Path(os.getenv("OUTPUT_DIR", "")).expanduser()
    if not output_dir or str(output_dir) == ".":
        # project root / output
        project_root = Path(__file__).resolve().parents[2]
        output_dir = project_root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    # Always write a new file path (timestamp in name); never overwrite prior runs.
    dest = output_dir / filename
    if dest.exists():
        stamp = started_at.strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{safe_user}_{stamp}.xlsx"
        dest = output_dir / filename
    dest.write_bytes(content)

    return ExportResult(
        content=content,
        filename=filename,
        row_count=len(rows),
        started_at=started_at,
        scope=scope,
        fields=selected_ids,
    )
