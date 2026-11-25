from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ComponentState = Literal["ok", "degraded", "error"]


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str
    environment: str
    time: datetime


class ComponentHealth(BaseModel):
    name: str
    status: ComponentState
    latency_ms: float | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class ReadinessResponse(BaseModel):
    status: ComponentState
    time: datetime
    components: list[ComponentHealth]
