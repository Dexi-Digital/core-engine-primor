"""Modulo A - Departamento Pessoal & SESMT.

Escopo (ver docs/roadmap.md):
    - Dossie de admissao (varredura de antecedentes, INSS, beneficios).
    - Varredura documental OCR (OneDrive -> deteccao de ASOs/docs faltantes).
    - Onboarding automatico (Dominio, Onvio, Tangerino, OnSafety).
    - E-learning via WhatsApp + assinatura digital + certificado.
    - Acompanhamento INSS (afastamentos, alertas).
"""
from __future__ import annotations

from fastapi import APIRouter

from app.modules.dp_sesmt.schemas import EmployeeOnboardingRequest, ModuleStatus

router = APIRouter()


@router.get("/status", response_model=ModuleStatus)
async def status() -> ModuleStatus:
    return ModuleStatus(module="dp_sesmt", implemented=False, stub=True)


@router.post("/onboarding", response_model=ModuleStatus)
async def onboarding(payload: EmployeeOnboardingRequest) -> ModuleStatus:
    # TODO(modulo-A): disparar sync com Dominio/Onvio/Tangerino/OnSafety via Celery.
    _ = payload
    return ModuleStatus(module="dp_sesmt", implemented=False, stub=True)
