# Test Plan — D.4 (PR #4): Download de editais + anexos via PNCP

**Feature under test:** `/licitacoes` e `/licitacoes/{id}` agora permitem baixar o edital + anexos de uma contratação pública direto do PNCP, via Server Action → `POST /api/v1/licitacoes/{id}/edital/download`. Arquivos são gravados em `EDITAIS_STORAGE_PATH` (`/tmp/motor-central/editais` em dev) e metadados em `licitacoes_editais` + `licitacoes_editais_anexos`.

**Branch/PR:** `devin/1776965747-modulo-d-scraper-editais` → main · PR #4 · CI 3/3 green · 0 review comments.

**Scope:** only PNCP primary path. Fallbacks (ComprasNet/Licitações-e) são scaffold por design e levantam exceções específicas — fora do escopo do E2E.

---

## Subject

**Licitação primária — id=2** (sem edital baixado ainda; PNCP retorna **10 arquivos**):
- CNPJ: `45368545000193` · ano `2026` · sequencial `24` · modalidade `Leilão - Eletrônico`
- Validação prévia: `GET https://pncp.gov.br/api/pncp/v1/orgaos/45368545000193/compras/2026/24/arquivos` → HTTP 200, 10 items.

**Licitação secundária (controle)** — id=14 (já baixada, 13 anexos, `status=completed`): usada para provar idempotência num caso já existente se id=2 tiver algum problema.

---

## Primary end-to-end flow (vai ser gravado)

### Passo 1 — Baseline `/licitacoes`
- **Ação:** abrir `http://localhost:3000/licitacoes`
- **Pass criteria (falham se o código estiver quebrado):**
  - Header da tabela contém a coluna literal **"Edital"** (se a coluna não existir, o feature não foi entregue)
  - Linha id=2 exibe botão **"Baixar"** no célula Edital (form com Server Action)
  - Linha id=14 exibe badge **"Baixado (13)"** ou equivalente (`status=completed`, `anexos_count=13`)
  - Contador total ainda em **197 licitações** (regressão PR #1)

### Passo 2 — Navegar para detalhe (estado vazio)
- **Ação:** clicar link **"detalhes"** da row id=2
- **Pass criteria:**
  - URL vira `/licitacoes/2`
  - Página renderiza título com `Leilão - Eletrônico · 2026/24`
  - Seção "Edital" mostra status **"pending"** (ou label equivalente "Ainda não baixado")
  - Não existe lista de anexos (ou lista vazia explícita)
  - Botão primário visível com texto literal **"Baixar edital"** (não "Atualizar")

### Passo 3 — Trigger do download (happy path)
- **Ação:** clicar **"Baixar edital"**
- **Pass criteria:**
  - Server Action faz `POST /api/v1/licitacoes/2/edital/download` → HTTP 200
  - Response body inclui `"status":"completed"` e `"new_anexos":10` (*número específico; se PNCP mudar o count muda*)
  - Após `revalidatePath`, página mostra:
    - Badge literal **"Baixado"** (ou "completed")
    - **10 anexos** na lista
    - Cada anexo tem: título, tamanho em MB ou KB (não "0 bytes"), link **"ver no PNCP"** apontando para `pncp.gov.br/...`
  - Filesystem: `/tmp/motor-central/editais/2/` existe e contém 10 arquivos com `size > 0`
  - DB: `SELECT COUNT(*) FROM licitacoes_editais_anexos WHERE edital_id=(SELECT id FROM licitacoes_editais WHERE licitacao_id=2)` = **10**

### Passo 4 — Idempotência (re-click Atualizar)
- **Ação:** clicar botão **"Atualizar"** (mesmo botão, label muda de "Baixar" para "Atualizar" quando já existe edital)
- **Pass criteria:**
  - Server Action faz segundo `POST /.../edital/download` → HTTP 200
  - Response body inclui `"new_anexos":0` (*crítico; se constraint UNIQUE falhar, vira ≥1 ou erro 500*)
  - Count no DB permanece **10** (não 20)
  - Count de arquivos em `/tmp/motor-central/editais/2/` permanece **10** (não duplica)
  - Lista na UI mostra os mesmos 10 anexos (mesmos sequenciais)

### Passo 5 — Regressão baseline (volta para lista)
- **Ação:** voltar para `/licitacoes`
- **Pass criteria:**
  - Linha id=2 agora mostra badge **"Baixado (10)"** (antes mostrava "Baixar")
  - Linha id=14 continua mostrando **"Baixado (13)"**
  - Contador total ainda **197 licitações**

---

## Would this look identical if the feature were broken?

Adversarial check — se o código D.4 estiver quebrado:

- Coluna "Edital" não apareceria → Passo 1 falha
- Server Action daria 404/500 → Passo 3 falha (sem badge "Baixado")
- Se `list_arquivos` quebrasse silently, `new_anexos` seria 0 na 1ª chamada (não 10) → Passo 3 falha numericamente
- Se a `UNIQUE(edital_id, sequencial_documento)` não existisse, idempotência falharia (count vira 20 ou erro DB) → Passo 4 falha
- Se `stream_arquivo` escrevesse arquivo vazio, tamanho na UI seria "0 bytes" ou arquivo no FS `size=0` → Passo 3 falha
- Se `revalidatePath` não funcionasse, badge não mudaria após download → Passo 3 falha (visual)

Cada passo tem assertion específica + número específico que só passa se a implementação estiver correta.

---

## Evidence collection

- **Recording:** full run Passo 1 → Passo 5, com annotations:
  - setup: navigating to /licitacoes
  - test_start: It should download edital on demand (id=2, expect 10 anexos)
  - assertion: Precondition: licitacao 2 shows "Baixar" button, no anexos
  - assertion: Edital downloaded — 10 anexos shown with sizes + PNCP links
  - test_start: It should be idempotent on re-download
  - assertion: Re-download keeps count at 10 (new_anexos: 0)
- **Screenshots:** baseline `/licitacoes`, detalhe vazio, detalhe baixado, idempotência
- **Shell proof:** `ls /tmp/motor-central/editais/2/ | wc -l` = 10 · DB counts
- **Report:** `test-report-d4.md` com tudo inline

---

## Known non-goals (not tested)

- ComprasNet adapter — raises `ComprasnetCaptchaRequired` by design (scaffold)
- Licitações-e adapter — raises `LicitacoesECredentialsRequired` by design (scaffold)
- Celery worker task `download_edital` — same service, already unit-tested
- MinIO/S3 storage — future PR, interface ready
