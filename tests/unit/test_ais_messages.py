from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from integrations.ais.messages import (
    AisPositionReport,
    AisRejected,
    AisStaticReport,
    clean_text,
    is_valid_imo,
    parse_ais_time,
    parse_message,
)

RECEIVED = datetime(2026, 11, 26, 12, 0, 5, tzinfo=UTC)


def position(**body: Any) -> dict[str, Any]:
    meta = body.pop("_meta", {})
    return {
        "MessageType": "PositionReport",
        "MetaData": {
            "MMSI": 259000420,
            "ShipName": "AUGUSTSON@@@@",
            "latitude": 66.02695,
            "longitude": 12.25382,
            "time_utc": "2026-11-26 12:00:00.318353123 +0000 UTC",
            **meta,
        },
        "Message": {
            "PositionReport": {
                "Cog": 308,
                "Latitude": 66.02695,
                "Longitude": 12.25382,
                "NavigationalStatus": 0,
                "PositionAccuracy": True,
                "RateOfTurn": 4,
                "Sog": 12.3,
                "TrueHeading": 235,
                "UserID": 259000420,
                **body,
            }
        },
    }


def static(**body: Any) -> dict[str, Any]:
    return {
        "MessageType": "ShipStaticData",
        "MetaData": {"MMSI": 259000420, "time_utc": "2026-11-26 12:00:00 +0000 UTC"},
        "Message": {
            "ShipStaticData": {
                "Name": "AUGUSTSON@@@",
                "CallSign": "LAAB@@",
                "ImoNumber": 9074729,
                "Type": 70,
                "Dimension": {"A": 100, "B": 50, "C": 10, "D": 12},
                "MaximumStaticDraught": 8.4,
                "Destination": "ROTTERDAM@@@",
                **body,
            }
        },
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-11-26 12:00:00.318353 +0000 UTC", datetime(2026, 11, 26, 12, 0, 0, 318353, UTC)),
        ("2026-11-26 12:00:00.318353999 +0000 UTC", datetime(2026, 11, 26, 12, 0, 0, 318353, UTC)),
        ("2026-11-26 12:00:00 +0000 UTC", datetime(2026, 11, 26, 12, 0, tzinfo=UTC)),
        ("2026-11-26 14:00:00.5 +0200 EET", datetime(2026, 11, 26, 12, 0, 0, 500000, UTC)),
        ("2026-11-26T12:00:00Z", datetime(2026, 11, 26, 12, 0, tzinfo=UTC)),
    ],
)
def test_parse_ais_time(raw: str, expected: datetime) -> None:
    assert parse_ais_time(raw) == expected


def test_parse_ais_time_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_ais_time("yesterday")


def test_position_report_parsed() -> None:
    r = parse_message(position(), received_at=RECEIVED)
    assert isinstance(r, AisPositionReport)
    assert r.mmsi == 259000420
    assert r.observed_at == datetime(2026, 11, 26, 12, 0, 0, 318353, UTC)
    assert (r.latitude, r.longitude) == (66.02695, 12.25382)
    assert (r.sog_knots, r.cog_deg, r.heading_deg, r.nav_status) == (12.3, 308.0, 235, 0)
    assert r.position_accuracy_high is True
    assert r.ship_name == "AUGUSTSON"


@pytest.mark.parametrize(
    ("field", "value", "attr"),
    [
        ("Sog", 102.3, "sog_knots"),
        ("Cog", 360, "cog_deg"),
        ("TrueHeading", 511, "heading_deg"),
        ("RateOfTurn", -128, "rate_of_turn"),
        ("NavigationalStatus", 99, "nav_status"),
    ],
)
def test_not_available_sentinels_become_none(field: str, value: float, attr: str) -> None:
    r = parse_message(position(**{field: value}), received_at=RECEIVED)
    assert getattr(r, attr) is None


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"Latitude": 91, "Longitude": 181}, "invalid_position"),
        ({"Latitude": 0, "Longitude": 0}, "invalid_position"),
        ({"_meta": {"MMSI": "259000420"}}, "invalid_mmsi"),
        ({"_meta": {"MMSI": 2_570_000}}, "non_vessel_mmsi"),  # coast station range
        ({"_meta": {"MMSI": 992_351_000}}, "non_vessel_mmsi"),  # aid to navigation
        ({"_meta": {"time_utc": "soon"}}, "invalid_timestamp"),
        ({"_meta": {"time_utc": "2026-11-26 13:00:00 +0000 UTC"}}, "future_timestamp"),
    ],
)
def test_invalid_reports_rejected(override: dict[str, Any], reason: str) -> None:
    with pytest.raises(AisRejected) as exc:
        parse_message(position(**override), received_at=RECEIVED)
    assert exc.value.reason == reason


def test_missing_body_rejected() -> None:
    with pytest.raises(AisRejected) as exc:
        parse_message({"MessageType": "PositionReport", "MetaData": {}}, received_at=RECEIVED)
    assert exc.value.reason == "invalid_payload"


def test_unsupported_types_ignored() -> None:
    assert parse_message({"MessageType": "BaseStationReport"}, received_at=RECEIVED) is None


def test_static_report_parsed() -> None:
    r = parse_message(static(), received_at=RECEIVED)
    assert isinstance(r, AisStaticReport)
    assert (r.name, r.call_sign, r.destination) == ("AUGUSTSON", "LAAB", "ROTTERDAM")
    assert (r.imo, r.ship_type, r.length_m, r.beam_m, r.draught_m) == (9074729, 70, 150, 22, 8.4)


def test_static_report_drops_untrustworthy_values() -> None:
    r = parse_message(
        static(ImoNumber=1234568, Dimension={"A": 0, "B": 0, "C": 0, "D": 0}, Type=0, Name=""),
        received_at=RECEIVED,
    )
    assert isinstance(r, AisStaticReport)
    assert (r.imo, r.length_m, r.beam_m, r.ship_type, r.name) == (None, None, None, None, None)


@pytest.mark.parametrize(
    ("imo", "ok"), [(9074729, True), (9176187, True), (9074728, False), (123, False)]
)
def test_imo_checksum(imo: int, ok: bool) -> None:
    assert is_valid_imo(imo) is ok


def test_clean_text() -> None:
    assert clean_text("  ever   given@@@ ", 64) == "EVER GIVEN"
    assert clean_text("@@@@", 64) is None
    assert clean_text(42, 64) is None


def test_unlocode_parser_skips_excluded_and_coordinate_less_entries() -> None:
    import io

    from integrations.ports.unlocode import EXCLUDED_CODES, iter_seaports

    csv_text = (
        "Change,Country,Location,Name,NameWoDiacritics,Subdivision,Status,Function,Date,IATA,Coordinates,Remarks\n"
        ",PH,PIS,Philippines,Philippines,,RL,1-------,1207,,1435N 12100E,\n"
        ",PH,MNL,Manila,Manila,,AI,1--45---,9601,,,\n"
        ",PH,BTG,Batangas,Batangas,,AI,1-------,0401,,1345N 12103E,\n"
    )
    codes = [p.unlocode for p in iter_seaports(io.StringIO(csv_text))]
    assert codes == ["PHBTG"]
    assert EXCLUDED_CODES.get("PHPIS")
