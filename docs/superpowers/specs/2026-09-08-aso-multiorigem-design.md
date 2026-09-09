# ASO multi-origem: OnSafety, OneDrive e entrada manual

**Data:** 2026-09-08
**Status:** desenho aprovado, aguardando plano de implementação
**Contexto:** issue #39 (pull SST OnSafety), PR #52 (etapa 2 mergeada)

## Problema

O resultado do ASO (`resultadoAso`) hoje só pode vir do pull da OnSafety, e
esse campo é um inteiro **sem `enum` e sem `description`** no spec OpenAPI
deles — varredura nas 354 schemas não encontrou nenhum literal
"apto"/"inapto"/"restrição". O mapeamento `{1: apto, 2: inapto, 3:
apto_restricoes}` em `onsafety_sync.py` é assumido, e por isso **o rollout do
pull de ASO em produção está travado**: é dado de saúde exibido ao RH, e um
rótulo invertido é pior do que dado ausente.

Duas origens alternativas resolvem isso sem depender do fornecedor:

1. O ASO chega como documento no **OneDrive**, onde o pessoal já deposita
   documentação.
2. Uma pessoa preenche o ASO **no Motor Central**, e o dado é persistido de
   volta no OneDrive e na OnSafety.

## Estado atual (o que já existe)

| Peça | Onde | Estado |
|---|---|---|
| Entrada manual dos campos de ASO | `dp_sesmt/schemas.py` (create/update do funcionário) | existe |
| Upload para o OneDrive | `integrations/onedrive/client.py` (`upload`, simples + chunked) | existe |
| Escrita de exame na OnSafety | `POST /v2/exames_ocupacionais` | existe **na API deles** |
| Upload do PDF do ASO na OnSafety | `POST /v2/exames_ocupacionais/{idTrabalhador}/aso_pdf` (multipart `arquivo`) | existe **na API deles** |
| Sync do OneDrive | `onedrive_sync/parser.py` + `service.py` | existe, **dirigido por nome de arquivo** (não lê conteúdo) |
| Regra "ASO nunca regride" | `dp_sesmt/onsafety_sync.py::pull_asos` | existe, embutida no laço |

### Duas lacunas estruturais

1. **`ASO` não é um tipo de documento.** `DOC_EMP_TIPOS_VALIDOS` tem 21 tipos
   (`FICHA_EPI`, `NR35`, `TOXICOLOGICO`…) e `ASO` não está entre eles. O ASO
   existe só como três colunas em `Employee` (`aso_data`, `aso_validade`,
   `aso_resultado`). Consequência: o sync do OneDrive **rejeitaria**
   `dp/42/ASO_apto_2026-03-10.pdf` como tipo desconhecido, e o PDF do ASO não
   tem onde morar.
2. **`aso_resultado` é string livre.** O único registro de que os valores são
   `apto`/`inapto`/`apto_restricoes` é um comentário em `models.py:126`. O pull
   grava `str(valor)[:16]` para inteiros desconhecidos e a entrada manual
   aceita qualquer string.

## Decisões de desenho

### D1 — ASO vira documento de primeira classe

Cria `DOC_EMP_ASO`. O ASO passa a ser row em `dp_employee_documents`, com
`source` distinguindo a origem (`onsafety` / `onedrive` / `manual`),
exatamente como `FICHA_EPI` e as NRs já funcionam.

As colunas `aso_*` de `Employee` viram uma **projeção do ASO vigente**,
recalculada quando um documento de ASO chega. **Os alertas A.2 (cron 08h05,
em produção) continuam lendo as mesmas colunas — zero mudança de
comportamento.** O `diagnostico/runner.py` também segue lendo
`emp.aso_validade` como já faz.

Alternativas descartadas:

- **Colunas novas em `Employee`** (`aso_source`, `aso_anexo_path`): menor, mas
  não guarda histórico de exames, e o ASO seria o único artefato de SST sem
  row de documento.
- **Tabela dedicada `dp_aso`**: modelo mais limpo, mas duplica o que
  `dp_employee_documents` já faz e cria um segundo caso especial no
  diagnóstico (que já trata ASO à parte).

### D2 — Vocabulário de resultado vira código

`ASO_RESULTADOS_VALIDOS: frozenset = {"apto", "inapto", "apto_restricoes"}`
em `dp_sesmt/models.py`, com labels para a UI.

É o mesmo vocabulário usado em três lugares: parsing da convenção de nome,
validação da entrada manual, e o mapeamento para o inteiro da OnSafety na
fase 4.

Valor desconhecido vindo do pull continua sendo preservado como string (não
inventamos semântica) e contado em `aso_resultado_desconhecido`.

### D3 — Convenção de nome no OneDrive

```
dp/<employee_id>/ASO_<resultado>_<data_aso>_ate_<validade>.pdf
```

Exemplo: `dp/42/ASO_apto_2026-03-10_ate_2027-03-10.pdf`

Três regras de rigor, deliberadas — a convenção de nome coloca dado de saúde
na mão da digitação de quem sobe o arquivo, então o erro precisa ser
**barulhento**, nunca silencioso:

1. **Resultado casa exatamente** com `ASO_RESULTADOS_VALIDOS`
   (case-insensitive). Qualquer outra coisa (`ok`, `Apto!`, `apt0`) **não vira
   documento**: entra em `summary_json["errors"]` com o path do arquivo. Nunca
   cai em valor padrão.
2. **Validade é obrigatória.** Mesmo raciocínio já aplicado às NRs no PR #52:
   sem validade, o diagnóstico lê `validade=None` como "perene → conforme" e um
   ASO vencido apareceria como **OK**. Arquivo sem `_ate_` é erro, não
   documento sem prazo.
3. **Datas em ISO** (`YYYY-MM-DD`). Formato brasileiro vira erro, não
   interpretação — `10/03/2026` é ambíguo entre dia e mês, e adivinhar errado
   inverte a validade do exame.

### D4 — Precedência num lugar só

Módulo novo `dp_sesmt/aso.py`, com uma responsabilidade:

```python
async def recompute_aso_vigente(db: AsyncSession, employee: Employee) -> None
```

Lê todos os documentos `DOC_EMP_ASO` do funcionário e projeta o vigente nas
colunas `aso_*`. **Todas** as três origens chamam essa função — pull da
OnSafety, sync do OneDrive e entrada manual.

Regra de precedência:

1. **Data do ASO mais recente vence.**
2. **Empate de data desempata por origem:** `manual` > `onedrive` > `onsafety`
   > `legado`. A afirmação de uma pessoa da Primor vale mais que o espelho de um
   sistema de terceiro.
3. **Sem nenhum documento de ASO, a projeção não escreve nada** — não zera
   coluna. Ver D6.

Benefício colateral: o `pull_asos` de hoje carrega a regra "ASO nunca regride"
embutida no laço de itens. Com a projeção, ele passa a só gravar o documento e
a regra sai do laço — uma responsabilidade a menos numa função que hoje faz
matching, log LGPD, parsing de data e decisão de sobrescrita.

### D5 — Fluxo da entrada manual

```
pessoa preenche ASO + anexa PDF no Motor Central
  → upload do PDF para o OneDrive (OneDriveClient.upload, path da convenção D3)
  → cria documento DOC_EMP_ASO com source="manual" e onedrive_item_id
  → recompute_aso_vigente()
  → enfileira push para a OnSafety (fase 4)
```

O push fica atrás de duas travas independentes: o `ONSAFETY_ALLOW_PROD_WRITE`
que já existe, e a confirmação do enum. Enquanto o enum não vier, o push não é
ligado e **todo o resto do fluxo funciona**.

### D6 — Backfill: a projeção não pode apagar o que já existe

Há funcionários com `aso_*` preenchido hoje (de pulls anteriores e de entrada
manual). Se a projeção recalculasse a partir dos documentos e não houvesse
nenhum documento de ASO ainda, ela **zeraria esses campos** — e os alertas A.2
em produção parariam de disparar para essas pessoas, em silêncio.

Duas defesas, as duas necessárias:

1. **Migration de backfill na fase 1**: para cada funcionário com `aso_data`
   não nulo, cria um `DOC_EMP_ASO` com `source="legado"` a partir das colunas
   atuais. É o histórico que já temos, preservado.
2. **A projeção nunca escreve quando não há documento algum.** Ausência de
   documento significa "não sei", não "não tem".

## Faseamento

| Fase | Entrega | Depende de |
|---|---|---|
| 1 | `DOC_EMP_ASO` + `ASO_RESULTADOS_VALIDOS` + `aso.py` (projeção) + **migration de backfill (D6)** + `pull_asos` refatorado para usá-la | — |
| 2 | Convenção de nome no OneDrive → documento de ASO | fase 1 |
| 3 | Entrada manual + upload do PDF para o OneDrive | fase 1 |
| 4 | Push do exame + PDF para a OnSafety | fase 1 + **enum confirmado** |

Cada fase é útil sozinha. As fases 2 e 3 são independentes entre si.

## Dependência externa (bloqueia só a fase 4)

O significado dos inteiros de `resultadoAso` continua desconhecido. **Caminho
mais barato para resolver, sem esperar o suporte:** alguém com acesso à
interface web da OnSafety em homologação cadastra um exame escolhendo "Apto" e
a gente lê o registro pela API para ver qual inteiro voltou. Zero código,
resposta definitiva, e destrava a fase 4 **e** o rollout do pull de ASO em
produção de uma vez.

Bloqueio operacional relacionado: `ONSAFETY_TOKEN` está vazio no ambiente —
todo `GET /v2/*` autenticado volta **403 com corpo vazio**. O token de
homologação segue com a gestão.

## Erros e casos de borda

- **Documento de ASO sem match de funcionário** (OneDrive com `employee_id`
  inexistente): erro no summary, não cria documento. Mesmo princípio do pull.
- **Funcionário sem nenhum documento de ASO**: a projeção deixa as colunas
  `aso_*` nulas. É o estado atual de quem nunca teve ASO — nada muda.
- **Upload no OneDrive falha na entrada manual**: o documento **não** é criado.
  Não queremos row apontando para anexo que não existe. A pessoa vê o erro e
  repete.
- **Push para a OnSafety falha**: não desfaz nada local. O ASO já está no Motor
  Central e no OneDrive; o push é reenfileirável.
- **Valor de resultado desconhecido vindo do pull**: o documento **participa
  normalmente** da projeção (as datas do exame são válidas mesmo quando o
  rótulo do resultado não é reconhecido — descartá-lo projetaria um ASO mais
  antigo, o que seria pior). O que vai para `aso_resultado` é a string crua, e o
  caso é contado em `aso_resultado_desconhecido`. A UI deve exibir valor não
  reconhecido como tal, nunca traduzido para um dos três rótulos.

## Testes

- `recompute_aso_vigente` isolado: cada regra de precedência (data mais
  recente; empate por origem; documento com resultado desconhecido).
- **Sem nenhum documento de ASO, a projeção não altera colunas já preenchidas**
  (D6) — o teste que protege os alertas A.2 de regressão silenciosa.
- Backfill: funcionário com `aso_*` preenchido e sem documento ganha um
  `DOC_EMP_ASO` `source="legado"` com os mesmos valores.
- Parser da convenção: caminho feliz, resultado fora do vocabulário, validade
  ausente, data em formato BR, casing variado.
- Sync do OneDrive: arquivo válido vira documento + projeção; arquivo inválido
  entra em `errors` e **não** cria documento.
- Entrada manual: upload chamado com o path certo; falha de upload não cria
  documento.
- **Regressão dos alertas A.2**: as colunas `aso_*` continuam preenchidas
  exatamente como hoje após um pull, com a suíte existente passando sem
  alteração de expectativa.
- Fase 4: mapeamento resultado → inteiro, e a trava do `ALLOW_PROD_WRITE`.

## Fora de escopo

- OCR/extração do conteúdo do PDF do ASO (foi considerado e descartado: ASO é
  digitalizado e carimbado, a extração erraria e exigiria revisão humana de
  qualquer forma).
- Mudança no comportamento dos alertas A.2.
- Exames toxicológicos (`/v2/exames_toxicologicos`), que já têm tipo de
  documento próprio (`TOXICOLOGICO`) e ficam para outra frente.
