"""Live vessel positions over WebSocket, fanned out from the AIS consumer via Redis pub/sub.

Protocol (JSON text frames):
  server -> client  {"type": "hello", "bbox": [...] | null}
  server -> client  {"type": "positions", "source": "...", "data_mode": "...", "positions": [...]}
  client -> server  {"type": "subscribe", "bbox": [minLon, minLat, maxLon, maxLat] | null}
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from chainsight_api.health import get_redis
from core.logging_config import get_logger
from core.settings import get_settings
from services.fleet_tracking.queries import BBox
from services.ingestion.ais_pipeline import LIVE_CHANNEL

log = get_logger(__name__)
router = APIRouter(tags=["live"])


def filter_message(message: dict[str, Any], bbox: BBox | None) -> dict[str, Any] | None:
    positions = message.get("positions") or []
    if bbox is not None:
        positions = [p for p in positions if bbox.contains(p["lat"], p["lon"])]
    if not positions:
        return None
    return {"type": "positions", **{k: v for k, v in message.items() if k != "positions"},
            "positions": positions}  # fmt: skip


def _parse_bbox(value: Any) -> BBox | None:
    if value is None:
        return None
    if isinstance(value, list) and len(value) == 4:
        return BBox.parse(",".join(str(v) for v in value))
    if isinstance(value, str):
        return BBox.parse(value)
    raise ValueError("bbox must be [minLon, minLat, maxLon, maxLat]")


@router.websocket("/ws/positions")
async def positions(ws: WebSocket, bbox: str | None = None) -> None:
    origin = ws.headers.get("origin")
    if origin is not None and origin not in get_settings().api_cors_origins:
        # Browsers always send Origin; reject cross-site pages. Non-browser clients omit it.
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    try:
        current = _parse_bbox(bbox)
    except ValueError:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await ws.accept()
    pubsub = get_redis().pubsub()
    await pubsub.subscribe(LIVE_CHANNEL)
    state = {"bbox": current}
    await ws.send_json({"type": "hello", "bbox": bbox})

    async def pump() -> None:
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg is None:
                continue
            try:
                payload = json.loads(msg["data"])
            except (TypeError, ValueError):
                continue
            out = filter_message(payload, state["bbox"])
            if out is not None:
                await ws.send_json(out)

    async def listen() -> None:
        while True:
            data = await ws.receive_json()
            if isinstance(data, dict) and data.get("type") == "subscribe":
                try:
                    state["bbox"] = _parse_bbox(data.get("bbox"))
                    await ws.send_json({"type": "subscribed", "bbox": data.get("bbox")})
                except ValueError as exc:
                    await ws.send_json({"type": "error", "message": str(exc)})

    tasks = [asyncio.create_task(pump()), asyncio.create_task(listen())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            exc = t.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                log.warning("ws_positions_error", error_type=type(exc).__name__)
    finally:
        for t in tasks:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(LIVE_CHANNEL)
            await pubsub.aclose()  # type: ignore[no-untyped-call]
