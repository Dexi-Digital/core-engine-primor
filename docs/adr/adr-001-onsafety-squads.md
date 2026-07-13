# ADR-001: Organização de squads — Integração OnSafety

**Status:** Aceito
**Data:** 2026-07-12
**Decisores:** Lorrayne (tech lead) + gestão Dexi
**Branch:** `feat/integracao-onsafety`

## Contexto

A integração OnSafety (SST/EPIs, Módulo A) tem 4 etapas: (1) adapter +
mock + config, (2) pull de ASO/EPI/treinamentos para o dossiê, (3) push
de onboarding, (4) docs + testes. A etapa 1 é bloqueante, mas o
`OnsafetyClient` em modo mock determinístico (padrão consolidado no repo:
DirectData, Infosimples, Domínio) permite que pull e push avancem em
paralelo contra o mock.

Restrições:

- O token disponível é de **produção** (validado em 2026-07-12:
  autentica em `api.onsafety.com.br`, 401 em `api.dev.onsafety.com.br`).
  Nenhuma squad faz escrita real até haver token de homologação.
- ASO é dado de saúde (LGPD): auditoria e minimização de campos
  (parâmetro `fields`) são requisito de qualquer pull, não polish final.
- O endpoint `/v2/*/contar` da OnSafety está quebrado (erro Querydsl no
  backend deles) — contagens usam `totalElements` da listagem paginada.

## Decisão

Três squads paralelas + responsabilidade transversal rotativa.

### Squad 1 — Plataforma / Adapter (fundação)

Etapa 1 inteira: `OnsafetyClient` (httpx, header `token`, paginação
Spring Data, `fields`), modo mock determinístico, exceções
`OnsafetyError`/`OnsafetyAuthError`, settings (`ONSAFETY_TOKEN`,
`ONSAFETY_BASE_URL` default homologação), `.env.example`.

**Entregável-chave:** a interface pública do client
(`list_trabalhadores`, `list_exames_ocupacionais`, `list_controles_epi`,
`list_treinamentos_trabalhadores`, `create_or_update_trabalhador`)
publicada como PR de skeleton no dia 1 — é o contrato que destrava as
squads 2 e 3.

### Squad 2 — Pull SST (dossiê do funcionário)

Etapa 2, contra o mock: matching OnSafety ↔ `dp_employees` por CPF
(fallback `codigoExterno`); sync ASO → colunas `aso_*` (alimenta alertas
A.2 em produção — não quebrar); EPIs → `EmployeeDocument` tipo
`FICHA_EPI`; treinamentos em seguida; task Celery agendada + log de
auditoria LGPD em todo pull.

### Squad 3 — Push Onboarding + UI

Etapa 3, contra o mock: OnSafety na task `sync_onboarding`
(`create_or_update` com `codigoExterno` = employee id, `correlation_id`
idempotente); status por sistema na UI de RH. **Gate:** merge só após
smoke test em homologação — única frente que escreve na OnSafety.

### Transversal — Docs + QA (rotativo)

Seção OnSafety em `docs/integrations.md`, testes de contrato do mock vs.
spec OpenAPI, revisão LGPD cruzada entre squads 2 e 3.

## Opções consideradas

### Opção A: 3 squads paralelas com contrato-primeiro (escolhida)

| Dimensão | Avaliação |
|----------|-----------|
| Complexidade | Média (exige disciplina no contrato do client) |
| Prazo | ~3 semanas com paralelismo |
| Risco | Baixo — mock isola do gargalo externo |

### Opção B: squad única sequencial (etapas 1→4)

| Dimensão | Avaliação |
|----------|-----------|
| Complexidade | Baixa |
| Prazo | ~5-6 semanas, e o token de homologação atrasaria tudo |
| Risco | Cronograma refém de dependência externa |

## Trade-off

O mock determinístico elimina o acoplamento que justificaria a opção B.
O caminho crítico não é código: é o token de homologação (Squad 3) e a
validação do formato de CPF na base OnSafety (Squad 2). O custo da
opção A é o skeleton no dia 1 ser inegociável.

## Consequências

- Fica mais fácil: paralelismo real, testes sem rede, onboarding de devs.
- Fica mais difícil: mudanças na interface do client após o dia 1 exigem
  coordenação entre as 3 squads.
- Revisitar: quando o token de homologação chegar, validar o formato do
  parâmetro `fields` com campos aninhados (`trabalhador.nome`) e o
  contrato real do `create_or_update`.

## Ações

1. [x] Squad 1: skeleton com interface do client + mock (este PR).
2. [ ] Gestão: solicitar token de **homologação** ao cliente OnSafety.
3. [ ] Squad 2: validar formato de CPF na base OnSafety antes do matching.
4. [ ] Kickoff: definir tamanho real das squads (1 a 3 devs por squad).
