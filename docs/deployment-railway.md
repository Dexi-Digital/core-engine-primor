# Deploy temporário no Railway (demo, sem VPS)

Publicação temporária do Motor Central **sem servidor próprio e sem adaptar
nada para serverless**. Substitui o roteiro da Vercel
(`docs/deployment-vercel.md`), que continua válido só como histórico.

## Por que Railway e não Vercel

A Vercel não roda processo longo. O Motor Central tem um worker Celery com
beat embutido rodando 15 agendamentos (lista completa na seção
[Agendamentos do beat](#agendamentos-do-beat)), então na Vercel **os crons
ficavam pausados** e a API precisava ser
reescrita como função serverless — foi de onde veio o 500 de `sslmode` vs
`ssl`, o `/tmp` efêmero para anexos e o rate limit de login desligado.

O Railway roda os `Dockerfile` que já existem no repo, como processos
normais. **Quais `railway.json` existem, e o que cada um builda** (conferido
nos arquivos, não na memória):

| Arquivo | Builda | Sobe | Migrations |
|---|---|---|---|
| `railway.json` (raiz) | `Dockerfile.allinone` | API + web no mesmo container (`start-allinone.sh`) | **sim**, no start do container |
| `apps/workers/railway.json` | `apps/workers/Dockerfile` | worker Celery com beat embutido (`-B`), `numReplicas: 1` | não |
| `apps/web/railway.json` | `apps/web/Dockerfile` (Root Directory `apps/web`) | só o Next (`node server.js`) | não |

**Não existe `railway.json` para uma API isolada.** O `apps/api/Dockerfile`
existe (é o que o `infra/docker-compose.prod.yml` usa), mas o `CMD` dele é só
`uvicorn` — sem migration. Nenhum arquivo versionado configura
`preDeployCommand`.

**Railway não elimina custo, elimina servidor.** Verificado em 2026-09-09:
plano Free ($0, $1/mês de crédito), trial de $5 uma vez por 30 dias sem
cartão, Hobby a $5/mês com $5 de crédito incluso. **Serviço parado não é
cobrado** — dá para desligar entre demos.

> **Coolify não serve para este caso.** Mesmo o Coolify Cloud exige que você
> conecte servidores próprios via SSH — ele deixa um VPS parecido com a
> Vercel, mas não dispensa o VPS. É o candidato para quando a Primor tiver o
> servidor definitivo, não para a demo.

## Imagem única (1 serviço) — caminho da demo

`Dockerfile.allinone` + `start-allinone.sh` (na raiz) põem **migrations, API e
web no mesmo container**. O `railway.json` da raiz aponta para ele, então o
serviço sobe **sem configurar Root Directory nem Config File**.

Funciona sem CORS e sem mudar código porque **o front é server-side**:
`src/lib/api.ts` usa cookie HTTP-only e roda em Server Components / Route
Handlers, e o navegador só chama rotas do próprio Next (`/m/api/...`). Quem
fala com a API é o Node, de dentro do container — por isso
`NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000` (inlined no build) resolve.

> **Por que o script fica na raiz, e não em `infra/`:** `infra` é ignorado no
> contexto de build. Reincluí-lo com `!infra/start-allinone.sh` parecia
> funcionar — mas em 11/09/2026 o build serviu uma versão **defasada** do
> script: o bloco novo simplesmente não executava, sem erro nenhum.
> Reinclusão de arquivo dentro de diretório excluído não é confiável.

As migrations rodam no start, antes de qualquer processo aceitar tráfego, e
falha derruba o boot — foi o que faltava quando a API subiu com
`relation "auth_users" does not exist`.

**O que essa imagem NÃO tem: o worker Celery.** Nenhum dos 15 agendamentos
do beat roda, e o que é enfileirado (inclusive "Sincronizar agora" do
Jurídico) fica parado; use os disparos manuais e
`POST /dp-sesmt/onsafety/pull?inline=true`. Para o worker, suba um segundo
serviço com `apps/workers/railway.json` (seção 2). Para o VPS, continue com
`infra/docker-compose.prod.yml`, que separa api/workers/web.

A API dessa imagem escuta em `127.0.0.1:8000`, **dentro** do container: só a
web fica exposta. Os `curl` da seção 5 contra `<api>.up.railway.app` valem
para a API em serviço próprio, não para esta imagem.

**Variáveis mínimas (demo):**

```ini
ENVIRONMENT=staging
SECRET_KEY=<openssl rand -hex 32>
DATABASE_URL=postgresql+asyncpg://...
ADMIN_EMAIL=...
ADMIN_PASSWORD=...
```

> **`ENVIRONMENT=staging` desliga as proteções de produção.** Os dois guards
> de `app/modules/auth/startup.py` (seção 3) só agem com
> `ENVIRONMENT=production` (ou `prod`): em `staging` a API sobe com
> `SECRET_KEY` de dev e com anexos em `/tmp` sem reclamar, e o envio fiscal
> em modo mock responde "enviado" sem enviar nada
> (`allow_mock_send=settings.environment != "production"` em
> `app/modules/fiscal/service.py`). Serve para demo com dado fictício.
> **Com dado real da Primor, use `ENVIRONMENT=production`** e as variáveis da
> seção 3.

Se sobrou `PORT=8000` de uma configuração anterior, pode remover — a API agora
é interna nessa porta, e o start script desvia a web para 3000 se houver
colisão.

## Escopo mínimo vs completo (serviços separados)

| | Serviços | O que funciona |
|---|---|---|
| **Mínimo** | `api` + `web` (+ Neon) | Telas, login, CRUD, e os disparos manuais — inclusive `POST /dp-sesmt/onsafety/pull?inline=true`, que roda sem Celery |
| **Completo** | mínimo + `workers` + Redis | Tudo acima + os 15 agendamentos do beat e os endpoints que enfileiram |

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

Um serviço sem Root Directory e sem Railway Config File lê o `railway.json`
da raiz — que é o **all-in-one** (migrations + API + web), não uma API
isolada.

| Serviço | Root Directory | Railway Config File | Observação |
|---|---|---|---|
| `app` (demo) | *(nada)* | *(nada)* | `railway.json` da raiz → `Dockerfile.allinone`; roda as migrations no start |
| `workers` | *(nada)* | `/apps/workers/railway.json` | **Nunca mais de 1 réplica** — o `-B` duplicaria todos os agendamentos |
| `web` (só se separar) | `apps/web` | *(nada)* | Next.js standalone; usa `apps/web/railway.json` |

A `web` precisa de Root Directory porque é o único app cujo Dockerfile espera
o próprio diretório como contexto. `workers` (e a API isolada, se existir)
buildam da raiz.

**API em serviço próprio** (em vez do all-in-one): não há config versionada
para isso. No painel do serviço, aponte o Dockerfile para `apps/api/Dockerfile`
e configure você mesmo um pre-deploy command `alembic upgrade head` — **sem
isso nenhuma migration roda**, porque o `CMD` dessa imagem é só `uvicorn`. E
não rode o all-in-one em paralelo contra o mesmo banco sem necessidade: são
dois lugares aplicando migration.

> **Se o log mostrar `╭─ Railpack ─╮`**, ele caiu no autodetect e vai falhar
> com *"could not determine how to build the app"* — a raiz do monorepo não é
> buildável sozinha. Quando estiver certo, o log começa com
> `FROM python:3.12-slim`. Atenção: o Railway deixa mudanças de Settings
> *staged* — é preciso aplicar pelo banner de deploy no topo da tela.

**Migration com alvo único.** Onde quer que ela rode (start do all-in-one ou
pre-deploy de uma API isolada), `alembic upgrade head` falha com
`Multiple head revisions` se houver duas cabeças — e, no all-in-one, o
`set -e` do `start-allinone.sh` derruba o container. Antes de deployar:

```bash
cd apps/api && alembic heads   # tem de listar UMA revisão
```

Não fixe o hash da cabeça nesta doc: ele muda a cada migration nova e o texto
envelhece calado.

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
**`SECRET_KEY` igual à da API**: além dos tokens, ela assina a impressão da
credencial do EasyJur usada na trava de login (ver `docs/integrations.md`);
chaves diferentes fazem API e worker discordarem sobre a trava.

O worker builda da **raiz** (as tasks importam `from app.*`, inalcançável a
partir de `apps/workers`). Deixe o Root Directory vazio e aponte o **Railway
Config File** para `/apps/workers/railway.json` — sem isso ele usaria o
`railway.json` da raiz, que é o **all-in-one** (API + web), e o serviço
subiria uma segunda cópia do site em vez do worker.

O que `apps/workers/Dockerfile` fixa no `CMD`: beat embutido (`-B`),
`--concurrency=2` (sem isso o Celery forka um processo por CPU da máquina e o
container morre por memória) e as filas
`default,dp_sesmt,manutencao,financeiro,licitacoes,ia`. O `numReplicas: 1` do
`railway.json` não é sugestão: com 2 réplicas, cada agendamento dispara duas
vezes.

**Não defina `DB_NULLPOOL` no serviço.** O próprio `worker/main.py` faz
`os.environ.setdefault("DB_NULLPOOL", "1")` antes de importar `app.core.db`,
porque cada task roda com `asyncio.run` (event loop novo) e conexão de pool
não sobrevive à troca de loop. Uma variável `DB_NULLPOOL` vazia no painel
vence o `setdefault` e religa o pool — `app/core/db.py` testa o valor, e
string vazia conta como desligado.

Até 2026-09-09 a imagem do worker era construída sem o pacote da API. Isso
**não quebrava**: as tasks capturam o `ImportError` e devolvem
`{"error": "API package not available in worker"}` — os crons disparavam no
horário e não processavam nada, com o container de pé. Vale o alerta porque o
sintoma é ausência de resultado, não erro.

### Agendamentos do beat

Fonte: `celery_app.conf.beat_schedule` em `apps/workers/worker/main.py`
(horário `America/Sao_Paulo`). São **15 entradas**:

| Entrada | Task | Quando |
|---|---|---|
| `ponto-pull-solides` | `dp_sesmt.pull_ponto` | diário 02h30 |
| `totvs-pull-lancamentos` | `financeiro.pull_totvs` | diário 03h00 |
| `easyjur-pull-daily` | `juridico.pull_easyjur` | diário 03h30 |
| `licitacoes-resultados-semanal` | `licitacoes.ingest_resultados` | segunda 04h00 |
| `licitacoes-atas-semanal` | `licitacoes.ingest_atas` | segunda 04h30 |
| `pncp-crawler-daily` | `licitacoes.crawler_pncp` | diário 05h00 |
| `onedrive-diagnostico-weekly` | `onedrive_diagnostico.run_diagnostico` | segunda 05h00 |
| `boletins-morning` | `licitacoes.dispatch_boletins` | diário 07h00 |
| `onsafety-pull-daily` | `dp_sesmt.pull_onsafety` | diário 07h30 |
| `certidao-alerts-daily` | `licitacoes.dispatch_certidao_alerts` | diário 08h00 |
| `aso-alerts-daily` | `dp_sesmt.dispatch_aso_alerts` | diário 08h05 |
| `afastamento-alerts-daily` | `dp_sesmt.dispatch_afastamento_alerts` | diário 08h10 |
| `contrato-alerts-daily` | `financeiro.dispatch_contrato_alerts` | diário 08h15 |
| `boletins-midday` | `licitacoes.dispatch_boletins` | diário 13h00 |
| `boletins-evening` | `licitacoes.dispatch_boletins` | diário 19h00 |

(Tasks abreviadas: o nome completo começa com `worker.tasks.`.) Para conferir
no código em vez de confiar nesta tabela:

```bash
cd apps/workers && PYTHONPATH=.:../api python -c \
  "from worker.main import celery_app as c; [print(k, v['schedule']) for k, v in c.conf.beat_schedule.items()]"
```

### Redeploy: mudou `apps/api`, redeploya a API **e** o worker

A imagem do worker carrega uma cópia do pacote da API (`COPY apps/api/app` no
`apps/workers/Dockerfile`). Qualquer alteração em `apps/api` exige redeploy
dos **dois** serviços (all-in-one/API e `workers`); senão o worker segue
rodando a regra antiga, sem erro nenhum — já aconteceu (PR #67, 16/09/2026).
Mudança só em `apps/web` exige só a web (ou o all-in-one); só em
`apps/workers`, só o worker.

Para conferir a defasagem depois do deploy:

```bash
curl -sS https://<api>/api/v1/observability/versao
# {"api_fingerprint": "...", "worker_fingerprint": "...",
#  "worker_boot_em": "...", "em_sincronia": true}
```

`api_fingerprint` é o hash do código `app/**/*.py` da instância que
respondeu; `worker_fingerprint` é o que o worker gravou no `audit_log` no
último boot. `em_sincronia: false` = um dos dois não foi redeployado.
`em_sincronia: null` = o worker nunca registrou boot (não subiu, ou não
alcança o banco) — não é "em dia". No all-in-one a API não é pública:
chame essa rota de dentro do container
(`curl -sS http://127.0.0.1:8000/api/v1/observability/versao`).

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
