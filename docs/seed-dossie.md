# Seed de dados a partir do dossiê de processos da PRIMOR

O dossiê **"Dôssie de rotinas e processos Bruno.docx"** descreve o
organograma administrativo, fluxos de licitação/contratos/frota e nomes
dos responsáveis pela operação da PRIMOR. Esse documento é a fonte de
verdade para os dados iniciais que o Motor Central usa em demonstrações
e ambientes de homologação.

O script <ref_file file="/home/ubuntu/repos/core-engine-primor/apps/api/scripts/seed_dossie.py" />
extrai esses dados e popula o banco de forma **idempotente** — pode ser
rodado quantas vezes for necessário sem duplicar registros.

## O que ele planta

| Tabela | Quantidade | Conteúdo |
|---|---|---|
| `dp_employees` | 14 (matrículas `A-001` a `A-014`) | Equipe administrativa do organograma do dossiê (Bruno, Evandro, Brenda, Brendon, Gigante, Emilly, Paulo, Wanderson, Lorena, Samuel, Marcelo, Dani, Rodrigo, Lorrayne). Todos com `is_admin_office=true`. |
| `obras_obra` | 5 (`z209`–`z213`) | Obras de exemplo: MG-010 (DER/MG), Av. Cristiano Machado (PBH), BR-040 (DNIT), SRP PBH, GO-070 (AGETOP). |
| `certidoes_empresa` | 14 | CNDs federal/FGTS/trabalhista + CATs para as 4 empresas do grupo (ZAG, Guaxima, CTC, CIRRUS). Inclui propositalmente uma vencida e uma vencendo para demonstrar os alertas. |
| `empresa_documentos` | 11 | Contratos sociais, balanços, CAGEF e SICAF das 4 empresas. |
| `licitacoes_saved_queries` | 2 | Saved queries do PNCP que espelham o trabalho da Evandro/Brenda: "Infraestrutura viária MG/ES/GO/MT/RO" e "Adesões SRP MG". |
| `licitacoes` | 6 | Editais publicados de exemplo dos órgãos onde a PRIMOR atua. |

| `frota_veiculos` | 5 | Caminhões, retroescavadeira, pick-up e caçamba, alocados nas 5 obras. Um deles em `manutencao` para a tela não ficar toda verde. |
| `frota_documentos` | 8 | CRLV, seguro e IPVA — com **um vencido** e **um vencendo**, mesma escolha das certidões. |
| `partes_diarias` | 4 | Em estágios diferentes do OCR (`revisado`, `processado`, `pendente`). A `pendente` é proposital: sem worker Celery no ar, é assim que o pipeline realmente se comporta. |
| `contratos` | 4 | Locação, fornecedor e cliente, incluindo um vigente perto do vencimento e um encerrado. |

### Dados que NÃO são plantados

- **Credenciais de sistemas externos** (Registro.br, ChatGPT, BitLocker, etc.)
  citadas no dossiê — segredos nunca devem ir para o banco. Use o secret
  manager configurado em `.env` ou o gerenciador da org (1Password / Bitwarden).
- **CPFs reais dos colaboradores** — o script usa CPFs sintaticamente
  válidos (passam no algoritmo de verificação), mas não são reais. Ao
  receber os reais, basta atualizar via UI (`/rh/funcionarios`).
- **Fiscal (NF-e), Ponto (Tangerino/Sólides) e TOTVS** — de propósito. O
  valor dessas telas está em ver a **integração** trazendo o dado; semear
  falsificaria justamente o que se quer demonstrar.
- **CNPJs reais das empresas do grupo** — usa placeholders
  (`33000001000101` … `33000004000104`). Quando o cliente entregar os
  reais, basta substituir as constantes `EMPRESA_*` no topo do script e
  rodar novamente (vai criar novos registros; remova os antigos via SQL
  ou UI).

## Como rodar

### Local (dev)

```bash
cd apps/api
uv run python -m scripts.seed_dossie
```

Saída esperada (primeira execução):

```
seed_dossie: starting
seed_dossie: done admins=14 obras=5 certidoes=14 docs_empresa=11 saved_queries=2 licitacoes=6
seed_dossie: criados admins=14, obras=5, certidoes=14, docs_empresa=11, saved_queries=2, licitacoes=6 (total=52)
```

Execuções subsequentes retornam `total=0` (idempotência).

### Em ambiente já rodando (docker compose / k8s)

```bash
# se o api estiver em container nomeado `motor-api`:
docker exec motor-api uv run python -m scripts.seed_dossie

# ou via compose:
docker compose exec api uv run python -m scripts.seed_dossie
```

O script lê a `DATABASE_URL` do mesmo `.env` que a API usa, então não
precisa de configuração extra.

## Quando rodar

| Cenário | Rodar? |
|---|---|
| Reset completo do banco (`alembic downgrade base && upgrade head`) para uma demo | **Sim** |
| Spin up de ambiente novo (homolog/staging) | **Sim** |
| Banco já com dados reais em produção | **Não** — use a UI |
| Após atualizar o dossiê (ex: novo colaborador) | Não direto — edite o script ou cadastre via UI |

## Onde aparece na UI

- `/rh` — card **"Equipe administrativa"** com contadores por setor.
- `/rh/equipe-administrativa` — página dedicada com cards agrupados por
  setor (Diretoria, Planejamento, Licitações, Contratos, Orçamento, TI,
  Comercial, Administrativo, Consultoria).
- `/rh/funcionarios` — filtros **Setor** e **Tipo** (admin/operacional),
  badge `adm` ao lado do nome dos administrativos.
- `/licitacoes` — saved queries e editais de exemplo aparecem nos
  boletins e na lista geral.
- `/diagnostico` (D1) — usa as certidões plantadas para mostrar
  documentos vencidos/vencendo da CIRRUS e da Guaxima.

## Mock antigo vs. seed do dossiê

O script **não deleta** dados antigos. Se o ambiente tiver registros de
seeds anteriores (`balanco_patrimonial`/`BP-2025`, etc. em
`empresa_documentos`), eles continuam lá lado a lado com os novos
(`BALANCO_PATRIMONIAL`/`ZAG-BAL-2024`, etc.). Para limpar o mock antigo,
use SQL direto — não há comando automatizado para evitar destruição
acidental em produção.

```sql
-- exemplo: limpar docs societários do seed mock antigo (lowercase)
DELETE FROM empresa_documentos
WHERE tipo IN ('contrato_social', 'balanco_patrimonial', 'cagef_mg', 'sicaf')
  AND created_at < '2026-04-01';
```
