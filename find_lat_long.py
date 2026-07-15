#!/usr/bin/env python3
"""
Standalone tool: export battery/IMEI lat-long from Postgres + Redis.

Flow (same Redis pattern as heyev-backend getFleetSum):
  1. Load batteries from inv_batteries and resolve:
       - IMEI via inv_batteries.inv_iot_id -> inv_iots.imei
       - center via inv_batteries.xref_install_prod_id
           -> xref_installation_product.installation_id
           -> loan_applications.installation_id
           -> loan_applications.center_id / centers
             (center lat/long = centers.geo_location_lat / geo_location_lng)
  2. MGET Redis keys by IMEI and parse latitude/longitude
     (same JSON shape as fleetsumm mapRedisToIotRaw).
  3. Write XLSX with battery number, imei, device lat/long, and center fields.
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

import psycopg2
import redis
from dotenv import load_dotenv
from openpyxl import Workbook

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

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
    lat: Optional[float] = None
    lon: Optional[float] = None


def env(name: str, default: Optional[str] = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise SystemExit(f"Missing required env var: {name}")
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
    # Match heyev-backend: REDIS_URL is "host:port", REDIS_PASS is password.
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
    return any(k in payload for k in ("speed", "received_at", "numeric_io_data", "boolean_io_data"))


def resolve_telemetry_root(root: dict) -> dict:
    inner = root.get("data")
    if isinstance(inner, dict) and is_telemetry_payload(inner):
        return inner
    return root


def resolve_numeric_io_map(root: dict) -> dict:
    telemetry = resolve_telemetry_root(root)
    for candidate in (telemetry.get("numeric_io_data"), root.get("numeric_io_data")):
        if isinstance(candidate, dict):
            return candidate
    return {}


def first_value(*values: Any) -> Any:
    for v in values:
        if v is not None:
            return v
    return None


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
    # DECIMAL(10,6) => abs < 10000
    return abs(v) < 10_000


def normalize_coordinate(v: float, is_latitude: bool) -> Optional[float]:
    """Same rule as heyev-backend fleetsumm.normalizeCoordinate."""
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


def extract_lat_lon(raw: Any) -> tuple[Optional[float], Optional[float]]:
    """Mirror fleetsumm mapRedisToIotRaw lat/lon extraction."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None, None
    if not isinstance(raw, dict):
        return None, None

    telemetry = resolve_telemetry_root(raw)
    numeric_io = resolve_numeric_io_map(raw)

    lat = parse_float(first_value(telemetry.get("latitude"), numeric_io.get("latitude")))
    lon = parse_float(first_value(telemetry.get("longitude"), numeric_io.get("longitude")))

    if lat is not None:
        lat = normalize_coordinate(lat, True)
    if lon is not None:
        lon = normalize_coordinate(lon, False)
    return lat, lon


def fetch_coords_from_redis(
    client: redis.Redis, imeis: list[str], batch_size: int
) -> dict[str, tuple[Optional[float], Optional[float]]]:
    """Same pattern as utils/redis FetchBulkByIMEI: MGET with IMEI as key."""
    coords: dict[str, tuple[Optional[float], Optional[float]]] = {}
    unique = list(dict.fromkeys(imeis))

    for batch in batched(unique, batch_size):
        values = client.mget(batch)
        for imei, raw in zip(batch, values):
            if raw is None:
                coords[imei] = (None, None)
                continue
            coords[imei] = extract_lat_lon(raw)
    return coords


def write_xlsx(path: Path, rows: list[BatteryRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "battery_lat_long"
    ws.append(
        [
            "battery_number",
            "imei",
            "lat",
            "long",
            "center_id",
            "center_name",
            "center_lat",
            "center_long",
        ]
    )

    for r in rows:
        ws.append(
            [
                r.battery_number,
                r.imei,
                r.lat,
                r.lon,
                r.center_id,
                r.center_name,
                r.center_lat,
                r.center_long,
            ]
        )
    wb.save(path)


def main() -> int:
    batch_size = int(os.getenv("REDIS_BATCH_SIZE", "50"))
    output = Path(os.getenv("OUTPUT_XLSX", "output/imei_lat_long.xlsx"))
    if not output.is_absolute():
        output = ROOT / output

    print("Connecting to Postgres...")
    with connect_postgres() as conn:
        print("Fetching batteries + IMEI + centers...")
        rows = fetch_batteries(conn)
    print(f"Loaded {len(rows)} battery rows from inv_batteries")

    imeis = [r.imei for r in rows if r.imei]
    print(f"{len(imeis)} batteries have a linked IMEI")

    print("Connecting to Redis...")
    rclient = connect_redis()
    rclient.ping()

    print(f"Fetching lat/long from Redis in batches of {batch_size}...")
    coords = fetch_coords_from_redis(rclient, imeis, batch_size)

    with_coords = 0
    with_center = 0
    for row in rows:
        if row.center_id is not None:
            with_center += 1
        if not row.imei:
            continue
        lat, lon = coords.get(row.imei, (None, None))
        row.lat, row.lon = lat, lon
        if lat is not None and lon is not None:
            with_coords += 1

    write_xlsx(output, rows)
    print(
        f"Wrote {len(rows)} rows "
        f"({with_coords} with device lat/long, {with_center} with center) -> {output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
