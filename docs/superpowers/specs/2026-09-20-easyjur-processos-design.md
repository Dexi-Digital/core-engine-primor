# EasyJur — pull do contencioso trabalhista

**Data:** 2026-09-20
**Estado:** desenho aprovado, aguardando plano de implementação
**Adapter existente:** `app/integrations/easyjur/client.py` (só login)

## Por que este spec existe

O registro anterior sobre o EasyJur estava errado em três pontos. A
exploração da conta real em 19/09/2026 corrigiu os três, e é dessa
medição que sai todo o desenho abaixo.

### 1. A credencial funciona

`docs/integrations.md` dizia "aguardando credencial válida" e o commit
`f5dc8ea` registrou a hipótese de que a conta entrava por SSO do
Google. Ambos vieram da senha de `rodrigo@construtorazag.com.br`, que
foi recusada.

A conta de integração é outra: **`sistemas@primorsolucoes.srv.br`**,
e autentica de primeira por email e senha. Não há SSO no caminho, não
há captcha, não há segundo fator. A hipótese do Google está
descartada.

### 2. Não são 0 processos. São 453

A medição de 17/09 concluiu "0 processos na base" e a tela
`/juridico` herdou essa nota. **A conclusão era artefato da forma de
medir.**

`ajax_processos_lista.php` só lista quando recebe
`acao_listagem=enviar`. Sem esse parâmetro ele devolve HTTP 200 com
`0 Registros Encontrados` — uma resposta bem-formada e indistinguível
de base vazia. Com o parâmetro, devolve **453**.

Este é o modo de falha mais perigoso desta integração e a razão de o
parâmetro ir comentado no código: não dá erro, devolve "vazio" com
cara de verdade.

### 3. Contratos judicializados não existem no EasyJur

O módulo de contratos tem **1 registro, e é um template em branco** —
o título é `"CONTRATO PARTICULAR DE PRESTAÇÃO DE SERVIÇOS QUE ENTRE SI
FAZEM CONSTRUTORA ZAG LTDA E NOME DO C"`, com o campo do cliente nunca
preenchido.

Verificado pelo render server-side de `?pg=contrato_lista`,
deliberadamente **não** pelo AJAX — o AJAX de contratos devolve 0, e
confiar nele seria repetir o erro do item 2.

Consequência: o roadmap #12 ("Relatórios EasyJur para contratos
judicializados") **não é implementável como escrito**. O EasyJur da
Primor não é um sistema de contratos; é o sistema do contencioso
trabalhista.

## O que existe de fato

| Módulo | Endpoint | Registros |
|---|---|---|
| Processos | `POST /sgr/advogados/scripts/processos/ajax_processos_lista.php` + `acao_listagem=enviar` | 453 |
| Andamentos | `POST /sgr/advogados/scripts/andamentos/ajax_andamento_lista.php` | 12.109 |
| Pessoas | `POST /sgr/advogados/scripts/pessoas/ajax_pessoas_lista.php` | 1.974 |
| Contratos | render de `GET /sgr/index.php?pg=contrato_lista` | 1 (vazio) |
| Export CSV | `POST /sgr/advogados/scripts/andamentos/exports/export_andamentos.php` | 12.121 em 1 requisição |

Nenhum devolve JSON. Todos devolvem **fragmento HTML** destinado a
`$('#alvo').html(...)`. Paginação: 50 por página, parâmetro `page`.

O export CSV aceita `campos[]` com 30 colunas selecionáveis e junta
processo + andamento na mesma linha. Ele tem uma armadilha própria,
documentada na seção "O CSV".

Perfil dos 453:

```
área:      Trabalhista 242 · Cível 59
tribunal:  TRT03 296 · TJMG 109 · TJSP 5 · TRF1 3 · TRT08 3 · TRF6 2
instância: 1º Grau 430 · 2º Grau 21 · 3º Grau 2
tipo:      judicial 453/453
partes:    PRIMOR SOLUCOES · CONSTRUTORA ZAG · GUAXIMA ENGENHARIA
```

### Cobertura por campo — o número que governa o desenho

| Campo | Preenchidos | % |
|---|---|---|
| `numero_cnj` | 453/453 | 100% |
| `tribunal` | 425/453 | 93% |
| `comarca` | 392/453 | 86% |
| `area` | 301/453 | 66% |
| `tipo_acao` | 72/453 | 15% |
| `risco` | 54/453 | 11% |
| `resultado` | 39/453 | 8% |
| `fase_atual` | 29/453 | 6% |

Os quatro últimos são **esparsos**, e isso não é defeito do pull — é o
estado do cadastro no escritório.

## Escopo

**Dentro:** processos, andamentos, vínculo com obra, pull idempotente,
log de sincronização.

**Fora, de propósito:** pessoas (1.974 — sem consumidor definido);
contratos (1 registro vazio); tela web (segunda rodada, sobre dado que
já existirá); escrita de volta no EasyJur (o pull é read-only).

## O CSV: rápido, e quebrado de duas formas

O export resolveria o volume — 12.121 linhas numa requisição contra
243 páginas de HTML. Mas ele tem três defeitos, todos medidos em
19/09/2026 e todos do tipo que não levanta erro.

**Primeiro: o export lê o filtro da sessão PHP.** Chamado logo após o
login, devolve HTTP 200 com o cabeçalho e **zero linhas**. Só depois
de um `POST ajax_andamento_lista.php` com `pesquisa=enviar` na mesma
sessão ele devolve as 12.121. Mesma família de armadilha do
`acao_listagem`: sucesso aparente, resultado vazio.

**Segundo: cabeçalho e dados estão desalinhados.** O cabeçalho tem 30
colunas; **todas as 12.121 linhas têm 29**. Da posição 1 à 8 os
valores ficam deslocados em relação ao rótulo:

| Posição | Rótulo no cabeçalho | Conteúdo real |
|---|---|---|
| 1 | `Processo` | Cliente |
| 2 | `Cliente` | Contrário |
| 3 | `Contrario` | Advogado |
| 4 | `Advogado` | Grupos |
| 7 | `Titulo Processo` | Tipo de Ação |
| 8 | `Tipo de Acao` | **número CNJ** |
| ≥9 | — | alinhado novamente |

Verificado por forma: o CNJ casa com `\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}`
na posição 8 em **12.121 de 12.121** linhas, nunca na posição 1. E
cruzado campo a campo, num processo trabalhista do TRT03 tomado como
amostra, contra o HTML do mesmo processo — que confirma o
deslocamento. (O número do processo fica fora deste documento pela
mesma política das fixtures; a conferência se refaz em qualquer linha.)

Ler esse CSV pelo nome da coluna produz dado errado com cara de certo:
`Cliente` devolveria o nome de quem processa a empresa, e `Tipo de
Acao` devolveria um número de processo. Numa reclamação trabalhista,
trocar cliente e contrário não é detalhe.

**Terceiro defeito, de encoding:** 6.955 células trazem `?` no lugar
de acento — `A??o Rescis?ria`, `Guaxup?`, `Desist?ncia`, `Ibi?`. A
perda é do export deles, não da nossa decodificação: os mesmos campos
vêm corretos no HTML (`Januária` no HTML, `Janu?ria` no CSV).

### Como isso decide a arquitetura

A corrupção não é uniforme — ela se concentra em campos **do
processo**:

| Campo | Células corrompidas |
|---|---|
| `tipo_acao` | 3.983 |
| `comarca` | 2.908 |
| `resultado` | 60 |
| `descricao` (andamento) | **4** |

Ou seja: o CSV é ruim exatamente nos campos que o pull de processos já
traz corretos pelo HTML, e é bom nos campos de andamento, que são os
únicos que existem em volume grande.

Daí o desenho híbrido: **HTML para processos** (453 linhas, 10
páginas, barato e correto) e **CSV para andamentos**, consumindo só as
colunas de andamento e ignorando as de processo.

## Arquitetura

Três camadas, na fronteira em que cada uma pode ser testada sozinha.

### Camada 1 — adapter (`integrations/easyjur/client.py`)

Estende o client existente, preservando a proteção contra bloqueio de
conta (`_MARGEM_SEGURANCA`, `health_check` que não autentica).

```python
async def listar_processos(self, page: int = 1) -> str        # HTML cru
async def exportar_andamentos_csv(self) -> bytes              # CSV cru
```

Não há `listar_andamentos` público: o HTML de andamentos entra só como
a chamada interna que arma o filtro da sessão antes do export, nunca
como caminho de ingestão. Duas formas de ler a mesma coisa viram duas
formas de ler diferente.

O adapter devolve bytes/texto cru e **não interpreta**. Motivo: manter
a única camada que toca a rede livre da única camada que muda quando o
layout deles muda.

Dois parâmetros de sessão ficam fixos no adapter, cada um com
comentário citando a medição de 19/09/2026 e o que acontece sem ele:

- `acao_listagem="enviar"` no POST de processos;
- `exportar_andamentos_csv` dispara **primeiro** o
  `ajax_andamento_lista.php` com `pesquisa=enviar`, porque o export lê
  o filtro da sessão e sem isso devolve só o cabeçalho.

Ambos existem pelo mesmo motivo: sem eles a resposta é um sucesso
vazio, não um erro.

### Camada 2 — parser (`integrations/easyjur/parser.py`, novo)

Funções puras, sem rede, sem banco:

```python
def parse_processos(html: str) -> list[ProcessoBruto]
def parse_andamentos_csv(bruto: bytes) -> list[AndamentoBruto]
def total_registros(html: str) -> int | None
```

`parse_andamentos_csv` lê por **posição fixa**, e o cabeçalho do
arquivo é deliberadamente ignorado como fonte de significado — ele
está errado. O mapa posicional fica numa constante única, ao lado da
tabela de deslocamento deste spec.

Antes de mapear, a função **valida o formato que ela assume**:
cabeçalho com 30 colunas, linhas com 29, e CNJ na posição 8. Se o
EasyJur corrigir o bug, essas condições quebram e o pull **falha alto**
em vez de reinterpretar tudo deslocado em silêncio. Um export
consertado lido com mapa de export quebrado produziria exatamente o
estrago que o mapa existe para evitar.

`total_registros` lê o rodapé `N Registros Encontrados` e existe para
o pull **conferir o que coletou contra o que o EasyJur declarou**. Se
divergir, o log registra a divergência em vez de aceitar em silêncio —
é a defesa direta contra o modo de falha do item 2.

Campo ausente vira `None`, nunca string vazia: é a diferença entre
"o escritório não preencheu" e "o parser não achou".

### Camada 3 — módulo (`app/modules/juridico/`)

Segue o `app/modules/ponto/` — mesmo formato de tabela, mesmo log de
sync, mesma postura sobre vínculo não resolvido.

**`juridico_processos`**
Chave natural `easyjur_id` (ex.: `14508921`), unique. `numero_cnj`
indexado. Campos de alta cobertura como colunas próprias; os esparsos
também como colunas, nullable. `codigo_obra` e `obra_id` nullable.

**`juridico_andamentos`**
Chave natural `easyjur_id` (ex.: `356330572`), unique. FK para
`juridico_processos` quando o CNJ casar; **nullable quando não casar**
— o feed de andamentos referencia processos por número, e nem todo
número citado está entre os 453.

**`juridico_sync_log`**
Unique em `(source, janela)` com `source ∈ {beat, manual}`. Precedente
explícito: TOTVS, ponto, e o bug do `dispatch_contrato_alerts_endpoint`
— sem o `source` na chave, um "sincronizar agora" manual queima a
janela do job agendado.

**`matching.py`**
Extrai o código da obra da tag de grupo do processo.

## Vínculo com obra

Processos carregam tags de grupo. 32 dos 453 têm a tag `OBRAS`,
seguida de `<Localidade> - z<código>`.

Os 5 códigos encontrados batem **integralmente** com os locais de
trabalho que o Tangerino já ingeriu, cidade inclusive:

| EasyJur | `ponto_locais_trabalho` |
|---|---|
| `Cachoeira do Campo - z202` | `Obra 202 - Cachoeira do Campo` |
| `Carangola - Z218` | `Obra 218 - Carangola` |
| `Manhumirim - Z228` | `Obra 228 - Manhumirim` |
| `Januária - z231` | `Obra 231 Januária` |
| `Abaete - z246` | `OBRA 246 - ABAETÉ` |

Regra: remover o `z` inicial (marcador de ZAG) e casar o número com
`obras_obra.codigo`.

**Cuidado que a regra precisa ter.** O `z` do EasyJur é prefixo
descartável, mas nem toda letra é: o Tangerino tem `C008`, `C034`,
`C043`, onde a letra **faz parte** do código. A regra só descarta `z`
inicial, e só no formato do EasyJur.

**Não inventamos obra.** Código sem obra cadastrada grava `codigo_obra`
preenchido e `obra_id` nulo, e aparece como pendência. Vale também
para os 421 processos sem tag nenhuma — ausência de tag é ausência de
informação, não erro de match. (`obras_obra` está com 0 linhas no
ambiente local; o vínculo é por código e resolve quando as obras forem
cadastradas.)

## Campos esparsos: a regra de apresentação

Nenhum endpoint agrega `risco`, `fase_atual`, `resultado` ou
`tipo_acao` sem devolver o denominador junto.

"Risco remoto em 96% dos processos" é falso — o verdadeiro é "52 de 54
preenchidos, num universo de 453". Uma resposta que omite o
denominador transforma 11% de cobertura em uma afirmação sobre a
carteira inteira.

Mesmo modo de falha já decidido no `sem_leitura` dos planos de
manutenção (PR #71): ausência de dado nunca vira o valor tranquilizador.

## Idempotência e volume

Upsert por `easyjur_id` (`ON CONFLICT DO UPDATE`). Rodar o pull N
vezes não duplica.

Volume por execução completa: **10 páginas de HTML** para os 453
processos, e **1 requisição de CSV** (~6,6 MB) para os 12.121
andamentos — contra as 243 páginas que o caminho por HTML exigiria.

**Janela por data: não funciona como esperado.** `dteDataInicial` e
`dteDataFinal` existem no formulário de busca avançada, mas enviá-los
sozinhos **não filtra** — medido em 19/09/2026, a mesma consulta com e
sem janela devolveu 12.121 registros nos dois casos. Provavelmente
depende de `tipo_data` (qual data filtrar), ainda não determinado.

Até isso ser resolvido, o pull de andamentos é **carga cheia**, e a
incrementalidade vem do upsert por `easyjur_id`, não de filtro na
origem. É mais tráfego do que o necessário, mas é correto; filtro
incremental que silenciosamente não filtra seria pior, porque daria a
impressão de janela pequena enquanto relê tudo.

Nota de medição: entre duas consultas da mesma sessão o total subiu de
12.109 para 12.121. A base é viva e cresce durante o pull — o
comparativo com `total_registros` precisa tolerar essa deriva em vez de
tratá-la como erro.

O login é **uma vez por execução** — a sessão `PHPSESSID` serve todas
as páginas. Reautenticar por página gastaria tentativa à toa contra um
sistema que bloqueia em 5 erros.

## Testes

Nenhum teste toca o EasyJur real — a regra já vigente no
`test_easyjur_client.py`, pelo risco de bloqueio da conta.

Fixtures de HTML **e de CSV** são **anonimizadas**: estrutura real do
EasyJur, nomes fictícios, CPFs inválidos, dígitos do CNJ alterados —
preservando a forma do CNJ, porque a validação posicional depende
dela. Processo trabalhista identifica quem processou a empresa, e
histórico de git é permanente.

A anonimização **não corrige** o deslocamento nem os acentos
corrompidos: a fixture precisa reproduzir os defeitos, senão testa um
arquivo que não existe.

Cobertura mínima:

*Parser de processos (HTML)*
- extrai os campos de alta cobertura do HTML real anonimizado
- campo ausente vira `None`, não `""`
- `total_registros` lê o rodapé
- **divergência entre coletado e declarado é reportada**
- `acao_listagem` ausente → o pull reconhece "0 com cara de vazio"

*Parser de andamentos (CSV)*
- mapa posicional entrega Cliente/Contrário nos campos certos —
  o teste que impede a troca de polo numa ação trabalhista
- cabeçalho com 30 colunas e linhas com 29 é aceito
- **cabeçalho consertado (30/30) FALHA alto**, não reinterpreta
- CNJ fora da posição 8 falha
- célula com acento corrompido é preservada como veio, não "consertada"
  por adivinhação
- export sem `pesquisa=enviar` → só cabeçalho é reconhecido como
  sessão sem filtro, não como base vazia

*Vínculo e persistência*
- `z228 → 228`; `C008` preservado; sem tag → `None`
- upsert duas vezes não duplica
- `(source, janela)` impede manual de queimar janela do beat

## Pendências para o cliente

1. **`resultado` em 8% e `fase_atual` em 6%** — o escritório preenche
   por exceção ou o cadastro está defasado? Muda se o campo vira
   indicador ou só detalhe.
2. **421 processos sem tag de obra** — a tag é usada só em alguns
   casos, ou há outro caminho processo→obra?
3. **Roadmap #12** precisa ser reescrito: não há contratos
   judicializados no EasyJur.

## Pendências para o suporte do EasyJur

Uma rodada só, pela regra já firmada no projeto (`resultadoAso` e
validade de EPI custaram duas rodadas cada por perguntar aos pedaços):

1. **Export CSV de andamentos entrega 30 cabeçalhos para 29 colunas**,
   com os valores deslocados entre as posições 1 e 8 — o número do
   processo sai sob o rótulo `Tipo de Acao`. Reproduzível em 12.121 de
   12.121 linhas.
2. **Acentos viram `?` no CSV** (`Janu?ria`, `A??o Rescis?ria`) nos
   mesmos campos que a interface mostra corretos. 6.955 células.
3. **`dteDataInicial`/`dteDataFinal` não filtram** quando enviados sem
   `tipo_data`. Qual o valor esperado de `tipo_data` para janela por
   data de andamento?
4. Existe **API REST** no plano contratado? Todo o desenho acima
   depende de raspar HTML e CSV de uma sessão de navegador; uma API
   tornaria os itens 1 a 3 irrelevantes.
