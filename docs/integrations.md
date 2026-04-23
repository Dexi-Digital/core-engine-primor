# Integrações (adapters em `apps/api/app/integrations/`)

Todos os adapters implementam `IntegrationClient` (`app/integrations/base.py`).
Chaves de acesso vivem em variáveis de ambiente; **nunca no código**.

| Subpackage | Sistema | Tipo | Módulos consumidores |
|------------|---------|------|----------------------|
| `dominio`       | Domínio (Thomson Reuters)         | API          | A, C |
| `onvio`         | Onvio (admissão/contabilidade)    | API          | A |
| `onsafety`      | OnSafety (SST, EPIs)              | API          | A |
| `tangerino`     | Tangerino (ponto, fotos base)     | API          | A, E |
| `totvs`         | TOTVS (ERP financeiro)            | API / DB     | C |
| `sistema90`     | Sistema 90 (legado frota)         | API parcial  | B |
| `onedrive`      | OneDrive / SharePoint             | Graph API    | A, B, C |
| `easyjur`       | EasyJur (jurídico)                | API          | C |
| `solides`       | Sólides (R&S)                     | API          | A (avaliação) |
| `conlicitacao`  | Conlicitação + PNCP + Diários     | Scraping     | D |
| `whatsapp`      | WhatsApp Business API             | API          | A, E |

## Padrões

- **Idempotência:** toda chamada `push` deve aceitar um `correlation_id`.
- **Timeouts + retries:** usar `tenacity` no worker; nunca retry dentro da API.
- **Rate limiting:** respeitar limites de cada provedor; usar Redis como
  token bucket global.
- **Scraping:** rodar headless via Playwright, com profile isolado por tenant.
  Sempre logar a URL/timestamp para rastreabilidade (LGPD).

## Credenciais esperadas (env vars)

A ser documentado conforme cada integração for implementada. Template em
`apps/api/.env.example`.
