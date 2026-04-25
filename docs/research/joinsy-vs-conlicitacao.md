# Pesquisa · Joinsy × ConLicitação — o que aplicar no Motor Central da Primor

Fontes:
- https://joinsy.com.br (home + página de soluções)
- https://conlicitacao.com.br (home, seção de ferramentas)
- 3 propostas comerciais da Joinsy para a Guaxima Engenharia (Starter · Business · Enterprise) — anexos do usuário

Data da pesquisa: 2026-04-23

---

## 1. O que é cada plataforma, em uma linha

**Joinsy** — SaaS "vender com IA para governo e grandes empresas" (B2G + B2B). Foco em predição (IA indica *quando* vender), automação de lances em portais, mentoria comercial. Se posiciona como ecossistema ponta-a-ponta (busca → proposta → fechamento → pós-venda). Usa Fabiano Zucco como estratégia âncora.

**ConLicitação** — plataforma clássica de monitoramento de editais (mercado, +20 anos). Pontos fortes: banco de dados amplo, boletins 3x/dia, IA "Dr. Licita" (petições e recursos), análise de edital por IA, monitoramento ao vivo do chat de pregão, consultoria jurídica humana.

---

## 2. Catálogo de funcionalidades (consolidado)

| Categoria | Funcionalidade | Joinsy | ConLicitação | Já no Motor Central (PR #1) |
|---|---|---|---|---|
| **Descoberta** | Banco de dados de editais com filtros | Sim (todos os tiers) | Sim | Parcial — filtro UF/modalidade/órgão; falta busca full-text em objeto |
| | Boletins diários por email/app (3x/dia) | (Monitoramento contínuo em todos) | Sim (3x/dia por email) | Não |
| | Monitoramento por palavras-chave do objeto | Sim | Sim (busca em objeto e **no PDF do edital**) | Não — não indexa objeto pra search |
| | Cobertura de atas de registro de preço | Business+ | Sim | Não |
| | Cotações públicas B2G + cotações privadas B2B | Sim (Enterprise para B2B) | Só B2G | Só B2G |
| **Análise** | Análise automática de edital via IA (resumo + Q&A) | "Análise automática de escopos" | Sim ("Análise do Edital") | Não |
| | Análise de concorrentes (histórico, descontos, CNPJ) | Business+ | Sim (+ sanções e penalidades) | Não |
| | Análise de preços praticados no mercado | Business+ | Sim | Não |
| | Inidôneos / sanções | Business+ ("Consulta de Inidôneos") | Sim | Não |
| | **Análise de saúde financeira do município** | **Não** | **Não** | **Planejado (demanda 9 do briefing — TCE + 95% endividamento)** — diferencial competitivo |
| | Inteligência por região (mapas de atuação) | Starter+ ("Mapas") | Indireto (análise de mercado) | Não |
| | Análise de crédito de órgãos/municípios | Starter+ | Não | Não |
| **Operação** | Workflow / agenda de oportunidades / kanban | Starter+ | "Gerenciamento de licitações" | Parcial (só listagem) |
| | Gestão de produtos/catálogo | Business+ | Não | Não |
| | Gestão de documentos (certidões, atestados) | Business+ | Sim ("Gestão de documentos") | Não |
| | Alertas de vencimento (CNDs, atestados) | Enterprise ("gatilhos automáticos") | Implícito | Não |
| | Lances automáticos integrados com portais | Sim (todos os tiers) | Não (é monitoramento, não robô) | Não |
| | Monitoramento do chat do pregão ao vivo | Enterprise ("comunicação com pregoeiros") | Sim ("Monitoramento de chat") | Não |
| | Multi-usuário / atribuição de tarefas | Business+ (3 users), Enterprise (5) | Sim | Não (auth ainda stub) |
| | Política de grupos / multi-filial | Enterprise | Não | Não |
| | Fluxo de aprovação de propostas | Enterprise | Não | Não |
| **Pós-licitação** | Gestão de empenhos (registro → pagamento, alertas) | Enterprise | Não | Não |
| | Gestão de contratos (ciclo completo) | Enterprise | Não | Parcial (Módulo C do briefing) |
| | Integração B2B (receber pedidos, enviar propostas em portais privados) | Enterprise | Não | Fora do escopo |
| **IA jurídica** | IA para gerar petições/recursos | Parcial ("apoio jurídico" como add-on) | Sim ("Dr. Licita") | Não |
| | Consultor jurídico IA | "Apoio jurídico" add-on | Sim ("Consultor Jurídico") | Não |
| | Assessoria cadastral (SICAF, CAUFESP, CRCs) | Add-on | Sim | Não |
| **Aquisição de documentos** | Download automático do PDF do edital + anexos | Implícito | Implícito | Não (planejado como próximo PR) |
| **Comunicação** | Comunicação com pregoeiros/compradores | Enterprise | Sim | Não |
| | Envio de propostas direto da plataforma (portais integrados) | Enterprise (verificar quais portais) | Não | Não |

---

## 3. Precificação de referência (proposta para Guaxima Engenharia LTDA)

Modelo de contratação: **12 meses, boleto bancário**.

| Tier | Valor mensal | Usuários | CNPJs | Resumo |
|---|---|---|---|---|
| **Starter** | R$ 1.490 | 1 | 1 | Workflow + agenda + monitoramento + KPIs + mapas + crédito de órgão |
| **Business** | R$ 3.490 | 3 | 1 | + gestão de produtos/docs/equipe + análise de preços + concorrentes + inidôneos + automação de processos |
| **Enterprise** | R$ 8.490 | 5 | 2 | + empenhos + contratos + multi-filial + fluxos avançados + **B2B** (integração de portais privados) + CS Sênior dedicado |

Custos adicionais: **R$ 99/mês por usuário extra**, **R$ 490/mês por CNPJ adicional**.

Implicação para a Primor: uma construtora com 2–4 CNPJs + 5 usuários ativos entraria em Enterprise → **~R$ 9–10k/mês** = **R$ 108–120k/ano** só em licença, sem contar custo dos scrapers/IA embutidos.

---

## 4. O que é específico da Primor (construtora de obras pesadas)

Olhando para o briefing (demandas 9 e 12 + contexto Zag), o recorte da Primor não é genérico — é **construtora de obras pesadas (terraplanagem, pavimentação, saneamento)**, que já atua em B2G mas sofre com:

1. **Decidir se vale entrar em uma licitação** — não basta ver o edital. Precisa saber:
   - O município tem dotação orçamentária? (histórico de inadimplência)
   - Está abaixo do limite de 95% de endividamento (LRF)?
   - Quem já ganhou obras similares lá (concorrentes locais)?
   - O BDI praticado historicamente nesta região?

2. **Documentação de habilitação técnica** — obras pesadas exigem acervo técnico CREA-CAT específico. Gerir vencimento de ASOs, certidões, atestados técnicos por ART.

3. **Acompanhamento de aditivos + medições** — depois de ganhar, 80% das obras têm aditivo. Quem está gerenciando?

4. **Atas de RP e adesão (carona)** — obras pesadas viram SRP com frequência; ganhar vaga de adesão é arbitragem barata.

5. **Análise do edital aplicada a obras**: cronograma físico-financeiro, garantia contratual, exigência de atestados mínimos por item (m² pavimentação, m³ movimento de terra, m linear drenagem).

---

## 5. Proposta de roadmap (com base nessa comparação)

Ordenado por **ROI para Primor × esforço de dev**:

### P0 — Extensões do Módulo D (licitações) que alinham com o que o mercado oferece

| # | Funcionalidade | Fonte inspiração | Complexidade | Depende de |
|---|---|---|---|---|
| **D.2** | Busca full-text no objeto do edital (pg_trgm + índice GIN) | ConLicitação, Joinsy | Baixa (1 PR) | Migration extra |
| **D.3** | Boletins por email (3×/dia) com matching por palavra-chave + UF + modalidade + valor | ConLicitação | Média (1 PR) | SMTP (MailHog em dev, SES/Resend em prod) |
| **D.4** | Scraper Playwright para baixar edital PDF + anexos | Implícito em ambos | Média (1 PR) | Playwright no workers |
| **D.5** | Análise do edital via LLM (resumo + Q&A estruturado: prazo, garantia, atestados mínimos, BDI) | ConLicitação "Análise do Edital", Joinsy | Média (1 PR) | API key (Anthropic/OpenAI); OCR p/ PDFs escaneados |
| **D.6** | Gestão de documentos da empresa + alertas de vencimento (CNDs, FGTS, atestados CAT) | Joinsy Business, ConLicitação | Baixa (1 PR) | S3/MinIO já no infra |

### P1 — Diferencial Primor (não existe no mercado)

| # | Funcionalidade | Por que | Complexidade |
|---|---|---|---|
| **D.7** | **Motor "Saúde Municipal"** — cruza Siconfi + TCE-estadual + portal transparência → score "vale licitar neste município?" | Demanda 9 do briefing; ninguém no mercado faz | Alta (scrapers por estado, RPA em alguns TCEs) |
| **D.8** | Concorrentes por CNPJ (histórico de obras ganhas na UF/região, descontos médios, aditivos) | Business+ Joinsy e ConLicitação têm, mas não com foco em obras | Média |
| **D.9** | Monitoramento de atas de RP + radar de adesão/carona | Joinsy Enterprise menciona "atas de registro de preço"; é crítico para construtora | Média |
| **D.10** | Integração com Módulo C (contratos/empenhos) — após ganhar, sincroniza empenho + medição | Enterprise Joinsy | Alta (depende Módulo C) |

### P2 — Depois

| # | Funcionalidade | Observações |
|---|---|---|
| D.11 | Monitoramento de chat do pregão ao vivo (Playwright rodando durante disputa) | ConLicitação diferencial; alta complexidade operacional |
| D.12 | Lance automático em portais (ComprasNet, BEC, Licitações-e, etc.) | Joinsy; risco jurídico + precisa credenciais por portal |
| D.13 | Dr. Licita (IA para gerar recursos/petições) | ConLicitação; add-on de IA jurídica |

### NÃO entra (fora de escopo Primor)

- B2B Joinsy Enterprise (envio de propostas em portais corporativos privados) — Primor é B2G puro
- Consultoria jurídica humana/assessoria cadastral — é serviço, não software
- Capacitação e treinamentos — módulo A (RH) cuida disso

---

## 6. O que o PR #1 (em aberto) cobre dessa lista

Atualmente o Módulo D tem:
- [x] Ingestão do PNCP (publicações) com 13 modalidades, upsert idempotente
- [x] Listagem API com filtros UF / modalidade / órgão CNPJ
- [x] Dashboard web com tabela + filtros + paginação
- [x] Worker Celery `crawler_pncp` para agendar

Isso entrega ~15% do que Joinsy/ConLicitação oferecem. É a **fundação** (dados limpos, idempotentes, estruturados) em cima da qual P0 fica viável.

---

## 7. Decisões que preciso do usuário

1. **Priorização do backlog D.2 → D.13** — segue a ordem sugerida ou repriorizo?
2. **Provedor de LLM** para D.5 (análise de edital): Anthropic Claude, OpenAI, ou ambos (fallback)?
3. **Provedor de email** para D.3 (boletins): SES, Resend, SendGrid?
4. **Quais portais** a Primor hoje usa (ComprasNet, BEC-SP, Licitações-e, Compras.gov)? Isso define prioridade do scraper Playwright em D.4.
5. **Quais estados/municípios** são alvo principal (pra guiar a ordem de implementação dos scrapers TCE em D.7)?
6. **Fechar o PR #1 antes** (mergear a base PNCP) ou acumular D.2–D.5 no mesmo branch?

Minha recomendação: **fechar PR #1 como está** (testado e verde), depois abrir D.2–D.5 em PRs pequenos sequenciais.
