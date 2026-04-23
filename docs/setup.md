# Setup — ambiente de desenvolvimento

Passo a passo completo está no [README](../README.md). Este documento cobre
dúvidas específicas.

## Rodando só um serviço via Docker

```bash
docker compose -f infra/docker-compose.yml up postgres redis minio
# em outro terminal, rode a API localmente (iteração mais rápida)
cd apps/api && uvicorn app.main:app --reload
```

## Criando a primeira migration

```bash
cd apps/api
alembic revision --autogenerate -m "initial"
alembic upgrade head
```

## Inspecionando tasks Celery

```bash
# Monitor web (Flower)
pip install flower
celery -A worker.main.celery_app flower --port=5555
# abre em http://localhost:5555
```

## Troubleshooting

- **`ModuleNotFoundError: app`** → certifique-se de rodar de `apps/api/` (não
  do root do monorepo) ou instale em modo editável.
- **Postgres não sobe** → remova o volume: `docker compose -f infra/docker-compose.yml down -v`.
- **Next.js `fetch failed`** → copie `apps/web/.env.example` para `apps/web/.env.local` e ajuste `NEXT_PUBLIC_API_BASE_URL`.
