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

## OneDrive / Microsoft Graph (implementado)

Adapter: `app/integrations/onedrive/client.py` (`OneDriveClient` real
com OAuth2 client_credentials + `OneDriveMockClient` determinístico).
Bridge para o `EditaisStorage` protocol em
`app/integrations/onedrive/storage.py` (`OneDriveStorage`).

**Casos de uso atuais:**
- D.4 — anexos de edital (storage backend alternativo ao
  `LocalStorage` quando `STORAGE_BACKEND=onedrive`).
- Próximos: ASOs digitalizados, contratos, documentos do funcionário
  (Módulo A).

**Como ligar credenciais reais:**

1. No Azure AD, criar um **App Registration** (single tenant).
2. Adicionar permissão de aplicação **`Files.ReadWrite.All`**
   (ou `Sites.ReadWrite.All` se a raiz for SharePoint) e
   conceder admin consent.
3. Gerar um client secret e copiar `tenant_id`, `client_id`,
   `client_secret`.
4. Pegar o `drive_id` do drive alvo via `GET /me/drive` (com login
   delegado uma vez) ou `GET /sites/{id}/drives` (SharePoint).
5. Setar no `.env`:

```
STORAGE_BACKEND=onedrive
MS_GRAPH_TENANT_ID=...
MS_GRAPH_CLIENT_ID=...
MS_GRAPH_CLIENT_SECRET=...
MS_GRAPH_DRIVE_ID=b!...
MS_GRAPH_ROOT_FOLDER=MotorCentral/editais   # opcional
```

Sem essas 4 variáveis (ou com `STORAGE_BACKEND=local`), os anexos
continuam indo para `editais_storage_path`. Quando todas as 4 estão
presentes E `STORAGE_BACKEND=onedrive`, o cliente real entra no lugar.
Se as 4 faltarem mas `STORAGE_BACKEND=onedrive` estiver setado, o
adapter cai no `OneDriveMockClient` (útil para desenvolvimento sem
Azure AD — itens viram `mock-<sha>` e bytes ficam em memória).

**Endpoints Graph que usamos:**

| Método | Path                                                    | Quando            |
|--------|---------------------------------------------------------|-------------------|
| POST   | `/oauth2/v2.0/token`                                    | autenticação      |
| GET    | `/drives/{drive_id}/root`                               | health check      |
| PUT    | `/drives/{drive_id}/items/root:/{path}:/content`        | upload < 4 MiB    |
| POST   | `/drives/{drive_id}/items/root:/{path}:/createUploadSession` | upload >= 4 MiB |
| GET    | `/drives/{drive_id}/items/{id}/content`                 | download          |
| DELETE | `/drives/{drive_id}/items/{id}`                         | remoção           |



## Domínio Sistemas — Central do Desenvolvedor (implementado, Módulo C)

Adapter: `app/integrations/dominio/client.py` (`DominioClient` real
com OAuth2 client_credentials + multipart upload, `DominioMockClient`
determinístico para dev/CI).

**Caso de uso:** envio automático de XMLs fiscais ao escritório
contábil parceiro (NF-e, NFC-e, NFS-e, CT-e, CF-e, Baixa de Parcela)
via API Domínio, eliminando a necessidade do cliente do ERP enviar
manualmente os documentos para a Contabilidade.

**Tipos suportados** (todos os do leiaute oficial Domínio):

| Tipo | Versão | Origem do leiaute |
|------|--------|-------------------|
| `nfe`   | 4.00 | Portal NFe (`<infNFe>`)            |
| `nfce`  | 4.00 | Portal NFe modelo 65 (`<mod>65</mod>`) |
| `nfse`  | ABRASF 1.0 / Nacional | Prefeitura emissora |
| `cte`   | 3.00 | Portal CT-e (`<infCte>`)           |
| `cfe`   | 0.07/0.08 | SEFAZ-SP SAT (`<infCFe>`)     |
| `baixa` | Domínio  | Manual de Baixas Domínio       |

**Como ligar credenciais reais:**

1. Cadastro de parceiro na **Central do Desenvolvedor** Domínio:
   <https://www.dominiosistemas.com.br/lp-centraldodesenvolvedor-api/>
2. A Domínio gera `client_id` e `client_secret` para o parceiro.
3. O escritório contábil cliente fornece `audit_url` + `integracao`
   (identificadores do tenant Domínio onde os XMLs vão parar).
4. Setar no `.env`:

```
DOMINIO_AUDIT_URL=https://audit.contabilidadexyz.com.br
DOMINIO_INTEGRACAO=identificador-tenant
DOMINIO_CLIENT_ID=...
DOMINIO_CLIENT_SECRET=...
DOMINIO_BASE_URL=https://api.dominioexterior.com.br/api/v1   # ajustar p/ homol
```

Sem as 4 variáveis preenchidas, o adapter cai no `DominioMockClient`
(útil para dev/CI — protocolos viram `MOCK-<TIPO>-<sha>-<seq>` e
nenhum tráfego de rede sai da máquina).

**Endpoints da API Domínio que usamos:**

| Método | Path                                  | Quando             |
|--------|---------------------------------------|--------------------|
| POST   | `/token`                              | autenticação OAuth2 |
| POST   | `/upload` (multipart `arquivo`)       | envio do XML       |

**Tratamento de erros:**

- `httpx.HTTPError` (timeout, DNS, conexão) → `DominioError` (NUNCA
  escapa do adapter — o serviço captura e marca `status_envio="erro"`).
- HTTP 401/403 no `/token` ou `/upload` → `DominioAuthError`
  (separado de `DominioError` porque auth não é transitório — não
  retentamos, precisa rotar credenciais).
- Token cached em memória com TTL (renova 5 min antes do expiry) para
  não sobrecarregar o `/token` que é rate-limitado.

---

## Infosimples — Consultas Detran (Módulo B.3)

**Adapter:** `apps/api/app/integrations/infosimples/client.py`
**Service:** `apps/api/app/modules/manutencao_frota/service.py::consultar_detran`
**Worker:** `apps/workers/worker/tasks/manutencao.py::consulta_detran`

API agregadora que cobre os Detrans estaduais sem precisar de credencial
de despachante. Cada consulta retorna multas, IPVA, licenciamento, dados
do veículo e restrições — normalizados pelo adapter num único schema
para SP, MG e GO.

| UF  | Endpoint Infosimples                                |
|-----|-----------------------------------------------------|
| SP  | `POST /api/v2/consultas/detran/sp/veiculo`          |
| MG  | `POST /api/v2/consultas/detran/mg/veiculo`          |
| GO  | `POST /api/v2/consultas/detran/go/veiculo`          |

**Schema normalizado (mesmo para SP/MG/GO):**

```json
{
  "uf": "SP",
  "placa": "ABC1234",
  "renavam": "12345678900",
  "chassi": "9BW...",
  "marca_modelo": "VW/CONSTELLATION",
  "ano_modelo": 2021,
  "cor": "BRANCA",
  "combustivel": "DIESEL",
  "situacao": "REGULAR",
  "licenciamento": { "exercicio": 2025, "vencimento": "2025-09-30",
                      "pago": true, "valor": "163.42" },
  "ipva":          { "exercicio": 2025, "vencimento": "2025-04-30",
                      "pago": false, "valor": "1234.56" },
  "multas":        [{ "auto": "AIT-X1", "data": "...", "valor": "...",
                      "descricao": "..." }],
  "restricoes":    ["ALIENACAO FIDUCIARIA"],
  "raw":           { ... },
  "source":        "infosimples"
}
```

**Como ligar credenciais reais:**

1. Cadastro em <https://infosimples.com> e ativação do produto Detran.
2. Setar no `.env`:

```
INFOSIMPLES_TOKEN=seu-token-aqui
INFOSIMPLES_BASE_URL=https://api.infosimples.com   # default
```

Sem `INFOSIMPLES_TOKEN`, o adapter cai no `InfosimplesMockClient`
determinístico (resposta varia por `placa+UF` mas é estável entre
chamadas — útil para dev/CI/screenshots).

**Persistência (B.3):**

- Cada consulta gera 1 row em `frota_consultas_detran` (`id`,
  `veiculo_id`, `placa`, `uf`, `status`, `source`, `payload` JSON,
  `error_msg`, `executed_at`). Tabela é append-only — re-consulta da
  mesma placa em datas distintas vira histórico, não overwrite.
- Quando `payload.ipva.vencimento` ou `payload.licenciamento.vencimento`
  vêm preenchidos, o serviço materializa rows em `frota_documentos`
  com `source="detran_rpa"`. Documentos manuais (`source="manual"`)
  ficam intactos — auditor distingue origem na coluna.
- Multas não viram documentos individuais (são N por veículo e não têm
  noção de "validade") — ficam dentro do `payload` para a UI renderizar
  como tabela embutida.
- Audit log em todas as mutações: `manutencao_frota.consulta_detran`
  com `action="create"` (sucesso) ou `action="error"` (falha de
  upstream).

**Tratamento de erros:**

- O service `consultar_detran` **nunca** propaga exceções para o
  router — qualquer falha (timeout, 5xx, formato inesperado) vira
  uma row `status="erro"` com `error_msg` truncado em 500 chars. A UI
  renderiza o erro inline na lista de consultas, e a placa fica
  disponível para retry sem bloquear o usuário.
- O endpoint `POST /veiculos/{id}/consultar-detran` devolve 201
  mesmo em caso de erro de upstream (com payload da row `status=erro`),
  e 422 só para UF não suportada / 404 para veículo inexistente.
