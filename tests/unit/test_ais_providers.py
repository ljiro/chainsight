from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from integrations.ais.messages import AisPositionReport, AisStaticReport, parse_message
from integrations.ais.providers import (
    AisStreamProvider,
    Backoff,
    RawAisMessage,
    ReplayProvider,
)
from integrations.ais.simulator import EPOCH, LANES, SimulatorProvider, build_fleet

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


class FakeSocket:
    def __init__(self, frames: list[Any]) -> None:
        self.frames = list(frames)
        self.sent: list[dict[str, Any]] = []

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def recv(self) -> str:
        if not self.frames:
            await asyncio.sleep(3600)  # silent socket -> watchdog fires
        frame = self.frames.pop(0)
        if isinstance(frame, Exception):
            raise frame
        return frame if isinstance(frame, str) else json.dumps(frame)


async def take(stream: AsyncIterator[RawAisMessage], n: int) -> list[RawAisMessage]:
    out = []
    async for msg in stream:
        out.append(msg)
        if len(out) == n:
            break
    return out


async def no_sleep(_: float) -> None:
    return None


def test_backoff_grows_caps_and_resets() -> None:
    b = Backoff(initial=1, maximum=8, jitter=0, rand=lambda: 0.5)
    assert [b.next() for _ in range(5)] == [1, 2, 4, 8, 8]
    b.reset()
    assert b.next() == 1


def test_backoff_jitter_bounds() -> None:
    assert Backoff(initial=10, jitter=0.2, rand=lambda: 0.0).next() == pytest.approx(8.0)
    assert Backoff(initial=10, jitter=0.2, rand=lambda: 1.0).next() == pytest.approx(12.0)


def test_aisstream_requires_key() -> None:
    with pytest.raises(ValueError):
        AisStreamProvider("")


async def test_aisstream_subscribes_then_reconnects_after_errors() -> None:
    sockets = [
        FakeSocket([{"MessageType": "PositionReport", "n": 1}, ConnectionResetError("boom")]),
        FakeSocket([{"error": "Api Key Is Not Valid"}]),
        FakeSocket([{"MessageType": "PositionReport", "n": 2}]),
    ]
    delays: list[float] = []

    @asynccontextmanager
    async def connect(url: str, **_: Any) -> AsyncIterator[FakeSocket]:
        yield sockets.pop(0)

    async def record_sleep(d: float) -> None:
        delays.append(d)

    first = sockets[0]
    provider = AisStreamProvider(
        "secret-key",
        bounding_boxes=[[[50, 0], [55, 10]]],
        connect=connect,
        backoff=Backoff(jitter=0),
        sleep=record_sleep,
        clock=lambda: NOW,
    )
    msgs = await take(provider.stream(), 2)

    assert [m.payload["n"] for m in msgs] == [1, 2]
    assert first.sent == [
        {
            "APIKey": "secret-key",
            "BoundingBoxes": [[[50, 0], [55, 10]]],
            "FilterMessageTypes": [
                "PositionReport",
                "StandardClassBPositionReport",
                "ShipStaticData",
            ],
        }
    ]
    # Reset after the first good message, then 1s (reset error), 2s (provider error).
    assert delays == [1.0, 2.0]
    assert provider.reconnects == 2


async def test_aisstream_watchdog_reconnects_silent_socket() -> None:
    sockets = [FakeSocket([]), FakeSocket([{"MessageType": "PositionReport", "n": 1}])]

    @asynccontextmanager
    async def connect(url: str, **_: Any) -> AsyncIterator[FakeSocket]:
        yield sockets.pop(0)

    provider = AisStreamProvider("k", connect=connect, idle_timeout=0.01, sleep=no_sleep)
    msgs = await take(provider.stream(), 1)
    assert msgs[0].payload["n"] == 1
    assert provider.reconnects == 1


async def test_replay_keeps_original_timestamps_and_paces(tmp_path: Path) -> None:
    lines = [
        {
            "MessageType": "PositionReport",
            "MetaData": {"time_utc": "2025-03-01 00:00:00 +0000 UTC"},
        },
        "not json",
        {
            "MessageType": "PositionReport",
            "MetaData": {"time_utc": "2025-03-01 00:00:10 +0000 UTC"},
        },
    ]
    path = tmp_path / "rec.ndjson"
    path.write_text("\n".join(x if isinstance(x, str) else json.dumps(x) for x in lines))
    pauses: list[float] = []

    async def record_sleep(d: float) -> None:
        pauses.append(d)

    provider = ReplayProvider(path, speed=2.0, sleep=record_sleep, clock=lambda: NOW)
    msgs = [m async for m in provider.stream()]
    assert len(msgs) == 2
    assert msgs[1].payload["MetaData"]["time_utc"].startswith("2025-03-01 00:00:10")
    assert pauses == [5.0]
    assert provider.skipped_lines == 1


def test_simulator_fleet_is_clearly_synthetic() -> None:
    fleet = build_fleet()
    assert len(fleet) == 24
    assert len({v.mmsi for v in fleet}) == 24
    assert all(200_000_000 < v.mmsi < 201_000_000 for v in fleet)  # MID 200: unallocated
    assert all(v.name.startswith("SYNTHETIC ") for v in fleet)
    assert {v.lane for v in fleet} == set(LANES)


def test_simulator_is_deterministic_and_continuous() -> None:
    a, b = build_fleet(seed=7)[0], build_fleet(seed=7)[0]
    t = EPOCH + timedelta(days=330)
    assert a.state_at(t) == b.state_at(t)
    s1, s2 = a.state_at(t), a.state_at(t + timedelta(seconds=30))
    # 30 s at <= 20 kn is < 310 m; positions never jump.
    assert abs(s1["lat"] - s2["lat"]) < 0.01 and abs(s1["lon"] - s2["lon"]) < 0.01


def test_simulator_dwells_moored_at_lane_ends() -> None:
    v = build_fleet()[0]
    for k in range(400):
        t = EPOCH + timedelta(seconds=k * v.period_s / 400 - v.offset_s)
        s = v.state_at(t)
        if s["nav"] == 5:
            end = min(
                v.route.waypoints[0], v.route.waypoints[-1], key=lambda p: abs(p[0] - s["lat"])
            )
            assert s["sog"] == 0
            assert abs(s["lat"] - end[0]) < 1e-6
            return
    pytest.fail("vessel never moored")


async def test_simulator_messages_pass_the_real_parser() -> None:
    clock_t = [NOW]
    provider = SimulatorProvider(
        vessel_count=3, interval_s=3, sleep=no_sleep, clock=lambda: clock_t[0]
    )
    msgs = await take(provider.stream(), 6)
    reports = [parse_message(m.payload, received_at=m.received_at) for m in msgs]
    assert sum(isinstance(r, AisStaticReport) for r in reports) == 3
    positions = [r for r in reports if isinstance(r, AisPositionReport)]
    assert len(positions) == 3
    assert all(r.observed_at == NOW for r in positions)
