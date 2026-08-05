# ADR-002 — Super Sprint: organização em squads

- **Status:** proposto
- **Data:** 2026-08-04
- **Contexto:** atacar em uma sprint tudo que não depende de credencial nova, decisão pendente do cliente ou material não recebido. Base: gap analysis do roadmap + Projeto Técnico do Captador de Licitações (time de licitação, 23/06/2026).

## Critério de corte

Entra na sprint o que roda ponta a ponta com o que já temos (PNCP público, storage abstraction, Resend, padrão de mock determinístico). Fica fora: assinatura digital (provider indefinido), EasyJur (API não pública, contato comercial), remessas CNAB (sem arquivos reais), e-learning/robô WhatsApp, credenciais reais Tangerino/Onvio (adapters entram com mock; credencial vira troca de config).

## Squads e planos

| Squad | Frente | Plano |
|---|---|---|
| 1 | Captador — workflow de triagem (status machine, decisões, Tela de Captação) | `docs/superpowers/plans/2026-08-04-captador-squad1-triagem.md` |
| 2 | Captador — pasta do projeto, identificação de planilha orçamentária, Aba de Triagem | `docs/superpowers/plans/2026-08-04-captador-squad2-planilha-pastas.md` |
| 3 | Resultados/homologações PNCP + atas RP (D.9) + dashboards comerciais | `docs/superpowers/plans/2026-08-04-captador-squad3-resultados-dashboards.md` |
| 4 | Módulo C — parser NF-e 4.00 + persistência | `docs/superpowers/plans/2026-08-04-modc-squad4-nfe-parser.md` |
| 5 | Módulo C — ciclo de contratos (CRUD + alertas, sem assinatura) | `docs/superpowers/plans/2026-08-04-modc-squad5-contratos.md` |
| 6 | Adapters Tangerino + Onvio (real + mock determinístico, padrão OnSafety) | `docs/superpowers/plans/2026-08-04-integracoes-squad6-tangerino-onvio.md` |

## Contratos entre squads

- **Squad 1 → Squad 2:** Squad 1 cria `Licitacao.status_triagem` (valores: `novo_captado`, `em_analise`, `aprovado`, `rejeitado`, `processando_anexos`, `completo`, `sem_planilha`, `erro_portal`, `erro_sharepoint`). Ao aprovar, com `CAPTADOR_AUTO_PROCESS=1`, despacha a task Celery `worker.tasks.licitacoes.processar_edital_aprovado(licitacao_id)` (entregue pela Squad 2) com try/except — aprovar nunca falha por ausência da task.
- **Squad 1 → Squad 3:** dashboard "não captados" lê `status_triagem` pelo nome exato.
- **Squad 6 → futuras:** adapters não expõem endpoint de negócio; mão de obra por obra (Tangerino) e envio contábil de NF-e (Onvio) consomem os Protocols em sprint seguinte.
- Squads 4 e 5 são independentes entre si e das demais.

## Regras de integração

1. **Migrations Alembic:** cada squad escreve a sua com `down_revision` provisório; na hora do merge, rebase para o head vigente (merges serializados: 1 → 2 → 3 → 4 → 5 → 6 é a ordem sugerida; 4/5/6 podem entrar em qualquer ordem entre si).
2. **1 squad = 1 branch = 1 PR**, com testes e ruff limpos (gate: suite completa verde, hoje 565 testes).
3. Padrões obrigatórios: audit_log com email do usuário em ação sensível, feature flag para todo comportamento automático novo, endpoint manual espelhando toda task Celery (a demo Vercel não tem worker).
4. UAT do Captador (critério do cliente): clique na Aba de Triagem abre a planilha orçamentária direto. Na sprint, o link aponta para o storage configurado (local/OneDrive); o SharePoint corporativo é troca de config quando a conta de serviço chegar.

## Fora da sprint — dependências externas a destravar em paralelo

| Item | O que pedir | De quem |
|---|---|---|
| Conta de serviço SharePoint | App registration no Entra ID (ver ADR, seção abaixo) | TI da Primor |
| Credenciais Tangerino | API key do Tangerino Employer | RH/DP Primor |
| Credenciais Onvio | client_id/secret + integration key do portal do desenvolvedor Domínio | Contabilidade da Primor |
| EasyJur | contato comercial para API | Jurídico/Primor |
| Áudio dos gargalos + código não testado | envio pendente | Time de licitação |

### Especificação da conta de serviço SharePoint (para a TI da Primor)

Registrar uma aplicação no **Microsoft Entra ID** (Azure AD) do tenant da Primor e fornecer: **Tenant ID**, **Client ID** e **Client Secret**. Permissões de aplicação (application permissions, com admin consent): `Sites.ReadWrite.All` — ou, preferível por segurança, `Sites.Selected` com grant de escrita apenas no site/diretório de orçamento. Informar também a **URL do site SharePoint** e o **caminho da biblioteca/pasta raiz de orçamento** onde as pastas de edital serão criadas.
