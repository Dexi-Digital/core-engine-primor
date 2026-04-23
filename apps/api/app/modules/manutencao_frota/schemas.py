from __future__ import annotations

from pydantic import BaseModel


class EquipmentUsage(BaseModel):
    equipment_id: str
    horas: float
    km: float | None = None
    data: str  # ISO-8601
