# Deploy temporário na Vercel (demo, até o servidor definitivo do cliente)

Arquitetura 100% gratuita para demonstrar a integração OnSafety (e o
restante do Motor Central) sem depender de infra própria. **Não roda
Celery worker/beat** — crons ficam pausados; use os endpoints de
disparo manual (`POST /dp-sesmt/onsafety/pull`,
`POST /dp-sesmt/employees/{id}/sync-onsafety`) e os botões
correspondentes na UI.

| Peça | Serviço | Free tier |
|---|---|---|
| `apps/web` (Next.js) | Vercel | Sim |
| `apps/api` (FastAPI serverless) | Vercel (`@vercel/python`) | Sim |
| Postgres | [Neon](https://neon.tech) | Sim (com *pooled connection*) |
| Redis | Nenhum — rate limit de login desativado | — |
| Storage de anexos (EPI/ASO) | Local efêmero (`/tmp` da função) | Sim, mas **não persiste** entre invocações/deploys |

## 1. Criar o banco (Neon)

1. Criar conta grátis em neon.tech, criar um projeto/database `primor`.
2. Copiar a **pooled connection string** (Neon oferece duas: direta e
   pooled via PgBouncer — usar a **pooled**, é a que funciona bem com
   serverless). Formato:
   `postgresql://user:pass@ep-xxx-pooler.region.aws.neon.tech/primor?sslmode=require`
3. Converter para o driver async que a API usa: trocar `postgresql://`
   por `postgresql+asyncpg://` no início da string.

## 2. Rodar migrations + seed (uma vez, antes do primeiro deploy)

Não depende da Vercel — pode ser feito de qualquer máquina com acesso
à internet:

```bash
cd apps/api
export DATABASE_URL="postgresql+asyncpg://...<a string do Neon, convertida>..."
uv run --extra dev --with greenlet alembic upgrade head
uv run --extra dev --with greenlet python -m scripts.seed_dossie   # opcional: dados de demo
```

## 3. Deploy da API na Vercel

1. Criar projeto na Vercel, importar o repo do GitHub.
2. **Root Directory:** `apps/api`.
3. Framework preset: "Other" (o `apps/api/vercel.json` já define o
   build Python via `api/index.py`).
4. Variáveis de ambiente (Settings → Environment Variables):

   | Variável | Valor |
   |---|---|
   | `DATABASE_URL` | pooled string do Neon (passo 1), com `+asyncpg` |
   | `ENVIRONMENT` | `staging` (evita os guards de produção do repo — ver nota abaixo) |
   | `SECRET_KEY` | gerar com `openssl rand -hex 32` |
   | `ADMIN_EMAIL` / `ADMIN_PASSWORD` | credenciais do admin de demo |
   | `CORS_ORIGINS` | `["https://<url-do-projeto-web>.vercel.app"]` |
   | `LOGIN_RATE_LIMIT_ENABLED` | `0` (sem Redis neste deploy) |
   | `ONSAFETY_TOKEN` | token de homologação |
   | `ONSAFETY_BASE_URL` | `https://api.dev.onsafety.com.br` |
   | `ONSAFETY_PROJETO_ID` | `fd68067b-7ed6-11f1-b7b9-82fc3a8f4fb4` (obra de teste criada no smoke) |
   | `ONSAFETY_ALLOW_PROD_WRITE` | deixar **sem setar** (guard de produção continua ativo) |
   | `STORAGE_BACKEND` | `local` (aceitar que anexos são efêmeros nesta demo) |
   | `EDITAIS_STORAGE_PATH` | `/tmp/motor-central/editais` |

5. Deploy. A URL final fica algo como `https://<projeto>.vercel.app`.

**Nota sobre `ENVIRONMENT`:** o startup da API (`app/modules/auth/startup.py`)
bloqueia subir com `SECRET_KEY` default ou storage local em path efêmero
quando `ENVIRONMENT` é `production`/`prod`. Como este deploy é
temporário, `staging` evita esses guards continuando com `SECRET_KEY`
real de qualquer forma (best practice, não é bloqueado só por estar
em staging).

## 4. Deploy do Web na Vercel

1. Novo projeto Vercel, mesmo repo, **Root Directory:** `apps/web`.
2. Framework preset: Next.js (detectado automaticamente).
3. Variável de ambiente: `NEXT_PUBLIC_API_BASE_URL` = URL do projeto
   da API (passo 3.5).
4. Deploy.

## Limitações conhecidas deste deploy de demo

- **Sem cron/worker**: alertas de ASO, afastamento, pull automático da
  OnSafety (07h30) e boletins de licitação não disparam sozinhos — use
  os endpoints/botões de disparo manual.
- **Anexos não persistem**: upload de PDF/foto (ASO, EPI, editais)
  desaparece no próximo cold start/deploy — armazenamento é
  `/tmp` da função serverless.
- **`ONSAFETY_ALLOW_PROD_WRITE` não deve ser setado** neste ambiente —
  o guard de escrita em produção do adapter (ver
  `docs/integrations.md`) deve continuar ativo enquanto o token
  disponível não for rotacionado para um token dedicado de homologação
  de longo prazo.
- Features que disparam Celery diretamente (ex.: OCR de parte diária
  via `manutencao_frota`) vão falhar nesta demo — não há broker.
