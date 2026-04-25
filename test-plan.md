# Test Plan — PR #1 Dashboard /licitacoes (Módulo D · crawler PNCP)

Source of approval: User clicked "Test the app" + confirmed "Fechar o PR #1 antes (mergear a base PNCP)".
Branch under test: `devin/1776952628-modulo-d-pncp` · PR https://github.com/Dexi-Digital/core-engine-primor/pull/1

## State já confirmado (não será testado de novo)

Estes passos foram validados em planning; não refaço durante execução:

- **T1.x Setup** (migração Alembic, `/health`, `/licitacoes/status`) — OK (passou em planning)
- **T2.x Ingestão PNCP real** — OK: `POST /ingest/pncp?uf=SP&max_paginas=1&data_inicial=2026-04-21&data_final=2026-04-22` retornou `{"inserted":197,"updated":0,"skipped":0,"total_fetched":197,"failed_modalidades":[]}` em 225s; banco tem 197 linhas com 11 modalidades distintas
- **T3.x API endpoints** — OK: `GET /licitacoes?page_size=3` (200, 3 rows), filtro `uf=SP` (197), filtro `uf=XX` (0), detalhe por id (200, external_id populado), id inexistente (404)
- **T4.x Idempotência** — OK: count_before=197 → re-ingest → count_after=197 (idempotente, upsert funciona)
- **T6.x CI + comments** — OK: 3/3 verde, 0 comentários

Tudo acima está em logs; serão incluídos como evidência textual no comentário do PR.

## O que será testado nesta execução (GUI, gravado)

Um único fluxo ponta-a-ponta no dashboard `/licitacoes` provando que o usuário consegue ler, filtrar e paginar os dados reais ingeridos. A gravação será o "yep, funciona" para o reviewer.

### Pré-condição

- Banco já contém 197 licitações reais do PNCP (UF=SP, 11 modalidades)
- Web dev server rodando em http://localhost:3000 · API em http://localhost:8000

### Flow 1 — It should render the 197 real PNCP rows with working filters and pagination

**Etapa 1 — Carregar o dashboard**
- Ação: navegar para `http://localhost:3000/licitacoes`
- Asserções (todas devem passar):
  - Sidebar exibe "MOTOR CENTRAL" e item "Licitações"
  - `<h1>` com texto exato **"Licitações"** aparece
  - Badge verde com texto **"Implementado"** aparece no canto direito
  - Contador mostra **"197 licitações · página 1"** (o número 197 é crítico — se o backend quebrar, esse número muda)
  - Tabela exibe pelo menos 1 linha com colunas: Publicação, UF / Município, Órgão, Modalidade, Objeto, Valor estimado
  - Pelo menos uma linha tem UF=SP no formato `SP · <município>` (ex: "SP · ARARAQUARA")
  - Pelo menos uma célula de valor estimado começa com "R$" (ex: "R$ 1.377.384,27") — prova formatação pt-BR funcionando
- Por que não pode passar com código quebrado: se o fetch falhar, o fallback mostra "Não foi possível conectar à API" em vez do contador. Se a tabela estiver vazia, aparece "Nenhuma licitação ingerida ainda". Se a formatação de moeda quebrar, não vai aparecer "R$".

**Etapa 2 — Filtrar por UF inexistente para forçar empty-state**
- Ação: apagar o campo UF e digitar `XX`, clicar em "Filtrar"
- Asserções:
  - URL muda para `…/licitacoes?uf=XX&modalidade=&page=1`
  - O bloco da tabela DESAPARECE
  - Aparece o texto **"Nenhuma licitação ingerida ainda. Dispare um crawler:"** + o snippet curl
- Por que não pode passar com código quebrado: esse copy é literal no JSX (page.tsx:114); se o filtro não estiver chegando na API, ainda veríamos 197 linhas. Se o fallback for outro (ex: erro de JS), aparece a mensagem amber da API offline.

**Etapa 3 — Filtrar por modalidade "Pregão" e verificar que só Pregão aparece**
- Ação: limpar UF, digitar "Pregão" em Modalidade, clicar "Filtrar"
- Asserções:
  - URL é `…?uf=&modalidade=Pregão&page=1`
  - Contador mostra um número > 0 e < 197 (SP tem 11 modalidades; Pregão filtra subset)
  - TODAS as linhas visíveis (5 primeiras checadas) têm "Pregão" em algum ponto do nome da modalidade
- Por que não pode passar com código quebrado: se o filtro ilike não estiver funcionando, o total seria 197. Se a query quebrar, aparece fallback.

**Etapa 4 — Navegar para a página 2**
- Ação: voltar à visão sem filtros (UF vazio + Modalidade vazio, Filtrar). Clicar no link **"Próxima →"** no rodapé da tabela.
- Asserções:
  - URL vira `…?page=2`
  - Contador mostra **"197 licitações · página 2"**
  - Rodapé mostra **"Página 2 de 10"** (197 ÷ 20 = 9.85 → 10 páginas)
  - Link **"← Anterior"** agora aparece (não existia na página 1)
  - As primeiras linhas exibidas são DIFERENTES das exibidas na página 1 (comparar id visível ou objeto)
- Por que não pode passar com código quebrado: se a paginação estiver bugada, a URL muda mas os dados não. Se o componente Pagination estiver quebrado, "Próxima →" não aparece e não há como clicar.

### Evidências a coletar

- Screenshots: (1) dashboard com 197 + tabela populada, (2) filtro UF=XX com empty-state, (3) filtro Modalidade=Pregão com subset, (4) página 2 com "Anterior"
- Vídeo contínuo curto (<60s) cobrindo etapas 1→4 com anotações
- Logs do uvicorn durante a interação (provam que API tá respondendo)
- Saídas textuais de T2/T3/T4 (já coletadas)

### Riscos/edge-cases que NÃO serão testados aqui

- Testes de detalhe individual de licitação (não há rota `/licitacoes/{id}` no frontend neste PR — só listagem)
- Fuso horário de `data_publicacao_pncp` (só verifico que renderiza pt-BR, não valido offset)
- Workers/Celery (task `crawler_pncp` em apps/workers/) — coberto por teste de unidade, não por fluxo de usuário
- Resposta 5xx do PNCP em tempo real — seria flaky; já coberto por teste com MockTransport

### Finding relevante (não-bloqueante, mas merece registro)

Durante planning descobri que se **uma** das 13 modalidades do PNCP der timeout após 3 retries (o que acontece na prática — `/api/consulta` é lento intermitentemente), a ingestão inteira retorna HTTP 500 e nenhuma linha é persistida (observei isso na primeira chamada real: 94s → ReadTimeout → 500). Preparei uma correção (try/except por-modalidade no service + `reraise=True` no tenacity + teste unitário), **stashed** durante o test mode. Vou aplicá-la após exit_test_mode se o usuário quiser que eu adicione à mesma PR, ou pode ir em um hotfix separado após o merge.
