"""Modulo C - Financeiro e Contratos.

Escopo:
    - Automacao TOTVS (OCR/XML de NFs, conciliacao bancaria).
    - Ciclo de contratos (emissao, assinatura, AP, alertas de vencimento).
    - Cruzamento de dados para conferencia (combustivel, aluguel, descontos).
"""
from __future__ import annotations

from fastapi import APIRouter

from app.modules.dp_sesmt.schemas import ModuleStatus

router = APIRouter()


@router.get("/status", response_model=ModuleStatus)
async def status() -> ModuleStatus:
    return ModuleStatus(module="financeiro_contratos", implemented=False, stub=True)
