# Test Plan — PR #2 (D.2 busca full-text objeto_compra)

## O que mudou (user-visible)

O dashboard `/licitacoes` ganhou um terceiro input no formulário de filtros: **"Busca no objeto"**. Digitar um termo e submeter filtra a listagem para apenas as licitações cujo `objeto_compra` contém o termo (case-insensitive, `ilike '%term%'`). Em Postgres, os resultados também são ranqueados por similaridade trigram (pg_trgm + índice GIN — migração `3fbc4a1e8d57`).

O filtro novo é ortogonal aos já existentes (UF e Modalidade): combinar os três no form estreita progressivamente o resultset.

## Estado pré-teste (setup — já feito, não faz parte do plano)

- Postgres local já tem 197 linhas reais do PNCP (SP, 2026-04-21..22, 11 modalidades) ingeridas no PR #1
- Migração `3fbc4a1e8d57` já aplicada (`pg_trgm` + índice GIN ativos) — confirmado via `alembic current`
- `GET /licitacoes?search=aquisicao` já respondeu 200 com `total=15` via curl (backend OK)
- `uvicorn` em :8000, `next dev` em :3000

## Fluxo único (primary) — UI gravada

**Cenário:** usuário orçamentista quer ver só as licitações de SP que tratam de "aquisição" (compra de bens) para direcionar a análise. Com o filtro antigo (só UF/modalidade) ele tinha que ler os 197 objetos um a um.

### Etapa 1 — Estado base

**Ação:** navegar para `http://localhost:3000/licitacoes` (sem query string).

**Asserções:**
- (a) Existe um `<input name="search">` com placeholder literal **"ex: pavimentação asfáltica"** visível no form — se faltar, o PR não renderizou o campo novo.
- (b) Contador exibe literalmente **"197 licitações · página 1"** — se o número estiver diferente, ou o layout quebrou, ou a DB mudou.
- (c) O input de busca está vazio (defaultValue="").

### Etapa 2 — Busca com match real

**Ação:** clicar no input "Busca no objeto", digitar literalmente `aquisicao` (sem acento, para bater com dados reais que podem ter grafia inconsistente), clicar em "Filtrar".

**Asserções:**
- (a) URL passa a ser `/licitacoes?uf=&modalidade=&search=aquisicao&page=1` (ou equivalente com os campos vazios filtrados). O `search=aquisicao` **precisa** estar na query string.
- (b) Contador muda para literalmente **"15 licitações · página 1"** (valor conhecido por curl antes do teste; se vier 197, o filtro não está sendo aplicado; se vier 0 ou outro número, o ranking/filtro está errado).
- (c) Todas as linhas visíveis da tabela (máx 15, já que page_size=20) têm a palavra "aquisicao", "aquisição" ou "aquisicao" visível na coluna "Objeto" (ou via tooltip no `title` do truncate). **Critério adversarial:** se pelo menos uma linha NÃO contiver o termo, o filtro está bugado.
- (d) A paginação não aparece (15 < 20, então só 1 página) — se aparecer "Próxima →", a lógica de `lastPage` está errada.

### Etapa 3 — Busca sem match (guard)

**Ação:** apagar o input, digitar `zzzzz`, clicar "Filtrar".

**Asserções:**
- (a) Contador some; aparece o empty-state com texto literal **"Nenhuma licitação ingerida ainda. Dispare um crawler:"**.
- (b) URL tem `search=zzzzz`.

**Por que inclui essa etapa:** valida o ramo `resp.data.length === 0` do Server Component com o novo param na URL. Se o componente quebrasse com search não-matching, este é o cenário que pega.

### Etapa 4 — Combinação com UF (guard de ortogonalidade)

**Ação:** apagar `zzzzz`, digitar `aquisicao` de volta, digitar `SP` no campo UF (já está SP implicitamente nos dados mas queremos provar o AND), clicar "Filtrar".

**Asserções:**
- (a) URL: `?uf=SP&...&search=aquisicao&...`
- (b) Contador: **"15 licitações · página 1"** (todas as 15 são de SP — confirma que o filtro é AND, não OR).

## Por que esse plano falha se o PR estiver quebrado

- Se o input "Busca no objeto" não foi adicionado no Server Component → Etapa 1 (a) falha.
- Se o query param `search` não é preservado na URL pelo form submit → Etapa 2 (a) falha.
- Se o service não recebe/usa o `search` → Etapa 2 (b) falha (volta 197).
- Se o `ilike` foi construído errado (e.g. sem `%`) → Etapa 2 (c) falha (0 resultados ou resultados sem o termo).
- Se o empty-state quebra com o novo param → Etapa 3 falha.
- Se os filtros não combinam com AND → Etapa 4 falha.

## Expected counts (verificados pré-teste via curl)

| Termo | Expected total |
|---|---|
| (nenhum) | 197 |
| aquisicao | 15 |
| servico | 8 |
| medicamento | 5 |
| pavimenta | 1 |
| zzzzz | 0 |

Fonte: queries curl em `http://localhost:8000/api/v1/licitacoes?search=…&page_size=1` após `alembic upgrade head`.
