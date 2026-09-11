# Deploy temporário no Railway (demo, sem VPS)

Publicação temporária do Motor Central **sem servidor próprio e sem adaptar
nada para serverless**. Substitui o roteiro da Vercel
(`docs/deployment-vercel.md`), que continua válido só como histórico.

## Por que Railway e não Vercel

A Vercel não roda processo longo. O Motor Central tem um worker Celery com
beat rodando 5 crons (pull OnSafety 07h30, alertas ASO 08h05, ponto 02h30,
etc.), então na Vercel **os crons ficavam pausados** e a API precisava ser
reescrita como função serverless — foi de onde veio o 500 de `sslmode` vs
`ssl`, o `/tmp` efêmero para anexos e o rate limit de login desligado.

O Railway roda os três `Dockerfile` que já existem no repo, como processos
normais. Os `railway.json` de cada app também já estão versionados.

**Railway não elimina custo, elimina servidor.** Verificado em 2026-09-09:
plano Free ($0, $1/mês de crédito), trial de $5 uma vez por 30 dias sem
cartão, Hobby a $5/mês com $5 de crédito incluso. **Serviço parado não é
cobrado** — dá para desligar entre demos.

> **Coolify não serve para este caso.** Mesmo o Coolify Cloud exige que você
> conecte servidores próprios via SSH — ele deixa um VPS parecido com a
> Vercel, mas não dispensa o VPS. É o candidato para quando a Primor tiver o
> servidor definitivo, não para a demo.

## Imagem única (1 serviço) — caminho da demo

`Dockerfile.allinone` + `infra/start-allinone.sh` põem **migrations, API e
web no mesmo container**. O `railway.json` da raiz aponta para ele, então o
serviço sobe **sem configurar Root Directory nem Config File**.

Funciona sem CORS e sem mudar código porque **o front é server-side**:
`src/lib/api.ts` usa cookie HTTP-only e roda em Server Components / Route
Handlers, e o navegador só chama rotas do próprio Next (`/m/api/...`). Quem
fala com a API é o Node, de dentro do container — por isso
`NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000` (inlined no build) resolve.

As migrations rodam no start, antes de qualquer processo aceitar tráfego, e
falha derruba o boot — foi o que faltava quando a API subiu com
`relation "auth_users" does not exist`.

**O que essa imagem NÃO tem: o worker Celery.** Os 5 crons não rodam; use os
disparos manuais e `POST /dp-sesmt/onsafety/pull?inline=true`. Para o VPS,
continue com `infra/docker-compose.prod.yml`, que separa api/workers/web.

**Variáveis mínimas:**

```ini
ENVIRONMENT=staging
SECRET_KEY=<openssl rand -hex 32>
DATABASE_URL=postgresql+asyncpg://...
ADMIN_EMAIL=...
ADMIN_PASSWORD=...
```

Se sobrou `PORT=8000` de uma configuração anterior, pode remover — a API agora
é interna nessa porta, e o start script desvia a web para 3000 se houver
colisão.

## Escopo mínimo vs completo (serviços separados)

| | Serviços | O que funciona |
|---|---|---|
| **Mínimo** | `api` + `web` (+ Neon) | Telas, login, CRUD, e os disparos manuais — inclusive `POST /dp-sesmt/onsafety/pull?inline=true`, que roda sem Celery |
| **Completo** | mínimo + `workers` + Redis | Tudo acima + os 5 crons e os endpoints que enfileiram |

A API **não importa Redis no boot** (`redis_url` é só default de config), então
o escopo mínimo sobe sem Redis nenhum.

## 1. Banco: continuar no Neon

O Neon já está provisionado e migrado. Não crie Postgres no Railway.

**A pegadinha que derrubou a API na Vercel não era da Vercel** e continua
valendo aqui: `asyncpg` não aceita `sslmode`, ele espera `ssl`. A string que o
painel do Neon mostra vem com `sslmode=require`.

| Uso | Driver | Parâmetro | Hostname |
|---|---|---|---|
| Runtime da API (`DATABASE_URL`) | asyncpg | `?ssl=require` | com `-pooler` |
| Alembic local (`resolve_sync_database_url`) | psycopg2 | `?sslmode=require` | com `-pooler` |

```ini
DATABASE_URL=postgresql+asyncpg://USER:PASS@ep-xxx-pooler.sa-east-1.aws.neon.tech/neondb?ssl=require
```

Copiar a string do Neon crua = 500 na subida.

## 2. Serviços no Railway

Projeto novo → **Deploy from GitHub repo** → `Dexi-Digital/core-engine-primor`.

**O serviço da API não precisa de nenhuma configuração.** O `railway.json` da
raiz é lido por padrão e já traz builder `DOCKERFILE`, o caminho do Dockerfile
e o `alembic upgrade head` no pre-deploy.

| Serviço | Root Directory | Railway Config File | Observação |
|---|---|---|---|
| `api` | *(nada)* | *(nada)* | usa o `railway.json` da raiz |
| `web` | `apps/web` | *(nada)* | Next.js standalone |
| `workers` | *(nada)* | `/apps/workers/railway.json` | **Nunca mais de 1 réplica** — o `-B` duplicaria os 5 crons |

Só a `web` precisa de Root Directory, porque é o único app cujo Dockerfile
espera o próprio diretório como contexto. `api` e `workers` buildam da raiz.

> **Se o log mostrar `╭─ Railpack ─╮`**, ele caiu no autodetect e vai falhar
> com *"could not determine how to build the app"* — a raiz do monorepo não é
> buildável sozinha. Quando estiver certo, o log começa com
> `FROM python:3.12-slim`. Atenção: o Railway deixa mudanças de Settings
> *staged* — é preciso aplicar pelo banner de deploy no topo da tela.

O `preDeployCommand` da API aborta o deploy se a migration falhar, sem virar o
tráfego para a versão nova. Por isso o `alembic heads` precisa ter **alvo
único** (hoje: `d0e1f2a3b4c5`); com duas cabeças o comando falha com
`Multiple head revisions` e nada sobe.

## 3. Variáveis

Todas as 73 settings têm default, então **nada impede o boot** — mas dois
guards em `app/modules/auth/startup.py` **abortam o startup de propósito** em
`ENVIRONMENT=production`:

1. **`SECRET_KEY` no default** → `InsecureProductionSecretError`. Qualquer um
   com acesso ao código forjaria tokens.
2. **`STORAGE_BACKEND=local` com path efêmero** (`/tmp`, `/var/tmp`,
   `/dev/shm`) → `InsecureProductionStorageError`. Anexos de licitação, ASO e
   XML fiscal sumiriam no primeiro restart do container.

O guard 2 é justamente o que o roteiro da Vercel fazia (`/tmp` da função).
Aqui use OneDrive de verdade — as credenciais do SharePoint foram entregues e
validadas ponta a ponta em 28/08/2026, e o `MS_GRAPH_DRIVE_ID` já está
resolvido.

### Serviço `api`

```ini
ENVIRONMENT=production
SECRET_KEY=<openssl rand -hex 32>
DATABASE_URL=postgresql+asyncpg://...-pooler.../neondb?ssl=require
PUBLIC_BASE_URL=https://<web>.up.railway.app
CORS_ORIGINS=["https://<web>.up.railway.app"]

# Storage -- NAO deixar local em producao (guard 2)
STORAGE_BACKEND=onedrive
MS_GRAPH_TENANT_ID=...
MS_GRAPH_CLIENT_ID=...
MS_GRAPH_CLIENT_SECRET=...        # expira em 28/08/2027
MS_GRAPH_DRIVE_ID=...

# Primeiro admin -- SEM ISSO NINGUEM LOGA (ver secao 4)
ADMIN_EMAIL=...
ADMIN_PASSWORD=...

# So no escopo completo
REDIS_URL=rediss://...
```

### Serviço `web`

```ini
NEXT_PUBLIC_API_BASE_URL=https://<api>.up.railway.app
```

### Serviço `workers` (escopo completo)

Mesmas variáveis da `api` (banco, storage, integrações) + `REDIS_URL`.

O worker builda da **raiz** (as tasks importam `from app.*`, inalcançável a
partir de `apps/workers`). Deixe o Root Directory vazio e aponte o **Railway
Config File** para `/apps/workers/railway.json` — sem isso ele usaria o
`railway.json` da raiz, que é o da API.

Até 2026-09-09 a imagem do worker era construída sem o pacote da API. Isso
**não quebrava**: as tasks capturam o `ImportError` e devolvem
`{"error": "API package not available in worker"}` — os 5 crons disparavam no
horário e não processavam nada, com o container de pé. Vale o alerta porque o
sintoma é ausência de resultado, não erro.

## 4. O erro que trava a demo em silêncio

`ensure_admin_seed` cria o primeiro admin a partir de `ADMIN_EMAIL` e
`ADMIN_PASSWORD`. **Sem essas duas variáveis o banco sobe sem nenhum usuário e
a tela de login não passa dali** — que foi exatamente onde a demo anterior
parou. A função é idempotente: pode rodar a cada boot.

## 5. Validar antes de mostrar para alguém

```bash
curl -sS https://<api>.up.railway.app/healthz     # liveness
curl -sS https://<api>.up.railway.app/readyz      # readiness (agrega dependencias)
```

`/healthz` responde sem tocar o banco; **`/readyz` é o que prova que a
`DATABASE_URL` está certa**. Só depois abra a URL da web e faça login de
verdade com o `ADMIN_EMAIL`. Não declare "no ar" com base na tela de login
aparecendo — ela aparece mesmo com a API morta.

## Limitações conhecidas do escopo mínimo

- Crons pausados (sem worker): use os disparos manuais na UI e
  `POST /dp-sesmt/onsafety/pull?inline=true`.
- Endpoints que enfileiram (OCR de parte diária, `/onsafety/pull` sem
  `inline`) respondem mas nada processa.
- Rate limit de login depende de Redis.
