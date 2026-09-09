# Integrações (adapters em `apps/api/app/integrations/`)

Todos os adapters implementam `IntegrationClient` (`app/integrations/base.py`).
Chaves de acesso vivem em variáveis de ambiente; **nunca no código**.

| Subpackage | Sistema | Tipo | Módulos consumidores |
|------------|---------|------|----------------------|
| `dominio`       | Domínio (Thomson Reuters)         | API          | A, C |
| `onvio`         | Onvio (admissão/contabilidade)    | API          | A |
| `onsafety`      | OnSafety (SST, EPIs)              | API          | A |
| `tangerino`     | Sólides Ponto (ex-Tangerino)      | API          | A, B |
| `totvs`         | TOTVS RM (ERP financeiro)         | API (REST/SOAP) | C |
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
disparar os jobs abaixo via Resend (America/Sao_Paulo). Horários
escalonados de propósito para distribuir o burst no provedor:

| Job                          | Task Celery                                          | Horário            |
|-------------------------------|-------------------------------------------------------|---------------------|
| Boletins de licitação          | `worker.tasks.licitacoes.dispatch_boletins`            | 07h, 13h, 19h        |
| Alertas de vencimento de certidões (D.6) | `worker.tasks.licitacoes.dispatch_certidao_alerts` | 08h00               |
| Alertas de vencimento de ASO (A.2) | `worker.tasks.dp_sesmt.dispatch_aso_alerts`         | 08h05               |
| Alertas de DCB/perícia de afastamentos (D.4) | `worker.tasks.dp_sesmt.dispatch_afastamento_alerts` | 08h10          |
| Alertas de vencimento de contratos (Squad 5) | `worker.tasks.financeiro.dispatch_contrato_alerts` | 08h15          |

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
  negócio**, não auth. Sem token válido **todo** `GET /v2/*` responde 403
  com corpo vazio — não confundir com projeção `fields` inválida.
- **O spec OpenAPI deles é público e não exige token:**
  `GET https://api.dev.onsafety.com.br/v3/api-docs` (~492 KB, 354
  schemas). É a fonte mais rápida para conferir campo/tipo antes de
  ampliar uma projeção `fields` — foi assim que os campos de data dos
  treinamentos (`dataFim`, `dataVencimento`, `validadeDias`) e o
  `establishment` foram descobertos (08/09/2026).
- **`resultadoAso` não tem enum nem descrição em lugar nenhum do spec**
  (`integer int32` puro; nenhum literal "apto"/"inapto" nas 354 schemas).
  O mapa `{1: apto, 2: inapto, 3: apto_restricoes}` do `onsafety_sync.py`
  segue **assumido** — pergunta em aberto com o suporte, e o rollout do
  ASO em produção depende dela.
- **`ControleEpi.validade` é `string` sem `format`** no spec (formato
  livre confirmado). Ao lado existe `vidaUtilDia` (int32), útil como
  sinal cruzado se o formato da string se mostrar instável.

**LGPD:** ASO é dado de saúde. Todo pull deve gravar log de auditoria
(padrão `dp_dossie_consultas`) no service que consome o adapter.

Env vars:

| Variável                    | Obrigatória | Descrição                                    |
|-----------------------------|-------------|----------------------------------------------|
| `ONSAFETY_TOKEN`            | opcional    | Sem token, adapter opera em mock determinístico. |
| `ONSAFETY_BASE_URL`         | não         | Default: homologação (`api.dev.onsafety.com.br`). |
| `ONSAFETY_ALLOW_PROD_WRITE` | não         | Default: `false`. **Guard-rail**: `create_or_update` contra `api.onsafety.com.br` levanta `OnsafetyProdWriteBlockedError` sem este opt-in (o token disponível hoje é o de produção — ADR-001). Leitura não é afetada. |
| `ONSAFETY_PROJETO_ID`       | p/ push     | Estabelecimento/projeto OnSafety ao qual o push vincula o trabalhador. Sem ele a API deles recusa com 403. Em homolog: obra de teste "OBRA TESTE MOTOR CENTRAL". |

**Pull SST (Squad 2):** `POST /api/v1/dp-sesmt/onsafety/pull` (manual —
**enfileira** a task na fila `dp_sesmt`; `?inline=true` roda no request e
devolve o summary, só para base pequena) e cron 07h30
(`worker.tasks.dp_sesmt.pull_onsafety` — antes dos alertas ASO das 08h05,
para usarem dado fresco). Matching por CPF (validado com `is_valid_cpf`;
sem match não cria funcionário). ASOs → colunas `aso_*` de `dp_employees`
com regra **"ASO nunca regride"** (pull não sobrescreve dado mais
recente); fichas de EPI → `EmployeeDocument` tipo `FICHA_EPI`; treinamentos
→ `EmployeeDocument` tipo `NR10`/`NR12`/`NR18`/`NR35`. Todos com
`source="onsafety"` e upsert por `onsafety_external_id` (docs manuais
nunca são tocados). O run carrega 3 índices em memória (funcionários por
CPF, documentos por external id, obras por código) — antes era 1 SELECT
por item. LGPD: 1 row em `dp_dossie_consultas` por (funcionário, fonte)
por run + summary do run em `audit_log`.

**Treinamentos → NR (regras de segurança).** Só entra no dossiê o
treinamento que é (a) **aprovado** e (b) **com validade conhecida**
(`dataVencimento`, ou `dataFim + validadeDias`). O motivo do item (b): o
`diagnostico/runner.py` lê `validade = None` como *"documento perene →
conforme"*, então uma NR-35 vencida gravada sem validade apareceria como
**OK** no checklist — pior do que aparecer como ausente. Descartados
contam em `treinos_sem_validade` / `treinos_reprovados`. A NR sai do
`treinamentoCodigo.grupo` (rótulo normalizado deles), com fallback para
`sigla` e `descricao`; NR fora do checklist (NR-06, integração, brigada)
não vira documento e conta em `treinos_nr_desconhecida`.

**Obra do documento.** O `establishment` (Projeto) vem junto nos
treinamentos e nas fichas de EPI e resolve `dp_employee_documents.obra_id`
por `Projeto.codigoExterno` → `obras_obra.codigo`, com fallback pelo
código embutido no nome (`"OBRA 243 - ..."`, mesmo padrão dos locais de
trabalho do Tangerino, ADR-003). Sem match o documento entra com
`obra_id` nulo e o contador `projeto_no_match` mede o buraco de cadastro.

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

## Tangerino (ponto eletronico — Solides)

**Adapter:** `app/integrations/tangerino/` (`TangerinoClient` com modo
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
  documento de mão de obra precisa ser confirmada com o suporte Solides
  — pode existir em outra superfície de API.
- Não há endpoint de afastamentos no spec público.
- Unidade dos timestamps (epoch ms assumido) e formato de
  `startDate`/`endDate` a confirmar com credencial real.

Env vars:

| Variável              | Obrigatória | Descrição                                    |
|-----------------------|-------------|----------------------------------------------|
| `TANGERINO_API_KEY`   | opcional    | API key (vazio = mock determinístico)        |
| `TANGERINO_BASE_URL`  | não         | Default: `https://employer.tangerino.com.br` |

## Onvio (Dominio/Thomson Reuters — NF-e para o contador)

**Adapter:** `app/integrations/onvio/` (`OnvioClient` com OAuth2
client_credentials real + modo mock determinístico embutido).

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

## TOTVS RM (adapter pronto contra mock — aguardando credencial)

Adapter: `app.integrations.totvs.client.TotvsClient`, com a extração
atrás da interface `TotvsExtractor` (`app/integrations/totvs/extractor.py`).
Consome: Módulo C. Ticket TOTVS **30268517**.

**Read-only por construção.** Não existe método de escrita no adapter;
`push()` levanta `TotvsReadOnlyError`. Perfil somente-leitura no RM é a
outra camada — se uma falhar, a outra segura.

### Ambiente (confirmado pela TOTVS em 24/08/2026)

| | |
|---|---|
| Hospedagem | **TOTVS Cloud (TCloud)** — leitura direta no banco está fora |
| Protocolo | **HTTPS** (config de API já vem pronta no cloud) |
| Versão produção | **12.1.2510.136** |
| Versão desenvolvimento | **12.1.2510.170** — usar para validar sem gastar licença de prod |
| ESN da conta | Otavio Moreira Abdo Lopes (`otavio.lopes@totvs.com.br`) |

Essa versão está acima de todos os cortes que importam: `HttpPort`/`ApiPort`
separáveis (≥12.1.25), controle de licença (≥12.1.15), `ApiPool` + log
LS006 no monitor (≥12.1.2306), `JWTTokenExpireMinutes` configurável
(≥12.1.2310), e as APIs de framework **sem consumo de licença**
(12.1.2302 p121 / 2209 p195 / 2205 p246).

### Licença — é isto que dita o desenho

Licença de WebService do RM é consumida **por requisição de dados** e só
liberada quando a requisição termina: 3 chamadas simultâneas = 3 licenças.
A geração de token em `/api/connect/token` **não consome licença**
(confirmado pela TOTVS em 27/08/2026), então renovar é de graça e o
default de 5 minutos deixou de ser um problema a resolver. Há
reaproveitamento da mesma licença por 30s entre requisições **não
concorrentes**. A escala de consumo é `4001 → 4199 → 4016 → 4017 → 4000
→ 4099`, subindo até a **TOTVS Full**, e a TOTVS documenta que *não é
possível nomear licenças* — ou seja, um pull descuidado tira assento de
usuário real do ERP. Mensagem de esgotamento: `Excedeu o número de licenças`.

Consequências, todas já implementadas:

- Pull **só no worker**, nunca na API (`worker.tasks.financeiro.pull_totvs`).
- Chamadas **sequenciais**, encadeadas (aproveita a janela de 30s).
- **Lock single-flight** no Redis (`app.core.locks.single_flight`,
  chave `totvs:pull:lancamentos`) — obrigatório.
- Slot no beat às **03h00**: fora do horário comercial e fora do bloco
  08h00–08h15, que já tem certidões, ASO, afastamentos e contratos.
- `health_check` usa `/api/framework/v1/coligadas`, que **não consome
  licença** nessa versão.

### Extractors

| Modo | Porta | Auth | Paginação | Estado |
|---|---|---|---|---|
| `consultasql` | `HttpPort` (SOAP) | Basic | sem limite de retorno | **caminho oficial** — sentença cadastrada no RM |
| `rest` | `ApiPort` (WebAPI) | Bearer | `page`/`pageSize` + `hasNext` | **não serve para lançamentos** (ver abaixo) |
| `mock` | — | — | — | determinístico, default sem credencial |

**A escolha está fechada.** A TOTVS confirmou em 31/08/2026 (Eduarda
Soares) que *não existe API REST de lançamento financeiro nativa* que
atenda extração massiva com paginação — as APIs do módulo Gestão
Financeira não cobrem esse caso — e indicou o **wsConsultaSQL** como a
alternativa adequada. O `RestExtractor` fica no código porque as APIs de
framework (coligadas) seguem úteis e porque a interface prova que trocar
é barato, mas não é o caminho para lançamentos.

**Bearer, não Basic** — a própria TOTVS registra que Basic "não é
recomendada pelo seu baixo nível de segurança". Token sai de
`POST /api/connect/token` com `{"username","password"}` e dura **5 minutos**
por default (refresh token, 16h). Um pull noturno atravessa o expiry: o
`RestExtractor` renova via `refresh_token` e faz **um** retry em 401.

**wsConsultaSQL não é SQL livre.** `RealizarConsultaSQLContexto` executa
uma sentença **cadastrada dentro do RM** (BI → Criação de consultas SQL),
por `codSentenca` + `codColigada` + `codSistema`, com parâmetros
separados por `;`. Resposta é um `NewDataSet` XML em CDATA dentro do
envelope SOAP. Reforça o read-only (a query nem mora no nosso código),
mas cria dependência: mudar a extração vira mudança no RM, não deploy
nosso. Erro `FE011` (sentença bloqueada por filtro de perfil/usuário)
vira `TotvsPermissionError` — **nunca** pode ser confundido com "zero
lançamentos".

### Armadilha: permissão que filtra em silêncio (CONFIRMADO)

Falta de permissão de **coligada** nas APIs **não levanta erro** — vira
**filtro**, devolvendo um subconjunto com HTTP 200. Confirmado pela TOTVS
em 27/08/2026, textualmente: *"o perfil vai funcionar como filtro também.
Nesse cenário, ao consultar uma API apenas recebe os registros da
coligada 1"*. (Permissão de *rotina* é caso diferente: essa dá erro.)
Campo restrito usado em filtro dá 403; em `fields`, só some da resposta.

Consequência prática: um perfil apertado demais entrega dado parcial
parecendo sucesso, e a reconciliação fecha "certo" em cima de metade dos
lançamentos.

**Guarda implementada.** `TOTVS_COLIGADAS_ESPERADAS` (CSV) declara quais
coligadas o pull deve enxergar. Antes de ler, o pull chama
`/api/framework/v1/coligadas` — que não consome licença — e compara. Se
faltar alguma, levanta `TotvsPermissionError` e grava `failed` no
`totvs_sync_log`, em vez de gravar leitura parcial. É opt-in: vazio
desliga a guarda. O extractor `consultasql` não consegue listar coligadas
(o endpoint vive na ApiPort, ele fala SOAP na HttpPort), então lá a
guarda é pulada com aviso em log.

### Decimal

O RM devolve valor como string, e o formato depende da tag
`WebServiceCulture` no Host. `app.core.valores.coerce_valor_rm` normaliza
o separador (o que aparece por último é o decimal) e delega ao
`coerce_valor` reusado do `financeiro_contratos`. Nada passa por `float`.
Ambiguidade conhecida: `"1.234"` sem vírgula é lido como **decimal
invariant** (1.234), que é o que `WebServiceCulture=Invariant` produz —
pedido em aberto com o time Cloud.

### Tabelas de destino

- **`totvs_lancamentos`** — unique em `external_id` **sozinho**, no
  formato **`codcoligada-idlan`** (padrão do PNCP: `external_id` +
  `ON CONFLICT`). `extractor` é proveniência, **não** chave: se entrasse
  na chave, trocar REST por ConsultaSQL duplicaria a base inteira.

  A chave foi confirmada pela TOTVS em 31/08/2026: *"a chave que
  identifica um lançamento de forma única na tabela FLAN é a combinação
  de CODCOLIGADA com IDLAN"*. **`CODFILIAL` ficou de fora** — a
  suposição inicial deste adapter a incluía, e isso criaria uma linha
  duplicada se a filial de um lançamento fosse corrigida no RM. A filial
  continua gravada como dado, só não como identidade.
- **`totvs_sync_log`** — unique em (`source`, `janela`), com `source`
  na chave **desde o primeiro commit**. É o bug do
  `dispatch_contrato_alerts_endpoint`: em `contratos_alertas_log` a
  unique key não distingue origem, então disparo manual queima a janela
  do beat. Aqui `beat` e `manual` ocupam linhas distintas.

Nada de coluna `totvs_*` no `Contrato` (mesmo precedente da assinatura
digital, `financeiro_contratos/models.py`, 04/08). Reconciliação é por
documento — e depende da normalização só-dígitos de
`Contrato.contraparte_documento`, hoje `String(32)` livre: **ticket
separado**.

### Env vars

| Variável | Obrigatória | Descrição |
|---|---|---|
| `TOTVS_BASE_URL` | sim (real) | `https://<host>:<ApiPort ou HttpPort>`. Sem ela, o adapter usa mock. |
| `TOTVS_USERNAME` | sim (real) | Usuário do RM. Permissão é a do Perfil dele no módulo. |
| `TOTVS_PASSWORD` | sim (real) | Mesma senha de acesso ao RM. |
| `TOTVS_EXTRACTOR` | não | `rest` \| `consultasql` (default) \| `mock`. |
| `TOTVS_LANCAMENTOS_PATH` | não | Endpoint REST de lançamentos — **placeholder** até o time RM Gestão Financeira responder. |
| `TOTVS_CONSULTASQL_COD_SENTENCA` | se `consultasql` | Código da sentença cadastrada no RM. |
| `TOTVS_CONSULTASQL_COD_COLIGADA` | não | Default `1`. |
| `TOTVS_CONSULTASQL_COD_SISTEMA` | não | Default `F` (Financeiro). |
| `TOTVS_COLIGADAS_ESPERADAS` | recomendada | CSV das coligadas que o pull deve enxergar (ex.: `1,2`). Guarda contra o filtro silencioso. Vazio = desligada. |
| `TOTVS_PULL_DIAS` | não | Janela do pull, em dias para trás. Default `45`. |
| `TOTVS_PULL_LOCK_TTL_S` | não | TTL do lock single-flight. Default `3600`. |

### Em aberto (bloqueiam o "ligar real", não o código)

1. ~~RM Gestão Financeira~~ — **RESPONDIDO em 31/08/2026.** Sem API REST
   para lançamentos; caminho é wsConsultaSQL; chave `CODCOLIGADA`+`IDLAN`;
   contraparte por `CODCFO`+`CODCOLIGADA` → `FCFO.CGCCFO`; campos
   `VALORORIGINAL`, `DATAVENCIMENTO`, `DATAEMISSAO`, `STATUSLAN`.
   **Resta cadastrar a sentença SQL no RM** (ver abaixo).
2. **Time Cloud**: hostname, portas, IP de saída / VPN, e a tag
   `WebServiceCulture=Invariant` no Host. (`JWTTokenExpireMinutes`
   saiu do pedido: como gerar token não consome licença, os 5 min
   default servem.)
3. **Portal do Cliente → Gestão de Licenças** + ESN: saldo de licenças de
   WebService. Há monitor em tempo real — dá para **medir** o consumo na
   madrugada antes de ligar.
### Usuário de serviço (resolvido)

No cadastro do usuário no RM, aba **Identificação**:

- **"Força a troca de senha a cada ___ dias"** — deixar **desmarcado**
- **"Sempre é Válido"** — deixar **marcado** (libera o campo Expiração
  de Validade)

Sem isso a senha ou o usuário expiram e o pull morre às 3h da manhã sem
ninguém perceber. Orientação da TOTVS em 27/08/2026.

## Onvio / Domínio (credencial recebida em 03/09/2026)

Adapter: `app.integrations.onvio.client.OnvioClient`. Módulo consumidor:
`app.modules.fiscal`. Este é o **único canal de máquina com o Domínio**
(ADR-003, D1): a API só *importa* XML, não há consulta de leitura.

### O fiscal estava plugado no adapter errado

Até 03/09/2026 o módulo fiscal instanciava o `DominioClient`, que aponta
para `api.dominioexterior.com.br` com um `POST /token` que não
corresponde a nenhuma API documentada. Com credencial real ele nunca
teria funcionado. A factory `get_dominio_client` agora monta o
`OnvioClient`; o **nome da função foi mantido** de propósito, porque é a
costura usada pelo router e pelos testes (`dependency_overrides`).

Há um teste-guarda (`test_producao_nao_instancia_mais_o_adapter_legado`)
que falha se alguém voltar a instanciar o adapter legado.

### Diferenças de semântica que isso trouxe

| | Antes (`DominioClient`) | Agora (`OnvioClient`) |
|---|---|---|
| Método | `upload_xml(filename, content, tipo)` | `send_nfe_xml(filename, content)` — sem `tipo` |
| Retorno | `protocolo` | `batch_id` |
| Confirmação | imediata | só em `get_batch_status(batch_id)` |

A coluna `protocolo_dominio` passou a guardar o `batch_id`. O nome foi
mantido para não exigir migration por troca de nomenclatura.

### Estado `bloqueado`

`ONVIO_ALLOW_SEND=false` (default) faz o envio real levantar
`OnvioSendBlockedError`. Isso vira `status_envio="bloqueado"`, **não**
`"erro"**, e **não** incrementa `retry_count` — é guard de configuração,
não falha. Marcar como erro faria o worker reprocessar para sempre algo
que nunca vai passar e poluiria a contagem de falhas reais.

### As três entidades

Validadas via `GET /activation/info` em 03/09/2026:

| Empresa | CNPJ |
|---|---|
| Primor Soluções Ltda | 57.803.505/0001-01 |
| Construtora ZAG Ltda | 00.356.328/0001-45 |
| Consórcio ZAG Guaxima | 54.641.090/0001-29 |

O "escritório contábil" das três é a **própria Primor Soluções** — a
contabilidade é interna, não terceirizada.

`ONVIO_INTEGRATION_KEY` é escalar e hoje aponta para a Primor Soluções.
Suporte multi-entidade (uma chave por CNPJ) é ticket separado.

### Bug corrigido

O campo `query` do multipart era `{"boxe/File": false}`; a documentação
(solução 8476) especifica `boxeFile`, sem barra. Nunca apareceu porque o
envio real nunca rodou.

### Falta para enviar de verdade

1. Rodar `activation/enable` para gerar a `integrationKey` de envio
2. `ONVIO_ALLOW_SEND=true`
3. Validar o primeiro envio — como a contabilidade é interna, dá para
   combinar sem depender de terceiro

Canais: `api.dominio@thomsonreuters.com`, WhatsApp 11 5047-2396,
call em calendly.com/leonardo-steiner.

## Sólides Ponto / Tangerino (LIGADO — credencial real desde 27/08/2026)

Adapter: `app.integrations.tangerino.client.TangerinoClient`.
Módulo consumidor: `app.modules.ponto`. Consome: Módulos A e B.

**Somente leitura.** O adapter não tem método de escrita, e a ingestão
**nunca cria funcionário no DP** — `dp_employees` continua sendo a fonte
da verdade do cadastro.

### Credencial

Token de integração obtido no painel em **Empregador → Integrações**
(menu antigo: Configurações → Integrações). Se não aparecer, é preciso
pedir liberação ao suporte (`suportedp@solides.com.br` ou chat dentro da
plataforma).

`TANGERINO_API_KEY` vai no header `Authorization`. A Sólides entrega o
valor já com o prefixo `Basic `; testado contra a API real, **funciona
com e sem o prefixo**, então o adapter repassa o valor como veio.
Sem a variável, cai em mock determinístico.

### O que a exploração da API real revelou

O spec público não declara várias coisas. Medido contra a conta da
Primor em 27–28/08/2026:

| Questão | Resposta |
|---|---|
| Formato de data em `startDate`/`endDate` | **`dd/MM/yyyy`**. ISO devolve **400** (BindException). |
| Unidade dos timestamps | **epoch em milissegundos** (`1787022000000`). |
| Parâmetros de paginação | **`page`/`size`**. `pageNumber`/`pageSize` são ignorados e a resposta cai no default de 20. |
| A API pagina? | **Não.** `page`, `offset`, `start` e `pageNumber` devolvem sempre os mesmos registros. Só `size` é honrado. |
| Teto de `size` | **2000**, imposto pelo servidor. |
| Geolocalização nas batidas | **Não existe** em nenhum modelo. |
| Sem batida no período | **404** `"Cant find punches for this employee"` — ausência de dado, não falha. |

Cada uma dessas viraria um bug silencioso: `pageNumber` traria 20 de 396
funcionários sem erro; ISO abortaria as batidas; o 404 derrubaria o pull
inteiro no primeiro funcionário sem ponto.

### Limitação do fornecedor (a levar à Sólides)

Sem paginação e com `size` limitado a 2000, **não é possível ler os 2400
funcionários da conta (contando demitidos) numa única chamada** — e a
API devolve os **demitidos primeiro**, então pedir tudo traz 2000
demitidos e **zero ativos**.

Por isso o pull usa `showFired=0` e traz os ~396 ativos, que são os que
geram batida. Consequência aceita: o histórico completo de demitidos não
é recuperável em lote. Vale abrir com o suporte deles.

### Vínculo funcionário → obra (fecha o ADR-003)

O ADR-003 deixou "workplace vs geolocalização" como pergunta aberta
aguardando a Sólides. **Resolvida pela API:** geolocalização não existe,
e os 83 locais de trabalho da conta **são as obras** — "Obra 243",
"Obra 217 - Januária", "OBRA 246 - ABAETÉ" — ao lado de ~20
administrativos ("ADM PRIMOR", "ESCRITÓRIO PRIMOR", "MANUTENÇÃO PRIMOR").

A ponte é o código embutido no nome (`app.modules.ponto.matching`):
`"Obra 243"` → `243`, casado contra `obras_obra.codigo`. Três desfechos,
todos legítimos:

- **ligado** — obra existe no cadastro
- **obra não cadastrada** — código extraído, obra ainda não existe. Fica
  pendente e visível na tela; não inventamos obra.
- **administrativo** — sem código, sem obra. Correto, não é falha.

Cuidado de dado: **"Obra 010 CTC" aparece duas vezes** na lista real com
ids diferentes. `nome_normalizado` (sem acento, sem caixa) existe para
detectar esse tipo de duplicidade.

### Tabelas

- `ponto_locais_trabalho` — workplaces + `codigo_obra` + `obra_id` nullable
- `ponto_funcionarios` — funcionário como o Sólides o conhece, ligado por
  CPF ao `dp_employees` (nulo = pendência de cadastro)
- `ponto_batidas` — `external_id` = `{tangerino_id}-{data}-{inicio_epoch}`,
  já que a API não expõe id próprio da batida
- `ponto_sync_log` — unique em (`source`, `janela`, `recurso`), com
  `source` na chave desde o primeiro commit (mesmo precedente do TOTVS)

### Agendamento

`worker.tasks.dp_sesmt.pull_ponto`, **02h30**, sob lock single-flight
(`ponto:pull`). Antes do TOTVS (03h00) para não disputarem o worker, e
fora do bloco 08h00–08h15. É o job mais longo da madrugada: a API só
devolve batidas **por funcionário**, então são ~396 requisições
sequenciais.

### Env vars

| Variável | Obrigatória | Descrição |
|---|---|---|
| `TANGERINO_API_KEY` | sim (real) | Token de Empregador → Integrações. Sem ela, mock. |
| `TANGERINO_BASE_URL` | não | Default `https://employer.tangerino.com.br`. |
| `PONTO_PULL_DIAS` | não | Janela de batidas, em dias para trás. Default `45`. |
| `PONTO_PULL_LOCK_TTL_S` | não | TTL do lock. Default `7200` (o pull é longo). |

### Tela

`/rh/ponto` — números de topo, as duas filas de pendência (local sem obra
cadastrada, funcionário sem cadastro no DP) e o histórico de importações.

### A sentença SQL a cadastrar no RM

Como o wsConsultaSQL executa uma sentença **cadastrada dentro do RM**
(BI → Criação de consultas SQL), esta é a peça que falta para ligar. Ela
usa exatamente os nomes de campo e o relacionamento confirmados pela
TOTVS em 31/08/2026.

Código sugerido: **`MOTOR.FLAN.01`** · Sistema: **`F`** (Financeiro)

```sql
SELECT
    FLAN.CODCOLIGADA,
    FLAN.CODFILIAL,
    FLAN.IDLAN,
    FLAN.VALORORIGINAL,
    FLAN.DATAVENCIMENTO,
    FLAN.DATAEMISSAO,
    FLAN.STATUSLAN,
    FLAN.CODCFO,
    FCFO.NOME    AS NOMECFO,
    FCFO.CGCCFO
FROM FLAN
LEFT JOIN FCFO
       ON FCFO.CODCOLIGADA = FLAN.CODCOLIGADA
      AND FCFO.CODCFO      = FLAN.CODCFO
WHERE FLAN.DATAVENCIMENTO BETWEEN :DATAINICIAL AND :DATAFINAL
```

Detalhes que importam:

- **O `LEFT JOIN` é proposital.** Com `INNER`, um lançamento sem
  contraparte cadastrada sumiria da extração — e sumir em silêncio é o
  pior modo de falha possível numa reconciliação financeira.
- **O join usa `CODCOLIGADA` além de `CODCFO`**, como a TOTVS
  especificou. Só por `CODCFO` haveria mistura entre coligadas.
- **`AS NOMECFO` é o alias que o adapter espera** (`CAMPO_NOME`). Se a
  coluna de nome da FCFO na instância da Primor não for `NOME`, basta
  ajustar o lado esquerdo do alias — o adapter não muda.
- Os parâmetros `:DATAINICIAL` e `:DATAFINAL` são preenchidos pelo
  adapter e enviados separados por `;`, no formato que o
  `ConsultaSqlExtractor` já monta.

Para carga **incremental** (em vez de recarregar a janela inteira),
trocar o `WHERE` por `FLAN.RECMODIFIEDON >= :DATAINICIAL` traz só o que
mudou desde a última execução. Fica como evolução — a janela por
vencimento resolve o caso de uso atual e é mais fácil de conferir.

Depois de cadastrada, preencher no ambiente:

```ini
TOTVS_EXTRACTOR=consultasql
TOTVS_CONSULTASQL_COD_SENTENCA=MOTOR.FLAN.01
TOTVS_CONSULTASQL_COD_SISTEMA=F
TOTVS_CONSULTASQL_COD_COLIGADA=<coligada da Primor>
TOTVS_COLIGADAS_ESPERADAS=<lista das coligadas, ex.: 1,2>
```
