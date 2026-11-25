"""Provider adapters (AIS, carriers, weather, ports).

Adapters fetch and validate provider data and map it to internal DTOs.
They must not import `database` or write to storage (see docs/architecture/system-design.md §3.1).
"""
