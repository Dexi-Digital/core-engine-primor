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
| `llm/anthropic` | Anthropic Messages (tool_use)     | API          | D (análise edital) |
| `llm/openai`    | OpenAI Chat Completions (json_schema) | API      | D (análise edital) |
| `whatsapp`      | WhatsApp Business API             | API          | A, E |
| `viacep`        | ViaCEP (endereço por CEP)         | API pública  | A (dossiê de admissão) |
| `brasilapi`     | BrasilAPI (CNPJ via Receita)      | API pública  | A (dossiê de admissão) |
| `directdata`    | DirectData (consulta CPF)         | API paga     | A (dossiê de admissão) |

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

## LLM providers — análise de edital (D.5)

Os adapters em `app/integrations/llm/` implementam um Protocol
`LLMProvider` com um único método `analyze(text, schema, system_prompt)`
que devolve um `LLMResult` (`data` = JSON extraído, `cost_usd`, tokens,
modelo). `CostRoutedProvider` encapsula N providers, ordena por
`input_price_per_mtok` e tenta o mais barato primeiro; em `LLMError`
faz fallback para o próximo. Se um provider levantar
`LLMUnavailableError` (chave ausente), ele é simplesmente pulado.

Env vars:

| Variável              | Obrigatória | Descrição                                    |
|-----------------------|-------------|----------------------------------------------|
| `ANTHROPIC_API_KEY`   | opcional    | Chave `sk-ant-…`. Se ausente, o provider é pulado. |
| `ANTHROPIC_MODEL`     | opcional    | Default: `claude-3-5-haiku-20241022`         |
| `OPENAI_API_KEY`      | opcional    | Chave `sk-…`. Se ausente, o provider é pulado. |
| `OPENAI_MODEL`        | opcional    | Default: `gpt-4.1-nano`                      |

Pelo menos **uma** das chaves precisa estar configurada; caso contrário
o endpoint `POST /api/v1/licitacoes/{id}/edital/analise` responde
`503 Service Unavailable`.

Extração estruturada:

- **Anthropic**: usa `tool_use` com uma tool `extract_edital` cuja
  `input_schema` é o schema de saída. Claude é forçado a responder com
  um `tool_use` block preenchendo o schema.
- **OpenAI**: usa `response_format.json_schema` com `strict=true`, que
  força o output a casar exatamente com o schema declarado.

Os PDFs baixados em D.4 são concatenados via `pypdf` em um único prompt
(anexos não-PDF, acima de 25 MiB, ou com erro de leitura são pulados e
reportados em `analise.data.anexos`). Truncamos em 400k caracteres
antes de enviar ao modelo para conter custo.

## ViaCEP / BrasilAPI / DirectData (Modulo A — dossie de admissao)

Tres adapters chamados durante o cadastro manual de funcionarios em
`/rh/funcionarios` para enriquecer o dossie:

- **ViaCEP** (`https://viacep.com.br/ws/{cep}/json/`) — auto-completa
  endereco quando o usuario digita o CEP. Publico, sem autenticacao.
- **BrasilAPI** (`https://brasilapi.com.br/api/cnpj/v1/{cnpj}`) — valida
  CNPJ de empregador anterior e expande a razao social. Publico.
- **DirectData** (`https://apiv3.directd.com.br/api/v1/consultas/cadastro_pessoa`)
  — consulta paga de CPF (nome, data de nascimento, situacao
  cadastral). Quando `DIRECTDATA_API_KEY` esta vazia, o adapter opera
  em modo *mock* (retorna struct deterministico), permitindo
  desenvolvimento sem custo. UI marca o resultado com `source =
  "directdata_mock"`.

Toda consulta a essas APIs grava uma linha em `dp_dossie_consultas`
(LGPD): quem foi consultado, quando, qual fonte, sucesso/erro. Util
tambem para rastrear o custo do DirectData por funcionario quando o
plano for contratado.

Env vars:

| Variável              | Obrigatória | Descrição                                   |
|-----------------------|-------------|---------------------------------------------|
| `DIRECTDATA_API_KEY`  | opcional    | Sem chave, o adapter usa mock determinístico. |
