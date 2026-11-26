"""Long-running AIS consumer process (not a Celery task: it holds a persistent socket).

    uv run python -m services.ingestion.ais_consumer          # provider from AIS_PROVIDER

Providers: aisstream (needs AISSTREAM_API_KEY), simulator (synthetic), replay (recording).
With AIS_RECORD_FILE set, raw AISStream messages are also appended as NDJSON so real
feeds can be replayed later as historical data.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal
from pathlib import Path
from typing import IO

import redis.asyncio as aioredis

from core.logging_config import configure_logging, get_logger
from core.settings import AisProvider as AisProviderSetting
from core.settings import Settings, get_settings
from database.session import get_async_engine, get_sessionmaker
from integrations.ais.providers import AisProvider, AisStreamProvider, RawAisMessage, ReplayProvider
from integrations.ais.simulator import SimulatorProvider
from services.ingestion.ais_pipeline import LIVE_CHANNEL, AisPipeline

log = get_logger(__name__)


class ProviderNotConfigured(RuntimeError):
    pass


def build_provider(s: Settings) -> AisProvider:
    match s.ais_provider:
        case AisProviderSetting.AISSTREAM:
            if s.aisstream_api_key is None:
                raise ProviderNotConfigured("AIS_PROVIDER=aisstream requires AISSTREAM_API_KEY")
            return AisStreamProvider(
                s.aisstream_api_key.get_secret_value(), bounding_boxes=s.ais_bounding_boxes
            )
        case AisProviderSetting.SIMULATOR:
            return SimulatorProvider(
                vessel_count=s.ais_simulator_vessels, interval_s=s.ais_simulator_interval_seconds
            )
        case AisProviderSetting.REPLAY:
            if not s.ais_replay_file:
                raise ProviderNotConfigured("AIS_PROVIDER=replay requires AIS_REPLAY_FILE")
            return ReplayProvider(s.ais_replay_file, speed=s.ais_replay_speed)
        case _:
            raise ProviderNotConfigured("AIS_PROVIDER is 'disabled'")


async def _produce(
    provider: AisProvider, queue: asyncio.Queue[RawAisMessage | None], recorder: IO[str] | None
) -> None:
    try:
        async for message in provider.stream():
            if recorder is not None:
                recorder.write(json.dumps(message.payload) + "\n")
            await queue.put(message)
    finally:
        await queue.put(None)  # finite providers (replay) signal completion


async def run(settings: Settings, stop: asyncio.Event) -> None:
    provider = build_provider(settings)
    redis = aioredis.from_url(str(settings.redis_url))  # type: ignore[no-untyped-call]
    pipeline = AisPipeline(
        get_sessionmaker(),
        source_key=provider.source_key,
        receiver_type=provider.receiver_type,
        publish=lambda payload: redis.publish(LIVE_CHANNEL, payload),
        batch_size=settings.ais_batch_size,
    )
    await pipeline.start()
    log.info("ais_consumer_started", provider=provider.source_key, data_mode=pipeline.data_mode)

    recorder: IO[str] | None = None
    if settings.ais_record_file and isinstance(provider, AisStreamProvider):
        recorder = await asyncio.to_thread(
            Path(settings.ais_record_file).open, "a", encoding="utf-8", buffering=1
        )

    queue: asyncio.Queue[RawAisMessage | None] = asyncio.Queue(maxsize=20_000)
    producer = asyncio.create_task(_produce(provider, queue, recorder))
    finished = False
    try:
        while not stop.is_set() and not finished:
            item: RawAisMessage | None
            try:
                item = await asyncio.wait_for(
                    queue.get(), timeout=settings.ais_flush_interval_seconds
                )
                timed_out = False
            except TimeoutError:
                item, timed_out = None, True  # nothing arrived; still flush and roll windows
            if item is None and not timed_out:
                finished = True
            elif item is not None:
                pipeline.ingest(item)
                # Drain whatever is already queued without waiting.
                while not queue.empty() and not pipeline.should_flush():
                    nxt = queue.get_nowait()
                    if nxt is None:
                        finished = True
                        break
                    pipeline.ingest(nxt)
            if pipeline.buffered:
                await pipeline.flush()
            if pipeline.window_due():
                await pipeline.close_window()
    finally:
        producer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await producer
        if pipeline.buffered:
            await pipeline.flush()
        await pipeline.close_window()
        if recorder is not None:
            recorder.close()
        await redis.aclose()
        await get_async_engine().dispose()
        log.info("ais_consumer_stopped")


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    stop = asyncio.Event()

    async def _main() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await run(settings, stop)

    try:
        asyncio.run(_main())
    except ProviderNotConfigured as exc:
        log.error("ais_provider_not_configured", reason=str(exc))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
