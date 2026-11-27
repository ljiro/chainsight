"""Inbound carrier webhooks. Authenticated by HMAC signature, not by session or API key."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from chainsight_api.auth import client_ip, settings_of
from chainsight_api.errors import ApiError
from chainsight_api.metrics import WEBHOOK_DELIVERIES
from core.timeutils import utcnow
from database.session import get_sessionmaker
from services import audit
from services.shipment_tracking import webhooks as wh

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
MAX_BODY_BYTES = 1024 * 1024


@router.post(
    "/carriers/{carrier_code}",
    status_code=202,
    summary="Receive signed DCSA Track & Trace events from a carrier",
    description=(
        "Headers: `X-ChainSight-Delivery: <id>` and "
        "`X-ChainSight-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, '<t>.<body>')>`. "
        "Only shipments already tracked with this carrier are updated."
    ),
)
async def carrier_push(request: Request, carrier_code: str) -> Any:
    settings = settings_of(request)
    code = carrier_code.lower()
    secrets = wh.secrets_for(settings, code)
    if not secrets:
        # Same answer whether the carrier exists or not: nothing to probe.
        raise ApiError(404, "webhook_not_configured", "No webhook is configured for this carrier")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise ApiError(413, "payload_too_large", "Webhook payloads are limited to 1 MB")
    delivery_id = (request.headers.get(wh.DELIVERY_HEADER) or "").strip()
    if not delivery_id or len(delivery_id) > 200:
        raise ApiError(400, "delivery_id_required", f"{wh.DELIVERY_HEADER} header is required")
    try:
        signed_at = wh.verify_signature(
            secrets, request.headers.get(wh.SIGNATURE_HEADER), body, now=utcnow(),
            tolerance_s=settings.webhook_tolerance_seconds,
        )  # fmt: skip
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise wh.WebhookRejected(422, "invalid_json", "Body is not valid JSON") from exc
        async with get_sessionmaker()() as session, session.begin():
            outcome = await wh.ingest_push(
                session, carrier_code=code, delivery_id=delivery_id, signed_at=signed_at,
                payload=payload, threshold_hours=settings.shipment_delay_threshold_hours,
            )  # fmt: skip
            for org in outcome.organizations:
                await audit.record(
                    session, "webhook.carrier_push", organization_id=org, entity_type="carrier",
                    ip=client_ip(request),
                    changes={"carrier": code, "delivery_id": delivery_id,
                             "events_inserted": outcome.events_inserted},
                )  # fmt: skip
    except wh.WebhookRejected as exc:
        WEBHOOK_DELIVERIES.labels(code, exc.code).inc()
        raise ApiError(exc.status, exc.code, exc.message) from exc
    WEBHOOK_DELIVERIES.labels(code, outcome.status).inc()
    body_out = {
        "status": outcome.status,
        "delivery_id": outcome.delivery_id,
        "events_received": outcome.events_received,
        "events_inserted": outcome.events_inserted,
        "unmatched_events": outcome.unmatched_events,
        "skipped_events": outcome.skipped_events,
        "shipments_updated": len(outcome.shipments),
    }
    return JSONResponse(body_out, status_code=200 if outcome.status == "duplicate" else 202)
