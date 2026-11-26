"""Autogenerate comparison hooks shared by Alembic's env.py and the drift test."""

from __future__ import annotations

from typing import Any

from geoalchemy2 import Geography, Geometry

# Time indexes created by TimescaleDB's create_hypertable(), or by the plain-PostGIS fallback
# in migration 0003. They are environment-dependent, so they are not declared on the models.
ENVIRONMENT_INDEXES = frozenset(
    {
        "vessel_positions_observed_at_idx",
        "port_observations_observed_at_idx",
        "ix_vessel_positions_observed_at",
    }
)


def include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    if reflected and compare_to is None:
        # Ignore reflected tables that are not ours (e.g. PostGIS `spatial_ref_sys`).
        if type_ == "table":
            return False
        if type_ == "index" and name in ENVIRONMENT_INDEXES:
            return False
    return True


def compare_type(
    context: Any,
    inspected_column: Any,
    metadata_column: Any,
    inspected_type: Any,
    metadata_type: Any,
) -> bool | None:
    # Spatial types reflect with slightly different parameters; compare by class only.
    if isinstance(metadata_type, Geography | Geometry):
        return not isinstance(inspected_type, Geography | Geometry)
    return None  # fall back to Alembic's default comparison
