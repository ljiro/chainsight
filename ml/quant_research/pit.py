"""Point-in-time access to indicator data.

Two rules keep research free of look-ahead bias:

1. **First release.** Historical signals use each period's first-released value (revision 0),
   available from its `available_at`. Later revisions are stored but treated as information
   that arrived later; they never leak backwards.
2. **Known-by decision time.** A value is usable at decision time `t` only if
   `available_at <= t`. Derived values inherit the latest `available_at` of their inputs.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.engine import Connection

COLUMNS = ["key", "as_of", "value", "available_at"]


def utc_ns(values: object) -> pd.Series:
    """Timestamps as UTC, nanosecond resolution (one unit everywhere avoids silent mismatches)."""
    s = values if isinstance(values, pd.Series) else pd.Series(values)
    return pd.to_datetime(s, utc=True).dt.as_unit("ns")


def load_first_release(conn: Connection, keys: Sequence[str]) -> pd.DataFrame:
    """Revision-0 observations for the series keys: columns key, as_of, value, available_at."""
    if not keys:
        return pd.DataFrame(columns=COLUMNS)
    rows = conn.execute(
        sa.text(
            "SELECT s.key, o.as_of, o.value, o.available_at FROM indicator_observations o "
            "JOIN indicator_series s ON s.id = o.series_id "
            "WHERE s.key = ANY(:keys) AND o.revision = 0 ORDER BY s.key, o.as_of"
        ),
        {"keys": list(keys)},
    ).all()
    df = pd.DataFrame(rows, columns=COLUMNS)
    if not df.empty:
        df["as_of"] = utc_ns(df["as_of"])
        df["available_at"] = utc_ns(df["available_at"])
        df["value"] = df["value"].astype(float)
    return df


def known_at(feature: pd.DataFrame, decision_times: pd.DatetimeIndex) -> pd.DataFrame:
    """For each decision time, the most recent period whose value was available by then.

    `feature` has columns as_of, value, available_at (one row per period). Returns a frame
    indexed by decision time with columns as_of (the period used), value, available_at and
    staleness_days. Periods that become available out of order are handled: a later period
    published earlier is used as soon as it is available; an earlier one arriving later never
    replaces a newer period already known.
    """
    f = feature.dropna(subset=["value"]).sort_values(["available_at", "as_of"])
    out = pd.DataFrame(
        index=decision_times, columns=["as_of", "value", "available_at"], dtype=object
    )
    if f.empty or len(decision_times) == 0:
        out["staleness_days"] = pd.NA
        return out
    # Event timeline: after each publication, the newest period known so far.
    as_of = utc_ns(f["as_of"]).reset_index(drop=True)
    keep = as_of == as_of.cummax()
    timeline = f.loc[keep.values, ["available_at", "as_of", "value"]].drop_duplicates(
        "available_at", keep="last"
    )
    timeline = timeline.assign(available_at=utc_ns(timeline["available_at"]))
    left = pd.DataFrame({"t": utc_ns(decision_times)}).sort_values("t")
    merged = pd.merge_asof(
        left, timeline, left_on="t", right_on="available_at", direction="backward"
    )
    merged.index = pd.DatetimeIndex(merged["t"])
    out = merged[["as_of", "value", "available_at"]].reindex(
        pd.DatetimeIndex(utc_ns(decision_times))
    )
    out["staleness_days"] = (out.index.to_series() - pd.to_datetime(out["as_of"], utc=True)).dt.days
    return out


def snapshot_hash(*frames: pd.DataFrame) -> str:
    """Deterministic hash of the exact inputs used by an analysis or backtest."""
    h = hashlib.sha256()
    for df in frames:
        h.update(
            pd.util.hash_pandas_object(df.reset_index(drop=True), index=False).values.tobytes()
        )
        h.update(b"|")
    return h.hexdigest()
