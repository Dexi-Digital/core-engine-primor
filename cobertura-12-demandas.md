# Cobertura das 12 demandas — estado em 26/abr/2026

Mapeamento de cada demanda do briefing original contra o que já foi
mergeado no `core-engine-primor` (PRs #1–#19) e os temas transversais
levantados na sua análise (LGPD, build vs buy, governança).

Legenda:
- 🟢 **Coberto** — entregue, em produção.
- 🟡 **Parcial** — fundação pronta, mas faltam pedaços do escopo.
- 🔴 **Não coberto** — ainda não tocado.

---

## D1 — Diagnóstico documental (DP, SST, Manutenção, Licitação, Obras)
🔴 **Não coberto.** Você tem razão em apontar que isso deveria vir antes —
hoje o sistema *armazena e cria* documentos, mas não *audita o que está
faltando* contra um checklist. Fácil de adicionar como módulo `/diagnostico`
que cruza obras × empregados × veículos × certidões e cospe um relatório de
"% de atendimento" — mas precisa da régua que você mencionou (CLT/NRs pra DP/SST,
e o que vocês considerarem "documentação completa" pra Obras/Licitação).

## D2 — Cadastro único + automação admissão/SST
🟡 **Parcial.**
- ✅ Cadastro de funcionários local — `Employee` (PR #10) com CPF/RG/PIS/dados pessoais/admissão/ASO/salário.
- ✅ Dossiê de admissão — ViaCEP + BrasilAPI + DirectData (mock) (PR #10).
- ✅ Alertas de vencimento de ASO (30/15/7/0d via Resend) (PR #18).
- 🔴 **Propagação para Domínio/Onvio/Tangerino/OnSafety** — você confirmou em sessão anterior que pulamos essa parte (sem credencial Domínio). Continua não coberto.
- 🔴 **OCR de RG/CPF/Comprovante para pré-preenchimento** — não existe.
- 🔴 **Treinamentos com confirmação de visualização** — não existe.

Concordo com sua sugestão de fatiar — se for retomar, recomendo 3 PRs separados:
(a) integração real Domínio/Onvio (depende de credencial); (b) OCR de
documentos pessoais reusando o Document AI já configurado pra parte
diária; (c) microsistema de treinamentos com confirmação.

## D3 — Background check
🟡 **Parcial.**
- ✅ Validação algorítmica de CPF (DV, sequências repetidas) (PR #10).
- ✅ Adapter DirectData (mock) que pode buscar nome/situação CPF/PIS quando você ligar a chave.
- ✅ Adapter BrasilAPI (público, gratuito) pra CNPJ.
- 🔴 Ficha policial / processos judiciais (Escavador, JusBrasil) — não existe.
- 🔴 INSS / CNIS — não existe.

Concordo com a ressalva LGPD — antes de implementar consulta a ficha policial vale o parecer jurídico que você sugeriu.

## D4 — Acompanhamento de INSS para afastados
🔴 **Não coberto.** Esse é um quick-win exatamente como você descreveu — sistema de prazos com alertas. Reusaria 100% da infra que já existe (`Resend` pra email, Celery beat pra agendamento, AGENTS.md pra audit). Estimativa: 4–6h pra um PR pequeno.

## D5 — Partes diárias
🟡 **Parcial — você está coberto pra OCR mas não pra entrada digital em campo.**
- ✅ Upload de PDF/foto + OCR via Google Document AI (mock) (PR #15).
- ✅ Persistência em `partes_diarias` com correção manual + reprocessar (PR #15-17).
- 🔴 Form digital no celular do apontador — você apontou bem que letra ruim em obra é desafio; PWA mobile-first com formulário estruturado seria o caminho mais robusto.
- 🔴 Cálculo de consumo + gatilho de manutenção preventiva — não existe.

## D6 — Downloads automáticos
🟡 **Parcial — sua subdivisão (frota vs CNDs/ARTs) bate exatamente com como o código está.**
- ✅ CNDs/atestados com alertas (PR #8) — Receita/PGFN/Trabalhista (D.6 do briefing licitações).
- ✅ Veículos via Infosimples — multas/IPVA/CRLV/licenciamento SP/MG/GO (PR #14, mock até ligar `INFOSIMPLES_TOKEN`).
- 🔴 ARTs do CREA — não existe (e como você notou, varia por estado).
- 🔴 Avaliação build-vs-buy do lado frota (Gringo/Autobrasil) — pulamos quando você escolheu Infosimples.

## D7 — Patrimônio × apropriação × custo
🔴 **Não coberto.** E concordo com seu argumento — depende criticamente do D8 (Totvs). Construir agora contra o Sistema 90 vira retrabalho.

## D8 — Reorganização financeira (Totvs)
🟡 **Parcial — mas direção diferente da que você descreveu.**
- ✅ Upload de XMLs fiscais via Domínio (PR #12) — 6 tipos (NF-e, NFS-e, NFC-e, CT-e, CF-e, baixa de parcela).
- ⚠️ **Importante:** A integração entregue é com **Domínio** (escritório contábil), não Totvs. Quando a migração pro Totvs acontecer, o adapter `DominioClient` será substituído por um `TotvsClient` (mesmo Protocol, plug-in). O código foi pensado pra isso.
- 🔴 Conciliação bancária, plano de contas, eliminação de planilhas paralelas — não existe.

Concordo 100% com seu ponto: a migração é a *oportunidade* — tentar replicar planilhas no Totvs é o anti-padrão clássico.

## D9 — Inteligência de licitações
🟡 **Parcial — cobre PNCP bem, mas é só uma das fontes que você listou.**
- ✅ Crawler PNCP (D.1) (PR #1).
- ✅ Full-text search com pg_trgm (D.2) (PR #2).
- ✅ Boletins por email (D.3) (PR #3).
- ✅ Download de editais + anexos (D.4) (PR #4).
- ✅ Análise IA com cost-routed LLM (D.5) (PR #6).
- ✅ Gestão de CNDs com alertas (D.6) (PR #8).
- 🔴 Conlicitação, Portal da Transparência, SICONFI, TCEs estaduais, IBGE — não cobertos. Você está certo: o que tá no documento é mais ambicioso que "acompanhamento" — é inteligência de mercado.
- 🔴 Análise municipal (arrecadação, emendas, contabilidade) — não existe.
- 🔴 Busca documental de concorrentes — não existe.

## D10 — Reconhecimento facial em fotos de obra
🔴 **Não coberto.** E concordo com sua leitura LGPD — biometria é dado sensível. Antes de construir vale validar a finalidade alternativa que você sugeriu (QR code no crachá / geo do Tangerino).

## D11 — Orçamento automático de compras
🔴 **Não coberto.** Concordo com seus dois alertas: scraping de fornecedores → bloqueio + ToS; WhatsApp pessoal automatizado → suspensão. Caminho viável seria catálogo cadastrado + WhatsApp Business Cloud API + RFQ estruturado.

## D12 — Gestão de contratos
🟡 **Parcial — só infra de storage.**
- ✅ OneDrive como backend de storage (PR #11) com `Protocol DocumentStorage` genérico.
- 🔴 Versionamento, prazos, alertas, fluxo de assinatura — não existe.
- 🔴 Decisão sobre Easyjur (consolidar / substituir / integrar) — pendente sua.

---

## Temas transversais que você apontou

| Tema | Estado |
|---|---|
| **Governança LGPD / audit_log** | 🟡 Parcial. PR #19 trouxe auth real; `/auth/users` grava actor real, mas os outros módulos (RH, frota, fiscal, certidões) ainda gravam `actor="system"`. É a primeira opção de PR seguinte que sugeri. |
| **Build-vs-buy** | 🔴 Não documentado. Várias decisões já tomadas implicitamente (Infosimples vs Gringo, Document AI vs Tesseract) mas sem decisão registrada por demanda. |
| **Sponsor / responsável por demanda** | 🔴 Não documentado. |
| **Métricas de sucesso** | 🔴 Não documentado. |
| **Orçamento e prazo** | 🔴 Não documentado. |

---

## Sua priorização sugerida (1, 4, 8 + 5 ou 7) — minha leitura

- **D1 (diagnóstico)** — concordo plenamente. É o que falta pra você saber pra onde vai o esforço.
- **D4 (INSS afastados)** — concordo. Quick-win real, 4–6h, infra já existe.
- **D8 (Totvs)** — depende **muito** de quando a migração acontece. Hoje você tem o Domínio funcionando como ponte; trocar pra Totvs é o evento que justifica gastar tempo aqui.
- **D5 (partes diárias) ou D7 (patrimônio)** — D5 já tem fundação OCR; faltaria o app-de-campo (PWA). D7 espera a migração pro Totvs. Eu iria de **D5** primeiro.

---

## Recomendação concreta de próximos PRs

Em ordem (cada item = 1 PR, mantendo padrão small-PR):

1. **Wire `current_user.email` no audit_log dos demais services** — fecha o LGPD que abrimos com PR #19. Pequeno (~3h). Fundamental antes de qualquer coisa que toque dado pessoal real (D2, D3, D10).
2. **D4 — INSS afastados** — quick-win que você listou. ~4–6h.
3. **D1 — Diagnóstico documental** — a régua de % de atendimento. Médio (~6–8h, depende dos checklists). Você precisa me passar pelo menos um esqueleto do "o que conta como completo" pra DP/SST/Obras/Licitação.
4. **D5 (segunda fase) — PWA mobile pra entrada estruturada de parte diária em campo**. Médio (~8–10h).

Os outros (D2 propagação real, D3 background check, D8 Totvs, D10 facial,
D11 orçamento, D12 contratos) eu deixaria parados até acontecer o gatilho
externo (credencial, parecer LGPD, migração Totvs, decisão Easyjur).
