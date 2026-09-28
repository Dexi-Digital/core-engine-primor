# Financeiro — legado 90 e TOTVS

Atualização de requisitos em 26/09/2026, demandas 8 e 12 do briefing.
Este documento registra o escopo e a inspeção dos exemplos. A importação
descrita aqui já tem fluxo implementado; falta validar o aceite com arquivos
reais e confirmar a cobertura do relatório FIN.

## Decisão de origem

O cliente definiu duas entradas para o financeiro:

- **Legado Sistema 90:** importação manual de planilha no sistema.
- **TOTVS:** outra parte dos dados financeiros, por integração com o RM.

Ambas precisam aparecer na análise consolidada com proveniência. Não
presumir que o mesmo título nas duas origens seja uma única operação ou
que todos os registros de uma origem devam substituir os da outra.
O período de transição e a regra para eventuais sobreposições ainda não
foram informados.

## Exemplos inspecionados

Arquivos fornecidos localmente pelo cliente, mantidos fora do repositório:

- `Planilhas.xlsx` — estrutura e dados de exemplo dos relatórios.
- `Relatorio_FIN_TOTVS.docx` — solicitação de relatório, campos,
  classificação, filtros e formato de exportação. Não é uma amostra de
  resposta da API TOTVS.

| Aba da planilha | Estrutura observada | Uso proposto |
|---|---|---|
| Contas a pagar | 16.411 linhas físicas, 43 colunas no intervalo utilizado, cabeçalhos de impressão e 64.351 regiões mescladas | Referência do relatório bruto; exige parser próprio se for aceito como entrada |
| Contas a receber | 551 linhas físicas, 32 colunas no intervalo utilizado e 4.865 regiões mescladas | Referência do relatório bruto; layout diferente de contas a pagar |
| Relatório Completo | 83.481 linhas de dados + cabeçalho, 23 colunas, sem mesclagens | Entrada preferencial proposta para a primeira versão do importador |
| Relatório Final (Resumo) | 66.184 linhas de dados + cabeçalho, tabela com 20 colunas, sem mesclagens | Referência analítica; não substitui a base completa |

Contagens de linhas não equivalem a quantidades de títulos únicos. Os
relatórios brutos incluem linhas vazias e estrutura de impressão.

O DOCX descreve um exemplo apenas da CTC, mas as abas consolidadas
contêm seis códigos de empresa: `CZC`, `CZG`, `CTC`, `GXM`, `PRM` e
`ZAG`. Logo, não fixar a empresa pelo nome do arquivo nem assumir que
todas as abas descrevem o mesmo recorte.

| Empresa | Linhas no Completo | Linhas no Resumo |
|---|---:|---:|
| CZC | 5.965 | 1.656 |
| CZG | 3.681 | 3.680 |
| CTC | 22.270 | 16.464 |
| GXM | 29.271 | 12.906 |
| PRM | 906 | 906 |
| ZAG | 21.388 | 30.572 |

O resumo contém campos de transformação (`Tipo`, `SUB-NATUREZA`,
`AJUSTE DATA EMISSÃO`, `AJUSTE DATA PAGAMENTO`, `REGRA_ZERAR`) e menos
linhas que a base completa. Essas diferenças não autorizam descartar
registros ou zerar valores na importação. As regras do Power Query e o
recorte de cada aba precisam ser confirmados antes de reproduzi-los.
Há 1.492 linhas com `REGRA_ZERAR = ZERAR` no resumo. A ZAG tem mais
linhas no resumo do que no completo; portanto, não é seguro tratá-lo
como simples subconjunto da aba completa.

## Campos preservados

Nenhuma das 23 colunas pode ser removida ou substituída por resumo.
Preservar também os valores originais e sua localização no arquivo para
conferência. A equivalência com os campos de extração TOTVS ainda deve
ser validada; nomes do Excel não comprovam nomes de tabelas no RM.

| Campo exigido pelo FIN | Coluna em Relatório Completo |
|---|---|
| Empresa | EMPRESA |
| Cliente ou Fornecedor | CLIENTE / FORNECEDOR |
| Título | TÍTULO |
| Data de Pagamento | DATA PAGAMENTO |
| Período | PERÍODO |
| Data de Inclusão da Nota Fiscal | DATA INCLUSÃO NF |
| Data de Emissão | DATA EMISSÃO |
| Valor Original | VALOR ORIGINAL |
| Valor Incremento | VALOR INCREMENTO |
| Valor Abatimento | VALOR ABATIMENTO |
| Valor Amortizado | VALOR AMORTIZADO |
| Valor Acréscimo | VALOR ACRÉSCIMO |
| Valor Dedução | VALOR DEDUÇÃO |
| Valor Pago | VALOR PAGO |
| Valor Atual | VALOR ATUAL |
| Local | LOCAL |
| Centro de Custo | CENTRO DE CUSTO |
| ID Natureza | ID NATUREZA |
| Natureza I | I |
| Natureza II | II |
| Natureza III | III |
| Descrição da Natureza | NATUREZA |
| Valor Apropriado | VALOR APROPRIADO |

No bruto de contas a receber aparece `VALOR RECEBIDO`; no consolidado,
`VALOR PAGO`. Manter essa informação de origem se o bruto passar a ser
aceito. `PERÍODO` e competência da emissão são campos distintos.

## Campos calculados

**Competência da Emissão:** primeiro dia do mês de `DATA EMISSÃO`.
Exemplo: 22/06/2026 → 01/06/2026. Sem data válida, deixar pendência
explícita, sem inventar competência ou usar a data de pagamento.

**Classificação:** aplicar as combinações do DOCX abaixo. A precedência
proposta coloca regras específicas antes das gerais: os aportes de
entrada também satisfazem a regra geral de Receita. Essa precedência
precisa ser validada pelo financeiro antes da homologação.

| Ordem proposta | I | II | III | Classificação |
|---|---|---|---|---|
| 1 | 1 | 1 | 3 | Devolução de Aporte de SCP entrada |
| 2 | 1 | 1 | 8 | Aporte entrada |
| 3 | 1 | Qualquer | Qualquer | Receita |
| 4 | 2 | 2 | Qualquer | Mão de Obra |
| 5 | 2 | 3 | 16, 17 ou 21 | Locação de Equipamento |
| 6 | 2 | 3 | 27 | Equipamento Próprio |
| 7 | 2 | 3 ou 4 | Demais combinações, excluídas as duas regras anteriores | Material |
| 8 | 2 | 1 | Qualquer | Execução e Escritório Local |
| 9 | 2 | 5, 6 ou 7 | Qualquer | Imposto |
| 10 | 2 | 8 | Qualquer | Serviço |
| 11 | 2 | 9 | 1 | Aporte saída |
| 12 | 2 | 9 | 2 | Devolução de Aporte de SCP saída |
| 13 | 3 | Qualquer | Qualquer | Investimento |

Comparar os níveis normalizados (`03` equivale a `3` para a regra),
preservando os códigos originais e zeros à esquerda. Campo necessário
à decisão ausente/inválido ou combinação sem regra deve gerar pendência
de classificação. Não usar Material ou Receita como fallback genérico.

A descrição da natureza continua disponível para filtros como
Combustíveis, Lubrificantes, Ferro e Aço e Peças. O DOCX não exige uma
nova coluna de Subgrupo. Os campos calculados não acrescentam trabalho
de preenchimento manual ao financeiro.

## Granularidade e importação propostas

1. Upload manual, identificação da origem e seleção da aba/layout.
   A proposta inicial aceita `Relatório Completo`; suporte às abas
   brutas deve ser tratado explicitamente, sem detecção silenciosa.
2. Processamento em worker para o volume observado, com validação de
   cabeçalho, códigos, datas e valores. Exibir prévia, contagens e erros
   por linha antes de confirmar a persistência.
3. Registrar lote, usuário, data, hash do arquivo, aba e linha original.
   Reenvio do mesmo arquivo não pode duplicar dados. Arquivos corrigidos
   ou com períodos sobrepostos exigem uma política própria; hash do
   arquivo não resolve deduplicação entre exportações diferentes.
4. Preservar cada apropriação. Há títulos repetidos com rateios distintos
   e valores do título preenchidos só na primeira linha do grupo.
   Não propagar valores monetários vazios para todas as linhas nem
   converter ausência em zero. Somar `VALOR APROPRIADO` para análises
   por obra/natureza; totais do título exigem identidade validada.
5. Usar representação decimal para cálculos e regra de arredondamento
   homologada: o Excel contém resíduos de precisão nos rateios.
6. Manter registros do legado separados da identidade de lançamento
   TOTVS. Não deduplicar apenas por título, fornecedor ou valor, nem
   eliminar linhas idênticas sem saber se representam rateios legítimos.

## TOTVS: diferença em relação ao que já existe

O adapter e `financeiro_totvs` já ingerem lançamentos com identificador,
empresa/coligada, contraparte, valor, datas, situação, baixas e saldo.
A identidade atual é `CODCOLIGADA + IDLAN`.

A tela financeira passa a apresentar os lançamentos e as últimas cinco
execuções TOTVS já guardadas em `totvs_lancamentos` e `totvs_sync_log`.
Essa consulta é somente leitura e mantém o RM como origem separada; a
integração atual não oferece todos os 23 campos nem detalha rateios.

O relatório solicitado exige também apropriações, local/obra, centro de
custo, natureza em três níveis e os demais valores/datas do FIN. O
modelo atual não representa todos esses campos. Será necessário ampliar
o contrato de extração e modelar as apropriações vinculadas ao lançamento,
com identidade estável para cada rateio. Não gravar vários rateios sob a
chave única do lançamento, o que sobrescreveria dados.

O acesso de manutenção/frota informado nesta conversa não confirma
liberação do TOTVS. O relatório DOCX ainda não é um payload real do RM.
A extração real e a disponibilidade dos campos de rateio no ambiente
autorizado precisam ser verificadas antes de prometer um consolidado FIN
que una registros TOTVS e legado.

## Filtros, exportação e aceite

- Filtros por empresa, local/obra, cliente/fornecedor, centro de custo,
  emissão, competência da emissão, pagamento, período, naturezas I/II/III,
  descrição da natureza e classificação. Acrescentar origem e lote para
  rastrear os dados importados.
- Excel com uma linha de cabeçalho e uma linha por apropriação, datas
  como datas, valores como números, códigos preservados como texto e
  nenhuma mesclagem na base. Permitir várias empresas selecionadas e
  processamento de grande volume.
- Conferência por empresa/obra/período entre arquivo, prévia, base e
  exportação; teste de reenvio e casos de títulos com vários rateios.
- Validar todas as classificações, especialmente exceções de Material
  e a precedência dos aportes sobre Receita.
- Se a classificação não puder ser entregue, disponibilizar os 23
  campos completos; nunca remover natureza ou valor apropriado para
  simplificar o relatório.

## Situação das outras integrações

- **EasyJur:** falha relatada pelo cliente em 26/09/2026. Causa não
  informada; distinguir login, extração, persistência e exibição no
  diagnóstico. O relato não cancela a integração nem comprova que a
  senha esteja errada. O contencioso não é fonte financeira validada.
- **Manutenção / frota:** acessos concedidos conforme o cliente.
  Identificar os serviços contemplados e validar configuração/operação.
  Não manter falta de concessão como bloqueio geral nem marcar os
  adapters como homologados apenas pela existência do acesso.
