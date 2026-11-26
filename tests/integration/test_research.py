from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import numpy as np
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from chainsight_api.main import create_app
from core.settings import get_settings
from integrations.research.availability import NextDay, WeeklyRelease
from integrations.research.fred import FredClient
from integrations.research.portwatch import PortWatchClient
from ml.quant_research.datasets import DatasetDefinition, build_dataset
from ml.quant_research.experiments import ExperimentConfig, create_experiment, run_experiment
from services.ingestion.research_data import (
    SeriesSpec,
    collect_fred,
    collect_portwatch,
    ensure_series,
    store_observations,
)
from services.research_jobs import local_org

NOW = datetime(2026, 11, 26, 12, tzinfo=UTC)
WEEKLY = WeeklyRelease(weekday=1, hour=12, min_lag_days=2)
SMALL_BT = {"min_train_periods": 60, "test_periods": 13, "lookbacks": [13, 26], "thresholds": [0.0]}


def q(engine: Engine, sql: str, **params: Any) -> list[Any]:
    with engine.connect() as conn:
        return list(conn.execute(sa.text(sql), params))


def _source(engine: Engine, key: str) -> Any:
    return q(engine, "SELECT id FROM data_sources WHERE key = :k", k=key)[0].id


def _spec(key: str, category: str, rule: Any) -> SeriesSpec:
    return SeriesSpec(key=key, category=category, name=key, entity_kind="test", entity_key=key,
                      entity_name=key, metric="value", unit=None, rule=rule)  # fmt: skip


@pytest.fixture(scope="module")
def research_data(migrated_db: Engine) -> dict[str, Any]:
    """Five years of synthetic test series (stored like real ones; never shipped as data)."""
    rng = np.random.default_rng(11)
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(5 * 365)]
    pw, fred_src = _source(migrated_db, "imf-portwatch"), _source(migrated_db, "fred")
    with migrated_db.begin() as conn:
        for n in (6, 4):
            spec = _spec(f"portwatch:chokepoint:chokepoint{n}:n_tanker", "maritime", WEEKLY)
            sid = ensure_series(conn, pw, spec)
            store_observations(conn, sid, [(d, float(rng.poisson(40))) for d in days], WEEKLY, NOW)
        spec = _spec("fred:TESTPRICE", "market", NextDay())
        sid = ensure_series(conn, fred_src, spec)
        px = 60 * np.exp(np.cumsum(rng.normal(0, 0.02, len(days))))
        store_observations(conn, sid, [(d, float(p)) for d, p in zip(days, px, strict=True) if d.weekday() < 5],
                           NextDay(), NOW)  # fmt: skip
    return {"days": days}


def test_bulk_load_then_live_values_and_revisions_never_overwrite(migrated_db: Engine) -> None:
    src = _source(migrated_db, "fred")
    spec = _spec("fred:REVTEST", "market", NextDay())
    with migrated_db.begin() as conn:
        sid = ensure_series(conn, src, spec)
        first = store_observations(
            conn, sid, [(date(2026, 1, 5), 10.0), (date(2026, 1, 6), 11.0)], NextDay(), NOW
        )
    with migrated_db.begin() as conn:
        later = NOW + timedelta(days=3)
        second = store_observations(
            conn,
            sid,
            [(date(2026, 1, 5), 10.0), (date(2026, 1, 6), 11.5), (date(2026, 1, 7), 12.0)],
            NextDay(),
            later,
        )
    assert (first.inserted, second.inserted, second.revised, second.unchanged) == (2, 1, 1, 1)
    rows = q(migrated_db, "SELECT as_of::date AS d, revision, value, available_at, available_at_assumed "
                          "FROM indicator_observations WHERE series_id = :s ORDER BY 1, 2", s=sid)  # fmt: skip
    by = {(r.d, r.revision): r for r in rows}
    assert by[(date(2026, 1, 5), 0)].available_at_assumed  # bulk history: rule-based time
    assert by[(date(2026, 1, 5), 0)].available_at == datetime(2026, 1, 6, tzinfo=UTC)
    assert by[(date(2026, 1, 6), 0)].value == 11.0  # original kept
    assert (
        by[(date(2026, 1, 6), 1)].value == 11.5 and by[(date(2026, 1, 6), 1)].available_at == later
    )
    assert not by[(date(2026, 1, 7), 0)].available_at_assumed  # first seen live
    assert by[(date(2026, 1, 7), 0)].available_at == later


def test_collectors_record_provenance_and_logs(migrated_db: Engine) -> None:
    settings = get_settings().model_copy(update={"research_fred_series": ["DCOILBRENTEU"],
                                                 "research_portwatch_ports": ["port1114"]})  # fmt: skip
    csv = "observation_date,DCOILBRENTEU\n2026-11-20,80.5\n2026-11-23,81.0\n"
    fred = FredClient(
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=csv)))
    )
    assert collect_fred(migrated_db, settings, client=fred, now=NOW).inserted == 2

    def pw(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("returnDistinctValues"):
            return httpx.Response(
                200,
                json={
                    "features": [
                        {"attributes": {"portid": "chokepoint1", "portname": "Suez Canal"}}
                    ]
                },
            )
        portid = "port1114" if "port1114" in request.url.params["where"] else "chokepoint1"
        metric = "portcalls_tanker" if portid == "port1114" else "n_tanker"
        return httpx.Response(
            200,
            json={
                "features": [
                    {
                        "attributes": {
                            "date": "2026-11-20",
                            "portid": portid,
                            "portname": "X",
                            "ISO3": "NLD",
                            metric: 47,
                        }
                    }
                ]
            },
        )

    res = collect_portwatch(
        migrated_db,
        settings,
        client=PortWatchClient(client=httpx.Client(transport=httpx.MockTransport(pw))),
        now=NOW,
    )
    assert res.inserted == 2
    series = {r.key: r for r in q(migrated_db, "SELECT key, attributes FROM indicator_series")}
    assert series["fred:DCOILBRENTEU"].attributes["download_method"] == "public_csv"
    assert (
        series["portwatch:port:port1114:portcalls_tanker"].attributes["availability_rule"]["kind"]
        == "weekly_release"
    )
    assert series["portwatch:chokepoint:chokepoint1:n_tanker"].attributes["estimated"] is True
    status = q(
        migrated_db, "SELECT key, status FROM data_sources WHERE key IN ('fred', 'imf-portwatch')"
    )
    assert {r.status for r in status} == {"active"}
    logs = q(migrated_db, "SELECT count(*) AS n FROM ingestion_logs l JOIN data_sources d ON d.id = l.data_source_id "
                          "WHERE d.key IN ('fred', 'imf-portwatch') AND l.status = 'succeeded'")  # fmt: skip
    assert logs[0].n == 2


def test_stored_dataset_is_versioned_and_idempotent(
    migrated_db: Engine, research_data: dict[str, Any]
) -> None:
    ds = DatasetDefinition("test-weekly", "Test", "test", "W", ("composite:hormuz_bab_tanker|W|level",
                                                              "composite:hormuz_bab_tanker|W|z13"))  # fmt: skip
    first = build_dataset(migrated_db, ds, NOW)
    assert first["features"] == 2 and first["inserted"] > 400
    assert build_dataset(migrated_db, ds, NOW)["inserted"] == 0
    bad = q(migrated_db, "SELECT count(*) AS n FROM maritime_features WHERE available_at <= as_of")
    assert bad[0].n == 0  # nothing is known before its period ends


def test_experiment_run_is_reproducible_and_point_in_time(
    migrated_db: Engine, research_data: dict[str, Any]
) -> None:
    cfg = ExperimentConfig(
        feature="composite:hormuz_bab_tanker|W|z13", asset="fred:TESTPRICE", backtest=SMALL_BT
    )
    with migrated_db.begin() as conn:
        eid = create_experiment(
            conn, local_org(migrated_db), name="t1", hypothesis="test hypothesis", config=cfg
        )
    r1, r2 = run_experiment(migrated_db, eid), run_experiment(migrated_db, eid)
    runs = {r.id: r for r in q(migrated_db, "SELECT id, status, data_snapshot_hash, series, folds FROM backtest_runs "
                                            "WHERE experiment_id = :e", e=eid)}  # fmt: skip
    assert runs[r1].status == runs[r2].status == "succeeded"
    assert runs[r1].data_snapshot_hash == runs[r2].data_snapshot_hash
    assert runs[r1].series == runs[r2].series  # deterministic
    assert (
        runs[r1].series["n_configs_tested"] == 4 and runs[r1].folds
    )  # 2 lookbacks x 1 threshold x 2 directions
    splits = {
        r.split
        for r in q(
            migrated_db, "SELECT split FROM backtest_metrics WHERE backtest_run_id = :r", r=r1
        )
    }
    assert splits == {"in_sample", "out_of_sample"}
    sig = q(migrated_db, "SELECT count(*) AS n, count(*) FILTER (WHERE available_at > as_of) AS late "
                         "FROM research_signals WHERE backtest_run_id = :r", r=r1)[0]  # fmt: skip
    assert sig.n == len(runs[r1].series["points"]) and sig.late == 0


def test_failed_run_is_recorded(migrated_db: Engine) -> None:
    cfg = ExperimentConfig(feature="composite:gulf_export_tanker|W|level", asset="fred:TESTPRICE")
    with migrated_db.begin() as conn:
        eid = create_experiment(
            conn, local_org(migrated_db), name="t2", hypothesis="no data here", config=cfg
        )
    with pytest.raises(Exception):  # noqa: B017 - any failure must be recorded, then surfaced
        run_experiment(migrated_db, eid)
    run = q(migrated_db, "SELECT status, error FROM backtest_runs WHERE experiment_id = :e", e=eid)[
        0
    ]
    assert run.status == "failed" and "no data" in run.error
    assert (
        q(migrated_db, "SELECT status FROM research_experiments WHERE id = :e", e=eid)[0].status
        == "failed"
    )


def test_research_api(migrated_db: Engine, research_data: dict[str, Any]) -> None:
    with TestClient(create_app()) as c:
        ind = c.get("/api/v1/research/indicators").json()
        keys = {i["key"] for i in ind["indicators"]}
        assert "composite:hormuz_bab_tanker" in keys and "fred:TESTPRICE" in keys
        f = c.get(
            "/api/v1/research/features", params={"key": "composite:hormuz_bab_tanker|W|yoy"}
        ).json()
        assert f["n"] > 100 and all(p["available_at"] > p["as_of"] for p in f["points"])
        a = c.post("/api/v1/research/analysis", json={"feature": "composite:hormuz_bab_tanker|W|z13",
                                                      "asset": "fred:TESTPRICE"}).json()  # fmt: skip
        assert (
            a["correlation"]["ok"]
            and a["alignment"]["n"] > 100
            and "not investment advice" in a["disclaimer"]
        )
        assert {"feature", "returns"} == set(a["stationarity"])
        created = c.post("/api/v1/research/experiments", json={
            "name": "api test", "hypothesis": "stated before the run",
            "config": {"feature": "composite:hormuz_bab_tanker|W|level", "asset": "fred:TESTPRICE", "backtest": SMALL_BT},
        })  # fmt: skip
        assert created.status_code == 201
        detail = created.json()
        assert detail["runs"][0]["status"] == "succeeded"
        assert "out_of_sample.sharpe" in detail["runs"][0]["metrics"]
        listed = c.get("/api/v1/research/experiments").json()
        assert any(
            e["id"] == detail["id"] and e["latest_run"]["status"] == "succeeded" for e in listed
        )
        bad = c.post("/api/v1/research/experiments", json={"name": "bad", "hypothesis": "unknown asset here",
                                                            "config": {"feature": "x|W|level", "asset": "fred:NOPE"}})  # fmt: skip
        assert bad.status_code == 422
        assert c.get("/api/v1/research/features", params={"key": "a|Q|level"}).status_code == 422
        assert (
            c.post(
                f"/api/v1/research/experiments/{'0' * 8}-0000-0000-0000-{'0' * 12}/runs"
            ).status_code
            == 404
        )
