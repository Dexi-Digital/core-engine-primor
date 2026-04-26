# Test Plan — D5 Fase 3 (PR #25): UI de consumo + alerta de manutenção

**Feature under test**: Detail page (`/manutencao/partes-diarias/[id]`) ganhou card "Consumo" + banner amarelo de alerta de manutenção + 2 campos de combustível no form. List page (`/manutencao/partes-diarias`) ganhou coluna de alerta + chip de filtro `?alerta=1` + hidden input no form que preserva o filtro entre submits.

**Branch/PR**: `devin/1777224338-d5-fase3-consumo-ui` → main · PR #25

**Source of approval**: User clicked "Test the app"

**Code paths grounding the plan**:
- `apps/web/src/app/(dashboard)/manutencao/partes-diarias/page.tsx` (list, badge, chip, filter form hidden input)
- `apps/web/src/app/(dashboard)/manutencao/partes-diarias/[id]/page.tsx` (consumo card, banner, fuel fields)
- `apps/api/app/modules/manutencao_frota/service.py:933-939` (250h marker logic: `marco_atual > marco_anterior` onde marco = `horimetro // 250`)

---

## Test data seeded (não testar — pré-condição)

Vehicle TST1A23 + 3 partes diárias, todas `ocr_status=revisado` (entram na query do gatilho 250h):

| ID | Data       | Horímetro | Combustível | km            | Marco 250 | Esperado `alerta_manutencao_preventiva` |
|----|------------|-----------|-------------|---------------|-----------|-----------------------------------------|
| 1  | 2025-04-21 | 0 → 240   | —           | —             | 0 → 0     | `false` (sem predecessora — primeira)   |
| 2  | 2025-04-22 | 240 → 260 | 60 L · R$420 | 1000 → 1080  | 0 → 1     | **`true`** ← cruza marco 250            |
| 3  | 2025-04-23 | 260 → 290 | 90 L · R$630 | 1080 → 1200  | 1 → 1     | `false` (não cruza marco 500)           |

Backend `/consumo` já validado via curl:
- P2: `alerta_manutencao_preventiva: true`, `horas_trabalhadas: 20.00`, `consumo_litros_por_hora: 3.000`, `consumo_km_por_litro: 1.333`, `custo_por_hora: 21.00`, `km_rodados: 80`
- P3: `alerta_manutencao_preventiva: false`, `horas_trabalhadas: 30.00`, `consumo_litros_por_hora: 3.000`, `consumo_km_por_litro: 1.333`, `custo_por_hora: 21.00`, `km_rodados: 120`

Diferença entre cenários: P2 cruza marco, P3 não. Isso permite que **assertions sejam diferentes para registros que parecem similares** — se a UI estivesse mostrando alerta sempre, ou nunca, ou comparando o campo errado, os dois renderizariam igual.

---

## Flow 1 — Detail page mostra consumo + banner SOMENTE para a parte que cruza marco

### T1.1 — Detail page de P2 (CRUZA marco 250)

**Action**: navegar para `http://localhost:3000/manutencao/partes-diarias/2`

**Pass criteria** (todos devem passar — qualquer falso = código quebrado):

1. Página carrega (HTTP 200), título contém "Parte diária #2"
2. **Banner amarelo** com texto literal **"Alerta de manutenção preventiva."** está visível **antes** do card de Consumo. Background visivelmente amarelo claro (amber-50), borda amarela mais escura (amber-400). Banner contém também a frase "atravessou um múltiplo de 250 horas".
3. Card "Consumo" presente, abaixo do banner.
4. Card mostra **6 metrics labels** literais: "Horas trabalhadas", "Km rodados", "Consumo L/h", "Consumo km/L", "Custo R$/h", "Alerta de manutenção".
5. **Valores concretos** (do backend `/consumo` — quebraria se UI estivesse calculando errado ou usando endpoint diferente):
   - "Horas trabalhadas": **`20.00 h`**
   - "Km rodados": **`80 km`**
   - "Consumo L/h": **`3.000 L/h`**
   - "Consumo km/L": **`1.333 km/L`**
   - "Custo R$/h": **`R$ 21,00 / h`** (ou equivalente formatação R$ ptBR)
6. O metric "Alerta de manutenção" exibe valor **"Sim"** (ou equivalente concreto, não "—").

**Why this is adversarial**:
- Se o `consumo` viesse `null`, o card mostraria "Sem dados suficientes" em vez dos 6 valores → falha visível
- Se a UI estivesse exibindo alerta para tudo, P3 também teria banner → coberto em T1.2
- Se a UI estivesse usando o endpoint errado (ex: `/parte-diaria/2` em vez de `/consumo`), os valores seriam outros (ou não existiriam)

### T1.2 — Detail page de P3 (NÃO cruza)

**Action**: navegar para `http://localhost:3000/manutencao/partes-diarias/3`

**Pass criteria**:

1. Página carrega, título contém "Parte diária #3"
2. **Banner amarelo NÃO está presente** — não existe nenhum elemento com classe `bg-amber-50` ou texto "Alerta de manutenção preventiva." no DOM
3. Card "Consumo" continua visível com metrics:
   - "Horas trabalhadas": **`30.00 h`**
   - "Km rodados": **`120 km`**
   - "Consumo L/h": **`3.000 L/h`**
   - "Consumo km/L": **`1.333 km/L`**
   - "Custo R$/h": **`R$ 21,00 / h`**
4. Metric "Alerta de manutenção" exibe valor **"Não"** ou **"—"** (não "Sim")

**Why this is adversarial**:
- Comparado com T1.1: mesmo veículo, mesmo motor, ranges parecidos (consumo L/h = 3.000 nos dois). Único distinguidor é o cruzamento de marco. Se a UI estivesse hardcoded "alerta=true" ou comparando coluna errada, T1.1 e T1.2 ficariam iguais → falha visível.

### T1.3 — Form de combustível persiste após PATCH

**Action**:
1. Em `/manutencao/partes-diarias/2`, scroll até o form de edição
2. Campo "Combustível (litros)" deve mostrar valor atual `60` (defaultValue). Alterar para `75`
3. Campo "Custo combustível (R$)" deve mostrar `420.00`. Alterar para `525`
4. Click "Salvar" / "Atualizar"
5. Aguardar redirect/refresh
6. Recarregar página (`F5`)

**Pass criteria**:

1. Após salvar, **toast/banner de sucesso** OU URL/page sem erro
2. Após reload (cold), campo "Combustível (litros)" exibe `defaultValue=75`
3. Campo "Custo combustível (R$)" exibe `defaultValue=525`
4. Card "Consumo" recalculado: `Consumo L/h = 75/20 = 3.750`, `Custo R$/h = 525/20 = 26.25` (não os valores antigos `3.000` / `21.00`)

**Why this is adversarial**:
- Se PATCH não persistir os campos novos, o defaultValue após reload mostraria os valores antigos → falha visível
- Se a UI estivesse mostrando consumo cacheado (Next.js fetch cache não revalidado), os valores no card ficariam os antigos → coberto pelo recálculo no card

---

## Flow 2 — List page mostra badge + chip + filter form preserva alerta

### T2.1 — Baseline da lista

**Action**: navegar para `http://localhost:3000/manutencao/partes-diarias`

**Pass criteria**:

1. Página carrega (HTTP 200), header "Lançamentos (3)" — total = 3 partes
2. **Coluna "Manutenção"** presente no header da tabela (ou similar — texto literal varia, mas badge column distinta)
3. **Badge `⚠ 250h`** (background amber-100, texto amber-800) aparece **somente na linha de P2** (id=2), NÃO em P1 nem P3
4. Linhas P1 e P3: célula da coluna mostra horas trabalhadas em texto cinza (`240.00 h` / `30.00 h`) ou `—`
5. **Chip no header** com texto literal **"1 com alerta de manutenção · filtrar"** está visível, com classe `bg-amber-50` (não-ativo) — link `<a>` clicável

**Why this is adversarial**:
- Se a UI estivesse mostrando badge sempre ou nunca, o teste detectaria (1 ≠ 0 ≠ 3)
- Se `consumos` map estivesse desalinhado (key vs id), o badge poderia aparecer na linha errada — visível
- O número "1" no chip vem de `totalAlertas = list.items.filter(...).length` — quebrar a função reduz para 0 ou 3

### T2.2 — Click no chip ativa filtro `?alerta=1`

**Action**: na lista, click no chip "1 com alerta de manutenção · filtrar"

**Pass criteria**:

1. URL muda para `http://localhost:3000/manutencao/partes-diarias?alerta=1`
2. Tabela mostra **somente 1 linha** (P2)
3. Chip muda visualmente: `bg-amber-100` (mais escuro), texto "**mostrando 1 com alerta · limpar filtro**" (não mais "filtrar")
4. Header continua "Lançamentos (3)" — `total` é da query do backend (sem filtrar `alerta`), e `itensVisiveis` filtrado client-side

**Why this is adversarial**:
- Se o handler `apenasComAlerta` estivesse hardcoded `false`, click não mudaria nada
- Se `itensVisiveis` não filtrasse, mostraria 3 linhas com `?alerta=1` ativo
- O texto do chip muda visivelmente — mistakes sticky aparecem

### T2.3 — Filter form preserva `?alerta=1` (regressão do commit 93c55ad)

**Action**:
1. Estando em `/manutencao/partes-diarias?alerta=1` (P2 visível, chip ativo)
2. No card "Filtros", inserir `TST1A23` no campo "Placa"
3. Click no botão "Filtrar"

**Pass criteria**:

1. URL final é `/manutencao/partes-diarias?placa=TST1A23&alerta=1` (ou `?alerta=1&placa=TST1A23` — ordem de query não importa, mas **`alerta=1` DEVE estar presente**)
2. Tabela continua mostrando somente P2 (1 linha)
3. Chip continua "mostrando 1 com alerta · limpar filtro" — filtro ainda ativo

**Why this is adversarial — esse é exatamente o bug fixed em 93c55ad**:
- Se o hidden input `<input type="hidden" name="alerta" value="1" />` fosse removido, o GET submit dropparia `alerta` da URL, chip voltaria pro estado inativo, e mostraria 3 linhas (todas com placa TST1A23). **Bug visível em 1 segundo.**
- Se o filtro `placa` não fosse preservado quando vinha do header chip, o teste também detectaria (não chega aqui)

### T2.4 — Toggle visível quando `apenasComAlerta=true && totalAlertas=0` (regressão do commit adac559 — bug fixed hoje)

**Action**:
1. Estando em `/manutencao/partes-diarias?alerta=1`
2. No card "Filtros", limpar campo "Placa" e inserir uma placa **inexistente** `XXX9X99`
3. Click "Filtrar"

**Pass criteria**:

1. URL final contém **`alerta=1` E `placa=XXX9X99`**
2. Tabela mostra "Nenhuma parte com alerta de manutenção nesse filtro." (estado vazio)
3. **Chip continua visível no header** com texto literal **"filtro de alerta ativo · limpar filtro"** (NÃO esconde o toggle)
4. Click no chip → URL volta para `?placa=XXX9X99` (sem `alerta=1`), tabela ainda vazia (mas SEM filtro de alerta ativo)

**Why this is adversarial — esse é o bug que descobrimos hoje (Devin Review finding 2)**:
- Antes do fix em adac559, condição era `list && totalAlertas > 0`. Quando filter zerou o resultado, `totalAlertas` virou 0, chip sumia, hidden input continuava propagando `alerta=1` em todo submit → usuário **preso**.
- Depois do fix, condição virou `list && (totalAlertas > 0 || apenasComAlerta)`, com texto especial pro caso count=0. Esse teste falha sem o fix.

---

## Out of scope (não testado)

- Login flow / session — pré-condição
- PWA mobile (`/m/parte-diaria`) — testado no PR #24
- OCR pipeline — feature anterior
- Server actions de delete — não tocadas neste PR
- Fluxo offline (PWA queue) — não tocado neste PR

## Test execution

Todos os passos via UI no Chrome (não devtools console). Recording inicia em T1.1 e termina em T2.4. Annotations em cada test_start + assertion.

Se um test falhar, gravo evidência (screenshot) e mantenho o recording rolando — não interrompo os outros tests. Falhas reportadas como `failed` no resumo final via comment do GitHub.
