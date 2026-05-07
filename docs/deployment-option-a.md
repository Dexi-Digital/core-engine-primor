# Option A — Deployment minimo (single tenant)

Stack containerizada para 1 construtora (~15-50 usuarios, ~500 funcionarios,
~50 veiculos, ~10 obras), custo-alvo R$ 250-400/mes em DigitalOcean / Render
/ Railway / Fly.io. **Storage usa OneDrive (Microsoft Graph)** — sem S3, sem
MinIO, sem volume persistente para anexos.

> Quando voce escalar para >1 construtora ou precisar de HA + backups
> automatizados em multi-AZ, leia `docs/deployment-option-b.md` (a
> redigir; refs ja estao no `AGENTS.md` na visao mais robusta).

## Componentes

| Processo | CPU | RAM | Notas |
|---|---|---|---|
| `api` (FastAPI/uvicorn) | 2 vCPU | 2 GB | 1 instancia |
| `workers` (Celery + beat embutido) | 2 vCPU | 2 GB | 1 instancia. **Nao escale para >1**: o `-B` (beat) duplicaria os 5 cron jobs |
| `web` (Next.js standalone) | 1 vCPU | 1 GB | Pode ir pra Vercel free/pro tambem |
| Postgres managed | 2 vCPU | 4 GB | 25 GB SSD, extensao `pg_trgm` habilitada |
| Redis managed | — | 256 MB | broker Celery + cache |
| Storage | — | — | **OneDrive** (Microsoft Graph) — 25 TB no plano da PRIMOR |

## Variaveis obrigatorias

Copie `apps/api/.env.example` para `.env.prod` na raiz e preencha:

```ini
# Core
ENVIRONMENT=production           # ATIVA secret guard (raise se SECRET_KEY=default)
DATABASE_URL=postgresql+asyncpg://USER:PASS@HOST:5432/DB
REDIS_URL=rediss://:PASS@HOST:6379/0
SECRET_KEY=<openssl rand -hex 32>
PUBLIC_BASE_URL=https://app.suaempresa.com.br
CORS_ORIGINS=["https://app.suaempresa.com.br"]

# Storage -> OneDrive
STORAGE_BACKEND=onedrive
MS_GRAPH_TENANT_ID=...
MS_GRAPH_CLIENT_ID=...
MS_GRAPH_CLIENT_SECRET=...
MS_GRAPH_DRIVE_ID=...
MS_GRAPH_ROOT_FOLDER=MotorCentral/editais

# Email outbound (alertas + boletins)
RESEND_API_KEY=re_...
RESEND_FROM_EMAIL=Motor Central <alertas@suaempresa.com.br>

# Integrações pagas (todas opcionais — caem em mock se vazias)
INFOSIMPLES_TOKEN=...                  # Detran SP/MG/GO + CREA
GOOGLE_DOCUMENTAI_CREDENTIALS_JSON=... # OCR de Parte Diaria
GCP_PROJECT_ID=...
DOCUMENTAI_PROCESSOR_ID=...
ANTHROPIC_API_KEY=...                  # ou OPENAI_API_KEY

# Seed do primeiro admin (so na primeira subida)
ADMIN_EMAIL=admin@suaempresa.com.br
ADMIN_PASSWORD=<senha forte temporaria>
ADMIN_NOME=Administrador
```

## Smoke test das credenciais antes do deploy

Roda em ~10s, valida auth + drive + upload + download + delete:

```bash
cd /caminho/para/core-engine-primor
MS_GRAPH_TENANT_ID=... \
MS_GRAPH_CLIENT_ID=... \
MS_GRAPH_CLIENT_SECRET=... \
MS_GRAPH_DRIVE_ID=... \
apps/api/.venv/bin/python scripts/test_onedrive_credentials.py
```

Saida esperada: `Smoke test PASSOU.` Se quebrar, **nao faz deploy** — corrige
credencial ou drive antes.

## Release script (migrations)

`Procfile` na raiz declara o `release:`:

```
release: cd apps/api && alembic upgrade head
```

`apps/api/alembic/env.py` ja le `DATABASE_URL` do ambiente (com normalizacao
`+asyncpg` → `+psycopg2`). Render/Railway/Fly executam o `release` antes de
trocar o trafego pra nova versao da API; em caso de falha (ex: migration
quebrada), o deploy aborta sem virar a versao live.

## Render

```yaml
# render.yaml na raiz (a criar quando for subir)
services:
  - type: web
    name: motor-central-api
    runtime: docker
    dockerfilePath: ./apps/api/Dockerfile
    dockerContext: ./apps/api
    plan: starter   # 0.5 vCPU / 512MB -- sobe pra `standard` (2vCPU/2GB) quando o trafego justificar
    envVars:
      - key: DATABASE_URL
        fromDatabase:
          name: motor-central-db
          property: connectionString
      - key: REDIS_URL
        fromService:
          type: redis
          name: motor-central-redis
          property: connectionString
      - key: ENVIRONMENT
        value: production
      - key: SECRET_KEY
        generateValue: true
      # ... resto dos secrets via dashboard
    healthCheckPath: /healthz
    preDeployCommand: cd apps/api && alembic upgrade head

  - type: worker
    name: motor-central-worker
    runtime: docker
    dockerfilePath: ./apps/workers/Dockerfile
    dockerContext: ./apps/workers
    plan: starter
    envVars:
      - key: DATABASE_URL
        fromDatabase: { name: motor-central-db, property: connectionString }
      - key: REDIS_URL
        fromService: { type: redis, name: motor-central-redis, property: connectionString }

  - type: web
    name: motor-central-web
    runtime: docker
    dockerfilePath: ./apps/web/Dockerfile
    dockerContext: ./apps/web
    plan: starter

databases:
  - name: motor-central-db
    plan: starter
    postgresMajorVersion: "16"
```

Aplica `pg_trgm` rodando uma vez no console do banco:

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

## Railway

1. Cria projeto e adiciona Postgres + Redis no dashboard (cada um vira service)
2. `railway up` na raiz — ele detecta o Procfile e cria 1 web (api) + 1 worker
3. Aponta o build da web pra `apps/web/Dockerfile` e cria service novo
4. Configura todas as env vars no dashboard
5. Railway roda o `release:` automatic antes do primeiro boot

## Fly.io

```bash
# fly.toml na raiz (a criar quando for subir)
fly launch --no-deploy --copy-config
fly volumes create pgdata --size 10 --region gru
fly secrets set DATABASE_URL=... REDIS_URL=... SECRET_KEY=... \
  MS_GRAPH_TENANT_ID=... MS_GRAPH_CLIENT_ID=... MS_GRAPH_CLIENT_SECRET=... \
  MS_GRAPH_DRIVE_ID=... STORAGE_BACKEND=onedrive RESEND_API_KEY=... \
  ENVIRONMENT=production
fly deploy
```

`fly.toml` precisa ter:

```toml
[deploy]
release_command = "cd apps/api && alembic upgrade head"
```

## DigitalOcean (1 VPS)

Para a opcao mais barata e simples (R$ ~120/mes):

1. Cria Droplet 4vCPU/8GB Ubuntu 24.04
2. `apt install docker.io docker-compose-plugin`
3. `git clone https://github.com/Dexi-Digital/core-engine-primor.git /opt/motor-central`
4. `cp apps/api/.env.example /opt/motor-central/.env.prod` e edita
5. `docker compose -f infra/docker-compose.prod.yml --env-file .env.prod up -d`
6. Configura Caddy / Traefik na frente pro TLS automatico

## Observability

- `/healthz` (liveness) e `/readyz` (readiness, agrega DB/Redis/storage) ja
  expostos.
- Logs estruturados (`structlog`) com `correlation_id` propagado API↔worker.
- Plug Sentry futuro: setar `SENTRY_DSN` (codigo a adicionar em PR pequeno).

## Backup

- Postgres managed (Render/Railway/Fly): backup diario PITR habilitado por
  padrao no plano Starter.
- OneDrive: o tenant da PRIMOR tem retencao do M365 — pasta `MotorCentral/`
  e versionada por padrao.

## Custos estimados (Render starter)

| Servico | Plano | USD/mes |
|---|---|---|
| Web (api) | Starter (0.5/512) | $7 |
| Worker | Starter | $7 |
| Web (next) | Starter (ou Vercel free) | $7 / 0 |
| Postgres | Starter (0.5/256MB/1GB) | $7 |
| Redis | Starter (25MB) | free |
| **Total** | | **~$28/mes** (~R$ 150) |

Sobe pra ~R$ 400/mes quando voce migrar pra Standard (2vCPU/2GB), o que e o
target real da Option A com tracos esperados.
