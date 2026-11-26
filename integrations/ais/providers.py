"""AIS providers. Each yields raw AISStream-format messages; parsing happens downstream.

- AisStreamProvider: live WebSocket feed (https://aisstream.io), reconnects with backoff.
- ReplayProvider:    plays back an NDJSON recording, keeping original timestamps (historical).
- SimulatorProvider: see simulator.py (synthetic).
"""

from __future__ import annotations

import asyncio
import json
import random
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import WebSocketException

from core.logging_config import get_logger
from core.timeutils import utcnow
from integrations.ais.messages import parse_ais_time

log = get_logger(__name__)

AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"
DEFAULT_MESSAGE_TYPES = ("PositionReport", "StandardClassBPositionReport", "ShipStaticData")
WORLD: list[list[list[float]]] = [[[-90.0, -180.0], [90.0, 180.0]]]

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class RawAisMessage:
    payload: dict[str, Any]
    received_at: datetime


class AisProvider(ABC):
    source_key: ClassVar[str]
    receiver_type: ClassVar[str]  # 'terrestrial' | 'satellite' | 'synthetic' | 'unknown'

    @abstractmethod
    def stream(self) -> AsyncIterator[RawAisMessage]:
        """Yield messages until cancelled (or until a finite source is exhausted)."""


class Backoff:
    """Exponential backoff with jitter: 1s, 2s, 4s ... capped, +/- `jitter` fraction."""

    def __init__(
        self,
        initial: float = 1.0,
        maximum: float = 60.0,
        factor: float = 2.0,
        jitter: float = 0.2,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self.initial, self.maximum, self.factor, self.jitter = initial, maximum, factor, jitter
        self._rand = rand
        self._attempt = 0

    def next(self) -> float:
        base = min(self.maximum, self.initial * self.factor**self._attempt)
        self._attempt += 1
        return max(0.0, base * (1 + self.jitter * (2 * self._rand() - 1)))

    def reset(self) -> None:
        self._attempt = 0


class AisStreamError(Exception):
    """The provider reported an error message (e.g. invalid API key or subscription)."""


class AisStreamProvider(AisProvider):
    source_key = "aisstream"
    receiver_type = "terrestrial"

    def __init__(
        self,
        api_key: str,
        bounding_boxes: Sequence[Sequence[Sequence[float]]] = WORLD,
        message_types: Sequence[str] = DEFAULT_MESSAGE_TYPES,
        *,
        url: str = AISSTREAM_URL,
        idle_timeout: float = 60.0,
        connect: Callable[..., Any] = ws_connect,
        backoff: Backoff | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = utcnow,
    ) -> None:
        if not api_key:
            raise ValueError("AISStream API key is required")
        self._api_key = api_key
        self.bounding_boxes = [list(map(list, box)) for box in bounding_boxes]
        self.message_types = list(message_types)
        self.url = url
        self.idle_timeout = idle_timeout
        self._connect = connect
        self.backoff = backoff or Backoff()
        self._sleep = sleep
        self._clock = clock
        self.reconnects = 0

    def subscription(self) -> dict[str, Any]:
        # Must be sent within a few seconds of connecting, or the server closes the socket.
        return {
            "APIKey": self._api_key,
            "BoundingBoxes": self.bounding_boxes,
            "FilterMessageTypes": self.message_types,
        }

    async def stream(self) -> AsyncIterator[RawAisMessage]:
        while True:
            try:
                async with self._connect(self.url, open_timeout=10, ping_interval=20) as ws:
                    await ws.send(json.dumps(self.subscription()))
                    log.info("ais_connected", url=self.url, boxes=len(self.bounding_boxes))
                    while True:
                        # Watchdog: a silent socket is treated as dead and reconnected.
                        frame = await asyncio.wait_for(ws.recv(), timeout=self.idle_timeout)
                        payload = json.loads(frame)
                        if isinstance(payload, dict) and "error" in payload:
                            raise AisStreamError(str(payload["error"]))
                        if not isinstance(payload, dict):
                            continue
                        self.backoff.reset()
                        yield RawAisMessage(payload, self._clock())
            except (TimeoutError, OSError, WebSocketException, ValueError, AisStreamError) as exc:
                delay = self.backoff.next()
                self.reconnects += 1
                # Never log the subscription (it contains the API key).
                log.warning(
                    "ais_disconnected",
                    error_type=type(exc).__name__,
                    error=str(exc)[:200],
                    retry_in_s=round(delay, 1),
                    reconnects=self.reconnects,
                )
                await self._sleep(delay)


class ReplayProvider(AisProvider):
    """Replays an NDJSON recording of AISStream messages (one JSON object per line).

    Original timestamps are preserved, so replayed data is historical and appears stale in
    the UI. `speed` scales the pause between messages (0 = as fast as possible)."""

    source_key = "ais-replay"
    receiver_type = "unknown"

    def __init__(
        self,
        path: str | Path,
        *,
        speed: float = 1.0,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = utcnow,
    ) -> None:
        self.path = Path(path)
        self.speed = speed
        self._sleep = sleep
        self._clock = clock
        self.skipped_lines = 0

    async def stream(self) -> AsyncIterator[RawAisMessage]:
        previous: datetime | None = None
        lines = await asyncio.to_thread(self._read_lines)
        for line in lines:
            try:
                payload = json.loads(line)
                observed = parse_ais_time(payload["MetaData"]["time_utc"])
            except (ValueError, KeyError, TypeError):
                self.skipped_lines += 1
                continue
            if previous is not None and self.speed > 0:
                gap = (observed - previous).total_seconds() / self.speed
                if gap > 0:
                    await self._sleep(min(gap, 60.0))
            previous = observed
            yield RawAisMessage(payload, self._clock())

    def _read_lines(self) -> list[str]:
        with self.path.open(encoding="utf-8") as fh:
            return [ln for ln in fh if ln.strip()]
