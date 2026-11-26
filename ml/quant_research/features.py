"""Maritime feature engineering with point-in-time availability.

A feature is `base | frequency | transform`, e.g. `composite:hormuz_bab_tanker|W|yoy`:

- **base**: an indicator series (`portwatch:chokepoint:chokepoint6:n_tanker`), a named composite
  (sum or ratio of series), or an AIS-derived daily series computed from ChainSight's own port
  calls (`ais:arrivals:NLRTM`).
- **frequency**: `D` (daily), `W` (weeks ending Friday) or `M` (calendar months). Daily values
  are summed (counts, tonnes) or averaged (ratios, hours) into complete periods only.
- **transform**: `level`, `ma<n>` (rolling mean), `pct<n>` (change over n periods),
  `yoy` (change vs the same period a year earlier), `z<n>` (rolling z-score), `diff<n>`.

Every output row carries `available_at` = the latest availability among its inputs, so a
feature is never usable before everything it was computed from was known.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd
import sqlalchemy as sa
from sqlalchemy.engine import Connection

from ml.quant_research.pit import load_first_release, utc_ns

FREQUENCIES = {"D": "D", "W": "W-FRI", "M": "ME"}
PERIODS_PER_YEAR = {"D": 365, "W": 52, "M": 12}
_TRANSFORM = re.compile(r"^(level|yoy|ma\d+|pct\d+|z\d+|diff\d+)$")
EMPTY = ["as_of", "value", "available_at"]


@dataclass(frozen=True)
class Composite:
    key: str
    name: str
    description: str
    kind: str  # sum | ratio
    numerator: tuple[str, ...]
    denominator: tuple[str, ...] = ()


def _cp(n: int, metric: str) -> str:
    return f"portwatch:chokepoint:chokepoint{n}:{metric}"


# Defined a priori from shipping geography, not tuned on results.
COMPOSITES: dict[str, Composite] = {
    c.key: c
    for c in [
        Composite(
            "composite:hormuz_bab_tanker",
            "Hormuz + Bab el-Mandeb tanker transits",
            "Daily tanker transits through the Strait of Hormuz and Bab el-Mandeb "
            "(Middle East crude and product exports).",
            "sum",
            (_cp(6, "n_tanker"), _cp(4, "n_tanker")),
        ),
        Composite(
            "composite:suez_share_tanker",
            "Suez share of Suez + Cape tanker transits",
            "Suez tanker transits / (Suez + Cape of Good Hope). Falls when tankers reroute "
            "around Africa.",
            "ratio",
            (_cp(1, "n_tanker"),),
            (_cp(1, "n_tanker"), _cp(7, "n_tanker")),
        ),
        Composite(
            "composite:malacca_tanker_capacity",
            "Malacca Strait tanker capacity",
            "Deadweight tonnage of tankers transiting Malacca (crude flows into East Asia).",
            "sum",
            (_cp(5, "capacity_tanker"),),
        ),
        Composite(
            "composite:asia_hub_container_calls",
            "Shanghai + Singapore container port calls",
            "Container ship port calls at the two largest Asian hubs.",
            "sum",
            (
                "portwatch:port:port1188:portcalls_container",
                "portwatch:port:port1201:portcalls_container",
            ),
        ),
        Composite(
            "composite:gulf_export_tanker",
            "Ras Tanura + Fujairah tanker export volume",
            "Estimated tanker export tonnes from two major Gulf oil terminals.",
            "sum",
            ("portwatch:port:port1091:export_tanker", "portwatch:port:port362:export_tanker"),
        ),
    ]
}

AIS_METRICS = {
    "arrivals": "AIS-detected arrivals",
    "departures": "AIS-detected departures",
    "port_hours": "Mean hours in port of calls ending that day",
    "route_deviations": "Route-deviation alerts raised (all ports)",
}


class FeatureError(ValueError):
    pass


@dataclass(frozen=True)
class FeatureSpec:
    base: str
    frequency: str = "W"
    transform: str = "level"

    @property
    def key(self) -> str:
        return f"{self.base}|{self.frequency}|{self.transform}"

    @classmethod
    def parse(cls, key: str) -> FeatureSpec:
        parts = key.split("|")
        if len(parts) != 3:
            raise FeatureError(f"feature key must be base|frequency|transform: {key!r}")
        spec = cls(*parts)
        spec.validate()
        return spec

    def validate(self) -> None:
        if self.frequency not in FREQUENCIES:
            raise FeatureError(f"unknown frequency {self.frequency!r}")
        if not _TRANSFORM.match(self.transform):
            raise FeatureError(f"unknown transform {self.transform!r}")
        digits = re.sub(r"\D", "", self.transform)
        if digits and not 1 <= int(digits) <= 520:
            raise FeatureError("window must be between 1 and 520 periods")

    def input_keys(self) -> tuple[str, ...]:
        if self.base in COMPOSITES:
            c = COMPOSITES[self.base]
            return tuple(dict.fromkeys(c.numerator + c.denominator))
        if self.base.startswith("ais:"):
            return ()
        return (self.base,)


def aggregate(daily: pd.DataFrame, frequency: str, how: str) -> pd.DataFrame:
    """Daily (as_of, value, available_at) -> complete periods only. A period is complete once
    data exists for its last calendar day; its availability is the latest of its days."""
    if daily.empty:
        return pd.DataFrame(columns=EMPTY)
    d = daily.sort_values("as_of")
    if frequency == "D":
        return d[EMPTY].reset_index(drop=True)
    d = d.set_index("as_of")
    grouped = d.resample(FREQUENCIES[frequency], label="right", closed="right")
    values = grouped["value"].sum(min_count=1) if how == "sum" else grouped["value"].mean()
    avail_ns = grouped["available_at"].max()
    agg = pd.DataFrame(
        {"value": values, "available_at": avail_ns, "days": grouped["value"].count()}
    )
    # Drop the trailing period if its last day has no data yet (it is still in progress).
    period_end = agg.index.normalize()
    agg = agg[(period_end <= d.index.max().normalize()) & (agg["days"] > 0)]
    agg.index.name = "as_of"
    return agg.reset_index()[EMPTY]


def _daily_sum(raw: pd.DataFrame, keys: Sequence[str]) -> pd.DataFrame:
    """Sum of the given series per day, only on days where every series has a value."""
    wanted = set(keys)
    parts = raw[raw["key"].isin(wanted)]
    if parts.empty:
        return pd.DataFrame(columns=EMPTY)
    g = parts.groupby("as_of")
    out = pd.DataFrame(
        {
            "value": g["value"].sum(),
            "available_at": g["available_at"].max(),
            "n": g["key"].nunique(),
        }
    )
    out = out[out["n"] == len(wanted)]
    return out.reset_index()[EMPTY]


def base_daily(spec: FeatureSpec, raw: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """Daily base values and how they aggregate into periods (sum | mean)."""
    if spec.base in COMPOSITES:
        c = COMPOSITES[spec.base]
        missing = sorted(
            set(c.numerator + c.denominator) - set(raw["key"] if not raw.empty else [])
        )
        if missing:
            raise FeatureError(f"no data for {spec.base!r}: missing inputs {missing}")
        num = _daily_sum(raw, c.numerator)
        if c.kind == "sum":
            return num, "sum"
        den = _daily_sum(raw, c.denominator)
        m = num.merge(den, on="as_of", suffixes=("_n", "_d"))
        m = m[m["value_d"] > 0]
        ratio = pd.DataFrame(
            {
                "as_of": m["as_of"],
                "value": m["value_n"] / m["value_d"],
                "available_at": np.maximum(m["available_at_n"], m["available_at_d"]),
            }
        )
        return ratio, "mean"
    if raw.empty or spec.base not in set(raw["key"]):
        raise FeatureError(f"no data for {spec.base!r}")
    return _daily_sum(raw, [spec.base]), "sum"


def apply_transform(periods: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame:
    if periods.empty:
        return pd.DataFrame(columns=EMPTY)
    p = periods.sort_values("as_of").reset_index(drop=True)
    v = p["value"].astype(float)
    t = spec.transform
    n = int(re.sub(r"\D", "", t) or 0)
    if t == "yoy":
        t, n = "pct", PERIODS_PER_YEAR[spec.frequency]
    if t == "level":
        out, window = v, 1
    elif t.startswith("ma"):
        out, window = v.rolling(n, min_periods=n).mean(), n
    elif t.startswith("pct"):
        prev = v.shift(n)
        out, window = (v / prev - 1).where(prev > 0), n + 1
    elif t.startswith("diff"):
        out, window = v - v.shift(n), n + 1
    else:  # z-score against the trailing window, current period included (all known by then)
        mu, sd = v.rolling(n, min_periods=n).mean(), v.rolling(n, min_periods=n).std()
        out, window = ((v - mu) / sd).where(sd > 0), n
    # A transformed value is available only once every period in its window was.
    avail_ns = utc_ns(p["available_at"]).astype("int64").reset_index(drop=True)
    window_avail = utc_ns(avail_ns.rolling(window, min_periods=1).max().astype("int64"))
    res = pd.DataFrame({"as_of": p["as_of"], "value": out, "available_at": window_avail})
    return res.replace([np.inf, -np.inf], np.nan).dropna(subset=["value"]).reset_index(drop=True)


_AIS_CALLS = {
    "arrivals": ("actual_arrival_at", "count(*)::float"),
    "departures": ("actual_departure_at", "count(*)::float"),
    "port_hours": (
        "actual_departure_at",
        "avg(EXTRACT(EPOCH FROM c.actual_departure_at - c.actual_arrival_at) / 3600)",
    ),
}


def ais_daily(conn: Connection, metric: str, unlocode: str | None) -> pd.DataFrame:
    """ChainSight's own AIS-derived daily series. A day is available once it has ended and
    the last contributing record was stored (no backdating)."""
    if metric == "route_deviations":
        sql = (
            "SELECT date_trunc('day', raised_at) AS as_of, count(*)::float AS value, "
            "max(raised_at) AS available_at FROM system_alerts "
            "WHERE category = 'vessel_anomaly' AND details->>'type' = 'route_deviation' "
            "GROUP BY 1 ORDER BY 1"
        )
        params: dict[str, object] = {}
    else:
        if not unlocode:
            raise FeatureError("AIS port metrics need a UN/LOCODE, e.g. ais:arrivals:NLRTM")
        col, value = _AIS_CALLS[metric]
        both = " AND c.actual_arrival_at IS NOT NULL" if metric == "port_hours" else ""
        sql = (
            f"SELECT date_trunc('day', c.{col}) AS as_of, {value} AS value, "  # noqa: S608 - constants
            "max(c.created_at) AS available_at FROM voyage_port_calls c "
            "JOIN ports p ON p.id = c.port_id "
            f"WHERE p.unlocode = :u AND c.{col} IS NOT NULL{both} GROUP BY 1 ORDER BY 1"
        )
        params = {"u": unlocode}
    df = pd.DataFrame(conn.execute(sa.text(sql), params).all(), columns=EMPTY)
    if df.empty:
        return df
    df["as_of"] = utc_ns(df["as_of"])
    df["available_at"] = utc_ns(df["available_at"])
    df["value"] = df["value"].astype(float)
    df["available_at"] = np.maximum(df["available_at"], df["as_of"] + pd.Timedelta(days=1))
    return df


def compute_feature(
    conn: Connection, spec: FeatureSpec, raw: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Feature rows: as_of (period end), value, available_at."""
    spec.validate()
    if spec.base.startswith("ais:"):
        _, metric, *rest = spec.base.split(":")
        if metric not in AIS_METRICS:
            raise FeatureError(f"unknown AIS metric {metric!r}")
        daily = ais_daily(conn, metric, rest[0] if rest else None)
        how = "mean" if metric == "port_hours" else "sum"
    else:
        raw = raw if raw is not None else load_first_release(conn, spec.input_keys())
        daily, how = base_daily(spec, raw)
    return apply_transform(aggregate(daily, spec.frequency, how), spec)


def market_prices(conn: Connection, key: str) -> pd.DataFrame:
    """Closing prices (first release) for a market series: as_of, value, available_at."""
    df = load_first_release(conn, [key])
    if df.empty:
        raise FeatureError(f"no data for market series {key!r}")
    return df[EMPTY].reset_index(drop=True)


def catalog(conn: Connection) -> list[dict[str, object]]:
    """Base indicators available for feature construction, with coverage and provenance."""
    rows = conn.execute(
        sa.text(
            """
            SELECT s.key, s.name, s.category, s.entity_kind, s.entity_name, s.metric, s.unit,
                   s.publication_lag_days, s.attributes, ds.key AS source_key,
                   ds.name AS source_name, ds.data_mode,
                   min(o.as_of) AS first_as_of, max(o.as_of) AS last_as_of,
                   count(*) FILTER (WHERE o.revision = 0) AS n_obs,
                   count(*) FILTER (WHERE o.revision > 0) AS n_revisions
            FROM indicator_series s JOIN data_sources ds ON ds.id = s.source_id
            LEFT JOIN indicator_observations o ON o.series_id = s.id
            GROUP BY s.id, ds.id ORDER BY s.category, s.key
            """
        )
    ).all()
    out: list[dict[str, object]] = [dict(r._mapping) | {"kind": "series"} for r in rows]
    have = {r["key"] for r in out}
    for c in COMPOSITES.values():
        inputs = set(c.numerator + c.denominator)
        out.append(
            {
                "key": c.key,
                "name": c.name,
                "category": "maritime",
                "kind": "composite",
                "description": c.description,
                "inputs": sorted(inputs),
                "available": inputs <= have,
            }
        )
    return out
