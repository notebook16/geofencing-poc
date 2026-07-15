# find-lat-long

Standalone script to export IMEI coordinates to an Excel file. Uses the same Postgres and Redis credentials / patterns as `heyev-backend` `getFleetSum`.

## Flow

1. **Postgres** — start from `inv_batteries`:
   ```
   inv_batteries
     --(inv_iot_id)-----------> inv_iots.imei
     --(xref_install_prod_id)-> xref_installation_product
     --(installation_id)------> loan_applications
     --(center_id)------------> centers
                                 (geo_location_lat / geo_location_lng)
   ```
2. **Redis** — `MGET` with the IMEI as the key (same as `FetchBulkByIMEI`), then parse `latitude` / `longitude` from the telemetry JSON (same as fleetsumm `mapRedisToIotRaw`).
3. **XLSX** — `battery_number`, `imei`, `lat`, `long`, `center_id`, `center_name`, `center_lat`, `center_long`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in DB + Redis creds
```

## Run

```bash
source .venv/bin/activate
python find_lat_long.py
```

Output defaults to `output/imei_lat_long.xlsx` (override with `OUTPUT_XLSX`).

## Env vars

| Var | Same as getFleetSum |
|---|---|
| `DB_POSTGRES_URL` | yes |
| `DB_POSTGRES_PORT` | yes |
| `DB_POSTGRES_DBNAME` | yes |
| `DB_POSTGRES_USERNAME` | yes |
| `DB_POSTGRES_PASS` | yes |
| `REDIS_URL` (`host:port`) | yes |
| `REDIS_PASS` | yes |
