# Integrações (adapters em `apps/api/app/integrations/`)

Todos os adapters implementam `IntegrationClient` (`app/integrations/base.py`).
Chaves de acesso vivem em variáveis de ambiente; **nunca no código**.

| Subpackage | Sistema | Tipo | Módulos consumidores |
|------------|---------|------|----------------------|
| `dominio`       | Domínio (Thomson Reuters)         | API          | A, C |
| `onvio`         | Onvio (admissão/contabilidade)    | API          | A |
| `onsafety`      | OnSafety (SST, EPIs)              | API          | A |
| `tangerino`     | Tangerino (ponto, fotos base)     | API          | A, E |
| `totvs`         | TOTVS (ERP financeiro)            | API / DB     | C |
| `sistema90`     | Sistema 90 (legado frota)         | API parcial  | B |
| `onedrive`      | OneDrive / SharePoint             | Graph API    | A, B, C |
| `easyjur`       | EasyJur (jurídico)                | API          | C |
| `solides`       | Sólides (R&S)                     | API          | A (avaliação) |
| `pncp`          | PNCP (Consulta v1 + Portal)       | API pública  | D |
| `comprasnet`    | ComprasNet (SIASG legacy)         | Scraping HTML| D (fallback) |
| `licitacoes_e`  | Licitações-e (Banco do Brasil)    | Scraping+SSO | D (fallback) |
| `conlicitacao`  | Conlicitação + Diários Oficiais   | Scraping     | D |
| `resend`        | Resend (email transacional)       | API          | D |
| `whatsapp`      | WhatsApp Business API             | API          | A, E |

## Padrões

- **Idempotência:** toda chamada `push` deve aceitar um `correlation_id`.
- **Timeouts + retries:** usar `tenacity` no worker; nunca retry dentro da API.
- **Rate limiting:** respeitar limites de cada provedor; usar Redis como
  token bucket global.
- **Scraping:** rodar headless via Playwright, com profile isolado por tenant.
  Sempre logar a URL/timestamp para rastreabilidade (LGPD).

## Credenciais esperadas (env vars)

A ser documentado conforme cada integração for implementada. Template em
`apps/api/.env.example`.

## PNCP (implementado)

Base URL: `https://pncp.gov.br/api/consulta` (configurável via `PNCP_BASE_URL`).
**Sem autenticação** — API pública, read-only.

Endpoint usado:
- `GET /v1/contratacoes/publicacao` — lista contratações publicadas em uma
  janela de datas. Requer `codigoModalidadeContratacao` (iteramos todas as
  modalidades conhecidas para cobrir a janela inteira).

Como disparar uma ingestão manual:

```bash
# via API (pequeno):
curl -X POST "http://localhost:8000/api/v1/licitacoes/ingest/pncp?uf=SP&max_paginas=2"

# via Celery (janela grande, agendada):
celery -A worker.main call worker.tasks.licitacoes.crawler_pncp \
  --args='["2025-04-01","2025-04-07","SP",null]'
```

Idempotência: rows são upsertadas por `external_id = cnpj-ano-sequencial`
(`ON CONFLICT` no Postgres). Rodar o mesmo comando N vezes não duplica dados.

### Download de editais (D.4)

Além da Consulta, o adapter usa o **Portal PNCP** para listar e baixar os
arquivos publicados por licitação. Pattern confirmado em 2026-04:

- `GET https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/arquivos`
  → lista JSON `[{ sequencialDocumento, titulo, tipoDocumentoDescricao, url, ... }]`
- `GET {url}` → PDF bruto com `Content-Disposition: attachment; filename="..."`

A base do Portal é configurável via `PNCP_PORTAL_BASE_URL` (default
`https://pncp.gov.br/api/pncp`). Arquivos baixados vão para
`editais_storage_path` (`/tmp/motor-central/editais` em dev) via
`LocalStorage`; troque por um backend MinIO/S3 em produção implementando
o `Protocol` `EditaisStorage` em `app/modules/licitacoes/storage.py`.

## ComprasNet / SIASG legacy (scaffold — fallback)

Adapter: `app.integrations.comprasnet.client.ComprasnetClient`.
Base URL: `https://comprasnet.gov.br`.

A página `/ConsultaLicitacoes/download/download_editais_detalhe.asp?coduasg=...&modprp=...&numprp=...`
é HTML puro e pode ser lida sem captcha. Já o download do PDF em si
(`/ConsultaLicitacoes/Download/Download.asp`) está protegido por
captcha javascript (função `ValidaCodigo()`), portanto o adapter levanta
`ComprasnetCaptchaRequired` quando chamado para baixar o PDF. A
estratégia atual é **cair no PNCP primeiro** (ver D.4 acima) e deixar
este cliente para metadata e para uma próxima iteração com Playwright
ou solver humano-no-loop.

## Licitações-e Banco do Brasil (scaffold — fallback)

Adapter: `app.integrations.licitacoes_e.client.LicitacoesEClient`.
Base URL: `https://www.licitacoes-e.com.br`.

Portal ASP.NET pesado em JS, com sessão autenticada (CPF + senha de um
fornecedor cadastrado no BB) para acessar o download dos editais não-públicos.
Este adapter é um **esqueleto**: o construtor aceita `cpf`/`senha`
prevendo um flow Playwright futuro, mas qualquer operação real levanta
`LicitacoesECredentialsRequired`. Callers devem fazer fallback para
PNCP enquanto o flow não estiver implementado.

## Resend (implementado)

Base URL: `https://api.resend.com`. Usado pelo Módulo D para disparar
**boletins por email 3x/dia** (ver `app/modules/licitacoes/boletins.py`).

Env vars:

| Variável              | Obrigatória | Descrição                                    |
|-----------------------|-------------|----------------------------------------------|
| `RESEND_API_KEY`      | sim         | Chave `re_…` emitida em https://resend.com/api-keys |
| `RESEND_FROM_EMAIL`   | não         | Default: `Motor Central <boletins@motorcentral.dev>` (domínio precisa estar verificado na Resend). |
| `PUBLIC_BASE_URL`     | não         | Default: `http://localhost:3000`. URL pública do dashboard (usada no link "Abrir no Motor Central" no digest). |

Endpoint usado:
- `POST /emails` — envia um email transacional (`from`, `to[]`, `subject`, `html`).

O cliente faz retry com backoff exponencial em 429/5xx (tenacity, 4 tentativas,
max 8s) e levanta `ResendError` em 4xx não-recuperáveis (ex: domínio não
verificado). Chamada feita dentro de `ResendClient.send_email(...)`.

Cadência: `worker/main.py` configura `celery_app.conf.beat_schedule` para
disparar `worker.tasks.licitacoes.dispatch_boletins` às **07h, 13h, 19h**
(America/Sao_Paulo).
