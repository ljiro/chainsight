# ChainSight — Integration Strategy

Status: Draft v1 (Phase 0). Provider terms change. Re-verify each provider's documentation and terms before enabling it in production. Statements marked *verify* were not confirmed against current documentation while writing this.

## 1. Principles

1. Every provider sits behind an adapter in `integrations/`. Adapters return validated internal DTOs and never write to the database.
2. Credentials are read from backend environment variables only and are never sent to the browser or returned by the API.
3. Each provider has a `data_sources` row whose `status` and `data_mode` are shown in the UI.
4. There is no scraping, and no bypassing of authentication, rate limits, or terms of service.
5. If access is not available, we ship the adapter contract, a mock provider for tests, CSV import where it makes sense, and setup documentation. The feature degrades; it does not pretend.
6. Mock and synthetic data are labeled `data_mode = synthetic` end-to-end, from the database through the API to the UI.

## 2. Provider matrix

| Provider | Purpose | Access | Credentials | Data mode | Phase |
|---|---|---|---|---|---|
| **AISStream** (aisstream.io) | Live vessel positions + static data | Free, needs sign-up | API key (`AISSTREAM_API_KEY`) | real_time | 2 |
| **Open-Meteo** | Weather forecast, historical (archive), marine (waves) | Free for non-commercial use, no key. Commercial use needs a paid plan | None (optional `OPEN_METEO_API_KEY` for paid tier) | real_time / historical | 2–4 |
| **IMF PortWatch** | Daily port calls, trade-volume estimates, chokepoint transits, disruptions | Open data, published via ArcGIS Hub / API | None | periodic (≈ weekly updates, daily granularity) | 2, 5 |
| **NGA World Port Index** (Pub 150) | Port registry with coordinates | Public domain (US Gov). Download endpoint refused automated requests in testing | None | historical (static reference) | later |
| **UN/LOCODE** (UNECE) | Port registry, codes and coordinates (**implemented**) | Free download; data licence not stated by the mirror, so it is downloaded at import time and not bundled | None | historical (static reference) | 2 ✅ |
| **UN Comtrade** | Monthly bilateral trade by HS code | Free tier with a registered subscription key, rate-limited | `UN_COMTRADE_API_KEY` | periodic (monthly, published with lag) | 5 |
| **FRED / EIA** | Commodity and macro series (e.g. Brent, WTI, Henry Hub) | Free with API key | `FRED_API_KEY`, `EIA_API_KEY` | periodic | 5 |
| **Maersk** | Track & Trace (DCSA), schedules | Developer portal, app registration *verify* | `MAERSK_CONSUMER_KEY` (+ secret) | real_time (polling) | 3 |
| **Hapag-Lloyd** | Track & Trace (DCSA) | API developer portal, registration/approval *verify* | `HAPAG_LLOYD_CLIENT_ID/SECRET` | real_time (polling) | 3 |
| **CMA CGM** | Track & Trace | API portal, registration *verify* | `CMA_CGM_API_KEY` | real_time (polling) | 3 |
| **MSC** | Track & Trace | Customer/partner access *verify* | `MSC_*` | — | 3 (adapter + mock) |
| **ONE** | Track & Trace | Partner access *verify* | `ONE_*` | — | 3 (adapter + mock) |
| **COSCO Shipping** | Track & Trace | Unknown *verify* | — | — | 3 (adapter + mock) |
| **Evergreen** | Track & Trace | Unknown *verify* | — | — | 3 (adapter + mock) |
| **DHL** | Shipment tracking (logistics provider, not an ocean carrier) | Developer portal, API key *verify* | `DHL_API_KEY` | real_time (polling) | 3 |
| **CSV import** | User-provided shipments/events | Always available | — | historical (user data) | 3 |
| **Mock carrier** | Tests and demo | Always available | — | synthetic | 3 |

### 2.1 What the free stack can deliver

All of these work without any commercial agreement:

- Live vessel map, vessel registry, tracks, approaching-port detection, and rule-based anomalies (AISStream + WPI/UN-LOCODE).
- Weather at vessel and port positions (Open-Meteo).
- Port activity history and congestion indicators (PortWatch + our own AIS-derived port calls).
- An ETA model trained on our own accumulated AIS history. It needs weeks of collection before it is meaningful.
- Quant research on PortWatch/AIS features against FRED/EIA series.

### 2.2 What needs credentials or a commercial agreement

- Container-level tracking for any carrier (Module A with real data). Until access is granted, Module A runs on CSV imports + the mock carrier, labeled as such.
- Satellite AIS (open-ocean coverage) from commercial providers (e.g. Spire, MarineTraffic/Kpler, ORBCOMM). It is out of scope unless the user provides access. The AIS adapter interface allows adding one.
- Commercial use of Open-Meteo.
- Licensed market data beyond free end-of-day series.

## 3. Carrier adapter contract (Phase 3)

```python
class CarrierAdapter(ABC):
    carrier_code: ClassVar[str]  # e.g. "maersk"
    capabilities: ClassVar[set[Capability]]

    async def track_shipment(self, ref: TrackingReference) -> ProviderResponse: ...
    async def normalize_events(
        self, response: ProviderResponse
    ) -> list[NormalizedShipmentEvent]: ...
    async def health_check(self) -> IntegrationHealth: ...
```

The base class provides:

- A shared `httpx.AsyncClient` with timeouts, retries (exponential backoff + jitter on 429/5xx, honoring `Retry-After`), and a per-provider token-bucket rate limiter in Redis.
- Response validation through a provider-specific Pydantic model. Unknown fields are ignored but logged once.
- Code mapping tables (`provider code → DCSA event code + classifier`). Unmapped codes are kept as `raw_code` with `event_code = UNKNOWN` and raise a data-quality counter.
- Integration logging (`carrier_integration_logs`) with outcome, latency, and HTTP status, without payload bodies.
- Status reporting: `health_check()` updates `carrier_integrations.status`.

Status semantics shown in the UI:

| Status | Meaning |
|---|---|
| `connected` | Credentials configured and the last health check succeeded |
| `authentication_required` | An adapter exists, but no credentials are configured, or they were rejected |
| `pending_approval` | Credentials were requested from the provider and are awaiting approval (set manually by an admin) |
| `unavailable` | No adapter exists yet, or the provider offers no programmatic access we can use |
| `demo_data_only` | Only the mock/CSV path is active for this carrier |

## 4. AIS integration (Phase 2)

- Endpoint: `wss://stream.aisstream.io/v0/stream`. Within a few seconds of connecting, send a subscription message with `APIKey`, `BoundingBoxes` (`[[[lat1, lon1], [lat2, lon2]], ...]`), and optionally `FiltersShipMMSI` and `FilterMessageTypes` (`PositionReport`, `ShipStaticData`, `StandardClassBPositionReport`). *verify field names against current docs.*
- The consumer runs as its own process with an exponential reconnect backoff (1 s → 60 s cap). It resubscribes on reconnect and has a heartbeat watchdog: if no message arrives for N seconds, it reconnects.
- Parsing maps AIS sentinel values to NULL: heading 511, SOG 102.3, COG 360, lat 91, lon 181. The timestamp comes from `MetaData.time_utc` (receiver time), with the AIS second-of-minute kept for quality checks.
- Dedup key: `(mmsi, observed_at, source)` in Redis with a short TTL, and the DB primary key as the backstop.
- Freshness: `data_sources.last_success_at` is updated each batch. Beat raises an alert if there are no messages for 5 minutes.
- Without credentials: `AIS_PROVIDER=simulator` generates synthetic traffic (`ais-simulator`, `data_mode = synthetic`), and `AIS_PROVIDER=replay` plays back real recordings (`ais-replay`, `data_mode = historical`). These are separate sources, so a recording is never labeled synthetic and simulated data is never labeled historical. See [docs/integrations/ais.md](../integrations/ais.md).

## 5. Weather (Open-Meteo)

- Forecast API for current/forecast conditions at port coordinates and at sampled vessel positions (rounded to a 0.25° grid and cached).
- The historical archive API provides features for model training. The Marine API provides wave height.
- Attribution "Weather data by Open-Meteo.com" (CC BY 4.0) appears wherever weather is shown.

## 6. Ports (PortWatch + registries)

- The registry is loaded from UN/LOCODE into `ports`, with provenance and 8 km circular geofences (see [ports-and-weather.md](../integrations/ports-and-weather.md)).
- PortWatch daily port activity loads into `port_observations` with `available_at` set to the dataset publication time. This matters for research because the series is revised and published with lag.
- We derive our own port calls from AIS geofences. These are kept separate (`source = chainsight-ais-derived`) and never merged with PortWatch values.

## 7. Configuring a real provider (template)

Each `docs/integrations/<provider>.md` (created in Phases 2–3) will cover:

1. How to request access (link to the official portal).
2. Which environment variables to set in `.env`.
3. How to verify (`make check-integration PROVIDER=maersk`, which runs `health_check()`).
4. Known limits: rate limits, coverage, latency, and license.
