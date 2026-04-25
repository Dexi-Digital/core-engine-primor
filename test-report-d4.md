# Test Report — D.4 (PR #4) · Download de editais via PNCP

**Sumário:** rodei a API + web local contra PNCP real, testei o fluxo completo de download de edital + anexos + idempotência pela UI na licitação id=2 (Leilão Eletrônico · Santa Rosa de Viterbo · 45368545000193/2026/24). Todos os passos do plano passaram. Uma observação menor de UX (não é bug).

**Devin session:** https://app.devin.ai/sessions/1c17171b07f94afdb601f97213a8a29d

## Resultados

- **It should download edital from PNCP when clicking Baixar edital** — ✅ passed
  - Estado inicial: detalhe mostra "Nenhum download solicitado ainda. Clique em **Baixar edital**..." + botão "Baixar edital"
  - Após click: badge "Baixado" + lista de **10 anexos** com tamanho em KB + link "ver no PNCP ↗" cada
  - Disk: `/tmp/motor-central/editais/2/` com **10 PDFs** (total ~2.5 MB: EDITAL DE LEILAO0022026.pdf 595.7 KB + 9 fotos/plantas)
  - DB: `licitacoes_editais.status=completed, anexos_count=10` · `COUNT(licitacoes_editais_anexos)=10`

- **It should be idempotent when clicking Atualizar again** — ✅ passed
  - Re-click "Atualizar": API retorna `{"status":"completed","anexos_count":10,"new_anexos":0}`
  - Count na UI, disk e DB permanece **10** (não 20)

- **It should show Baixar shortcut on list view** — ✅ passed (com observação)
  - Coluna "Edital" aparece no header
  - Cada linha tem botão "Baixar" + link "detalhes"
  - **Observação:** a lista mostra sempre "Baixar" — não reflete o status `completed/pending` por design atual. Só o detalhe tem o badge. Não é um bug, mas é uma melhoria óbvia (ex: mostrar "Baixado (10)" para licitações já baixadas) que vale P1 num próximo PR.

## Evidências

### Before/after do download (licitação id=2)

| 🔴 Antes (empty state) | 🟢 Depois (10 anexos baixados) |
|---|---|
| ![empty](https://app.devin.ai/attachments/e936b2b4-49bf-46a7-bb9b-96e3095e380b/screenshot_0c62be1ed9b042e19327172ea9ad1c70.png) | ![baixado](https://app.devin.ai/attachments/e96f9b34-676e-4731-88bb-c4101c891732/screenshot_831c268903ab46a186cd6e64411b7e5c.png) |

### Lista com coluna Edital (regressão) · Lista filtrada após download

| Baseline `/licitacoes` (197 rows, coluna Edital visível) | Filtro "Santa Rosa de Viterbo" pós-download (botão Baixar ainda mostrado na lista — ver observação) |
|---|---|
| ![list](https://app.devin.ai/attachments/4eda679f-0766-4df8-9f80-e02a507db005/screenshot_dec4ae553fe24e838d1c90f1ee89dd1c.png) | ![filter](https://app.devin.ai/attachments/1be9c085-5d49-4f8e-8147-40ee0f544e87/screenshot_b940da8c40da48de850ec2ca64128264.png) |

### Vídeo do fluxo completo

https://app.devin.ai/attachments/5b46d711-8813-4fe4-b1c7-77db944ac283/rec-ebd387ba-d4f1-468d-b64e-ea5636cdbdbe-edited.mp4

### Shell proof (backend)

```
$ ls /tmp/motor-central/editais/2/ | wc -l
10

$ psql -c "SELECT status, anexos_count FROM licitacoes_editais WHERE licitacao_id=2;"
 completed | 10

$ psql -c "SELECT COUNT(*) FROM licitacoes_editais_anexos WHERE edital_id=...;"
 10

$ curl -X POST .../api/v1/licitacoes/2/edital/download
{
    "licitacao_id": 2,
    "source": "pncp",
    "status": "completed",
    "anexos_count": 10,
    "new_anexos": 0,   # idempotente
    "error_message": null
}
```

## Não testado (fora do escopo por design)

- Fallback ComprasNet (levanta `ComprasnetCaptchaRequired` — scaffold)
- Fallback Licitações-e (levanta `LicitacoesECredentialsRequired` — scaffold)
- Celery worker task `download_edital` (service idêntico ao endpoint HTTP, coberto por 14 unit tests)
