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

## OnSafety — SST: ASOs, EPIs, treinamentos (Módulo A, em implementação)

**Adapter:** `app/integrations/onsafety/client.py` (`OnsafetyClient` com
modo mock determinístico embutido — padrão Infosimples).
**Branch:** `feat/integracao-onsafety` — ver
`docs/adr/adr-001-onsafety-squads.md` para a divisão de squads.

API REST/JSON estilo Spring Data, auth por API key no header `token`.
Swagger: <https://api.dev.onsafety.com.br/swagger-ui/>.

| Ambiente     | Base URL                        |
|--------------|---------------------------------|
| Homologação  | `https://api.dev.onsafety.com.br` (default) |
| Produção     | `https://api.onsafety.com.br`   |

**Tokens não são intercambiáveis entre ambientes** — token de produção
contra `api.dev.*` devolve 401 ("Usuário ou senha incorretos"), o que
confunde o diagnóstico. O `OnsafetyAuthError` do adapter menciona isso.

Endpoints usados:

| Método | Path                                           | Caso de uso        |
|--------|------------------------------------------------|--------------------|
| GET    | `/v2/trabalhadores`                            | pull cadastro      |
| GET    | `/v2/exames_ocupacionais`                      | pull ASO (A.2)     |
| GET    | `/v2/controles_epi`                            | pull ficha de EPI  |
| GET    | `/v2/treinamentos_realizados_trabalhadores`    | pull treinamentos  |
| POST   | `/v2/trabalhadores/create_or_update`           | push onboarding    |

Particularidades confirmadas em chamadas reais (2026-07-12) e no smoke
de homologação (2026-07-13):

- Listagens são páginas Spring Data (`?page=&size=`, resposta
  `{content, totalElements}`).
- Parâmetro `fields` (projeção de colunas) é **obrigatório** — 409 sem
  ele. Usamos projeções mínimas por recurso (minimização LGPD). Sintaxe
  aninhada (`trabalhador.cpf`) **validada em homolog**.
- `/v2/*/contar` está quebrado no backend deles (erro Querydsl) —
  contagens via `totalElements` de uma página `size=1`.
- `codigoExterno` no trabalhador carrega o nosso employee id
  (reconciliação do onboarding).
- **CPF é armazenado formatado** (`529.982.247-25`) e filtros comparam a
  string exata — filtrar pelos 11 dígitos devolve vazio. O adapter
  normaliza na saída e formata nos filtros (`find_trabalhador_by_cpf`).
- **Push exige projeto/estabelecimento vinculado** (403 "Estabelecimento
  não especificado" sem ele) → `ONSAFETY_PROJETO_ID`.
- **`create_or_update` com `isEditing=false` é upsert completo por CPF**
  (cria e atualiza; N pushes = 1 registro — idempotência confirmada).
  `isEditing=true` exige `id`+`versao` (lock otimista) e responde 409
  sem eles; não é usado no fluxo padrão.
- **Escrita bem-sucedida responde 200 com corpo vazio** — o id sai de um
  lookup por CPF na sequência.
- **DELETE é soft-delete** (`excluidoEm` + `ativo=false`) e a listagem
  padrão deles **inclui** excluídos — os `list_*` do adapter defaultam
  `ativo=True` para o pull não ingerir registros deletados.
- 401 = auth (token inválido/ambiente errado); **403 = validação de
  negócio**, não auth.

**LGPD:** ASO é dado de saúde. Todo pull deve gravar log de auditoria
(padrão `dp_dossie_consultas`) no service que consome o adapter.

Env vars:

| Variável                    | Obrigatória | Descrição                                    |
|-----------------------------|-------------|----------------------------------------------|
| `ONSAFETY_TOKEN`            | opcional    | Sem token, adapter opera em mock determinístico. |
| `ONSAFETY_BASE_URL`         | não         | Default: homologação (`api.dev.onsafety.com.br`). |
| `ONSAFETY_ALLOW_PROD_WRITE` | não         | Default: `false`. **Guard-rail**: `create_or_update` contra `api.onsafety.com.br` levanta `OnsafetyProdWriteBlockedError` sem este opt-in (o token disponível hoje é o de produção — ADR-001). Leitura não é afetada. |
| `ONSAFETY_PROJETO_ID`       | p/ push     | Estabelecimento/projeto OnSafety ao qual o push vincula o trabalhador. Sem ele a API deles recusa com 403. Em homolog: obra de teste "OBRA TESTE MOTOR CENTRAL". |

**Pull SST (Squad 2):** `POST /api/v1/dp-sesmt/onsafety/pull` (manual) e
cron 07h30 (`worker.tasks.dp_sesmt.pull_onsafety` — antes dos alertas ASO
das 08h05, para usarem dado fresco). Matching por CPF (validado com
`is_valid_cpf`; sem match não cria funcionário). ASOs → colunas `aso_*`
de `dp_employees` com regra **"ASO nunca regride"** (pull não sobrescreve
dado mais recente); fichas de EPI → `EmployeeDocument` tipo `FICHA_EPI`
com `source="onsafety"` e upsert por `onsafety_external_id` (docs manuais
nunca são tocados). LGPD: 1 row em `dp_dossie_consultas` por
(funcionário, fonte) por run + summary do run em `audit_log`.

**Push de onboarding (etapa 3):** `POST /api/v1/dp-sesmt/employees/{id}/sync-onsafety`
envia o funcionário via `create_or_update` (`codigoExterno` = employee id;
upsert na OnSafety → idempotente). Cada tentativa vira uma row append-only
em `dp_onboarding_syncs` (ok/erro) + audit log; a task Celery
`worker.tasks.dp_sesmt.sync_onboarding` faz o mesmo por CPF. Erros de
upstream (inclusive o guard de prod) viram row `status="erro"` — nunca 500.

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

## Google Document AI — OCR Parte Diária (Módulo B.2)

**Adapter:** `app/integrations/google_documentai/client.py`

**O que faz:** OCR de partes diárias escaneadas (PDF/JPG/PNG) via Google
Document AI. Extrai data, operador, obra, equipamento, placa, horímetros e
KM. Os campos extraídos são pré-preenchidos em `partes_diarias` e o
operador revisa pela UI antes de marcar `ocr_status='revisado'`.

**Auth:** OAuth2 Service Account (JWT Bearer flow). O JSON da service
account é colado em `GOOGLE_DOCUMENTAI_CREDENTIALS_JSON` e o adapter
gera um RS256 JWT, troca por access_token em
`https://oauth2.googleapis.com/token`, e cacheia o token por 55 minutos.
Não dependemos de `google-auth` — apenas `python-jose[cryptography]`
(que já está no projeto pelo módulo de autenticação).

**Endpoint Document AI:**

```
POST https://{location}-documentai.googleapis.com/v1/projects/{project_id}/locations/{location}/processors/{processor_id}:process
Authorization: Bearer {access_token}

{
  "rawDocument": {
    "mimeType": "application/pdf",
    "content": "<base64 do arquivo>"
  }
}
```

A resposta tem `document.entities[]`, cada entity com `{type, mentionText,
confidence}`. O adapter mapeia o `type` para o schema canônico
(`data`/`operador`/`obra`/`equipamento`/`placa`/`horimetro_inicio`/
`horimetro_fim`/`km_inicio`/`km_fim`). Tipos desconhecidos são ignorados
mas ficam no `ocr_payload` para auditoria.

**Configuração:**

| Env var | Default | Função |
|---|---|---|
| `GOOGLE_DOCUMENTAI_CREDENTIALS_JSON` | vazio → mock | JSON inteiro da service account (uma única string, escape de `\n` ok) |
| `GCP_PROJECT_ID` | vazio → mock | ID do projeto GCP |
| `DOCUMENTAI_PROCESSOR_ID` | vazio → mock | ID do processor (recomendo `FORM_PARSER_PROCESSOR`) |
| `DOCUMENTAI_LOCATION` | `us` | Região Document AI (`us` ou `eu`) |
| `PARTE_DIARIA_STORAGE_SUBDIR` | `MotorCentral/partes-diarias` | Subpasta no storage para os anexos |

**Modo mock (default em dev/CI):** se *qualquer* uma das 3 credenciais
estiver vazia, o adapter cai em `GoogleDocumentAIMockClient`. Mock é
determinístico — `sha1(filename|len(content))` é o seed dos campos
gerados. Mesmo arquivo upload sempre gera mesma extração — útil para
testes de regressão. Source field marcado como `google_documentai_mock`.

**Pricing:** ~US$1.50 por 1 000 páginas processadas (FORM_PARSER no
plano standard, abr/2025). Quotas de API: 600 requests/min por projeto.

**Para ligar real:**

1. Console GCP → Document AI → Processadores → "Criar processador"
   → escolher *FORM_PARSER_PROCESSOR* na região `us` (ou `eu`).
2. Copiar o **Processor ID** mostrado na página do processador.
3. IAM & Admin → Service Accounts → criar conta com role
   *Document AI API User* → gerar chave JSON.
4. Colar a chave JSON inteira em `GOOGLE_DOCUMENTAI_CREDENTIALS_JSON`
   no `.env` (linha única; `\n` dentro do `private_key` é aceito).
5. Configurar `GCP_PROJECT_ID` e `DOCUMENTAI_PROCESSOR_ID`.
6. Restart da API — adapter detecta as 3 vars preenchidas e sai do mock.

**Persistência (`partes_diarias`):**

- Anexo armazenado via `EditaisStorage` (local em dev, OneDrive em prod) —
  reusa abstração do D.4.
- `ocr_status` ∈ `{pendente, processado, revisado, erro}`. Workflow:
  upload → `pendente`; OCR roda → `processado` (com campos preenchidos);
  operador edita pela UI → auto-promovido para `revisado`; falha de
  Document AI → `erro` com `ocr_error_msg`, sem perder o anexo
  (operador pode reprocessar).
- FK `veiculo_id` é `ON DELETE SET NULL` — parte diária sobrevive ao
  delete do veículo (auditoria operacional).
- Audit log em todas as mutações: `manutencao_frota.parte_diaria` com
  `action="create"` (upload), `action="update"` (OCR processado ou
  revisão), `action="delete"`, `action="error"` (falha do Document AI).

**Tratamento de erros:**

- `processar_ocr_parte_diaria` **nunca** propaga exceção para o
  router — falha de transporte, auth, formato inesperado: tudo vira
  `ocr_status='erro'` com `ocr_error_msg` truncado em 500 chars. UI
  renderiza erro inline e oferece botão "Reprocessar".
- Endpoints retornam 201 mesmo em caso de erro de OCR (com payload
  da parte com `ocr_status='erro'`); 422 só para arquivo vazio /
  404 para `parte_id` inexistente.

---

## Tangerino (ponto eletrônico — Sólides)

**Adapter:** `app/integrations/tangerino/` (`TangyrinoClient` com modo
mock determinístico embutido — padrão Infosimples).

Sem `TANGERINO_API_KEY`, mock determinístico (3 funcionários, 2 obras).
Endpoints validados contra o spec público
`https://employer.tangerino.com.br/v2/api-docs` (2026-08-04):
funcionários (`/employee/find-all`), batidas
(`/external/api/v1/payssego/punches/{id}`), locais de trabalho
(`/workplace/find-all`). Auth: api key crua no header `Authorization`.

**Limitações descobertas no spec (impactam a apropriação de mão de obra):**

- **Não há geolocalização nas batidas** (`PunchSimpleDTO` só tem
  timestamps) nem em nenhum modelo do spec público. O vínculo
  funcionário→obra confiável é o **workplace** (local de trabalho)
  associado ao funcionário. A hipótese "localização da batida" do
  documento de mão de obra precisa ser confirmada com o suporte Sólides
  — pode existir em outra superfície de API.
- Não há endpoint de afastamentos no spec público.
- Unidade dos timestamps (epoch ms assumido) e formato de
  `startDate`/`endDate` a confirmar com credencial real.

Env vars:

| Variável              | Obrigatória | Descrição                                    |
|-----------------------|-------------|----------------------------------------------|
| `TANGERINO_API_KEY`   | opcional    | API key (vazio = mock determinístico)        |
| `TANGERINO_BASE_URL`  | não         | Default: `https://employer.tangerino.com.br` |

## Onvio (Domínio/Thomson Reuters — NF-e para o contador)

**Adapter:** `app/integrations/onvio/` (`OnvioClient` com OAuth2
client_credentials real + `OnvioMockClient` determinístico).

Sem qualquer uma das 3 credenciais, mock determinístico. Fluxo: token
OAuth2 (`auth.thomsonreuters.com`, cache 24h em memória) → activation
(`/dominio/integration/v1/activation/*`) → envio (`POST
/dominio/invoice/v3/batches`, multipart) → status (`GET /batches/{id}`;
sucesso = mensagem "Arquivo armazenado na API").

**Guard-rail:** envio real é escrita no Domínio de PRODUÇÃO do escritório
contábil (sem sandbox conhecido). `ONVIO_ALLOW_SEND=false` (default)
bloqueia `send_nfe_xml` real com `OnvioSendBlockedError`; mock não é
afetado. Coexiste com `app/integrations/dominio` (Central do
Desenvolvedor) — qual superfície o módulo fiscal usa é decisão de
serviço, não do adapter.

Env vars:

| Variável                  | Obrigatória | Descrição                                    |
|---------------------------|-------------|----------------------------------------------|
| `ONVIO_CLIENT_ID`         | opcional    | Credencial OAuth Thomson Reuters             |
| `ONVIO_CLIENT_SECRET`     | opcional    | Credencial OAuth Thomson Reuters             |
| `ONVIO_INTEGRATION_KEY`   | opcional    | Chave de integração do vínculo contador↔cliente |
| `ONVIO_AUDIENCE`          | não         | Default: `409f91f6-dc17-44c8-a5d8-e0a1bafd8b67` |
| `ONVIO_ALLOW_SEND`        | não         | Default: `false` bloqueia envio real          |
