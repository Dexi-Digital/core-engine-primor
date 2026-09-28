# Roadmap por Módulo

Baseado nas 12 demandas do briefing (`demandas programação - rev01.docx`).

## Atualização de escopo — 26/09/2026

- **Financeiro:** duas origens complementares: legado do Sistema 90 por
  importação manual de planilha e TOTVS. O relatório deve preservar os
  dados por apropriação e permitir análise consolidada com origem identificada.
  Mapeamento dos exemplos e requisitos em
  [Financeiro: legado 90 e TOTVS](./financeiro-origens.md).
- **EasyJur:** cliente informou que não funcionou. A integração permanece
  pendente de diagnóstico e validação; autenticação histórica não comprova
  funcionamento atual. Não considerar essa entrega concluída.
- **Manutenção / frota:** acessos já concedidos, conforme informado pelo
  cliente. Próxima etapa é validar a configuração e as operações de cada
  integração; falta de concessão de acesso não é mais o bloqueio geral.
  O adapter Sistema 90 para esse módulo ainda é stub. A importação financeira
  manual do legado já tem fluxo implementado; não depende de API do Sistema 90.
- **Escopo comercial:** reunião ou menção no briefing não comprovam inclusão
  no orçamento. OCR financeiro de PDF e conciliação de retorno bancário ficam
  fora da contagem de entregas confirmadas até conferência da proposta aprovada.
  Retorno bancário também exige arquivo real do banco para especificar o parser.
- **OneDrive:** existe um diagnóstico de estrutura, mas a convenção configurada
  precisa ser comparada às pastas reais. Isso não bloqueia as consultas públicas
  de CEP e CNPJ do dossiê; CPF ainda depende da credencial DirectData.

Esta atualização prevalece sobre os estados históricos de acesso registrados
na documentação. Não representa validação em produção.

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
| 7 | Apropriação e custo por equipamento | Motor de regras que cruza apropriação (h/km) × NF e alerta de custo acima da referência. Acessos de manutenção/frota concedidos; o adapter Sistema 90 ainda é stub. Identificar endpoints, autenticação, dados/operações cobertos e validar a conexão antes de implementar. A importação financeira do legado 90 é tratada na demanda 8. |

## Módulo C — Financeiro / Contratos

| # | Demanda | Entrega |
|---|---------|---------|
| 8 | Financeiro: legado 90 + TOTVS | A importação manual da aba Relatório Completo e a leitura do TOTVS têm implementações próprias, preservando as origens. Falta validar os dados reais, a cobertura dos campos FIN e os critérios de consolidação. OCR de PDF escaneado e conciliação bancária só entram como compromisso após confirmação na proposta/orçamento; o parser bancário também depende de arquivo real de retorno. Confirmar separadamente encaminhamento de NFs e cruzamentos em medição. Ver [mapeamento](./financeiro-origens.md). |
| 12 | Ciclo de contratos | Cadastro, AP e alertas de vencimento; possível organização no OneDrive. Assinatura digital fora do escopo por decisão de 04/08/2026. EasyJur é tratado como fonte de contencioso; relatórios de contratos judicializados não são considerados entrega enquanto não houver base de contratos e confirmação comercial. A leitura do contencioso permanece em diagnóstico. |

## Módulo D — Licitações

| # | Demanda | Entrega |
|---|---------|---------|
| 9 | Inteligência de licitações | Código de captação PNCP, análise, resultados, triagem e quatro painéis comerciais implementados. Concorrentes/geotargeting só mostram homologações consultadas para licitações captadas; eficiência/não-captados dependem da triagem. Operação: rodar “Popular inteligência comercial” em Licitações (até 200 processos nos últimos 180 dias por padrão), acompanhar a task do worker e registrar decisões na triagem. CREA via Infosimples também tem consulta/importação de ART em MG/SP/GO, mas exige token real; sem ele, o adapter responde em mock. ComprasNet/Licitações-e continuam scaffold por captcha/SSO. |

## Módulo E — IA / Ferramentas avançadas

| # | Demanda | Entrega |
|---|---------|---------|
| 11 | Robô de compras WhatsApp | Agente LLM que recebe demanda de manutenção, contacta fornecedores, compila cotação em tabela. Mesmo fluxo para locações. |

## Ordem sugerida de execução

1. **Fundações** (este PR): scaffold, CI, docker-compose, audit_log.
2. **Módulo A** — onboarding + varredura OCR (impacto imediato no DP).
3. **Módulo C** — ingestão de NF-e (rastreabilidade financeira).
4. **Módulo B** — RPA despachante + OCR partes diárias.
5. **Módulo D** — crawlers B2G.
6. **Módulo E** — bot de cotação via WhatsApp.

Cada entrega vira 1 PR com documentação, testes e feature flag.
