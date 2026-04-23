"""ORM models for DP / SESMT (stubs)."""
from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Employee(Base):
    __tablename__ = "dp_employees"

    id: Mapped[int] = mapped_column(primary_key=True)
    cpf: Mapped[str] = mapped_column(String(14), unique=True, index=True)
    nome_completo: Mapped[str] = mapped_column(String(255))
    cargo: Mapped[str] = mapped_column(String(100))
