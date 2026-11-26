from __future__ import annotations

import pytest

from chainsight_api.routers.live import filter_message
from services.fleet_tracking.queries import BBox, position_freshness


def test_bbox_parse_and_contains() -> None:
    b = BBox.parse("-10,40,10,60")
    assert b.contains(50, 0) and not b.contains(50, 20) and not b.contains(30, 0)


def test_bbox_across_antimeridian() -> None:
    b = BBox.parse("170,-10,-170,10")
    assert b.crosses_antimeridian
    assert b.contains(0, 175) and b.contains(0, -175) and not b.contains(0, 0)


def test_bbox_sql_uses_bound_params_only() -> None:
    params: dict[str, float] = {}
    sql = BBox.parse("170,-10,-170,10").sql("v.last_location", params)
    assert "170" not in sql.replace("180", "")  # values are bound, not interpolated
    assert params == {"bb_min_lon": 170, "bb_min_lat": -10, "bb_max_lon": -170, "bb_max_lat": 10}


@pytest.mark.parametrize("raw", ["", "1,2,3", "1,2,3,x", "0,10,5,5", "-181,0,0,1", "0,-91,1,1"])
def test_bbox_rejects_invalid(raw: str) -> None:
    with pytest.raises(ValueError):
        BBox.parse(raw)


def test_filter_message_keeps_provenance_and_drops_outside() -> None:
    msg = {
        "source": "s",
        "data_mode": "synthetic",
        "positions": [{"lat": 0, "lon": 0}, {"lat": 50, "lon": 50}],
    }
    out = filter_message(msg, BBox.parse("-1,-1,1,1"))
    assert out == {
        "type": "positions",
        "source": "s",
        "data_mode": "synthetic",
        "positions": [{"lat": 0, "lon": 0}],
    }
    assert filter_message(msg, BBox.parse("100,10,110,20")) is None
    assert len(filter_message(msg, None)["positions"]) == 2  # type: ignore[index]


@pytest.mark.parametrize(
    ("age", "label"),
    [(0, "live"), (600, "live"), (601, "recent"), (3600, "recent"), (3601, "stale")],
)
def test_position_freshness(age: float, label: str) -> None:
    assert position_freshness(age) == label
