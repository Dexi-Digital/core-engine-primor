# Arquitetura

## Princípios

1. **Monólito modular, não microserviços (ainda).** Um único processo FastAPI
   com módulos independentes por domínio. Extração para serviços separados só
   quando houver pressão real de escala ou release cadence distinto.
2. **Workers isolados.** Scraping, OCR e RPA rodam em um processo Celery
   separado. A API nunca executa IO pesado inline.
3. **Adapters plugáveis.** Cada sistema externo (Domínio, TOTVS, OnSafety…)
   é encapsulado em um subpackage `app/integrations/<sistema>/` com uma
   interface comum (`IntegrationClient`).
4. **Eventos vs sync.** Onboarding e operações que tocam múltiplos sistemas
   são disparadas como jobs assíncronos com correlation_id; a UI faz polling
   ou recebe updates via SSE/WebSocket (a ser definido).

## Fluxo típico (ex: onboarding)

```
Web (form)
  → POST /api/v1/dp-sesmt/onboarding
      → API grava "onboarding iniciado" + audit_log
      → enqueue worker.tasks.dp_sesmt.sync_onboarding(cpf)
          → IntegrationClient.dominio.push(...)
          → IntegrationClient.onvio.push(...)
          → IntegrationClient.tangerino.push(...)
          → IntegrationClient.onsafety.push(...)
      → cada sub-etapa grava status/erro em tabela `onboarding_runs`
Web (polling)
  → GET /api/v1/dp-sesmt/onboarding/{corr_id}/status
```

## Camadas

```
 ┌──────────────┐   ┌────────────────┐   ┌──────────────────┐
 │  Next.js UI  │──▶│  FastAPI REST  │──▶│  SQLAlchemy / PG │
 └──────────────┘   └────────┬───────┘   └──────────────────┘
                             │
                             ▼
                     ┌──────────────┐        ┌────────────────┐
                     │  Celery API  │──────▶│  Redis (broker) │
                     │  enqueue()   │        └────────┬───────┘
                     └──────────────┘                 ▼
                                              ┌──────────────┐
                                              │ Celery Worker│
                                              │ tasks/*      │
                                              └──────┬───────┘
                                                     ▼
                                     ┌───────────────────────────┐
                                     │ Integrations: Domínio,    │
                                     │ Onvio, OnSafety, Tangerino│
                                     │ TOTVS, Sistema90, OneDrive│
                                     │ EasyJur, Conlicitação,    │
                                     │ WhatsApp, LLM providers   │
                                     └───────────────────────────┘
```

## Storage

- **PostgreSQL**: dados transacionais (funcionários, contratos, licitações, audit_log).
- **MinIO/S3**: binários (PDFs, fotos de obra, XMLs de NF, ASOs escaneados).
- **Redis**: broker Celery + cache de short-lived tokens de integrações.

## Observabilidade

A ser definido (sugestão: Grafana + Prometheus para métricas, Loki para logs).
`structlog` já configurado com JSON renderer para facilitar agregação.
