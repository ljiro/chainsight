"""DCSA Track & Trace (v2.x) event parsing and a generic, configurable adapter.

Reference: https://dcsa.org/standards/track-and-trace. Several carriers publish T&T APIs
based on this standard, but base URLs, authentication and field details differ by carrier
and version. This adapter is therefore configured per carrier and has NOT been verified
against any live carrier API (no credentials were available while building it). Expect to
adjust field mappings when connecting a real carrier, guided by the tests.
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime
from typing import Any, ClassVar, Literal

import httpx
from pydantic import BaseModel, SecretStr

from core.timeutils import ensure_utc, utcnow
from integrations.carriers.base import CarrierAdapter, ResilientHttp, TokenBucket
from integrations.carriers.models import (
    CallRecord,
    CarrierAuthError,
    CarrierResponseInvalid,
    IntegrationHealth,
    NormalizedEvent,
    ProviderResponse,
    ShipmentNotFound,
    TrackingReference,
    TrackingResult,
)

CLASSIFIERS = {"PLN": "PLANNED", "EST": "ESTIMATED", "ACT": "ACTUAL"}
EQUIPMENT_CODES = frozenset(
    {"LOAD", "DISC", "GTIN", "GTOT", "STUF", "STRP", "PICK", "DROP", "INSP", "RSEA", "RMVD"}
)
TRANSPORT_CODES = frozenset({"ARRI", "DEPA"})
SHIPMENT_CODES = frozenset(
    {"RECE", "DRFT", "PENA", "PENU", "REJE", "APPR", "ISSU", "SURR", "SUBM", "VOID", "CONF",
     "REQS", "CMPL", "HOLD", "RELS"}
)  # fmt: skip
DESCRIPTIONS = {
    "GTIN": "Gate in", "GTOT": "Gate out", "LOAD": "Loaded on vessel",
    "DISC": "Discharged from vessel", "STUF": "Stuffed", "STRP": "Stripped",
    "PICK": "Picked up", "DROP": "Dropped off", "ARRI": "Vessel arrival",
    "DEPA": "Vessel departure", "INSP": "Inspected", "RSEA": "Resealed", "RMVD": "Removed",
}  # fmt: skip
QUERY_PARAMS = {
    "container": "equipmentReference",
    "booking": "carrierBookingReference",
    "bill_of_lading": "transportDocumentReference",
}


def _unlocode(event: dict[str, Any]) -> tuple[str | None, str | None]:
    """UN/LOCODE + name from eventLocation, transportCall.location or transportCall."""
    for holder in (
        event.get("eventLocation"),
        (event.get("transportCall") or {}).get("location"),
        event.get("transportCall"),
    ):
        if isinstance(holder, dict):
            code = holder.get("UNLocationCode")
            if isinstance(code, str) and len(code.strip()) == 5:
                return code.strip().upper(), holder.get("locationName")
    return None, None


def _time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"missing {field}")
    return ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def normalize_dcsa_event(event: dict[str, Any]) -> tuple[NormalizedEvent | None, str | None]:
    """Return (event, unmapped_code). Requested ('REQ') and malformed events return None."""
    classifier = CLASSIFIERS.get(str(event.get("eventClassifierCode", "")).upper())
    if classifier is None:
        return None, f"classifier:{event.get('eventClassifierCode')}"
    kind = str(event.get("eventType", "")).upper()
    if kind == "EQUIPMENT":
        raw, known, event_type = event.get("equipmentEventTypeCode"), EQUIPMENT_CODES, "equipment"
    elif kind == "TRANSPORT":
        raw, known, event_type = event.get("transportEventTypeCode"), TRANSPORT_CODES, "transport"
    elif kind == "SHIPMENT":
        raw, known, event_type = event.get("shipmentEventTypeCode"), SHIPMENT_CODES, "shipment"
    else:
        return None, f"eventType:{kind or 'missing'}"
    try:
        event_at = _time(event.get("eventDateTime"), "eventDateTime")
        created = (
            _time(event["eventCreatedDateTime"], "eventCreatedDateTime")
            if event.get("eventCreatedDateTime")
            else None
        )
    except (ValueError, KeyError):
        return None, "invalid:eventDateTime"

    raw_code = str(raw).upper() if raw else None
    code = raw_code if raw_code in known else "UNKNOWN"
    unmapped = None if code != "UNKNOWN" else f"{kind}:{raw_code}"
    call_raw, vessel_raw = event.get("transportCall"), None
    call: dict[str, Any] = call_raw if isinstance(call_raw, dict) else {}
    vessel_raw = call.get("vessel")
    vessel: dict[str, Any] = vessel_raw if isinstance(vessel_raw, dict) else {}
    imo_raw = vessel.get("vesselIMONumber")
    try:
        imo = int(imo_raw) if imo_raw not in (None, "") else None
    except (TypeError, ValueError):
        imo = None
    location, location_name = _unlocode(event)
    container = event.get("equipmentReference")
    return (
        NormalizedEvent(
            event_type=event_type,
            event_code=code,
            classifier=classifier,
            event_at=event_at,
            event_created_at=created,
            location_unlocode=location,
            location_name=location_name,
            container_number=str(container).upper() if container else None,
            vessel_imo=imo,
            vessel_name=vessel.get("vesselName"),
            voyage_number=call.get("carrierExportVoyageNumber")
            or call.get("carrierImportVoyageNumber")
            or call.get("carrierVoyageNumber"),
            transport_mode=call.get("modeOfTransport"),
            description=DESCRIPTIONS.get(code),
            raw_code=raw_code,
            source_event_id=str(event["eventID"]) if event.get("eventID") else None,
        ),
        unmapped,
    )


def normalize_dcsa_payload(payload: Any) -> tuple[list[NormalizedEvent], list[str], int]:
    if isinstance(payload, dict) and isinstance(payload.get("events"), list):
        payload = payload["events"]  # some implementations wrap the list
    if not isinstance(payload, list):
        raise CarrierResponseInvalid("expected a JSON list of DCSA events")
    events, unmapped, skipped = [], [], 0
    for item in payload:
        if not isinstance(item, dict):
            skipped += 1
            continue
        ev, problem = normalize_dcsa_event(item)
        if ev is None:
            skipped += 1
        else:
            events.append(ev)
        if problem:
            unmapped.append(problem)
    return events, sorted(set(unmapped)), skipped


def event_fingerprint(e: NormalizedEvent) -> str:
    """Stable id for events without a provider eventID (CSV, some APIs)."""
    parts = [e.event_type, e.event_code, e.classifier, e.event_at.isoformat(),
             e.location_unlocode or "", e.container_number or "", e.vessel_imo or "",
             e.voyage_number or ""]  # fmt: skip
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:40]


class DcsaAuth(BaseModel):
    type: Literal["api_key", "bearer", "oauth2_client_credentials"]
    header: str = "API-Key"
    api_key: SecretStr | None = None
    token_url: str | None = None
    client_id: str | None = None
    client_secret: SecretStr | None = None
    scope: str | None = None


class DcsaCarrierConfig(BaseModel):
    base_url: str  # e.g. https://api.example-carrier.com/track-and-trace/v2
    auth: DcsaAuth
    rate_per_s: float = 2.0


class DcsaTntAdapter(CarrierAdapter):
    adapter_key: ClassVar[str] = "dcsa_tnt_v2"

    def __init__(
        self,
        carrier_code: str,
        config: DcsaCarrierConfig,
        *,
        client: httpx.AsyncClient | None = None,
        http: ResilientHttp | None = None,
    ) -> None:
        self.carrier_code = carrier_code  # type: ignore[misc]
        self.config = config
        self._client = client or httpx.AsyncClient(timeout=20.0)
        self.http = http or ResilientHttp(
            self._client, limiter=TokenBucket(config.rate_per_s, burst=2)
        )
        self._token: tuple[str, float] | None = None

    async def _auth_headers(self, calls: list[CallRecord]) -> dict[str, str]:
        a = self.config.auth
        if a.type == "api_key" and a.api_key:
            return {a.header: a.api_key.get_secret_value()}
        if a.type == "bearer" and a.api_key:
            return {"Authorization": f"Bearer {a.api_key.get_secret_value()}"}
        if (
            a.type == "oauth2_client_credentials"
            and a.token_url
            and a.client_id
            and a.client_secret
        ):
            if self._token and self._token[1] > time.time() + 30:
                return {"Authorization": f"Bearer {self._token[0]}"}
            resp = await self._client.post(
                a.token_url,
                data={"grant_type": "client_credentials", "client_id": a.client_id,
                      "client_secret": a.client_secret.get_secret_value(),
                      **({"scope": a.scope} if a.scope else {})},
            )  # fmt: skip
            if resp.status_code != 200:
                raise CarrierAuthError("OAuth token request failed", calls)
            body = resp.json()
            self._token = (body["access_token"], time.time() + float(body.get("expires_in", 300)))
            return {"Authorization": f"Bearer {self._token[0]}"}
        raise CarrierAuthError("credentials are not configured", calls)

    async def track_shipment(self, reference: TrackingReference) -> ProviderResponse:
        calls: list[CallRecord] = []
        headers = await self._auth_headers(calls)
        payload = await self.http.get_json(
            f"{self.config.base_url.rstrip('/')}/events",
            operation="track_shipment",
            params={QUERY_PARAMS[reference.reference_type]: reference.value},
            headers={**headers, "Accept": "application/json"},
            calls=calls,
        )
        if isinstance(payload, list) and not payload:
            raise ShipmentNotFound("carrier returned no events for this reference", calls)
        return ProviderResponse(
            reference=reference, payload=payload, fetched_at=utcnow(), calls=calls
        )

    async def normalize_events(self, response: ProviderResponse) -> TrackingResult:
        events, unmapped, skipped = normalize_dcsa_payload(response.payload)
        return TrackingResult(
            reference=response.reference, events=events, fetched_at=response.fetched_at,
            unmapped_codes=unmapped, skipped_events=skipped,
        )  # fmt: skip

    async def health_check(self) -> IntegrationHealth:
        calls: list[CallRecord] = []
        try:
            await self._auth_headers(calls)
        except CarrierAuthError as exc:
            return IntegrationHealth(
                status="authentication_required", reason=str(exc), checked_at=utcnow(),
                calls=tuple(calls),
            )  # fmt: skip
        verified = self.config.auth.type == "oauth2_client_credentials"  # a token was obtained
        return IntegrationHealth(
            status="connected",
            reason=(
                "OAuth token obtained (generic DCSA T&T adapter; field mappings unverified for "
                "this carrier)."
                if verified
                else "Credentials configured; not yet verified. The first successful tracking "
                "call confirms them (generic DCSA T&T adapter)."
            ),
            checked_at=utcnow(),
            calls=tuple(calls),
        )
