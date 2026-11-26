# Carrier tracking integrations

ChainSight tracks containers, bookings and bills of lading through one adapter contract
(`integrations/carriers/base.py`). Every adapter returns DCSA-style events
(PLANNED/ESTIMATED/ACTUAL), and the service layer stores them append-only.

| Integration | Data source | Data mode | Status without credentials |
|---|---|---|---|
| Generic DCSA Track & Trace v2 (`dcsa_tnt_v2`) | `carrier-<code>` | real_time | `authentication_required` (ocean carriers) |
| Demo carrier (`demo`) | `carrier-demo` | **synthetic** | `demo_data_only` |
| CSV import | `csv-import` | historical | always available |
| DHL | — | — | `unavailable` (no adapter) |

Statuses are recomputed at API startup and every 10 minutes (`carriers.sync_integrations`).
A tracking call that the carrier rejects with 401/403 flips the status to
`authentication_required` immediately. Every provider call, including failed ones, is written
to `carrier_integration_logs` without request or response bodies.

## Connecting a real carrier (DCSA Track & Trace)

Several carriers publish Track & Trace APIs based on the DCSA standard
(<https://dcsa.org/standards/track-and-trace>). **The generic adapter has not been verified
against any live carrier API.** No credentials were available while building it. Expect
to adjust field mappings when you connect one, guided by the unit tests.

1. Request API access in the carrier's developer portal and note the base URL of its
   Track & Trace API and its authentication scheme.
2. Add a JSON object to `.env`. The whole value is treated as a secret, is never logged,
   and is never returned by the API:

   ```env
   CARRIER_DCSA_CONFIG={"hapag-lloyd": {"base_url": "https://<carrier-api-host>/track-and-trace/v2", "auth": {"type": "oauth2_client_credentials", "token_url": "https://<carrier-auth-host>/token", "client_id": "...", "client_secret": "..."}}, "maersk": {"base_url": "https://<carrier-api-host>/track-and-trace/v2", "auth": {"type": "api_key", "header": "<header name from the portal>", "api_key": "..."}}}
   ```

   - The key is the carrier code from `GET /api/v1/carriers` (`maersk`, `msc`, `cma-cgm`,
     `hapag-lloyd`, `cosco`, `evergreen`, `one`).
   - Auth types: `api_key` (custom header), `bearer`, or `oauth2_client_credentials`.
   - Optional: `rate_per_s` (default 2), enforced client-side with a token bucket.
3. Restart the API and worker: `docker compose up -d api worker`. The carrier's status
   becomes `connected`. For API-key auth the reason says "not yet verified" until the first
   successful tracking call sets `last_success_at`.
4. Track a reference on `/shipments` or with `POST /api/v1/shipments/track`.

Query parameters sent to `{base_url}/events`: `equipmentReference` (container),
`carrierBookingReference` (booking) or `transportDocumentReference` (B/L).

### Normalization rules

- `eventClassifierCode` PLN/EST/ACT maps to PLANNED/ESTIMATED/ACTUAL. REQ events are skipped.
- Known equipment, transport and shipment codes are kept. Unknown codes are stored as
  `UNKNOWN` with the provider's `raw_code`, and reported in logs, never guessed.
- Timestamps without a timezone are rejected, and all times are stored in UTC.
- An event's identity is the provider `eventID`, or a content fingerprint when absent, so
  re-fetching never duplicates.
- A vessel leg links to an AIS-tracked vessel **only** by the IMO number the carrier
  reports. It is never matched by name or AIS proximity.

## Demo carrier (synthetic)

`integrations/carriers/demo.py` generates realistic DCSA journeys: gate-in, load,
departure, optional transshipment, arrival, discharge and gate-out. Delays emerge during
the voyage, and every changed estimate is a new event. Safeguards:

- It only answers obviously demo references: bookings/B/Ls starting `DEMO` and containers
  with owner prefix `ZZZU`. **It never generates data for a real reference.**
- Vessel names start with `SYNTHETIC` and carry no IMO, so they never link to real AIS
  vessels.
- One route ends at Manila (`PHMNL`), which has no coordinates in UN/LOCODE. This
  exercises the "port not mapped" display.

## CSV import

`POST /api/v1/shipments/import` with a `text/csv` body (5 MB max), or **Import CSV** on
`/shipments`. One row per event:

| Column | Required | Notes |
|---|---|---|
| `carrier_code` | yes | e.g. `msc` |
| `reference_type` | yes | `container`, `booking`, `bill_of_lading` (or `bl`) |
| `reference_value` | yes | containers must pass the ISO 6346 check digit |
| `event_code` | yes | DCSA code: GTIN, LOAD, DEPA, ARRI, DISC, GTOT, … |
| `classifier` | yes | PLANNED / ESTIMATED / ACTUAL (or PLN / EST / ACT) |
| `event_at` | yes | ISO 8601 **with timezone**, e.g. `2026-11-20T08:00:00Z` |
| `location_unlocode` | | 5-character UN/LOCODE |
| `container_number` | | ISO 6346 validated |
| `vessel_name`, `vessel_imo`, `voyage_number` | | IMO checksum validated; enables AIS linking |
| `origin_unlocode`, `destination_unlocode` | | Otherwise derived from the events |

Validation is **all-or-nothing**. If any row is invalid, nothing is imported and the
response (HTTP 422) lists every problem by row. Re-importing the same file is idempotent.
Imported shipments are labeled historical and cannot be refreshed from a carrier.

## How status and delay are derived

See `services/shipment_tracking/milestones.py`.

- **Status** comes from ACTUAL events only: at origin → loaded → in transit →
  transshipment → discharged → delivered.
- **Original planned arrival** is the destination arrival from the earliest-issued
  PLANNED event. It is stored write-once.
- **Current ETA** is the actual arrival, else the most recently issued estimate, else the
  latest plan.
- **Delay** = current ETA − original planned arrival. The shipment is flagged delayed (or
  arrived late) when that exceeds `SHIPMENT_DELAY_THRESHOLD_HOURS` (default 12).
- **Refresh:** active, carrier-backed shipments are re-tracked every 30 minutes
  (`carriers.refresh_shipments`, interval `SHIPMENT_AUTO_REFRESH_MINUTES`).
