"""Entity resolution: map AIS MMSIs to vessels and apply static (identity) reports.

Identity rules
- An active MMSI identifier maps to exactly one vessel (partial unique index).
- IMO is the stable identity. If a static report pairs an MMSI with an IMO that already
  belongs to another vessel (reflagging / MMSI reassignment), the MMSI moves to that
  vessel; history is kept by closing the old identifier rows, never by deleting them.
- A vessel's IMO is never overwritten by a conflicting one; the conflict is logged.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from core.logging_config import get_logger
from integrations.ais.messages import AisStaticReport
from services.vessel_registry.classification import (
    guess_carrier_from_name,
    vessel_class_from_ship_type,
)

log = get_logger(__name__)


@dataclass
class VesselState:
    vessel_id: uuid.UUID
    last_seen_at: datetime | None
    last_lat: float | None
    last_lon: float | None
    is_new: bool = False


_SELECT_BY_MMSI = sa.text(
    """
    SELECT i.value AS mmsi, v.id, v.last_seen_at,
           ST_Y(v.last_location::geometry) AS lat, ST_X(v.last_location::geometry) AS lon
    FROM vessel_identifiers i JOIN vessels v ON v.id = i.vessel_id
    WHERE i.scheme = 'mmsi' AND i.valid_to IS NULL AND i.value = ANY(:values)
    """
)


async def _select_existing(session: AsyncSession, mmsis: Iterable[int]) -> dict[int, VesselState]:
    rows = await session.execute(_SELECT_BY_MMSI, {"values": [str(m) for m in mmsis]})
    return {int(r.mmsi): VesselState(r.id, r.last_seen_at, r.lat, r.lon) for r in rows}


async def resolve_vessels(
    session: AsyncSession,
    mmsis: set[int],
    names: Mapping[int, str | None],
    source_id: uuid.UUID,
    seen_at: Mapping[int, datetime],
) -> dict[int, VesselState]:
    """Return state for every MMSI, creating vessels for unseen ones."""
    found = await _select_existing(session, mmsis)
    missing = sorted(mmsis - found.keys())
    if not missing:
        return found

    payload = [
        {"mmsi": m, "name": names.get(m), "first_seen_at": seen_at[m].isoformat()} for m in missing
    ]
    created = await session.execute(
        sa.text(
            "INSERT INTO vessels (mmsi, name, first_seen_at) "
            "SELECT r.mmsi, r.name, r.first_seen_at FROM jsonb_to_recordset(CAST(:rows AS jsonb)) "
            "AS r(mmsi int, name text, first_seen_at timestamptz) RETURNING id, mmsi"
        ),
        {"rows": json.dumps(payload)},
    )
    new_ids = {r.mmsi: r.id for r in created}
    claimed = await session.execute(
        sa.text(
            "INSERT INTO vessel_identifiers (vessel_id, scheme, value, valid_from, source_id) "
            "SELECT r.vessel_id, 'mmsi', r.mmsi, r.valid_from, :source_id "
            "FROM jsonb_to_recordset(CAST(:rows AS jsonb)) "
            "AS r(vessel_id uuid, mmsi text, valid_from timestamptz) "
            "ON CONFLICT (scheme, value) WHERE valid_to IS NULL AND scheme IN ('mmsi', 'imo') "
            "DO NOTHING RETURNING value"
        ),
        {
            "rows": json.dumps(
                [
                    {"vessel_id": str(vid), "mmsi": str(m), "valid_from": seen_at[m].isoformat()}
                    for m, vid in new_ids.items()
                ]
            ),
            "source_id": source_id,
        },
    )
    won = {int(r.value) for r in claimed}
    lost = [vid for m, vid in new_ids.items() if m not in won]
    if lost:  # another writer claimed these MMSIs concurrently; use its vessels
        await session.execute(sa.text("DELETE FROM vessels WHERE id = ANY(:ids)"), {"ids": lost})
        found.update(await _select_existing(session, set(new_ids) - won))
    for m in won:
        found[m] = VesselState(new_ids[m], None, None, None, is_new=True)
        if names.get(m):
            await _record_name(session, new_ids[m], names[m], seen_at[m], source_id)  # type: ignore[arg-type]
    return found


async def _record_name(
    session: AsyncSession, vessel_id: uuid.UUID, name: str, at: datetime, source_id: uuid.UUID
) -> None:
    await session.execute(
        sa.text(
            "UPDATE vessel_identifiers SET valid_to = :at WHERE vessel_id = :vid "
            "AND scheme = 'name' AND valid_to IS NULL AND value <> :name"
        ),
        {"vid": vessel_id, "at": at, "name": name},
    )
    await session.execute(
        sa.text(
            "INSERT INTO vessel_identifiers (vessel_id, scheme, value, valid_from, source_id) "
            "SELECT CAST(:vid AS uuid), 'name', CAST(:name AS varchar), :at, :source_id "
            "WHERE NOT EXISTS (SELECT 1 FROM vessel_identifiers WHERE vessel_id = CAST(:vid AS uuid) "
            "AND scheme = 'name' AND valid_to IS NULL AND value = CAST(:name AS varchar))"
        ),
        {"vid": vessel_id, "name": name, "at": at, "source_id": source_id},
    )


async def _move_mmsi(
    session: AsyncSession,
    mmsi: int,
    from_vessel: uuid.UUID,
    to_vessel: uuid.UUID,
    at: datetime,
    source_id: uuid.UUID,
) -> None:
    await session.execute(
        sa.text(
            "UPDATE vessel_identifiers SET valid_to = :at WHERE scheme = 'mmsi' "
            "AND valid_to IS NULL AND (value = :mmsi OR vessel_id = :to)"
        ),
        {"at": at, "mmsi": str(mmsi), "to": to_vessel},
    )
    await session.execute(
        sa.text(
            "INSERT INTO vessel_identifiers (vessel_id, scheme, value, valid_from, source_id) "
            "VALUES (:to, 'mmsi', :mmsi, :at, :source_id)"
        ),
        {"to": to_vessel, "mmsi": str(mmsi), "at": at, "source_id": source_id},
    )
    await session.execute(
        sa.text("UPDATE vessels SET mmsi = NULL WHERE id = :id"), {"id": from_vessel}
    )
    await session.execute(
        sa.text("UPDATE vessels SET mmsi = :mmsi, updated_at = now() WHERE id = :id"),
        {"mmsi": mmsi, "id": to_vessel},
    )
    log.info(
        "vessel_identity_merged", mmsi=mmsi, from_vessel=str(from_vessel), to_vessel=str(to_vessel)
    )


async def apply_static_reports(
    session: AsyncSession,
    reports: Iterable[AisStaticReport],
    states: dict[int, VesselState],
    source_id: uuid.UUID,
) -> int:
    """Apply identity/voyage data. Mutates `states` when an MMSI moves to another vessel.

    Returns the number of IMO conflicts that were refused."""
    conflicts = 0
    for r in reports:
        state = states[r.mmsi]
        vid = state.vessel_id
        if r.imo is not None:
            owner = (
                await session.execute(
                    sa.text(
                        "SELECT vessel_id FROM vessel_identifiers WHERE scheme = 'imo' "
                        "AND value = :imo AND valid_to IS NULL"
                    ),
                    {"imo": str(r.imo)},
                )
            ).scalar_one_or_none()
            if owner is not None and owner != vid:
                await _move_mmsi(session, r.mmsi, vid, owner, r.observed_at, source_id)
                vid = owner
                states[r.mmsi] = VesselState(owner, None, None, None)
                # Re-read the latest position of the vessel the MMSI now belongs to.
                states[r.mmsi] = (await _select_existing(session, {r.mmsi}))[r.mmsi]
            elif owner is None:
                current = (
                    await session.execute(
                        sa.text("SELECT imo FROM vessels WHERE id = :id"), {"id": vid}
                    )
                ).scalar_one()
                if current is None:
                    await session.execute(
                        sa.text("UPDATE vessels SET imo = :imo WHERE id = :id"),
                        {"imo": r.imo, "id": vid},
                    )
                    await session.execute(
                        sa.text(
                            "INSERT INTO vessel_identifiers (vessel_id, scheme, value, valid_from, "
                            "source_id) VALUES (:id, 'imo', :imo, :at, :source_id)"
                        ),
                        {"id": vid, "imo": str(r.imo), "at": r.observed_at, "source_id": source_id},
                    )
                elif current != r.imo:
                    conflicts += 1
                    log.warning(
                        "vessel_imo_conflict", vessel_id=str(vid), stored=current, reported=r.imo
                    )

        if r.name:
            await _record_name(session, vid, r.name, r.observed_at, source_id)
        cls = vessel_class_from_ship_type(r.ship_type) if r.ship_type is not None else None
        await session.execute(
            sa.text(
                """
                UPDATE vessels SET
                    name = COALESCE(:name, name),
                    call_sign = COALESCE(:call_sign, call_sign),
                    ship_type_code = COALESCE(:ship_type, ship_type_code),
                    length_m = COALESCE(:length, length_m),
                    beam_m = COALESCE(:beam, beam_m),
                    draught_m = COALESCE(:draught, draught_m),
                    destination = COALESCE(:destination, destination),
                    -- AIS may only set the class if nothing better (registry/manual) has.
                    vessel_class = CASE WHEN CAST(:cls AS varchar) IS NOT NULL
                        AND (vessel_class_source IS NULL OR vessel_class_source = 'ais_ship_type')
                        THEN CAST(:cls AS varchar) ELSE vessel_class END,
                    vessel_class_source = CASE WHEN CAST(:cls AS varchar) IS NOT NULL
                        AND (vessel_class_source IS NULL OR vessel_class_source = 'ais_ship_type')
                        THEN 'ais_ship_type' ELSE vessel_class_source END,
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {
                "id": vid,
                "name": r.name,
                "call_sign": r.call_sign,
                "ship_type": r.ship_type,
                "length": r.length_m,
                "beam": r.beam_m,
                "draught": r.draught_m,
                "destination": r.destination,
                "cls": cls.value if cls else None,
            },
        )
        guess = guess_carrier_from_name(r.name)
        if guess is not None:
            await session.execute(
                sa.text(
                    "INSERT INTO vessel_carrier_history (vessel_id, carrier_id, relation, method, "
                    "confidence, valid_from, source_id) "
                    "SELECT CAST(:vid AS uuid), c.id, 'operator', 'name_heuristic', :conf, :at, "
                    ":source_id FROM carriers c WHERE c.code = :code AND NOT EXISTS ("
                    "SELECT 1 FROM vessel_carrier_history h WHERE h.vessel_id = CAST(:vid AS uuid) "
                    "AND h.valid_to IS NULL)"
                ),
                {
                    "vid": vid,
                    "conf": guess.confidence,
                    "at": r.observed_at,
                    "source_id": source_id,
                    "code": guess.carrier_code,
                },
            )
    return conflicts
