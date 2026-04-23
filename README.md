# Motor Central — ZAG / PRIMOR

> Plataforma de governança e orquestração de dados para obras pesadas. Camada
> inteligente que integra Domínio, Onvio, Tangerino, OnSafety, TOTVS, Sistema 90,
> OneDrive, EasyJur, Conlicitação e WhatsApp em um único fluxo operacional.

O objetivo **não é recriar sistemas de mercado** — é eliminar as ilhas de
informação entre eles via APIs, scraping e RPA, com dashboards departamentais
(RH, Manutenção, Financeiro, Licitações, Jurídico).

## Estrutura do monorepo

```
apps/
  api/        FastAPI + SQLAlchemy + Alembic (REST, orquestração)
  workers/    Celery + Redis (scrapers, OCR, RPA, LLM handlers)
  web/        Next.js 15 (App Router) + Tailwind (dashboards)
infra/
  docker-compose.yml   Postgres, Redis, MinIO, MailHog, api, workers, web
docs/
  architecture.md   Visão arquitetural
  roadmap.md        Backlog por módulo (A–E)
  integrations.md   Cada adapter de terceiro
  lgpd.md           Estratégia de conformidade e auditoria
```

## Módulos (backend)

| Código | Módulo | Demandas do briefing |
|--------|--------|----------------------|
| **A** | `dp_sesmt`            | 1, 2, 3, 4 (DP, SESMT, INSS, e-learning) |
| **B** | `manutencao_frota`    | 5, 6, 7 (partes diárias, RPA, Sistema 90) |
| **C** | `financeiro_contratos`| 8, 12 (TOTVS, NF, contratos) |
| **D** | `licitacoes`          | 9 (Conlicitação, saúde municipal, habilitação) |
| **E** | `ia_tools`            | 10, 11 (reconhecimento facial, compras via WhatsApp) |

Cada módulo tem `router.py`, `schemas.py`, `service.py`, `models.py` isolados.
Integrações externas vivem em `apps/api/app/integrations/<sistema>/`.

## Setup local

### Pré-requisitos

- Docker + Docker Compose
- Python 3.12 (para rodar API fora do Docker)
- Node 22 (para rodar web fora do Docker)

### Subir tudo via Docker

```bash
cp apps/api/.env.example apps/api/.env
cp apps/web/.env.example apps/web/.env.local
docker compose -f infra/docker-compose.yml up --build
```

Serviços:
- API: http://localhost:8000 (Swagger em `/docs`)
- Web: http://localhost:3000
- MinIO console: http://localhost:9001 (`minio` / `minio123`)
- MailHog: http://localhost:8025

### Rodar API localmente

```bash
cd apps/api
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

### Rodar web localmente

```bash
cd apps/web
npm ci
npm run dev
```

### Rodar workers localmente

```bash
cd apps/workers
pip install -e ".[dev]"
celery -A worker.main.celery_app worker --loglevel=info
```

## Testes e lint

```bash
# API
cd apps/api && ruff check . && pytest

# Web
cd apps/web && npm run lint && npm run build
```

## Status do scaffold

Este é o **commit inicial**. Endpoints e tasks existem como stubs (retornam
`{"stub": true}`) — nenhuma integração com sistemas de terceiros está
implementada ainda. O roadmap de implementação está em [`docs/roadmap.md`](./docs/roadmap.md).

## Licença

Proprietário — Dexi Digital / Construtora ZAG.
