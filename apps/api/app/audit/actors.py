"""Identifiers canonicos de `actor` gravados em `audit_log`.

AGENTS.md manda registrar TODA mutacao de recurso sensivel (ASO, docs DP,
partes diarias, CND, ART, consultas Detran, etc.) em `audit_log`. Quando a
mutacao vem de um request HTTP, o router passa `actor=current_user.email`
e esse email fica no audit.

Quando a mutacao vem do worker (Celery), nao ha `current_user` no contexto.
Antes desse modulo cada pacote definia seu proprio `_AUDIT_ACTOR_PLACEHOLDER
= "system"`, gravando bare string `"system"` sem distinguir:

- upload HTTP que dispara OCR async (deveria virar email do uploader);
- task agendada pelo Celery beat (cron diario de alertas);
- task ad-hoc dispatched por worker sem contexto de usuario (raro).

Os constantes abaixo dao semantica a cada caso. O valor `"system"` e mantido
pra nao quebrar queries existentes que filtram `WHERE actor = 'system'`
(testes, dashboards). Novos paths devem usar os sub-qualificadores.
"""
from __future__ import annotations

# Mutacao sem contexto de usuario (default legado). Evitar em novos paths --
# prefira SYSTEM_BEAT ou SYSTEM_WORKER conforme a origem.
SYSTEM = "system"

# Task agendada pelo Celery beat (cron). Ex.: `dispatch_aso_alerts` as 08h05.
# Quando voce precisa responder "quem enviou esse email de ASO?" em auditoria,
# `system:beat` distingue de uma chamada manual feita pelo endpoint
# `POST /dp-sesmt/aso/alerts/dispatch` (que grava o email real do admin).
SYSTEM_BEAT = "system:beat"

# Task async dispatched por um worker SEM contexto de usuario (ex.: retry
# agendado por um dead-letter handler). Caso raro -- tipicamente o caller
# HTTP ja propaga `actor=current_user.email` nos args do send_task.
SYSTEM_WORKER = "system:worker"


__all__ = ["SYSTEM", "SYSTEM_BEAT", "SYSTEM_WORKER"]
