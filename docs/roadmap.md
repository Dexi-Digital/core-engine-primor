# Roadmap por Módulo

Baseado nas 12 demandas do briefing (`demandas programação - rev01.docx`).

## Módulo A — DP / SESMT (prioridade alta)

| # | Demanda | Entrega |
|---|---------|---------|
| 1 | Varredura e conferência documental | Job Celery que percorre OneDrive, roda OCR (Tesseract ou Textract), cruza com checklist de docs exigidos por perfil (DP, SESMT, Obras, Licitação) e gera relatório `%` de atendimento. |
| 2 | Onboarding unificado | Endpoint `POST /dp-sesmt/onboarding` que enfileira sync paralelo para Domínio, Onvio, Tangerino, OnSafety. Contrato assinado digitalmente via provider (a definir). Alertas periódicos (experiência, ASO, retorno). |
| 3 | Dossiê de candidatos | Robô que consulta fontes públicas (processos, ficha policial via API, histórico INSS, benefícios, empresas anteriores). |
| 4 | Acompanhamento INSS | Scheduler que monitora afastados, alerta datas e documentos. |
| ⊕ | E-learning WhatsApp | Bot conversacional: envia vídeo → registra visualização → assinatura → emite certificado. |

## Módulo B — Manutenção / Frota

| # | Demanda | Entrega |
|---|---------|---------|
| 5 | Unificação de partes diárias | Upload de foto/PDF → OCR (visão computacional treinada em manuscrito) → parsing para apontamento. Cálculo de combustível e alerta de manutenção preventiva. |
| 6 | Download automático de docs | RPA via Playwright para IPVA, CRLV, certidões, multas. Agendado por placa/CNPJ. |
| 7 | Apropriação e custo por equipamento | Integração parcial com Sistema 90; motor de regras que cruza apropriação (h/km) × NF. Alertas de custo acima da referência. |

## Módulo C — Financeiro / Contratos

| # | Demanda | Entrega |
|---|---------|---------|
| 8 | Migração 90 → TOTVS | Ingestão de NF-e (XML parser + OCR de PDFs). Conferência automática de remessas bancárias. Encaminhamento estruturado de NFs das obras. Cruzamento combustível × alimentação × aluguel × descontos em medição. |
| 12 | Ciclo de contratos | Emissão, assinatura digital, AP, alertas de vencimento. Possível organização no OneDrive. Relatórios EasyJur para contratos judicializados. |

## Módulo D — Licitações

| # | Demanda | Entrega |
|---|---------|---------|
| 9 | Inteligência de licitações | **[Em andamento]** Ingestão PNCP (✓ D.1), busca full-text no objeto com pg_trgm + índice GIN (✓ D.2), boletins por email 3x/dia via Resend com dedup por cursor `last_licitacao_id` (✓ D.3), download de edital + anexos via PNCP Portal (✓ D.4; ComprasNet/Licitações-e ficaram como scaffold por exigirem captcha / SSO), análise de edital via LLM cost-routed (Anthropic Haiku + OpenAI Nano com fallback automático, pypdf + schema estruturado, ✓ D.5), gestão de CNDs/atestados com alertas de vencimento por email em janelas 30/15/7/0 dias antes do vencimento (Celery beat 1x/dia 08h, idempotente via UniqueConstraint `(certidao_id, janela)`, ✓ D.6). **Próximos passos:** Módulo A — onboarding sync via Domínio (D.6.1 / Mod A); análise de saúde municipal (TCE-MG/SP/GO + Siconfi + limite 95% LRF, D.7); adesões/atas de RP (D.9). |

## Módulo E — IA / Ferramentas avançadas

| # | Demanda | Entrega |
|---|---------|---------|
| 10 | Reconhecimento facial | Foto Tangerino como referência → busca nas fotos de obra. Prova trabalhista auditável. |
| 11 | Robô de compras WhatsApp | Agente LLM que recebe demanda de manutenção, contacta fornecedores, compila cotação em tabela. Mesmo fluxo para locações. |

## Ordem sugerida de execução

1. **Fundações** (este PR): scaffold, CI, docker-compose, audit_log.
2. **Módulo A** — onboarding + varredura OCR (impacto imediato no DP).
3. **Módulo C** — ingestão de NF-e (rastreabilidade financeira).
4. **Módulo B** — RPA despachante + OCR partes diárias.
5. **Módulo D** — crawlers B2G.
6. **Módulo E** — reconhecimento facial + bot de cotação.

Cada entrega vira 1 PR com documentação, testes e feature flag.
