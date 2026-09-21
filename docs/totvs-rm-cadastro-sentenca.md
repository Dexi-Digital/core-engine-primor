# TOTVS RM — cadastro da sentença `MOTOR.FLAN.01`

Passo a passo para cadastrar no RM a consulta SQL que o Motor Central usa
para ler os lançamentos financeiros. Escrito em 21/09/2026, depois de a
TOTVS responder as quatro perguntas do ticket 30268517 (ver
`integrations.md`, seção TOTVS RM).

Quem faz: alguém com acesso ao RM e permissão em **Gestão → Consultas SQL**
(Lorrayne ou a TI da Primor). O Motor Central não tem como cadastrar por
fora — a sentença vive dentro do RM, e é isso que garante que a integração
é somente leitura.

## 1. Onde

Abra o RM no módulo **Gestão Financeira** e vá em
**Gestão → Consultas SQL → Incluir** (`Ctrl+Ins`).
Em versões mais novas o mesmo cadastro aparece como
**BI → Criação de consultas SQL** — é a mesma tela.

Faça **primeiro no DEV** (`12.1.2510.170`) e só depois no PROD
(`12.1.2510.136`). O ambiente de desenvolvimento existe para isso e não
consome licença de produção.

> **O módulo em que você está logada importa.** O RM registra a sentença no
> contexto do módulo ativo. No Financeiro ela fica no sistema `F`, que é o que
> o adapter envia. Cadastrada de outro módulo, a chamada falha.

## 2. O que preencher

| Campo na tela | Valor | Por quê |
|---|---|---|
| **Código** | `MOTOR.FLAN.01` | É o `codSentenca` que o adapter envia; tem de bater letra por letra |
| **Título** | `Motor Central – lançamentos FLAN com baixas (incremental)` | Só identificação |
| **Visível a todas Coligadas** | **marcar** | A sentença sai sem filtro de coligada de propósito; quem restringe é o perfil do usuário de integração |
| **Banco de Dados** | em branco | É o banco do próprio RM |
| Disponível para Visões / Filtros / Relatórios / RM Portal | **desmarcar todos** | Não é para uso interno no RM; menos superfície, menos risco |

Anote a **coligada** em que a sentença foi criada: é o `codColigada` da
chamada.

## 3. A sentença

```sql
SELECT
    FLAN.CODCOLIGADA,
    FLAN.CODFILIAL,
    FLAN.IDLAN,
    FLAN.VALORORIGINAL,
    BX.VALORBAIXADO,
    FLAN.DATAVENCIMENTO,
    FLAN.DATAEMISSAO,
    FLAN.STATUSLAN,
    FLAN.CODCFO,
    FCFO.NOME    AS NOMECFO,
    FCFO.CGCCFO
FROM FLAN (NOLOCK)
LEFT JOIN FCFO (NOLOCK)
       ON FCFO.CODCOLIGADA = FLAN.CODCOLIGADA
      AND FCFO.CODCFO      = FLAN.CODCFO
LEFT JOIN (
    SELECT CODCOLIGADA, IDLAN, SUM(VALORBAIXADO) AS VALORBAIXADO
    FROM FLANBAIXA (NOLOCK)
    GROUP BY CODCOLIGADA, IDLAN
) BX
       ON BX.CODCOLIGADA = FLAN.CODCOLIGADA
      AND BX.IDLAN       = FLAN.IDLAN
WHERE FLAN.RECMODIFIEDON >= :DATAINICIAL
  AND FLAN.RECMODIFIEDON <  :DATAFINAL
```

O que não pode mudar, porque o adapter depende:

- Os aliases **`NOMECFO`** e **`VALORBAIXADO`** — são os nomes que o adapter
  procura no retorno.
- Os parâmetros **`:DATAINICIAL`** e **`:DATAFINAL`**, **nessa ordem**. O RM
  casa parâmetros pela ordem em que aparecem na sentença, e o adapter envia
  `DATAINICIAL=…;DATAFINAL=…`.
- Os dois `LEFT JOIN`. Com `INNER`, lançamento sem contraparte ou sem baixa
  sumiria da extração em silêncio.

O `(NOLOCK)` é recomendação da TOTVS para tabelas grandes em SQL Server; não
muda o resultado.

## 4. Testar dentro do RM (2 minutos)

Isto resolve a única coisa que a documentação **não** diz: o formato da data
no parâmetro. O adapter envia `2026-09-01` (ISO); o RM pode tipar o
parâmetro como *Data* e esperar `01/09/2026`.

1. Salve a sentença e, na lista, dê **duplo clique** nela. O RM pede os
   parâmetros.
2. Informe `DATAINICIAL = 2026-01-01` e `DATAFINAL = 2026-12-31`.
   - **Veio resultado** → o formato ISO serve; o adapter funciona sem mudar
     nada.
   - **Erro de conversão** → repita com `01/01/2026` e `31/12/2026`. Se
     funcionar, avise: a troca no adapter é uma linha
     (`desde.isoformat()` → `strftime("%d/%m/%Y")`).
3. Confira nas linhas retornadas que `STATUSLAN` traz valores de 0 a 5 e que
   `VALORBAIXADO` vem preenchido em lançamentos baixados.

Anote qual formato funcionou.

## 5. Depois de cadastrada — configuração no Railway

Nos services **`api` e `worker`** (os dois; o worker carrega o mesmo
adapter):

```ini
TOTVS_EXTRACTOR=consultasql
TOTVS_CONSULTASQL_COD_SENTENCA=MOTOR.FLAN.01
TOTVS_CONSULTASQL_COD_SISTEMA=F
TOTVS_CONSULTASQL_COD_COLIGADA=<a coligada onde cadastrou>
TOTVS_COLIGADAS_ESPERADAS=<todas que o usuário de integração deve enxergar, ex.: 1,2>
```

`TOTVS_COLIGADAS_ESPERADAS` liga a guarda contra o filtro silencioso do RM:
um perfil sem permissão numa coligada não recebe erro — recebe menos linhas.
Sem a guarda, uma leitura parcial passaria por completa.

## 6. O que ainda depende de terceiros

O cadastro pode ser feito hoje. A **chamada de verdade** só funciona quando
chegarem as duas pendências que estão em chamados separados:

- **Time Cloud da TOTVS** — liberação de portas e do IP de saída do Motor
  Central, para alcançar o `wsConsultaSQL` no TCloud.
- **Portal do Cliente → Gestão de Licenças** — saldo de licença de
  WebService para o usuário de integração.

## 7. Como o adapter chama

Para quem for depurar. O adapter (`apps/api/app/integrations/totvs/extractor.py`,
`ConsultaSqlExtractor`) faz um POST SOAP em `/wsConsultaSQL/MEX` com
autenticação Basic:

```xml
<tot:RealizarConsultaSQLContexto>
  <tot:codSentenca>MOTOR.FLAN.01</tot:codSentenca>
  <tot:codColigada>1</tot:codColigada>
  <tot:codSistema>F</tot:codSistema>
  <tot:parameters>DATAINICIAL=2026-09-01;DATAFINAL=2026-09-22</tot:parameters>
  <tot:context>CODCOLIGADA=1</tot:context>
</tot:RealizarConsultaSQLContexto>
```

O retorno é um `NewDataSet` em CDATA; o adapter grava o bruto em `raw`,
interpreta o `STATUSLAN` em `situacao` e calcula `saldo = VALORORIGINAL −
VALORBAIXADO` (zero quando cancelado).

## Fontes

- TDN — [Trabalhando com Consultas SQL](https://tdn.totvs.com/display/public/LRM/Trabalhando+com+Consultas+SQL)
  (caminho de menu, campos do cadastro)
- TDN — [TBC – Exemplo Web Service Consulta SQL](https://tdn.totvs.com/display/public/LRM/TBC+-+Exemplo+Web+Service+Consulta+SQL)
  (formato de `parameters`, ordem dos parâmetros)
- Central de Atendimento — [Criação de consultas SQL](https://centraldeatendimento.totvs.com/hc/pt-br/articles/360004646072),
  [Passagem de parâmetros em consultas SQL](https://centraldeatendimento.totvs.com/hc/pt-br/articles/360004683832),
  [wsConsultaSQL](https://centraldeatendimento.totvs.com/hc/pt-br/articles/360016515671)
- Ticket 30268517 — respostas de Eduarda Soares (31/08 e 21/09/2026),
  transcritas em `integrations.md`
