# ADR-003 — Fluxo DP ↔ Contabilidade (Domínio/Onvio)

- **Status:** aceito
- **Data:** 2026-08-07
- **Contexto:** respostas oficiais do suporte Domínio/Thomson Reuters e da contabilidade da Primor (ago/2026):
  1. A API Onvio **só importa XML de NF-e** — não existe API de consulta/leitura.
  2. A consulta direta ao banco (solução 3227, ODBC SQL Anywhere) **só funciona no Domínio Local**; a contabilidade da Primor usa **Domínio Web** → rota inviável.
  3. **Não existe layout de importação por arquivo** para cadastros (funcionários/admissão) — só o XML de NF.

## Decisões

**D1 — Envio de NF-e às obras → contabilidade: via API Onvio (mantido).**
O conector construído na super sprint (adapter Onvio, guard `ONVIO_ALLOW_SEND` default off) usa exatamente a única API oferecida. Ativação pendente apenas de credenciais (client_id/secret + integration key).

**D2 — Custo de mão de obra: fonte da verdade é o Motor Central, sem dependência do Domínio.**
Cargo e remuneração entram no cadastro de funcionários (Módulo A) na admissão e mudam por evento (promoção, dissídio). Encargos para custo **gerencial** por obra são calculados por **fórmula paramétrica** sobre o salário (INSS patronal, FGTS, provisões — percentuais configuráveis por parâmetro do sistema). A folha real do Domínio não é necessária para o costing gerencial; batimento contábil fino, se um dia exigido, usa D3.

**D3 — Transporte de exports (fallback, quando um relatório da folha for indispensável): pasta OneDrive monitorada.**
A contabilidade salva o export (CSV/Excel) numa pasta do OneDrive que o `onedrive_sync` já monitora; a ingestão é automática. Esforço manual residual: salvar um arquivo por competência. RPA no portal Onvio para download de relatórios fica documentado como alternativa de automação total, adiado por fragilidade (mudanças de portal, MFA) até que D2+D3 se provem insuficientes.

**D4 — Onboarding (Motor Central → Domínio): kit de admissão estruturado + digitação pela contabilidade.**
Sem API e sem layout de importação, a digitação final no Domínio permanece com a contabilidade — **limitação do fornecedor, não do projeto**. O Motor Central automatiza tudo ao redor: gera o kit de admissão completo (todos os campos que o Domínio pede, na ordem, + documentos do dossiê) e entrega automaticamente (email/OneDrive) com rastreio de confirmação. **RPA de digitação** no portal fica condicionado a volume: se admissões/mês × tempo de digitação justificar (critério a validar com a Primor — rotatividade de obra pode ser alta), vira fase própria.

**D5 — Pergunta em aberto ao suporte Domínio:** o sistema importa **eventos eSocial (ex.: S-2200 admissão) em XML**? Se sim, o Motor Central pode gerar o XML do evento e eliminar a digitação de D4 sem RPA. A confirmar; não bloqueia D1–D4.

## Consequências

- Nenhuma entrega existente é afetada; o adapter Onvio permanece o único canal de máquina com o Domínio.
- A apropriação de mão de obra (Módulo B #7 + docx do cliente) fica **desbloqueada do lado contábil** — resta só a definição do "onde" (workplace vs geolocalização, aguardando Sólides).
- Parâmetros de encargos (D2) viram configuração do sistema e precisam de validação inicial do financeiro da Primor.
- Registrar com a Primor a limitação do fornecedor (D4) para alinhamento de expectativa: admissão 100% sem digitação só com eSocial (D5) ou RPA.
