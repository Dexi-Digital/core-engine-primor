# AGENTS.md

Guia operacional para quem trabalha neste repositório.

## Onde fazer mudanças

- **Novo módulo de negócio** → crie `apps/api/app/modules/<nome>/{router,schemas,service,models}.py`
  e registre o router em `apps/api/app/main.py`. Espelhe em `apps/workers/worker/tasks/<nome>.py`
  se precisar de tasks assíncronas e crie uma rota `fila` em `celery_app.conf.task_routes`.
- **Novo adapter de terceiro** → crie `apps/api/app/integrations/<sistema>/client.py`
  herdando de `IntegrationClient` (`app/integrations/base.py`). Documente em
  `docs/integrations.md`.
- **Novo dashboard** → adicione uma página em `apps/web/src/app/(dashboard)/<rota>/page.tsx`
  e inclua o link em `apps/web/src/app/(dashboard)/layout.tsx`.
- **Migration** → `cd apps/api && alembic revision --autogenerate -m "..."`.

## Convenções

- Python 3.12, Ruff para lint, Pytest para testes (asyncio mode auto).
- TypeScript strict, ESLint do Next.js, Tailwind v4.
- Scrapers/OCR/RPA rodam **sempre** no worker (Celery) — nunca bloqueiem a API.
- Toda mutação de recurso sensível (ASO, documentos DP, INSS) grava em `audit_log`.
- Segredos nunca entram no código — use `.env` (dev) ou o secret manager do ambiente.

## Workflow de PR

1. Branch a partir de `main`: `git checkout -b feat/<descricao-curta>`.
2. Rodar lint/tests localmente: `ruff check . && pytest` em `apps/api`; `npm run lint && npm run build` em `apps/web`.
3. Abrir PR descrevendo **qual demanda do briefing** está sendo atendida (1–12).
4. CI precisa passar (api + workers + web).

## Prioridades de implementação

Seguir a ordem em [`docs/roadmap.md`](./docs/roadmap.md) — começar por Módulo A
(DP / SESMT) porque é o que tem maior dor operacional segundo o briefing.
