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

## Railway (caminho recomendado)

Por que Railway e nao Vercel: na Vercel a API vira serverless, **nao ha
worker nem Redis**, e o storage de anexos e efemero — os crons ficam
parados e o que separa "web" de "app" precisa ser costurado na mao. No
Railway tudo vive num projeto so, com Postgres e Redis como plugins de
um clique, e o worker roda de verdade.

O repo ja esta preparado: tres `Dockerfile` (api, workers, web), o
`next.config` em `output: "standalone"`, e um `railway.json` por app com
build, start e migrations.

### Passo a passo

1. **Criar o projeto** — em railway.app, "New Project" → "Deploy from
   GitHub repo" → `Dexi-Digital/core-engine-primor`.

2. **Adicionar os dois plugins** — "New" → "Database" → **PostgreSQL**, e
   de novo → **Redis**. O Railway injeta `DATABASE_URL` e `REDIS_URL`
   nos outros services automaticamente. Não é preciso converter a URL na
   mão: `resolve_async_database_url` normaliza `postgresql://` →
   `postgresql+asyncpg://` e `sslmode=` → `ssl=` no boot.

3. **Habilitar o `pg_trgm`** — no console do Postgres do projeto:
   ```sql
   CREATE EXTENSION IF NOT EXISTS pg_trgm;
   ```
   Sem isso a migration `3fbc4a1e8d57` falha.

4. **Criar os três services**, cada um apontando para um diretório do
   monorepo (Settings → Root Directory):

   | Service | Root Directory | O que faz |
   |---|---|---|
   | `api` | `apps/api` | FastAPI. Roda `alembic upgrade head` antes de subir. |
   | `worker` | `apps/workers` | Celery + beat. **Manter em 1 réplica** — com 2, os crons disparam em dobro. |
   | `web` | `apps/web` | Next.js. É o que ganha o domínio público. |

   O `railway.json` de cada pasta já traz build e start; não é preciso
   configurar comando nenhum.

5. **Variáveis de ambiente.** No service `web`, apontar para a API:
   ```ini
   NEXT_PUBLIC_API_BASE_URL=https://<dominio-do-service-api>
   ```
   Nos services `api` e `worker`, o mínimo para subir:
   ```ini
   ENVIRONMENT=production
   SECRET_KEY=<gere com: openssl rand -hex 32>
   PUBLIC_BASE_URL=https://<dominio-do-service-web>
   CORS_ORIGINS=["https://<dominio-do-service-web>"]
   ADMIN_EMAIL=...
   ADMIN_PASSWORD=<senha forte temporaria>
   ```
   `DATABASE_URL` e `REDIS_URL` vêm dos plugins. **`ENVIRONMENT=production`
   ativa o guard que impede subir com a `SECRET_KEY` default.**

   As credenciais de integração são todas opcionais — sem elas o adapter
   correspondente cai em mock. Para o Sólides funcionar de verdade,
   basta `TANGERINO_API_KEY`.

6. **Gerar o domínio** — no service `web`, Settings → Networking →
   "Generate Domain". É a URL para mandar ao cliente.

### Ordem na primeira subida

Postgres e Redis primeiro, depois `api` (as migrations rodam no
pre-deploy e criam o schema), depois `worker` e `web`. Subir a `api`
antes do banco existir só gera um deploy vermelho e um retry.

### Custo

Railway cobra por uso. Três services pequenos + Postgres + Redis para
uma demo ficam na casa de US$ 15–25/mês. O plano Hobby (US$ 5 de
crédito) segura um ambiente de demonstração ligado por poucas horas
por dia, mas não 24/7.

### Alternativa

Render faz o mesmo com um `render.yaml` na raiz (Blueprint declarativo,
tudo num arquivo versionado). Vale se preferir o deploy descrito em
código em vez de configurado no dashboard. O `Procfile` da raiz já
existe para esse caminho.

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
3. `git clone https://github.com/Dexi-Digital/core-engine-primor.git /opt/motor-central && cd /opt/motor-central`
4. `cp apps/api/.env.example .env.prod` e edita (paths relativos a `/opt/motor-central` daqui pra frente)
5. `docker compose -f infra/docker-compose.prod.yml --env-file .env.prod up -d`
6. Configura Caddy / Traefik na frente pro TLS automatico (HTTPS) -- este PR nao bundla reverse proxy; sem ele, `:8000` e `:3000` ficam expostos em HTTP puro

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
