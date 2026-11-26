"""Carrier adapter contract and shared HTTP plumbing.

Every carrier implementation follows the same interface, validates responses, maps
provider codes to the internal (DCSA-based) event model, retries transient failures with
backoff, respects rate limits, and returns a record of every call so the service layer can
write `carrier_integration_logs`. Adapters never touch the database.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

import httpx

from core.timeutils import utcnow
from integrations.carriers.models import (
    CallRecord,
    CarrierAuthError,
    CarrierRateLimited,
    CarrierUnavailable,
    IntegrationHealth,
    ProviderResponse,
    ShipmentNotFound,
    TrackingReference,
    TrackingResult,
)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
Sleep = Callable[[float], Awaitable[None]]


class CarrierAdapter(ABC):
    carrier_code: ClassVar[str]
    adapter_key: ClassVar[str]
    data_mode: ClassVar[str] = "real_time"

    @abstractmethod
    async def track_shipment(self, reference: TrackingReference) -> ProviderResponse:
        """Fetch raw tracking data. Raises ShipmentNotFound / CarrierAuthError / ..."""

    @abstractmethod
    async def normalize_events(self, response: ProviderResponse) -> TrackingResult:
        """Validate and map provider events to NormalizedEvent."""

    @abstractmethod
    async def health_check(self) -> IntegrationHealth:
        """Report whether the integration can be used right now."""

    async def track(self, reference: TrackingReference) -> TrackingResult:
        response = await self.track_shipment(reference)
        result = await self.normalize_events(response)
        result.calls = [*response.calls, *result.calls]
        return result


class TokenBucket:
    """In-process rate limiter (per adapter instance). Phase 6 moves this to Redis so it
    holds across workers."""

    def __init__(
        self,
        rate_per_s: float,
        burst: int = 1,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.rate, self.capacity = rate_per_s, float(burst)
        self._tokens, self._clock, self._sleep = float(burst), clock, sleep
        self._last = clock()

    async def acquire(self) -> None:
        while True:
            now = self._clock()
            self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
            self._last = now
            if self._tokens >= 1:
                self._tokens -= 1
                return
            await self._sleep((1 - self._tokens) / self.rate)


class ResilientHttp:
    """GET with retries (429/5xx/transport errors), Retry-After support, and call records."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        limiter: TokenBucket | None = None,
        retries: int = 3,
        backoff_s: float = 1.0,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.client, self.limiter, self.retries = client, limiter, retries
        self.backoff_s, self._sleep = backoff_s, sleep

    async def get_json(
        self,
        url: str,
        *,
        operation: str,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        calls: list[CallRecord],
    ) -> Any:
        delay = self.backoff_s
        for attempt in range(1, self.retries + 1):
            if self.limiter:
                await self.limiter.acquire()
            started = time.perf_counter()
            try:
                resp = await self.client.get(url, params=params, headers=headers)
            except httpx.TimeoutException as exc:
                calls.append(
                    self._record(operation, "timeout", started, message=type(exc).__name__)
                )
                if attempt == self.retries:
                    raise CarrierUnavailable("carrier API timed out", calls) from exc
            except httpx.TransportError as exc:
                calls.append(
                    self._record(operation, "failure", started, message=type(exc).__name__)
                )
                if attempt == self.retries:
                    raise CarrierUnavailable("carrier API unreachable", calls) from exc
            else:
                request_id = resp.headers.get("x-request-id") or resp.headers.get(
                    "x-correlation-id"
                )
                status = resp.status_code
                if status < 300:
                    calls.append(
                        self._record(operation, "success", started, status, request_id=request_id)
                    )
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise CarrierUnavailable("carrier API returned non-JSON", calls) from exc
                outcome = (
                    "auth_error"
                    if status in (401, 403)
                    else "rate_limited"
                    if status == 429
                    else "failure"
                )
                calls.append(
                    self._record(operation, outcome, started, status, request_id=request_id)
                )
                if status in (401, 403):
                    raise CarrierAuthError("carrier rejected the credentials", calls)
                if status == 404:
                    raise ShipmentNotFound("reference not found at carrier", calls)
                if status not in RETRY_STATUSES or attempt == self.retries:
                    if status == 429:
                        raise CarrierRateLimited("carrier rate limit exceeded", calls)
                    raise CarrierUnavailable(f"carrier API error (HTTP {status})", calls)
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    delay = float(retry_after)
            await self._sleep(delay)
            delay *= 2
        raise CarrierUnavailable("unreachable", calls)  # pragma: no cover

    @staticmethod
    def _record(
        operation: str,
        outcome: str,
        started: float,
        http_status: int | None = None,
        *,
        request_id: str | None = None,
        message: str | None = None,
    ) -> CallRecord:
        return CallRecord(
            operation=operation,
            outcome=outcome,
            occurred_at=utcnow(),
            http_status=http_status,
            latency_ms=int((time.perf_counter() - started) * 1000),
            provider_request_id=request_id,
            message=message,
        )
